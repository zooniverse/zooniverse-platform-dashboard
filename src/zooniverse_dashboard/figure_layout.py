"""Build shared Plotly axes, viewport metadata, and figure layout settings.

The activity and Projects figure builders deliberately retain their own traces,
hover content, and navigation semantics. This module centralizes only the
presentation contract that they share: calendar-slot axes for grouped bars,
real UTC date axes for lines and daily bars, browser-side zoom metadata, and
common Plotly layout defaults. It performs no I/O or Dash callback registration.

The ``history_zoom`` metadata maps numeric calendar slots back to actual UTC
period boundaries for the browser-side zoom utility. Any change to its keys or
the ``period_labels`` sibling must be coordinated with ``history_zoom.js``.

See Also
--------
:func:`zooniverse_dashboard.ui.history_figure`
    Render activity-specific lines, bars, and optional overlays.
:func:`zooniverse_dashboard.projects_ui.projects_figure`
    Render monthly and yearly launch rates.
:func:`zooniverse_dashboard.chart_geometry.period_ticks`
    Compute appropriately spaced numeric-axis tick labels.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Sequence

import plotly.graph_objects as go

from zooniverse_dashboard.chart_data import ChartBucket, next_bucket_start
from zooniverse_dashboard.chart_geometry import period_ticks


def _range_slider() -> dict[str, Any]:
    """Return an independent copy of the shared range-slider configuration.

    Returns
    -------
    dict[str, typing.Any]
        A Plotly slider that fits its vertical range to the visible data.
    """
    return {"visible": True, "thickness": 0.11, "yaxis": {"rangemode": "auto"}}


def indexed_bar_axis(
    bins: Sequence[ChartBucket],
    labels: Sequence[str],
    *,
    first_index: int,
    last_index: int,
) -> dict[str, Any]:
    """Build an equally spaced numeric calendar axis for grouped bars.

    Parameters
    ----------
    bins : sequence of :class:`zooniverse_dashboard.chart_data.ChartBucket`
        All buckets, including those outside the initial visible window.
    labels : sequence of str
        Short labels for the same buckets, ordered chronologically.
    first_index, last_index : int
        Inclusive indices of the initially displayed period range.

    Returns
    -------
    dict[str, typing.Any]
        Plotly X-axis configuration with sparse ticks and the range slider.
    """
    tickvals, ticktext = period_ticks(first_index, last_index, labels)
    return {
        "type": "linear",
        "tickmode": "array",
        "tickvals": tickvals,
        "ticktext": ticktext,
        "tickangle": -30,
        "automargin": True,
        "range": [first_index - 0.5, last_index + 0.5],
        "minallowed": -0.5,
        "maxallowed": len(bins) - 0.5,
        "rangeslider": _range_slider(),
    }


def utc_date_axis(
    *,
    visible_first: date,
    visible_last_exclusive: date,
    first: date,
    last_exclusive: date,
) -> dict[str, Any]:
    """Build a continuous UTC date axis for activity and Projects figures.

    Parameters
    ----------
    visible_first : datetime.date
        Inclusive initial date-axis boundary, possibly aligned to a bucket.
    visible_last_exclusive : datetime.date
        Exclusive initial right-hand boundary.
    first : datetime.date
        Earliest valid snapshot date.
    last_exclusive : datetime.date
        Exclusive end of the validated snapshot coverage.

    Returns
    -------
    dict[str, typing.Any]
        Plotly date-axis settings with immutable snapshot constraints.
    """
    return {
        "type": "date",
        "hoverformat": "%a %d %b %Y",
        "range": [visible_first.isoformat(), visible_last_exclusive.isoformat()],
        "minallowed": first.isoformat(),
        "maxallowed": last_exclusive.isoformat(),
        "rangeslider": _range_slider(),
    }


def history_zoom_metadata(
    *,
    source: str,
    window: str,
    interval: str,
    plot_type: str,
    first: date,
    last_exclusive: date,
    focused: bool,
    revision: str,
    bins: Sequence[ChartBucket],
    indexed_bars: bool,
) -> dict[str, Any]:
    """Publish the shared absolute-UTC viewport contract used by JavaScript.

    Parameters
    ----------
    source : str
        Page key identifying the plotted data source.
    window, interval, plot_type : str
        Chosen chart controls, retained for browser-side zoom restoration.
    first, last_exclusive : datetime.date
        Inclusive/exclusive boundaries of the validated snapshot history.
    focused : bool
        Whether an anomaly deep link has requested an initial focused range.
    revision : str
        Caller-specific revision identifier; format is intentionally preserved.
    bins : sequence of :class:`zooniverse_dashboard.chart_data.ChartBucket`
        Complete ordered sequence of chart buckets.
    indexed_bars : bool
        Whether numeric X positions require corresponding UTC period bounds.

    Returns
    -------
    dict[str, typing.Any]
        ``layout.meta.history_zoom`` payload for ``history_zoom.js``.
    """
    return {
        "source": source,
        "window": window,
        "interval": interval,
        "plot_type": plot_type,
        "first": first.isoformat(),
        "last_exclusive": last_exclusive.isoformat(),
        "focused": focused,
        "revision": revision,
        "period_bounds": (
            [bucket.start.isoformat() for bucket in bins]
            + [next_bucket_start(bins[-1].start, interval).isoformat()]
            if indexed_bars and bins
            else None
        ),
    }


def apply_history_layout(
    fig: go.Figure,
    *,
    xaxis: dict[str, Any],
    y_title: str,
    y_max: int | float,
    zoom_metadata: dict[str, Any],
    period_labels: Sequence[str] | None,
    hovermode: str,
    uirevision: str,
    horizontal_legend: bool = False,
) -> None:
    """Apply common Plotly layout without changing caller-specific plot behavior.

    Parameters
    ----------
    fig : plotly.graph_objects.Figure
        Figure whose activity- or launch-specific traces are already populated.
    xaxis : dict[str, typing.Any]
        Configuration returned by :func:`indexed_bar_axis` or :func:`utc_date_axis`.
    y_title : str
        Source-specific count label and calendar unit.
    y_max : int or float
        Largest count in the initial viewport, used for initial Y scaling.
    zoom_metadata : dict[str, typing.Any]
        Payload from :func:`history_zoom_metadata`.
    period_labels : sequence of str or None
        Indexed-bar labels; omit for date axes.
    hovermode : str
        Source-specific Plotly hover behavior.
    uirevision : str
        Caller-specific revision string for preserving UI state.
    horizontal_legend : bool, optional
        Retain the activity page's horizontal legend when true.

    Returns
    -------
    None
        Updates ``fig`` in place.
    """
    settings: dict[str, Any] = {
        "template": "plotly_white",
        "margin": {"l": 40, "r": 18, "t": 35, "b": 40},
        "xaxis": xaxis,
        "yaxis": {
            "title": y_title,
            "range": [0, max(1, y_max) * 1.15],
            "rangemode": "tozero",
        },
        "meta": {
            "history_zoom": zoom_metadata,
            **({"period_labels": list(period_labels)} if period_labels is not None else {}),
        },
        "dragmode": "zoom",
        "hovermode": hovermode,
        "uirevision": uirevision,
    }
    if horizontal_legend:
        settings["legend"] = {"orientation": "h", "y": 1.12, "x": 0}
    fig.update_layout(**settings)
