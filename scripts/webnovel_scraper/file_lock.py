"""A cross-process, non-blocking advisory file lock (stdlib only; v0.3.0 Checkpoint 03.2a).

The chapter manifest store does a read-modify-write on one runtime file per source. Two
processes doing that at once would silently lose one update, so the writer holds this lock
for the whole load -> reconcile -> publish sequence. The operating system releases the lock
when its holder dies, so a crash never leaves a stale lock behind that a person must clean up.

``msvcrt.locking`` (Windows) and ``fcntl.flock`` (macOS/POSIX) both lock per open handle, so a
second handle in the *same* process also finds the lock busy; callers that need re-entry
(the store does) track their own holders.
"""

from __future__ import annotations

import os
from pathlib import Path


class LockBusy(OSError):
    """Another holder (this process or another) already has the lock."""


class FileLock:
    """Hold an exclusive lock on ``path`` (created if missing; its contents are never used)."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> None:
        """Take the lock without waiting; raise :class:`LockBusy` if it is not free."""
        if self._fd is not None:
            raise RuntimeError("lock already held by this FileLock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise LockBusy(f"{self.path.name} is locked by another holder") from exc
        self._fd = fd

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass  # closing the descriptor releases it regardless
        finally:
            os.close(fd)

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()
