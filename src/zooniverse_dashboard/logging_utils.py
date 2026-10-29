"""Provide privacy-conscious logging for refresh and pipeline operations.

Remote database errors and HTTP exceptions can embed credentials, URLs,
request parameters, and response bodies. The refresh logger therefore emits
only structured operational metadata and deliberately avoids printing raw
exception strings, local-variable dumps, or full Python tracebacks. The
sanitizer retains a bounded series of Python source basenames, line numbers,
and function names for diagnosis without copying exception messages.

The dedicated logger is shared by the direct CLI refresh, detached worker,
and dashboard server; output is UTC-formatted on standard error.

See Also
--------
:func:`configure_refresh_logging`
    Configure the refresh logger once per process.
:func:`safe_error_trace`
    Produce safe code-location context for a caught exception.
"""

from __future__ import annotations

import logging
import sys
import time
import traceback
from pathlib import Path
from typing import Final

LOGGER_NAME: Final[str] = "zooniverse_dashboard.refresh"


def configure_refresh_logging() -> logging.Logger:
    """Configure and return the dedicated UTC refresh logger.

    Attach a standard-error handler only if none is present, set the formatter
    to UTC, set the level to ``INFO``, and disable propagation into the root
    logger. Repeated calls return the same configured logger without stacking
    handlers. In detached workers, stderr is redirected to the private worker
    log by :func:`zooniverse_dashboard.refresh_job.start_job`.

    Returns
    -------
    logging.Logger
        The configured ``zooniverse_dashboard.refresh`` logger.

    See Also
    --------
    :func:`safe_error_trace`
        Build safe diagnostic text for an exception.
    """
    logger: logging.Logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        handler: logging.StreamHandler = logging.StreamHandler(sys.stderr)
        formatter: logging.Formatter = logging.Formatter(
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
    """Summarize an exception by code location without exposing its message.

    Inspect up to 12 traceback frames and render at most the six most recent as
    ``filename:line:function`` joined by `` > ``. Only source-file **basenames**
    are retained. Exception text, arguments, local variables, connection URLs,
    and remote payloads are never interpolated into the result. If no traceback
    exists, return ``no-traceback``.

    Parameters
    ----------
    exc : BaseException
        Caught exception with an optional Python traceback.

    Returns
    -------
    str
        A bounded, context-only diagnostic string safe for structured logging.

    See Also
    --------
    :func:`zooniverse_dashboard.refresh.refresh_source`
        Logs the sanitized trace while propagating the original exception.
    """
    frames: traceback.StackSummary = traceback.extract_tb(exc.__traceback__, limit=12)
    locations: list[str] = [
        f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}" for frame in frames[-6:]
    ]
    return " > ".join(locations) if locations else "no-traceback"
