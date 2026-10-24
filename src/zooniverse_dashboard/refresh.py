"""Single-volume manual refresh coordination and safe status metadata."""

from __future__ import annotations

import fcntl
import json
import os
import stat
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
from zooniverse_dashboard.snapshots import MAX_DAYS, SOURCES, _atomic_publish, publish_snapshot
from zooniverse_dashboard.sources import fetch_panoptes

STATUS_FORMAT = "ZOONIVERSE_REFRESH_STATUS_V1"
COOLDOWN_MINUTES = 60


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
        or set(doc["streams"]) != set(SOURCES)
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

    No incremental/backfill workflow is used: even existing snapshots are
    refreshed over the full selected range on each successful call.
    """
    folder = root if root is not None else data_dir()
    if source not in SOURCES:
        raise ValueError("Invalid analytics stream")
    days = effective_history_days(folder) if history_days is None else history_days
    if type(days) is not int or not 0 <= days <= MAX_DAYS:
        raise ValueError("Invalid history limit")
    if source == "users":
        counts = fetch_panoptes(through=through, history_days=days)
    else:
        if days == 0:
            fetcher = (
                fetch_full_classifications if source == "classifications" else fetch_full_comments
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
    return publish_snapshot(
        source,
        counts,
        through=through,
        root=folder,
        now=now,
        history_complete=(days == 0),
        history_days=days,
    )


def refresh_all(*, root: Path | None = None, now: datetime | None = None) -> dict:
    """Synchronous, serialized on-demand refresh; no scheduler is started."""
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
        status = {
            "format": STATUS_FORMAT,
            "started_at_utc": stamp.isoformat(),
            "finished_at_utc": None,
            "state": "running",
            "streams": {name: "pending" for name in SOURCES},
        }
        _write_status(folder, status)
        for source in SOURCES:
            try:
                refresh_source(source, through=through, root=folder, history_days=days)
                status["streams"][source] = "ok"
            except Exception:
                # Never expose URLs, SQL exception strings or API response bodies.
                status["streams"][source] = "failed"
        status["state"] = (
            "ok" if all(x == "ok" for x in status["streams"].values()) else "partial_failure"
        )
        status["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        _write_status(folder, status)
        return status
