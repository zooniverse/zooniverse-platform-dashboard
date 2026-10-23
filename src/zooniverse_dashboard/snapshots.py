"""Validated, aggregate-only daily snapshots and atomic durable publication."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from zooniverse_dashboard.config import data_dir

FORMAT = "ZOONIVERSE_DAILY_AGGREGATES_V1"
SOURCES = ("users", "classifications", "talks")
MAX_DAYS = 35 * 366
MAX_BYTES = 1_500_000


@dataclass(frozen=True)
class Snapshot:
    """One complete UTC daily time series without individual records."""

    source: str
    first: date
    last: date
    as_of: date
    last_modified: datetime
    daily: tuple[tuple[date, int], ...]

    def counts(self) -> dict[date, int]:
        return dict(self.daily)


def snapshot_path(source: str, root: Path | None = None) -> Path:
    if source not in SOURCES:
        raise ValueError("Unrecognized aggregate source")
    return (root if root is not None else data_dir()) / f"{source}.json"


def _parse(doc: object, expected: str) -> Snapshot:
    if not isinstance(doc, dict) or set(doc) != {
        "format", "source", "first", "last", "as_of", "last_modified_utc", "daily"
    }:
        raise ValueError("Invalid aggregate snapshot envelope")
    if doc["format"] != FORMAT or doc["source"] != expected or expected not in SOURCES:
        raise ValueError("Unexpected aggregate snapshot source")
    first = date.fromisoformat(doc["first"])
    last = date.fromisoformat(doc["last"])
    as_of = date.fromisoformat(doc["as_of"])
    modified = datetime.fromisoformat(doc["last_modified_utc"])
    span = (last - first).days + 1
    if not 1 <= span <= MAX_DAYS or as_of != last + timedelta(days=1):
        raise ValueError("Invalid aggregate coverage")
    if modified.tzinfo is None or modified.utcoffset() != timedelta(0):
        raise ValueError("Snapshot timestamp must be UTC")
    rows = doc["daily"]
    if not isinstance(rows, list) or len(rows) != span:
        raise ValueError("Aggregate snapshot must contain each date")
    daily = []
    for i, row in enumerate(rows):
        day = first + timedelta(days=i)
        if (not isinstance(row, list) or len(row) != 2
                or row[0] != day.isoformat()
                or type(row[1]) is not int or row[1] < 0):
            raise ValueError("Invalid aggregate daily bucket")
        daily.append((day, row[1]))
    return Snapshot(expected, first, last, as_of, modified, tuple(daily))


def load_snapshot(source: str, *, root: Path | None = None) -> Snapshot:
    """Load only a small, marked JSON file, never any remote database."""
    path = snapshot_path(source, root)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError("Snapshot missing, unsafe or oversized")
    return _parse(json.loads(path.read_text(encoding="utf-8")), source)


def _atomic_publish(path: Path, document: dict) -> None:
    if path.suffix != ".json" or path.is_symlink():
        raise ValueError("Invalid snapshot destination")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".analytics-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(document, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_symlink():
            raise ValueError("Snapshot destination changed")
        os.replace(tmp, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def publish_snapshot(
    source: str,
    counts: Mapping[date, int],
    *,
    through: date,
    root: Path | None = None,
    now: datetime | None = None,
) -> Snapshot:
    """Validate and atomically save daily counts; record *each* successful refresh.

    Callers provide ONLY counts already grouped at source. Any missing day
    inside the requested range must be explicitly assigned zero beforehand.
    """
    stamp = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None or through >= stamp.astimezone(timezone.utc).date():
        raise ValueError("Reporting cutoff must be a completed UTC day")
    if not counts or any(type(v) is not int or v < 0 for v in counts.values()):
        raise ValueError("Invalid aggregate counts")
    if any(not isinstance(d, date) or isinstance(d, datetime) for d in counts):
        raise ValueError("Invalid aggregate dates")
    first = min(counts)
    if first > through or max(counts) > through or (through - first).days + 1 > MAX_DAYS:
        raise ValueError("Unsupported aggregate coverage")
    if len(counts) != (through - first).days + 1:
        raise ValueError("Aggregate counts must have continuous calendar coverage")
    path = snapshot_path(source, root)
    if path.exists():
        load_snapshot(source, root=root)  # Never replace arbitrary or corrupt files.
    doc = {
        "format": FORMAT,
        "source": source,
        "first": first.isoformat(),
        "last": through.isoformat(),
        "as_of": (through + timedelta(days=1)).isoformat(),
        "last_modified_utc": stamp.astimezone(timezone.utc).isoformat(),
        "daily": [[d.isoformat(), counts[d]] for d in sorted(counts)],
    }
    parsed = _parse(doc, source)
    if len(json.dumps(doc)) > MAX_BYTES:
        raise ValueError("Aggregate exceeds storage limit")
    _atomic_publish(path, doc)
    return parsed
