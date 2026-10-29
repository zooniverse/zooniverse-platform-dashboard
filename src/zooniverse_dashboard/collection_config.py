"""Validate and persist the daily-aggregate collection history limit.

The environment variable ``ANALYTICS_HISTORY_DAYS`` provides a default
history window; a small JSON file on the analytics volume can override it
without mutating the environment of a running container. Zero requests
all available history. The override must be validated before it can be
used or replaced, so a corrupt file never silently triggers a larger
production query.

See Also
--------
:func:`zooniverse_dashboard.snapshots.publish_snapshot`
    Publishes results with the resolved history-limit metadata.
:func:`zooniverse_dashboard.config.data_dir`
    Selects the shared volume containing the override file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Final

from zooniverse_dashboard.config import data_dir
from zooniverse_dashboard.snapshots import MAX_DAYS, _atomic_publish

CONFIG_FORMAT: Final[str] = "ZOONIVERSE_COLLECTION_CONFIG_V1"
CONFIG_NAME: Final[str] = "collection_config.json"
ENV_NAME: Final[str] = "ANALYTICS_HISTORY_DAYS"


def _valid_days(value: object) -> int:
    """Validate a requested count of calendar days.

    Parameters
    ----------
    value : object
        Candidate value; must be an actual integer, not a boolean or string.
        Zero denotes all available history.

    Returns
    -------
    int
        The unchanged, validated history limit.

    Raises
    ------
    ValueError
        If the value is not an integer in the range ``0..MAX_DAYS``.
    """
    if type(value) is not int or not 0 <= value <= MAX_DAYS:
        raise ValueError(f"History limit must be an integer from 0 to {MAX_DAYS}")
    return value


def _environment_days() -> int:
    """Parse the history limit from the server environment.

    An absent ``ANALYTICS_HISTORY_DAYS`` defaults to zero. Only ASCII decimal
    digits are accepted; signs, whitespace within the number, and floats are
    rejected.

    Returns
    -------
    int
        Validated environment default in days.

    Raises
    ------
    ValueError
        If the environment contains a malformed or out-of-range limit.
    """
    raw: str = os.environ.get(ENV_NAME, "0").strip()
    if not raw.isascii() or not raw.isdecimal():
        raise ValueError(f"{ENV_NAME} must be a nonnegative integer (0 means all history)")
    return _valid_days(int(raw))


def _override_path(root: Path | None) -> Path:
    """Resolve the location of the persistent history-limit override.

    Parameters
    ----------
    root : pathlib.Path or None
        Alternative analytics root, primarily for tests. ``None`` uses
        :func:`zooniverse_dashboard.config.data_dir`.

    Returns
    -------
    pathlib.Path
        Path to ``collection_config.json``; it need not exist yet.
    """
    return (root if root is not None else data_dir()) / CONFIG_NAME


def effective_history_days(root: Path | None = None) -> int:
    """Return the durable history override or the environment default.

    An absent override falls back to ``ANALYTICS_HISTORY_DAYS``. Existing
    overrides must be ordinary, small JSON files with the exact expected
    format and a valid integer limit. Unsafe or corrupted files fail closed.

    Parameters
    ----------
    root : pathlib.Path or None, optional
        Root of the persistent analytics directory. If omitted, use
        :func:`zooniverse_dashboard.config.data_dir`.

    Returns
    -------
    int
        Effective number of calendar days; ``0`` means full history.

    Raises
    ------
    ValueError
        If an existing override is unsafe, malformed, or out of range, or
        if the environment default is invalid.
    OSError
        If an override cannot be inspected or read.
    """
    path: Path = _override_path(root)
    if not path.exists() and not path.is_symlink():
        return _environment_days()
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024:
        raise ValueError("Invalid collection configuration")
    try:
        doc: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid collection configuration") from exc
    if not isinstance(doc, dict) or set(doc) != {"format", "history_days"}:
        raise ValueError("Invalid collection configuration")
    if doc["format"] != CONFIG_FORMAT:
        raise ValueError("Invalid collection configuration")
    return _valid_days(doc["history_days"])


def set_history_days(days: int, root: Path | None = None) -> Path:
    """Atomically save a validated history limit on the shared volume.

    An existing override is validated before replacement, preventing a
    corrupted or unsafe file from being silently overwritten. Publication
    uses the internal ``_atomic_publish`` helper in the snapshots module.

    Parameters
    ----------
    days : int
        New limit. ``0`` means the full available history.
    root : pathlib.Path or None, optional
        Analytics directory, defaulting to the configured persistent root.

    Returns
    -------
    pathlib.Path
        Location of the published ``collection_config.json`` file.

    Raises
    ------
    ValueError
        If ``days`` is invalid or an existing override fails validation.
    OSError
        If file creation or atomic publication fails.
    """
    value: int = _valid_days(days)
    path = _override_path(root)
    if path.exists() or path.is_symlink():
        effective_history_days(root)  # Reject corrupt or unsafe files, no overwrite.
    _atomic_publish(path, {"format": CONFIG_FORMAT, "history_days": value})
    return path
