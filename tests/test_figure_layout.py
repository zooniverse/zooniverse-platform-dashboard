"""Pure regression tests for common Plotly axes and shared UTC zoom metadata."""

from datetime import date

import plotly.graph_objects as go
import pytest

from zooniverse_dashboard.chart_data import ChartBucket
from zooniverse_dashboard.chart_geometry import period_label
from zooniverse_dashboard.figure_layout import (
    apply_history_layout,
    history_zoom_metadata,
    indexed_bar_axis,
    utc_date_axis,
)


def _leap_month_buckets():
    """Provide partial February and March buckets across a leap-year boundary."""
    return (
        ChartBucket(date(2024, 2, 1), date(2024, 2, 10), date(2024, 2, 29), 7),
        ChartBucket(date(2024, 3, 1), date(2024, 3, 1), date(2024, 3, 15), 3),
    )


def test_indexed_bar_axis_includes_all_calendar_slots_and_sparse_ticks():
    """The indexed axis uses original slot limits even for partial months."""
    bins = _leap_month_buckets()
    labels = [period_label(bucket, "monthly") for bucket in bins]
    axis = indexed_bar_axis(bins, labels, first_index=0, last_index=1)
    assert axis["type"] == "linear"
    assert axis["tickmode"] == "array"
    assert axis["tickvals"] == [0, 1]
    assert axis["ticktext"] == labels
    assert axis["range"] == [-0.5, 1.5]
    assert axis["minallowed"] == -0.5
    assert axis["maxallowed"] == 1.5
    assert axis["rangeslider"]["yaxis"]["rangemode"] == "auto"


def test_date_axis_preserves_exclusive_utc_bounds_and_independent_slider():
    """Line charts use ISO date bounds and get a fresh slider mapping."""
    kwargs = {
        "visible_first": date(2024, 2, 10),
        "visible_last_exclusive": date(2024, 3, 16),
        "first": date(2024, 2, 10),
        "last_exclusive": date(2024, 3, 16),
    }
    axis = utc_date_axis(**kwargs)
    assert axis["type"] == "date"
    assert axis["hoverformat"] == "%a %d %b %Y"
    assert axis["range"] == ["2024-02-10", "2024-03-16"]
    assert axis["minallowed"] == "2024-02-10"
    assert axis["maxallowed"] == "2024-03-16"
    axis["rangeslider"]["yaxis"]["rangemode"] = "fixed"
    assert utc_date_axis(**kwargs)["rangeslider"]["yaxis"]["rangemode"] == "auto"


@pytest.mark.parametrize(
    "interval,exclusive", [("monthly", "2024-04-01"), ("yearly", "2025-01-01")]
)
def test_zoom_metadata_contains_real_period_bounds(interval, exclusive):
    """The browser can recover UTC boundaries from numeric bar slots."""
    bins = (
        _leap_month_buckets()
        if interval == "monthly"
        else (ChartBucket(date(2024, 1, 1), date(2024, 2, 10), date(2024, 3, 15), 10),)
    )
    meta = history_zoom_metadata(
        source="projects",
        window="all",
        interval=interval,
        plot_type="bars",
        first=date(2024, 2, 10),
        last_exclusive=date(2024, 3, 16),
        focused=False,
        revision="projects:revision",
        bins=bins,
        indexed_bars=True,
    )
    assert meta["period_bounds"][-1] == exclusive
    assert len(meta["period_bounds"]) == len(bins) + 1
    assert meta["first"] == "2024-02-10"
    assert meta["last_exclusive"] == "2024-03-16"
    assert meta["revision"] == "projects:revision"
    assert meta["focused"] is False
    assert (
        history_zoom_metadata(
            source="projects",
            window="all",
            interval=interval,
            plot_type="line",
            first=date(2024, 2, 10),
            last_exclusive=date(2024, 3, 16),
            focused=False,
            revision="projects:revision",
            bins=bins,
            indexed_bars=False,
        )["period_bounds"]
        is None
    )


def test_empty_indexed_history_keeps_optional_period_bounds_empty():
    """Unpopulated numeric axes never invent a calendar boundary."""
    meta = history_zoom_metadata(
        source="users",
        window="all",
        interval="monthly",
        plot_type="bars",
        first=date(2024, 2, 1),
        last_exclusive=date(2024, 3, 1),
        focused=False,
        revision="empty",
        bins=(),
        indexed_bars=True,
    )
    assert meta["period_bounds"] is None


@pytest.mark.parametrize("horizontal_legend", [False, True])
def test_common_layout_preserves_caller_specific_hover_and_legend(horizontal_legend):
    """Projects omit the legend, whereas activity charts retain theirs."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=["2024-02-10"], y=[7], name="Example"))
    zoom = {"source": "users", "revision": "abc"}
    apply_history_layout(
        fig,
        xaxis=utc_date_axis(
            visible_first=date(2024, 2, 10),
            visible_last_exclusive=date(2024, 2, 11),
            first=date(2024, 2, 10),
            last_exclusive=date(2024, 2, 11),
        ),
        y_title="Registrations per day",
        y_max=7,
        zoom_metadata=zoom,
        period_labels=None,
        hovermode="x unified",
        uirevision="page-specific",
        horizontal_legend=horizontal_legend,
    )
    assert fig.layout.meta == {"history_zoom": zoom}
    assert fig.layout.yaxis.range == (0, pytest.approx(8.05))
    assert fig.layout.yaxis.title.text == "Registrations per day"
    assert fig.layout.hovermode == "x unified"
    assert fig.layout.uirevision == "page-specific"
    assert fig.layout.dragmode == "zoom"
    assert (fig.layout.legend.orientation == "h") == horizontal_legend
    assert fig.data[0].name == "Example"
