"""Find and describe earlier runs for Resume Existing Run (v0.3.0 03.3). Pure, no Tk.

A run folder qualifies only if this version made it: ``<novel-slug>-N`` directly in the output
root, with a matching reservation marker and a readable journal. Listing reads only the journal
(fast, no file hashing); the pipeline re-verifies every output by checksum when the resume starts.
Pre-0.3.0 folders are never listed and never touched.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .models import OutputMode
from .run_journal import JOURNAL_FILENAME, JournalError, RunHeader, replay_journal
from .run_output import plan_groups
from .run_storage import RunStorageError, open_existing_run

_MODE_TEXT = {"separate": "individual chapters", "single": "whole range", "chunked": "every {n} chapters"}
_END_TEXT = {
    None: "interrupted",
    "cancelled": "stopped",
    "failed": "ended by an error",
    "complete_with_problems": "finished with problems",
    "complete": "finished",
}


@dataclass(frozen=True)
class RunSummary:
    path: Path
    header: RunHeader
    files_done: int
    files_total: int
    last_end: Optional[str]

    @property
    def unfinished(self) -> bool:
        return self.files_done < self.files_total or self.last_end != "complete"

    @property
    def host(self) -> str:
        return self.header.source.host

    def describe(self, novel_title: Optional[str] = None) -> str:
        h = self.header
        grouping = _MODE_TEXT[h.grouping_mode].format(n=h.chunk_size)
        return (
            f"{self.path.name}: {novel_title or h.novel_slug} from {self.host}, chapters "
            f"{h.first_label}-{h.last_label} ({h.selection_count}), {'+'.join(h.formats).upper()}, "
            f"{grouping}; {self.files_done} of {self.files_total} files saved; "
            f"{_END_TEXT.get(self.last_end, self.last_end)}"
        )


def describe_run(run_dir: str | os.PathLike[str]) -> RunSummary:
    """Summarise one run folder. Raises ``RunStorageError``/``JournalError`` if it is not a
    resumable run of this version (the message says why)."""
    path = Path(run_dir)
    found = open_existing_run(path.parent, path)
    replay = replay_journal(found.path / JOURNAL_FILENAME)
    header = replay.header
    if header.novel_slug != found.novel_slug or header.run_number != found.number:
        raise JournalError("The run journal describes another run folder.")
    groups = plan_groups(header.selection_count, OutputMode(header.grouping_mode), header.chunk_size)
    done = sum(
        1
        for group in groups
        for fmt in header.formats
        if (record := replay.publishes.get((group.key, fmt))) is not None and not record["missing"]
    )
    return RunSummary(path=found.path, header=header, files_done=done,
                      files_total=len(groups) * len(header.formats), last_end=replay.last_end)


def list_runs(root: str | os.PathLike[str], *, unfinished_only: bool = True) -> list[RunSummary]:
    """Every resumable run directly in ``root``, newest first. Folders that are not runs of this
    version are skipped silently (they are simply not offered)."""
    base = Path(root)
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return []
    found: list[RunSummary] = []
    for entry in entries:
        if not entry.is_dir() or not (entry / JOURNAL_FILENAME).is_file():
            continue
        try:
            summary = describe_run(entry)
        except (RunStorageError, JournalError, OSError, ValueError):
            continue
        if summary.unfinished or not unfinished_only:
            found.append(summary)
    found.sort(key=lambda s: (s.header.created_utc, s.path.name), reverse=True)
    return found
