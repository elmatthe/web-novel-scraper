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
* **Resume.** A group/format is complete only with current semantic policy, accepted content-event
  references, immutable group membership and intact recorded bytes. Legacy outputs refuse Resume.
  The latest record must lack no page. Complete
  groups are skipped without any network or cache read; accepted durable bodies are reused for
  everything else before fetching missing pages. A group that was published with pages missing is
  re-rendered once they are obtained.
* **Cancel.** Individual chapters and complete chunks are published as they arrive. After Stop no
  further grouped files are built; accepted bodies remain durable for Resume. Stop during a build
  ends the build at the next chapter of the file in progress (v0.3.0 06). Nothing partial is ever
  published by a Stop.
"""

from __future__ import annotations

import re
import hashlib
from contextlib import nullcontext
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .adapters.base import BaseAdapter
from .models import ChapterContent, ChapterMeta, OutputMode
from .run_journal import FORMATS, Replay, RunJournal, JournalError, file_sha256
from .semantic_resume import EXTRACTION_POLICY, content_reference, observed_label
from .content_integrity import BodyFingerprints, FailureInfo, OUTCOME_POLICY, body_fingerprint, classify_failure
from .run_accounting import page_accounting
from .run_storage import checked_output_path
from .pdf_builder import UnsupportedGlyphError, require_glyph_coverage
from .chapter_bodies import load_body, save_body

FORMAT_DIRS = {"pdf": "PDF", "txt": "TXT"}
_OWNED_TEMP = re.compile(r"\.wns-output-[a-z0-9_]{8}\.part")

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
    """Delete only our uniquely prefixed interrupted renders in unlinked format folders.

    Legacy ambiguous .part/.tmp files are deliberately preserved.
    """
    removed = []
    for folder in FORMAT_DIRS.values():
        directory = checked_output_path(run_dir, folder)
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if _OWNED_TEMP.fullmatch(path.name):
                path = checked_output_path(run_dir, f"{folder}/{path.name}")
                if not path.is_file():
                    continue
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
        for folder in FORMAT_DIRS.values():
            checked_output_path(self.run_dir, folder)
        self.journal = journal
        self._semantic = bool(journal and journal.header and journal.header.resume_policy)
        self._conflicted: set[int] = set()
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
        if len(self._position) != len(self.metas):
            repeated = Counter(m.url for m in self.metas)
            raise ValueError("DUPLICATE SOURCE URL in selection: " + "; ".join(
                f"{url} at positions {[p for p, meta in enumerate(self.metas) if meta.url == url]}"
                for url, count in repeated.items() if count > 1))
        self.cancel_event = cancel_event
        # Content waiting to be published (released once its file is written, so a long run does
        # not keep the whole novel's text in memory), and what the audit needs afterwards.
        self.contents: dict[int, ChapterContent] = {}
        self.obtained: set[int] = set()
        self.titles: dict[int, str] = {}
        self.failed: dict[int, str] = {}
        self._attempts = dict(replay.attempts) if replay else {}
        self._content_records = dict(replay.contents) if replay else {}
        self._failure_history = list(replay.failures) if replay else []
        self._explicit_outcomes = replay is None or replay.header.outcome_policy == OUTCOME_POLICY
        self._fingerprints = BodyFingerprints(self._content_records.values())
        self._session_attempted: set[int] = set()
        self._session_contents: set[int] = set()
        self._recorded_failures: dict[tuple, FailureInfo] = {}
        self._deferred_failures: dict[int, FailureInfo] = {}
        self._finished = False
        self.pending: dict[str, set[str]] = {}         # group key -> formats still to publish
        self.resumed_positions: list[int] = []
        # Which group owns which relative path (from earlier sessions and this one).
        self._owner: dict[str, str] = {}
        self._records: dict[tuple[str, str], dict] = dict(replay.publishes) if replay else {}
        self._earlier = bool(self._records)            # files from an earlier session exist
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
    def _intact(self, key: str, fmt: str) -> Optional[dict]:
        """The latest record of this group's file in *fmt*, if that file is still on disk exactly
        as recorded (else ``None``: there is nothing there worth keeping)."""
        record = self._records.get((key, fmt))
        if record is None:
            return None
        path = checked_output_path(self.run_dir, record["path"])
        try:
            if not path.is_file() or path.is_symlink():
                return None
            size, digest = file_sha256(path)
        except OSError:
            return None
        return record if size == record["size"] and digest == record["sha256"] else None

    def _verified(self, key: str, fmt: str) -> bool:
        record = self._intact(key, fmt)
        return self._semantic and record is not None and "certificate" in record and not record["missing"]

    def plan_fetch(self) -> list[ChapterMeta]:
        """Record already-complete groups as resumed and return the pages still needed."""
        needed: list[ChapterMeta] = []
        self.resumed_positions.clear()
        self.pending.clear()
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

    def restore_body(self, meta: ChapterMeta) -> bool:
        """Reuse only canonical content bound to the admitted run's journal."""
        position = self.position_of(meta)
        record = self._content_records.get(position)
        content = load_body(self.run_dir, record) if record else None
        if content is None:
            return False
        self.contents[position] = content
        self.obtained.add(position)
        self.titles[position] = content.title
        return True

    def attempt(self, meta: ChapterMeta) -> None:
        position = self.position_of(meta)
        if position not in self._session_attempted:
            self._attempts[position] = self.journal.attempt(
                position=position, page_url=meta.url, label=self.labels[position])
            self._session_attempted.add(position)

    def add(self, meta: ChapterMeta, content: ChapterContent) -> None:
        """A page's content is ready; an individual-chapter file is published now."""
        position = self._position[meta.url]
        self.attempt(meta)
        if not content.paragraphs or not any(paragraph.strip() for paragraph in content.paragraphs):
            self.fail(meta, FailureInfo("EMPTY_BODY", "The adapter returned no usable canonical story paragraphs."))
            return
        observed = observed_label(content)
        if self._semantic and observed != self.labels[position]:
            self.fail(meta, FailureInfo("IDENTITY_MISMATCH", "Accepted content differs from the exact selected label.", (observed,)))
            return
        previous = self._content_records.get(position)
        candidate_digest = body_fingerprint(content.paragraphs)[0]
        if self._semantic and previous and previous["body_sha256"] != candidate_digest:
            self.journal.append("content_conflict", position=position, page_url=meta.url,
                                label=self.labels[position], accepted=content_reference(previous),
                                observed_body_sha256=candidate_digest)
            self._conflicted.add(position)
            self.fail(meta, FailureInfo("CONTENT_CONFLICT", "Canonical body changed since acceptance; previous publications preserved. Start a new run."))
            return
        fingerprint = self._fingerprints.observe(position, content.paragraphs)
        if position not in self._session_contents or previous is None or previous["body_sha256"] != fingerprint["body_sha256"]:
            artifact = save_body(self.run_dir, position, meta, content) if self._semantic else None
            self._content_records[position] = self.journal.content(
                position=position, page_url=meta.url, label=self.labels[position], **fingerprint,
                **({"extraction_policy": EXTRACTION_POLICY, "observed_label": observed,
                    "body_artifact": artifact} if self._semantic else {}))
            other = fingerprint["duplicate_of"]
            if other is not None:
                same_label = self.labels[other] == self.labels[position]
                kind = "same label, different URLs" if same_label else "different labels, identical canonical body"
                message = (f"DUPLICATE CONTENT: {kind}: position {other} chapter {self.labels[other]} "
                           f"({self.metas[other].url}) and position {position} chapter {self.labels[position]} "
                           f"({meta.url}); {fingerprint['body_sha256']}. Suspicion only; both pages are retained.")
                self.report.warnings.append(message)
                self.log(message)
        self._session_contents.add(position)
        self._deferred_failures.pop(position, None)
        self.contents[position] = content
        self.obtained.add(position)
        self.titles[position] = content.title
        self.failed.pop(position, None)
        group = self._group_of[position]
        if self.mode is OutputMode.SEPARATE and group.key in self.pending:
            self._publish(group)
        elif (self.mode is OutputMode.CHUNKED and group.key in self.pending
              and not (self.cancel_event is not None and self.cancel_event.is_set())
              and all(p in self.contents for p in group.positions)):
            self._publish(group)

    def fail(self, meta: ChapterMeta, reason) -> None:
        position = self._position.get(meta.url)
        if position is None:
            return
        self._deferred_failures.pop(position, None)
        info = classify_failure(reason)
        self.attempt(meta)
        self.failed[position] = info.reason
        if meta.index not in self.report.failed:
            self.report.failed.append(meta.index)
        if info.reason_code == "EMPTY_BODY" and meta.index not in self.report.extraction_failed:
            self.report.extraction_failed.append(meta.index)
        key = (position, info.stage, info.format)
        if self._recorded_failures.get(key) != info:
            record = self.journal.failure(position=position, page_url=meta.url,
                                          label=self.labels[position], **info.fields())
            self._failure_history.append(record)
            self._recorded_failures[key] = info

    def defer_failure(self, meta, error) -> None:
        """A real primary response failed but recovery is still pending, not a new page."""
        self._deferred_failures[self.position_of(meta)] = classify_failure(error)

    def flush_deferred_failures(self) -> None:
        for position, info in list(self._deferred_failures.items()):
            self.fail(self.metas[position], info)

    def publish_ready(self) -> None:
        """Publish complete groups, including groups restored from durable bodies."""
        for group in self.groups:
            if self.cancel_event is not None and self.cancel_event.is_set():
                return
            if group.key in self.pending and all(p in self.contents for p in group.positions):
                self._publish(group)

    def finish(self, *, cancelled: bool) -> None:
        """Build the grouped files still pending (none after a Stop: Resume builds them), then
        record the pages that could not be saved. Raises ``ScrapeCancelled`` when Stop is pressed
        during the build; every file finished before it is kept and recorded."""
        if self._finished:
            return
        self._finished = True
        self.flush_deferred_failures()
        if cancelled:
            return  # known failures were already durably appended by fail(), with no rendering
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
            self._keep_saved_failures()

    def _keep_saved_failures(self) -> None:
        """A chapter that failed this session but is still in an earlier session's intact file in
        every format is saved. The fetch failure stays in journal history; it does not
        negate the preserved artifact's publication outcome."""
        if not self._earlier or not self.failed:
            return                                  # only an earlier session's file can hold one
        held: dict[tuple[str, str], set] = {}

        def holds(position: int, fmt: str) -> bool:
            key = self._group_of[position].key
            if (key, fmt) not in held:              # each file is checked once
                held[(key, fmt)] = set((self._intact(key, fmt) or {}).get("pages", ()))
            return position in held[(key, fmt)]

        kept = [p for p in sorted(self.failed)
                if not any(position == p and stage == "render" for position, stage, _fmt in self._recorded_failures)
                and all(holds(p, fmt) for fmt in self.formats)]
        if not kept:
            return
        for position in kept:
            del self.failed[position]
            index = self.metas[position].index
            if index in self.report.failed:
                self.report.failed.remove(index)
        labels = ", ".join(self.labels[p] for p in kept[:20]) + (" …" if len(kept) > 20 else "")
        self.log(f"  {len(kept)} chapter(s) could not be fetched again but are still in the files "
                 f"saved earlier, which were kept: {labels}")

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
        if any(p in self._conflicted for p in group.positions):
            self.log("CONTENT_CONFLICT: group preserved; start a new run after reviewing changed source text.")
            return
        present = [p for p in group.positions if p in self.contents]
        missing = [p for p in group.positions if p not in self.contents]
        contents = [self.contents[p] for p in present]
        formats = list(self.formats)
        if "pdf" in self.pending.get(group.key, ()):
            try:
                require_glyph_coverage(contents)
            except UnsupportedGlyphError as exc:
                self._render_failure(present, "pdf", exc, glyph=True)
                formats = [f for f in self.formats if f != "pdf"]
                if "txt" not in formats:
                    formats.append("txt")
                self.pending[group.key].add("txt")
        for fmt in formats:
            if fmt not in self.pending.get(group.key, ()):
                continue
            # Never replace an earlier file with one that lacks a chapter it holds (a resume that
            # could not fetch every page again): keep it, and leave the group for a later resume.
            earlier = self._intact(group.key, fmt) if missing else None
            lost = sorted(set(earlier["pages"]) - set(present)) if earlier else []
            if lost:
                labels = ", ".join(self.labels[p] for p in lost[:20]) + (" …" if len(lost) > 20 else "")
                self.log(f"  kept {Path(earlier['path']).name} as it was: it holds chapter(s) {labels} "
                         "that could not be fetched again; Resume Existing Run tries again later")
                continue
            relative = self._name(group, fmt, contents)
            path = checked_output_path(self.run_dir, relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            checked_output_path(self.run_dir, relative)
            # A multi-chapter build can take minutes, so it stops at the next chapter on Stop; a
            # single chapter's file is always finished.
            try:
                metrics = getattr(self.report, "_performance", None)
                with metrics.measure("rendering_seconds") if metrics else nullcontext():
                    started = metrics.clock() if metrics else None
                    self._render(fmt, contents, path, self.title,
                                 cancel_event=self.cancel_event if len(group.positions) > 1 else None)
                if metrics:
                    self.log(f"  {fmt.upper()} rendering time: {metrics.clock() - started:.3f}s ({path.name})")
                if self._semantic and fmt == "pdf":
                    from pypdf import PdfReader
                    texts = [page.extract_text() or "" for page in PdfReader(path, strict=True).pages]
                    expected = "".join("".join((c.heading, *c.paragraphs)) for c in contents)
                    if (not texts or any(not text.strip() for text in texts)
                            or "".join("".join(texts).split()) != "".join(expected.split())):
                        raise ValueError("PDF canonical text/sections/order mismatch or blank page; no certificate issued")
            except Exception as exc:
                from .request_manager import ScrapeCancelled
                if isinstance(exc, ScrapeCancelled):
                    raise
                self._render_failure(present, fmt, exc, glyph=isinstance(exc, UnsupportedGlyphError))
                if isinstance(exc, OSError):
                    raise  # retain fatal filesystem/publication failure semantics after durable evidence
                if fmt == "pdf" and isinstance(exc, UnsupportedGlyphError):
                    if "txt" not in formats:
                        formats.append("txt")
                    self.pending[group.key].add("txt")
                continue
            checked_output_path(self.run_dir, relative)
            size, digest = file_sha256(path)
            if self._semantic:
                if len(contents) != len(present) or any(
                    body_fingerprint(content.paragraphs) != (
                        self._content_records[p]["body_sha256"], self._content_records[p]["body_bytes"],
                        self._content_records[p]["paragraph_count"])
                    or observed_label(content) != self.labels[p]
                    for p, content in zip(present, contents)
                ):
                    raise JournalError("Renderer input changed from accepted canonical content; no certificate issued.")
            if self._semantic and fmt == "txt":
                from .txt_builder import chapters_to_txt
                expected = chapters_to_txt(contents).encode("utf-8")
                if (size, digest) != (len(expected), hashlib.sha256(expected).hexdigest()):
                    raise JournalError("TXT publication differs from the accepted canonical content; no certificate issued.")
            record = self.journal.publish(key=group.key, fmt=fmt, path=relative, size=size,
                                          sha256=digest, pages=present, missing=missing,
                                          contents=self._content_records,
                                          **({"fallback_for": "pdf"} if fmt not in self.formats else {}))
            self._records[(group.key, fmt)] = record
            self.pending[group.key].discard(fmt)
            self._owner[relative.casefold()] = group.key
            self.report.written.append(path)
            detail = "" if len(contents) == 1 else f" ({len(contents)} chapters)"
            if missing:
                detail += f" — {len(missing)} chapter(s) missing; a resume adds them"
            self.log(f"  wrote {path.name}{detail}")
        if not missing:
            if not self.pending.get(group.key):
                self.pending.pop(group.key, None)
            for position in present:          # published for good: its text is no longer needed
                self.contents.pop(position, None)
        else:
            # Published as far as it goes; still pending, so a resume re-renders it complete.
            self.pending[group.key] = set(self.formats)

    def _render_failure(self, positions, fmt, error, *, glyph=False):
        info = classify_failure(error, stage="render", fmt=fmt,
                                code="PDF_GLYPH_UNSUPPORTED" if glyph else "RENDER_ERROR")
        for position in positions:
            self.fail(self.metas[position], info)
        message = f"{fmt.upper()} failed [{info.reason_code}]: {info.reason}."
        if glyph:
            message += " Original Unicode is preserved for TXT publication; the requested PDF remains unfinished."
        self.report.warnings.append(message)
        self.log(f"WARNING: {message}")

    # ── audit ──────────────────────────────────────────────────────────────
    def audit(self) -> dict:
        """Expected versus actual, page by page, with no chapter text: what was selected, which
        file holds it in each format (with that file's checksum), and what is missing and why."""
        intact = {key: record for key in self._records
                  if (record := self._intact(*key)) is not None}
        outcomes, accounting = page_accounting(len(self.metas), self.formats, self._attempts,
            self._content_records, self._failure_history, intact, explicit=self._explicit_outcomes)
        self.report.accounting = accounting
        resumed = set(self.resumed_positions)
        rows = []
        for position, meta in enumerate(self.metas):
            outputs = {}
            outcome = outcomes[position]
            for fmt, record in outcome["outputs"].items():
                outputs[fmt] = {"file": record["path"], "sha256": record["sha256"],
                                "complete_file": not record["missing"]}
            if all(state == "success" for state in outcome["requested_format_outcomes"].values()):
                status = "resumed" if position in resumed and position not in self.obtained else "saved"
            elif position in self.failed:
                status = "failed"
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
                "attempted": outcome["attempted"],
                "processed": outcome["processed"],
                "requested_format_outcomes": outcome["requested_format_outcomes"],
                "fallback_formats": outcome["fallback_formats"],
                "body_evidence": outcome["content"],
                "semantic_status": ("INCOMPATIBLE" if position in self._conflicted else
                                    "LEGACY UNVERIFIED" if not self._semantic else
                                    "SUSPICIOUS" if outcome["content"] and outcome["content"].get("duplicate_of") is not None else
                                    "CERTIFIED" if outputs and all(not r["missing"] for r in outcome["outputs"].values())
                                    and all(f in outputs for f in self.formats) else "INCOMPLETE"),
            })
        return {"formats": list(self.formats), "grouping": self.mode.value, "chapters": rows,
                "accounting": accounting, "failures": self._failure_history,
                "integrity_diagnostics": [r for r in self._content_records.values() if r.get("duplicate_of") is not None]}
