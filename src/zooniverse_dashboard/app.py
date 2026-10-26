"""Application factory. Importing Dash never connects to Panoptes or ERAS."""

import logging
from pathlib import Path

import diskcache
from dash import (
    Dash,
    DiskcacheManager,
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
from zooniverse_dashboard.config import data_dir
from zooniverse_dashboard.logging_utils import configure_refresh_logging
from zooniverse_dashboard.pipeline import refresh_and_rebuild
from zooniverse_dashboard.refresh import RefreshRejected, load_status
from zooniverse_dashboard.snapshots import SOURCES, load_snapshot

PACKAGE_DIR = Path(__file__).resolve().parent
LOG = logging.getLogger("zooniverse_dashboard.refresh")


def create_app() -> Dash:
    configure_refresh_logging()
    cache_root = data_dir() / "dash-background-jobs"
    cache_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    worker_manager = DiskcacheManager(diskcache.Cache(str(cache_root)))
    app = Dash(
        __name__,
        use_pages=True,
        pages_folder=str(PACKAGE_DIR / "pages"),
        assets_folder=str(PACKAGE_DIR / "assets"),
        title="Zooniverse | Platform Dashboard",
        suppress_callback_exceptions=True,
        background_callback_manager=worker_manager,
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
                    dcc.Store(id="refresh-completion"),
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
        Output("refresh-completion", "data"),
        Input("refresh-now", "n_clicks"),
        background=True,
        progress=Output("refresh-steps", "children"),
        progress_default="Preparing refresh...",
        running=[
            (Output("refresh-now", "disabled"), True, False),
            (
                Output("refresh-panel", "style"),
                {"display": "block"},
                {"display": "none"},
            ),
        ],
        prevent_initial_call=True,
    )
    def do_refresh(set_progress, _clicks):
        LOG.info("event=web_refresh_button_clicked")
        lines = []

        def step(message):
            # This list contains only controlled operational messages; never
            # raw provider responses, exception messages, SQL or credentials.
            lines.append(message)
            set_progress(
                html.Ol([html.Li(item) for item in lines[-24:]], className="refresh-step-list")
            )

        try:
            result = refresh_and_rebuild(progress=step)
            return {"reload": True, "state": result["state"]}
        except RefreshRejected as exc:
            # Only expected fixed-text refresh rejections are safe to display.
            step(str(exc))
            return {"reload": False, "message": str(exc)}
        except Exception:
            # The server logger has sanitized failure diagnostics.
            step("Refresh unavailable; previous valid snapshots were preserved")
            return {"reload": False, "message": "Refresh unavailable; contact the operator"}

    # A completed job causes a real browser reload, preserving the current
    # route, query and hash. The memory Store resets, preventing a reload loop.
    clientside_callback(
        """function(result) {
            if (!result) return window.dash_clientside.no_update;
            if (result.reload) {
                window.location.reload();
                return window.dash_clientside.no_update;
            }
            return result.message || "Refresh was not completed";
        }""",
        Output("refresh-feedback", "children"),
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
            state = "No previous refresh" if info is None else f"Last attempt: {info['state']}"
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
