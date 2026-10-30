"""Orchestrate collection and offline anomaly analysis as one manual run.

This module connects the serialized four-stream collector in
:mod:`zooniverse_dashboard.refresh` with the snapshot-only anomaly-catalog
rebuild in :mod:`zooniverse_dashboard.anomalies.catalog`. The same function
serves the CLI and the detached browser worker. A failure to rebuild the
catalog never rolls back successfully published aggregate snapshots or
replaces a prior valid catalog.

The return value and progress messages are deliberately bounded to safe
operational state and per-source outcomes, not raw error details, credentials,
or individual records. No Dash request performs upstream queries through
this module.

See Also
--------
:func:`refresh_and_rebuild`
    Execute both stages and return a privacy-safe summary.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable

from zooniverse_dashboard.anomalies.catalog import rebuild
from zooniverse_dashboard.logging_utils import safe_error_trace
from zooniverse_dashboard.refresh import refresh_all
from zooniverse_dashboard.snapshots import SOURCES

LOG: logging.Logger = logging.getLogger("zooniverse_dashboard.refresh")


def refresh_and_rebuild(
    *, root: Path | None = None, progress: Callable[[str], None] | None = None
) -> dict[str, Any]:
    """Refresh aggregate snapshots, then rebuild the anomaly catalog.

    Run :func:`zooniverse_dashboard.refresh.refresh_all` first. Collection
    rejections propagate unchanged. Whether collection succeeds entirely or
    partially, attempt the offline
    :func:`zooniverse_dashboard.anomalies.catalog.rebuild` using whatever valid
    snapshots remain. Rebuild failure is converted to an overall partial failure;
    no valid activity snapshot or anomaly catalog is discarded.

    Parameters
    ----------
    root : pathlib.Path or None, optional
        Shared directory containing validated snapshots and anomaly metadata.
    progress : collections.abc.Callable[[str], None] or None, optional
        Receives predefined, aggregate-only progress steps. This callback is
        also used by the collector and anomaly rebuild.

    Returns
    -------
    dict[str, typing.Any]
        Public-safe mapping with ``state`` (``ok`` or ``partial_failure``),
        ``refresh_state``, ``anomalies_state``, and a ``sources`` mapping of
        collection outcomes. ``anomalies_state`` is ``failed`` after a rebuild
        exception, otherwise ``ok`` or ``partial_failure``.

    Raises
    ------
    zooniverse_dashboard.refresh.RefreshRejected
        If collection admission fails due to locking or the cooldown.
    ValueError
        If collection settings or stored metadata are invalid before source work.
    OSError
        If collection status cannot be published or the shared volume fails.

    See Also
    --------
    :func:`zooniverse_dashboard.refresh_job.run_job`
        Persist the same workflow in a detached browser-triggered worker.
    """
    started: float = time.perf_counter()

    def announce(message: str) -> None:
        """Log and forward one privacy-safe pipeline progress description.

        Parameters
        ----------
        message : str
            Fixed progress text, never a raw exception or remote API payload.

        Returns
        -------
        None
            Emits a log event and optionally calls the progress callback.
        """
        LOG.info("event=pipeline_progress detail=%s", message)
        if progress is not None:
            progress(message)

    announce("Starting data refresh")
    refresh: dict[str, Any] = refresh_all(root=root, progress=announce)
    announce("Aggregate collection finished; rebuilding anomalies")
    try:
        catalog: dict[str, Any] = rebuild(root=root, progress=announce)
    except Exception as exc:
        LOG.error(
            "event=anomaly_rebuild_failed error_type=%s trace=%s",
            type(exc).__name__,
            safe_error_trace(exc),
        )
        announce("Anomaly analysis failed; saved aggregate snapshots are still available")
        return {
            "state": "partial_failure",
            "refresh_state": refresh["state"],
            "anomalies_state": "failed",
            "sources": dict(refresh["streams"]),
        }

    analysis: dict[str, Any] = catalog["sources"]
    anomaly_ok: bool = all(analysis.get(source, {}).get("status") == "ok" for source in SOURCES)
    state: str = "ok" if refresh["state"] == "ok" and anomaly_ok else "partial_failure"
    announce(
        "Refresh and anomaly analysis complete"
        if state == "ok"
        else "Refresh and anomaly analysis finished with warnings"
    )
    LOG.info(
        "event=pipeline_finished state=%s refreshed=%d analyzed=%d elapsed_ms=%.1f",
        state,
        sum(v == "ok" for v in refresh["streams"].values()),
        sum(analysis.get(s, {}).get("status") == "ok" for s in SOURCES),
        (time.perf_counter() - started) * 1000,
    )
    return {
        "state": state,
        "refresh_state": refresh["state"],
        "anomalies_state": "ok" if anomaly_ok else "partial_failure",
        "sources": dict(refresh["streams"]),
    }
