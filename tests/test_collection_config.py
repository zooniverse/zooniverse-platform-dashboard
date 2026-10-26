"""History-window environment defaults and atomic persistent override."""

import json

import pytest

from zooniverse_dashboard.collection_config import (
    effective_history_days,
    set_history_days,
)
from zooniverse_dashboard.refresh import refresh_lock


def test_default_all_time_and_environment_days(monkeypatch, tmp_path):
    monkeypatch.delenv("ANALYTICS_HISTORY_DAYS", raising=False)
    assert effective_history_days(tmp_path) == 0
    monkeypatch.setenv("ANALYTICS_HISTORY_DAYS", " 365 ")
    assert effective_history_days(tmp_path) == 365


def test_persisted_override_wins_and_zero_means_all(monkeypatch, tmp_path):
    monkeypatch.setenv("ANALYTICS_HISTORY_DAYS", "28")
    set_history_days(7, tmp_path)
    assert effective_history_days(tmp_path) == 7
    set_history_days(0, tmp_path)
    assert effective_history_days(tmp_path) == 0
    document = json.loads((tmp_path / "collection_config.json").read_text())
    assert document["history_days"] == 0
    assert not (tmp_path / "collection_config.json").is_symlink()


@pytest.mark.parametrize("value", ["abc", "-1", "2.5", "1 day", "", "999999"])
def test_invalid_environment_is_rejected_without_fallback(monkeypatch, tmp_path, value):
    monkeypatch.setenv("ANALYTICS_HISTORY_DAYS", value)
    with pytest.raises(ValueError):
        effective_history_days(tmp_path)


@pytest.mark.parametrize("value", [-1, True, 2.5, 999999])
def test_invalid_limit_is_rejected(tmp_path, value):
    with pytest.raises(ValueError):
        set_history_days(value, tmp_path)
    assert not (tmp_path / "collection_config.json").exists()


def test_config_symlink_and_malformed_json_fail_closed(tmp_path):
    target = tmp_path / "private"
    target.write_text("not modified")
    path = tmp_path / "collection_config.json"
    path.symlink_to(target)
    with pytest.raises(ValueError):
        set_history_days(0, tmp_path)
    assert target.read_text() == "not modified"
    path.unlink()
    path.write_text("{")
    with pytest.raises(ValueError):
        effective_history_days(tmp_path)
    with pytest.raises(ValueError):
        set_history_days(0, tmp_path)
    assert path.read_text() == "{"


def test_reject_config_changes_while_refresh_lock_is_held(tmp_path):
    with refresh_lock(tmp_path):
        with pytest.raises(Exception):
            with refresh_lock(tmp_path):
                set_history_days(365, tmp_path)


def test_cli_set_limit_and_show_effective_value(monkeypatch, tmp_path, capsys):
    import sys

    from zooniverse_dashboard import cli

    monkeypatch.setenv("ANALYTICS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["set-date-limit", "365"])
    cli.set_date_limit()
    assert effective_history_days(tmp_path) == 365
    monkeypatch.setattr(sys, "argv", ["set-date-limit"])
    cli.set_date_limit()
    assert "365" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["set-date-limit", "0"])
    cli.set_date_limit()
    assert effective_history_days(tmp_path) == 0


def test_cli_refresh_is_argument_free(monkeypatch):
    import sys

    from zooniverse_dashboard import cli

    called = []
    monkeypatch.setattr(
        cli,
        "refresh_and_rebuild",
        lambda: (
            called.append(True),
            {"state": "ok", "streams": {"users": "ok", "classifications": "ok", "talks": "ok"}},
        )[1],
    )
    monkeypatch.setattr(sys, "argv", ["refresh-data"])
    cli.refresh_data()
    assert called == [True]
    monkeypatch.setattr(sys, "argv", ["refresh-data", "--through", "2026-10-20"])
    with pytest.raises(SystemExit):
        cli.refresh_data()
