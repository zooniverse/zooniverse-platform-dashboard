"""Expose the installed, operator-facing analytics command-line commands.

The :command:`refresh-data` entry point invokes the same safeguarded
collection-and-analysis pipeline as the dashboard's Refresh data button. Its
status messages contain no raw upstream responses, individual records, or
connection credentials. A rejected run or a partially successful run exits
nonzero so that a shell or scheduler cannot mistake it for complete success.

The :command:`set-date-limit` entry point reads or atomically persists the
number of *completed UTC calendar days* collected on the next refresh. It does
not query Panoptes or ERAS, and updating the setting does not rewrite existing
snapshots. Both commands are registered under ``[project.scripts]`` in
``pyproject.toml`` and operate on the same configured persistent data directory
as the running dashboard.

See Also
--------
:func:`zooniverse_dashboard.pipeline.refresh_and_rebuild`
    Shared aggregate collection and anomaly-analysis pipeline.
:func:`zooniverse_dashboard.collection_config.effective_history_days`
    Resolve the persistent override or environment default.
:func:`zooniverse_dashboard.collection_config.set_history_days`
    Validate and publish the persistent override.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any

from zooniverse_dashboard.collection_config import effective_history_days, set_history_days
from zooniverse_dashboard.logging_utils import configure_refresh_logging
from zooniverse_dashboard.pipeline import refresh_and_rebuild
from zooniverse_dashboard.refresh import RefreshRejected, refresh_lock


def refresh_data() -> None:
    """Run a guarded full-window refresh and offline anomaly rebuild.

    Parse an argument-free command line, configure privacy-safe progress
    logging, and invoke :func:`zooniverse_dashboard.pipeline.refresh_and_rebuild`.
    Collection and catalog reconstruction share the lock/cooldown policies of
    the web application. Each successfully collected snapshot remains published
    even if another source or the later anomaly rebuild fails.

    Returns
    -------
    None
        Successful runs exit normally after a final ``ok`` log entry.

    Raises
    ------
    SystemExit
        Exit status 2 indicates a rejected refresh (for example a held lock
        or cooldown); status 1 indicates a failed or partially successful run.
        Argument parsing may also raise ``SystemExit`` for invalid options.

    See Also
    --------
    :func:`zooniverse_dashboard.refresh.refresh_all`
        Four-stream aggregate collection and independent snapshot publication.
    :func:`zooniverse_dashboard.logging_utils.configure_refresh_logging`
        Configure sanitized refresh diagnostics.
    """
    argparse.ArgumentParser(description="Refresh all Zooniverse aggregate snapshots").parse_args()
    logger: logging.Logger = configure_refresh_logging()
    try:
        result: dict[str, Any] = refresh_and_rebuild()
    except RefreshRejected:
        logger.error("event=refresh_cli_exit result=rejected")
        raise SystemExit(2) from None
    except Exception:
        # refresh_all already emitted a sanitized diagnostic with code locations.
        logger.error("event=refresh_cli_exit result=aborted")
        raise SystemExit(1) from None
    logger.info("event=refresh_cli_exit result=%s", result["state"])
    if result["state"] != "ok":
        raise SystemExit(1)


def set_date_limit() -> None:
    """Inspect or persist the history limit used by subsequent refreshes.

    With no positional argument, print the effective count of completed UTC
    calendar days. With ``DAYS``, acquire the shared refresh lock and atomically
    save an override to ``collection_config.json`` inside the configured data
    directory. Zero means all available history; a positive value selects the
    most recent ``DAYS`` completed dates. The override takes precedence over
    ``ANALYTICS_HISTORY_DAYS`` and survives container restarts when the data
    volume is retained. No upstream data are fetched by this command.

    Returns
    -------
    None
        Prints either the current setting or a reminder to run
        :command:`refresh-data` after changing it.

    Raises
    ------
    ValueError
        If the requested count violates the history-limit constraints, or the
        existing saved/environment configuration is invalid.
    zooniverse_dashboard.refresh.RefreshRejected
        If a refresh or another settings update already holds the lock.
    OSError
        If the settings file cannot be read or atomically written.
    SystemExit
        If the command-line arguments cannot be parsed.

    See Also
    --------
    :func:`zooniverse_dashboard.collection_config.effective_history_days`
        Determine the current effective setting.
    :func:`zooniverse_dashboard.collection_config.set_history_days`
        Persist and validate a new limit.
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Set historical collection window in completed UTC days (0 = all)"
    )
    parser.add_argument(
        "days", type=int, nargs="?", help="0 for all history, or a positive day count"
    )
    args: argparse.Namespace = parser.parse_args()
    if args.days is None:
        value: int = effective_history_days()
        print(f"Current history limit: {value} ({'all available' if value == 0 else 'days'})")
        return
    with refresh_lock():
        set_history_days(args.days)
    print(f"History limit set to {args.days}; run refresh-data to update snapshots")
