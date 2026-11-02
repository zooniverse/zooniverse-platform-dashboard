"""Project-launch overlay safety, storage, independent refresh and graph positioning."""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from zooniverse_dashboard import project_launches, refresh
from zooniverse_dashboard.project_launches import (
    LAUNCHES_BOUNDED_SQL,
    LAUNCHES_SQL,
    load_launches,
    publish_launches,
)

NOW = datetime(2026, 10, 28, 15, tzinfo=timezone.utc)
END = date(2026, 10, 27)


def test_only_approved_aggregate_launches_are_selected():
    for query in (LAUNCHES_SQL, LAUNCHES_BOUNDED_SQL):
        sql = str(query).lower()
        assert "count(*)" in sql and "group by launch_date::date" in sql
        assert "display_name" in sql and "private is false" in sql
        assert "array_agg" in sql  # Grouped, not one SQL result row per project.
        assert "launch_approved is true" in sql
        assert "launch_date < :end_exclusive" in sql
        assert all(
            token not in sql
            for token in ("select *", "project_id", "owner", "title", "subject", "email")
        )
    assert ":start_inclusive" in str(LAUNCHES_BOUNDED_SQL)
    assert ":start_inclusive" not in str(LAUNCHES_SQL)


def test_sparse_roundtrip_counts_and_coverage(tmp_path):
    counts = {date(2026, 10, 13): 1, END: 3}
    snap = publish_launches(counts, through=END, history_days=28, now=NOW, root=tmp_path)
    assert snap == load_launches(root=tmp_path)
    assert snap.first == date(2026, 9, 30)
    assert snap.last == END
    assert snap.as_of == date(2026, 10, 28)
    assert snap.counts() == counts
    doc = json.loads((tmp_path / "project_launches.json").read_text())
    assert doc["format"] == project_launches.FORMAT
    assert doc["launches"] == [["2026-10-13", 1, []], ["2026-10-27", 3, []]]
    assert not any(field in doc for field in ("users", "owner", "id", "project"))
    assert (tmp_path / "project_launches.json").stat().st_mode & 0o777 == 0o600


def test_no_launches_in_window_is_valid(tmp_path):
    snap = publish_launches({}, through=END, history_days=7, now=NOW, root=tmp_path)
    assert snap.launches == ()
    assert snap.first == date(2026, 10, 21)
    assert load_launches(root=tmp_path) == snap
    all_time = publish_launches({}, through=END, now=NOW, root=tmp_path)
    assert all_time.first == END
    assert all_time.history_days == 0


def test_all_time_first_day_follows_earliest_launch(tmp_path):
    counts = {date(2010, 2, 22): 4, END: 1}
    snap = publish_launches(counts, through=END, now=NOW, root=tmp_path)
    assert snap.first == date(2010, 2, 22)
    assert len(snap.launches) == 2  # Only launch days, not thousands of zero buckets.


@pytest.mark.parametrize(
    "invalid",
    [
        {date(2026, 10, 20): 0},
        {date(2026, 10, 20): -1},
        {date(2026, 10, 20): True},
        {date(2026, 10, 28): 1},
        {datetime(2026, 10, 20): 1},
    ],
)
def test_rejects_bad_aggregate(invalid, tmp_path):
    with pytest.raises((ValueError, TypeError)):
        publish_launches(invalid, through=END, history_days=28, root=tmp_path, now=NOW)
    assert not (tmp_path / "project_launches.json").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda doc: doc.update(launches=[["2026-10-27", 3], ["2026-10-13", 1]]),
        lambda doc: doc.update(launches=[["2026-10-13", 1], ["2026-10-13", 1]]),
        lambda doc: doc.update(launches=[["2026-10-13", 0]]),
        lambda doc: doc.update(launches=[["2026-10-29", 2]]),
        lambda doc: doc.update(last="2026-10-26"),
        lambda doc: doc.update(history_days=7),
        lambda doc: doc.update(extra="private"),
    ],
)
def test_rejects_corrupt_file_and_does_not_overwrite(tmp_path, mutation):
    publish_launches({date(2026, 10, 13): 1}, through=END, history_days=28, root=tmp_path, now=NOW)
    path = tmp_path / "project_launches.json"
    doc = json.loads(path.read_text())
    mutation(doc)
    path.write_text(json.dumps(doc))
    before = path.read_bytes()
    with pytest.raises((ValueError, TypeError)):
        load_launches(root=tmp_path)
    with pytest.raises((ValueError, TypeError)):
        publish_launches({END: 1}, through=END, root=tmp_path, now=NOW)
    assert path.read_bytes() == before


def test_symlink_is_never_loaded_or_overwritten(tmp_path):
    target = tmp_path / "secret"
    target.write_text("private")
    (tmp_path / "project_launches.json").symlink_to(target)
    with pytest.raises(ValueError):
        load_launches(root=tmp_path)
    with pytest.raises(ValueError):
        publish_launches({END: 1}, through=END, now=NOW, root=tmp_path)
    assert target.read_text() == "private"


def test_fetch_returns_only_daily_counts_and_validates_rows(monkeypatch):
    calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def begin(self):
            return self

        def exec_driver_sql(self, sql):
            calls.append(sql)

        def execute(self, query, params):
            calls.append((query, params))
            return self

        def all(self):
            return [(date(2026, 10, 13), 2, ["Galaxy Zoo", "Planet Hunters"])]

    class Engine:
        dialect = type("Dialect", (), {"name": "postgresql"})()

        def connect(self):
            return Connection()

        def dispose(self):
            calls.append("disposed")

    monkeypatch.setattr(project_launches, "panoptes_url", lambda: "postgresql://fake")
    monkeypatch.setattr(project_launches, "create_engine", lambda *args, **kw: Engine())
    result = project_launches.fetch_project_launches(through=END, history_days=28)
    assert result.counts == {date(2026, 10, 13): 2}
    assert result.names == {date(2026, 10, 13): ("Galaxy Zoo", "Planet Hunters")}
    assert "SET TRANSACTION READ ONLY" in calls
    assert "SET LOCAL TIME ZONE 'UTC'" in calls
    query, params = next(entry for entry in calls if isinstance(entry, tuple))
    assert query is LAUNCHES_BOUNDED_SQL
    assert params["start_inclusive"] == datetime(2026, 9, 30)
    assert params["end_exclusive"] == datetime(2026, 10, 28)
    assert calls[-1] == "disposed"


def test_successful_launch_refresh_publishes_public_titles(monkeypatch, tmp_path):
    monkeypatch.setattr(
        refresh,
        "fetch_project_launches",
        lambda **_kw: project_launches.LaunchAggregates({END: 2}, {END: ("Galaxy Zoo",)}),
    )
    snapshot = refresh.refresh_launches(through=END, root=tmp_path, now=NOW)
    assert snapshot.counts() == {END: 2}
    assert snapshot.names_for(END) == ("Galaxy Zoo",)
    assert load_launches(root=tmp_path) == snapshot


def test_failed_launch_stream_preserves_snapshot(monkeypatch, tmp_path):
    publish_launches({END: 1}, through=END, root=tmp_path, now=NOW)
    path = tmp_path / "project_launches.json"
    original = path.read_bytes()
    monkeypatch.setattr(refresh, "refresh_source", lambda source, **kwargs: None)
    monkeypatch.setattr(
        refresh,
        "fetch_project_launches",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("postgresql://secret:password@host")),
    )
    result = refresh.refresh_all(root=tmp_path, now=NOW)
    assert result["state"] == "partial_failure"
    assert result["streams"]["project_launches"] == "failed"
    assert all(result["streams"][name] == "ok" for name in ("users", "classifications", "talks"))
    assert path.read_bytes() == original
    assert "password" not in (tmp_path / "refresh_status.json").read_text()


def test_three_stream_status_is_rejected(tmp_path):
    status = {
        "format": refresh.STATUS_FORMAT,
        "started_at_utc": NOW.isoformat(),
        "finished_at_utc": NOW.isoformat(),
        "state": "ok",
        "streams": {k: "ok" for k in ("users", "classifications", "talks")},
    }
    (tmp_path / "refresh_status.json").write_text(json.dumps(status))
    with pytest.raises(ValueError, match="status"):
        refresh.load_status(tmp_path)


def test_markers_on_every_activity_page_and_all_intervals(monkeypatch, tmp_path):
    pytest.importorskip("dash")
    from zooniverse_dashboard import ui
    from zooniverse_dashboard.snapshots import publish_snapshot

    monkeypatch.setattr(ui, "visible_events", lambda: ([], {}))
    first = date(2026, 10, 1)
    counts = {first + timedelta(days=i): 100 for i in range(27)}
    launches = publish_launches(
        {date(2026, 10, 13): 1, date(2026, 10, 27): 3},
        names={
            date(2026, 10, 13): ["Planet Hunters"],
            date(2026, 10, 27): ["Galaxy Zoo", "Test <img src=x onerror=alert(1)>"],
        },
        through=END,
        root=tmp_path,
        now=NOW,
    )
    for source in ("users", "classifications", "talks"):
        snapshot = publish_snapshot(source, counts, through=END, root=tmp_path, now=NOW)
        for grouping in ("daily", "weekly", "monthly", "yearly"):
            for style in ("line", "bars"):
                fig = ui.history_figure(
                    snapshot, interval=grouping, plot_type=style, launches=launches
                )
                marker = next(trace for trace in fig.data if trace.name == "Project launched")
                if style == "bars" and grouping != "daily":
                    assert fig.layout.xaxis.type == "linear"
                    assert fig.layout.hovermode == "closest"
                    assert all(isinstance(position, float) for position in marker.x)
                    assert list(marker.y) == [0, 0]
                    assert fig.layout.meta["period_labels"]
                else:
                    assert list(marker.x) == ["2026-10-13", "2026-10-27"]
                    assert fig.layout.xaxis.type == "date"
                    assert list(marker.y) == [0, 0]
                assert marker.marker.size == 9
                assert marker.marker.color == "#c026d3"
                assert marker.marker.line.width == 0
                assert marker.marker.opacity == 0.9
                assert marker.cliponaxis is False
                assert fig.layout.yaxis.range[0] == 0
                assert fig.layout.yaxis.range[1] == pytest.approx(max(fig.data[0].y) * 1.15)
                assert "Tue 13 Oct 2026" in marker.customdata[0]
                assert "Planet Hunters" in marker.customdata[0]
                assert "3 projects launched" in marker.customdata[1]
                assert "Galaxy Zoo" in marker.customdata[1]
                assert "&lt;img" in marker.customdata[1]
                assert "<img" not in marker.customdata[1]
                assert "private or unavailable" in marker.customdata[1]
                assert marker.hovertemplate == "%{customdata}<extra></extra>"
                if not (style == "bars" and grouping != "daily"):
                    assert fig.layout.xaxis.hoverformat == "%a %d %b %Y"
                assert any("Thu 01 Oct 2026" in row for row in fig.data[0].customdata)
                assert 100 < fig.layout.yaxis.range[1] < 5000


def test_v1_count_only_snapshot_rejected_and_not_overwritten(tmp_path):
    publish_launches({END: 1}, through=END, now=NOW, root=tmp_path)
    path = tmp_path / "project_launches.json"
    doc = json.loads(path.read_text())
    doc["format"] = "ZOONIVERSE_PROJECT_LAUNCHES_V1"
    doc["launches"] = [[day, count] for day, count, _ in doc["launches"]]
    path.write_text(json.dumps(doc))
    original = path.read_bytes()
    with pytest.raises(ValueError, match="envelope"):
        load_launches(root=tmp_path)
    with pytest.raises(ValueError, match="envelope"):
        publish_launches({END: 1}, through=END, now=NOW, root=tmp_path)
    assert path.read_bytes() == original


def test_names_must_be_public_safe_and_consistent_with_counts(tmp_path):
    for names in (
        {END: ["\n bad"]},
        {END: ["  extra spaces "]},
        {END: [""]},
        {END: ["x" * 161]},
        {END: ["one", "two"]},
        {END - timedelta(days=2): ["wrong date"]},
    ):
        with pytest.raises(ValueError):
            publish_launches({END: 1}, names=names, through=END, now=NOW, root=tmp_path)
    assert not (tmp_path / "project_launches.json").exists()


def test_v2_corrupt_titles_are_rejected(tmp_path):
    publish_launches({END: 1}, names={END: ["Galaxy Zoo"]}, through=END, now=NOW, root=tmp_path)
    path = tmp_path / "project_launches.json"
    original = json.loads(path.read_text())
    for bad in (None, [3], [""], ["x" * 161], ["a", "b"], [" x"]):
        doc = json.loads(json.dumps(original))
        doc["launches"][0][2] = bad
        path.write_text(json.dumps(doc))
        with pytest.raises((TypeError, ValueError)):
            load_launches(root=tmp_path)


def test_overlay_load_failure_does_not_break_registration_graph(monkeypatch, tmp_path):
    pytest.importorskip("dash")
    from zooniverse_dashboard import ui

    monkeypatch.setattr(ui, "load_launches", lambda: (_ for _ in ()).throw(ValueError("bad")))
    assert ui._load_optional_launches("users") is None
    assert ui._load_optional_launches("talks") is None
    assert ui._load_optional_launches("classifications") is None
