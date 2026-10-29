"""Manage browser-triggered, detached refresh jobs and durable progress.

The web process validates admission and starts a separate Python interpreter;
refresh work is not tied to an HTTP request or browser tab. A small
``refresh_job.json`` tracks the queue, running state, bounded progress text,
completion, and sanitized source summary. The worker writes to the shared
persistent data volume and runs :func:`zooniverse_dashboard.pipeline.refresh_and_rebuild`.

An admission lock protects rapid duplicate clicks; a separate worker lock
guards execution. The underlying collection lock and 60-minute cooldown in
:mod:`zooniverse_dashboard.refresh` remain authoritative. With no live worker,
a stale queued/running job is **reported** as interrupted by
:func:`load_job` without changing the persisted record.

All locks are local POSIX filesystem locks and require a shared volume.
The child inherits runtime configuration but no credentials are written into
job JSON or progress messages.

See Also
--------
:func:`start_job`
    Validate admission and launch a detached refresh process.
:func:`run_job`
    Execute the pipeline and persist a final job state.
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
from typing import Any, Final, Iterator

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

FORMAT: Final[str] = "ZOONIVERSE_REFRESH_JOB_V1"
LOG: logging.Logger = logging.getLogger("zooniverse_dashboard.refresh")
STEPS_MAX: Final[int] = 24
STARTUP_GRACE_SECONDS: Final[int] = 30
JOB_FIELDS: Final[set[str]] = {
    "format",
    "run_id",
    "started_at_utc",
    "updated_at_utc",
    "finished_at_utc",
    "state",
    "steps",
    "summary",
}
JOB_STATES: Final[set[str]] = {
    "queued",
    "running",
    "ok",
    "partial_failure",
    "rejected",
    "failed",
    "interrupted",
}


@contextmanager
def _lock(folder: Path, name: str, *, reject: bool = False) -> Iterator[None]:
    """Hold a nonblocking advisory lock on a job coordination file.

    The file must be regular and is opened with restrictive permissions.
    ``reject=True`` maps contention to the fixed-message
    :class:`zooniverse_dashboard.refresh.RefreshRejected` exception; otherwise
    an ordinary ``BlockingIOError`` propagates. The descriptor is closed on exit.

    Parameters
    ----------
    folder : pathlib.Path
        Shared persistent directory for coordination files.
    name : str
        Lock filename relative to ``folder``.
    reject : bool, optional
        Convert lock contention into a user-facing refresh rejection.

    Yields
    ------
    None
        Execution while the exclusive advisory lock is held.

    Raises
    ------
    ValueError
        If the lock target is not a regular file.
    BlockingIOError
        If the lock is held and ``reject`` is false.
    zooniverse_dashboard.refresh.RefreshRejected
        If the lock is held and ``reject`` is true.
    OSError
        If file or locking operations fail.
    """
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd: int = os.open(folder / name, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
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
    """Check whether another process holds the job-execution lock.

    Parameters
    ----------
    folder : pathlib.Path
        Shared directory containing the ``.refresh-job.lock`` file.

    Returns
    -------
    bool
        ``True`` on nonblocking lock contention; otherwise ``False``.

    Raises
    ------
    OSError
        If the lock file cannot be accessed or examined.
    ValueError
        If the existing lock target is not a regular file.
    """
    try:
        with _lock(folder, ".refresh-job.lock"):
            return False
    except BlockingIOError:
        return True


def _path(folder: Path) -> Path:
    """Return the durable job-state JSON path.

    Parameters
    ----------
    folder : pathlib.Path
        Shared persistent data directory.

    Returns
    -------
    pathlib.Path
        ``refresh_job.json`` within ``folder``.
    """
    return folder / "refresh_job.json"


def _read(folder: Path) -> dict[str, Any] | None:
    """Read and validate the persisted refresh-job document.

    Enforce the expected format, exact fields, 32-character lowercase hexadecimal
    run ID, accepted states, bounded progress text, and UTC timestamps. Reject
    unsafe or oversized files. This helper does not infer worker liveness.

    Parameters
    ----------
    folder : pathlib.Path
        Directory containing the job record.

    Returns
    -------
    dict[str, typing.Any] or None
        Valid job mapping, or ``None`` when the file is absent.

    Raises
    ------
    ValueError
        If the document is malformed or fails schema validation.
    OSError
        If the JSON file cannot be inspected or read.
    """
    path: Path = _path(folder)
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 8192:
        raise ValueError("Invalid refresh job status")
    doc: Any = json.loads(path.read_text(encoding="utf-8"))
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
        value: str | None = doc[key]
        if value is None and key == "finished_at_utc":
            continue
        timestamp: datetime = datetime.fromisoformat(value)
        if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
            raise ValueError("Invalid refresh job timestamp")
    return doc


def _publish(folder: Path, doc: dict[str, Any]) -> None:
    """Atomically persist a job record with a fresh update timestamp.

    Mutate ``doc['updated_at_utc']`` to the current UTC time before using
    ``zooniverse_dashboard.snapshots._atomic_publish`` to publish it.

    Parameters
    ----------
    folder : pathlib.Path
        Shared data directory.
    doc : dict[str, typing.Any]
        Job state mapping; this input is updated in place.

    Returns
    -------
    None
        The job document is written atomically.

    Raises
    ------
    ValueError
        If the atomic publisher rejects the destination or document.
    OSError
        If writing the job JSON fails.
    """
    doc["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    _atomic_publish(_path(folder), doc)


def load_job(root: Path | None = None) -> dict[str, Any] | None:
    """Report the current durable job state, detecting vanished workers.

    Read the validated job JSON without modifying it. For a ``queued`` or
    ``running`` record, report an ephemeral ``interrupted`` status only if its
    last update is older than the short startup grace and no execution lock is
    held. This avoids declaring a newly spawned child dead before it obtains
    its execution lock.

    Parameters
    ----------
    root : pathlib.Path or None, optional
        Shared data directory, defaulting to
        :func:`zooniverse_dashboard.config.data_dir`.

    Returns
    -------
    dict[str, typing.Any] or None
        A validated job mapping (possibly with a derived ``interrupted`` state),
        or ``None`` when no file exists. The on-disk record is not changed.

    Raises
    ------
    ValueError
        If the job record or lock target is invalid.
    OSError
        If job or lock files cannot be read or examined.

    See Also
    --------
    :func:`run_job`
        Owns the execution lock while the pipeline is active.
    """
    folder: Path = root if root is not None else data_dir()
    doc: dict[str, Any] | None = _read(folder)
    if doc is None or doc["state"] not in ("queued", "running"):
        return doc
    last: datetime = datetime.fromisoformat(doc["updated_at_utc"])
    if datetime.now(timezone.utc) - last > timedelta(
        seconds=STARTUP_GRACE_SECONDS
    ) and not _job_locked(folder):
        return {**doc, "state": "interrupted"}
    return doc


def start_job(root: Path | None = None) -> str:
    """Admit a browser refresh and spawn a detached worker process.

    Under the admission lock, refuse an existing queued/running job, an active
    collection, or a collection attempt within the last 60 minutes. Publish a
    ``queued`` job record with a random run ID before spawning a detached
    interpreter that executes this module with that ID. Standard output/error go
    to the private ``refresh-worker.log``; the file must be regular, is opened
    without following a symlink where supported; newly created files
    request mode ``0600``.

    This method returns after spawning; the child reports actual pipeline
    completion through :func:`run_job`. Process-launch failures transition the
    record to ``failed`` and produce only a sanitized error in application logs.

    Parameters
    ----------
    root : pathlib.Path or None, optional
        Directory holding admission locks, job status, snapshots, and worker log.

    Returns
    -------
    str
        Opaque 32-character hexadecimal run ID of the queued job.

    Raises
    ------
    zooniverse_dashboard.refresh.RefreshRejected
        If another job/refresh is active, the cooldown has not expired, or the
        detached worker cannot be started.
    ValueError
        If existing status or filesystem targets fail validation.
    OSError
        If an admission lock or durable publication cannot be created.

    See Also
    --------
    :func:`zooniverse_dashboard.refresh.refresh_all`
        Applies the authoritative collection lock and cooldown in the child.
    """
    folder: Path = root if root is not None else data_dir()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _lock(folder, ".refresh-job-admission.lock", reject=True):
        prior_job: dict[str, Any] | None = load_job(folder)
        if prior_job and prior_job["state"] in ("queued", "running"):
            raise RefreshRejected("Another refresh is already running")
        # A CLI invocation may already be collecting. The real coordinator
        # continues to own its lock and cooldown checks in the child process.
        with refresh_lock(folder):
            previous: dict[str, Any] | None = load_status(folder)
            now: datetime = datetime.now(timezone.utc)
            if previous is not None:
                started: datetime = datetime.fromisoformat(previous["started_at_utc"])
                if now < started + timedelta(minutes=COOLDOWN_MINUTES):
                    raise RefreshRejected("Refresh was attempted within the last hour")

            run_id: str = uuid.uuid4().hex
            job: dict[str, Any] = {
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
            log_fd: int = os.open(
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


def run_job(run_id: str, *, root: Path | None = None) -> dict[str, Any]:
    """Execute an admitted refresh job and finalize durable status.

    Under the execution lock, require the matching ``queued`` record, update its
    state to ``running``, then call
    :func:`zooniverse_dashboard.pipeline.refresh_and_rebuild`. Append only
    fixed progress messages, capped in length and number, and store aggregate
    per-source outcomes. Ordinary exceptions transition to ``failed`` or
    ``rejected``; the ``finally`` block records a completion time and publishes
    the final status. The method does not expose raw provider exception messages.

    Parameters
    ----------
    run_id : str
        Hexadecimal identifier matching the currently queued job.
    root : pathlib.Path or None, optional
        Directory containing the shared job state and aggregate snapshots.

    Returns
    -------
    dict[str, typing.Any]
        Final durable job mapping. ``state`` may be ``ok``, ``partial_failure``,
        ``failed``, or ``rejected``.

    Raises
    ------
    zooniverse_dashboard.refresh.RefreshRejected
        If the worker lock is occupied or the expected queued job is absent.
    ValueError
        If the job state file or lock target is invalid.
    OSError
        If locking or durable state publication fails.

    See Also
    --------
    :func:`load_job`
        Read and report the persisted status to the dashboard.
    """
    folder: Path = root if root is not None else data_dir()
    configure_refresh_logging()
    with _lock(folder, ".refresh-job.lock", reject=True):
        job: dict[str, Any] | None = _read(folder)
        if job is None or job["run_id"] != run_id or job["state"] != "queued":
            raise RefreshRejected("Refresh job is no longer queued")
        job["state"] = "running"
        _publish(folder, job)

        def step(message: str) -> None:
            # Only fixed operational descriptions from pipeline.py/refresh.py.
            # Never append raw exception text, provider payloads or credentials.
            """Append and persist one sanitized progress message.

            Parameters
            ----------
            message : str
                Fixed operator-facing progress text produced by the refresh pipeline.
                Text is truncated to 160 characters and only the last 24 steps are kept.

            Returns
            -------
            None
                The updated job record is atomically published.
            """
            job["steps"] = (job["steps"] + [message[:160]])[-STEPS_MAX:]
            _publish(folder, job)

        try:
            result: dict[str, Any] = refresh_and_rebuild(root=folder, progress=step)
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
    """Run the detached-worker CLI entry point for a queued job ID.

    The module is invoked by :func:`start_job` as
    ``python -m zooniverse_dashboard.refresh_job <run_id>``. Invalid argument
    shapes exit with status 2; a completed job with ``state == 'ok'`` exits 0,
    and any other result exits 1.

    Returns
    -------
    None
        This function always exits rather than returning normally.

    Raises
    ------
    SystemExit
        With the CLI exit status described above.
    """
    if len(sys.argv) != 2 or re.fullmatch(r"[a-f0-9]{32}", sys.argv[1]) is None:
        raise SystemExit(2)
    result: dict[str, Any] = run_job(sys.argv[1])
    raise SystemExit(0 if result["state"] == "ok" else 1)


if __name__ == "__main__":
    main()
