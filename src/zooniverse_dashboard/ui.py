"""Render activity histories and register the three snapshot-backed chart pages.

Users, Classifications, and Talk share this presentation layer. Layouts read
validated local aggregate snapshots and create daily/weekly/monthly/yearly
Plotly figures. Daily views optionally show anomaly diamonds; all intervals
show approved project launches as violet baseline markers when a launch
snapshot is available. Public project names are escaped before appearing in
Plotly hover HTML.

Grouped **bar** charts use numeric, equally spaced calendar slots instead of
a continuous date axis. Their period boundaries are embedded as UTC dates in
``layout.meta.history_zoom`` so the browser-side ``history_zoom.js`` asset
can retain an absolute time window across binning, chart types, and pages.
Line charts and daily bars retain a true UTC date axis. Figure construction
performs no Panoptes or ERAS requests; missing or invalid snapshots produce
an explanatory placeholder instead of failing page navigation.

See Also
--------
:class:`zooniverse_dashboard.snapshots.Snapshot`
    Validated continuous daily activity history.
:class:`zooniverse_dashboard.project_launches.LaunchSnapshot`
    Optional sparse project-launch overlay.
:func:`zooniverse_dashboard.chart_data.group_daily`
    Calendar-bucket aggregation for activity counts.
:func:`zooniverse_dashboard.projects_ui.projects_figure`
    Dedicated monthly/yearly projects chart.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from html import escape
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode

import plotly.graph_objects as go
from dash import Input, Output, callback, ctx, dcc, html, no_update
from dash.development.base_component import Component

from zooniverse_dashboard.anomalies.catalog import event_by_id, visible_events
from zooniverse_dashboard.chart_data import ChartBucket, bucket_start, group_daily
from zooniverse_dashboard.collection_config import effective_history_days
from zooniverse_dashboard.periods import calendar_periods, visible_date_range
from zooniverse_dashboard.project_launches import LaunchSnapshot, load_launches
from zooniverse_dashboard.snapshots import Snapshot, load_snapshot

WINDOW_OPTIONS: list[dict[str, str]] = [
    {"label": f"Last {days} days", "value": str(days)} for days in (7, 28, 90, 180, 365)
] + [{"label": "All available", "value": "all"}]

PAGE_CONFIG: dict[str, tuple[str, str, str, str]] = {
    "users": ("User registrations", "Registration history", "registrations", "Panoptes"),
    "classifications": ("Classifications", "Classification history", "classifications", "ERAS"),
    "talks": ("Talk activity", "Talk comment history", "comments", "ERAS"),
}

INTERVAL_OPTIONS: list[dict[str, str]] = [
    {"label": label, "value": value}
    for value, label in (
        ("daily", "Day"),
        ("weekly", "Week"),
        ("monthly", "Month"),
        ("yearly", "Year"),
    )
]
PLOT_OPTIONS: list[dict[str, str]] = [
    {"label": "Line", "value": "line"},
    {"label": "Bars", "value": "bars"},
]


def layout_for(source: str) -> Component:
    """Build a snapshot-driven activity page for one supported metric.

    Render summary cards, freshness warnings, and chart controls. A missing or
    invalid snapshot produces a message directing the operator to refresh data;
    otherwise no network requests occur during page construction. The chart's
    callbacks are registered separately by :func:`register_history_callback`.

    Parameters
    ----------
    source : str
        One of ``users``, ``classifications`` or ``talks`` in ``PAGE_CONFIG``.

    Returns
    -------
    dash.development.base_component.Component
        Dash page layout including summary cards or a missing-snapshot notice.

    Raises
    ------
    KeyError
        If ``source`` is not a registered activity page.

    See Also
    --------
    :func:`zooniverse_dashboard.snapshots.load_snapshot`
        Validate and load a saved activity history.
    """
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
    cards: list[Component] = []
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
                        "and launch positions indicate the day within each period. "
                        "The selected date range is shared across charts in this browser tab; "
                        "changing the Visible time window preset resets it.",
                        className="muted",
                    ),
                ],
                className="section-card",
            ),
        ]
    )


def _display_date(day: date) -> str:
    """Format a calendar day consistently in plot hover text.

    Parameters
    ----------
    day : datetime.date
        Calendar day already interpreted as UTC by the caller.

    Returns
    -------
    str
        English abbreviated weekday, day, month and year, such as
        ``Mon 25 May 2026``.
    """
    return day.strftime("%a %d %b %Y")


def _launch_hover(day: date, count: int, names: tuple[str, ...]) -> str:
    """Build HTML-safe hover text for one day's recorded project launches.

    Only up to eight available public titles are displayed. Remaining public
    titles are counted, and a notice is appended if the total project count is
    larger than the available public-title count. Titles are HTML-escaped to
    avoid interpreting upstream project names as Plotly markup.

    Parameters
    ----------
    day : datetime.date
        Recorded UTC launch date.
    count : int
        Number of approved launches recorded on that date, including projects
        without public names.
    names : tuple[str, ...]
        Public project display names from the saved launch snapshot.

    Returns
    -------
    str
        HTML-escaped tooltip body suitable for Plotly ``customdata``.

    See Also
    --------
    :class:`zooniverse_dashboard.project_launches.LaunchSnapshot`
        Snapshot supplying counts and optional public titles.
    """
    label = f"{count} project{'s' if count != 1 else ''} launched"
    titles = "".join(f"<br>• {escape(name, quote=True)}" for name in names[:8])
    if len(names) > 8:
        titles += f"<br>… and {len(names) - 8} more public titles"
    if len(names) < count:
        titles += "<br>Some project titles are private or unavailable"
    return f"{_display_date(day)} UTC<br>{label}{titles}"


_PERIOD_BAR_WIDTH: float = 0.82


def _next_bucket_start(start: date, interval: str) -> date:
    """Compute the exclusive end of a complete calendar bucket.

    Parameters
    ----------
    start : datetime.date
        First date of the bucket (Monday, first of month, or first of year).
    interval : str
        One of ``weekly``, ``monthly``, or ``yearly``.

    Returns
    -------
    datetime.date
        First day of the following calendar period.

    Raises
    ------
    ValueError
        If ``interval`` is not a supported grouped-bar interval.
    """
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
    """Produce a compact calendar label for a grouped bar axis.

    Parameters
    ----------
    bucket : :class:`zooniverse_dashboard.chart_data.ChartBucket`
        Calendar bucket whose start identifies the displayed period.
    interval : str
        Grouped interval: ``weekly``, ``monthly`` or ``yearly``.

    Returns
    -------
    str
        Week-start date, abbreviated month plus year, or a four-digit year.

    Raises
    ------
    ValueError
        If ``interval`` is not a supported grouped-bar interval.
    """
    if interval == "weekly":
        return bucket.start.strftime("%d %b '%y")
    if interval == "monthly":
        return bucket.start.strftime("%b %Y")
    if interval == "yearly":
        return str(bucket.start.year)
    raise ValueError("Only grouped bars use period labels")


def _period_position(day: date, bucket: ChartBucket, index: int, interval: str) -> float:
    """Map one launch day to a fractional coordinate inside a calendar slot.

    Weekly, monthly and yearly bars occupy equally spaced numeric slots. The
    launch's position within the slot is proportional to elapsed calendar days,
    including leap-year months and years.

    Parameters
    ----------
    day : datetime.date
        Launch date within the supplied bucket.
    bucket : :class:`zooniverse_dashboard.chart_data.ChartBucket`
        Bucket containing the launch day.
    index : int
        Zero-based numeric X-coordinate of the corresponding period.
    interval : str
        Grouped-bar interval.

    Returns
    -------
    float
        Fractional numeric X-coordinate within the bar's allotted slot.

    Raises
    ------
    ValueError
        If the interval is unsupported for grouped bars.
    """
    span = (_next_bucket_start(bucket.start, interval) - bucket.start).days
    elapsed = (day - bucket.start).days
    return index + _PERIOD_BAR_WIDTH * ((elapsed + 0.5) / span - 0.5)


def _period_bar_geometry(bucket: ChartBucket, index: int, interval: str) -> tuple[float, float]:
    """Calculate bar center and proportional observed width in a period slot.

    A partial first or last bucket occupies only the fraction of its month,
    week or year covered by the snapshot; the unobserved portion remains empty.

    Parameters
    ----------
    bucket : :class:`zooniverse_dashboard.chart_data.ChartBucket`
        Calendar bucket with first and last observed days.
    index : int
        Integer slot index on the numeric X-axis.
    interval : str
        Weekly, monthly or yearly period selection.

    Returns
    -------
    tuple[float, float]
        Numeric bar center and width, in period-slot units.

    Raises
    ------
    ValueError
        If ``interval`` is not a grouped-bar interval.
    """
    span = (_next_bucket_start(bucket.start, interval) - bucket.start).days
    first = (bucket.first_observed - bucket.start).days
    observed = (bucket.last_observed - bucket.first_observed).days + 1
    center = index + _PERIOD_BAR_WIDTH * ((first + observed / 2) / span - 0.5)
    return center, _PERIOD_BAR_WIDTH * observed / span


def _period_ticks(
    first: int, last: int, labels: list[str], *, max_ticks: int = 10
) -> tuple[list[int], list[str]]:
    """Select sparse initial tick locations from indexed calendar labels.

    Browser-side navigation can later update tick density as the view changes.
    The returned indexes are clipped to the supplied label sequence.

    Parameters
    ----------
    first : int
        First visible zero-based period index.
    last : int
        Last visible zero-based period index, inclusive.
    labels : list[str]
        All available calendar-period axis labels.
    max_ticks : int, optional
        Maximum target number of initial labels, default 10.

    Returns
    -------
    tuple[list[int], list[str]]
        Selected numeric tick positions and their matching text labels.
    """
    step = max(1, (last - first + 1 + max_ticks - 1) // max_ticks)
    indexes = list(range(max(0, first), min(len(labels) - 1, last) + 1, step))
    return indexes, [labels[index] for index in indexes]


def history_figure(
    snapshot: Snapshot,
    *,
    window: str = "90",
    focus_event: dict[str, Any] | None = None,
    interval: str = "daily",
    plot_type: str = "line",
    launches: LaunchSnapshot | None = None,
) -> go.Figure:
    """Build a Plotly activity chart with optional anomalies and launches.

    Calendar-binned source counts are displayed as daily/weekly/monthly/yearly
    lines or bars. Non-daily bars use evenly spaced numeric calendar positions;
    daily bars and all lines retain an actual UTC date axis. Snapshot-bounded
    initial ranges and period metadata allow ``history_zoom.js`` to restore a
    shared UTC viewport after switching controls or pages. Daily anomaly diamonds
    are clickable; optional violet launch triangles sit on the baseline on every
    activity chart. The Y-axis initially fits the visible counts.

    Parameters
    ----------
    snapshot : :class:`zooniverse_dashboard.snapshots.Snapshot`
        Validated daily count history for Users, Classifications, or Talk.
    window : str, optional
        Visible-window preset (number of days or ``all``), default ``90``.
    focus_event : dict[str, typing.Any] or None, optional
        Optional validated anomaly event with source, date bounds and ID. A
        matching event temporarily focuses its surrounding time region.
    interval : str, optional
        ``daily``, ``weekly``, ``monthly`` or ``yearly``, default ``daily``.
    plot_type : str, optional
        ``line`` or ``bars``, default ``line``.
    launches : :class:`zooniverse_dashboard.project_launches.LaunchSnapshot` or None, optional
        Optional independently validated project-launch overlay. Missing data
        simply omit launch markers.

    Returns
    -------
    plotly.graph_objects.Figure
        Interactive figure with calendar-axis metadata, hover labels, and traces.

    Raises
    ------
    ValueError
        If ``plot_type`` is unsupported, or a supplied interval/window value
        cannot be interpreted by its calendar helpers.

    See Also
    --------
    :func:`zooniverse_dashboard.chart_data.group_daily`
        Aggregate continuous daily source history.
    :func:`zooniverse_dashboard.anomalies.catalog.visible_events`
        Retrieve current catalog events without recalculating them.
    """
    if plot_type not in {"line", "bars"}:
        raise ValueError("Unsupported chart type")
    bins: list[ChartBucket] = group_daily(snapshot.daily, interval)
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
        marker_days: list[tuple[date, int]] = [
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
    # Store UTC period boundaries for browser-side zoom translation. Grouped
    # bars use numeric slots, but the shared viewport is always absolute time.
    # Only calendar metadata is exposed; no additional source records are read.
    zoom_meta = {
        "source": snapshot.source,
        "window": window,
        "interval": interval,
        "plot_type": plot_type,
        "first": snapshot.first.isoformat(),
        "last_exclusive": (snapshot.last + timedelta(days=1)).isoformat(),
        "focused": focus_event is not None,
        "revision": (
            f"{snapshot.source}:{snapshot.last_modified.isoformat()}:"
            f"{window}:{interval}:{plot_type}:"
            f"{focus_event['id'] if focus_event is not None else ''}"
        ),
        "period_bounds": (
            [bucket.start.isoformat() for bucket in bins]
            + [_next_bucket_start(bins[-1].start, interval).isoformat()]
            if indexed_bars and bins
            else None
        ),
    }
    fig.update_layout(
        template="plotly_white",
        margin={"l": 40, "r": 18, "t": 35, "b": 40},
        xaxis=xaxis,
        meta={"history_zoom": zoom_meta, **({"period_labels": labels} if indexed_bars else {})},
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
    """Load the optional project-launch overlay for an activity chart.

    Unreadable, missing or invalid launch snapshots do not block a valid source
    chart from rendering, and no upstream project query is performed.

    Parameters
    ----------
    source : str
        Activity page key; unsupported keys skip launch loading.

    Returns
    -------
    :class:`zooniverse_dashboard.project_launches.LaunchSnapshot` or None
        Parsed launch snapshot if available, otherwise ``None``.

    See Also
    --------
    :func:`zooniverse_dashboard.project_launches.load_launches`
        Load and validate the locally stored launch data.
    """
    if source not in PAGE_CONFIG:
        return None
    try:
        return load_launches()
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def register_history_callback(source: str) -> Callable[..., go.Figure]:
    """Register chart rendering and anomaly navigation for an activity source.

    The figure callback reads only local snapshots and substitutes a placeholder
    if snapshot data cannot be loaded. The click callback validates event IDs
    against the currently visible anomaly catalog before navigating to the detail
    page. Callback registration takes place when a Dash Page module is imported.

    Parameters
    ----------
    source : str
        Activity key used to name graph, route, and chart controls.

    Returns
    -------
    collections.abc.Callable
        Registered Dash figure callback function for this source.

    See Also
    --------
    :func:`zooniverse_dashboard.anomalies.catalog.event_by_id`
        Validate clicked anomaly identifiers against active catalog events.
    """

    @callback(
        Output(f"{source}-graph", "figure"),
        Input(f"{source}-route", "search"),
        Input(f"{source}-window", "value"),
        Input(f"{source}-interval", "value"),
        Input(f"{source}-plot-type", "value"),
    )
    def update(
        search: str | None, window: str, interval: str, plot_type: str
    ) -> go.Figure:
        """Regenerate an activity chart after a route or control change.

        A route's ``episode`` parameter focuses an anomaly only when navigation or
        initial rendering, rather than a user control change, triggered the callback.

        Parameters
        ----------
        search : str or None
            Current URL query component, possibly containing ``episode``.
        window : str
            Visible-time preset selected in the page controls.
        interval : str
            Calendar aggregation interval.
        plot_type : str
            Line or bar chart selection.

        Returns
        -------
        plotly.graph_objects.Figure
            Chart with optional anomaly focus, or an unavailable-snapshot figure.
        """
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
    def open_anomaly(click_data: dict[str, Any] | None) -> Any:
        """Convert a validated anomaly click into a detail-page navigation.

        Launch markers and regular activity traces cannot redirect to arbitrary
        routes because only event identifiers present in the current catalog are used.

        Parameters
        ----------
        click_data : dict[str, typing.Any] or None
            Dash Plotly click payload; may contain multiple point descriptions.

        Returns
        -------
        str or dash.no_update
            URL for the selected anomaly episode, or Dash's no-update sentinel when
            the click did not select a valid episode.
        """
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
