"""Detached browser refresh job: admission, progress, failure and crash diagnosis."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from zooniverse_dashboard import refresh, refresh_job


def test_start_admits_only_one_job_without_executing_pipeline(monkeypatch, tmp_path):
    launched = []
    monkeypatch.setattr(
        refresh_job.subprocess,
        "Popen",
        lambda argv, **kw: launched.append((argv, kw)),
    )
    run_id = refresh_job.start_job(root=tmp_path)
    assert len(run_id) == 32
    assert launched[0][0][1:3] == ["-m", "zooniverse_dashboard.refresh_job"]
    assert launched[0][0][-1] == run_id
    assert launched[0][1]["start_new_session"] is True
    assert launched[0][1]["close_fds"] is True
    assert launched[0][1]["stdin"] == refresh_job.subprocess.DEVNULL
    assert refresh_job.load_job(tmp_path)["state"] == "queued"
    with pytest.raises(refresh.RefreshRejected, match="already running"):
        refresh_job.start_job(root=tmp_path)
    assert len(launched) == 1
    assert (tmp_path / "refresh-worker.log").stat().st_mode & 0o777 == 0o600


def test_run_job_publishes_progress_and_success(monkeypatch, tmp_path):
    monkeypatch.setattr(refresh_job.subprocess, "Popen", lambda *args, **kwargs: None)
    run_id = refresh_job.start_job(root=tmp_path)
    states = []

    def pipeline(*, root, progress):
        assert root == tmp_path
        assert refresh_job.load_job(root)["state"] == "running"
        progress("Fetching users aggregates...")
        states.append(refresh_job.load_job(root))
        progress("users: snapshot validated and published")
        return {
            "state": "ok",
            "refresh_state": "ok",
            "anomalies_state": "ok",
            "sources": {src: "ok" for src in ("users", "classifications", "talks")},
        }

    monkeypatch.setattr(refresh_job, "refresh_and_rebuild", pipeline)
    result = refresh_job.run_job(run_id, root=tmp_path)
    assert result["state"] == "ok"
    assert "Fetching users aggregates..." in states[0]["steps"]
    assert refresh_job.load_job(tmp_path)["finished_at_utc"]
    assert result["summary"]["sources"]["talks"] == "ok"


def test_worker_failure_is_sanitized_and_finalized(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(refresh_job.subprocess, "Popen", lambda *args, **kwargs: None)
    run_id = refresh_job.start_job(root=tmp_path)
    secret = "postgresql://someone:PRIVATE_SECRET@example.org/db"

    def fail(**_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(refresh_job, "refresh_and_rebuild", fail)
    result = refresh_job.run_job(run_id, root=tmp_path)
    assert result["state"] == "failed"
    assert result["finished_at_utc"] is not None
    assert secret not in (tmp_path / "refresh_job.json").read_text()
    assert "PRIVATE_SECRET" not in caplog.text


def test_dead_worker_is_reported_interrupted(monkeypatch, tmp_path):
    monkeypatch.setattr(refresh_job.subprocess, "Popen", lambda *args, **kwargs: None)
    run_id = refresh_job.start_job(root=tmp_path)
    path = tmp_path / "refresh_job.json"
    doc = json.loads(path.read_text())
    doc["state"] = "running"
    doc["updated_at_utc"] = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    path.write_text(json.dumps(doc))
    assert refresh_job.load_job(tmp_path)["state"] == "interrupted"
    assert json.loads(path.read_text())["state"] == "running"  # read-only
    # The obsolete job doesn't block a new admission once its worker is gone.
    assert refresh_job.start_job(root=tmp_path) != run_id


def test_active_worker_not_mistaken_for_crashed_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(refresh_job.subprocess, "Popen", lambda *args, **kwargs: None)
    refresh_job.start_job(root=tmp_path)
    path = tmp_path / "refresh_job.json"
    doc = json.loads(path.read_text())
    doc["state"] = "running"
    doc["updated_at_utc"] = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    path.write_text(json.dumps(doc))
    with refresh_job._lock(tmp_path, ".refresh-job.lock"):
        assert refresh_job.load_job(tmp_path)["state"] == "running"


def test_cooldown_and_cli_lock_prevent_spawn(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(refresh_job.subprocess, "Popen", lambda *a, **kw: called.append(1))
    with refresh.refresh_lock(tmp_path):
        with pytest.raises(refresh.RefreshRejected, match="running"):
            refresh_job.start_job(root=tmp_path)
    now = datetime.now(timezone.utc)
    status = {
        "format": refresh.STATUS_FORMAT,
        "started_at_utc": now.isoformat(),
        "finished_at_utc": now.isoformat(),
        "state": "ok",
        "streams": {source: "ok" for source in ("users", "classifications", "talks")},
    }
    refresh._write_status(tmp_path, status)
    with pytest.raises(refresh.RefreshRejected, match="last hour"):
        refresh_job.start_job(root=tmp_path)
    assert not called


def test_worker_exception_on_refresh_rejection_has_safe_status(monkeypatch, tmp_path):
    monkeypatch.setattr(refresh_job.subprocess, "Popen", lambda *args, **kwargs: None)
    run_id = refresh_job.start_job(root=tmp_path)

    def reject(**kwargs):
        raise refresh.RefreshRejected("Refresh was attempted within the last hour")

    monkeypatch.setattr(refresh_job, "refresh_and_rebuild", reject)
    result = refresh_job.run_job(run_id, root=tmp_path)
    assert result["state"] == "rejected"
    assert "last hour" in result["steps"][-1]


def test_source_status_is_durable_after_each_source(monkeypatch, tmp_path):
    stamps = []

    def fake_source(source, **kwargs):
        status = refresh.load_status(tmp_path)
        stamps.append((source, dict(status["streams"])))
        if source == "talks":
            raise RuntimeError("secret password hidden")

    monkeypatch.setattr(refresh, "refresh_source", fake_source)
    monkeypatch.setattr(refresh, "refresh_launches", lambda **kwargs: None)
    result = refresh.refresh_all(root=tmp_path)
    assert stamps[0][1] == {
        "users": "pending",
        "classifications": "pending",
        "talks": "pending",
        "project_launches": "pending",
    }
    assert stamps[1][1]["users"] == "ok"
    assert stamps[2][1]["classifications"] == "ok"
    assert refresh.load_status(tmp_path)["streams"] == {
        "users": "ok",
        "classifications": "ok",
        "talks": "failed",
        "project_launches": "ok",
    }
    assert result["state"] == "partial_failure"
