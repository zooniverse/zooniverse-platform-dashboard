"""Publish small JSON documents with private, atomic filesystem replacement.

The collection, refresh-status, worker-status and anomaly-catalog schemas are
validated by their owning modules. This module owns only the shared write
mechanism: a same-directory temporary file, restrictive permissions, durable
file sync, atomic replacement, parent-directory sync and temporary cleanup.

The destination is checked for symlinks both before and after writing. These
checks reduce accidental or malicious symlink-following, but a shared directory
must itself be access-controlled; this is not a general multi-user secure
filesystem transaction or a distributed storage primitive.

See Also
--------
:func:`atomic_publish_json`
    Publish a JSON-serializable document using compact encoding.
:func:`atomic_replace_text`
    Publish an already-serialized UTF-8 payload with the same durability rules.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_replace_text(path: Path, content: str, *, prefix: str = ".analytics-") -> None:
    """Atomically replace one JSON file with a UTF-8 text payload.

    The existing destination is not opened for writing. If serialization or
    writing fails before replacement, the previous file remains unchanged. A
    directory-sync failure after replacement is propagated, but the rename has
    already occurred and cannot be rolled back by this function.

    Parameters
    ----------
    path : pathlib.Path
        Destination JSON path on a writable local POSIX filesystem.
    content : str
        Fully serialized UTF-8 document supplied by the schema owner.
    prefix : str, optional
        Prefix for the private temporary file created beside ``path``.

    Returns
    -------
    None
        The destination is replaced as a side effect.

    Raises
    ------
    ValueError
        If ``path`` is not a JSON file path or is a symlink.
    OSError
        If directory creation, writing, fsync, or replacement fails.
    """
    if path.suffix != ".json" or path.is_symlink():
        raise ValueError("Invalid snapshot destination")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd: int
    tmp: str
    fd, tmp = tempfile.mkstemp(prefix=prefix, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_symlink():
            raise ValueError("Snapshot destination changed")
        os.replace(tmp, path)
        dir_fd: int = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_publish_json(path: Path, document: dict[str, Any]) -> None:
    """Atomically publish a compact JSON document without validating its schema.

    Parameters
    ----------
    path : pathlib.Path
        Target ``.json`` path. The owning module validates existing contents.
    document : dict[str, typing.Any]
        JSON-serializable mapping to publish.

    Returns
    -------
    None
        The document is saved atomically.

    Raises
    ------
    TypeError
        If the document cannot be serialized to JSON.
    ValueError
        If the destination is not a safe JSON path.
    OSError
        If filesystem publication fails.

    See Also
    --------
    :func:`atomic_replace_text`
        Performs the actual durable replacement.
    """
    atomic_replace_text(path, json.dumps(document, separators=(",", ":")))
