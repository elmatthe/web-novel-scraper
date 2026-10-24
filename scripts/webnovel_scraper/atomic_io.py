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
import time
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
        # Windows readers/scanners can briefly deny replacement of a closed
        # checkpoint. Retry the same fsynced temp; persistent denial still fails
        # with the previous target intact and normal temp cleanup below.
        for attempt in range(5):
            try:
                os.replace(temp_name, target)
                break
            except PermissionError as exc:
                if getattr(exc, "winerror", None) not in (5, 32, 33) or attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
