"""Render approved project-launch history using saved aggregate JSON.

The Projects page reads :class:`zooniverse_dashboard.project_launches.LaunchSnapshot`
from disk and summarizes launches by calendar month or year. It shows zero-count
periods, retains partial-period boundaries, and never extrapolates an
incomplete current period. Monthly bars are the default, with an optional
line chart and year aggregation. No new Panoptes queries or anomaly model fits
occur during page rendering or chart interaction.

Like the activity charts, Projects publishes ``layout.meta.history_zoom``
metadata so the client can translate between numeric calendar slots and UTC
boundaries when the user navigates across pages. Figures themselves do not
show project-launch triangles, because the plotted values already represent
project launches.

See Also
--------
:func:`zooniverse_dashboard.projects_data.launch_buckets`
    Build monthly or yearly launch aggregates.
:func:`zooniverse_dashboard.projects_data.project_metrics`
    Calculate period summary cards.
:func:`zooniverse_dashboard.ui.history_figure`
    Activity chart sharing the same browser viewport.
:mod:`zooniverse_dashboard.figure_layout`
    Shared axes and viewport metadata used by both figure builders.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Callable

import plotly.graph_objects as go
from dash import Input, Output, callback
from dash.development.base_component import Component

from zooniverse_dashboard.chart_geometry import (
    display_date,
    period_bar_geometry,
    period_label,
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
from zooniverse_dashboard.project_launches import LaunchSnapshot, load_launches
from zooniverse_dashboard.projects_data import (
    INTERVALS,
    WINDOWS,
    launch_buckets,
    project_metrics,
    viewport_start,
)


def projects_figure(
    snapshot: LaunchSnapshot,
    *,
    window: str = "36",
    interval: str = "monthly",
    plot_type: str = "bars",
) -> go.Figure:
    """Build a monthly or yearly Plotly chart of approved project launches.

    Project-launch snapshots contain sparse daily launch counts. This figure
    uses :func:`zooniverse_dashboard.projects_data.launch_buckets` to include
    zero-launch periods and preserve observed boundary widths. Bars use equal
    calendar slots with numeric positions; lines use a UTC date axis. Both supply
    real UTC period boundaries in ``layout.meta.history_zoom`` for cross-page
    zoom restoration. The chart itself needs no launch overlay or anomaly traces.

    Parameters
    ----------
    snapshot : :class:`zooniverse_dashboard.project_launches.LaunchSnapshot`
        Validated launch history with recorded UTC dates and counts.
    window : str, optional
        View preset (``12``, ``36``, ``60`` or ``all``), default ``36`` months.
    interval : str, optional
        ``monthly`` or ``yearly``, default ``monthly``.
    plot_type : str, optional
        ``bars`` or ``line``, default ``bars``.

    Returns
    -------
    plotly.graph_objects.Figure
        Figure including hover periods, partial-period geometry and zoom metadata.

    Raises
    ------
    ValueError
        If any graph configuration is unsupported.

    See Also
    --------
    :func:`zooniverse_dashboard.projects_data.viewport_start`
        Find the initial calendar cutoff.
    :func:`zooniverse_dashboard.ui.history_figure`
        Related activity graph participating in shared UTC zoom.
    """
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
        [display_date(bucket.first_observed), display_date(bucket.last_observed)] for bucket in bins
    ]
    fig = go.Figure()
    labels = [period_label(bucket, interval) for bucket in bins]
    if plot_type == "bars":
        geometry = [period_bar_geometry(bucket, i, interval) for i, bucket in enumerate(bins)]
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
        xaxis = indexed_bar_axis(bins, labels, first_index=first_index, last_index=last_index)
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
        xaxis = utc_date_axis(
            visible_first=begin,
            visible_last_exclusive=snapshot.last + timedelta(days=1),
            first=snapshot.first,
            last_exclusive=snapshot.last + timedelta(days=1),
        )
    meta = history_zoom_metadata(
        source="projects",
        window=window,
        interval=interval,
        plot_type=plot_type,
        first=snapshot.first,
        last_exclusive=snapshot.last + timedelta(days=1),
        focused=False,
        revision=f"projects:{snapshot.last_modified.isoformat()}:{window}:{interval}:{plot_type}",
        bins=bins,
        indexed_bars=plot_type == "bars",
    )
    apply_history_layout(
        fig,
        xaxis=xaxis,
        y_title=f"Projects launched per {'month' if interval == 'monthly' else 'year'}",
        y_max=peak,
        zoom_metadata=meta,
        period_labels=labels if plot_type == "bars" else None,
        hovermode="closest" if plot_type == "bars" else "x unified",
        uirevision=meta["revision"],
    )
    return fig


def projects_layout() -> Component:
    """Render the Projects page with the same configurable page skeleton.

    Unlike the activity adapters, this page reads sparse approved launches
    and computes five launch-specific summary metrics. It shares layout and
    controls but retains its independent figure and snapshot semantics.

    Returns
    -------
    dash.development.base_component.Component
        Projects page or a missing-launch-snapshot notice.
    """
    spec = PAGE_SPECS["projects"]
    try:
        snapshot = load_launches()
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return missing_snapshot_page(
            spec,
            "No valid project launch snapshot yet. Click Refresh data to collect "
            "approved launch dates from Panoptes.",
            include_provider=True,
        )
    try:
        configured_days = effective_history_days()
    except ValueError:
        configured_days = None
    return build_metric_page(
        spec,
        snapshot,
        project_metrics(snapshot),
        configured_days=configured_days,
        lead=(
            "Approved Zooniverse project launches per calendar month or year (UTC). "
            "Counts reflect recorded launch dates, not project creation dates. "
            "Current and boundary periods may be partial."
        ),
        eyebrow="PROJECT LAUNCHES",
        footnote=(
            "Zoom, pan and use the range slider to inspect the launch history. "
            "The selected UTC time interval is shared with Users, "
            "Classifications and Talk in this browser tab. "
            "Periods with no launches appear as zero; boundary periods are "
            "not extrapolated to complete months or years."
        ),
        mismatch_message="Date-limit setting has changed. Refresh data to apply the new window.",
        stale_prefix="Launch data are ",
        stale_suffix=" completed UTC day(s) behind. Use Refresh data above to update them.",
        uppercase_provider=True,
    )


def register_projects_callback() -> Callable[..., go.Figure]:
    """Register the snapshot-only callback for Projects chart controls.

    Each control change loads the locally saved launch snapshot and constructs a
    new figure. Invalid, unavailable or corrupt snapshots yield an explanatory
    Plotly annotation; no remote collection is triggered.

    Returns
    -------
    collections.abc.Callable
        Registered callback accepting window, interval and plot type.
    """

    @callback(
        Output("projects-graph", "figure"),
        Input("projects-window", "value"),
        Input("projects-interval", "value"),
        Input("projects-plot-type", "value"),
    )
    def update(window: str, interval: str, plot_type: str) -> go.Figure:
        """Regenerate the Projects figure from local launch data.

        Parameters
        ----------
        window : str
            Visible-window preset (months or all history).
        interval : str
            Monthly or yearly aggregation interval.
        plot_type : str
            Line or bar visualization selection.

        Returns
        -------
        plotly.graph_objects.Figure
            Project-launch plot or an unavailable-snapshot annotation.
        """
        try:
            return projects_figure(
                load_launches(), window=window, interval=interval, plot_type=plot_type
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            fig = go.Figure()
            fig.add_annotation(text="Project launch snapshot unavailable", showarrow=False)
            return fig

    return update
