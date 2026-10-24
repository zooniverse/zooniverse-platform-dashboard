"""Validated history-window configuration shared by CLI, refresh and Dash.

The environment provides the default; a file on the persistent volume is a
runtime override. Container processes cannot modify their own deployment env.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from zooniverse_dashboard.config import data_dir
from zooniverse_dashboard.snapshots import MAX_DAYS, _atomic_publish

CONFIG_FORMAT = "ZOONIVERSE_COLLECTION_CONFIG_V1"
CONFIG_NAME = "collection_config.json"
ENV_NAME = "ANALYTICS_HISTORY_DAYS"


def _valid_days(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_DAYS:
        raise ValueError(f"History limit must be an integer from 0 to {MAX_DAYS}")
    return value


def _environment_days() -> int:
    raw = os.environ.get(ENV_NAME, "0").strip()
    if not raw.isascii() or not raw.isdecimal():
        raise ValueError(f"{ENV_NAME} must be a nonnegative integer (0 means all history)")
    return _valid_days(int(raw))


def _override_path(root: Path | None) -> Path:
    return (root if root is not None else data_dir()) / CONFIG_NAME


def effective_history_days(root: Path | None = None) -> int:
    """Return the durable override, or else the configured environment default.

    A missing file uses the environment. A corrupted or unsafe file fails
    closed instead of unexpectedly falling back to a longer data query.
    """
    path = _override_path(root)
    if not path.exists() and not path.is_symlink():
        return _environment_days()
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024:
        raise ValueError("Invalid collection configuration")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid collection configuration") from exc
    if not isinstance(doc, dict) or set(doc) != {"format", "history_days"}:
        raise ValueError("Invalid collection configuration")
    if doc["format"] != CONFIG_FORMAT:
        raise ValueError("Invalid collection configuration")
    return _valid_days(doc["history_days"])


def set_history_days(days: int, root: Path | None = None) -> Path:
    """Persist an effective date limit on the shared volume (0 means all)."""
    value = _valid_days(days)
    path = _override_path(root)
    if path.exists() or path.is_symlink():
        effective_history_days(root)  # Reject corrupt or unsafe files, no overwrite.
    _atomic_publish(path, {"format": CONFIG_FORMAT, "history_days": value})
    return path
