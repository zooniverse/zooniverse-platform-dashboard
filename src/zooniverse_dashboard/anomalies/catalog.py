"""Read, validate and atomically publish the offline anomaly catalog.

This module is the lightweight bridge between the offline NB2 analysis and
Dash history/anomaly pages. Reading the ``anomalies.json`` file requires no
NumPy, SciPy or statsmodels imports: numerical code is imported lazily only
by :func:`zooniverse_dashboard.anomalies.catalog.build_catalog` during explicit offline rebuilding.

Catalog visibility is tied to each source snapshot's last-modified revision.
If a source snapshot is unavailable, stale, or has no successful analysis, its
episodes are hidden without hiding valid episodes from other sources. JSON
content is size-limited, files are rejected if they are symlinks, and writes
use a private temporary file followed by atomic replacement.

Events represent aggregate statistical inspection hints, not confirmed
incidents. Neither chart navigation nor catalog reads fetch upstream data.

See Also
--------
:func:`zooniverse_dashboard.anomalies.catalog.build_catalog`
    Assemble the catalog from saved aggregate snapshots.
:func:`zooniverse_dashboard.anomalies.catalog.rebuild`
    Compute and atomically publish a replacement catalog.
:func:`zooniverse_dashboard.anomalies.compute.score_days`
    Offline negative-binomial scoring.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from zooniverse_dashboard.config import data_dir
from zooniverse_dashboard.snapshots import SOURCES, load_snapshot

CATALOG_FORMAT: str = "ZOONIVERSE_ANOMALIES_V1"
ID_PATTERN: re.Pattern[str] = re.compile(
    r"^(users|classifications|talks)-\d{4}-\d{2}-\d{2}-(spike|drop)$"
)
MAX_CATALOG_BYTES: int = 500_000


def catalog_path(root: Path | None = None) -> Path:
    """Return the location of the anomaly catalog JSON file.

    Parameters
    ----------
    root : pathlib.Path or None, optional
        Override the aggregate storage directory; otherwise use
        :func:`zooniverse_dashboard.config.data_dir`.

    Returns
    -------
    pathlib.Path
        Full path to ``anomalies.json``. This function does not access the file.
    """
    return (root if root is not None else data_dir()) / "anomalies.json"


def read_catalog(*, root: Path | None = None) -> dict[str, Any] | None:
    """Read the on-disk catalog after basic format and size checks.

    Reject missing files, symlinks, files larger than ``MAX_CATALOG_BYTES``,
    malformed JSON, and documents without the expected top-level ``format``,
    ``sources`` and ``events`` types. This is a deliberately *shallow* structural
    check, not complete per-event schema validation. Only the source-revision
    check in :func:`zooniverse_dashboard.anomalies.catalog.visible_events` determines which episodes may be shown.

    Parameters
    ----------
    root : pathlib.Path or None, optional
        Directory containing ``anomalies.json``; defaults to the configured
        aggregate data directory.

    Returns
    -------
    dict[str, typing.Any] or None
        Parsed top-level catalog dictionary, or ``None`` for missing, oversize,
        unsupported or unreadable catalog data.

    See Also
    --------
    :func:`zooniverse_dashboard.anomalies.catalog.visible_events`
        Filter valid-looking episodes by current snapshot revision and source.
    """
    path: Path = catalog_path(root)
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_CATALOG_BYTES:
        return None
    try:
        result: Any = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(result, dict)
            or result.get("format") != CATALOG_FORMAT
            or not isinstance(result.get("sources"), dict)
            or not isinstance(result.get("events"), list)
        ):
            return None
        return result
    except (ValueError, OSError, TypeError):
        return None


def visible_events(*, root: Path | None = None) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Return episodes whose source analysis matches its current snapshot.

    Load each activity snapshot via
    :func:`zooniverse_dashboard.snapshots.load_snapshot` and compare its
    ``last_modified`` timestamp with the catalog's ``snapshot_revision``. Only
    sources marked ``ok`` with matching revisions are considered ready. Filter
    their events by supported source key and an ID matching ``ID_PATTERN``;
    results are newest-detected first. This prevents a refresh from exposing
    stale analysis for a source whose underlying counts have changed.

    Parameters
    ----------
    root : pathlib.Path or None, optional
        Shared directory holding catalog and source snapshots; defaults to the
        configured aggregate directory.

    Returns
    -------
    tuple[list[dict[str, typing.Any]], dict[str, str]]
        Visible episode dictionaries and a status string for each source (for
        example ``ready``, ``snapshot unavailable``, or ``analysis stale``).
        Unavailable sources do not prevent results from other sources appearing.

    See Also
    --------
    :func:`zooniverse_dashboard.anomalies.catalog.read_catalog`
        Read and check the on-disk catalog format.
    :func:`zooniverse_dashboard.snapshots.load_snapshot`
        Validate current aggregate data and expose the source revision.
    """
    doc: dict[str, Any] | None = read_catalog(root=root)
    statuses: dict[str, str] = {}
    for source in SOURCES:
        try:
            stamp: str = load_snapshot(source, root=root).last_modified.isoformat()
        except (OSError, ValueError, TypeError, KeyError):
            statuses[source] = "snapshot unavailable"
            continue
        metadata: dict[str, Any] | None = doc.get("sources", {}).get(source) if doc else None
        if metadata is None:
            statuses[source] = "analysis not generated"
        elif metadata.get("snapshot_revision") != stamp:
            statuses[source] = "analysis stale — rebuild after refreshing data"
        elif metadata.get("status") != "ok":
            statuses[source] = metadata.get("message", "analysis unavailable")
        else:
            statuses[source] = "ready"
    safe: list[dict[str, Any]] = []
    for event in doc.get("events", []) if doc else []:
        if (
            isinstance(event, dict)
            and event.get("source") in SOURCES
            and statuses[event["source"]] == "ready"
            and isinstance(event.get("id"), str)
            and ID_PATTERN.fullmatch(event["id"])
        ):
            safe.append(event)
    return sorted(safe, key=lambda item: (item["detected"], item["id"]), reverse=True), statuses


def event_by_id(event_id: str, *, source: str | None = None) -> dict[str, Any] | None:
    """Find a currently visible episode by its stable formatted ID.

    Only IDs matching ``<source>-YYYY-MM-DD-<direction>`` for a supported source
    and ``spike`` or ``drop`` direction are considered. Lookup uses the current,
    revision-filtered catalog, not any stale or unavailable source data.

    Parameters
    ----------
    event_id : str
        Candidate episode identifier to match against ``ID_PATTERN``.
    source : str or None, optional
        If provided, restrict the match to this activity source.

    Returns
    -------
    dict[str, typing.Any] or None
        Matching visible episode, or ``None`` if the ID is invalid, absent,
        stale, or excluded by the source filter.

    See Also
    --------
    :func:`zooniverse_dashboard.anomalies.catalog.visible_events`
        Determine currently safe-to-display episode records.
    """
    if not ID_PATTERN.fullmatch(event_id):
        return None
    events, _ = visible_events()
    return next(
        (
            event
            for event in events
            if event["id"] == event_id and (source is None or event["source"] == source)
        ),
        None,
    )


def write_catalog(doc: dict[str, Any], *, root: Path | None = None) -> None:
    """Atomically replace the saved anomaly catalog after safety checks.

    Serialize the supplied dictionary to compact UTF-8 JSON, reject unsafe
    destinations and documents above ``MAX_CATALOG_BYTES``, and publish using a
    0600-permission temporary file in the destination directory. Flush and fsync
    the temporary file before replacing the old catalog; remove the temporary
    file on completion or failure. A valid previous catalog survives errors that
    occur before the final atomic replacement.

    Parameters
    ----------
    doc : dict[str, typing.Any]
        Fully assembled catalog to persist. The writer checks size and path
        safety; it does not validate every document field.
    root : pathlib.Path or None, optional
        Aggregate data directory; default from
        :func:`zooniverse_dashboard.config.data_dir`.

    Returns
    -------
    None
        Writes the catalog as a side effect.

    Raises
    ------
    ValueError
        If the destination is unsafe or serialized data exceed the size limit.
    OSError
        If directory creation, disk I/O, fsync, or atomic replacement fails.
    TypeError
        If ``doc`` cannot be serialized to JSON.

    See Also
    --------
    :func:`zooniverse_dashboard.anomalies.catalog.rebuild`
        Assemble and publish the file in one explicit offline operation.
    """
    destination: Path = catalog_path(root)
    if destination.is_symlink() or destination.suffix != ".json":
        raise ValueError("Unsafe anomaly catalog destination")
    content: str = json.dumps(doc, separators=(",", ":"))
    if len(content.encode("utf-8")) > MAX_CATALOG_BYTES:
        raise ValueError("Anomaly catalog exceeds size limit")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix=".anomaly-", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if destination.is_symlink():
            raise ValueError("Anomaly catalog destination changed")
        os.replace(temp, destination)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def build_catalog(
    *, root: Path | None = None, progress: Callable[[str], None] | None = None
) -> dict[str, Any]:
    """Compute a source-aware anomaly catalog from saved snapshots only.

    For each source in :data:`zooniverse_dashboard.snapshots.SOURCES`, load its
    validated daily aggregates, run :func:`zooniverse_dashboard.anomalies.compute.score_days`
    followed by :func:`zooniverse_dashboard.anomalies.compute.episodes`, and record
    source status, snapshot revision, evaluated coverage, model name and episode
    count. Snapshot failures, insufficient history and expected model failures
    are recorded *per source*, so other sources remain independently usable.

    After collecting events, append contextual cross-source drop notes. This
    function builds a dictionary but does not write it; :func:`zooniverse_dashboard.anomalies.catalog.rebuild` performs
    the atomic publication. The progress callback gets only fixed, aggregate-only
    messages. The numerical dependencies are imported only inside this function,
    so normal dashboard requests do not require them.

    Parameters
    ----------
    root : pathlib.Path or None, optional
        Directory containing validated daily snapshots; defaults to configured
        aggregate storage.
    progress : collections.abc.Callable[[str], None] or None, optional
        Optional notification callback invoked with privacy-safe per-source status
        messages; no raw upstream records or credentials are sent.

    Returns
    -------
    dict[str, typing.Any]
        Catalog document with ``format``, UTC generation timestamp, per-source
        ``sources`` metadata, and ``events`` sorted newest detected first.

    Raises
    ------
    Exception
        Unexpected failures outside the per-source snapshot/model exception
        handling, including an exception raised by ``progress``, propagate.

    See Also
    --------
    :func:`zooniverse_dashboard.anomalies.catalog.rebuild`
        Persist the resulting catalog atomically.
    """
    # Delayed import: web workers need neither statsmodels nor a numerical fit.
    from zooniverse_dashboard.anomalies.compute import add_shared_drop_context, episodes, score_days

    results: list[dict[str, Any]] = []
    metadata: dict[str, dict[str, Any]] = {}
    for source in SOURCES:
        if progress is not None:
            progress(f"Analyzing {source} from saved daily aggregates...")
        try:
            snapshot = load_snapshot(source, root=root)
        except (OSError, ValueError, TypeError, KeyError):
            metadata[source] = {"status": "missing", "message": "snapshot unavailable"}
            continue
        stamp: str = snapshot.last_modified.isoformat()
        try:
            scored: list[dict[str, Any]] = score_days(snapshot.daily, source=source)
            events: list[dict[str, Any]] = episodes(scored, source=source)
            metadata[source] = {
                "status": "ok" if scored else "insufficient_history",
                "message": "" if scored else "Need at least 911 daily buckets for NB2 calibration",
                "snapshot_revision": stamp,
                "evaluated_first": scored[0]["date"] if scored else None,
                "evaluated_last": scored[-1]["date"] if scored else None,
                "model": "Calendar-aware NB2" if source == "users" else "NB2",
                "episode_count": len(events),
            }
            results.extend(events)
        except (RuntimeError, ValueError, FloatingPointError) as exc:
            metadata[source] = {
                "status": "error",
                "snapshot_revision": stamp,
                "message": f"NB2 analysis failed ({type(exc).__name__}); aggregate snapshots unchanged",
            }
        if progress is not None:
            progress(
                f"{source}: {metadata[source]['status']} "
                f"{metadata[source].get('episode_count', 0)} episodes"
            )
    add_shared_drop_context(results)
    return {
        "format": CATALOG_FORMAT,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources": metadata,
        "events": sorted(results, key=lambda item: (item["detected"], item["id"]), reverse=True),
    }


def rebuild(
    *, root: Path | None = None, progress: Callable[[str], None] | None = None
) -> dict[str, Any]:
    """Rebuild and persist the offline anomaly catalog in one operation.

    This is the explicit offline entry point, also called by
    :func:`zooniverse_dashboard.pipeline.refresh_and_rebuild`. A new catalog is
    published only after :func:`zooniverse_dashboard.anomalies.catalog.build_catalog` succeeds and
    :func:`zooniverse_dashboard.anomalies.catalog.write_catalog` completes; a failed write leaves the preceding
    valid catalog in place.

    Parameters
    ----------
    root : pathlib.Path or None, optional
        Shared directory for validated snapshots and anomaly catalog.
    progress : collections.abc.Callable[[str], None] or None, optional
        Optional callback receiving predefined aggregate-only progress messages.

    Returns
    -------
    dict[str, typing.Any]
        Newly built and successfully written catalog document.

    Raises
    ------
    ValueError
        If publication violates the catalog destination or size restrictions.
    OSError
        If publication fails due to filesystem errors.
    TypeError
        If catalog serialization fails.

    See Also
    --------
    :func:`zooniverse_dashboard.anomalies.catalog.build_catalog`
        Produce the catalog without writing it.
    :func:`zooniverse_dashboard.anomalies.catalog.write_catalog`
        Publish a finished catalog.
    """
    doc: dict[str, Any] = build_catalog(root=root, progress=progress)
    write_catalog(doc, root=root)
    return doc
