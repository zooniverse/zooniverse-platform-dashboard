"""Approved Panoptes launches: daily counts and *public* project display names.

Names are optional context, not user analytics or inputs to anomaly detection.
Project IDs, owner details, private names and individual timestamps stay in Panoptes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Mapping, Sequence

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from zooniverse_dashboard.config import data_dir, panoptes_url
from zooniverse_dashboard.snapshots import MAX_DAYS, _atomic_publish

LEGACY_FORMAT = "ZOONIVERSE_PROJECT_LAUNCHES_V1"
FORMAT = "ZOONIVERSE_PROJECT_LAUNCHES_V2"
MAX_BYTES = 250_000
MAX_TITLE_CHARS = 160
MAX_NAMES_PER_DAY = 1000

# Grouped results only. The count includes all approved projects, but the
# name array includes only currently non-private projects. Panoptes Project's
# title is an alias of display_name; see app/models/project.rb upstream.
# Do not select owner/ID or expose individual launch timestamps.
_SELECT = (
    "SELECT launch_date::date AS day, COUNT(*)::bigint AS launches, "
    "COALESCE(array_agg(LEFT(BTRIM(display_name), 160) ORDER BY display_name) "
    "FILTER (WHERE private IS FALSE AND display_name IS NOT NULL "
    "AND BTRIM(display_name) <> ''), ARRAY[]::text[]) AS public_names "
    "FROM public.projects WHERE launch_approved IS TRUE "
    "AND launch_date IS NOT NULL "
)
_END = "AND launch_date < :end_exclusive GROUP BY launch_date::date ORDER BY day"
LAUNCHES_SQL = text(_SELECT + _END)
LAUNCHES_BOUNDED_SQL = text(_SELECT + "AND launch_date >= :start_inclusive " + _END)


@dataclass(frozen=True)
class LaunchSnapshot:
    """Sparse UTC dates, approved launch counts, and optional public names."""

    first: date
    last: date
    as_of: date
    last_modified: datetime
    history_days: int
    launches: tuple[tuple[date, int], ...]
    names_by_day: tuple[tuple[date, tuple[str, ...]], ...] = ()

    def counts(self) -> dict[date, int]:
        return dict(self.launches)

    def names_for(self, day: date) -> tuple[str, ...]:
        return dict(self.names_by_day).get(day, ())


@dataclass(frozen=True)
class LaunchAggregates:
    """Only one SQL row per launch day, never one row per project."""

    counts: dict[date, int]
    names: dict[date, tuple[str, ...]]


def _clean_name(name: str) -> str:
    """Normalize whitespace; markup is escaped later, at Plotly presentation."""
    if not isinstance(name, str):
        raise ValueError("Invalid public project title")
    return " ".join(name.split())[:MAX_TITLE_CHARS]


def _validate_names(names: Sequence[str], *, count: int) -> tuple[str, ...]:
    if not isinstance(names, (list, tuple)) or len(names) > count or len(names) > MAX_NAMES_PER_DAY:
        raise ValueError("Invalid public project name collection")
    valid = []
    for name in names:
        if (
            not isinstance(name, str)
            or not name
            or len(name) > MAX_TITLE_CHARS
            or _clean_name(name) != name
        ):
            raise ValueError("Invalid public project title")
        valid.append(name)
    return tuple(valid)


def fetch_project_launches(*, through: date, history_days: int = 0) -> LaunchAggregates:
    """Return daily approved counts plus public display names, read-only.

    Public titles may be user-authored. Never log or persist them outside the
    restricted local overlay, and never pass them to anomaly analysis.
    """
    if type(history_days) is not int or not 0 <= history_days <= MAX_DAYS:
        raise ValueError("Invalid history limit")
    start = through - timedelta(days=history_days - 1) if history_days else None
    engine = create_engine(panoptes_url(), poolclass=NullPool, connect_args={"connect_timeout": 5})
    try:
        if engine.dialect.name != "postgresql":
            raise ValueError("PostgreSQL required")
        with engine.connect() as conn, conn.begin():
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            conn.exec_driver_sql("SET LOCAL statement_timeout = 20000")
            conn.exec_driver_sql("SET LOCAL lock_timeout = 250")
            conn.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout = 35000")
            conn.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
            bound = datetime.combine(through + timedelta(days=1), time.min)
            if start is None:
                rows = conn.execute(LAUNCHES_SQL, {"end_exclusive": bound}).all()
            else:
                rows = conn.execute(
                    LAUNCHES_BOUNDED_SQL,
                    {
                        "start_inclusive": datetime.combine(start, time.min),
                        "end_exclusive": bound,
                    },
                ).all()
        counts: dict[date, int] = {}
        names: dict[date, tuple[str, ...]] = {}
        for day, count, public_names in rows:
            if (
                not isinstance(day, date)
                or isinstance(day, datetime)
                or day > through
                or (start is not None and day < start)
                or type(count) is not int
                or count < 1
                or day in counts
            ):
                raise ValueError("Unexpected project launch aggregate")
            if not isinstance(public_names, (list, tuple)):
                raise ValueError("Invalid public project title array")
            cleaned = tuple(
                cleaned_name for title in public_names if (cleaned_name := _clean_name(title))
            )
            counts[day] = count
            names[day] = _validate_names(cleaned, count=count)
        return LaunchAggregates(counts, names)
    finally:
        engine.dispose()


def launch_path(root: Path | None = None) -> Path:
    return (root if root is not None else data_dir()) / "project_launches.json"


def _parse(doc: object) -> LaunchSnapshot:
    keys = {"format", "first", "last", "as_of", "last_modified_utc", "history_days", "launches"}
    if (
        not isinstance(doc, dict)
        or set(doc) != keys
        or doc["format"]
        not in {
            FORMAT,
            LEGACY_FORMAT,
        }
    ):
        raise ValueError("Invalid project launch snapshot envelope")
    legacy = doc["format"] == LEGACY_FORMAT
    days = doc["history_days"]
    if type(days) is not int or not 0 <= days <= MAX_DAYS:
        raise ValueError("Invalid project launch history limit")
    first = date.fromisoformat(doc["first"])
    last = date.fromisoformat(doc["last"])
    as_of = date.fromisoformat(doc["as_of"])
    modified = datetime.fromisoformat(doc["last_modified_utc"])
    if (
        first > last
        or (last - first).days + 1 > MAX_DAYS
        or as_of != last + timedelta(days=1)
        or modified.tzinfo is None
        or modified.utcoffset() != timedelta(0)
        or (days > 0 and first != last - timedelta(days=days - 1))
    ):
        raise ValueError("Invalid project launch snapshot coverage")
    rows = doc["launches"]
    if not isinstance(rows, list) or len(rows) > MAX_DAYS:
        raise ValueError("Invalid project launch dates")
    pairs = []
    named = []
    prev = None
    for row in rows:
        if not isinstance(row, list) or len(row) != (2 if legacy else 3):
            raise ValueError("Invalid project launch bucket")
        day = date.fromisoformat(row[0])
        count = row[1]
        if (
            day < first
            or day > last
            or (prev is not None and day <= prev)
            or type(count) is not int
            or count < 1
        ):
            raise ValueError("Invalid project launch bucket")
        if not legacy:
            named.append((day, _validate_names(row[2], count=count)))
        pairs.append((day, count))
        prev = day
    if days == 0 and pairs and pairs[0][0] != first:
        raise ValueError("Inconsistent all-time launch coverage")
    if days == 0 and not pairs and first != last:
        raise ValueError("Inconsistent empty launch coverage")
    return LaunchSnapshot(first, last, as_of, modified, days, tuple(pairs), tuple(named))


def load_launches(*, root: Path | None = None) -> LaunchSnapshot:
    """Load V1 counts or V2 counts + names. Missing/invalid overlay is nonfatal."""
    path = launch_path(root)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError("Project launch snapshot missing, unsafe or oversized")
    return _parse(json.loads(path.read_text(encoding="utf-8")))


def publish_launches(
    counts: Mapping[date, int],
    *,
    names: Mapping[date, Sequence[str]] | None = None,
    through: date,
    history_days: int = 0,
    root: Path | None = None,
    now: datetime | None = None,
) -> LaunchSnapshot:
    """Atomically replace a V1/V2 overlay with a strictly validated V2 one."""
    stamp = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None or through >= stamp.astimezone(timezone.utc).date():
        raise ValueError("Reporting cutoff must be a completed UTC day")
    if type(history_days) is not int or not 0 <= history_days <= MAX_DAYS:
        raise ValueError("Invalid history limit")
    first = (
        through - timedelta(days=history_days - 1) if history_days else min(counts, default=through)
    )
    if first > through or (through - first).days + 1 > MAX_DAYS:
        raise ValueError("Unsupported launch coverage")
    for day, count in counts.items():
        if (
            not isinstance(day, date)
            or isinstance(day, datetime)
            or day < first
            or day > through
            or type(count) is not int
            or count < 1
        ):
            raise ValueError("Invalid launch aggregate")
    named = names if names is not None else {}
    if not isinstance(named, Mapping) or any(day not in counts for day in named):
        raise ValueError("Project names must refer to an approved launch date")
    rows = [
        [day.isoformat(), counts[day], list(_validate_names(named.get(day, ()), count=counts[day]))]
        for day in sorted(counts)
    ]
    path = launch_path(root)
    if path.exists() or path.is_symlink():
        load_launches(root=root)  # Refuse to replace corrupt or unsafe files.
    doc = {
        "format": FORMAT,
        "first": first.isoformat(),
        "last": through.isoformat(),
        "as_of": (through + timedelta(days=1)).isoformat(),
        "last_modified_utc": stamp.astimezone(timezone.utc).isoformat(),
        "history_days": history_days,
        "launches": rows,
    }
    _parse(doc)
    if len(json.dumps(doc, separators=(",", ":")).encode("utf-8")) > MAX_BYTES:
        raise ValueError("Project launch snapshot oversized")
    _atomic_publish(path, doc)
    return _parse(doc)
