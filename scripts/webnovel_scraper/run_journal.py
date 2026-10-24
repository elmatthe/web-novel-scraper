"""Append-only, checksummed run journal and the fixed selection of a run (v0.3.0 03.3, plan §9.5).

A run folder ``<root>/<novel-slug>-N`` holds, next to its outputs:

* ``run-reservation.json`` - written by ``run_storage.allocate_run_dir`` (who reserved the folder);
* ``run-selection.json``   - the run's chapter pages, in order, exactly as selected when it began;
* ``run-journal.jsonl``    - one header line, then one line per event, each with a CRC;
* ``run.lock``             - held while a run (new or resumed) is working in the folder.

The selection is written once and never changed: a resumed run fetches exactly the pages the run
started with, whatever the site's chapter list says today. Its SHA-256 is in the journal header,
so an edited or swapped selection is refused.

Journal lines are ``{"seq": n, "type": ..., ..., "crc": "xxxxxxxx"}`` where ``crc`` is CRC-32 over
the canonical JSON of the line without it, ``seq`` counts from 0 (the header) without gaps, and
every line is flushed and ``fsync``-ed before the call returns. A crash can only tear the *last*
line, which replay ignores (and a resume trims before appending). A bad line anywhere else, a gap
in ``seq`` or a header that does not describe this folder makes the journal unusable: Resume
refuses and a new run is the remedy.

Event types: ``publish`` (actual format, path, file hash and held/missing positions),
``failure`` (legacy reason or typed fetch/render outcome), ``attempt`` (a selected
page was attempted), ``content`` (compact canonical-body fingerprint), ``resume``
and ``end``. CP4 page events use event_version=1 and immutable position/URL/label;
new readers retain old journals, while older readers refuse unknown event types.
No event contains chapter prose. A TXT fallback is a publish with fallback_for=pdf,
alongside the requested PDF failure, never an extra selected page.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .atomic_io import write_json_atomic
from .source_identity import ChapterIdentity, ChapterLabel, SourceIdentity, SourceIdentityError
from .content_integrity import BODY_POLICY, OUTCOME_POLICY, REASON_CODES

JOURNAL_FILENAME = "run-journal.jsonl"
SELECTION_FILENAME = "run-selection.json"
LOCK_FILENAME = "run.lock"
JOURNAL_KIND = "web-novel-scraper-run-journal"
SELECTION_KIND = "web-novel-scraper-run-selection"
SCHEMA_VERSION = 1
APP_VERSION = "0.3.0"
GROUPING_MODES = ("separate", "chunked", "single")
FORMATS = ("pdf", "txt")
END_STATES = ("complete", "complete_with_problems", "cancelled", "failed")
# A selection or journal larger than this is not something this app wrote.
MAX_FILE_BYTES = 64 * 1024 * 1024
_HEX64 = re.compile(r"[0-9a-f]{64}")
_REL_PATH = re.compile(r"(?:PDF|TXT)/[^/\\:*?\"<>|\x00-\x1f]{1,250}\.(?:pdf|txt)")


class JournalError(RuntimeError):
    """The run's journal or selection cannot be trusted (the message says why)."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def record_crc(record: dict) -> str:
    """CRC-32 (8 hex digits) over a record's canonical JSON without its ``crc`` field."""
    body = {k: v for k, v in record.items() if k != "crc"}
    return f"{zlib.crc32(_canonical(body)) & 0xFFFFFFFF:08x}"


def selection_sha256(chapters: Iterable[ChapterIdentity]) -> str:
    return hashlib.sha256(_canonical([c.to_json() for c in chapters])).hexdigest()


def file_sha256(path: Path) -> tuple[int, str]:
    """``(size, sha256)`` of a file, read in blocks."""
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest()


# ── header ───────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class RunHeader:
    """What a run is: which source, which pages, which outputs. Fixed for the run's life."""

    novel_slug: str
    run_number: int
    source: SourceIdentity
    selection_sha256: str
    selection_count: int
    first_label: str
    last_label: str
    requested: tuple[int, int]
    effective: tuple[int, int]
    formats: tuple[str, ...]
    grouping_mode: str
    chunk_size: int
    manifest_sha256: Optional[str] = None
    notes: tuple[str, ...] = ()
    app_version: str = APP_VERSION
    created_utc: str = ""
    outcome_policy: Optional[str] = None
    resume_policy: Optional[str] = None
    extraction_policy: Optional[str] = None
    toc_gaps: Optional[tuple[int, ...]] = None  # None: legacy header without typed coverage

    def __post_init__(self) -> None:
        if self.grouping_mode not in GROUPING_MODES:
            raise JournalError(f"Unknown grouping mode {self.grouping_mode!r}.")
        if not self.formats or any(f not in FORMATS for f in self.formats) or len(set(self.formats)) != len(self.formats):
            raise JournalError(f"Invalid output formats {self.formats!r}.")
        if type(self.chunk_size) is not int or self.chunk_size < 1:
            raise JournalError(f"Invalid chunk size {self.chunk_size!r}.")
        if not _HEX64.fullmatch(self.selection_sha256 or ""):
            raise JournalError("The run's selection checksum is malformed.")
        if self.source.novel_slug != self.novel_slug:
            raise JournalError("The run's source belongs to another novel.")
        if self.outcome_policy not in (None, OUTCOME_POLICY):
            raise JournalError("The run's outcome policy is unsupported.")
        if self.toc_gaps is not None and any(type(n) is not int or n < 1 for n in self.toc_gaps):
            raise JournalError("The run's source-list gaps are malformed.")

    def to_json(self) -> dict:
        return {
            "kind": JOURNAL_KIND,
            "schema_version": SCHEMA_VERSION,
            "run": {"novel_slug": self.novel_slug, "number": self.run_number},
            "source": self.source.to_json(),
            "source_fingerprint": self.source.fingerprint(),
            "selection": {
                "sha256": self.selection_sha256, "count": self.selection_count,
                "first_label": self.first_label, "last_label": self.last_label,
                "requested": list(self.requested), "effective": list(self.effective),
                "toc_gaps": list(self.toc_gaps) if self.toc_gaps is not None else None,
            },
            "formats": list(self.formats),
            "grouping": {"mode": self.grouping_mode, "chunk_size": self.chunk_size},
            "manifest": {"content_sha256": self.manifest_sha256},
            "notes": list(self.notes),
            "app_version": self.app_version,
            "created_utc": self.created_utc,
            "outcome_policy": self.outcome_policy,
            "resume_policy": self.resume_policy,
            "extraction_policy": self.extraction_policy,
        }

    @classmethod
    def from_json(cls, raw: dict) -> "RunHeader":
        try:
            if raw.get("kind") != JOURNAL_KIND or raw.get("schema_version") != SCHEMA_VERSION:
                raise JournalError("The journal was not written by this version of the app.")
            source = SourceIdentity.from_json(raw["source"])
            if raw.get("source_fingerprint") != source.fingerprint():
                raise JournalError("The journal's source record does not match its fingerprint.")
            sel, grouping = raw["selection"], raw["grouping"]
            return cls(
                novel_slug=raw["run"]["novel_slug"], run_number=int(raw["run"]["number"]),
                source=source, selection_sha256=sel["sha256"], selection_count=int(sel["count"]),
                first_label=str(sel["first_label"]), last_label=str(sel["last_label"]),
                requested=tuple(int(v) for v in sel["requested"]),
                effective=tuple(int(v) for v in sel["effective"]),
                formats=tuple(raw["formats"]), grouping_mode=grouping["mode"],
                chunk_size=grouping["chunk_size"], manifest_sha256=raw["manifest"]["content_sha256"],
                notes=tuple(str(n) for n in raw.get("notes", ())), app_version=str(raw["app_version"]),
                created_utc=str(raw["created_utc"]),
                outcome_policy=raw.get("outcome_policy"),
                resume_policy=raw.get("resume_policy"),
                extraction_policy=raw.get("extraction_policy"),
                toc_gaps=tuple(sel["toc_gaps"]) if sel.get("toc_gaps") is not None else None,
            )
        except JournalError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError, SourceIdentityError) as exc:
            raise JournalError(f"The journal header is malformed ({exc}).") from exc


def source_list_gaps(header: RunHeader, chapters: list[ChapterIdentity]) -> list[int]:
    """Restore typed coverage; old headers recorded a specific gap note instead."""
    if header.toc_gaps is not None:
        return list(header.toc_gaps)
    if not any("chapter number(s) inside the range are not on the site's chapter list" in n
               for n in header.notes):
        return []
    selected = {c.label.value for c in chapters}
    return [n for n in range(header.effective[0], header.effective[1] + 1) if n not in selected]


# ── replay ───────────────────────────────────────────────────────────────────
@dataclass
class Replay:
    header: RunHeader
    publishes: dict = field(default_factory=dict)    # (group key, format) -> latest publish record
    failures: list = field(default_factory=list)
    attempts: dict = field(default_factory=dict)    # position -> latest explicit attempt
    contents: dict = field(default_factory=dict)    # position -> latest compact body observation
    conflicts: list = field(default_factory=list)
    semantic_groups: Optional[dict] = None
    publication_owners: dict = field(default_factory=dict)
    ends: list = field(default_factory=list)
    sessions: int = 1
    next_seq: int = 1
    good_length: int = 0                              # bytes up to the last intact line
    torn_tail: bool = False

    @property
    def last_end(self) -> Optional[str]:
        return self.ends[-1]["state"] if self.ends else None


def _check_publish(record: dict, count: int) -> None:
    for name in ("key", "format", "path", "size", "sha256", "pages", "missing"):
        if name not in record:
            raise JournalError(f"a publish record lacks {name!r}")
    if not isinstance(record["key"], str):
        raise JournalError("a publish record has a malformed group key")
    if not isinstance(record["format"], str) or record["format"] not in FORMATS or not isinstance(record["path"], str) or not _REL_PATH.fullmatch(record["path"]):
        raise JournalError("a publish record names an unexpected file")
    if not record["path"].upper().startswith(record["format"].upper() + "/"):
        raise JournalError("a publish record's file is in the wrong format folder")
    if not record["path"].lower().endswith("." + record["format"]):
        raise JournalError("a publish record's actual file extension disagrees with its format")
    if record.get("fallback_for") is not None and (record["fallback_for"] != "pdf" or record["format"] != "txt"):
        raise JournalError("a publish record has an unsupported fallback format")
    if type(record["size"]) is not int or record["size"] < 0 or not _HEX64.fullmatch(str(record["sha256"])):
        raise JournalError("a publish record has a malformed checksum")
    for name in ("pages", "missing"):
        values = record[name]
        if not isinstance(values, list) or any(type(v) is not int or not 0 <= v < count for v in values):
            raise JournalError(f"a publish record has malformed {name}")


def _check_page_event(record: dict, header: RunHeader, chapters: list[ChapterIdentity]) -> None:
    position = record.get("position")
    if type(position) is not int or not 0 <= position < header.selection_count:
        raise JournalError("a page event has an invalid selection position")
    chapter = chapters[position]
    if record.get("label") != chapter.label.text or record.get("page_url") != chapter.page_url:
        raise JournalError("a page event does not match its immutable selected identity")
    kind = record["type"]
    if kind in ("attempt", "content") and (type(record.get("event_version")) is not int or record["event_version"] != 1):
        raise JournalError("a page event has an unsupported version")
    if kind == "failure":
        if not isinstance(record.get("reason_code"), str) or record["reason_code"] not in REASON_CODES or record.get("stage") not in ("fetch", "render"):
            raise JournalError("a typed failure has an unsupported reason or stage")
        if not isinstance(record.get("reason"), str) or len(record["reason"]) > 1000:
            raise JournalError("a typed failure has an invalid reason")
        fmt = record.get("format")
        if fmt is not None and fmt not in FORMATS:
            raise JournalError("a typed failure has an unsupported format")
        if record["stage"] == "render" and fmt not in FORMATS:
            raise JournalError("a render failure must identify its format")
        if record["stage"] == "fetch" and fmt is not None:
            raise JournalError("a fetch failure cannot identify an output format")
        if record["reason_code"].startswith("IDENTITY_") and record["stage"] != "fetch":
            raise JournalError("identity failure is a fetch/extraction outcome")
        if record["reason_code"] == "PDF_GLYPH_UNSUPPORTED" and (record["stage"], fmt) != ("render", "pdf"):
            raise JournalError("a glyph failure must identify PDF rendering")
        labels = record.get("observed_labels")
        if not isinstance(labels, list) or len(labels) > 20:
            raise JournalError("a typed failure has invalid observed labels")
        try:
            for label in labels:
                ChapterLabel(label)
        except SourceIdentityError as exc:
            raise JournalError(str(exc)) from exc
        if record.get("http_status") is not None and (type(record["http_status"]) is not int or not 100 <= record["http_status"] <= 599):
            raise JournalError("a typed failure has an invalid HTTP status")
    elif kind == "content":
        if record.get("body_policy") != BODY_POLICY or not isinstance(record.get("body_sha256"), str) or not _HEX64.fullmatch(record["body_sha256"]):
            raise JournalError("a content observation has an invalid fingerprint policy/hash")
        if any(type(record.get(k)) is not int or record[k] <= 0 for k in ("body_bytes", "paragraph_count")):
            raise JournalError("a content observation has invalid body dimensions")
        other = record.get("duplicate_of")
        if other is not None and (type(other) is not int or not 0 <= other < header.selection_count or other == position):
            raise JournalError("a duplicate-body observation has an invalid referenced position")


def replay_journal(path: Path) -> Replay:
    """Read and verify a journal. Raises :class:`JournalError` if it cannot be trusted."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise JournalError("The run journal is unexpectedly large.")
        data = path.read_bytes()
    except FileNotFoundError:
        raise JournalError("This folder has no run journal, so it cannot be resumed.") from None
    except OSError as exc:
        raise JournalError(f"The run journal could not be read ({exc}).") from exc
    lines = data.split(b"\n")
    complete = lines[:-1]                  # every line that ended with a newline
    tail = lines[-1]                       # b"" when the file ends with a newline
    replay: Optional[Replay] = None
    event_chapters = None
    offset = 0
    for number, raw_line in enumerate(complete):
        is_last = number == len(complete) - 1 and not tail
        try:
            record = json.loads(raw_line.decode("utf-8"))
            if not isinstance(record, dict) or record.get("crc") != record_crc(record):
                raise ValueError("checksum mismatch")
            if type(record.get("seq")) is not int or record["seq"] != number:
                raise ValueError("sequence gap")
        except (ValueError, UnicodeDecodeError) as exc:
            if is_last:                    # a torn final line: ignore it, resume trims it
                if replay is None:
                    break
                replay.torn_tail = True
                break
            raise JournalError(f"Line {number + 1} of the run journal is damaged ({exc}).") from None
        if number == 0:
            if record.get("type") != "header":
                raise JournalError("The run journal does not start with a header.")
            replay = Replay(header=RunHeader.from_json(record))
        else:
            assert replay is not None
            kind = record.get("type")
            from .semantic_resume import check_event
            if kind in ("attempt", "content") or (kind == "failure" and "reason_code" in record):
                if event_chapters is None:
                    event_chapters = load_selection(path.parent, replay.header)
                _check_page_event(record, replay.header, event_chapters)
            if kind == "publish":
                try:
                    _check_publish(record, replay.header.selection_count)
                except JournalError as exc:
                    raise JournalError(f"Line {number + 1} of the run journal: {exc}.") from None
                if record.get("fallback_for") and (record["fallback_for"] not in replay.header.formats or record["format"] in replay.header.formats):
                    raise JournalError("a fallback publication must preserve an unrequested format for a failed requested format")
                check_event(replay, record)
                replay.publishes[(record["key"], record["format"])] = record
            elif kind == "attempt":
                replay.attempts[record["position"]] = record
            elif kind == "content":
                check_event(replay, record)
                other = record.get("duplicate_of")
                if other is not None and (replay.contents.get(other, {}).get("body_sha256") != record["body_sha256"]):
                    raise JournalError("a duplicate-body observation does not reference matching earlier evidence")
                replay.contents[record["position"]] = record
            elif kind == "content_conflict":
                if event_chapters is None:
                    event_chapters = load_selection(path.parent, replay.header)
                _check_page_event(record, replay.header, event_chapters)
                check_event(replay, record)
                replay.conflicts.append(record)
            elif kind == "failure":
                replay.failures.append(record)
            elif kind == "end":
                if record.get("state") not in END_STATES:
                    raise JournalError(f"Line {number + 1} of the run journal has an unknown end state.")
                replay.ends.append(record)
            elif kind == "resume":
                replay.sessions += 1
            else:
                raise JournalError(f"Line {number + 1} of the run journal has an unknown type {kind!r}.")
        offset += len(raw_line) + 1
        replay.next_seq = number + 1
        replay.good_length = offset
    if replay is None:
        raise JournalError("The run journal has no readable header.")
    if tail:                               # bytes after the last newline: a torn append
        replay.torn_tail = True
    return replay


# ── the journal ──────────────────────────────────────────────────────────────
# Binary mode on Windows: without it ``os.write`` turns each b"\n" into b"\r\n".
_O_BINARY = getattr(os, "O_BINARY", 0)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


class RunJournal:
    """Writes one run folder's journal. Create a new run with :meth:`create`, continue one with
    :meth:`resume`. Not thread-safe by itself; the pipeline writes from one thread."""

    def __init__(self, run_dir: Path, next_seq: int, clock=_utc_now) -> None:
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / JOURNAL_FILENAME
        self._seq = next_seq
        self._clock = clock
        self._broken = False        # a failed write could not be cut back off the end of the file
        self.header = None

    # -- creation -----------------------------------------------------------
    @classmethod
    def create(
        cls, run_dir: Path, header: RunHeader, chapters: list[ChapterIdentity], *, clock=_utc_now
    ) -> "RunJournal":
        """Write the selection, then the journal header. Refuses a folder that already has either."""
        run_dir = Path(run_dir)
        journal_path, selection_path = run_dir / JOURNAL_FILENAME, run_dir / SELECTION_FILENAME
        if journal_path.exists() or selection_path.exists():
            raise JournalError(
                f"{run_dir} already holds a run. Use Resume Existing Run for it, or start a new run."
            )
        if selection_sha256(chapters) != header.selection_sha256 or len(chapters) != header.selection_count:
            raise JournalError("The selection does not match the run header.")
        write_json_atomic(selection_path, {
            "kind": SELECTION_KIND,
            "schema_version": SCHEMA_VERSION,
            "source": header.source.to_json(),
            "sha256": header.selection_sha256,
            "chapters": [c.to_json() for c in chapters],
        })
        record = {"seq": 0, "type": "header", **header.to_json()}
        record["crc"] = record_crc(record)
        fd = os.open(journal_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY, 0o644)
        try:
            _write_all(fd, _canonical(record) + b"\n")
            os.fsync(fd)
        finally:
            os.close(fd)
        journal = cls(run_dir, 1, clock)
        journal.header = header
        return journal

    @classmethod
    def resume(cls, run_dir: Path, replay: Replay, *, clock=_utc_now) -> "RunJournal":
        """Continue a verified journal: a torn last line is trimmed first, so the next record
        follows the last intact one."""
        journal = cls(run_dir, replay.next_seq, clock)
        journal.header = replay.header
        if replay.header.resume_policy is not None:
            from .semantic_resume import validate_admission
            validate_admission(replay)
        if replay.torn_tail:
            with open(journal.path, "r+b") as handle:
                handle.truncate(replay.good_length)
                handle.flush()
                os.fsync(handle.fileno())
        return journal

    # -- appending ----------------------------------------------------------
    def append(self, kind: str, **fields: object) -> dict:
        if self._broken:
            raise OSError("the run journal is not written to after a failed write it could not undo")
        record = {"seq": self._seq, "type": kind, "utc": self._clock(), **fields}
        record["crc"] = record_crc(record)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | _O_BINARY)
        try:
            start = os.lseek(fd, 0, os.SEEK_END)
            try:
                _write_all(fd, _canonical(record) + b"\n")
                os.fsync(fd)
            except BaseException:
                # A part-written line followed by another record would sit mid-file, where replay
                # refuses it as damage; cut it off so the journal still ends at a whole record.
                try:
                    os.ftruncate(fd, start)
                    os.fsync(fd)
                except OSError:
                    self._broken = True
                raise
        finally:
            os.close(fd)
        self._seq += 1
        return record

    def publish(self, *, key: str, fmt: str, path: str, size: int, sha256: str,
                pages: list[int], missing: list[int], fallback_for: Optional[str] = None,
                contents: Optional[dict] = None) -> dict:
        fields = dict(key=key, format=fmt, path=path, size=size, sha256=sha256,
                      pages=list(pages), missing=list(missing),
                      **({"fallback_for": fallback_for} if fallback_for else {}))
        if self.header is not None and self.header.resume_policy is not None:
            from .semantic_resume import publication_certificate
            fields["certificate"] = publication_certificate(self.header, fields, contents or {})
        return self.append("publish", **fields)

    def failure(self, *, position: int, page_url: str, label: str, reason: str, **details) -> dict:
        return self.append("failure", position=position, page_url=page_url, label=label,
                           reason=str(reason)[:1000], **details)

    def attempt(self, *, position: int, page_url: str, label: str) -> dict:
        return self.append("attempt", event_version=1, position=position, page_url=page_url, label=label)

    def content(self, *, position: int, page_url: str, label: str, **fingerprint) -> dict:
        return self.append("content", event_version=1, position=position, page_url=page_url, label=label, **fingerprint)

    def resumed(self) -> dict:
        return self.append("resume")

    def end(self, state: str, **counts: object) -> dict:
        if state not in END_STATES:
            raise ValueError(f"unknown end state {state!r}")
        return self.append("end", state=state, **counts)


# ── the selection file ───────────────────────────────────────────────────────
def load_selection(run_dir: Path, header: RunHeader) -> list[ChapterIdentity]:
    """The run's pages, verified against the header (same source, same checksum)."""
    path = Path(run_dir) / SELECTION_FILENAME
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise JournalError("The run's selection file is unexpectedly large.")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise JournalError("The run's selection file is missing.") from None
    except (OSError, ValueError) as exc:
        raise JournalError(f"The run's selection file is unreadable ({exc}).") from exc
    try:
        if raw.get("kind") != SELECTION_KIND or raw.get("schema_version") != SCHEMA_VERSION:
            raise JournalError("The run's selection file is not from this version of the app.")
        if SourceIdentity.from_json(raw["source"]) != header.source:
            raise JournalError("The run's selection file belongs to another source.")
        chapters = [ChapterIdentity.from_json(header.source, row) for row in raw["chapters"]]
    except JournalError:
        raise
    except (KeyError, TypeError, AttributeError, SourceIdentityError) as exc:
        raise JournalError(f"The run's selection file is malformed ({exc}).") from exc
    if selection_sha256(chapters) != header.selection_sha256 or raw.get("sha256") != header.selection_sha256:
        raise JournalError("The run's selection file was changed since the run began (checksum mismatch).")
    if len(chapters) != header.selection_count or len({c.page_url for c in chapters}) != len(chapters):
        raise JournalError("The run's selection file is inconsistent.")
    if not chapters or (chapters[0].label.text, chapters[-1].label.text) != (header.first_label, header.last_label):
        raise JournalError("The run's selection boundaries disagree with its exact labels.")
    return chapters
