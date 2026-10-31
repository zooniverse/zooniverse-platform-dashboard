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
:mod:`zooniverse_dashboard.figure_layout`
    Shared axes, zoom metadata, and Plotly layout defaults.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from html import escape
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode

import plotly.graph_objects as go
from dash import Input, Output, callback, ctx, no_update
from dash.development.base_component import Component

from zooniverse_dashboard.anomalies.catalog import event_by_id, visible_events
from zooniverse_dashboard.chart_data import ChartBucket, bucket_start, group_daily
from zooniverse_dashboard.chart_geometry import (
    display_date,
    period_bar_geometry,
    period_label,
    period_position,
)
from zooniverse_dashboard.collection_config import effective_history_days
from zooniverse_dashboard.figure_layout import (
    apply_history_layout,
    history_zoom_metadata,
    indexed_bar_axis,
    utc_date_axis,
)
from zooniverse_dashboard.page_components import build_metric_page, missing_snapshot_page
from zooniverse_dashboard.page_specs import PAGE_SPECS
from zooniverse_dashboard.periods import calendar_periods, visible_date_range
from zooniverse_dashboard.project_launches import LaunchSnapshot, load_launches
from zooniverse_dashboard.snapshots import Snapshot, load_snapshot


def layout_for(source: str) -> Component:
    """Render an activity page via its immutable page specification.

    The source adapter remains responsible for loading a continuous daily
    snapshot and calculating calendar summaries. All shared Dash structure is
    rendered by :func:`zooniverse_dashboard.page_components.build_metric_page`.

    Parameters
    ----------
    source : str
        Registered activity source (users, classifications, or talks).

    Returns
    -------
    dash.development.base_component.Component
        Full page or a missing-snapshot notice.

    Raises
    ------
    KeyError
        If the source does not describe an activity page.
    """
    spec = PAGE_SPECS[source]
    if spec.snapshot_kind != "activity":
        raise KeyError(source)
    try:
        snapshot = load_snapshot(source)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return missing_snapshot_page(
            spec,
            "No valid aggregate snapshot yet. Click Refresh data to initialize the "
            "configured history. An oversized or invalid source response is rejected.",
            include_provider=False,
        )
    try:
        configured_days = effective_history_days()
    except ValueError:
        configured_days = None
    return build_metric_page(
        spec,
        snapshot,
        calendar_periods(snapshot),
        configured_days=configured_days,
        lead=(
            "Daily aggregate snapshots, viewable as day/week/month/year totals. "
            "Weeks begin Monday; all calendar periods use UTC. "
            "Current and boundary periods may be partial."
        ),
        eyebrow="HISTORICAL ACTIVITY",
        footnote=(
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
            "changing the Visible time window preset resets it."
        ),
        mismatch_message=(
            "Date-limit setting has changed (or snapshot predates this feature). "
            "Refresh data to apply the selected historical window."
        ),
        stale_prefix="Data are ",
        stale_suffix=(
            " completed UTC day(s) behind. Use Refresh data above to update this source."
        ),
        route=True,
    )


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
    return f"{display_date(day)} UTC<br>{label}{titles}"


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
    spec = PAGE_SPECS[snapshot.source]
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
        [period_bar_geometry(bucket, index, interval) for index, bucket in enumerate(bins)]
        if indexed_bars
        else []
    )
    x = (
        [center for center, _ in bar_geometry]
        if indexed_bars
        else [b.center.isoformat() for b in bins]
    )
    y = [b.count for b in bins]
    hover_periods = [[display_date(b.first_observed), display_date(b.last_observed)] for b in bins]
    unit = {"daily": "day", "weekly": "week", "monthly": "month", "yearly": "year"}[interval]
    name = spec.count_noun.capitalize()
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
        if spec.has_layer("trailing_average", interval=interval, plot_type=plot_type):
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
    if spec.has_layer("anomalies", interval=interval, plot_type=plot_type):
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
                                display_date(date.fromisoformat(v["start"])),
                                display_date(date.fromisoformat(v["end"])),
                                display_date(date.fromisoformat(v["detected"])),
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
    if launches is not None and spec.has_layer(
        "project_launches", interval=interval, plot_type=plot_type
    ):
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
                    marker_x.append(period_position(day, bucket, index, interval))
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
    if spec.has_layer("trailing_average", interval=interval, plot_type=plot_type):
        trailing_in_view = [
            value for (bucket, value) in zip(bins, trailing) if begin <= bucket.start <= end
        ]
        max_in_view = max(max_in_view, max(trailing_in_view, default=0))
    # Indexed bar positions are equal-width categories; the launch positions
    # retain their fractional calendar day *inside* each category. All line
    # plots and daily bar plots continue using the true UTC date axis.
    if indexed_bars:
        labels = [period_label(bucket, interval) for bucket in bins]
        first_index = next(
            (index for index, bucket in enumerate(bins) if bucket.last_observed >= begin), 0
        )
        last_index = next(
            (index for index in range(len(bins) - 1, -1, -1) if bins[index].first_observed <= end),
            len(bins) - 1,
        )
        xaxis = indexed_bar_axis(bins, labels, first_index=first_index, last_index=last_index)
    else:
        xaxis = utc_date_axis(
            visible_first=displayed_first,
            visible_last_exclusive=displayed_last + timedelta(days=1),
            first=snapshot.first,
            last_exclusive=snapshot.last + timedelta(days=1),
        )
    # Store UTC period boundaries for browser-side zoom translation. Grouped
    # bars use numeric slots, but the shared viewport is always absolute time.
    # Only calendar metadata is exposed; no additional source records are read.
    zoom_meta = history_zoom_metadata(
        source=snapshot.source,
        window=window,
        interval=interval,
        plot_type=plot_type,
        first=snapshot.first,
        last_exclusive=snapshot.last + timedelta(days=1),
        focused=focus_event is not None,
        revision=(
            f"{snapshot.source}:{snapshot.last_modified.isoformat()}:"
            f"{window}:{interval}:{plot_type}:"
            f"{focus_event['id'] if focus_event is not None else ''}"
        ),
        bins=bins,
        indexed_bars=indexed_bars,
    )
    apply_history_layout(
        fig,
        xaxis=xaxis,
        y_title=f"{name} per {unit}",
        y_max=max_in_view,
        zoom_metadata=zoom_meta,
        period_labels=labels if indexed_bars else None,
        hovermode="closest" if indexed_bars else "x unified",
        uirevision=(
            f"{snapshot.source}-{snapshot.last_modified.isoformat()}-{window}"
            f"-{interval}-{plot_type}"
            + (f"-focus-{focus_event['id']}" if focus_event is not None else "")
        ),
        horizontal_legend=True,
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
    if source not in PAGE_SPECS or PAGE_SPECS[source].snapshot_kind != "activity":
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
    def update(search: str | None, window: str, interval: str, plot_type: str) -> go.Figure:
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
