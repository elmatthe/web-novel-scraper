"""Output groups, atomic publication and verified resume for one run (v0.3.0 03.3, plan §9.5).

Both pipeline paths hand every chapter they obtain to one :class:`RunOutputs`. It owns:

* **Groups.** The run's selection (fixed, ordered pages) is cut into output groups by the one
  grouping mode: one group per page (Individual chapters), one group per ``chunk_size`` pages
  (Every X chapters) or one group for all (Whole selected range). Every requested format renders
  the same groups from the same content, so TXT and PDF always share boundaries and order.
* **Names.** ``PDF/`` and ``TXT/`` folders in the run folder. An individual chapter is named from
  its heading (``Chapter 3664_ Twisted Reflection.pdf``; a second page showing the same number gets
  `` [slug 3679]``); a group is ``<Novel>_Chapters_<first>-<last>.<ext>`` from the labels of its
  first and last *selected* page, so a group keeps its name when a missing page is added later.
  A name another group of the run already owns is never reused: it gets the group's key appended.
* **Publication.** Render to a temp file in the destination folder, ``fsync``, ``os.replace``, then
  hash the published file and append a ``publish`` record to the journal. A file without a record
  is unverified and is rendered again under the same name.
* **Resume.** A group/format is complete only if its latest record lacks no page and the file it
  names exists with the recorded size and SHA-256; filename presence alone never counts. Complete
  groups are skipped without any network or cache read; everything else is fetched (the HTML cache
  makes repeats cheap) and rendered again. A group that was published with pages missing is
  re-rendered once they are obtained.
* **Cancel.** Individual chapters are published as they arrive (the chapter in progress when Stop
  is pressed is finished). Grouped files are built at the end; after Stop none are built (their
  pages are in the HTML cache, so Resume builds them), and Stop pressed while they are being built
  ends the build at the next chapter of the file in progress (v0.3.0 06). Nothing partial is ever
  published by a Stop.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .adapters.base import BaseAdapter
from .models import ChapterContent, ChapterMeta, OutputMode
from .run_journal import FORMATS, Replay, RunJournal, file_sha256

FORMAT_DIRS = {"pdf": "PDF", "txt": "TXT"}
_TEMP_SUFFIXES = (".part", ".tmp")

Renderer = Callable[..., None]   # (format, contents, path, title, *, cancel_event=None)


@dataclass(frozen=True)
class OutputGroup:
    key: str
    positions: tuple[int, ...]


def plan_groups(count: int, mode: OutputMode, chunk_size: int) -> list[OutputGroup]:
    """The run's output groups over ``count`` selected pages (positions 0..count-1)."""
    if count <= 0:
        return []
    if mode is OutputMode.SEPARATE:
        return [OutputGroup(f"chapter-{p + 1:05d}", (p,)) for p in range(count)]
    if mode is OutputMode.SINGLE:
        return [OutputGroup("range", tuple(range(count)))]
    size = max(1, int(chunk_size))
    return [
        OutputGroup(f"chunk-{i // size + 1:05d}", tuple(range(i, min(i + size, count))))
        for i in range(0, count, size)
    ]


def novel_stem(title: str, slug: str) -> str:
    """A safe, space-free filename stem for grouped output (``Shadow_Slave``)."""
    safe = BaseAdapter.safe_filename(title)
    return re.sub(r"\s+", "_", safe).strip("_") or slug


def duplicate_suffix(meta: ChapterMeta, ordinal: int, count: int) -> str:
    """Readable suffix for the later pages sharing one displayed number."""
    if count <= 1 or ordinal == 1:
        return ""
    if meta.slug is not None:
        return f" [slug {meta.slug}]"
    return f" [occurrence {ordinal}]"


def separate_stem(content: ChapterContent) -> str:
    return BaseAdapter.safe_filename(content.heading).rstrip(".") or "untitled"


def remove_orphan_temps(run_dir: Path) -> list[Path]:
    """Delete our own interrupted temp files (``*.part``/``*.tmp`` in ``PDF/``/``TXT/``)."""
    removed = []
    for folder in FORMAT_DIRS.values():
        directory = Path(run_dir) / folder
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.is_file() and path.name.endswith(_TEMP_SUFFIXES):
                try:
                    path.unlink()
                    removed.append(path)
                except OSError:
                    pass
    return removed


class RunOutputs:
    """See the module docstring. ``labels`` are the exact label texts of the selected pages."""

    def __init__(
        self,
        *,
        run_dir: Path,
        journal: RunJournal,
        metas: list[ChapterMeta],
        labels: list[str],
        mode: OutputMode,
        chunk_size: int,
        formats: tuple[str, ...],
        title: str,
        stem: str,
        render: Renderer,
        report: object,
        log: Callable[[str], None],
        replay: Optional[Replay] = None,
        cancel_event: Optional[object] = None,
    ) -> None:
        if not formats or any(f not in FORMATS for f in formats):
            raise ValueError(f"unsupported output formats: {formats!r}")
        self.run_dir = Path(run_dir)
        self.journal = journal
        self.metas = list(metas)
        self.labels = list(labels)
        self.mode = mode
        self.formats = tuple(formats)
        self.title, self.stem = title, stem
        self._render = render
        self.report = report
        self.log = log
        self.groups = plan_groups(len(self.metas), mode, chunk_size)
        self._group_of = {p: g for g in self.groups for p in g.positions}
        self._position = {m.url: i for i, m in enumerate(self.metas)}
        self.cancel_event = cancel_event
        # Content waiting to be published (released once its file is written, so a long run does
        # not keep the whole novel's text in memory), and what the audit needs afterwards.
        self.contents: dict[int, ChapterContent] = {}
        self.obtained: set[int] = set()
        self.titles: dict[int, str] = {}
        self.failed: dict[int, str] = {}
        self.pending: dict[str, set[str]] = {}         # group key -> formats still to publish
        self.resumed_positions: list[int] = []
        # Which group owns which relative path (from earlier sessions and this one).
        self._owner: dict[str, str] = {}
        self._records: dict[tuple[str, str], dict] = dict(replay.publishes) if replay else {}
        for (key, _fmt), record in self._records.items():
            self._owner.setdefault(record["path"].casefold(), key)
        # (ordinal, count) of each page among the pages sharing its displayed label.
        totals = Counter(self.labels)
        seen: Counter = Counter()
        self._ordinal: dict[int, tuple[int, int]] = {}
        for i, label in enumerate(self.labels):
            seen[label] += 1
            self._ordinal[i] = (seen[label], totals[label])

    # ── planning ───────────────────────────────────────────────────────────
    def _verified(self, key: str, fmt: str) -> bool:
        record = self._records.get((key, fmt))
        if record is None or record["missing"]:
            return False
        path = self.run_dir / record["path"]
        try:
            if not path.is_file() or path.is_symlink():
                return False
            size, digest = file_sha256(path)
        except OSError:
            return False
        return size == record["size"] and digest == record["sha256"]

    def plan_fetch(self) -> list[ChapterMeta]:
        """Record already-complete groups as resumed and return the pages still needed."""
        needed: list[ChapterMeta] = []
        for group in self.groups:
            missing = {fmt for fmt in self.formats if not self._verified(group.key, fmt)}
            if not missing:
                self.resumed_positions.extend(group.positions)
                continue
            self.pending[group.key] = missing
            needed.extend(self.metas[p] for p in group.positions)
        return needed

    # ── content ────────────────────────────────────────────────────────────
    def position_of(self, meta: ChapterMeta) -> int:
        return self._position[meta.url]

    def add(self, meta: ChapterMeta, content: ChapterContent) -> None:
        """A page's content is ready; an individual-chapter file is published now."""
        position = self._position[meta.url]
        self.contents[position] = content
        self.obtained.add(position)
        self.titles[position] = content.title
        self.failed.pop(position, None)
        group = self._group_of[position]
        if self.mode is OutputMode.SEPARATE and group.key in self.pending:
            self._publish(group)

    def fail(self, meta: ChapterMeta, reason: str) -> None:
        position = self._position.get(meta.url)
        if position is not None and position not in self.obtained:
            self.failed[position] = str(reason)

    def finish(self, *, cancelled: bool) -> None:
        """Build the grouped files still pending (none after a Stop: Resume builds them), then
        record the pages that could not be saved. Raises ``ScrapeCancelled`` when Stop is pressed
        during the build; every file finished before it is kept and recorded."""
        if cancelled:
            return
        for group in self.groups:
            if group.key not in self.pending:
                continue
            if self.cancel_event is not None and self.cancel_event.is_set():
                from .request_manager import ScrapeCancelled

                raise ScrapeCancelled("stopped while the output files were being built")
            present = [p for p in group.positions if p in self.contents]
            if not present:
                continue
            self._publish(group)
        if not cancelled:
            for position, reason in sorted(self.failed.items()):
                self.journal.failure(position=position, page_url=self.metas[position].url,
                                     label=self.labels[position], reason=reason)

    # ── publication ────────────────────────────────────────────────────────
    def _name(self, group: OutputGroup, fmt: str, contents: list[ChapterContent]) -> str:
        if len(group.positions) == 1 and self.mode is OutputMode.SEPARATE:
            position = group.positions[0]
            ordinal, count = self._ordinal[position]
            stem = separate_stem(contents[0]) + duplicate_suffix(self.metas[position], ordinal, count)
        else:
            first, last = self.labels[group.positions[0]], self.labels[group.positions[-1]]
            stem = f"{self.stem}_Chapters_{first}-{last}"
        relative = f"{FORMAT_DIRS[fmt]}/{stem}.{fmt}"
        owner = self._owner.get(relative.casefold())
        if owner is not None and owner != group.key:
            relative = f"{FORMAT_DIRS[fmt]}/{stem} ({group.key}).{fmt}"
        return relative

    def _publish(self, group: OutputGroup) -> None:
        present = [p for p in group.positions if p in self.contents]
        missing = [p for p in group.positions if p not in self.contents]
        contents = [self.contents[p] for p in present]
        for fmt in self.formats:
            if fmt not in self.pending.get(group.key, ()):
                continue
            relative = self._name(group, fmt, contents)
            path = self.run_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            # A multi-chapter build can take minutes, so it stops at the next chapter on Stop; a
            # single chapter's file is always finished.
            self._render(fmt, contents, path, self.title,
                         cancel_event=self.cancel_event if len(group.positions) > 1 else None)
            size, digest = file_sha256(path)
            record = self.journal.publish(key=group.key, fmt=fmt, path=relative, size=size,
                                          sha256=digest, pages=present, missing=missing)
            self._records[(group.key, fmt)] = record
            self._owner[relative.casefold()] = group.key
            self.report.written.append(path)
            detail = "" if len(contents) == 1 else f" ({len(contents)} chapters)"
            if missing:
                detail += f" — {len(missing)} chapter(s) missing; a resume adds them"
            self.log(f"  wrote {path.name}{detail}")
        if not missing:
            self.pending.pop(group.key, None)
            for position in present:          # published for good: its text is no longer needed
                self.contents.pop(position, None)
        else:
            # Published as far as it goes; still pending, so a resume re-renders it complete.
            self.pending[group.key] = set(self.formats)

    # ── audit ──────────────────────────────────────────────────────────────
    def audit(self) -> dict:
        """Expected versus actual, page by page, with no chapter text: what was selected, which
        file holds it in each format (with that file's checksum), and what is missing and why."""
        holders: dict[tuple[int, str], dict] = {}
        for (key, fmt), record in self._records.items():
            for position in record["pages"]:
                holders[(position, fmt)] = record
        resumed = set(self.resumed_positions)
        rows = []
        for position, meta in enumerate(self.metas):
            outputs = {}
            for fmt in self.formats:
                record = holders.get((position, fmt))
                if record is not None:
                    outputs[fmt] = {"file": record["path"], "sha256": record["sha256"],
                                    "complete_file": not record["missing"]}
            if position in self.failed:
                status = "failed"
            elif len(outputs) == len(self.formats):
                status = "resumed" if position in resumed and position not in self.obtained else "saved"
            else:
                status = "not saved"
            rows.append({
                "position": position,
                "label": self.labels[position],
                "page_url": meta.url,
                "slug": meta.slug,
                "title": self.titles.get(position, meta.title),
                "status": status,
                "reason": self.failed.get(position),
                "outputs": outputs,
            })
        return {"formats": list(self.formats), "grouping": self.mode.value, "chapters": rows}
