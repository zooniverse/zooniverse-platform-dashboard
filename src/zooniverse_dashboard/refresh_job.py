"""Browser-independent manual refresh worker with durable, aggregate-only progress.

The web process admits a job and spawns a detached interpreter; a browser
navigation is not responsible for the job's lifetime. This assumes the same
single-host/shared-POSIX-volume deployment as refresh_lock().
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import stat
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from zooniverse_dashboard.config import data_dir
from zooniverse_dashboard.logging_utils import configure_refresh_logging, safe_error_trace
from zooniverse_dashboard.pipeline import refresh_and_rebuild
from zooniverse_dashboard.refresh import (
    COOLDOWN_MINUTES,
    RefreshRejected,
    load_status,
    refresh_lock,
)
from zooniverse_dashboard.snapshots import _atomic_publish

FORMAT = "ZOONIVERSE_REFRESH_JOB_V1"
LOG = logging.getLogger("zooniverse_dashboard.refresh")
STEPS_MAX = 24
STARTUP_GRACE_SECONDS = 30
JOB_FIELDS = {
    "format",
    "run_id",
    "started_at_utc",
    "updated_at_utc",
    "finished_at_utc",
    "state",
    "steps",
    "summary",
}
JOB_STATES = {"queued", "running", "ok", "partial_failure", "rejected", "failed", "interrupted"}


@contextmanager
def _lock(folder: Path, name: str, *, reject: bool = False):
    """Hold a local advisory lock without exposing subprocess identifiers."""
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(folder / name, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Invalid job lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            if reject:
                raise RefreshRejected("Another refresh is already running") from exc
            raise
        yield
    finally:
        os.close(fd)


def _job_locked(folder: Path) -> bool:
    try:
        with _lock(folder, ".refresh-job.lock"):
            return False
    except BlockingIOError:
        return True


def _path(folder: Path) -> Path:
    return folder / "refresh_job.json"


def _read(folder: Path) -> dict | None:
    path = _path(folder)
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 8192:
        raise ValueError("Invalid refresh job status")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(doc, dict)
        or set(doc) != JOB_FIELDS
        or doc["format"] != FORMAT
        or not isinstance(doc["run_id"], str)
        or re.fullmatch(r"[a-f0-9]{32}", doc["run_id"]) is None
        or doc["state"] not in JOB_STATES
        or not isinstance(doc["steps"], list)
        or len(doc["steps"]) > STEPS_MAX
        or any(not isinstance(s, str) or len(s) > 160 for s in doc["steps"])
        or (doc["summary"] is not None and not isinstance(doc["summary"], dict))
    ):
        raise ValueError("Invalid refresh job status")
    for key in ("started_at_utc", "updated_at_utc", "finished_at_utc"):
        value = doc[key]
        if value is None and key == "finished_at_utc":
            continue
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
            raise ValueError("Invalid refresh job timestamp")
    return doc


def _publish(folder: Path, doc: dict) -> None:
    doc["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    _atomic_publish(_path(folder), doc)


def load_job(root: Path | None = None) -> dict | None:
    """Read status. A vanished worker is reported as interrupted, not running.

    The short start grace prevents a still-launching child from being mistaken
    for a crashed worker. No file is changed by this read-only operation.
    """
    folder = root if root is not None else data_dir()
    doc = _read(folder)
    if doc is None or doc["state"] not in ("queued", "running"):
        return doc
    last = datetime.fromisoformat(doc["updated_at_utc"])
    if datetime.now(timezone.utc) - last > timedelta(
        seconds=STARTUP_GRACE_SECONDS
    ) and not _job_locked(folder):
        return {**doc, "state": "interrupted"}
    return doc


def start_job(root: Path | None = None) -> str:
    """Quick HTTP-side admission; the actual work runs in a detached process."""
    folder = root if root is not None else data_dir()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _lock(folder, ".refresh-job-admission.lock", reject=True):
        prior_job = load_job(folder)
        if prior_job and prior_job["state"] in ("queued", "running"):
            raise RefreshRejected("Another refresh is already running")
        # A CLI invocation may already be collecting. The real coordinator
        # continues to own its lock and cooldown checks in the child process.
        with refresh_lock(folder):
            previous = load_status(folder)
            now = datetime.now(timezone.utc)
            if previous is not None:
                started = datetime.fromisoformat(previous["started_at_utc"])
                if now < started + timedelta(minutes=COOLDOWN_MINUTES):
                    raise RefreshRejected("Refresh was attempted within the last hour")

            run_id = uuid.uuid4().hex
            job = {
                "format": FORMAT,
                "run_id": run_id,
                "started_at_utc": now.isoformat(),
                "updated_at_utc": now.isoformat(),
                "finished_at_utc": None,
                "state": "queued",
                "steps": ["Refresh scheduled"],
                "summary": None,
            }
            _publish(folder, job)
        try:
            # The detached worker survives navigation and page reloads.
            # Keep sanitized application logs for operational diagnosis.
            # Do not follow an operator-supplied symlink for the log path.
            log_fd = os.open(
                folder / "refresh-worker.log",
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                if not stat.S_ISREG(os.fstat(log_fd).st_mode):
                    raise OSError("Invalid refresh log")
                subprocess.Popen(
                    [sys.executable, "-m", "zooniverse_dashboard.refresh_job", run_id],
                    stdin=subprocess.DEVNULL,
                    stdout=log_fd,
                    stderr=log_fd,
                    start_new_session=True,
                    close_fds=True,
                )
            finally:
                os.close(log_fd)
        except OSError as exc:
            LOG.error(
                "event=refresh_job_spawn_failed error_type=%s trace=%s",
                type(exc).__name__,
                safe_error_trace(exc),
            )
            job.update(state="failed", finished_at_utc=datetime.now(timezone.utc).isoformat())
            job["steps"].append("Unable to start refresh worker")
            _publish(folder, job)
            raise RefreshRejected("Unable to start refresh worker") from None
        return run_id


def run_job(run_id: str, *, root: Path | None = None) -> dict:
    """Child entry point. Always finalize durable status on ordinary failures."""
    folder = root if root is not None else data_dir()
    configure_refresh_logging()
    with _lock(folder, ".refresh-job.lock", reject=True):
        job = _read(folder)
        if job is None or job["run_id"] != run_id or job["state"] != "queued":
            raise RefreshRejected("Refresh job is no longer queued")
        job["state"] = "running"
        _publish(folder, job)

        def step(message: str) -> None:
            # Only fixed operational descriptions from pipeline.py/refresh.py.
            # Never append raw exception text, provider payloads or credentials.
            job["steps"] = (job["steps"] + [message[:160]])[-STEPS_MAX:]
            _publish(folder, job)

        try:
            result = refresh_and_rebuild(root=folder, progress=step)
            job["state"] = result["state"]
            job["summary"] = {
                "refresh_state": result["refresh_state"],
                "anomalies_state": result["anomalies_state"],
                "sources": dict(result["sources"]),
            }
            if job["state"] != "ok":
                step("Refresh finished with warnings; review source freshness")
        except RefreshRejected as exc:
            job["state"] = "rejected"
            step(str(exc))
        except Exception as exc:
            LOG.error(
                "event=refresh_job_failed error_type=%s trace=%s",
                type(exc).__name__,
                safe_error_trace(exc),
            )
            job["state"] = "failed"
            step("Refresh failed; consult sanitized server logs")
        finally:
            job["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
            _publish(folder, job)
        return job


def main() -> None:
    if len(sys.argv) != 2 or re.fullmatch(r"[a-f0-9]{32}", sys.argv[1]) is None:
        raise SystemExit(2)
    result = run_job(sys.argv[1])
    raise SystemExit(0 if result["state"] == "ok" else 1)


if __name__ == "__main__":
    main()
