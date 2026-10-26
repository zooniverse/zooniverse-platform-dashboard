"""One manual collection-to-analysis workflow for CLI and web background workers.

Only validated aggregate counts are ingested; analysis reads local snapshots.
The existing refresh lock, cooldown and atomic publication remain authoritative.
"""

from __future__ import annotations

import logging
import time

from zooniverse_dashboard.anomalies.catalog import rebuild
from zooniverse_dashboard.logging_utils import safe_error_trace
from zooniverse_dashboard.refresh import refresh_all
from zooniverse_dashboard.snapshots import SOURCES

LOG = logging.getLogger("zooniverse_dashboard.refresh")


def refresh_and_rebuild(*, root=None, progress=None) -> dict:
    """Refresh sources and rebuild the catalog, retaining good snapshots on failures.

    Expected refresh rejections propagate unchanged. An analysis failure does
    not discard successfully refreshed snapshots or overwrite a valid catalog.
    Returns only a small public-safe summary for the web or CLI.
    """
    started = time.perf_counter()

    def announce(message: str) -> None:
        LOG.info("event=pipeline_progress detail=%s", message)
        if progress is not None:
            progress(message)

    announce("Starting data refresh")
    refresh = refresh_all(root=root, progress=announce)
    announce("Aggregate collection finished; rebuilding anomalies")
    try:
        catalog = rebuild(root=root, progress=announce)
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

    analysis = catalog["sources"]
    anomaly_ok = all(analysis.get(source, {}).get("status") == "ok" for source in SOURCES)
    state = "ok" if refresh["state"] == "ok" and anomaly_ok else "partial_failure"
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
