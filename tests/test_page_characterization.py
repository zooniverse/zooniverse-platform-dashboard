"""Integration contracts for the shared page renderer and chart variants.

Run the complete aggregation/style matrix once for the common activity figure
builder; use targeted checks for source isolation. Pure calendar calculations,
zoom metadata and layout helpers have their own dedicated test modules.
All snapshots are synthetic; no upstream I/O occurs.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

pytest.importorskip("dash")

from dash.development.base_component import Component  # noqa: E402

from zooniverse_dashboard import projects_ui, ui  # noqa: E402
from zooniverse_dashboard.page_specs import PAGE_SPECS  # noqa: E402
from zooniverse_dashboard.project_launches import LaunchSnapshot  # noqa: E402
from zooniverse_dashboard.snapshots import Snapshot  # noqa: E402


def _activity(source):
    first = date(2024, 1, 30)
    last = date(2024, 4, 3)
    rows = tuple(
        (first + timedelta(days=day), day % 7 + 1) for day in range((last - first).days + 1)
    )
    return Snapshot(
        source,
        first,
        last,
        last + timedelta(days=1),
        datetime(2024, 4, 4, tzinfo=timezone.utc),
        rows,
        True,
        0,
    )


def _launches():
    first = date(2024, 1, 30)
    last = date(2025, 2, 5)
    return LaunchSnapshot(
        first,
        last,
        last + timedelta(days=1),
        datetime(2025, 2, 6, tzinfo=timezone.utc),
        0,
        ((date(2024, 2, 5), 2), (date(2025, 2, 5), 1)),
        ((date(2024, 2, 5), ("<Galaxy & Stars>",)),),
    )


def _walk(component):
    if isinstance(component, Component):
        yield component
        yield from _walk(getattr(component, "children", None))
    elif isinstance(component, (tuple, list)):
        for child in component:
            yield from _walk(child)


def _component_with_id(layout, key):
    matches = [part for part in _walk(layout) if getattr(part, "id", None) == key]
    assert len(matches) == 1, key
    return matches[0]


def _metric_labels(layout):
    return [
        part.children
        for part in _walk(layout)
        if getattr(part, "className", None) == "metric-title"
    ]


def _options(component):
    return [(item["value"], item["label"]) for item in component.options]


@pytest.mark.parametrize("source", ("users", "classifications", "talks"))
def test_activity_page_controls_cards_and_graph_are_baselined(source, monkeypatch):
    monkeypatch.setattr(ui, "load_snapshot", lambda key: _activity(key))
    monkeypatch.setattr(ui, "effective_history_days", lambda: 0)
    page = PAGE_SPECS[source]
    layout = ui.layout_for(source)

    assert _metric_labels(layout) == [card.label for card in page.cards]
    for suffix, expected, default in (
        ("window", page.windows, page.default_window),
        ("interval", page.intervals, page.default_interval),
        ("plot-type", page.plot_types, page.default_plot_type),
    ):
        control = _component_with_id(layout, f"{source}-{suffix}")
        assert _options(control) == [(item.value, item.label) for item in expected]
        assert control.value == default
    graph = _component_with_id(layout, f"{source}-graph")
    assert graph.config["responsive"] is True
    assert graph.config["scrollZoom"] is False
    assert graph.style["height"] == "500px"
    assert _component_with_id(layout, f"{source}-route") is not None


def test_projects_page_controls_and_cards_are_baselined(monkeypatch):
    monkeypatch.setattr(projects_ui, "load_launches", _launches)
    monkeypatch.setattr(projects_ui, "effective_history_days", lambda: 0)
    spec = PAGE_SPECS["projects"]
    layout = projects_ui.projects_layout()
    assert _metric_labels(layout) == [card.label for card in spec.cards]
    for suffix, expected, default in (
        ("window", spec.windows, spec.default_window),
        ("interval", spec.intervals, spec.default_interval),
        ("plot-type", spec.plot_types, spec.default_plot_type),
    ):
        control = _component_with_id(layout, f"projects-{suffix}")
        assert _options(control) == [(option.value, option.label) for option in expected]
        assert control.value == default
    assert _component_with_id(layout, "projects-graph") is not None


@pytest.mark.parametrize("interval", ("daily", "weekly", "monthly", "yearly"))
@pytest.mark.parametrize("plot_type", ("line", "bars"))
def test_activity_figure_traces_and_shared_zoom_metadata(interval, plot_type, monkeypatch):
    source = "users"
    event = {
        "id": "users-episode",
        "source": source,
        "direction": "spike",
        "detected": "2024-02-05",
        "start": "2024-02-05",
        "end": "2024-02-06",
    }
    monkeypatch.setattr(ui, "visible_events", lambda: ([event], {}))
    snapshot = _activity(source)
    fig = ui.history_figure(
        snapshot,
        window="all",
        interval=interval,
        plot_type=plot_type,
        launches=_launches(),
    )
    spec = PAGE_SPECS[source]
    meta = fig.layout.meta["history_zoom"]
    assert meta["source"] == source
    assert meta["window"] == "all"
    assert meta["interval"] == interval
    assert meta["plot_type"] == plot_type
    assert (meta["first"], meta["last_exclusive"]) == ("2024-01-30", "2024-04-04")
    assert meta["focused"] is False
    assert fig.layout.dragmode == "zoom"
    assert fig.layout.xaxis.rangeslider.visible is True
    assert fig.data[0].type == ("bar" if plot_type == "bars" else "scatter")
    assert sum(fig.data[0].y) == sum(count for _, count in snapshot.daily)
    assert fig.data[0].customdata[0][0] == "Tue 30 Jan 2024"

    indexed = plot_type == "bars" and interval != "daily"
    assert fig.layout.xaxis.type == ("linear" if indexed else "date")
    if indexed:
        assert len(meta["period_bounds"]) == len(fig.data[0].x) + 1
        assert len(fig.layout.meta["period_labels"]) == len(fig.data[0].x)
    else:
        assert meta["period_bounds"] is None
    assert any(trace.name == "7-day trailing average" for trace in fig.data) == (
        spec.has_layer("trailing_average", interval=interval, plot_type=plot_type)
    )
    diamonds = [trace for trace in fig.data if trace.name == "Unusual increase episode"]
    assert len(diamonds) == int(spec.has_layer("anomalies", interval=interval, plot_type=plot_type))
    if diamonds:
        assert diamonds[0].customdata[0][0] == event["id"]
        assert diamonds[0].marker.symbol == "diamond"
    launches = [trace for trace in fig.data if trace.name == "Project launched"]
    assert len(launches) == 1
    marker = launches[0]
    assert marker.marker.symbol == "triangle-up"
    assert marker.marker.color == "#c026d3"
    assert marker.marker.size == 9
    assert marker.marker.line.width == 0
    assert set(marker.y) == {0}
    assert "&lt;Galaxy &amp; Stars&gt;" in marker.customdata[0]
    assert "Mon 05 Feb 2024" in marker.customdata[0]
    assert isinstance(marker.x[0], float if indexed else str)


@pytest.mark.parametrize("source", ("classifications", "talks"))
def test_activity_sources_keep_their_own_counts_events_and_launch_overlay(source, monkeypatch):
    """Sources share chart rendering, but anomaly episodes must not cross sources."""
    own_event = {
        "id": f"{source}-episode",
        "source": source,
        "direction": "spike",
        "detected": "2024-02-05",
        "start": "2024-02-05",
        "end": "2024-02-06",
    }
    other_event = {**own_event, "id": "users-episode", "source": "users"}
    monkeypatch.setattr(ui, "visible_events", lambda: ([other_event, own_event], {}))
    snapshot = _activity(source)
    fig = ui.history_figure(
        snapshot, interval="daily", plot_type="line", window="all", launches=_launches()
    )

    assert fig.layout.meta["history_zoom"]["source"] == source
    assert sum(fig.data[0].y) == sum(value for _, value in snapshot.daily)
    assert fig.layout.yaxis.title.text == f"{PAGE_SPECS[source].count_noun.capitalize()} per day"
    spikes = [trace for trace in fig.data if trace.name == "Unusual increase episode"]
    assert len(spikes) == 1
    assert [row[0] for row in spikes[0].customdata] == [own_event["id"]]
    assert len([trace for trace in fig.data if trace.name == "Project launched"]) == 1
    assert len([trace for trace in fig.data if trace.name == "7-day trailing average"]) == 1


@pytest.mark.parametrize("interval", ("monthly", "yearly"))
@pytest.mark.parametrize("plot_type", ("line", "bars"))
def test_projects_figure_is_launch_only_and_shares_calendar_zoom(interval, plot_type):
    fig = projects_ui.projects_figure(
        _launches(), window="all", interval=interval, plot_type=plot_type
    )
    assert len(fig.data) == 1
    assert fig.data[0].name == "Approved launches"
    assert fig.data[0].type == ("bar" if plot_type == "bars" else "scatter")
    assert sum(fig.data[0].y) == 3
    meta = fig.layout.meta["history_zoom"]
    assert meta["source"] == "projects"
    assert meta["interval"] == interval
    assert meta["plot_type"] == plot_type
    assert meta["first"] == "2024-01-30"
    if plot_type == "bars":
        assert fig.layout.xaxis.type == "linear"
        assert len(meta["period_bounds"]) == len(fig.data[0].x) + 1
        if interval == "monthly":
            assert 0 in fig.data[0].y  # Empty months remain explicit.
    else:
        assert fig.layout.xaxis.type == "date"
        assert meta["period_bounds"] is None


def test_missing_snapshots_display_notices_without_remote_fetch(monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("invalid saved snapshot")

    monkeypatch.setattr(ui, "load_snapshot", fail)
    monkeypatch.setattr(projects_ui, "load_launches", fail)
    for source in ("users", "classifications", "talks"):
        page = ui.layout_for(source)
        assert any(
            "No valid aggregate snapshot" in part.children
            for part in _walk(page)
            if isinstance(getattr(part, "children", None), str)
        )
    projects = projects_ui.projects_layout()
    assert any(
        "No valid project launch snapshot" in part.children
        for part in _walk(projects)
        if isinstance(getattr(part, "children", None), str)
    )
