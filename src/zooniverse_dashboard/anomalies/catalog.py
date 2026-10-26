"""Validated on-disk anomaly cache used by Dash without numerical dependencies."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from zooniverse_dashboard.config import data_dir
from zooniverse_dashboard.snapshots import SOURCES, load_snapshot

CATALOG_FORMAT = "ZOONIVERSE_ANOMALIES_V1"
ID_PATTERN = re.compile(r"^(users|classifications|talks)-\d{4}-\d{2}-\d{2}-(spike|drop)$")
MAX_CATALOG_BYTES = 500_000


def catalog_path(root: Path | None = None) -> Path:
    return (root if root is not None else data_dir()) / "anomalies.json"


def read_catalog(*, root: Path | None = None) -> dict | None:
    path = catalog_path(root)
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_CATALOG_BYTES:
        return None
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(result, dict)
            or result.get("format") != CATALOG_FORMAT
            or not isinstance(result.get("sources"), dict)
            or not isinstance(result.get("events"), list)
        ):
            return None
        return result
    except (ValueError, OSError, TypeError):
        return None


def visible_events(*, root: Path | None = None) -> tuple[list[dict], dict[str, str]]:
    """Return only results matching the *current* source snapshot revision."""
    doc = read_catalog(root=root)
    statuses = {}
    for source in SOURCES:
        try:
            stamp = load_snapshot(source, root=root).last_modified.isoformat()
        except (OSError, ValueError, TypeError, KeyError):
            statuses[source] = "snapshot unavailable"
            continue
        metadata = doc.get("sources", {}).get(source) if doc else None
        if metadata is None:
            statuses[source] = "analysis not generated"
        elif metadata.get("snapshot_revision") != stamp:
            statuses[source] = "analysis stale — rebuild after refreshing data"
        elif metadata.get("status") != "ok":
            statuses[source] = metadata.get("message", "analysis unavailable")
        else:
            statuses[source] = "ready"
    safe = []
    for event in doc.get("events", []) if doc else []:
        if (
            isinstance(event, dict)
            and event.get("source") in SOURCES
            and statuses[event["source"]] == "ready"
            and isinstance(event.get("id"), str)
            and ID_PATTERN.fullmatch(event["id"])
        ):
            safe.append(event)
    return sorted(safe, key=lambda item: (item["detected"], item["id"]), reverse=True), statuses


def event_by_id(event_id: str, *, source: str | None = None) -> dict | None:
    if not ID_PATTERN.fullmatch(event_id):
        return None
    events, _ = visible_events()
    return next(
        (
            event
            for event in events
            if event["id"] == event_id and (source is None or event["source"] == source)
        ),
        None,
    )


def write_catalog(doc: dict, *, root: Path | None = None) -> None:
    """Atomic replacement so readers never observe partially written analyses."""
    destination = catalog_path(root)
    if destination.is_symlink() or destination.suffix != ".json":
        raise ValueError("Unsafe anomaly catalog destination")
    content = json.dumps(doc, separators=(",", ":"))
    if len(content.encode("utf-8")) > MAX_CATALOG_BYTES:
        raise ValueError("Anomaly catalog exceeds size limit")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix=".anomaly-", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if destination.is_symlink():
            raise ValueError("Anomaly catalog destination changed")
        os.replace(temp, destination)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def build_catalog(*, root: Path | None = None, progress=None) -> dict:
    """Purely offline computation; deliberately not invoked during Dash requests."""
    # Delayed import: web workers need neither statsmodels nor a numerical fit.
    from zooniverse_dashboard.anomalies.compute import add_shared_drop_context, episodes, score_days

    results = []
    metadata = {}
    for source in SOURCES:
        if progress is not None:
            progress(f"Analyzing {source} from saved daily aggregates...")
        try:
            snapshot = load_snapshot(source, root=root)
        except (OSError, ValueError, TypeError, KeyError):
            metadata[source] = {"status": "missing", "message": "snapshot unavailable"}
            continue
        stamp = snapshot.last_modified.isoformat()
        try:
            scored = score_days(snapshot.daily, source=source)
            events = episodes(scored, source=source)
            metadata[source] = {
                "status": "ok" if scored else "insufficient_history",
                "message": "" if scored else "Need at least 911 daily buckets for NB2 calibration",
                "snapshot_revision": stamp,
                "evaluated_first": scored[0]["date"] if scored else None,
                "evaluated_last": scored[-1]["date"] if scored else None,
                "model": "Calendar-aware NB2" if source == "users" else "NB2",
                "episode_count": len(events),
            }
            results.extend(events)
        except (RuntimeError, ValueError, FloatingPointError) as exc:
            metadata[source] = {
                "status": "error",
                "snapshot_revision": stamp,
                "message": f"NB2 analysis failed ({type(exc).__name__}); aggregate snapshots unchanged",
            }
        if progress is not None:
            progress(
                f"{source}: {metadata[source]['status']} "
                f"{metadata[source].get('episode_count', 0)} episodes"
            )
    add_shared_drop_context(results)
    return {
        "format": CATALOG_FORMAT,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources": metadata,
        "events": sorted(results, key=lambda item: (item["detected"], item["id"]), reverse=True),
    }


def rebuild(*, root: Path | None = None, progress=None) -> dict:
    doc = build_catalog(root=root, progress=progress)
    write_catalog(doc, root=root)
    return doc
