"""Resolve server-side configuration for local and deployed environments.

This module is the boundary between the application and its deployment
environment. It loads optional local ``.env`` settings, chooses the
persistent directory used for aggregate JSON snapshots, and reads the
Panoptes PostgreSQL URL *only* for server-side collection. Configuration
values must never be returned to Dash clients, logged, or written to
analytics snapshots.

See Also
--------
:mod:`zooniverse_dashboard.collection_config`
    The validated, durable history-window override.
:mod:`zooniverse_dashboard.snapshots`
    Local aggregate snapshot paths and publication.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def data_dir() -> Path:
    """Return the directory used for persistent analytics state.

    ``ANALYTICS_DATA_DIR`` overrides the default ``.local/data`` directory.
    Relative paths are resolved by the operating system when accessed; ``~``
    is expanded, but this function does not create the directory.

    Returns
    -------
    pathlib.Path
        Configured path for snapshots, status files, and runtime overrides.
    """
    return Path(os.environ.get("ANALYTICS_DATA_DIR", ".local/data")).expanduser()


def panoptes_url() -> str:
    """Read the PostgreSQL connection URL for read-only Panoptes collection.

    The URL is read from ``DB_URL_PANOPTES`` at call time and is never
    serialized to a snapshot. This function checks the URL scheme, not
    database reachability, credentials, or the connection's permissions.

    Returns
    -------
    str
        The configured PostgreSQL connection URL, including any credentials.
        This value must remain strictly server-side.

    Raises
    ------
    RuntimeError
        If ``DB_URL_PANOPTES`` is absent or does not use an accepted
        PostgreSQL URL scheme.
    """
    url: str = os.environ.get("DB_URL_PANOPTES", "")
    if not url.startswith(("postgresql+psycopg://", "postgresql://")):
        raise RuntimeError("Panoptes PostgreSQL connection is not configured")
    return url
