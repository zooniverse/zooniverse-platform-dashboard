"""Reusable Dash presentation of daily counts; no network or database access."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html

from zooniverse_dashboard.detection import find_weekday_outliers
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
                    "available history. ERAS sources begin with 28 days; a deliberate "
                    "historical backfill is available from the command line.",
                    className="notice",
                ),
            ]
        )
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
                ],
                className="notice",
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
                        "Red and amber diamonds indicate experimental statistical deviations, "
                        "not confirmed abuse or outages.",
                        className="muted",
                    ),
                ],
                className="section-card",
            ),
        ]
    )


def history_figure(snapshot: Snapshot, *, window: str = "90") -> go.Figure:
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
    outliers = find_weekday_outliers(snapshot.counts())
    for direction, color, title in (
        ("spike", "#c53f42", "Unusual increase"),
        ("drop", "#bc7919", "Unusual decrease"),
    ):
        selected = [outlier for outlier in outliers if outlier.direction == direction]
        if selected:
            fig.add_trace(
                go.Scatter(
                    x=[v.day.isoformat() for v in selected],
                    y=[v.observed for v in selected],
                    name=title,
                    mode="markers",
                    marker={"color": color, "symbol": "diamond", "size": 10},
                    customdata=[[v.baseline, v.score] for v in selected],
                    hovertemplate="%{x}: %{y:,}<br>Weekday baseline: "
                    "%{customdata[0]:,.0f}<br>Score: "
                    "%{customdata[1]:+.1f}<extra></extra>",
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
    return fig


def register_history_callback(source: str):
    """Each page registers exactly one callback to read its JSON snapshot."""

    @callback(Output(f"{source}-graph", "figure"), Input(f"{source}-window", "value"))
    def update(window):
        try:
            return history_figure(load_snapshot(source), window=window)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            fig = go.Figure()
            fig.add_annotation(text="Aggregate snapshot unavailable", showarrow=False)
            return fig

    return update
