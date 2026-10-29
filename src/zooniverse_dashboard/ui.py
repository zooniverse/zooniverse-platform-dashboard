"""Reusable Dash presentation of daily counts; no network or database access."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from html import escape
from urllib.parse import parse_qs, urlencode

import plotly.graph_objects as go
from dash import Input, Output, callback, ctx, dcc, html, no_update

from zooniverse_dashboard.anomalies.catalog import event_by_id, visible_events
from zooniverse_dashboard.chart_data import ChartBucket, bucket_start, group_daily
from zooniverse_dashboard.collection_config import effective_history_days
from zooniverse_dashboard.periods import calendar_periods, visible_date_range
from zooniverse_dashboard.project_launches import LaunchSnapshot, load_launches
from zooniverse_dashboard.snapshots import Snapshot, load_snapshot

WINDOW_OPTIONS = [
    {"label": f"Last {days} days", "value": str(days)} for days in (7, 28, 90, 180, 365)
] + [{"label": "All available", "value": "all"}]

PAGE_CONFIG = {
    "users": ("User registrations", "Registration history", "registrations", "Panoptes"),
    "classifications": ("Classifications", "Classification history", "classifications", "ERAS"),
    "talks": ("Talk activity", "Talk comment history", "comments", "ERAS"),
}

INTERVAL_OPTIONS = [
    {"label": label, "value": value}
    for value, label in (
        ("daily", "Day"),
        ("weekly", "Week"),
        ("monthly", "Month"),
        ("yearly", "Year"),
    )
]
PLOT_OPTIONS = [
    {"label": "Line", "value": "line"},
    {"label": "Bars", "value": "bars"},
]


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
                "Daily aggregate snapshots, viewable as day/week/month/year totals. "
                "Weeks begin Monday; all calendar periods use UTC. "
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
                                [
                                    html.Label("Group by", htmlFor=f"{source}-interval"),
                                    dcc.RadioItems(
                                        id=f"{source}-interval",
                                        options=INTERVAL_OPTIONS,
                                        value="daily",
                                        inline=True,
                                        className="history-radio-group",
                                    ),
                                ],
                                className="registration-date-control",
                            ),
                            html.Div(
                                [
                                    html.Label("Chart type", htmlFor=f"{source}-plot-type"),
                                    dcc.RadioItems(
                                        id=f"{source}-plot-type",
                                        options=PLOT_OPTIONS,
                                        value="line",
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
                        "Drag a rectangle to zoom both axes. Choose Pan in the chart toolbar "
                        "to move across dates, or use the range slider. The vertical scale "
                        "adjusts to the visible data when panning or changing the date range. "
                        "Daily views show clickable anomaly diamonds; other intervals show "
                        "calendar totals without moving the daily anomaly dates. "
                        "Violet triangles along the baseline mark approved Panoptes project "
                        "launches on their recorded dates (hover for UTC dates and public titles). "
                        "On grouped bar charts, equal-width bars represent calendar periods "
                        "and launch positions indicate the day within each period. ",
                        className="muted",
                    ),
                ],
                className="section-card",
            ),
        ]
    )


def _display_date(day: date) -> str:
    """Consistent UTC hover dates across calendar intervals and overlays."""
    return day.strftime("%a %d %b %Y")


def _launch_hover(day: date, count: int, names: tuple[str, ...]) -> str:
    """Escape untrusted project titles before inserting them into Plotly HTML."""
    label = f"{count} project{'s' if count != 1 else ''} launched"
    titles = "".join(f"<br>• {escape(name, quote=True)}" for name in names[:8])
    if len(names) > 8:
        titles += f"<br>… and {len(names) - 8} more public titles"
    if len(names) < count:
        titles += "<br>Some project titles are private or unavailable"
    return f"{_display_date(day)} UTC<br>{label}{titles}"


_PERIOD_BAR_WIDTH = 0.82


def _next_bucket_start(start: date, interval: str) -> date:
    """Return the exclusive calendar end of a grouped bucket (not its observed end)."""
    if interval == "weekly":
        return start + timedelta(days=7)
    if interval == "monthly":
        if start.month == 12:
            return date(start.year + 1, 1, 1)
        return date(start.year, start.month + 1, 1)
    if interval == "yearly":
        return date(start.year + 1, 1, 1)
    raise ValueError("Only grouped bars use indexed calendar buckets")


def _period_label(bucket: ChartBucket, interval: str) -> str:
    """Readable categorical labels for equally spaced grouped bars."""
    if interval == "weekly":
        return bucket.start.strftime("%d %b '%y")
    if interval == "monthly":
        return bucket.start.strftime("%b %Y")
    if interval == "yearly":
        return str(bucket.start.year)
    raise ValueError("Only grouped bars use period labels")


def _period_position(day: date, bucket: ChartBucket, index: int, interval: str) -> float:
    """Preserve a launch's calendar-day position within an equal-width bar slot."""
    span = (_next_bucket_start(bucket.start, interval) - bucket.start).days
    elapsed = (day - bucket.start).days
    return index + _PERIOD_BAR_WIDTH * ((elapsed + 0.5) / span - 0.5)


def _period_bar_geometry(bucket: ChartBucket, index: int, interval: str) -> tuple[float, float]:
    """Center/width for the observed portion, including partial edge periods."""
    span = (_next_bucket_start(bucket.start, interval) - bucket.start).days
    first = (bucket.first_observed - bucket.start).days
    observed = (bucket.last_observed - bucket.first_observed).days + 1
    center = index + _PERIOD_BAR_WIDTH * ((first + observed / 2) / span - 0.5)
    return center, _PERIOD_BAR_WIDTH * observed / span


def _period_ticks(first: int, last: int, labels: list[str], *, max_ticks: int = 10):
    """Initial ticks for the visible window; browser navigation adapts their density."""
    step = max(1, (last - first + 1 + max_ticks - 1) // max_ticks)
    indexes = list(range(max(0, first), min(len(labels) - 1, last) + 1, step))
    return indexes, [labels[index] for index in indexes]


def history_figure(
    snapshot: Snapshot,
    *,
    window: str = "90",
    focus_event: dict | None = None,
    interval: str = "daily",
    plot_type: str = "line",
    launches: LaunchSnapshot | None = None,
) -> go.Figure:
    """Create a calendar-binned plot with a y range fitted to its initial viewport.

    Daily anomaly events remain plotted at their exact detected UTC dates.
    They are not shifted into a weekly/monthly/yearly bucket. Grouped bars
    are evenly spaced by calendar period, with launch days positioned within
    their actual period rather than on an absolute-time axis.
    """
    if plot_type not in {"line", "bars"}:
        raise ValueError("Unsupported chart type")
    bins = group_daily(snapshot.daily, interval)
    visible_first, visible_last = visible_date_range(
        first=snapshot.first, last=snapshot.last, window=window
    )
    begin, end = visible_first, visible_last
    if focus_event is not None and focus_event["source"] == snapshot.source:
        begin = max(snapshot.first, date.fromisoformat(focus_event["start"]) - timedelta(days=7))
        end = min(snapshot.last, date.fromisoformat(focus_event["end"]) + timedelta(days=7))

    # Include the entire calendar bucket that contains the selected boundary.
    displayed_first = max(snapshot.first, bucket_start(begin, interval))
    shown = [b for b in bins if b.last_observed >= begin and b.first_observed <= end]
    displayed_last = max((b.last_observed for b in shown), default=end)
    indexed_bars = plot_type == "bars" and interval != "daily"
    bar_geometry = (
        [_period_bar_geometry(bucket, index, interval) for index, bucket in enumerate(bins)]
        if indexed_bars
        else []
    )
    x = (
        [center for center, _ in bar_geometry]
        if indexed_bars
        else [b.center.isoformat() for b in bins]
    )
    y = [b.count for b in bins]
    hover_periods = [
        [_display_date(b.first_observed), _display_date(b.last_observed)] for b in bins
    ]
    unit = {"daily": "day", "weekly": "week", "monthly": "month", "yearly": "year"}[interval]
    name = PAGE_CONFIG[snapshot.source][2].capitalize()
    fig = go.Figure()
    period_hover = (
        "%{customdata[0]} UTC<br>Total: %{y:,}<extra></extra>"
        if interval == "daily"
        else "%{customdata[0]} – %{customdata[1]} UTC<br>Total: %{y:,}<extra></extra>"
    )
    if plot_type == "bars":
        fig.add_trace(
            go.Bar(
                x=x,
                y=y,
                width=(
                    [width for _, width in bar_geometry]
                    if indexed_bars
                    else [b.width_ms for b in bins]
                ),
                name=f"{unit.title()} total",
                marker={"color": "#16718c"},
                customdata=hover_periods,
                hovertemplate=period_hover,
            )
        )
    else:
        fig.add_trace(
            go.Scatter(
                x=x,
                y=y,
                name=f"{unit.title()} total",
                mode="lines" if interval == "daily" else "lines+markers",
                line={"color": "#16718c", "width": 1.8},
                marker={"size": 5},
                customdata=hover_periods,
                hovertemplate=period_hover,
            )
        )
        if interval == "daily":
            trailing = [sum(y[max(0, i - 6) : i + 1]) / min(i + 1, 7) for i in range(len(y))]
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=trailing,
                    name="7-day trailing average",
                    mode="lines",
                    line={"color": "#405b76", "width": 2, "dash": "dash"},
                    hovertemplate="%{x|%a %d %b %Y} UTC: %{y:,.0f}<extra></extra>",
                )
            )
    if interval == "daily":
        events, _statuses = visible_events()
        row_by_date = dict(snapshot.daily)
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
                fig.add_trace(
                    go.Scatter(
                        x=[v["detected"] for v in selected],
                        y=[row_by_date.get(date.fromisoformat(v["detected"]), 0) for v in selected],
                        name=title,
                        mode="markers",
                        marker={"color": color, "symbol": "diamond", "size": 12},
                        customdata=[
                            [
                                v["id"],
                                _display_date(date.fromisoformat(v["start"])),
                                _display_date(date.fromisoformat(v["end"])),
                                _display_date(date.fromisoformat(v["detected"])),
                            ]
                            for v in selected
                        ],
                        hovertemplate=(
                            "%{customdata[3]} UTC: %{y:,}"
                            "<br>Episode %{customdata[1]} – %{customdata[2]}"
                            "<br>Click for details<extra></extra>"
                        ),
                    )
                )
    max_in_view = max((b.count for b in shown), default=0)
    # Keep launches on the baseline for both lines and bars. Drawing the
    # scatter after the bars makes its saturated violet markers visible over
    # teal bar fills, without the white borders that merged into blobs when
    # many projects launched close together. Calendar X positions stay exact.
    if launches is not None:
        by_period = {bucket.start: (index, bucket) for index, bucket in enumerate(bins)}
        marker_days = [
            (day, count)
            for day, count in launches.launches
            if snapshot.first <= day <= snapshot.last and launches.first <= day <= launches.last
        ]
        if marker_days:
            marker_x: list[str | float] = []
            for day, _count in marker_days:
                if indexed_bars:
                    index, bucket = by_period[bucket_start(day, interval)]
                    marker_x.append(_period_position(day, bucket, index, interval))
                else:
                    marker_x.append(day.isoformat())
            fig.add_trace(
                go.Scatter(
                    x=marker_x,
                    y=[0] * len(marker_days),
                    name="Project launched",
                    mode="markers",
                    marker={
                        "color": "#c026d3",
                        "symbol": "triangle-up",
                        "size": 9,
                        "line": {"width": 0},
                        "opacity": 0.9,
                    },
                    # Draw the full triangle at y=0, rather than cutting it in
                    # half along the bottom edge of the plot area.
                    cliponaxis=False,
                    customdata=[
                        _launch_hover(day, count, launches.names_for(day))
                        for day, count in marker_days
                    ],
                    hovertemplate="%{customdata}<extra></extra>",
                )
            )
    if interval == "daily" and plot_type == "line":
        trailing_in_view = [
            value for (bucket, value) in zip(bins, trailing) if begin <= bucket.start <= end
        ]
        max_in_view = max(max_in_view, max(trailing_in_view, default=0))
    # Indexed bar positions are equal-width categories; the launch positions
    # retain their fractional calendar day *inside* each category. All line
    # plots and daily bar plots continue using the true UTC date axis.
    if indexed_bars:
        labels = [_period_label(bucket, interval) for bucket in bins]
        first_index = next(
            (index for index, bucket in enumerate(bins) if bucket.last_observed >= begin), 0
        )
        last_index = next(
            (index for index in range(len(bins) - 1, -1, -1) if bins[index].first_observed <= end),
            len(bins) - 1,
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
        xaxis = {
            "type": "date",
            "hoverformat": "%a %d %b %Y",
            "range": [
                displayed_first.isoformat(),
                (displayed_last + timedelta(days=1)).isoformat(),
            ],
            "minallowed": snapshot.first.isoformat(),
            "maxallowed": (snapshot.last + timedelta(days=1)).isoformat(),
            "rangeslider": {"visible": True, "thickness": 0.11, "yaxis": {"rangemode": "auto"}},
        }
    fig.update_layout(
        template="plotly_white",
        margin={"l": 40, "r": 18, "t": 35, "b": 40},
        xaxis=xaxis,
        meta={"period_labels": labels} if indexed_bars else None,
        yaxis={
            "title": f"{name} per {unit}",
            "range": [0, max(1, max_in_view) * 1.15],
            "rangemode": "tozero",
        },
        dragmode="zoom",
        hovermode="closest" if indexed_bars else "x unified",
        legend={"orientation": "h", "y": 1.12, "x": 0},
        uirevision=(
            f"{snapshot.source}-{snapshot.last_modified.isoformat()}-{window}"
            f"-{interval}-{plot_type}"
            + (f"-focus-{focus_event['id']}" if focus_event is not None else "")
        ),
    )
    return fig


def _load_optional_launches(source: str) -> LaunchSnapshot | None:
    if source not in PAGE_CONFIG:
        return None
    try:
        return load_launches()
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def register_history_callback(source: str):
    """Register snapshot-backed chart navigation and marker hover affordances."""

    @callback(
        Output(f"{source}-graph", "figure"),
        Input(f"{source}-route", "search"),
        Input(f"{source}-window", "value"),
        Input(f"{source}-interval", "value"),
        Input(f"{source}-plot-type", "value"),
    )
    def update(search, window, interval, plot_type):
        event_id = parse_qs((search or "").lstrip("?")).get("episode", [None])[0]
        route_changed = f"{source}-route.search" in ctx.triggered_prop_ids
        controls_changed = any(
            f"{source}-{field}.value" in ctx.triggered_prop_ids
            for field in ("window", "interval", "plot-type")
        )
        focus = (
            event_by_id(event_id, source=source)
            if isinstance(event_id, str) and (route_changed or not controls_changed)
            else None
        )
        try:
            return history_figure(
                load_snapshot(source),
                window=window,
                focus_event=focus,
                interval=interval,
                plot_type=plot_type,
                launches=_load_optional_launches(source),
            )
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
