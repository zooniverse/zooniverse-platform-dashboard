"""Privacy-preserving refresh logging for the CLI and dashboard server.

Never log remote exception messages or Python tracebacks verbatim: database
libraries may include credential-bearing connection strings or response data.
"""

from __future__ import annotations

import logging
import sys
import time
import traceback
from pathlib import Path

LOGGER_NAME = "zooniverse_dashboard.refresh"


def configure_refresh_logging() -> logging.Logger:
    """Write UTC INFO-level refresh events to stderr (captured by Docker/Gunicorn).

    Safe to call from both the Dash app factory and command-line entry point;
    the dedicated logger does not depend on root/Gunicorn logging settings.
    """
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        formatter = logging.Formatter(
            "%(asctime)sZ %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        formatter.converter = time.gmtime
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def safe_error_trace(exc: BaseException) -> str:
    """Show exception *type* and code locations, never message/args/local vars.

    Raw tracebacks and ``str(exc)`` may include database URLs, API response
    bodies and other sensitive information. Path basenames and static Python
    function names are enough to pinpoint the failing code location.
    """
    frames = traceback.extract_tb(exc.__traceback__, limit=12)
    locations = [
        f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}" for frame in frames[-6:]
    ]
    return " > ".join(locations) if locations else "no-traceback"
