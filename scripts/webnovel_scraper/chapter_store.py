"""Chapter manifest store: baseline + runtime layers, merging, quarantine (v0.3.0 Checkpoint 03.1).

Two files can exist per source (plan §9.2):

* the **baseline**, tracked in the repository under ``scripts/webnovel_scraper/chapter_lists/``.
  It is a read-only runtime asset: the app never renames, rewrites or deletes it, and an invalid
  baseline is rejected with a diagnostic and left exactly as found;
* the **runtime overlay**, under ``files/runtime-data/chapter-lists/`` (Git-ignored). It is the
  only file the app creates or updates, always atomically and only after validation. An invalid
  overlay is never deleted: it is renamed aside as ``<name>.corrupt-<utc>`` (a name that is never
  reused), and the loader falls back to the baseline.

Both are filed under ``<site_key>/<novel_slug>.json`` where ``site_key`` is the website host, so
``freewebnovel.com`` and ``freewebnovel.net`` never share a file.

:func:`merge_manifests` projects the two layers into one *effective* manifest keyed by source
page URL. Contradictions are reported, never resolved by picking a winner, and the coverage they
touch becomes unverified until rediscovered.

The pipeline reaches this store through ``chapter_preflight`` (03.2a). Writing is a
read-modify-write on one runtime file, so every writer first takes :meth:`ChapterManifestStore.writer_lock`,
a per-source cross-process lock: a second writer is refused visibly (:class:`ManifestLockBusy`)
rather than allowed to lose an update. The legacy output-scoped ``chapter_index.json`` import is a
separate, explicit store operation.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

from .atomic_io import write_json_atomic
from .chapter_verification import requires_verification
from .file_lock import FileLock, LockBusy
from .chapter_manifest import (
    LAYER_BASELINE,
    LAYER_RUNTIME,
    LAYERS,
    MAX_LABEL_NUMBER,
    MAX_MANIFEST_BYTES,
    METHOD_LEGACY,
    AbsentChapter,
    ChapterManifest,
    ManifestError,
    Provenance,
    accounted_numbers,
    accounted_spans,
    chapter_sort_key,
    coalesce_spans,
    load_strict_json,
    parse_manifest_bytes,
    uncovered_spans,
    whole_number,
)
from .source_identity import (
    ChapterIdentity,
    ChapterLabel,
    SourceIdentity,
    SourceIdentityError,
    duplicate_labels,
)

_PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_BASELINE_ROOT = _PACKAGE_DIR / "chapter_lists"
DEFAULT_RUNTIME_ROOT = _PACKAGE_DIR.parents[1] / "files" / "runtime-data" / "chapter-lists"
APP_VERSION = "0.3.0"

_MAX_QUARANTINE_TRIES = 1000
_MAX_NOTES = 1000
LEGACY_INDEX_SCHEMA = 2
_LEGACY_IDENTITY_KEYS = {"novel_slug", "adapter_key", "source_url", "base_url"}
_LEGACY_REQUIRED_ROW_KEYS = {"index", "url"}
_LEGACY_ROW_KEYS = {"index", "url", "title", "source_id", "slug"}


class ManifestStoreError(RuntimeError):
    """A store operation was refused or could not be completed safely (user-actionable)."""


class ManifestLockBusy(ManifestStoreError):
    """Another writer (another run or program instance) is updating this source's chapter list."""


# ── effective manifest (baseline + overlay) ──────────────────────────────────
@dataclass(frozen=True)
class ManifestConflict:
    """One contradiction between the layers. ``kind`` is ``label``, ``title``, ``slug`` or
    ``source_id`` (one page described two ways), ``slug-collision`` (two pages claim one slug)
    or ``absent-vs-chapter`` (one layer lists a chapter the other confirms absent)."""

    kind: str
    message: str
    page_urls: tuple = ()
    labels: tuple = ()


@dataclass(frozen=True)
class EffectiveManifest:
    """The deterministic projection of baseline + overlay for one source.

    ``spans`` is only the coverage that survived: every whole number in it is a retained
    chapter or retained source-confirmed absence, and no contradicted label is inside it.
    """

    source: SourceIdentity
    chapters: tuple
    absent: tuple
    spans: tuple
    toc_complete: bool
    conflicts: tuple = ()
    baseline_sha256: Optional[str] = None
    runtime_sha256: Optional[str] = None

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)

    def uncovered_spans(self, start: int, end: int) -> list[tuple[int, int]]:
        return uncovered_spans(self.spans, start, end)

    def covers(self, start: int, end: int) -> bool:
        return not self.uncovered_spans(start, end)

    def duplicate_labels(self) -> dict:
        return duplicate_labels(self.chapters)

    def notices(self) -> tuple[str, ...]:
        """One readable line per contradiction, ready for a future Activity log."""
        return tuple(
            f"{c.message} The affected chapters are treated as unverified and will be rediscovered."
            for c in self.conflicts
        )

    def as_manifest(self, layer: str, provenance: Provenance) -> ChapterManifest:
        """This projection as a validated manifest of ``layer`` (used to persist merged data)."""
        return ChapterManifest.create(
            layer=layer,
            source=self.source,
            provenance=provenance,
            toc_complete=self.toc_complete,
            spans=self.spans,
            chapters=self.chapters,
            absent=self.absent,
        )


def _combine_pages(
    source: SourceIdentity, kept: ChapterIdentity, other: ChapterIdentity
) -> tuple[Optional[ChapterIdentity], list[tuple[str, object, object]]]:
    """Merge two descriptions of one page. A field that is missing on one side is filled from
    the other; two different known values are a conflict (the page is then dropped)."""
    differences: list[tuple[str, object, object]] = []
    if kept.label != other.label:
        differences.append(("label", kept.label.text, other.label.text))
    values = {}
    for name in ("title", "slug", "source_id"):
        a, b = getattr(kept, name), getattr(other, name)
        if a is not None and b is not None and a != b:
            differences.append((name, a, b))
        values[name] = a if a is not None else b
    if differences:
        return None, differences
    return (
        source.chapter_identity(
            kept.page_url, kept.label,
            slug=values["slug"], source_id=values["source_id"], title=values["title"],
        ),
        [],
    )


def _check_layer(source: SourceIdentity, manifest: Optional[ChapterManifest], layer: str) -> None:
    if manifest is None:
        return
    if manifest.layer != layer:
        raise ManifestError(f"Wrong manifest layer: expected {layer}, got {manifest.layer}.")
    if manifest.source != source:
        raise ManifestError(
            f"The {layer} manifest's source ({manifest.source.site_key}, {manifest.source.novel_slug}) "
            f"is not the requested source ({source.site_key}, {source.novel_slug})."
        )


def merge_manifests(
    source: SourceIdentity,
    baseline: Optional[ChapterManifest],
    runtime: Optional[ChapterManifest],
) -> EffectiveManifest:
    """Project validated ``baseline`` and ``runtime`` manifests into one effective manifest.

    Chapters are matched by source page URL only, never by displayed number. Everything that
    contradicts is reported in ``conflicts`` and withheld: a page described two ways is dropped,
    two pages sharing a slug are both dropped, and absence evidence that a chapter contradicts is
    discarded. The whole numbers involved leave the coverage until rediscovered. The result does
    not depend on which file is newer.
    """
    _check_layer(source, baseline, LAYER_BASELINE)
    _check_layer(source, runtime, LAYER_RUNTIME)
    if requires_verification(source):
        if any(m and m.verification is None for m in (baseline, runtime)):
            raise ManifestError("Supreme Magus legacy metadata requires current-policy revalidation.")
        # Policy snapshots contain the full evidence universe. A runtime invalidation
        # must suppress a historical baseline rather than reintroduce it additively.
        if runtime is not None:
            projected = _merge(source, [("runtime overlay", runtime)])
            if baseline is not None:
                disagreement = _merge(source, [("baseline", baseline), ("runtime overlay", runtime)])
                if disagreement.conflicts:
                    from dataclasses import replace
                    return replace(projected, spans=(), toc_complete=False, conflicts=disagreement.conflicts)
            return projected
    return _merge(source, [("baseline", baseline), ("runtime overlay", runtime)])


def _merge(
    source: SourceIdentity, labelled: list[tuple[str, Optional[ChapterManifest]]]
) -> EffectiveManifest:
    """:func:`merge_manifests` over any ordered, named layers (earlier layers win ties on
    evidence text only; nothing contradictory is ever resolved by order)."""
    named = [(name, m) for name, m in labelled if m is not None]
    layers = [m for _, m in named]
    baseline = named[0][1] if named and named[0][0] == "baseline" else None
    runtime = next((m for n, m in named if n == "runtime overlay"), None)

    conflicts: list[ManifestConflict] = []
    tainted: set[int] = set()

    def taint(*labels: ChapterLabel) -> None:
        for label in labels:
            number = whole_number(label)
            if number is not None:
                tainted.add(number)

    merged: dict[str, ChapterIdentity] = {}
    first_layer: dict[str, str] = {}
    dropped: set[str] = set()
    for layer_name, manifest in named:
        for chapter in manifest.chapters:
            url = chapter.page_url
            if url in dropped:
                continue
            kept = merged.get(url)
            if kept is None:
                merged[url] = chapter
                first_layer[url] = layer_name
                continue
            combined, differences = _combine_pages(source, kept, chapter)
            if combined is not None:
                merged[url] = combined
                continue
            dropped.add(url)
            del merged[url]
            taint(kept.label, chapter.label)
            for name, left, right in differences:
                conflicts.append(ManifestConflict(
                    kind=name,
                    message=(
                        f"Chapter page {url} has a conflicting {name}: {left!r} in the "
                        f"{first_layer[url]} but {right!r} in the {layer_name}."
                    ),
                    page_urls=(url,),
                    labels=tuple(sorted({kept.label.text, chapter.label.text})),
                ))

    by_slug: dict[int, list[ChapterIdentity]] = {}
    for chapter in merged.values():
        if chapter.slug is not None:
            by_slug.setdefault(chapter.slug, []).append(chapter)
    for slug in sorted(by_slug):
        group = by_slug[slug]
        if len(group) < 2:
            continue
        for chapter in group:
            del merged[chapter.page_url]
            taint(chapter.label)
        urls = tuple(sorted(c.page_url for c in group))
        conflicts.append(ManifestConflict(
            kind="slug-collision",
            message=f"{len(group)} different chapter pages claim slug {slug}: {', '.join(urls)}.",
            page_urls=urls,
            labels=tuple(sorted({c.label.text for c in group})),
        ))

    chapters = tuple(sorted(merged.values(), key=chapter_sort_key))
    listed_labels = {c.label for c in chapters}
    absent: dict[ChapterLabel, AbsentChapter] = {}
    for manifest in layers:
        for item in manifest.absent:
            number = whole_number(item.label)
            if number in tainted:
                continue
            if item.label in listed_labels:
                tainted.add(number)
                absent.pop(item.label, None)
                conflicts.append(ManifestConflict(
                    kind="absent-vs-chapter",
                    message=(
                        f"Chapter {item.label} is confirmed absent ({item.evidence!r}) in one layer "
                        "but listed as a chapter page in the other."
                    ),
                    page_urls=tuple(sorted(c.page_url for c in chapters if c.label == item.label)),
                    labels=(item.label.text,),
                ))
                continue
            absent.setdefault(item.label, item)
    kept_absent = tuple(sorted(
        (a for a in absent.values() if whole_number(a.label) not in tainted),
        key=lambda a: a.label.sort_key,
    ))

    accounted = [n for n in accounted_numbers(chapters, kept_absent) if n not in tainted]
    union = coalesce_spans(span for m in layers for span in m.spans)
    spans = accounted_spans(union, accounted)

    wholes = accounted_numbers(chapters, ())
    complete = (
        bool(layers)
        and not conflicts
        and all(m.toc_complete for m in layers)
        and len(spans) <= 1
        and (all(spans[0][0] <= n <= spans[0][1] for n in wholes) if spans else not wholes)
    )
    return EffectiveManifest(
        source=source,
        chapters=chapters,
        absent=kept_absent,
        spans=spans,
        toc_complete=complete,
        conflicts=tuple(conflicts),
        baseline_sha256=baseline.content_sha256 if baseline else None,
        runtime_sha256=runtime.content_sha256 if runtime else None,
    )


# ── read-only legacy chapter_index.json (schema v2) import ───────────────────
class LegacyIndexRejected(ManifestError):
    """A legacy ``chapter_index.json`` is not safe to import (message says why)."""


@dataclass(frozen=True)
class LegacyImportResult:
    """Outcome of :meth:`ChapterManifestStore.import_legacy_index`.

    ``status`` is ``imported`` (overlay written), ``already-covered`` (nothing to add; nothing
    written), ``rejected`` (the legacy file is unsafe or unreadable), ``conflict`` (it contradicts
    verified data; nothing written) or ``error`` (publishing failed; nothing changed).
    """

    status: str
    reason: str = ""
    chapters: int = 0
    legacy_sha256: Optional[str] = None


def _legacy_identity_matches(source: SourceIdentity, identity: object) -> None:
    if not isinstance(identity, dict) or set(identity) != _LEGACY_IDENTITY_KEYS:
        raise LegacyIndexRejected(
            "The legacy index has no usable identity block (novel_slug, adapter_key, source_url, "
            "base_url), so it cannot be tied to this website and novel."
        )
    values = [identity[k] for k in ("novel_slug", "adapter_key", "source_url", "base_url")]
    if not all(isinstance(v, str) for v in values):
        raise LegacyIndexRejected("The legacy index identity block holds non-text values.")
    slug, adapter, source_url, base_url = values
    try:
        origin = base_url
        if not origin:
            parts = urlsplit(source_url)
            origin = f"{parts.scheme}://{parts.netloc}"
        legacy = SourceIdentity(
            novel_slug=slug, adapter_key=adapter, origin=origin, index_url=source_url
        )
    except SourceIdentityError as exc:
        raise LegacyIndexRejected(f"The legacy index identity is invalid: {exc}") from exc
    same = (legacy.novel_slug, legacy.adapter_key, legacy.origin, legacy.index_url) == (
        source.novel_slug, source.adapter_key, source.origin, source.index_url
    )
    if not same:
        raise LegacyIndexRejected(
            f"The legacy index belongs to {legacy.site_key} / {legacy.novel_slug}, which does not "
            f"match the requested source {source.site_key} / {source.novel_slug}."
        )


def _legacy_chapter(source: SourceIdentity, position: int, row: object) -> ChapterIdentity:
    if not isinstance(row, dict) or not _LEGACY_REQUIRED_ROW_KEYS <= set(row) <= _LEGACY_ROW_KEYS:
        raise LegacyIndexRejected(f"Legacy chapter row {position} has an unexpected shape.")
    number = row["index"]
    if isinstance(number, bool) or not isinstance(number, (int, float)):
        raise LegacyIndexRejected(f"Legacy chapter row {position} has a non-numeric index {number!r}.")
    try:
        return source.chapter_identity(
            row["url"],
            ChapterLabel.from_index(number),
            slug=row.get("slug"),
            source_id=row.get("source_id"),
            title=row.get("title"),
        )
    except SourceIdentityError as exc:
        raise LegacyIndexRejected(f"Legacy chapter row {position} is invalid: {exc}") from exc


def parse_legacy_index(
    source: SourceIdentity, data: bytes, *, now_utc: str, app_version: str
) -> ChapterManifest:
    """Turn the bytes of a legacy ``chapter_index.json`` into a runtime manifest of ``source``.

    Accepts only a complete, schema-2 file whose explicit identity matches ``source`` and whose
    every row validates (plan §9.4); anything else raises :class:`LegacyIndexRejected`. Coverage
    is the maximal contiguous whole-number runs only: gaps and the unknown tail stay uncovered.
    Pure: nothing is read from or written to disk.
    """
    try:
        if len(data) > MAX_MANIFEST_BYTES:
            raise LegacyIndexRejected("The legacy index is larger than the 20 MB limit.")
        payload = load_strict_json(data)
        if isinstance(payload, list):
            raise LegacyIndexRejected(
                "The legacy index is a bare list (the pre-identity format), which cannot be trusted."
            )
        if not isinstance(payload, dict):
            raise LegacyIndexRejected("The legacy index is not a JSON object.")
        version = payload.get("schema_version")
        if type(version) is not int or version != LEGACY_INDEX_SCHEMA:
            raise LegacyIndexRejected(
                f"The legacy index has schema version {version!r}; only schema {LEGACY_INDEX_SCHEMA} "
                "can be imported."
            )
        if "identity" not in payload:
            raise LegacyIndexRejected(
                "The legacy index has no identity block, so it cannot be tied to this source."
            )
        _legacy_identity_matches(source, payload["identity"])
        if payload.get("complete") is not True:
            raise LegacyIndexRejected(
                "The legacy index is not marked complete (it is an interrupted checkpoint)."
            )
        rows = payload.get("chapters")
        if not isinstance(rows, list) or not rows:
            raise LegacyIndexRejected("The legacy index lists no chapters.")
        chapters = [_legacy_chapter(source, i, row) for i, row in enumerate(rows)]
        runs = accounted_spans(((0, MAX_LABEL_NUMBER),), accounted_numbers(chapters, ()))
        digest = hashlib.sha256(data).hexdigest()
        return ChapterManifest.create(
            layer=LAYER_RUNTIME,
            source=source,
            provenance=Provenance(
                generated_utc=None,
                validated_utc=now_utc,
                app_version=app_version,
                method=METHOD_LEGACY,
                legacy_index_sha256=digest,
                notes="Imported from a legacy chapter_index.json (schema 2).",
            ),
            toc_complete=False,
            spans=runs,
            chapters=chapters,
        )
    except LegacyIndexRejected:
        raise
    except ManifestError as exc:
        raise LegacyIndexRejected(str(exc)) from exc


# ── store ────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class LayerLoad:
    """What loading one layer found. ``status`` is ``missing``, ``loaded``, ``invalid`` (a
    tracked baseline, left untouched), ``quarantined`` (a runtime overlay set aside),
    ``quarantine-failed`` (an invalid overlay that could not be moved and stays in place) or
    ``unreadable`` (an I/O error; nothing was moved)."""

    layer: str
    path: Path
    status: str
    manifest: Optional[ChapterManifest] = None
    error: Optional[str] = None
    quarantined_to: Optional[Path] = None


@dataclass(frozen=True)
class EffectiveLoad:
    """Both layers, their projection, and one readable line per problem found."""

    effective: EffectiveManifest
    baseline: LayerLoad
    runtime: LayerLoad
    notices: tuple = ()


@dataclass(frozen=True)
class DiscoveryRecord:
    """Outcome of :meth:`ChapterManifestStore.record_discovery`.

    ``status`` is ``recorded`` (the overlay was published), ``unchanged`` (everything was already
    known and covered; nothing written), ``conflict`` (the evidence contradicts verified data;
    nothing written, ``effective`` is the unchanged projection), ``rejected`` (the evidence itself
    was not a valid manifest; nothing written) or ``error`` (publishing failed; ``effective`` is
    the projection the write would have produced, usable for this run only).
    """

    status: str
    effective: EffectiveManifest
    reason: str = ""
    path: Optional[Path] = None


def _quarantine_move(source_path: Path, target_path: Path) -> None:
    """Move an invalid overlay aside (a seam so tests can simulate a locked file)."""
    os.replace(source_path, target_path)


def _utc_text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ChapterManifestStore:
    """Reads both layers and writes only the runtime overlay."""

    def __init__(
        self,
        baseline_root: str | os.PathLike[str] = DEFAULT_BASELINE_ROOT,
        runtime_root: str | os.PathLike[str] = DEFAULT_RUNTIME_ROOT,
        *,
        clock: Optional[Callable[[], datetime]] = None,
        app_version: str = APP_VERSION,
    ) -> None:
        self._baseline_root = Path(baseline_root)
        self._runtime_root = Path(runtime_root)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.app_version = app_version
        # Writer locks this store instance holds: lock path -> [owner thread, depth, FileLock].
        self._held: dict[str, list] = {}
        self._held_guard = threading.Lock()

    # -- locations ----------------------------------------------------------
    def baseline_path(self, source: SourceIdentity) -> Path:
        return self._baseline_root / source.site_key / f"{source.novel_slug}.json"

    def runtime_path(self, source: SourceIdentity) -> Path:
        return self._runtime_root / source.site_key / f"{source.novel_slug}.json"

    def _path(self, source: SourceIdentity, layer: str) -> Path:
        if layer not in LAYERS:
            raise ManifestStoreError(f"Unknown manifest layer {layer!r}.")
        return self.baseline_path(source) if layer == LAYER_BASELINE else self.runtime_path(source)

    def now_utc(self) -> str:
        return _utc_text(self._clock())

    def migration_preview(self, source: SourceIdentity, legacy_index=None) -> dict:
        """Read-only, deterministic disposition; no migration is executed by this API."""
        paths = [("baseline", self.baseline_path(source)), ("runtime", self.runtime_path(source))]
        if legacy_index is not None:
            paths.append(("legacy-output-index", Path(legacy_index)))
        entries = []
        for layer, path in paths:
            if not os.path.lexists(path):
                continue
            schema, policy, digest, reason = None, None, None, "legacy index cannot certify page identity"
            try:
                data = self._read_bytes(path)
                digest = hashlib.sha256(data).hexdigest()
                raw = load_strict_json(data)
                if isinstance(raw, dict):
                    schema = raw.get("schema_version")
                    verification = raw.get("verification")
                    policy = verification.get("policy") if isinstance(verification, dict) else None
                if layer != "legacy-output-index":
                    manifest = parse_manifest_bytes(data, expected_source=source, expected_layer=layer)
                    if manifest.verification is not None:
                        continue
                    reason = "valid legacy metadata has no independent current-policy page observations"
            except (OSError, ManifestError) as exc:
                reason = str(exc)
            backup = path.with_name(f"{path.name}.migration-{digest or 'unreadable'}.bak")
            suffix = 0
            while os.path.lexists(backup):
                suffix += 1
                backup = path.with_name(f"{path.name}.migration-{digest or 'unreadable'}.bak-{suffix}")
            entries.append(dict(layer=layer, path=str(path.resolve()), schema=schema, policy=policy,
                                sha256=digest, reason=reason, proposed_backup=str(backup.resolve())))
        return dict(source=source.to_json(), files=entries,
                    procedure="Independently read every source slug using uncached HTML and CP1 positive exact identity; retain unresolved outcomes; never seed trust from old mappings or caches.",
                    transitions="Separate approval required. Exclusively copy exact old bytes to unique non-overwriting backups, verify hashes, retain baseline unchanged, then atomically publish a validated schema-2 runtime observation manifest under the writer lock. An incompatible baseline requires a separately approved disposition before reuse.",
                    failure="Before publication keep original bytes; after invalidation preserve observations and backups without restoring verification authority. Restored legacy bytes remain untrusted. No automatic migration or rollback to old authority.",
                    effect="Initial revalidation reads every source page; verified partial observations remain incomplete. Later finite ranges reuse complete historical coverage; open-ended requests recheck the bound and tail.")

    def assert_compatible(self, source: SourceIdentity, legacy_index=None) -> None:
        if not requires_verification(source):
            return
        preview = self.migration_preview(source, legacy_index)
        if preview["files"]:
            raise ManifestStoreError(
                "Supreme Magus saved chapter list requires revalidation under the current policy. "
                "Existing files were left unchanged. MIGRATION PREVIEW (not executed): "
                + json.dumps(preview, ensure_ascii=True, sort_keys=True)
            )

    # -- single-writer lock -------------------------------------------------
    def lock_path(self, source: SourceIdentity) -> Path:
        path = self.runtime_path(source)
        return path.with_name(f"{path.name}.lock")

    @contextmanager
    def writer_lock(self, source: SourceIdentity):
        """Hold the cross-process single-writer lock for ``source`` (non-blocking).

        Cover the whole load -> reconcile -> publish sequence with it. Re-entrant for the
        thread that holds it, so :meth:`save_runtime` and the operations below may be called
        inside. Raises :class:`ManifestLockBusy` (and writes nothing) when another run or program
        instance holds it; the operating system frees it if that holder dies.
        """
        self.assert_compatible(source)
        key = str(self.lock_path(source))
        me = threading.get_ident()
        with self._held_guard:
            entry = self._held.get(key)
            if entry is not None and entry[0] == me:
                entry[1] += 1
            else:
                entry = None
        if entry is None:
            lock = FileLock(key)
            try:
                lock.acquire()
            except LockBusy as exc:
                raise ManifestLockBusy(
                    f"Another run or program instance is updating the saved chapter list for "
                    f"{source.novel_slug} on {source.site_key}. Wait for it to finish (or close it) "
                    "and try again; nothing was changed."
                ) from exc
            except OSError as exc:
                raise ManifestStoreError(
                    f"The chapter list lock for {source.novel_slug} on {source.site_key} could not "
                    f"be taken ({exc}); nothing was changed."
                ) from exc
            with self._held_guard:
                self._held[key] = [me, 1, lock]
        try:
            yield
        finally:
            with self._held_guard:
                held = self._held[key]
                held[1] -= 1
                if held[1] == 0:
                    del self._held[key]
                    held[2].release()

    # -- reading ------------------------------------------------------------
    @staticmethod
    def _read_bytes(path: Path) -> bytes:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ManifestError(f"{path.name} is not a regular file.")
        if info.st_size > MAX_MANIFEST_BYTES:
            raise ManifestError(
                f"Manifest is larger than the {MAX_MANIFEST_BYTES // (1024 * 1024)} MB limit."
            )
        with open(path, "rb") as handle:
            return handle.read(MAX_MANIFEST_BYTES + 1)

    def load_layer(
        self, source: SourceIdentity, layer: str, *, quarantine: bool = True
    ) -> LayerLoad:
        """Load and validate one layer. A bad baseline is only reported; a bad overlay is
        quarantined (see the module docstring) unless ``quarantine`` is false, which a reader
        that does not hold the writer lock uses so that it never moves a file."""
        path = self._path(source, layer)
        if not os.path.lexists(path):
            return LayerLoad(layer, path, "missing")
        try:
            try:
                data = self._read_bytes(path)
            except OSError as exc:
                if path.is_dir():
                    raise ManifestError(f"{path.name} is a folder, not a manifest file.") from exc
                return LayerLoad(layer, path, "unreadable",
                                 error=f"The {layer} chapter list {path.name} could not be read: {exc}.")
            if path.is_dir():
                raise ManifestError(f"{path.name} is a folder, not a manifest file.")
            manifest = parse_manifest_bytes(data, expected_source=source, expected_layer=layer)
        except ManifestError as exc:
            if requires_verification(source):
                return LayerLoad(layer, path, "invalid", error=f"Saved chapter evidence requires policy revalidation ({exc}); left unchanged.")
            return self._reject(layer, path, str(exc), quarantine)
        if requires_verification(source) and manifest.verification is None:
            return LayerLoad(layer, path, "incompatible", error="Saved chapter list requires current-policy revalidation; left unchanged.")
        return LayerLoad(layer, path, "loaded", manifest=manifest)

    def _reject(self, layer: str, path: Path, reason: str, quarantine: bool = True) -> LayerLoad:
        if layer != LAYER_BASELINE and not quarantine:
            return LayerLoad(
                layer, path, "invalid",
                error=(
                    f"The runtime chapter list {path.name} was rejected ({reason}) and left in place "
                    "because another run is updating the chapter lists."
                ),
            )
        if layer == LAYER_BASELINE:
            return LayerLoad(
                layer, path, "invalid",
                error=(
                    f"The tracked baseline chapter list {path.name} was rejected ({reason}). It was "
                    "left unchanged; replace it with a freshly generated, verified file."
                ),
            )
        try:
            moved = self._quarantine(path)
        except OSError as exc:
            return LayerLoad(
                layer, path, "quarantine-failed",
                error=(
                    f"The runtime chapter list {path.name} was rejected ({reason}) and could not be "
                    f"set aside ({exc}); it was left in place."
                ),
            )
        return LayerLoad(
            layer, path, "quarantined", quarantined_to=moved,
            error=(
                f"The runtime chapter list {path.name} was rejected ({reason}) and set aside as "
                f"{moved.name}; its chapters will be rediscovered."
            ),
        )

    def _quarantine(self, path: Path) -> Path:
        return self._set_aside(path, "corrupt")

    def _set_aside(self, path: Path, tag: str) -> Path:
        """Rename ``path`` to a never-used ``<name>.<tag>-<utc>[-N]`` beside it (``corrupt`` for
        an invalid overlay, ``bak`` for an explicit refresh). The target name is claimed
        exclusively first, so an existing file is never overwritten."""
        stamp = self._clock().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        for attempt in range(_MAX_QUARANTINE_TRIES):
            suffix = f"-{attempt}" if attempt else ""
            target = path.with_name(f"{path.name}.{tag}-{stamp}{suffix}")
            try:
                fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                continue
            os.close(fd)
            try:
                _quarantine_move(path, target)
            except BaseException:
                try:
                    os.unlink(target)
                except OSError:
                    pass
                raise
            return target
        raise OSError(f"no free {tag} name beside {path.name}")

    def load_effective(self, source: SourceIdentity, *, quarantine: bool = True) -> EffectiveLoad:
        """Load both layers and merge whatever is valid."""
        self.assert_compatible(source)
        baseline = self.load_layer(source, LAYER_BASELINE)
        runtime = self.load_layer(source, LAYER_RUNTIME, quarantine=quarantine)
        effective = merge_manifests(source, baseline.manifest, runtime.manifest)
        notices = [layer.error for layer in (baseline, runtime) if layer.error]
        notices.extend(effective.notices())
        return EffectiveLoad(effective, baseline, runtime, tuple(notices))

    # -- writing (runtime overlay only) -------------------------------------
    def save_runtime(self, manifest: ChapterManifest) -> Path:
        """Validate ``manifest`` and publish it atomically as the runtime overlay of its source.

        An existing overlay that is itself invalid is set aside first; if that is impossible
        nothing is written, so evidence is never overwritten.
        """
        if manifest.layer != LAYER_RUNTIME:
            raise ManifestStoreError(
                f"Only runtime overlays can be saved; got a {manifest.layer} manifest."
            )
        source = manifest.source
        if requires_verification(source) and manifest.verification is None:
            raise ManifestStoreError("Supreme Magus publication requires current-policy page verification.")
        payload = manifest.to_json()
        ChapterManifest.from_json(payload, expected_source=source, expected_layer=LAYER_RUNTIME)
        size = len(json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"))
        if size > MAX_MANIFEST_BYTES:
            raise ManifestStoreError(
                f"The chapter list is larger than the {MAX_MANIFEST_BYTES // (1024 * 1024)} MB limit."
            )
        path = self.runtime_path(source)
        with self.writer_lock(source):
            path.parent.mkdir(parents=True, exist_ok=True)
            if os.path.lexists(path):
                existing = self.load_layer(source, LAYER_RUNTIME)
                if existing.status in ("quarantine-failed", "unreadable"):
                    raise ManifestStoreError(
                        f"{existing.error} Nothing was written, so that file's contents are preserved."
                    )
            write_json_atomic(path, payload)
        return path

    # -- legacy migration (explicit, read-only on the old file) -------------
    def import_legacy_index(
        self, source: SourceIdentity, legacy_index_path: str | os.PathLike[str]
    ) -> LegacyImportResult:
        """Import one legacy ``chapter_index.json`` into the runtime overlay of ``source``.

        Only called explicitly; nothing scans old output folders. The legacy file is opened
        read-only and is never moved, rewritten or deleted. It is imported only when it passes
        :func:`parse_legacy_index`, does not contradict the baseline or the overlay, and adds
        something the effective manifest does not already hold. Verified overlay entries are
        kept as they are, so a newer overlay is never overwritten by older legacy data.
        """
        if requires_verification(source):
            return LegacyImportResult("rejected", "Supreme Magus legacy metadata requires independent current-policy revalidation; nothing imported.")
        path = Path(legacy_index_path)
        try:
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                return LegacyImportResult("rejected", f"{path.name} is not a regular file.")
            if info.st_size > MAX_MANIFEST_BYTES:
                return LegacyImportResult("rejected", "The legacy index is larger than the 20 MB limit.")
            with open(path, "rb") as handle:
                data = handle.read(MAX_MANIFEST_BYTES + 1)
        except OSError as exc:
            return LegacyImportResult("rejected", f"The legacy index could not be read: {exc}.")
        digest = hashlib.sha256(data).hexdigest()
        try:
            candidate = parse_legacy_index(
                source, data, now_utc=self.now_utc(), app_version=self.app_version
            )
        except ManifestError as exc:
            return LegacyImportResult("rejected", str(exc), legacy_sha256=digest)

        try:
            with self.writer_lock(source):
                return self._import_candidate(source, candidate, digest)
        except ManifestLockBusy as exc:
            return LegacyImportResult("error", str(exc), legacy_sha256=digest)

    def _import_candidate(
        self, source: SourceIdentity, candidate: ChapterManifest, digest: str
    ) -> LegacyImportResult:
        """The locked read-modify-write half of :meth:`import_legacy_index`."""
        loaded = self.load_effective(source)
        existing = [("baseline", loaded.baseline.manifest), ("runtime overlay", loaded.runtime.manifest)]
        for name, manifest in existing:
            if manifest is None:
                continue
            clashes = _merge(source, [(name, manifest), ("legacy index", candidate)]).conflicts
            if clashes:
                return LegacyImportResult(
                    "conflict",
                    "The legacy index contradicts verified data: "
                    + " ".join(c.message for c in clashes),
                    legacy_sha256=digest,
                )

        effective = loaded.effective
        known_pages = {c.page_url for c in effective.chapters}
        if all(c.page_url in known_pages for c in candidate.chapters) and all(
            effective.covers(a, b) for a, b in candidate.spans
        ):
            return LegacyImportResult("already-covered", legacy_sha256=digest)

        overlay = loaded.runtime.manifest
        if overlay is None:
            result = candidate
        else:
            merged = _merge(source, [("runtime overlay", overlay), ("legacy index", candidate)])
            note = "Merged a legacy chapter_index.json import."
            old = overlay.provenance.notes[: _MAX_NOTES - len(note) - 1]
            result = merged.as_manifest(
                LAYER_RUNTIME,
                Provenance(
                    generated_utc=overlay.provenance.generated_utc,
                    validated_utc=self.now_utc(),
                    app_version=self.app_version,
                    method=overlay.provenance.method,
                    legacy_index_sha256=digest,
                    notes=f"{old} {note}".strip(),
                ),
            )
        try:
            self.save_runtime(result)
        except (OSError, ManifestError, ManifestStoreError) as exc:
            return LegacyImportResult(
                "error", f"The imported chapter list could not be saved: {exc}", legacy_sha256=digest
            )
        return LegacyImportResult("imported", chapters=len(candidate.chapters), legacy_sha256=digest)

    # -- discovery evidence + explicit refresh ------------------------------
    def record_discovery(
        self,
        source: SourceIdentity,
        chapters: "list[ChapterIdentity]",
        spans: "tuple[tuple[int, int], ...]",
        *,
        method: str = "walk",
        notes: str = "",
    ) -> DiscoveryRecord:
        """Merge freshly discovered chapters and the spans they actually examined into the overlay.

        Everything already in the overlay is kept (a discovery delta is never saved on its own),
        the baseline is never touched, and evidence that contradicts either layer is refused
        rather than resolved. Load, merge and publish happen under :meth:`writer_lock`, so two
        processes cannot lose each other's update; a busy lock raises :class:`ManifestLockBusy`.
        """
        if requires_verification(source):
            raise ManifestStoreError("Supreme Magus discovery requires per-page policy evidence; metadata alone cannot certify coverage.")
        with self.writer_lock(source):
            loaded = self.load_effective(source)
            now = self.now_utc()
            try:
                candidate = ChapterManifest.create(
                    layer=LAYER_RUNTIME,
                    source=source,
                    provenance=Provenance(
                        generated_utc=now, validated_utc=now, app_version=self.app_version,
                        method=method, notes=notes[:_MAX_NOTES],
                    ),
                    spans=spans,
                    chapters=chapters,
                )
            except ManifestError as exc:
                return DiscoveryRecord("rejected", loaded.effective, str(exc))
            for name, manifest in (
                ("baseline", loaded.baseline.manifest), ("runtime overlay", loaded.runtime.manifest)
            ):
                if manifest is None:
                    continue
                clashes = _merge(source, [(name, manifest), ("discovery", candidate)]).conflicts
                if clashes:
                    return DiscoveryRecord(
                        "conflict", loaded.effective,
                        "The discovered chapters contradict verified data: "
                        + " ".join(c.message for c in clashes),
                    )
            known = {c.page_url for c in loaded.effective.chapters}
            if all(c.page_url in known for c in candidate.chapters) and all(
                loaded.effective.covers(a, b) for a, b in candidate.spans
            ):
                return DiscoveryRecord("unchanged", loaded.effective)

            overlay = loaded.runtime.manifest
            merged = _merge(source, [("runtime overlay", overlay), ("discovery", candidate)])
            result = merged.as_manifest(
                LAYER_RUNTIME,
                Provenance(
                    generated_utc=overlay.provenance.generated_utc if overlay else now,
                    validated_utc=now,
                    app_version=self.app_version,
                    method=overlay.provenance.method if overlay else method,
                    legacy_index_sha256=overlay.provenance.legacy_index_sha256 if overlay else None,
                    notes=(overlay.provenance.notes if overlay else notes)[:_MAX_NOTES],
                ),
            )
            projected = merge_manifests(source, loaded.baseline.manifest, result)
            try:
                path = self.save_runtime(result)
            except (OSError, ManifestError, ManifestStoreError) as exc:
                return DiscoveryRecord("error", projected, f"The chapter list could not be saved: {exc}")
            return DiscoveryRecord("recorded", projected, path=path)

    def refresh_runtime(self, source: SourceIdentity) -> Optional[Path]:
        """Set the current runtime overlay aside as ``<name>.bak-<utc>[-N]`` so it is rediscovered.

        The baseline is never touched. Returns the backup path, or ``None`` when there was no
        overlay. The backup name is claimed exclusively, so an earlier backup is never replaced;
        if the overlay cannot be moved it stays exactly where it was and
        :class:`ManifestStoreError` is raised.
        """
        with self.writer_lock(source):
            path = self.runtime_path(source)
            if not os.path.lexists(path):
                return None
            if requires_verification(source):
                manifest = self.load_layer(source, LAYER_RUNTIME, quarantine=False).manifest
                if manifest is None or manifest.verification is None:
                    raise ManifestStoreError("Refresh requires compatible verified observations.")
                # Copy rather than move: failures leave a whole manifest at its canonical path.
                backup = self._copy_verified_backup(path)
                invalidated = ChapterManifest.create(
                    layer=LAYER_RUNTIME, source=source, provenance=manifest.provenance,
                    verification=manifest.verification.invalidate("Explicit chapter-list refresh requires independent reobservation."),
                )
                self.save_runtime(invalidated)
                return backup
            try:
                return self._set_aside(path, "bak")
            except OSError as exc:
                raise ManifestStoreError(
                    f"The saved chapter list {path.name} could not be backed up ({exc}); it was left "
                    "unchanged and nothing was refreshed."
                ) from exc

    def restore_backup(self, source: SourceIdentity, backup: str | os.PathLike[str]) -> bool:
        """Put a :meth:`refresh_runtime` backup back, but only if no overlay exists again yet.

        Used when a refresh fails before it recorded anything, so a failed refresh does not leave
        the user without the list they had. Returns whether the backup was restored; a backup is
        never moved over a newer overlay.
        """
        self.assert_compatible(source)
        if requires_verification(source):
            # Refresh invalidation and conflicts must survive failure. Backups are inspection
            # artifacts, never an automatic route back to obsolete verification authority.
            return False
        with self.writer_lock(source):
            path = self.runtime_path(source)
            backup_path = Path(backup)
            if os.path.lexists(path) or not backup_path.is_file():
                return False
            try:
                os.replace(backup_path, path)
            except OSError:
                return False
            return True

    def _copy_verified_backup(self, path: Path) -> Path:
        digest = hashlib.sha256(self._read_bytes(path)).hexdigest()
        for attempt in range(_MAX_QUARANTINE_TRIES):
            backup = path.with_name(f"{path.name}.bak-{digest}-{attempt}")
            try:
                with backup.open("xb") as handle:
                    handle.write(self._read_bytes(path))
                    handle.flush()
                    os.fsync(handle.fileno())
            except FileExistsError:
                continue
            return backup
        raise ManifestStoreError("No unused backup name is available; nothing refreshed.")
