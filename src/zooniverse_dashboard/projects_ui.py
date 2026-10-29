"""Snapshot-only Projects page: approved launch counts by month or year."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html

from zooniverse_dashboard.collection_config import effective_history_days
from zooniverse_dashboard.project_launches import LaunchSnapshot, load_launches
from zooniverse_dashboard.projects_data import (
    INTERVALS,
    WINDOWS,
    launch_buckets,
    next_period,
    project_metrics,
    viewport_start,
)
from zooniverse_dashboard.ui import (
    _display_date,
    _period_bar_geometry,
    _period_label,
    _period_ticks,
)

WINDOW_OPTIONS = [
    {"label": "Last 12 months", "value": "12"},
    {"label": "Last 3 years", "value": "36"},
    {"label": "Last 5 years", "value": "60"},
    {"label": "All available", "value": "all"},
]
INTERVAL_OPTIONS = [
    {"label": "Month", "value": "monthly"},
    {"label": "Year", "value": "yearly"},
]
PLOT_OPTIONS = [
    {"label": "Line", "value": "line"},
    {"label": "Bars", "value": "bars"},
]


def projects_figure(
    snapshot: LaunchSnapshot,
    *,
    window: str = "36",
    interval: str = "monthly",
    plot_type: str = "bars",
) -> go.Figure:
    """Launch counts per calendar period; no markers or anomaly inference."""
    if interval not in INTERVALS or window not in WINDOWS or plot_type not in {"bars", "line"}:
        raise ValueError("Unsupported Projects graph configuration")
    bins = launch_buckets(snapshot, interval)
    begin = viewport_start(snapshot, window)
    included = [i for i, bucket in enumerate(bins) if bucket.last_observed >= begin]
    first_index = included[0]
    last_index = len(bins) - 1
    shown = bins[first_index:]
    peak = max((bucket.count for bucket in shown), default=0)
    customdata = [
        [_display_date(bucket.first_observed), _display_date(bucket.last_observed)]
        for bucket in bins
    ]
    fig = go.Figure()
    labels = [_period_label(bucket, interval) for bucket in bins]
    if plot_type == "bars":
        geometry = [_period_bar_geometry(bucket, i, interval) for i, bucket in enumerate(bins)]
        fig.add_trace(
            go.Bar(
                x=[center for center, _ in geometry],
                y=[bucket.count for bucket in bins],
                width=[width for _, width in geometry],
                name="Approved launches",
                marker={"color": "#16718c"},
                customdata=customdata,
                hovertemplate=(
                    "%{customdata[0]} – %{customdata[1]} UTC"
                    "<br>Projects launched: %{y:,}<extra></extra>"
                ),
            )
        )
        tickvals, ticktext = _period_ticks(first_index, last_index, labels)
        xaxis = {
            "type": "linear",
            "tickmode": "array",
            "tickvals": tickvals,
            "ticktext": ticktext,
            "tickangle": -30,
            "automargin": True,
            "range": [first_index - 0.5, last_index + 0.5],
            "minallowed": -0.5,
            "maxallowed": len(bins) - 0.5,
            "rangeslider": {"visible": True, "thickness": 0.11, "yaxis": {"rangemode": "auto"}},
        }
    else:
        fig.add_trace(
            go.Scatter(
                x=[bucket.center.isoformat() for bucket in bins],
                y=[bucket.count for bucket in bins],
                name="Approved launches",
                mode="lines+markers",
                marker={"size": 6},
                line={"color": "#16718c", "width": 2},
                customdata=customdata,
                hovertemplate=(
                    "%{customdata[0]} – %{customdata[1]} UTC"
                    "<br>Projects launched: %{y:,}<extra></extra>"
                ),
            )
        )
        xaxis = {
            "type": "date",
            "hoverformat": "%a %d %b %Y",
            "range": [begin.isoformat(), (snapshot.last + timedelta(days=1)).isoformat()],
            "minallowed": snapshot.first.isoformat(),
            "maxallowed": (snapshot.last + timedelta(days=1)).isoformat(),
            "rangeslider": {"visible": True, "thickness": 0.11, "yaxis": {"rangemode": "auto"}},
        }
    meta = {
        "source": "projects",
        "window": window,
        "interval": interval,
        "plot_type": plot_type,
        "first": snapshot.first.isoformat(),
        "last_exclusive": (snapshot.last + timedelta(days=1)).isoformat(),
        "focused": False,
        "revision": (
            f"projects:{snapshot.last_modified.isoformat()}:{window}:{interval}:{plot_type}"
        ),
        "period_bounds": None,
    }
    # Numeric bar slots must publish the corresponding real calendar boundaries,
    # so the shared viewport survives switching chart types and pages.
    if plot_type == "bars":
        meta["period_bounds"] = [bucket.start.isoformat() for bucket in bins] + [
            next_period(bins[-1].start, interval).isoformat()
        ]
    fig.update_layout(
        template="plotly_white",
        margin={"l": 40, "r": 18, "t": 35, "b": 40},
        xaxis=xaxis,
        yaxis={
            "title": f"Projects launched per {'month' if interval == 'monthly' else 'year'}",
            "range": [0, max(1, peak) * 1.15],
            "rangemode": "tozero",
        },
        meta={"history_zoom": meta, **({"period_labels": labels} if plot_type == "bars" else {})},
        dragmode="zoom",
        hovermode="closest" if plot_type == "bars" else "x unified",
        uirevision=meta["revision"],
    )
    return fig


def projects_layout():
    """Render the page exclusively from the last valid launch JSON."""
    try:
        snapshot = load_launches()
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return html.Div(
            [
                html.Div("PLATFORM METRICS · PANOPTES", className="kicker"),
                html.H1("Projects"),
                html.Div(
                    "No valid project launch snapshot yet. Click Refresh data to collect "
                    "approved launch dates from Panoptes.",
                    className="notice",
                ),
            ]
        )
    now = datetime.now(timezone.utc)
    stale_days = max(0, (now.date() - snapshot.as_of).days)
    try:
        configured_days = effective_history_days()
    except ValueError:
        configured_days = None
    cards = []
    for metric in project_metrics(snapshot):
        range_label = (
            f"{metric.start.isoformat()} – {metric.end.isoformat()} UTC"
            if metric.start <= metric.end
            else "No completed days"
        )
        cards.append(
            html.Div(
                [
                    html.Div(metric.label, className="metric-title"),
                    html.Div(
                        "—" if metric.count is None else f"{metric.count:,}",
                        className="metric-value",
                    ),
                    html.Div(range_label, className="metric-period"),
                    html.Div(
                        "Insufficient historical coverage" if metric.count is None else "",
                        className="metric-pending",
                    ),
                ],
                className="metric-card registration-metric",
            )
        )
    return html.Div(
        [
            html.Div("PLATFORM METRICS · PANOPTES", className="kicker"),
            html.H1("Projects"),
            html.P(
                "Approved Zooniverse project launches per calendar month or year (UTC). "
                "Counts reflect recorded launch dates, not project creation dates. "
                "Current and boundary periods may be partial.",
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
                        else f"Last {snapshot.history_days} days"
                    ),
                ],
                className="notice",
            ),
            (
                html.Div(
                    "Date-limit setting has changed. Refresh data to apply the new window.",
                    className="notice notice-warning",
                )
                if configured_days is None or configured_days != snapshot.history_days
                else None
            ),
            (
                html.Div(
                    f"Launch data are {stale_days} completed UTC day(s) behind. "
                    "Use Refresh data above to update them.",
                    className="notice notice-warning",
                )
                if stale_days
                else None
            ),
            html.Section(cards, className="registration-metrics"),
            html.Section(
                [
                    html.Div("PROJECT LAUNCHES", className="section-label"),
                    html.H2("Project launch history"),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Label("Visible time window", htmlFor="projects-window"),
                                    dcc.Dropdown(
                                        id="projects-window",
                                        options=WINDOW_OPTIONS,
                                        value="36",
                                        clearable=False,
                                        searchable=False,
                                        style={"minWidth": "220px"},
                                    ),
                                ],
                                className="registration-date-control",
                            ),
                            html.Div(
                                [
                                    html.Label("Group by", htmlFor="projects-interval"),
                                    dcc.RadioItems(
                                        id="projects-interval",
                                        options=INTERVAL_OPTIONS,
                                        value="monthly",
                                        inline=True,
                                        className="history-radio-group",
                                    ),
                                ],
                                className="registration-date-control",
                            ),
                            html.Div(
                                [
                                    html.Label("Chart type", htmlFor="projects-plot-type"),
                                    dcc.RadioItems(
                                        id="projects-plot-type",
                                        options=PLOT_OPTIONS,
                                        value="bars",
                                        inline=True,
                                        className="history-radio-group",
                                    ),
                                ],
                                className="registration-date-control",
                            ),
                        ],
                        className="registration-filters",
                    ),
                    dcc.Loading(
                        dcc.Graph(
                            id="projects-graph",
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
                        "Zoom, pan and use the range slider to inspect the launch history. "
                        "The selected UTC time interval is shared with Users, "
                        "Classifications and Talk in this browser tab. "
                        "Periods with no launches appear as zero; boundary periods are "
                        "not extrapolated to complete months or years.",
                        className="muted",
                    ),
                ],
                className="section-card",
            ),
        ]
    )


def register_projects_callback():
    """Render the Projects chart without a DB query on interaction."""

    @callback(
        Output("projects-graph", "figure"),
        Input("projects-window", "value"),
        Input("projects-interval", "value"),
        Input("projects-plot-type", "value"),
    )
    def update(window, interval, plot_type):
        try:
            return projects_figure(
                load_launches(), window=window, interval=interval, plot_type=plot_type
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            fig = go.Figure()
            fig.add_annotation(text="Project launch snapshot unavailable", showarrow=False)
            return fig

    return update
