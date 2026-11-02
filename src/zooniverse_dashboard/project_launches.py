"""Collect and persist approved Panoptes project launches for local charts.

A read-only grouped query produces one row per UTC launch date, including
an approved-project count and optionally the display names of *currently*
non-private projects. Individual project IDs, owners, private titles and
exact timestamps never leave the database. Titles are user-authored context;
they must be escaped when displayed and never passed to anomaly detection.

The launch snapshot format is intentionally **sparse**: dates without launches
are omitted. Each JSON row stores approved counts and available public names. Only the
current V2 schema is accepted. Validated snapshots are written atomically
into the configured analytics data directory, separately from activity data.

See Also
--------
:class:`LaunchSnapshot`
    Immutable validated launch dates and optional public names.
:func:`fetch_project_launches`
    Perform the grouped Panoptes query.
:func:`publish_launches`
    Validate and atomically publish a launch overlay.
:func:`zooniverse_dashboard.projects_data.launch_buckets`
    Aggregate the sparse launch dates into calendar-month/year counts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Final, Mapping, Sequence

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.elements import TextClause

from zooniverse_dashboard.config import data_dir, panoptes_url
from zooniverse_dashboard.snapshots import MAX_DAYS
from zooniverse_dashboard.storage import atomic_publish_json

FORMAT: Final[str] = "ZOONIVERSE_PROJECT_LAUNCHES_V2"
MAX_BYTES: Final[int] = 250_000
MAX_TITLE_CHARS: Final[int] = 160
MAX_NAMES_PER_DAY: Final[int] = 1000

# Grouped results only. The count includes all approved projects, but the
# name array includes only currently non-private projects. Panoptes Project's
# title is an alias of display_name; see app/models/project.rb upstream.
# Do not select owner/ID or expose individual launch timestamps.
_SELECT: Final[str] = (
    "SELECT launch_date::date AS day, COUNT(*)::bigint AS launches, "
    "COALESCE(array_agg(LEFT(BTRIM(display_name), 160) ORDER BY display_name) "
    "FILTER (WHERE private IS FALSE AND display_name IS NOT NULL "
    "AND BTRIM(display_name) <> ''), ARRAY[]::text[]) AS public_names "
    "FROM public.projects WHERE launch_approved IS TRUE "
    "AND launch_date IS NOT NULL "
)
_END: Final[str] = "AND launch_date < :end_exclusive GROUP BY launch_date::date ORDER BY day"
LAUNCHES_SQL: Final[TextClause] = text(_SELECT + _END)
LAUNCHES_BOUNDED_SQL: Final[TextClause] = text(
    _SELECT + "AND launch_date >= :start_inclusive " + _END
)


@dataclass(frozen=True)
class LaunchSnapshot:
    """Represent one immutable, validated launch-count snapshot.

    The daily series is **sparse**: every entry in :attr:`launches` has a
    positive count, and omitted dates represent zero launches *within the
    snapshot's coverage*. The V2 format optionally records public display
    names by date. Instances produced by :func:`load_launches` or
    :func:`publish_launches` satisfy the snapshot's exact schema and chronology
    rules; constructing the dataclass directly does not validate those rules.

    Attributes
    ----------
    first : datetime.date
        Earliest day of coverage, inclusive. For all-history collection, the
        earliest known approved launch (or ``last`` if no launches exist).
    last : datetime.date
        Most recent completed UTC day, inclusive.
    as_of : datetime.date
        Next UTC day immediately after ``last``.
    last_modified : datetime.datetime
        UTC-aware timestamp when the launch snapshot was published.
    history_days : int
        Configured history window in days, or zero for all available history.
    launches : tuple[tuple[datetime.date, int], ...]
        Increasing sequence of calendar dates and strictly positive approved
        launch counts, without explicit zero days.
    names_by_day : tuple[tuple[datetime.date, tuple[str, ...]], ...]
        Optional sanitized public project titles for launch days; the number
        of titles may be smaller than the total approved count.

    See Also
    --------
    :class:`LaunchAggregates`
        Result of the grouped read-only Panoptes query prior to publication.
    """

    first: date
    last: date
    as_of: date
    last_modified: datetime
    history_days: int
    launches: tuple[tuple[date, int], ...]
    names_by_day: tuple[tuple[date, tuple[str, ...]], ...] = ()

    def counts(self) -> dict[date, int]:
        """Create a mutable date-to-count index of approved launch dates.

        Returns
        -------
        dict[datetime.date, int]
            Copy of :attr:`launches` keyed by date. Missing keys represent zero
            launches within the known collection coverage.
        """
        return dict(self.launches)

    def names_for(self, day: date) -> tuple[str, ...]:
        """Look up available public project names for a launch date.

        Parameters
        ----------
        day : datetime.date
            UTC launch calendar day to inspect.

        Returns
        -------
        tuple[str, ...]
            Sanitized public display names recorded for ``day``, or an empty
            tuple if the date has no stored public names.
            No private project names are included.
        """
        return dict(self.names_by_day).get(day, ())


@dataclass(frozen=True)
class LaunchAggregates:
    """Hold grouped Panoptes query results before snapshot publication.

    The source query returns a single row per approved launch date rather than
    per project. Counts include all approved projects; names include only
    currently non-private projects with nonempty display names. This dataclass
    is a transport container, not an independently validated persisted snapshot.

    Attributes
    ----------
    counts : dict[datetime.date, int]
        Sparse positive approved-project counts by UTC launch day.
    names : dict[datetime.date, tuple[str, ...]]
        Public display names for corresponding launch days; titles are cleaned
        and length-limited prior to storage.
    """

    counts: dict[date, int]
    names: dict[date, tuple[str, ...]]


def _clean_name(name: str) -> str:
    """Normalize a public project title before writing it to a snapshot.

    Collapse Unicode whitespace to single spaces and truncate the result to
    :data:`MAX_TITLE_CHARS` characters. This is *not* HTML sanitization: the
    presentation layer must still escape the title before adding it to a tooltip.

    Parameters
    ----------
    name : str
        Public, user-authored project display name returned by Panoptes.

    Returns
    -------
    str
        Whitespace-normalized title, truncated to the storage limit.

    Raises
    ------
    ValueError
        If ``name`` is not a string.
    """
    if not isinstance(name, str):
        raise ValueError("Invalid public project title")
    return " ".join(name.split())[:MAX_TITLE_CHARS]


def _validate_names(names: Sequence[str], *, count: int) -> tuple[str, ...]:
    """Validate and freeze the titles associated with one daily launch count.

    Require a list or tuple of normalized, nonempty strings, no more than the
    approved count and no more than :data:`MAX_NAMES_PER_DAY` entries. Titles
    must already satisfy the ``_clean_name`` normalization constraints.

    Parameters
    ----------
    names : collections.abc.Sequence[str]
        Candidate title collection for one UTC launch day.
    count : int
        Number of approved launches on that day; at most this many titles
        may be retained.

    Returns
    -------
    tuple[str, ...]
        Validated public names in original order.

    Raises
    ------
    ValueError
        If the collection is not a list/tuple, has too many entries, or
        contains invalid, empty, oversized, or non-normalized titles.
    """
    if not isinstance(names, (list, tuple)) or len(names) > count or len(names) > MAX_NAMES_PER_DAY:
        raise ValueError("Invalid public project name collection")
    valid: list[str] = []
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
    """Fetch approved project-launch counts and non-private display names.

    Execute one grouped, read-only PostgreSQL query over ``public.projects``.
    All approved launch dates through ``through`` are eligible, regardless of
    current live status. The optional bounded window adds an inclusive midnight
    start; the exclusive upper bound is midnight UTC after ``through``. A missing
    launch date yields no row, rather than a zero-count row.

    Counts include *all* approved launches, but ``public_names`` are returned
    only for projects currently marked non-private and with a nonblank title.
    The query selects neither project identifiers nor user/owner attributes.
    The transaction explicitly sets its timezone to UTC; Panoptes launch dates
    are timestamp-without-timezone values interpreted as UTC.

    Parameters
    ----------
    through : datetime.date
        Last completed UTC launch day, inclusive.
    history_days : int, optional
        Zero for all-time history (default); otherwise a positive window length
        not exceeding :data:`zooniverse_dashboard.snapshots.MAX_DAYS`.

    Returns
    -------
    :class:`LaunchAggregates`
        Sparse daily counts and normalized, validated public names.

    Raises
    ------
    ValueError
        If the history limit is invalid, the database is not PostgreSQL,
        or a grouped row has invalid dates, counts, or public-name fields.
    sqlalchemy.exc.SQLAlchemyError
        If the read-only connection or query fails, including server timeouts.
    """
    if type(history_days) is not int or not 0 <= history_days <= MAX_DAYS:
        raise ValueError("Invalid history limit")
    start: date | None = through - timedelta(days=history_days - 1) if history_days else None
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
            bound: datetime = datetime.combine(through + timedelta(days=1), time.min)
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
            cleaned: tuple[str, ...] = tuple(
                cleaned_name for title in public_names if (cleaned_name := _clean_name(title))
            )
            counts[day] = count
            names[day] = _validate_names(cleaned, count=count)
        return LaunchAggregates(counts, names)
    finally:
        engine.dispose()


def launch_path(root: Path | None = None) -> Path:
    """Determine the location of the dedicated project-launch JSON file.

    Parameters
    ----------
    root : pathlib.Path or None, optional
        Explicit analytics data directory; defaults to
        :func:`zooniverse_dashboard.config.data_dir`.

    Returns
    -------
    pathlib.Path
        ``project_launches.json`` beneath the selected directory.
    """
    return (root if root is not None else data_dir()) / "project_launches.json"


def _parse(doc: object) -> LaunchSnapshot:
    """Validate a decoded current-format launch document into an immutable snapshot.

    Validate the exact envelope keys, reporting-window bounds, UTC modification
    timestamp, strictly ascending sparse positive launch counts, and public-name
    constraints. Every row contains a date, count, and public names list.
    An all-time snapshot must begin at the first known launch or,
    for an empty series, cover only the cutoff day.

    Parameters
    ----------
    doc : object
        Decoded JSON object requiring complete schema validation.

    Returns
    -------
    :class:`LaunchSnapshot`
        Parsed dates, counts and optional public titles from a valid document.

    Raises
    ------
    ValueError
        If the envelope, coverage, dates, counts, title collections or history
        metadata fail validation (including malformed ISO timestamps).
    TypeError
        If malformed JSON field types cannot be interpreted as date strings.
    """
    keys: set[str] = {
        "format",
        "first",
        "last",
        "as_of",
        "last_modified_utc",
        "history_days",
        "launches",
    }
    if not isinstance(doc, dict) or set(doc) != keys or doc["format"] != FORMAT:
        raise ValueError("Invalid project launch snapshot envelope")
    days: object = doc["history_days"]
    if type(days) is not int or not 0 <= days <= MAX_DAYS:
        raise ValueError("Invalid project launch history limit")
    first: date = date.fromisoformat(doc["first"])
    last: date = date.fromisoformat(doc["last"])
    as_of: date = date.fromisoformat(doc["as_of"])
    modified: datetime = datetime.fromisoformat(doc["last_modified_utc"])
    if (
        first > last
        or (last - first).days + 1 > MAX_DAYS
        or as_of != last + timedelta(days=1)
        or modified.tzinfo is None
        or modified.utcoffset() != timedelta(0)
        or (days > 0 and first != last - timedelta(days=days - 1))
    ):
        raise ValueError("Invalid project launch snapshot coverage")
    rows: object = doc["launches"]
    if not isinstance(rows, list) or len(rows) > MAX_DAYS:
        raise ValueError("Invalid project launch dates")
    pairs: list[tuple[date, int]] = []
    named: list[tuple[date, tuple[str, ...]]] = []
    prev: date | None = None
    for row in rows:
        if not isinstance(row, list) or len(row) != 3:
            raise ValueError("Invalid project launch bucket")
        day: date = date.fromisoformat(row[0])
        count: object = row[1]
        if (
            day < first
            or day > last
            or (prev is not None and day <= prev)
            or type(count) is not int
            or count < 1
        ):
            raise ValueError("Invalid project launch bucket")
        named.append((day, _validate_names(row[2], count=count)))
        pairs.append((day, count))
        prev = day
    if days == 0 and pairs and pairs[0][0] != first:
        raise ValueError("Inconsistent all-time launch coverage")
    if days == 0 and not pairs and first != last:
        raise ValueError("Inconsistent empty launch coverage")
    return LaunchSnapshot(first, last, as_of, modified, days, tuple(pairs), tuple(named))


def load_launches(*, root: Path | None = None) -> LaunchSnapshot:
    """Load a validated project-launch snapshot from local JSON storage.

    Refuse symlinks, missing files and files larger than :data:`MAX_BYTES`.
    The parser accepts only the current V2 count-and-public-name schema.
    A missing or invalid overlay can be treated as nonfatal by the chart caller;
    this function itself raises rather than returning an empty snapshot.

    Parameters
    ----------
    root : pathlib.Path or None, optional
        Directory containing ``project_launches.json``; defaults to the
        configured analytics data directory.

    Returns
    -------
    :class:`LaunchSnapshot`
        Fully validated, immutable sparse daily launch history.

    Raises
    ------
    ValueError
        If the snapshot is missing, a symlink, oversized, or contains invalid
        schema/coverage data.
    OSError
        If the local file cannot be read or inspected.
    json.JSONDecodeError
        If the file does not contain valid JSON syntax.
    TypeError
        If JSON field types cannot be parsed as dates or timestamps.
    """
    path: Path = launch_path(root)
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
    """Validate and atomically replace the project-launch overlay with V2 JSON.

    Assemble the requested sparse daily series, validate public names and the
    coverage contract, and reject an existing corrupted or symlinked target
    rather than overwriting it. :func:`zooniverse_dashboard.storage.atomic_publish_json`
    writes the replacement atomically. Publication never creates a user-level
    data export or modifies an existing activity snapshot.

    Parameters
    ----------
    counts : collections.abc.Mapping[datetime.date, int]
        Positive approved launch counts keyed by UTC day; omit zero-launch days.
    names : collections.abc.Mapping[datetime.date, collections.abc.Sequence[str]] or None, optional
        Normalized, public display names keyed only by dates present in
        ``counts``; omitted names are stored as an empty collection.
    through : datetime.date
        Most recent completed UTC day included in the snapshot.
    history_days : int, optional
        Zero for all history; otherwise the number of requested calendar days
        ending at ``through``.
    root : pathlib.Path or None, optional
        Destination data directory; defaults to runtime configuration.
    now : datetime.datetime or None, optional
        Collection timestamp; defaults to current UTC time. Must be timezone-
        aware and strictly after the reporting cutoff date when expressed in UTC.

    Returns
    -------
    :class:`LaunchSnapshot`
        Newly validated V2 snapshot represented in memory.

    Raises
    ------
    ValueError
        If cutoff, history span, counts, names, or the pre-existing target file
        are invalid, or the serialized document exceeds :data:`MAX_BYTES`.
    TypeError
        If invalid input types cannot be serialized or parsed.
    OSError
        If creating or atomically replacing the file fails.

    See Also
    --------
    :func:`load_launches`
        Read the new snapshot and independently validate it before use.
    """
    stamp: datetime = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None or through >= stamp.astimezone(timezone.utc).date():
        raise ValueError("Reporting cutoff must be a completed UTC day")
    if type(history_days) is not int or not 0 <= history_days <= MAX_DAYS:
        raise ValueError("Invalid history limit")
    first: date = (
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
    named: Mapping[date, Sequence[str]] = names if names is not None else {}
    if not isinstance(named, Mapping) or any(day not in counts for day in named):
        raise ValueError("Project names must refer to an approved launch date")
    rows: list[list[object]] = [
        [day.isoformat(), counts[day], list(_validate_names(named.get(day, ()), count=counts[day]))]
        for day in sorted(counts)
    ]
    path: Path = launch_path(root)
    if path.exists() or path.is_symlink():
        load_launches(root=root)  # Refuse to replace corrupt or unsafe files.
    doc: dict[str, object] = {
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
    atomic_publish_json(path, doc)
    return _parse(doc)
