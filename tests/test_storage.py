"""Regression tests for shared atomic JSON publication and failure cleanup."""

from __future__ import annotations

import json
import stat

import pytest

from zooniverse_dashboard.anomalies import catalog
from zooniverse_dashboard.storage import atomic_publish_json, atomic_replace_text


def test_atomic_json_publish_is_private_and_replaces_whole_document(tmp_path):
    path = tmp_path / "nested" / "metrics.json"
    atomic_publish_json(path, {"count": 1})
    assert path.read_bytes() == b'{"count":1}'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    atomic_publish_json(path, {"count": 2, "notes": "\N{SNOWMAN}"})
    assert json.loads(path.read_text()) == {"count": 2, "notes": "\N{SNOWMAN}"}
    assert not list(path.parent.glob(".analytics-*"))


def test_atomic_replacement_refuses_symlink_and_keeps_target(tmp_path):
    target = tmp_path / "target.json"
    target.write_text('"untouched"')
    path = tmp_path / "redirect.json"
    path.symlink_to(target)
    with pytest.raises(ValueError, match="destination"):
        atomic_replace_text(path, '"replacement"')
    assert target.read_text() == '"untouched"'
    assert not list(tmp_path.glob(".analytics-*"))


def test_failed_rename_preserves_previous_document_and_cleans_temp(monkeypatch, tmp_path):
    import zooniverse_dashboard.storage as storage

    path = tmp_path / "status.json"
    path.write_text('{"state":"old"}')
    previous = path.read_bytes()

    def fail_rename(*args):
        raise OSError("synthetic rename failure")

    monkeypatch.setattr(storage.os, "replace", fail_rename)
    with pytest.raises(OSError, match="synthetic rename"):
        atomic_publish_json(path, {"state": "new"})
    assert path.read_bytes() == previous
    assert not list(tmp_path.glob(".analytics-*"))


def test_failed_file_fsync_preserves_previous_document(monkeypatch, tmp_path):
    import zooniverse_dashboard.storage as storage

    path = tmp_path / "status.json"
    path.write_text('{"state":"old"}')

    def fail_sync(*args):
        raise OSError("synthetic file sync failure")

    monkeypatch.setattr(storage.os, "fsync", fail_sync)
    with pytest.raises(OSError, match="synthetic file sync"):
        atomic_publish_json(path, {"state": "new"})
    assert path.read_text() == '{"state":"old"}'
    assert not list(tmp_path.glob(".analytics-*"))


def test_catalog_writer_uses_shared_atomic_mechanism(tmp_path):
    content = {"format": catalog.CATALOG_FORMAT, "sources": {}, "events": []}
    catalog.write_catalog(content, root=tmp_path)
    path = tmp_path / "anomalies.json"
    assert json.loads(path.read_text()) == content
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".anomaly-*"))


def test_catalog_rejects_oversize_before_replacing_prior_catalog(tmp_path):
    catalog.write_catalog({"initial": True}, root=tmp_path)
    path = tmp_path / "anomalies.json"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="size limit"):
        catalog.write_catalog({"long": "x" * (catalog.MAX_CATALOG_BYTES + 1)}, root=tmp_path)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".anomaly-*"))
