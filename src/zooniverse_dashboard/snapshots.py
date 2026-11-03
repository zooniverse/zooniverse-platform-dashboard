"""Validate, load, and atomically publish privacy-safe daily snapshots.

Each activity source (registrations, classifications, and Talk comments)
stores a continuous sequence of completed UTC daily **aggregate counts**
in a bounded JSON file. This module enforces source identity, calendar
coverage, nonnegative integer counts, file size limits, and atomic
replacement. Neither the dashboard nor the anomaly detector needs
individual Panoptes or ERAS records to read this format.

The separate :mod:`zooniverse_dashboard.project_launches` module owns
sparse project-launch snapshots; its schema must not be confused with
the continuous activity-series schema implemented here.

See Also
--------
:mod:`zooniverse_dashboard.collection_config`
    Configures the requested collection history window.
:class:`Snapshot`
    In-memory representation of validated daily counts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Final, Mapping

from zooniverse_dashboard.config import data_dir
from zooniverse_dashboard.storage import atomic_publish_json

FORMAT: Final[str] = "ZOONIVERSE_DAILY_AGGREGATES_V1"
SOURCES: Final[tuple[str, ...]] = ("users", "classifications", "talks")
MAX_DAYS: Final[int] = 35 * 366
MAX_BYTES: Final[int] = 1_500_000


@dataclass(frozen=True)
class Snapshot:
    """Immutable, validated daily activity series for one source.

    Instances created by :func:`load_snapshot` or :func:`publish_snapshot`
    represent a continuous, fully completed UTC day sequence containing
    aggregate counts only. The ``history_complete`` and ``history_days``
    fields convey collection coverage, not whether every day had activity.

    Attributes
    ----------
    source : str
        Identifies ``users``, ``classifications``, or ``talks``.
    first : datetime.date
        Earliest observed calendar day, inclusive.
    last : datetime.date
        Latest completed day, inclusive.
    as_of : datetime.date
        Reporting boundary at midnight UTC after ``last``.
    last_modified : datetime.datetime
        Timestamp of the successful collection, with UTC timezone.
    daily : tuple[tuple[datetime.date, int], ...]
        Continuous chronological sequence of dates and nonnegative counts.
    history_complete : bool
        Indicates an upstream full-history collection rather than an
        incomplete historical source response; defaults to ``False``.
    history_days : int
        Requested history window: zero for all history, a positive daily
        limit. The canonical snapshot always records this field.
    """

    source: str
    first: date
    last: date
    as_of: date
    last_modified: datetime
    daily: tuple[tuple[date, int], ...]
    history_complete: bool = False
    history_days: int = 0  # Zero means all-time.

    def counts(self) -> dict[date, int]:
        """Create a date-to-count lookup from the validated daily rows.

        Returns
        -------
        dict[datetime.date, int]
            A new dictionary containing one count for each available UTC date.
            Modifying it does not mutate the :class:`Snapshot` instance.
        """
        return dict(self.daily)


def snapshot_path(source: str, root: Path | None = None) -> Path:
    """Resolve the JSON file path for a recognized activity source.

    Parameters
    ----------
    source : str
        One of :data:`SOURCES`.
    root : pathlib.Path or None, optional
        Alternative data root; defaults to
        :func:`zooniverse_dashboard.config.data_dir`.

    Returns
    -------
    pathlib.Path
        Expected file path for the source's daily aggregate snapshot.

    Raises
    ------
    ValueError
        If ``source`` is not a recognized aggregate source.
    """
    if source not in SOURCES:
        raise ValueError("Unrecognized aggregate source")
    return (root if root is not None else data_dir()) / f"{source}.json"


def _parse(doc: object, expected: str) -> Snapshot:
    """Validate a decoded snapshot document against the requested source.

    All required coverage markers must be present. Every day between
    ``first`` and ``last`` must appear exactly once in chronological order.

    Parameters
    ----------
    doc : object
        Decoded JSON value to validate; expected to be a mapping.
    expected : str
        Expected source identifier, one of :data:`SOURCES`.

    Returns
    -------
    :class:`Snapshot`
        Fully parsed in-memory daily series.

    Raises
    ------
    ValueError
        If identity, schema, UTC timestamps, date coverage, counts, or
        recorded history settings are inconsistent.
    TypeError
        If malformed JSON field types cannot be parsed as dates or times.
    """
    required: set[str] = {
        "format",
        "source",
        "first",
        "last",
        "as_of",
        "last_modified_utc",
        "daily",
        "history_complete",
        "history_days",
    }
    if not isinstance(doc, dict) or set(doc) != required:
        raise ValueError("Invalid aggregate snapshot envelope")
    if doc["format"] != FORMAT or doc["source"] != expected or expected not in SOURCES:
        raise ValueError("Unexpected aggregate snapshot source")
    complete: object = doc["history_complete"]
    if type(complete) is not bool:
        raise ValueError("Invalid history coverage marker")
    history_days: object = doc["history_days"]
    if type(history_days) is not int or not 0 <= history_days <= MAX_DAYS:
        raise ValueError("Invalid snapshot date limit")
    first: date = date.fromisoformat(doc["first"])
    last: date = date.fromisoformat(doc["last"])
    as_of: date = date.fromisoformat(doc["as_of"])
    modified: datetime = datetime.fromisoformat(doc["last_modified_utc"])
    span: int = (last - first).days + 1
    if not 1 <= span <= MAX_DAYS or as_of != last + timedelta(days=1):
        raise ValueError("Invalid aggregate coverage")
    if modified.tzinfo is None or modified.utcoffset() != timedelta(0):
        raise ValueError("Snapshot timestamp must be UTC")
    rows: object = doc["daily"]
    if not isinstance(rows, list) or len(rows) != span:
        raise ValueError("Aggregate snapshot must contain each date")
    daily: list[tuple[date, int]] = []
    for i, row in enumerate(rows):
        day: date = first + timedelta(days=i)
        if (
            not isinstance(row, list)
            or len(row) != 2
            or row[0] != day.isoformat()
            or type(row[1]) is not int
            or row[1] < 0
        ):
            raise ValueError("Invalid aggregate daily bucket")
        daily.append((day, row[1]))
    if history_days and first != last - timedelta(days=history_days - 1):
        raise ValueError("Snapshot date range does not match requested limit")
    return Snapshot(expected, first, last, as_of, modified, tuple(daily), complete, history_days)


def load_snapshot(source: str, *, root: Path | None = None) -> Snapshot:
    """Load and validate one bounded local aggregate JSON snapshot.

    This reader does not access the network or query Panoptes/ERAS. Symlink,
    missing, unsafe, or oversized snapshot paths are rejected before parsing.

    Parameters
    ----------
    source : str
        Activity source, one of :data:`SOURCES`.
    root : pathlib.Path or None, optional
        Local snapshot directory, defaulting to the configured analytics
        data directory.

    Returns
    -------
    :class:`Snapshot`
        Validated daily aggregate data and coverage metadata.

    Raises
    ------
    ValueError
        If the path is unsafe or missing, the file is oversized, or its
        contents fail snapshot validation.
    OSError
        If an existing snapshot cannot be read or inspected.
    json.JSONDecodeError
        If the file does not contain valid JSON text.
    """
    path: Path = snapshot_path(source, root)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError("Snapshot missing, unsafe or oversized")
    return _parse(json.loads(path.read_text(encoding="utf-8")), source)


def publish_snapshot(
    source: str,
    counts: Mapping[date, int],
    *,
    through: date,
    root: Path | None = None,
    now: datetime | None = None,
    history_complete: bool = True,
    history_days: int = 0,
) -> Snapshot:
    """Validate and atomically publish daily counts from an aggregate source.

    The caller must first group records at the upstream source and explicitly
    supply zeros for all dates without activity. This function rejects gaps,
    invalid count types, unsafe existing files, and invalid coverage metadata.
    A successful write records its timestamp and history-window settings.

    Parameters
    ----------
    source : str
        Destination source, one of :data:`SOURCES`.
    counts : collections.abc.Mapping[datetime.date, int]
        Date-to-aggregate-count mapping with uninterrupted daily coverage.
        No individual user, classification, or comment records are accepted.
    through : datetime.date
        Last completed UTC reporting day, inclusive.
    root : pathlib.Path or None, optional
        Output directory, defaulting to the configured analytics root.
    now : datetime.datetime or None, optional
        Current timezone-aware timestamp used to validate ``through`` and
        record publication time; defaults to the current UTC timestamp.
    history_complete : bool, optional
        Whether the collector retrieved its complete available history.
    history_days : int, optional
        Requested collection history in days; zero means all available dates.

    Returns
    -------
    :class:`Snapshot`
        Validated, newly published aggregate snapshot.

    Raises
    ------
    ValueError
        If the reporting cutoff, counts, source, date span, coverage
        metadata, or existing destination are invalid, or the serialized
        document exceeds the byte limit.
    OSError
        If snapshot publication fails due to a filesystem error.
    """
    stamp: datetime = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None or through >= stamp.astimezone(timezone.utc).date():
        raise ValueError("Reporting cutoff must be a completed UTC day")
    if type(history_complete) is not bool:
        raise ValueError("Invalid history coverage marker")
    if type(history_days) is not int or not 0 <= history_days <= MAX_DAYS:
        raise ValueError("Invalid snapshot date limit")
    if not counts or any(type(v) is not int or v < 0 for v in counts.values()):
        raise ValueError("Invalid aggregate counts")
    if any(not isinstance(d, date) or isinstance(d, datetime) for d in counts):
        raise ValueError("Invalid aggregate dates")
    first: date = min(counts)
    if first > through or max(counts) > through or (through - first).days + 1 > MAX_DAYS:
        raise ValueError("Unsupported aggregate coverage")
    if len(counts) != (through - first).days + 1:
        raise ValueError("Aggregate counts must have continuous calendar coverage")
    path = snapshot_path(source, root)
    if path.exists():
        load_snapshot(source, root=root)  # Never replace arbitrary or corrupt files.
    doc: dict[str, object] = {
        "format": FORMAT,
        "source": source,
        "first": first.isoformat(),
        "last": through.isoformat(),
        "as_of": (through + timedelta(days=1)).isoformat(),
        "last_modified_utc": stamp.astimezone(timezone.utc).isoformat(),
        "history_complete": history_complete,
        "history_days": history_days,
        "daily": [[d.isoformat(), counts[d]] for d in sorted(counts)],
    }
    parsed: Snapshot = _parse(doc, source)
    if len(json.dumps(doc)) > MAX_BYTES:
        raise ValueError("Aggregate exceeds storage limit")
    atomic_publish_json(path, doc)
    return parsed
