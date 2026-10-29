"""Single-volume manual refresh coordination and safe status metadata."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import stat
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from zooniverse_dashboard.collection_config import effective_history_days
from zooniverse_dashboard.config import data_dir
from zooniverse_dashboard.eras import (
    fetch_full_classifications,
    fetch_full_comments,
    fetch_window_classifications,
    fetch_window_comments,
)
from zooniverse_dashboard.logging_utils import safe_error_trace
from zooniverse_dashboard.project_launches import fetch_project_launches, publish_launches
from zooniverse_dashboard.snapshots import MAX_DAYS, SOURCES, _atomic_publish, publish_snapshot
from zooniverse_dashboard.sources import fetch_panoptes

STATUS_FORMAT = "ZOONIVERSE_REFRESH_STATUS_V1"
COOLDOWN_MINUTES = 60
LOG = logging.getLogger("zooniverse_dashboard.refresh")
LAUNCH_STREAM = "project_launches"
REFRESH_STREAMS = (*SOURCES, LAUNCH_STREAM)


class RefreshRejected(ValueError):
    """Expected lock, cooldown or validation failure, safe for UI display."""


@contextmanager
def refresh_lock(root: Path | None = None) -> Iterator[None]:
    """POSIX flock shared by all Gunicorn workers on ONE mounted volume."""
    folder = root if root is not None else data_dir()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(
        folder / ".refresh.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RefreshRejected("Invalid refresh lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RefreshRejected("Another refresh is already running") from exc
        yield
    finally:
        os.close(fd)


def load_status(root: Path | None = None) -> dict | None:
    path = (root if root is not None else data_dir()) / "refresh_status.json"
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
        raise ValueError("Invalid refresh status")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(doc, dict)
        or set(doc) != {"format", "started_at_utc", "finished_at_utc", "state", "streams"}
        or doc["format"] != STATUS_FORMAT
        or doc["state"] not in {"running", "ok", "partial_failure"}
        or not isinstance(doc["streams"], dict)
        or set(doc["streams"]) not in (set(SOURCES), set(REFRESH_STREAMS))
        or any(value not in {"pending", "ok", "failed"} for value in doc["streams"].values())
    ):
        raise ValueError("Invalid refresh status")
    for key in ("started_at_utc", "finished_at_utc"):
        if doc[key] is not None:
            stamp = datetime.fromisoformat(doc[key])
            if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
                raise ValueError("Invalid status time")
    return doc


def _write_status(root: Path, status: dict) -> None:
    path = root / "refresh_status.json"
    if path.exists():
        load_status(root)
    _atomic_publish(path, status)


def refresh_source(
    source: str,
    *,
    through: date,
    root: Path | None = None,
    now: datetime | None = None,
    history_days: int | None = None,
):
    """Replace one entire configured history window with validated aggregates.

    Each step is logged without emitting database records, URL strings, raw
    remote exceptions, SQL parameters or API payloads.
    """
    started = time.perf_counter()
    phase = "validate"
    folder = root if root is not None else data_dir()
    try:
        if source not in SOURCES:
            raise ValueError("Invalid analytics stream")
        days = effective_history_days(folder) if history_days is None else history_days
        if type(days) is not int or not 0 <= days <= MAX_DAYS:
            raise ValueError("Invalid history limit")
        LOG.info(
            "event=source_fetch_started source=%s through=%s history_days=%d",
            source,
            through.isoformat(),
            days,
        )
        phase = "fetch_and_validate"
        if source == "users":
            counts = fetch_panoptes(through=through, history_days=days)
        else:
            if days == 0:
                fetcher = (
                    fetch_full_classifications
                    if source == "classifications"
                    else fetch_full_comments
                )
                result = fetcher(through)
                if result.end != through or result.reported_total != result.observed_total:
                    raise ValueError("ERAS full-history aggregate failed validation")
            else:
                start = through - timedelta(days=days - 1)
                fetcher = (
                    fetch_window_classifications
                    if source == "classifications"
                    else fetch_window_comments
                )
                result = fetcher(start, through)
                if (
                    result.start != start
                    or result.end != through
                    or result.reported_total != result.observed_total
                ):
                    raise ValueError("ERAS date-range aggregate failed validation")
            counts = dict(result.calendar_series())
        LOG.info(
            "event=source_fetch_completed source=%s buckets=%d aggregate_total=%d "
            "elapsed_ms=%.1f",
            source,
            len(counts),
            sum(counts.values()),
            (time.perf_counter() - started) * 1000,
        )
        phase = "snapshot_publication"
        snapshot = publish_snapshot(
            source,
            counts,
            through=through,
            root=folder,
            now=now,
            history_complete=(days == 0),
            history_days=days,
        )
        LOG.info(
            "event=snapshot_published source=%s first=%s last=%s last_modified_utc=%s "
            "elapsed_ms=%.1f",
            source,
            snapshot.first.isoformat(),
            snapshot.last.isoformat(),
            snapshot.last_modified.isoformat(),
            (time.perf_counter() - started) * 1000,
        )
        return snapshot
    except Exception as exc:
        LOG.error(
            "event=source_failed source=%s phase=%s error_type=%s trace=%s elapsed_ms=%.1f",
            source if source in SOURCES else "invalid",
            phase,
            type(exc).__name__,
            safe_error_trace(exc),
            (time.perf_counter() - started) * 1000,
        )
        raise


def refresh_launches(
    *,
    through: date,
    root: Path | None = None,
    now: datetime | None = None,
    history_days: int = 0,
):
    """Collect and atomically publish project-launch context, independently."""
    started = time.perf_counter()
    phase = "fetch_and_validate"
    try:
        launch_data = fetch_project_launches(through=through, history_days=history_days)
        LOG.info(
            "event=source_fetch_completed source=%s buckets=%d aggregate_total=%d elapsed_ms=%.1f",
            LAUNCH_STREAM,
            len(launch_data.counts),
            sum(launch_data.counts.values()),
            (time.perf_counter() - started) * 1000,
        )
        phase = "snapshot_publication"
        result = publish_launches(
            launch_data.counts,
            names=launch_data.names,
            through=through,
            history_days=history_days,
            root=root,
            now=now,
        )
        LOG.info(
            "event=snapshot_published source=%s first=%s last=%s "
            "last_modified_utc=%s elapsed_ms=%.1f",
            LAUNCH_STREAM,
            result.first.isoformat(),
            result.last.isoformat(),
            result.last_modified.isoformat(),
            (time.perf_counter() - started) * 1000,
        )
        return result
    except Exception as exc:
        LOG.error(
            "event=source_failed source=%s phase=%s error_type=%s trace=%s elapsed_ms=%.1f",
            LAUNCH_STREAM,
            phase,
            type(exc).__name__,
            safe_error_trace(exc),
            (time.perf_counter() - started) * 1000,
        )
        raise


def refresh_all(*, root: Path | None = None, now: datetime | None = None, progress=None) -> dict:
    """Synchronous, serialized on-demand refresh; no scheduler is started."""
    started = time.perf_counter()
    run_id = uuid.uuid4().hex[:12]
    LOG.info("event=refresh_requested run=%s", run_id)
    try:
        folder = root if root is not None else data_dir()
        stamp = now or datetime.now(timezone.utc)
        if stamp.tzinfo is None:
            raise RefreshRejected("Refresh clock must be timezone-aware")
        stamp = stamp.astimezone(timezone.utc)
        through = stamp.date() - timedelta(days=1)
        with refresh_lock(folder):
            prior = load_status(folder)
            if prior is not None:
                previous_time = datetime.fromisoformat(prior["started_at_utc"])
                if stamp < previous_time + timedelta(minutes=COOLDOWN_MINUTES):
                    raise RefreshRejected("Refresh was attempted within the last hour")
            days = effective_history_days(folder)
            LOG.info(
                "event=refresh_started run=%s cutoff=%s history_days=%d streams=%d",
                run_id,
                through.isoformat(),
                days,
                len(REFRESH_STREAMS),
            )
            status = {
                "format": STATUS_FORMAT,
                "started_at_utc": stamp.isoformat(),
                "finished_at_utc": None,
                "state": "running",
                "streams": {name: "pending" for name in REFRESH_STREAMS},
            }
            _write_status(folder, status)
            if progress is not None:
                progress("Retrieving validated daily aggregates...")
            for source in REFRESH_STREAMS:
                source_started = time.perf_counter()
                LOG.info("event=source_started run=%s source=%s", run_id, source)
                if progress is not None:
                    progress(f"Fetching {source} aggregates...")
                try:
                    if source == LAUNCH_STREAM:
                        refresh_launches(through=through, root=folder, history_days=days, now=stamp)
                    else:
                        refresh_source(source, through=through, root=folder, history_days=days)
                    status["streams"][source] = "ok"
                    _write_status(folder, status)
                    if progress is not None:
                        progress(f"{source}: snapshot validated and published")
                    LOG.info(
                        "event=source_succeeded run=%s source=%s elapsed_ms=%.1f",
                        run_id,
                        source,
                        (time.perf_counter() - source_started) * 1000,
                    )
                except Exception as exc:
                    # Never log exception messages: drivers can embed sensitive URLs.
                    status["streams"][source] = "failed"
                    _write_status(folder, status)
                    if progress is not None:
                        progress(f"{source}: refresh failed; previous valid snapshot retained")
                    LOG.warning(
                        "event=source_status_failed run=%s source=%s error_type=%s "
                        "elapsed_ms=%.1f",
                        run_id,
                        source,
                        type(exc).__name__,
                        (time.perf_counter() - source_started) * 1000,
                    )
            status["state"] = (
                "ok" if all(x == "ok" for x in status["streams"].values()) else "partial_failure"
            )
            status["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
            _write_status(folder, status)
            LOG.info(
                "event=refresh_finished run=%s state=%s succeeded=%d failed=%d " "elapsed_ms=%.1f",
                run_id,
                status["state"],
                sum(v == "ok" for v in status["streams"].values()),
                sum(v == "failed" for v in status["streams"].values()),
                (time.perf_counter() - started) * 1000,
            )
            return status
    except RefreshRejected as exc:
        # RefreshRejected is an internal fixed-text operational exception.
        LOG.warning("event=refresh_rejected run=%s reason=%s", run_id, str(exc))
        raise
    except Exception as exc:
        LOG.error(
            "event=refresh_aborted run=%s error_type=%s trace=%s elapsed_ms=%.1f",
            run_id,
            type(exc).__name__,
            safe_error_trace(exc),
            (time.perf_counter() - started) * 1000,
        )
        raise
