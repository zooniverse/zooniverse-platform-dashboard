"""Coordinate manual refreshes and publish privacy-safe per-source status.

The coordinator refreshes the three activity sources and the independent
project-launch series using complete history windows. It serializes operations
with a POSIX advisory lock on the shared data volume, enforces a 60-minute
cooldown, and records per-stream progress in ``refresh_status.json``. Each
validated source publishes its own snapshot atomically; failure of one stream
does not discard a valid snapshot from another stream or a previous run.

The refresh cutoff is the **previous completed UTC day**. This module performs
no scheduling and does not spawn a worker: the browser-facing admission and
detached-process lifecycle live in :mod:`zooniverse_dashboard.refresh_job`.
Progress and diagnostics contain aggregate metadata only; exception messages,
credentials, SQL parameters, and remote payloads must not enter logs.

These locks assume one host or multiple workers sharing one POSIX filesystem;
they are **not** distributed locks across independently mounted replicas.

See Also
--------
:func:`refresh_all`
    Serialize and execute a complete manual collection attempt.
:func:`zooniverse_dashboard.pipeline.refresh_and_rebuild`
    Combine collection with the offline anomaly-catalog rebuild.
:func:`zooniverse_dashboard.refresh_job.start_job`
    Admit a detached browser-triggered refresh.
"""

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
from typing import Any, Callable, Final, Iterator

from zooniverse_dashboard.collection_config import effective_history_days
from zooniverse_dashboard.config import data_dir
from zooniverse_dashboard.eras import (
    fetch_full_classifications,
    fetch_full_comments,
    fetch_window_classifications,
    fetch_window_comments,
)
from zooniverse_dashboard.logging_utils import safe_error_trace
from zooniverse_dashboard.project_launches import (
    LaunchAggregates,
    LaunchSnapshot,
    fetch_project_launches,
    publish_launches,
)
from zooniverse_dashboard.snapshots import (
    MAX_DAYS,
    SOURCES,
    Snapshot,
    publish_snapshot,
)
from zooniverse_dashboard.sources import fetch_panoptes
from zooniverse_dashboard.storage import atomic_publish_json

STATUS_FORMAT: Final[str] = "ZOONIVERSE_REFRESH_STATUS_V1"
COOLDOWN_MINUTES: Final[int] = 60
LOG: logging.Logger = logging.getLogger("zooniverse_dashboard.refresh")
LAUNCH_STREAM: Final[str] = "project_launches"
REFRESH_STREAMS: Final[tuple[str, ...]] = (*SOURCES, LAUNCH_STREAM)


class RefreshRejected(ValueError):
    """Signal an expected refresh admission or validation refusal.

    The coordinator raises this :class:`ValueError` subclass for predictable
    operational cases such as overlapping work, a recent refresh, or an unsafe
    lock file. Its fixed, operator-facing messages may be displayed in the UI;
    this is **not** a wrapper for raw database or HTTP exception messages.

    See Also
    --------
    :func:`refresh_lock`
        Reject a concurrent refresh on the shared volume.
    :func:`refresh_all`
        Enforce the cooldown before collecting any source.
    """


@contextmanager
def refresh_lock(root: Path | None = None) -> Iterator[None]:
    """Hold the shared nonblocking POSIX lock during a refresh.

    Create ``.refresh.lock`` inside the selected data directory if needed, then
    hold an exclusive advisory ``flock`` until the context exits. The lock file
    must be a regular file. The descriptor is closed even when collection fails.
    All cooperating workers must mount the **same** filesystem for this guard to
    prevent concurrent operations; this is not a network-wide mutex.

    Parameters
    ----------
    root : pathlib.Path or None, optional
        Shared data directory. If omitted, use
        :func:`zooniverse_dashboard.config.data_dir`.

    Yields
    ------
    None
        Control to the caller while the refresh lock is held.

    Raises
    ------
    RefreshRejected
        If the lock file is unsafe or another refresh owns the lock.
    OSError
        If directory creation or file/lock operations fail.

    See Also
    --------
    :func:`zooniverse_dashboard.collection_config.set_history_days`
        Persist a setting while callers hold this lock.
    """
    folder: Path = root if root is not None else data_dir()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd: int = os.open(
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


def load_status(root: Path | None = None) -> dict[str, Any] | None:
    """Load and validate the durable collection-status document.

    Read ``refresh_status.json`` without changing it. Both the legacy
    three-activity-source format and the current format including
    ``project_launches`` are accepted. Expected states are ``running``, ``ok``,
    and ``partial_failure``; each included stream is ``pending``, ``ok`` or
    ``failed``. Present timestamps must carry an explicit UTC offset.

    Parameters
    ----------
    root : pathlib.Path or None, optional
        Directory containing the status JSON, defaulting to
        :func:`zooniverse_dashboard.config.data_dir`.

    Returns
    -------
    dict[str, typing.Any] or None
        Validated status mapping with ``format``, ``started_at_utc``,
        ``finished_at_utc``, ``state``, and ``streams`` keys; ``None`` if absent.

    Raises
    ------
    ValueError
        If the file is unsafe, too large, malformed, or fails schema checks.
    OSError
        If the status file cannot be inspected or read.

    See Also
    --------
    :func:`refresh_all`
        Publishes this status during each collection attempt.
    """
    path: Path = (root if root is not None else data_dir()) / "refresh_status.json"
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
        raise ValueError("Invalid refresh status")
    doc: Any = json.loads(path.read_text(encoding="utf-8"))
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
            stamp: datetime = datetime.fromisoformat(doc[key])
            if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
                raise ValueError("Invalid status time")
    return doc


def _write_status(root: Path, status: dict[str, Any]) -> None:
    """Atomically replace a validated refresh-status file.

    Revalidate an existing document before overwriting it. Publication uses
    ``zooniverse_dashboard.storage.atomic_publish_json``, so readers cannot
    observe a partially written JSON document. The caller constructs the status
    mapping and is responsible for its state transitions.

    Parameters
    ----------
    root : pathlib.Path
        Data directory containing ``refresh_status.json``.
    status : dict[str, typing.Any]
        Status document to publish.

    Returns
    -------
    None
        The document is written to the data volume.

    Raises
    ------
    ValueError
        If a prior status document is malformed or its destination is unsafe.
    OSError
        If validation or atomic publication cannot access the filesystem.
    """
    path: Path = root / "refresh_status.json"
    if path.exists():
        load_status(root)
    atomic_publish_json(path, status)


def _fetch_activity_counts(source: str, *, through: date, days: int) -> dict[date, int]:
    """Fetch and validate one activity stream without publishing its snapshot.

    The Panoptes adapter provides date-indexed registration aggregates, while
    the ERAS adapters provide validated response objects. ERAS full-history
    and bounded-history responses have different boundary requirements; both
    must reconcile their reported and observed totals before daily conversion.

    Fetcher selection happens for each call, so tests and operational overrides
    that replace a module-level fetch function still take effect.

    Parameters
    ----------
    source : str
        Supported activity source in :data:`zooniverse_dashboard.snapshots.SOURCES`.
    through : datetime.date
        Inclusive latest completed UTC day.
    days : int
        Requested completed-day limit; zero requests all history.

    Returns
    -------
    dict[datetime.date, int]
        Daily aggregate counts ready for snapshot-level validation.

    Raises
    ------
    ValueError
        If the source is unsupported or ERAS returns mismatched boundaries or
        an inconsistent aggregate total.
    """
    if source not in SOURCES:
        raise ValueError("Invalid analytics stream")
    if source == "users":
        return fetch_panoptes(through=through, history_days=days)

    full_fetchers = {
        "classifications": fetch_full_classifications,
        "talks": fetch_full_comments,
    }
    window_fetchers = {
        "classifications": fetch_window_classifications,
        "talks": fetch_window_comments,
    }
    if days == 0:
        result = full_fetchers[source](through)
        if result.end != through or result.reported_total != result.observed_total:
            raise ValueError("ERAS full-history aggregate failed validation")
    else:
        start: date = through - timedelta(days=days - 1)
        result = window_fetchers[source](start, through)
        if (
            result.start != start
            or result.end != through
            or result.reported_total != result.observed_total
        ):
            raise ValueError("ERAS date-range aggregate failed validation")
    return dict(result.calendar_series())


def refresh_source(
    source: str,
    *,
    through: date,
    root: Path | None = None,
    now: datetime | None = None,
    history_days: int | None = None,
) -> Snapshot:
    """Fetch, validate, and atomically replace one activity-source snapshot.

    For ``users``, query read-only Panoptes registration aggregates. For
    ``classifications`` or ``talks``, request ERAS aggregates and explicitly
    verify that the remote reported total matches the parsed calendar series.
    An all-history request uses the full-history ERAS endpoint; bounded requests
    must match both requested date boundaries. Only validated daily counts are
    passed to :func:`zooniverse_dashboard.snapshots.publish_snapshot`.

    Log only the source name, aggregate totals, timing, and safe error locations.
    An exception leaves the previous valid snapshot unchanged.

    Parameters
    ----------
    source : str
        Activity source in :data:`zooniverse_dashboard.snapshots.SOURCES`.
    through : datetime.date
        Inclusive last completed UTC reporting day.
    root : pathlib.Path or None, optional
        Snapshot directory, defaulting to the configured data volume.
    now : datetime.datetime or None, optional
        Timestamp forwarded to the snapshot publisher for deterministic metadata.
    history_days : int or None, optional
        Explicit number of completed days (zero means all available history).
        ``None`` reads :func:`zooniverse_dashboard.collection_config.effective_history_days`.

    Returns
    -------
    :class:`zooniverse_dashboard.snapshots.Snapshot`
        Validated, published continuous activity snapshot.

    Raises
    ------
    ValueError
        If the source, history limit, or upstream aggregate response is invalid.
    OSError
        If snapshot publication fails.
    sqlalchemy.exc.SQLAlchemyError
        If the read-only Panoptes database query fails.
    zooniverse_dashboard.eras.ErasResponseError
        If the ERAS response cannot be parsed or validated. Other HTTP or
        database errors propagate after sanitized logging.

    See Also
    --------
    :func:`refresh_launches`
        Independently collect sparse project-launch data.
    :func:`refresh_all`
        Serialize and track all source refreshes.
    """
    started: float = time.perf_counter()
    phase: str = "validate"
    folder: Path = root if root is not None else data_dir()
    try:
        if source not in SOURCES:
            raise ValueError("Invalid analytics stream")
        days: int = effective_history_days(folder) if history_days is None else history_days
        if type(days) is not int or not 0 <= days <= MAX_DAYS:
            raise ValueError("Invalid history limit")
        LOG.info(
            "event=source_fetch_started source=%s through=%s history_days=%d",
            source,
            through.isoformat(),
            days,
        )
        phase = "fetch_and_validate"
        counts: dict[date, int] = _fetch_activity_counts(source, through=through, days=days)
        LOG.info(
            "event=source_fetch_completed source=%s buckets=%d aggregate_total=%d "
            "elapsed_ms=%.1f",
            source,
            len(counts),
            sum(counts.values()),
            (time.perf_counter() - started) * 1000,
        )
        phase = "snapshot_publication"
        snapshot: Snapshot = publish_snapshot(
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
) -> LaunchSnapshot:
    """Fetch and publish approved project launches independently of activity.

    Collect nonzero daily launch counts and the permitted public project names
    from read-only Panoptes queries. Publish the sparse series using
    :func:`zooniverse_dashboard.project_launches.publish_launches`; a collection
    or validation error does not replace a previous valid launch snapshot.

    Parameters
    ----------
    through : datetime.date
        Inclusive completed UTC launch-date cutoff.
    root : pathlib.Path or None, optional
        Directory used for the launch JSON, defaulting to the configured volume.
    now : datetime.datetime or None, optional
        Publication timestamp; if omitted the publisher uses the current UTC time.
    history_days : int, optional
        Requested date window; zero (default) fetches all available launches.

    Returns
    -------
    :class:`zooniverse_dashboard.project_launches.LaunchSnapshot`
        Published snapshot of the sparse approved launch series.

    Raises
    ------
    ValueError
        If collected dates, names, counts, or the requested window are invalid.
    OSError
        If snapshot publication fails.
    sqlalchemy.exc.SQLAlchemyError
        If the read-only Panoptes launch query fails; the original exception
        propagates after a sanitized error log.

    See Also
    --------
    :func:`zooniverse_dashboard.project_launches.fetch_project_launches`
        Fetch grouped launch counts and public titles without private records.
    """
    started: float = time.perf_counter()
    phase: str = "fetch_and_validate"
    try:
        launch_data: LaunchAggregates = fetch_project_launches(
            through=through, history_days=history_days
        )
        LOG.info(
            "event=source_fetch_completed source=%s buckets=%d aggregate_total=%d elapsed_ms=%.1f",
            LAUNCH_STREAM,
            len(launch_data.counts),
            sum(launch_data.counts.values()),
            (time.perf_counter() - started) * 1000,
        )
        phase = "snapshot_publication"
        result: LaunchSnapshot = publish_launches(
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


def refresh_all(
    *,
    root: Path | None = None,
    now: datetime | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run one serialized manual refresh across all configured streams.

    Compute the cutoff as yesterday in UTC, acquire the shared refresh lock,
    verify the 60-minute cooldown against the previous start timestamp, then
    publish a ``running`` status. Refresh each of the three activity streams and
    ``project_launches`` in turn. A failing stream is marked ``failed`` while
    other streams continue. Write durable status after each stage and finish with
    ``ok`` or ``partial_failure``. No scheduler or anomaly rebuild is run here.

    The cooldown begins at the **attempt start**, including an attempt whose
    sources later fail. A failure before status initialization may abort the
    entire attempt; per-stream failures after initialization are isolated.

    Parameters
    ----------
    root : pathlib.Path or None, optional
        Shared persistent directory for the lock, settings, JSON snapshots, and
        refresh status.
    now : datetime.datetime or None, optional
        Timezone-aware test/operation clock, normalized to UTC. Defaults to the
        current UTC instant.
    progress : collections.abc.Callable[[str], None] or None, optional
        Callback receiving fixed, non-sensitive step descriptions.

    Returns
    -------
    dict[str, typing.Any]
        Final status document with ``state``, ``streams``, UTC timestamps, and a
        format identifier. Stream outcomes are ``ok`` or ``failed``.

    Raises
    ------
    RefreshRejected
        If another refresh owns the shared lock, the attempt falls within the
        60-minute cooldown, or ``now`` is timezone-naive.
    ValueError
        If stored configuration or existing status data is invalid.
    OSError
        If locking or durable status publication fails.

    See Also
    --------
    :func:`zooniverse_dashboard.pipeline.refresh_and_rebuild`
        Add the offline anomaly-analysis stage to this collection result.
    :func:`zooniverse_dashboard.refresh_job.run_job`
        Run the pipeline under a detached, durable job record.
    """
    started: float = time.perf_counter()
    run_id: str = uuid.uuid4().hex[:12]
    LOG.info("event=refresh_requested run=%s", run_id)
    try:
        folder: Path = root if root is not None else data_dir()
        stamp: datetime = now or datetime.now(timezone.utc)
        if stamp.tzinfo is None:
            raise RefreshRejected("Refresh clock must be timezone-aware")
        stamp = stamp.astimezone(timezone.utc)
        through: date = stamp.date() - timedelta(days=1)
        with refresh_lock(folder):
            prior: dict[str, Any] | None = load_status(folder)
            if prior is not None:
                previous_time: datetime = datetime.fromisoformat(prior["started_at_utc"])
                if stamp < previous_time + timedelta(minutes=COOLDOWN_MINUTES):
                    raise RefreshRejected("Refresh was attempted within the last hour")
            days: int = effective_history_days(folder)
            LOG.info(
                "event=refresh_started run=%s cutoff=%s history_days=%d streams=%d",
                run_id,
                through.isoformat(),
                days,
                len(REFRESH_STREAMS),
            )
            status: dict[str, Any] = {
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
                source_started: float = time.perf_counter()
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
