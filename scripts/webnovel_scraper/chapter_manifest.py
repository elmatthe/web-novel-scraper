"""Chapter manifest schema, serialization and strict validation (v0.3.0 Checkpoint 03.1).

One schema serves both layers (plan §9.2): a *baseline* manifest tracked in the repository
and a mutable *runtime* overlay. This module is pure: no file, network or catalog access.
Reading, writing, merging and migration live in :mod:`chapter_store`.

A manifest lists the chapters one source (a novel on one website origin) is known to have,
plus *coverage*: the integer chapter-number spans that were actually examined. Coverage is
set membership, never ``min``/``max``:

* every whole number inside a span is either a listed chapter or a source-confirmed
  *absence* (with evidence), so a span can never claim more than the manifest can show;
* a number outside every span is simply not covered, and a gap is never assumed absent;
* fractional labels count only when listed.

Chapters are identified by their source page (``ChapterIdentity``); displayed numbers are
canonical decimal text (``ChapterLabel``) and never pass through a float. Identity and label
validation is reused from :mod:`source_identity`, not re-implemented here.
"""

from __future__ import annotations

import hashlib
import json
import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from typing import Iterable, Optional
from urllib.parse import urlsplit

from .source_identity import (
    ChapterIdentity,
    ChapterLabel,
    SourceIdentity,
    SourceIdentityError,
    duplicate_labels,
    ensure_distinct_pages,
)

SCHEMA_VERSION = 1
MANIFEST_KIND = "chapter-manifest"
LAYER_BASELINE = "baseline"
LAYER_RUNTIME = "runtime"
LAYERS = (LAYER_BASELINE, LAYER_RUNTIME)
METHODS = ("toc", "walk", "probe", "legacy-migration")
METHOD_LEGACY = "legacy-migration"

MAX_MANIFEST_BYTES = 20 * 1024 * 1024
MAX_LABEL_NUMBER = 9_999_999  # the largest whole number a 7-digit label can show

_MAX_TITLE = 1000
_MAX_SOURCE_ID = 200
_MAX_EVIDENCE = 500
_MAX_NOTES = 1000
_TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,39}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SPAN_NUMBER_RE = re.compile(r"0|[1-9][0-9]{0,6}")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class ManifestError(ValueError):
    """A manifest (or one of its parts) failed validation (message is user-actionable)."""


# ── small helpers ────────────────────────────────────────────────────────────
def _text(value: object, what: str, *, limit: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ManifestError(f"{what} must be text, got {value!r}.")
    if not allow_empty and not value.strip():
        raise ManifestError(f"{what} must not be empty.")
    if len(value) > limit:
        raise ManifestError(f"{what} is longer than {limit} characters.")
    if _CONTROL_CHARS.search(value):
        raise ManifestError(f"{what} contains control characters.")
    return value


def whole_number(label: ChapterLabel) -> Optional[int]:
    """The integer a whole-number label shows, or ``None`` for a fractional label."""
    return None if "." in label.text else int(label.text)


def chapter_sort_key(chapter: ChapterIdentity) -> tuple:
    """Stable manifest order: label (numeric, then text), slug (absent first), page URL."""
    return (chapter.label.sort_key, -1 if chapter.slug is None else chapter.slug, chapter.page_url)


def _absent_sort_key(absent: "AbsentChapter") -> tuple:
    return absent.label.sort_key


def coalesce_spans(spans: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """Sort spans and merge any that overlap or touch (``[1,5]`` + ``[6,9]`` -> ``[1,9]``)."""
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return tuple((a, b) for a, b in merged)


def uncovered_spans(
    spans: Iterable[tuple[int, int]], start: int, end: int
) -> list[tuple[int, int]]:
    """The sub-spans of ``[start, end]`` that no span in ``spans`` (sorted) covers."""
    if start > end:
        raise ValueError(f"Empty request: {start} > {end}.")
    gaps: list[tuple[int, int]] = []
    cursor = start
    for a, b in spans:
        if b < cursor:
            continue
        if a > end:
            break
        if a > cursor:
            gaps.append((cursor, a - 1))
        cursor = max(cursor, b + 1)
        if cursor > end:
            break
    if cursor <= end:
        gaps.append((cursor, end))
    return gaps


def accounted_numbers(
    chapters: Iterable[ChapterIdentity], absent: Iterable["AbsentChapter"]
) -> list[int]:
    """Sorted, de-duplicated whole numbers shown by a chapter or confirmed absent."""
    found: set[int] = set()
    for chapter in chapters:
        number = whole_number(chapter.label)
        if number is not None:
            found.add(number)
    for item in absent:
        number = whole_number(item.label)
        if number is not None:
            found.add(number)
    return sorted(found)


def accounted_spans(
    spans: Iterable[tuple[int, int]], accounted: list[int]
) -> tuple[tuple[int, int], ...]:
    """The runs of ``accounted`` numbers that lie inside ``spans`` (work is bounded by the
    number of entries, never by how wide a span is)."""
    runs: list[tuple[int, int]] = []
    for a, b in spans:
        lo, hi = bisect_left(accounted, a), bisect_right(accounted, b)
        start = prev = None
        for number in accounted[lo:hi]:
            if start is None:
                start = prev = number
            elif number == prev + 1:
                prev = number
            else:
                runs.append((start, prev))
                start = prev = number
        if start is not None:
            runs.append((start, prev))
    return coalesce_spans(runs)


# ── provenance ───────────────────────────────────────────────────────────────
def _check_timestamp(value: object, what: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not _TIMESTAMP_RE.fullmatch(value):
        raise ManifestError(f"{what} must be a UTC timestamp like 2026-10-18T10:00:00Z, got {value!r}.")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise ManifestError(f"{what} is not a real date and time: {value!r}.") from None
    return value


@dataclass(frozen=True)
class Provenance:
    """Where a manifest's evidence came from. Never holds an absolute path."""

    generated_utc: Optional[str]
    validated_utc: Optional[str]
    app_version: str
    method: str
    legacy_index_sha256: Optional[str] = None
    notes: str = ""

    def __post_init__(self) -> None:
        _check_timestamp(self.generated_utc, "Provenance generated_utc")
        _check_timestamp(self.validated_utc, "Provenance validated_utc")
        if not isinstance(self.app_version, str) or not _VERSION_RE.fullmatch(self.app_version):
            raise ManifestError(f"Provenance app_version is not a version string: {self.app_version!r}.")
        if self.method not in METHODS:
            raise ManifestError(
                f"Provenance method {self.method!r} is not one of: {', '.join(METHODS)}."
            )
        digest = self.legacy_index_sha256
        if digest is not None and (not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest)):
            raise ManifestError("Provenance legacy_index_sha256 must be 64 lowercase hex digits or null.")
        if self.method == METHOD_LEGACY and digest is None:
            raise ManifestError("Provenance method 'legacy-migration' needs legacy_index_sha256.")
        _text(self.notes, "Provenance notes", limit=_MAX_NOTES, allow_empty=True)

    def to_json(self) -> dict:
        return {
            "generated_utc": self.generated_utc,
            "validated_utc": self.validated_utc,
            "app_version": self.app_version,
            "method": self.method,
            "legacy_index_sha256": self.legacy_index_sha256,
            "notes": self.notes,
        }

    @classmethod
    def from_json(cls, payload: object) -> "Provenance":
        keys = {
            "generated_utc", "validated_utc", "app_version", "method",
            "legacy_index_sha256", "notes",
        }
        _exact_keys(payload, keys, "Provenance")
        return cls(**payload)  # type: ignore[arg-type]


@dataclass(frozen=True)
class AbsentChapter:
    """A whole chapter number the *source itself* lacks, with the evidence that shows it."""

    label: ChapterLabel
    evidence: str

    def __post_init__(self) -> None:
        if not isinstance(self.label, ChapterLabel):
            raise ManifestError("Absent label must be a ChapterLabel.")
        _text(self.evidence, f"Absent evidence for {self.label}", limit=_MAX_EVIDENCE)

    def to_json(self) -> dict:
        return {"label": self.label.text, "evidence": self.evidence}

    @classmethod
    def from_json(cls, payload: object) -> "AbsentChapter":
        _exact_keys(payload, {"label", "evidence"}, "Absent entry")
        label = payload["label"]  # type: ignore[index]
        if not isinstance(label, str):
            raise ManifestError(f"Absent label must be text, got {label!r}.")
        try:
            return cls(ChapterLabel(label), payload["evidence"])  # type: ignore[index]
        except SourceIdentityError as exc:
            raise ManifestError(str(exc)) from exc


def _exact_keys(payload: object, keys: set[str], what: str) -> None:
    if not isinstance(payload, dict) or set(payload) != keys:
        raise ManifestError(f"{what} must be an object with exactly: {', '.join(sorted(keys))}.")


# ── the manifest ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ChapterManifest:
    """A validated chapter manifest. Every invariant is checked on construction, so an
    instance is always internally consistent whether it was built or loaded."""

    layer: str
    source: SourceIdentity
    provenance: Provenance
    toc_complete: bool
    spans: tuple
    chapters: tuple
    absent: tuple

    def __post_init__(self) -> None:
        if self.layer not in LAYERS:
            raise ManifestError(f"Manifest layer must be one of {LAYERS}, got {self.layer!r}.")
        if not isinstance(self.source, SourceIdentity):
            raise ManifestError("Manifest source must be a SourceIdentity.")
        if not isinstance(self.provenance, Provenance):
            raise ManifestError("Manifest provenance must be a Provenance.")
        if not isinstance(self.toc_complete, bool):
            raise ManifestError("Manifest toc_complete must be true or false.")
        if self.layer == LAYER_BASELINE and self.provenance.method == METHOD_LEGACY:
            raise ManifestError("A baseline manifest cannot come from a legacy-migration.")
        object.__setattr__(self, "spans", tuple(self.spans))
        object.__setattr__(self, "chapters", tuple(self.chapters))
        object.__setattr__(self, "absent", tuple(self.absent))
        self._check_spans()
        self._check_chapters()
        self._check_absent()
        self._check_accounting()
        self._check_toc_complete()

    # -- validation ---------------------------------------------------------
    def _check_spans(self) -> None:
        previous_end = -1
        for span in self.spans:
            if (
                not isinstance(span, tuple)
                or len(span) != 2
                or any(isinstance(n, bool) or not isinstance(n, int) for n in span)
            ):
                raise ManifestError(f"Coverage span must be a pair of whole numbers, got {span!r}.")
            start, end = span
            if start < 0 or end > MAX_LABEL_NUMBER or start > end:
                raise ManifestError(
                    f"Coverage span [{start}, {end}] is empty or outside 0..{MAX_LABEL_NUMBER}."
                )
            if start <= previous_end:
                raise ManifestError(
                    f"Coverage spans must be sorted and must not overlap (at [{start}, {end}])."
                )
            previous_end = end

    def _check_chapters(self) -> None:
        host = self.source.host
        slugs: set[int] = set()
        for chapter in self.chapters:
            if not isinstance(chapter, ChapterIdentity):
                raise ManifestError("Manifest chapters must be ChapterIdentity values.")
            if urlsplit(chapter.page_url).netloc.lower() != host:
                raise ManifestError(
                    f"Chapter page {chapter.page_url} is not on the source host {host}."
                )
            if chapter.title is not None:
                _text(chapter.title, f"Title of {chapter.page_url}", limit=_MAX_TITLE, allow_empty=True)
            if chapter.source_id is not None:
                _text(chapter.source_id, f"source_id of {chapter.page_url}", limit=_MAX_SOURCE_ID)
            if chapter.slug is not None:
                if chapter.slug in slugs:
                    raise ManifestError(
                        f"Chapter slug {chapter.slug} is used by more than one page "
                        f"(including {chapter.page_url})."
                    )
                slugs.add(chapter.slug)
        try:
            ensure_distinct_pages(self.chapters)
        except SourceIdentityError as exc:
            raise ManifestError(str(exc)) from exc
        ordered = sorted(self.chapters, key=chapter_sort_key)
        if any(a is not b and a != b for a, b in zip(self.chapters, ordered)):
            raise ManifestError("Manifest chapters are not sorted by label, slug and page URL.")

    def _check_absent(self) -> None:
        listed = {c.label for c in self.chapters}
        seen: set[ChapterLabel] = set()
        for item in self.absent:
            if not isinstance(item, AbsentChapter):
                raise ManifestError("Manifest absent entries must be AbsentChapter values.")
            if item.label in seen:
                raise ManifestError(f"Chapter {item.label} is listed absent more than once.")
            seen.add(item.label)
            number = whole_number(item.label)
            if number is None:
                raise ManifestError(
                    f"Absent label {item.label} has a fraction; only whole numbers can be "
                    "confirmed absent."
                )
            if item.label in listed:
                raise ManifestError(
                    f"Chapter {item.label} is both a listed chapter and listed absent."
                )
            if uncovered_spans(self.spans, number, number):
                raise ManifestError(f"Absent chapter {item.label} lies outside every coverage span.")
        ordered = sorted(self.absent, key=_absent_sort_key)
        if list(self.absent) != ordered:
            raise ManifestError("Manifest absent entries are not sorted by label.")

    def _check_accounting(self) -> None:
        accounted = accounted_numbers(self.chapters, self.absent)
        for start, end in self.spans:
            found = bisect_right(accounted, end) - bisect_left(accounted, start)
            if found != end - start + 1:
                raise ManifestError(
                    f"Coverage span [{start}, {end}] claims whole numbers that are not accounted "
                    "for by a listed chapter or a source-confirmed absence."
                )

    def _check_toc_complete(self) -> None:
        if not self.toc_complete:
            return
        if self.provenance.method == METHOD_LEGACY:
            raise ManifestError("A legacy-migration manifest cannot claim the TOC is complete.")
        wholes = [n for n in accounted_numbers(self.chapters, ()) ]
        if len(self.spans) > 1:
            raise ManifestError(
                "A manifest whose toc_complete is true must have a single contiguous coverage span."
            )
        if self.spans:
            start, end = self.spans[0]
            if any(n < start or n > end for n in wholes):
                raise ManifestError(
                    "toc_complete is true but a listed whole chapter lies outside the coverage span."
                )
        elif wholes:
            raise ManifestError("toc_complete is true but whole chapters are listed with no coverage span.")

    # -- construction -------------------------------------------------------
    @classmethod
    def create(
        cls,
        *,
        layer: str,
        source: SourceIdentity,
        provenance: Provenance,
        toc_complete: bool = False,
        spans: Iterable[tuple[int, int]] = (),
        chapters: Iterable[ChapterIdentity] = (),
        absent: Iterable[AbsentChapter] = (),
    ) -> "ChapterManifest":
        """Build a manifest from trusted code: chapters, absences and spans are put in
        canonical order (spans that touch are merged). Validation is the same as on load."""
        return cls(
            layer=layer,
            source=source,
            provenance=provenance,
            toc_complete=toc_complete,
            spans=coalesce_spans(tuple(s) for s in spans),
            chapters=tuple(sorted(chapters, key=chapter_sort_key)),
            absent=tuple(sorted(absent, key=_absent_sort_key)),
        )

    @classmethod
    def empty(cls, layer: str, source: SourceIdentity, provenance: Provenance) -> "ChapterManifest":
        return cls.create(layer=layer, source=source, provenance=provenance)

    # -- coverage queries ---------------------------------------------------
    def uncovered_spans(self, start: int, end: int) -> list[tuple[int, int]]:
        """Parts of the whole-number request ``[start, end]`` this manifest does not cover."""
        return uncovered_spans(self.spans, start, end)

    def covers(self, start: int, end: int) -> bool:
        return not self.uncovered_spans(start, end)

    def duplicate_labels(self) -> dict:
        """Labels shown by more than one page (kept, never merged)."""
        return duplicate_labels(self.chapters)

    # -- serialization ------------------------------------------------------
    @cached_property
    def content_sha256(self) -> str:
        """SHA-256 over the canonical chapters, absences and spans (not provenance)."""
        canonical = json.dumps(
            {
                "chapters": [c.to_json() for c in self.chapters],
                "absent": [a.to_json() for a in self.absent],
                "spans": [[str(a), str(b)] for a, b in self.spans],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(canonical.encode("ascii")).hexdigest()

    def to_json(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": MANIFEST_KIND,
            "layer": self.layer,
            "source": self.source.to_json(),
            "provenance": self.provenance.to_json(),
            "coverage": {
                "toc_complete": self.toc_complete,
                "spans": [[str(a), str(b)] for a, b in self.spans],
            },
            "chapters": [c.to_json() for c in self.chapters],
            "absent": [a.to_json() for a in self.absent],
            "content_sha256": self.content_sha256,
        }

    @classmethod
    def from_json(
        cls, payload: object, *, expected_source: SourceIdentity, expected_layer: str
    ) -> "ChapterManifest":
        """Strictly parse ``payload``; it must be exactly the manifest of ``expected_source``
        in ``expected_layer``."""
        if expected_layer not in LAYERS:
            raise ManifestError(f"Unknown manifest layer {expected_layer!r}.")
        _exact_keys(
            payload,
            {"schema_version", "kind", "layer", "source", "provenance", "coverage",
             "chapters", "absent", "content_sha256"},
            "Manifest",
        )
        assert isinstance(payload, dict)
        version = payload["schema_version"]
        if type(version) is not int or version != SCHEMA_VERSION:
            raise ManifestError(
                f"Unsupported manifest schema_version {version!r} (this app reads {SCHEMA_VERSION})."
            )
        if payload["kind"] != MANIFEST_KIND:
            raise ManifestError(f"Not a chapter manifest: kind is {payload['kind']!r}.")
        if payload["layer"] != expected_layer:
            raise ManifestError(
                f"Manifest layer is {payload['layer']!r}, expected {expected_layer!r}."
            )
        try:
            source = SourceIdentity.from_json(payload["source"])
        except SourceIdentityError as exc:
            raise ManifestError(f"Manifest source is invalid: {exc}") from exc
        if source != expected_source:
            raise ManifestError(
                f"Manifest source ({source.site_key}, {source.novel_slug}) does not match the "
                f"expected source ({expected_source.site_key}, {expected_source.novel_slug})."
            )
        provenance = Provenance.from_json(payload["provenance"])
        coverage = payload["coverage"]
        _exact_keys(coverage, {"toc_complete", "spans"}, "Manifest coverage")
        spans = _parse_spans(coverage["spans"])
        chapters = _parse_chapters(source, payload["chapters"])
        raw_absent = payload["absent"]
        if not isinstance(raw_absent, list):
            raise ManifestError("Manifest absent must be a list.")
        manifest = cls(
            layer=expected_layer,
            source=source,
            provenance=provenance,
            toc_complete=coverage["toc_complete"],
            spans=spans,
            chapters=chapters,
            absent=tuple(AbsentChapter.from_json(item) for item in raw_absent),
        )
        stored = payload["content_sha256"]
        if not isinstance(stored, str) or not _SHA256_RE.fullmatch(stored):
            raise ManifestError("Manifest content_sha256 must be 64 lowercase hex digits.")
        if stored != manifest.content_sha256:
            raise ManifestError(
                "Manifest content_sha256 does not match its chapters, absences and spans; the "
                "file is damaged or was edited."
            )
        return manifest


def _parse_spans(raw: object) -> tuple[tuple[int, int], ...]:
    if not isinstance(raw, list):
        raise ManifestError("Coverage spans must be a list.")
    spans = []
    for item in raw:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or any(not isinstance(n, str) or not _SPAN_NUMBER_RE.fullmatch(n) for n in item)
        ):
            raise ManifestError(
                f"Coverage span must be a pair of whole-number texts like [\"1\", \"3700\"], got {item!r}."
            )
        spans.append((int(item[0]), int(item[1])))
    return tuple(spans)


def _parse_chapters(source: SourceIdentity, raw: object) -> tuple[ChapterIdentity, ...]:
    if not isinstance(raw, list):
        raise ManifestError("Manifest chapters must be a list.")
    chapters = []
    for row in raw:
        if isinstance(row, dict) and "label" in row and not isinstance(row["label"], str):
            raise ManifestError(f"Chapter label must be text, got {row['label']!r}.")
        try:
            chapters.append(ChapterIdentity.from_json(source, row))
        except SourceIdentityError as exc:
            raise ManifestError(str(exc)) from exc
    return tuple(chapters)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key in JSON object")
    return dict(pairs)


def _reject_constant(name: str) -> object:
    raise ValueError(f"non-finite number {name} in JSON")


def load_strict_json(data: bytes) -> object:
    """Decode strict UTF-8 JSON: no byte-order mark, duplicate keys, NaN or Infinity, and
    hostile nesting is a rejection rather than a crash."""
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ManifestError(f"Not valid JSON: {exc}") from exc


def parse_manifest_bytes(
    data: bytes, *, expected_source: SourceIdentity, expected_layer: str
) -> ChapterManifest:
    """Parse raw file bytes: size-capped and strict (see :func:`load_strict_json`)."""
    if len(data) > MAX_MANIFEST_BYTES:
        raise ManifestError(
            f"Manifest is larger than the {MAX_MANIFEST_BYTES // (1024 * 1024)} MB limit."
        )
    return ChapterManifest.from_json(
        load_strict_json(data), expected_source=expected_source, expected_layer=expected_layer
    )
