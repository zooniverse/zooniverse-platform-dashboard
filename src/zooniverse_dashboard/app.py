"""Application factory. Importing Dash never connects to Panoptes or ERAS."""
from pathlib import Path

from dash import Dash, Input, Output, dcc, html, page_container, page_registry
from flask import redirect, request

from zooniverse_dashboard.refresh import RefreshRejected, load_status, refresh_all
from zooniverse_dashboard.snapshots import SOURCES, load_snapshot

PACKAGE_DIR = Path(__file__).resolve().parent


def create_app() -> Dash:
    app = Dash(
        __name__, use_pages=True,
        pages_folder=str(PACKAGE_DIR / "pages"),
        assets_folder=str(PACKAGE_DIR / "assets"),
        title="Zooniverse | Platform Dashboard",
        suppress_callback_exceptions=True,
    )
    app.layout = html.Div([
        html.Header([
            html.Div([
                html.Div("ZOONIVERSE", className="brand-eyebrow"),
                html.Div("Platform Dashboard", className="brand-title"),
            ], className="brand"),
            html.Nav([
                dcc.Link(page["name"], href=page["relative_path"])
                for page in page_registry.values()
            ], className="navigation", **{"aria-label": "Main navigation"}),
        ], className="site-header"),
        html.Div([
            html.Button("Refresh data", id="refresh-now", n_clicks=0),
            dcc.Loading(html.Span(id="refresh-feedback", role="status"), type="circle"),
        ], className="manual-refresh-bar"),
        dcc.Interval(id="refresh-status-poll", interval=60_000, n_intervals=0),
        html.Div(id="refresh-system-status", className="refresh-system-status"),
        html.Main(page_container, className="page-container"),
        html.Footer("Internal Zooniverse analytics · Aggregated counts only · UTC", className="site-footer"),
    ], className="app-shell")

    @app.callback(Output("refresh-feedback", "children"), Input("refresh-now", "n_clicks"),
                  prevent_initial_call=True)
    def do_refresh(_clicks):
        try:
            result = refresh_all()
            if result["state"] == "ok":
                return "Refresh complete. Reload this page to show the new snapshots."
            return "Some sources could not refresh; previous valid snapshots were preserved."
        except RefreshRejected as exc:
            return str(exc)
        except Exception:
            return "Refresh unavailable; contact the dashboard operator."

    @app.callback(Output("refresh-system-status", "children"),
                  Input("refresh-status-poll", "n_intervals"),
                  Input("refresh-feedback", "children"))
    def status(_ticks, _message):
        parts = []
        for source in SOURCES:
            try:
                snapshot = load_snapshot(source)
                parts.append(
                    f"{source}: {snapshot.last_modified.strftime('%Y-%m-%d %H:%M UTC')}"
                )
            except (OSError, ValueError, TypeError, KeyError):
                parts.append(f"{source}: not yet collected")
        try:
            info = load_status()
            state = "No previous refresh" if info is None else f"Last attempt: {info['state']}"
        except (OSError, ValueError):
            state = "Refresh status unavailable"
        return "Snapshot last modified · " + " · ".join(parts) + " | " + state

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
