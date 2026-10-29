"""Create the Dash application and coordinate browser-visible refresh status.

The application serves the snapshot-backed Users, Classifications, Talk,
Projects, and Anomalies pages through Dash Pages. A manually requested refresh
is admitted to a detached worker; neither the initial page load nor a chart
callback connects directly to Panoptes or ERAS. Global callbacks display the
persisted job state, check snapshot freshness, and reload the browser once a
successful run has completed.

The Flask application also exposes ``/healthz`` and redirects ``/`` to the
Users page. Importing this module creates the Dash application and its Flask
``server`` object for Gunicorn; this is distinct from launching the development
server via the repository's top-level ``app.py``.

See Also
--------
:func:`zooniverse_dashboard.refresh_job.start_job`
    Admission to the detached refresh worker.
:func:`zooniverse_dashboard.refresh_job.load_job`
    Read persisted worker progress without accessing upstream systems.
:func:`zooniverse_dashboard.ui.register_history_callback`
    Register snapshot-backed activity chart callbacks.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dash import (
    Dash,
    Input,
    Output,
    clientside_callback,
    dcc,
    html,
    page_container,
    page_registry,
)
from flask import Response, redirect, request

from zooniverse_dashboard.anomalies.catalog import visible_events
from zooniverse_dashboard.collection_config import effective_history_days
from zooniverse_dashboard.logging_utils import configure_refresh_logging
from zooniverse_dashboard.refresh import RefreshRejected, load_status
from zooniverse_dashboard.refresh_job import load_job, start_job
from zooniverse_dashboard.snapshots import SOURCES, load_snapshot

PACKAGE_DIR: Path = Path(__file__).resolve().parent
LOG: logging.Logger = logging.getLogger("zooniverse_dashboard.refresh")


def create_app() -> Dash:
    """Create the fully configured Dash application and Flask routes.

    Register the global navigation, the manual-refresh interface, status polling,
    and the client-side completion reload. The Dash Pages system registers page
    layouts and metric-specific callbacks independently. Creating this object
    configures logging but does not itself collect new upstream data.

    Returns
    -------
    dash.Dash
        App instance with the Flask ``server`` registered for WSGI serving.

    See Also
    --------
    :func:`zooniverse_dashboard.refresh_job.start_job`
        Start the detached data collection workflow.
    :func:`zooniverse_dashboard.refresh.load_status`
        Read the last coordinator status from local storage.
    """
    configure_refresh_logging()
    app = Dash(
        __name__,
        use_pages=True,
        pages_folder=str(PACKAGE_DIR / "pages"),
        assets_folder=str(PACKAGE_DIR / "assets"),
        title="Zooniverse | Platform Dashboard",
        suppress_callback_exceptions=True,
    )
    app.layout = html.Div(
        [
            html.Header(
                [
                    html.Div(
                        [
                            html.Div("ZOONIVERSE", className="brand-eyebrow"),
                            html.Div("Platform Dashboard", className="brand-title"),
                        ],
                        className="brand",
                    ),
                    html.Nav(
                        [
                            dcc.Link(page["name"], href=page["relative_path"])
                            for page in page_registry.values()
                        ],
                        className="navigation",
                        **{"aria-label": "Main navigation"},
                    ),
                ],
                className="site-header",
            ),
            html.Div(
                [
                    html.Button("Refresh data", id="refresh-now", n_clicks=0),
                    html.Span(id="refresh-feedback", role="status"),
                    dcc.Store(id="refresh-run", storage_type="session"),
                    dcc.Store(id="refresh-completion"),
                    html.Div(id="refresh-reload-signal", style={"display": "none"}),
                ],
                className="manual-refresh-bar",
            ),
            html.Section(
                [
                    html.H2("Refresh progress"),
                    html.Div(
                        "Preparing refresh...",
                        id="refresh-steps",
                        role="status",
                        **{"aria-live": "polite"},
                    ),
                ],
                id="refresh-panel",
                className="refresh-progress-panel",
                style={"display": "none"},
            ),
            html.Div(
                id="refresh-success",
                className="refresh-success-banner",
                role="status",
                style={"display": "none"},
            ),
            # Fast polling is enabled only while a refresh job is active.
            # The slow status poll also detects jobs started by other sessions.
            dcc.Interval(id="refresh-progress-poll", interval=1_000, n_intervals=0, disabled=True),
            dcc.Interval(id="refresh-status-poll", interval=60_000, n_intervals=0),
            html.Div(id="refresh-system-status", className="refresh-system-status"),
            html.Main(page_container, className="page-container"),
            html.Footer(
                "Internal Zooniverse analytics · Aggregated counts only · UTC",
                className="site-footer",
            ),
        ],
        className="app-shell",
    )

    @app.callback(
        Output("refresh-run", "data"),
        Output("refresh-feedback", "children"),
        Input("refresh-now", "n_clicks"),
        prevent_initial_call=True,
    )
    def do_refresh(_clicks: int | None) -> tuple[str | None, str]:
        """Attempt to schedule a detached refresh in response to a button click.

        The browser callback returns immediately after worker admission, not after
        collection. Expected cooldown or concurrency rejection is returned as a
        human-readable message; unexpected errors are logged without raw details.

        Parameters
        ----------
        _clicks : int or None
            Dash click count used solely to trigger callback execution.

        Returns
        -------
        tuple[str or None, str]
            Scheduled run ID and success text, or ``None`` and a safe rejection/error
            message. This callback intentionally does not propagate worker errors.

        See Also
        --------
        :func:`zooniverse_dashboard.refresh_job.start_job`
            Enforce admission and return a run identifier.
        """
        try:
            run_id = start_job()
            LOG.info("event=web_refresh_scheduled run=%s", run_id[:12])
            return run_id, "Refresh started"
        except RefreshRejected as exc:
            return None, str(exc)
        except Exception:
            LOG.error("event=web_refresh_schedule_failed")
            return None, "Could not start refresh; contact the operator"

    @app.callback(
        Output("refresh-steps", "children"),
        Output("refresh-panel", "style"),
        Output("refresh-now", "disabled"),
        Output("refresh-completion", "data"),
        Output("refresh-success", "children"),
        Output("refresh-success", "style"),
        Output("refresh-progress-poll", "disabled"),
        Input("refresh-progress-poll", "n_intervals"),
        Input("refresh-run", "data"),
        Input("refresh-status-poll", "n_intervals"),
    )
    def job_progress(
        _tick: int | None, run_id: str | None, _status_tick: int | None
    ) -> tuple[Any, dict[str, str], bool, dict[str, Any] | None, str, dict[str, str], bool]:
        """Describe the persisted refresh job and set UI polling controls.

        Active jobs remain visible if another browser session started them. The fast
        one-second poll is enabled only while an active job exists. A completed
        successful run triggers the reloading signal only for its initiating browser
        session and only shortly after completion; interrupted, rejected and partial
        runs are represented without erasing existing valid snapshots.

        Parameters
        ----------
        _tick : int or None
            Fast poll counter, used only as a Dash callback trigger.
        run_id : str or None
            Identifier of the run initiated by this browser session, if any.
        _status_tick : int or None
            Slow poll counter, allowing detection of other sessions' jobs.

        Returns
        -------
        tuple
            Seven Dash output values, in order: progress children, panel style,
            refresh-button disabled flag, optional reload payload, success-banner
            text, banner style, and fast-poll disabled flag.

        See Also
        --------
        :func:`zooniverse_dashboard.refresh_job.load_job`
            Load worker state from durable local JSON.
        """
        hidden: dict[str, str] = {"display": "none"}
        try:
            job = load_job()
        except (OSError, ValueError, TypeError, KeyError):
            return "Refresh progress unavailable", hidden, False, None, "", hidden, True
        if job is None:
            return "No refresh job", hidden, False, None, "", hidden, True

        status = job["state"]
        same_run = run_id is not None and job["run_id"] == run_id
        active = status in ("queued", "running")

        if status == "ok":
            # A compact confirmation survives the automatic page reload (the
            # session Store retains the run ID), but doesn't linger all day.
            try:
                finished = datetime.fromisoformat(job["finished_at_utc"])
                age = datetime.now(timezone.utc) - finished
                recent = timedelta(0) <= age < timedelta(seconds=60)
            except (TypeError, ValueError):
                recent = False
            if same_run and recent:
                return (
                    "",
                    hidden,
                    False,
                    {"reload": True, "run_id": run_id},
                    "Data refreshed successfully — Users, Classifications, Talk, "
                    "project launches, and anomaly analysis are up to date.",
                    {"display": "block"},
                    True,
                )
            return "", hidden, False, None, "", hidden, True

        # Show running jobs even when a browser opens mid-refresh. A completed
        # unsuccessful run is only shown to the browser session that started it.
        if not (active or same_run):
            return "No active refresh", hidden, False, None, "", hidden, True
        steps = [html.Li(item) for item in job["steps"]]
        if status in ("failed", "interrupted", "rejected"):
            steps.append(html.Li(f"Refresh {status}; existing snapshots retained"))
        elif status == "partial_failure":
            steps.append(html.Li("Refresh finished with warnings; review stale sources"))
        return (
            html.Ol(steps, className="refresh-step-list"),
            {"display": "block"},
            active,
            None,
            "",
            hidden,
            not active,
        )

    # The session Store survives navigation and even an unexpected page reload.
    # A per-tab marker prevents an infinite loop after a successful reload.
    clientside_callback(
        """function(result) {
            if (result && result.reload && result.run_id) {
                var key = "zooniverse-refresh-reloaded";
                try {
                    if (window.sessionStorage.getItem(key) === result.run_id) {
                        return window.dash_clientside.no_update;
                    }
                    window.sessionStorage.setItem(key, result.run_id);
                } catch (_) {
                    // Reload even when session storage is unavailable.
                }
                window.location.reload();
            }
            return window.dash_clientside.no_update;
        }""",
        Output("refresh-reload-signal", "children"),
        Input("refresh-completion", "data"),
        prevent_initial_call=True,
    )

    @app.callback(
        Output("refresh-system-status", "children"),
        Input("refresh-status-poll", "n_intervals"),
        Input("refresh-feedback", "children"),
    )
    def status(_ticks: int | None, _message: str | None) -> str:
        """Build the concise global snapshot and anomaly-status summary.

        Read local source snapshots, last refresh status, worker state, configured
        history window, and visible anomaly analyses. Unavailable data are reported
        as text rather than causing the header to fail. This is a read-only status
        callback and does not trigger collection or rebuild analysis.

        Parameters
        ----------
        _ticks : int or None
            Slow polling interval counter used as a callback trigger.
        _message : str or None
            Refresh-feedback text, also used only to trigger a status update.

        Returns
        -------
        str
            Human-readable status line containing history coverage, snapshot
            timestamps, refresh state, and analysis readiness.

        See Also
        --------
        :func:`zooniverse_dashboard.anomalies.catalog.visible_events`
            Filter anomaly episodes against snapshot revisions.
        """
        parts: list[str] = []
        for source in SOURCES:
            try:
                snapshot = load_snapshot(source)
                parts.append(f"{source}: {snapshot.last_modified.strftime('%Y-%m-%d %H:%M UTC')}")
            except (OSError, ValueError, TypeError, KeyError):
                parts.append(f"{source}: not yet collected")
        try:
            info = load_status()
            state = "No previous refresh" if info is None else f"Last collection: {info['state']}"
            job = load_job()
            if job is not None:
                state += f" · Last pipeline: {job['state']}"
        except (OSError, ValueError):
            state = "Refresh status unavailable"
        try:
            days = effective_history_days()
            window = "All available history" if days == 0 else f"Last {days} UTC days"
        except ValueError:
            window = "Invalid history configuration"
        try:
            _events, anomaly_states = visible_events()
            ready_count = sum(value == "ready" for value in anomaly_states.values())
            anomaly_status = f"Anomaly analyses ready: {ready_count}/{len(SOURCES)}"
        except (OSError, ValueError, TypeError, KeyError):
            anomaly_status = "Anomaly analysis status unavailable"
        return (
            "Selected history: "
            + window
            + " | Snapshot last modified · "
            + " · ".join(parts)
            + " | "
            + state
            + " | "
            + anomaly_status
        )

    @app.server.before_request
    def homepage_redirect() -> Response | None:
        """Redirect an HTTP GET for the root route to the Users page.

        The Flask before-request hook leaves all other paths and methods unchanged.

        Returns
        -------
        flask.Response or None
            A 302 redirect for ``GET /``, otherwise ``None`` to continue dispatch.
        """
        if request.method == "GET" and request.path == "/":
            return redirect("/users", code=302)
        return None

    @app.server.get("/healthz")
    def healthz() -> dict[str, str]:
        """Return the lightweight HTTP liveness response.

        This endpoint checks the web process only: it does not validate upstream
        database access, snapshot completeness, or refresh-worker readiness.

        Returns
        -------
        dict[str, str]
            JSON-serializable ``{"status": "ok"}`` payload.
        """
        return {"status": "ok"}

    return app


app = create_app()
server = app.server
