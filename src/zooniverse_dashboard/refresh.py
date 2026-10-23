"""Single-volume refresh coordination, ERAS reconciliation and safe status metadata."""
from __future__ import annotations

import fcntl
import json
import os
import stat
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from zooniverse_dashboard.config import data_dir
from zooniverse_dashboard.eras import fetch_classifications, fetch_comments
from zooniverse_dashboard.snapshots import (
    SOURCES, _atomic_publish, load_snapshot, publish_snapshot
)
from zooniverse_dashboard.sources import fetch_panoptes

STATUS_FORMAT = "ZOONIVERSE_REFRESH_STATUS_V1"
COOLDOWN_MINUTES = 60
MAX_ERAS_REQUESTS = 48
ERAS_CHUNK_DAYS = 35


class RefreshRejected(ValueError):
    """Expected lock, cooldown or validation failure, safe for UI display."""


@contextmanager
def refresh_lock(root: Path | None = None) -> Iterator[None]:
    """POSIX flock shared by all Gunicorn workers on ONE mounted volume."""
    folder = root if root is not None else data_dir()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(folder / ".refresh.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
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
    if (not isinstance(doc, dict) or set(doc) != {
        "format", "started_at_utc", "finished_at_utc", "state", "streams"
    } or doc["format"] != STATUS_FORMAT
        or doc["state"] not in {"running", "ok", "partial_failure"}
        or not isinstance(doc["streams"], dict) or set(doc["streams"]) != set(SOURCES)
        or any(value not in {"pending", "ok", "failed"} for value in doc["streams"].values())):
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


def _fetch_eras_range(source: str, start: date, through: date) -> dict[date, int]:
    """Bounded public ERAS day counts, with zero-filled missing dates."""
    if source not in {"classifications", "talks"} or start > through:
        raise ValueError("Invalid ERAS request")
    fetcher = fetch_classifications if source == "classifications" else fetch_comments
    result: dict[date, int] = {}
    current = start
    requests = 0
    while current <= through:
        upper = min(current + timedelta(days=ERAS_CHUNK_DAYS - 1), through)
        requests += 1
        if requests > MAX_ERAS_REQUESTS:
            raise ValueError("ERAS backfill too large for a single request")
        response = fetcher(current, upper)
        if (response.start != current or response.end != upper
                or response.reported_total != response.observed_total):
            raise ValueError("ERAS aggregate response failed validation")
        for day, count in response.calendar_series():
            result[day] = count
        current = upper + timedelta(days=1)
    return result


def refresh_source(
    source: str, *, through: date, root: Path | None = None,
    backfill_start: date | None = None, now: datetime | None = None,
):
    """Produce a complete daily-count JSON snapshot from one approved source.

    For first ERAS refresh, initialize the most recent 28 complete days only.
    An explicit CLI backfill is required for full-year historical KPI coverage.
    Existing ERAS history is reconciled over 14 days on every refresh.
    """
    folder = root if root is not None else data_dir()
    if source not in SOURCES:
        raise ValueError("Invalid analytics stream")
    if source == "users":
        counts = fetch_panoptes(through=through)
    else:
        try:
            previous = load_snapshot(source, root=folder)
        except ValueError:
            previous = None
        if backfill_start is not None:
            if backfill_start > through:
                raise ValueError("Invalid backfill date")
            start = backfill_start
            counts = {}
        elif previous is None:
            start = through - timedelta(days=27)
            counts = {}
        else:
            if through < previous.last:
                raise ValueError("Cannot move reporting cutoff backward")
            start = max(previous.first, previous.last - timedelta(days=13))
            counts = previous.counts()
        counts.update(_fetch_eras_range(source, start, through))
    return publish_snapshot(source, counts, through=through, root=folder, now=now)


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
                refresh_source(source, through=through, root=folder)
                status["streams"][source] = "ok"
            except Exception:
                # Never expose URLs, SQL exception strings or API response bodies.
                status["streams"][source] = "failed"
        status["state"] = (
            "ok" if all(x == "ok" for x in status["streams"].values())
            else "partial_failure"
        )
        status["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        _write_status(folder, status)
        return status
