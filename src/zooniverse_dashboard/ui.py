"""Reusable Dash presentation of daily counts; no network or database access."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode

import plotly.graph_objects as go
from dash import Input, Output, callback, ctx, dcc, html, no_update

from zooniverse_dashboard.anomalies.catalog import event_by_id, visible_events
from zooniverse_dashboard.collection_config import effective_history_days
from zooniverse_dashboard.periods import calendar_periods, visible_date_range
from zooniverse_dashboard.snapshots import Snapshot, load_snapshot

WINDOW_OPTIONS = [
    {"label": f"Last {days} days", "value": str(days)} for days in (7, 28, 90, 180, 365)
] + [{"label": "All available", "value": "all"}]

PAGE_CONFIG = {
    "users": ("User registrations", "Daily registrations", "registrations", "Panoptes"),
    "classifications": ("Classifications", "Daily classifications", "classifications", "ERAS"),
    "talks": ("Talk activity", "Daily Talk comments", "comments", "ERAS"),
}


def layout_for(source: str):
    """A fully snapshot-driven page, with no remote calls on navigation."""
    title, chart_title, noun, provider = PAGE_CONFIG[source]
    try:
        snapshot = load_snapshot(source)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return html.Div(
            [
                html.Div("PLATFORM METRICS", className="kicker"),
                html.H1(title),
                html.Div(
                    "No valid aggregate snapshot yet. Click Refresh data to initialize the "
                    "configured history. An oversized or invalid source response is rejected.",
                    className="notice",
                ),
            ]
        )
    try:
        configured_days = effective_history_days()
    except ValueError:
        configured_days = None
    now = datetime.now(timezone.utc)
    stale_days = max(0, (now.date() - snapshot.as_of).days)
    cards = []
    for period in calendar_periods(snapshot):
        range_label = (
            f"{period.start.isoformat()} – {period.end.isoformat()} UTC"
            if period.start <= period.end
            else "No completed days"
        )
        cards.append(
            html.Div(
                [
                    html.Div(period.label, className="metric-title"),
                    html.Div(
                        "—" if period.count is None else f"{period.count:,}",
                        className="metric-value",
                    ),
                    html.Div(range_label, className="metric-period"),
                    html.Div(
                        "Insufficient historical coverage" if period.count is None else "",
                        className="metric-pending",
                    ),
                ],
                className="metric-card registration-metric",
            )
        )
    return html.Div(
        [
            dcc.Location(id=f"{source}-route", refresh=True),
            html.Div("PLATFORM METRICS · " + provider, className="kicker"),
            html.H1(title),
            html.P(
                "Daily aggregated counts only. Weeks begin Monday; periods are UTC and "
                "current week/month/year values are partial through the last collected day.",
                className="lead",
            ),
            html.Div(
                [
                    html.Strong("Snapshot last modified: "),
                    snapshot.last_modified.strftime("%Y-%m-%d %H:%M:%S UTC"),
                    " · Available data: ",
                    snapshot.first.isoformat(),
                    " – ",
                    snapshot.last.isoformat(),
                    " · Collected range: ",
                    (
                        "All available"
                        if snapshot.history_days == 0
                        else (
                            f"Last {snapshot.history_days} days"
                            if snapshot.history_days is not None
                            else "Legacy / unknown"
                        )
                    ),
                ],
                className="notice",
            ),
            (
                html.Div(
                    "Date-limit setting has changed (or snapshot predates this feature). "
                    "Refresh data to apply the selected historical window.",
                    className="notice notice-warning",
                )
                if configured_days is None or snapshot.history_days != configured_days
                else None
            ),
            (
                html.Div(
                    f"Data are {stale_days} completed UTC day(s) behind. "
                    "Use Refresh data above to update this source.",
                    className="notice notice-warning",
                )
                if stale_days
                else None
            ),
            html.Section(cards, className="registration-metrics"),
            html.Section(
                [
                    html.Div("HISTORICAL ACTIVITY", className="section-label"),
                    html.H2(chart_title),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Label("Visible time window", htmlFor=f"{source}-window"),
                                    dcc.Dropdown(
                                        id=f"{source}-window",
                                        options=WINDOW_OPTIONS,
                                        value="90",
                                        clearable=False,
                                        searchable=False,
                                        style={"minWidth": "220px"},
                                    ),
                                ],
                                className="registration-date-control",
                            ),
                            html.Div(
                                "Drag to pan or use the range slider; navigation stays inside "
                                "available dates.",
                                className="muted",
                            ),
                        ],
                        className="registration-filters",
                    ),
                    dcc.Loading(
                        dcc.Graph(
                            id=f"{source}-graph",
                            className="history-graph",
                            config={
                                "displaylogo": False,
                                "responsive": True,
                                "scrollZoom": False,
                                "modeBarButtonsToRemove": ["select2d", "lasso2d"],
                            },
                            style={"height": "500px"},
                        ),
                        type="circle",
                    ),
                    html.P(
                        "Diamonds mark the start of an anomaly episode. Click one for "
                        "statistical details; they are not confirmed incidents.",
                        className="muted",
                    ),
                ],
                className="section-card",
            ),
        ]
    )


def history_figure(
    snapshot: Snapshot, *, window: str = "90", focus_event: dict | None = None
) -> go.Figure:
    """Show the complete series with a constrained, pannable initial viewport."""
    visible_first, visible_last = visible_date_range(
        first=snapshot.first, last=snapshot.last, window=window
    )
    x = [d.isoformat() for d, _ in snapshot.daily]
    y = [count for _, count in snapshot.daily]
    trailing = [sum(y[max(0, i - 6) : i + 1]) / min(i + 1, 7) for i in range(len(y))]
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=x,
            y=y,
            name="Daily count",
            mode="lines",
            line={"color": "#16718c", "width": 1.8},
            hovertemplate="%{x}: %{y:,}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=trailing,
            name="7-day trailing average",
            mode="lines",
            line={"color": "#405b76", "width": 2, "dash": "dash"},
            hovertemplate="%{x}: %{y:,.0f}<extra></extra>",
        )
    )
    events, _statuses = visible_events()
    for direction, color, title in (
        ("spike", "#c53f42", "Unusual increase episode"),
        ("drop", "#bc7919", "Unusual decrease episode"),
    ):
        selected = [
            event
            for event in events
            if event["source"] == snapshot.source and event["direction"] == direction
        ]
        if selected:
            row_by_date = dict(snapshot.daily)
            fig.add_trace(
                go.Scatter(
                    x=[v["detected"] for v in selected],
                    y=[row_by_date.get(date.fromisoformat(v["detected"]), 0) for v in selected],
                    name=title,
                    mode="markers",
                    marker={"color": color, "symbol": "diamond", "size": 12},
                    customdata=[[v["id"], v["start"], v["end"]] for v in selected],
                    hovertemplate=(
                        "%{x}: %{y:,}<br>Episode %{customdata[1]}–%{customdata[2]}"
                        "<br>Click for details<extra></extra>"
                    ),
                )
            )
    fig.update_layout(
        template="plotly_white",
        margin={"l": 40, "r": 18, "t": 35, "b": 40},
        xaxis={
            "type": "date",
            "range": [visible_first.isoformat(), (visible_last + timedelta(days=1)).isoformat()],
            "minallowed": snapshot.first.isoformat(),
            "maxallowed": (snapshot.last + timedelta(days=1)).isoformat(),
            "rangeslider": {"visible": True, "thickness": 0.11},
        },
        yaxis={"title": "Count / day", "rangemode": "tozero"},
        dragmode="pan",
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.12, "x": 0},
        uirevision=f"{snapshot.source}-{window}",
    )
    if focus_event is not None and focus_event["source"] == snapshot.source:
        begin = max(snapshot.first, date.fromisoformat(focus_event["start"]) - timedelta(days=7))
        end = min(snapshot.last, date.fromisoformat(focus_event["end"]) + timedelta(days=7))
        fig.update_xaxes(range=[begin.isoformat(), (end + timedelta(days=1)).isoformat()])
        visible_counts = [count for day, count in snapshot.daily if begin <= day <= end]
        if visible_counts:
            fig.update_yaxes(range=[0, max(1, max(visible_counts)) * 1.15])
        fig.update_layout(uirevision=f"{snapshot.source}-focus-{focus_event['id']}")
    return fig


def register_history_callback(source: str):
    """Register snapshot-backed chart navigation and marker hover affordances."""

    @callback(
        Output(f"{source}-graph", "figure"),
        Input(f"{source}-route", "search"),
        Input(f"{source}-window", "value"),
    )
    def update(search, window):
        event_id = parse_qs((search or "").lstrip("?")).get("episode", [None])[0]
        route_changed = f"{source}-route.search" in ctx.triggered_prop_ids
        window_changed = f"{source}-window.value" in ctx.triggered_prop_ids
        focus = (
            event_by_id(event_id, source=source)
            if isinstance(event_id, str) and (route_changed or not window_changed)
            else None
        )
        try:
            return history_figure(load_snapshot(source), window=window, focus_event=focus)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            fig = go.Figure()
            fig.add_annotation(text="Aggregate snapshot unavailable", showarrow=False)
            return fig

    @callback(
        Output(f"{source}-route", "href"),
        Input(f"{source}-graph", "clickData"),
        prevent_initial_call=True,
    )
    def open_anomaly(click_data):
        if not click_data:
            return no_update
        for point in click_data.get("points", []):
            custom = point.get("customdata")
            if not isinstance(custom, (list, tuple)) or not custom:
                continue
            event_id = custom[0]
            if isinstance(event_id, str) and event_by_id(event_id, source=source):
                return "/anomalies?" + urlencode({"event": event_id})
        return no_update

    return update
