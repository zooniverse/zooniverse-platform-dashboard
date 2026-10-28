"""Application factory. Importing Dash never connects to Panoptes or ERAS."""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
from flask import redirect, request

from zooniverse_dashboard.anomalies.catalog import visible_events
from zooniverse_dashboard.collection_config import effective_history_days
from zooniverse_dashboard.logging_utils import configure_refresh_logging
from zooniverse_dashboard.refresh import RefreshRejected, load_status
from zooniverse_dashboard.refresh_job import load_job, start_job
from zooniverse_dashboard.snapshots import SOURCES, load_snapshot

PACKAGE_DIR = Path(__file__).resolve().parent
LOG = logging.getLogger("zooniverse_dashboard.refresh")


def create_app() -> Dash:
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
    def do_refresh(_clicks):
        """Admit the job quickly; collection never runs in a web callback."""
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
    def job_progress(_tick, run_id, _status_tick):
        """Poll quickly only for active jobs, including jobs from other sessions."""
        hidden = {"display": "none"}
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
                    "Data refreshed successfully — Users, Classifications, Talk, and "
                    "anomaly analysis are up to date.",
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
    def status(_ticks, _message):
        parts = []
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
    def homepage_redirect():
        if request.method == "GET" and request.path == "/":
            return redirect("/users", code=302)
        return None

    @app.server.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app


app = create_app()
server = app.server
