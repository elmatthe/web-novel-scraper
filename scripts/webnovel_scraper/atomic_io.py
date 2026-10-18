"""Atomic JSON publication shared by the run reservation (03.0) and, later, manifests.

The payload is serialised first, written to a uniquely named temp file in the target's own
folder, flushed to disk, then moved over the target with ``os.replace``. A reader therefore
sees the old file or the new file, never a torn one, and a failure leaves the old file in
place with no temp file behind.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def write_json_atomic(path: str | os.PathLike[str], payload: object) -> None:
    """Write ``payload`` as UTF-8 JSON to ``path`` atomically (raises on failure)."""
    target = Path(path)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"  # TypeError before any I/O
    fd, temp_name = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
