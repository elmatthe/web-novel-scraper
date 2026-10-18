"""Manifest-backed chapter preflight: coverage, discovery and selection (v0.3.0 Checkpoint 03.2a).

One entry point, :func:`resolve_chapters`, decides which chapters a run will fetch. Both pipeline
paths (the legacy synchronous path and the FreeWebNovel rescue conductor) call it, so the
selection rules exist exactly once. It replaces the old trust-the-output-folder index.

What it does, in order (plan §9.3):

1. Take the per-source writer lock (non-blocking). If another run holds it, this run can still
   *read* the saved list but may not write; a run that would need to discover refuses visibly.
2. Optionally ``refresh`` first: the saved runtime overlay is backed up (never deleted) and the
   baseline is left alone.
3. Load the effective manifest (tracked baseline + runtime overlay), surfacing quarantine,
   rejection and conflict notices. Offer a legacy output-folder ``chapter_index.json`` to the
   store's strict importer (never modified).
4. Compute what the request still lacks. A finite range that is fully covered needs **no TOC
   traffic**. An open-ended range always re-checks the tail, because the saved last chapter may
   be stale.
5. Discover only the uncovered window through the adapter's own ``build_chapter_index`` (so the
   577b5db offset reconciliation, binary search and ``count + 64`` ceiling are untouched),
   seeded with everything already known. Each reconciled window is persisted; coverage only grows
   over labels the adapter was actually asked about *and* accounted for, so an interrupted
   discovery never declares an unexamined window covered.
6. Select by manifest, keyed by source page URL, never by displayed number alone. Anything the
   request needs that the list neither holds nor records as confirmed-absent refuses the run with a
   diagnostic. A hole *inside* a list the site just returned (a sparse TOC) is reported as a TOC
   gap and is never turned into a claim that the source lacks the chapter.
"""

from __future__ import annotations

import re
from contextlib import ExitStack
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterable, Optional

from .chapter_manifest import (
    MAX_LABEL_NUMBER,
    ManifestError,
    accounted_numbers,
    accounted_spans,
    whole_number,
)
from .chapter_store import (
    ChapterManifestStore,
    EffectiveLoad,
    EffectiveManifest,
    ManifestLockBusy,
    ManifestStoreError,
)
from .models import ChapterMeta, SiteSpec
from .request_manager import ScrapeCancelled
from .source_identity import ChapterIdentity, ChapterLabel, SourceIdentity, SourceIdentityError

LogFn = Callable[[str], None]

# How far below the saved last chapter an open-ended run re-examines, so a stale or extended tail
# is noticed (plan §9.3 step 1). Bounded: the rest of the saved list is trusted as it is.
TAIL_RECHECK_MARGIN = 64
# A run of missing numbers longer than this inside a returned list is not credibly a sparse TOC;
# it looks like a failed or truncated discovery, so it is refused rather than reported as a gap.
MAX_TOC_GAP_RUN = 200
# ``ChapterMeta.index`` is a float for fractional labels, which cannot tell 627.1 from 627.10. The
# exact label text rides in ``ChapterMeta.extra`` under this key whenever a meta is built from a
# manifest entry (the only label-to-meta bridge besides ``ChapterLabel.to_index``).
LABEL_KEY = "manifest_label"

_MAX_LISTED_ISSUES = 5
_MAX_TITLE = 1000
_MAX_SOURCE_ID = 200
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class ChapterListRefused(RuntimeError):
    """The run cannot proceed on a verified chapter list (the message says exactly why)."""


@dataclass
class Selection:
    """What the run will fetch, and the facts the report needs about the selection."""

    chapters: list = field(default_factory=list)       # ChapterMeta, in manifest order
    effective_range: tuple = (0, 0)
    toc_gaps: list = field(default_factory=list)       # whole numbers the site's list skipped
    absent: list = field(default_factory=list)         # (label, evidence): source-confirmed absent
    discovered: bool = False                           # the adapter was asked this run
    empty_index: bool = False                          # the adapter returned nothing at all


# ── ChapterMeta <-> manifest identity bridge ─────────────────────────────────
def meta_label(meta: object) -> ChapterLabel:
    """The exact label of a meta: the manifest text carried in ``extra`` when it agrees with the
    numeric ``index``, otherwise the label of the index itself."""
    index = getattr(meta, "index", None)
    from_index = ChapterLabel.from_index(index)
    extra = getattr(meta, "extra", None)
    raw = extra.get(LABEL_KEY) if isinstance(extra, dict) else None
    if isinstance(raw, str):
        try:
            carried = ChapterLabel(raw)
        except SourceIdentityError:
            return from_index
        if carried.value == from_index.value:
            return carried
    return from_index


def _clean(text: str, limit: int) -> Optional[str]:
    cleaned = _CONTROL_CHARS.sub(" ", text).strip()[:limit].strip()
    return cleaned or None


def identity_from_meta(source: SourceIdentity, meta: object) -> ChapterIdentity:
    """Validate one discovered meta as a chapter of ``source`` (raises ``SourceIdentityError``)."""
    title = getattr(meta, "title", None)
    source_id = getattr(meta, "source_id", None)
    return source.chapter_identity(
        getattr(meta, "url", None),
        meta_label(meta),
        slug=getattr(meta, "slug", None),
        source_id=None if source_id is None else _clean(str(source_id), _MAX_SOURCE_ID),
        title=title if title is None or not isinstance(title, str) else _clean(title, _MAX_TITLE),
    )


def meta_from_identity(identity: ChapterIdentity, original: Optional[ChapterMeta] = None) -> ChapterMeta:
    """The legacy ``ChapterMeta`` of a manifest entry. A meta discovered this run is kept (its
    prefetched page rides in ``extra``); the exact label text is always attached."""
    extra = dict(original.extra) if original is not None else {}
    extra[LABEL_KEY] = identity.label.text
    return ChapterMeta(
        index=identity.label.to_index(),
        url=identity.page_url,
        title=identity.title,
        source_id=identity.source_id,
        slug=identity.slug,
        extra=extra,
    )


def clipped_spans(
    chapters: Iterable[ChapterIdentity], low: Optional[int], high: Optional[int]
) -> tuple[tuple[int, int], ...]:
    """Coverage a discovery may claim: the unbroken runs of whole numbers its chapters account for,
    clipped to the window the adapter was asked about (``None`` = unbounded on that side).

    Chapters just outside the asked window are real evidence and are stored, but the adapter's
    padding at its window edge is not a promise about their neighbours, so they claim nothing.
    """
    runs = accounted_spans(((0, MAX_LABEL_NUMBER),), accounted_numbers(chapters, ()))
    out: list[tuple[int, int]] = []
    for a, b in runs:
        a2 = a if low is None else max(a, low)
        b2 = b if high is None else min(b, high)
        if a2 <= b2:
            out.append((a2, b2))
    return tuple(out)


def _span_text(a: int, b: int) -> str:
    return f"chapter {a}" if a == b else f"chapters {a}-{b}"


# ── the preflight ────────────────────────────────────────────────────────────
class _Preflight:
    def __init__(
        self,
        source: SourceIdentity,
        *,
        start: int,
        end: Optional[int],
        store: ChapterManifestStore,
        discover: Callable[..., list],
        log: LogFn,
        legacy_index: Optional[Path],
        refresh: bool,
        cancel_event: object,
    ) -> None:
        self.source, self.start, self.end = source, start, end
        self.store, self.discover, self.log = store, discover, log
        self.legacy_index, self.refresh, self.cancel_event = legacy_index, refresh, cancel_event
        self.writable = False
        self.backup: Optional[Path] = None
        self.originals: dict[str, ChapterMeta] = {}
        self._shown: set[str] = set()

    # -- entry --------------------------------------------------------------
    def execute(self) -> Selection:
        with ExitStack() as stack:
            try:
                stack.enter_context(self.store.writer_lock(self.source))
                self.writable = True
            except ManifestLockBusy as exc:
                self.log(f"WARNING: {exc} Reading the saved chapter list only.")
            except ManifestStoreError as exc:
                raise ChapterListRefused(str(exc)) from exc
            if self.refresh:
                self._back_up_for_refresh()
            try:
                return self._resolve()
            except BaseException:
                self._keep_or_restore_backup()
                raise

    def _cancelled(self) -> bool:
        return bool(getattr(self.cancel_event, "is_set", lambda: False)())

    # -- refresh ------------------------------------------------------------
    def _back_up_for_refresh(self) -> None:
        if not self.writable:
            raise ChapterListRefused(
                "The chapter list cannot be refreshed right now: another run or program instance "
                "is updating it. Nothing was changed."
            )
        try:
            self.backup = self.store.refresh_runtime(self.source)
        except ManifestStoreError as exc:
            raise ChapterListRefused(str(exc)) from exc
        if self.backup is None:
            self.log("NOTE: chapter list refresh: no saved list existed, so it is built from the site.")
        else:
            self.log(
                f"NOTE: chapter list refresh: the saved list was kept as {self.backup.name}; "
                "the list is being rebuilt from the site (the tracked baseline is unchanged)."
            )

    def _keep_or_restore_backup(self) -> None:
        if self.backup is None:
            return
        if self.store.restore_backup(self.source, self.backup):
            self.log(
                f"NOTE: chapter list refresh did not finish, so the previous saved list "
                f"({self.backup.name}) was put back."
            )
        else:
            self.log(
                f"NOTE: chapter list refresh did not finish; the previous saved list is still "
                f"kept as {self.backup.name}."
            )

    # -- load ---------------------------------------------------------------
    def _announce(self, loaded: EffectiveLoad) -> None:
        for notice in loaded.notices:
            self.log(f"WARNING: {notice}")

    def _import_legacy(self, effective: EffectiveManifest) -> EffectiveManifest:
        legacy = self.legacy_index
        if legacy is None or not legacy.is_file():
            return effective
        result = self.store.import_legacy_index(self.source, legacy)
        if result.status == "imported":
            self.log(
                f"NOTE: the earlier {legacy.name} in the output folder passed every check and was "
                f"imported into the saved chapter list ({result.chapters} chapters; the file itself "
                "was not changed)."
            )
            loaded = self.store.load_effective(self.source)
            self._announce_new(loaded)
            return loaded.effective
        if result.status in ("rejected", "conflict", "error"):
            self.log(
                f"WARNING: the earlier {legacy.name} in the output folder was not used "
                f"({result.reason}) The chapter list is checked against the site instead; the file "
                "itself was not changed."
            )
        return effective

    def _announce_new(self, loaded: EffectiveLoad) -> None:
        # Notices were already shown for the first load; show only what a reload added.
        for notice in loaded.notices:
            if notice not in self._shown:
                self.log(f"WARNING: {notice}")

    # -- window planning ----------------------------------------------------
    def _window(self, effective: EffectiveManifest) -> Optional[tuple[int, Optional[int]]]:
        if self.end is None:
            wholes = [whole_number(c.label) for c in effective.chapters]
            known_hi = max((n for n in wholes if n is not None), default=None)
            if known_hi is None:
                return (self.start, None)
            lower = max(self.start, known_hi - TAIL_RECHECK_MARGIN)
            if self.start <= known_hi:
                gaps = effective.uncovered_spans(self.start, known_hi)
                if gaps:
                    lower = min(lower, gaps[0][0])
            return (lower, None)
        gaps = effective.uncovered_spans(self.start, self.end)
        return (gaps[0][0], gaps[-1][1]) if gaps else None

    # -- discovery ----------------------------------------------------------
    def _identities(self, metas: Iterable[object], *, report: bool) -> list[ChapterIdentity]:
        """Validate discovered metas. Invalid entries are dropped (and named), never persisted; a
        page listed twice with different facts is dropped entirely."""
        kept: dict[str, ChapterIdentity] = {}
        sources: dict[str, ChapterMeta] = {}
        problems: list[str] = []
        contradicted: set[str] = set()
        for meta in metas:
            try:
                identity = identity_from_meta(self.source, meta)
            except (SourceIdentityError, ManifestError, TypeError, ValueError) as exc:
                problems.append(f"chapter {getattr(meta, 'index', '?')}: {exc}")
                continue
            prior = kept.get(identity.page_url)
            if prior is None:
                kept[identity.page_url] = identity
                sources[identity.page_url] = meta
            elif (prior.label, prior.slug) != (identity.label, identity.slug):
                contradicted.add(identity.page_url)
                problems.append(
                    f"{identity.page_url} is listed as both chapter {prior.label} and "
                    f"chapter {identity.label}"
                )
        for url in contradicted:
            kept.pop(url, None)
            sources.pop(url, None)
        self.originals.update(sources)
        if problems and report:
            for line in problems[:_MAX_LISTED_ISSUES]:
                self.log(f"WARNING: a discovered chapter list entry was ignored ({line}).")
            if len(problems) > _MAX_LISTED_ISSUES:
                self.log(
                    f"WARNING: {len(problems) - _MAX_LISTED_ISSUES} more discovered entries were ignored."
                )
        return list(kept.values())

    def _record(self, identities: list[ChapterIdentity], low: Optional[int], high: Optional[int]):
        return self.store.record_discovery(
            self.source, identities, clipped_spans(identities, low, high), method="toc",
            notes="Discovered through the site adapter.",
        )

    def _checkpoint(self, partial: list, through_slug: int, low: int, high: Optional[int]) -> None:
        """Persist a reconciled window. Coverage stops at the last label whose slug has been fully
        resolved, so the unresolved remainder is never claimed. Never raises: a failed checkpoint
        costs only resumability."""
        try:
            identities = self._identities(partial, report=False)
            resolved = [
                whole_number(identity.label)
                for identity in identities
                if identity.slug is not None and identity.slug <= through_slug
            ]
            bound = max((n for n in resolved if n is not None), default=None)
            if bound is None:
                return
            cap = bound if high is None else min(high, bound)
            record = self.store.record_discovery(
                self.source, identities, clipped_spans(identities, low, cap), method="toc",
                notes="Interrupted-discovery checkpoint.",
            )
            if record.status in ("conflict", "rejected", "error"):
                self.log(f"WARNING: a chapter list checkpoint was not saved ({record.reason})")
        except Exception as exc:  # noqa: BLE001 - discovery must keep going
            self.log(f"WARNING: a chapter list checkpoint was not saved ({exc}).")

    def _discover(
        self, window: tuple[int, Optional[int]], effective: EffectiveManifest
    ) -> tuple[EffectiveManifest, Optional[tuple[int, int]], bool]:
        low, high = window
        if self._cancelled():
            raise ScrapeCancelled("cancelled before the chapter list was checked")
        seed = [meta_from_identity(c) for c in effective.chapters]
        self.log("Building chapter index (TOC) …")
        metas = self.discover(
            start_hint=low,
            end_hint=high,
            resume_metas=seed,
            index_progress_cb=lambda partial, through, _total: self._checkpoint(
                partial, through, low, high
            ),
        )
        if not metas and not effective.chapters:
            return effective, None, True
        identities = self._identities(metas, report=True)
        wholes = [n for n in (whole_number(c.label) for c in identities) if n is not None]
        examined = (min(wholes), max(wholes)) if wholes else None
        record = self._record(identities, low, high)
        if record.status == "recorded":
            self.log(f"  discovered {len(identities)} chapters; saved the chapter list.")
        elif record.status == "unchanged":
            self.log("  the site's chapter list matches the saved chapter list.")
        else:
            examined = None if record.status in ("conflict", "rejected") else examined
            note = (
                "this run uses it, but it will be rediscovered next time"
                if record.status == "error" else "its chapters could not be verified"
            )
            self.log(f"WARNING: the discovered chapter list was not saved ({record.reason}) — {note}.")
        return record.effective, examined, False

    # -- resolve + select ---------------------------------------------------
    def _resolve(self) -> Selection:
        if self.end is not None and self.start > self.end:
            raise ChapterListRefused(
                f"The start chapter ({self.start}) is after the end chapter ({self.end}); nothing was fetched."
            )
        loaded = self.store.load_effective(self.source, quarantine=self.writable)
        self._shown = set(loaded.notices)
        self._announce(loaded)
        effective = loaded.effective
        if self.writable and not self.refresh:
            effective = self._import_legacy(effective)

        window = self._window(effective)
        examined: Optional[tuple[int, int]] = None
        if window is not None:
            if not self.writable:
                raise ChapterListRefused(
                    "The chapter list has to be checked against the site for this range, but another "
                    "run or program instance is updating the saved list. Wait for it to finish and "
                    "try again; nothing was fetched."
                )
            effective, examined, empty = self._discover(window, effective)
            if empty:
                return Selection(effective_range=(self.start, self.start), discovered=True, empty_index=True)
        return self._select(effective, examined, discovered=window is not None)

    def _select(
        self, effective: EffectiveManifest, examined: Optional[tuple[int, int]], *, discovered: bool
    ) -> Selection:
        listed = effective.chapters
        wholes = sorted({n for n in (whole_number(c.label) for c in listed) if n is not None})
        if self.end is None:
            tail = [c for c in listed if c.label.value >= self.start]
            if not tail:
                where = (
                    f"the site's chapter list ends at chapter {wholes[-1]}" if wholes
                    else "no chapters could be listed for it"
                )
                raise ChapterListRefused(
                    f"Start chapter {self.start} is past the available chapters: {where}. "
                    "Nothing was fetched."
                )
            top_value = max(c.label.value for c in tail)
            high = int(top_value)
            high_value = top_value
        else:
            high, high_value = self.end, Decimal(self.end)

        gap_runs: list[tuple[int, int]] = []
        unresolved: list[tuple[int, int]] = []
        for a, b in effective.uncovered_spans(self.start, high):
            if examined is None:
                unresolved.append((a, b))
                continue
            low_x, high_x = examined
            if a < low_x:
                unresolved.append((a, min(b, low_x - 1)))
            if b > high_x:
                unresolved.append((max(a, high_x + 1), b))
            inner = (max(a, low_x), min(b, high_x))
            if inner[0] <= inner[1]:
                (unresolved if inner[1] - inner[0] + 1 > MAX_TOC_GAP_RUN else gap_runs).append(inner)
        if unresolved:
            raise ChapterListRefused(self._refusal(unresolved, wholes))

        low_value = Decimal(self.start)
        chapters = [
            meta_from_identity(c, self.originals.get(c.page_url))
            for c in listed
            if low_value <= c.label.value <= high_value
        ]
        absent = [
            (a.label.text, a.evidence)
            for a in effective.absent
            if self.start <= int(a.label.text) <= high
        ]
        if not discovered:
            self.log(
                f"  loaded {len(chapters)} chapters from the saved chapter list for "
                f"{self.source.site_key} (no TOC request needed)."
            )
        return Selection(
            chapters=chapters,
            effective_range=(self.start, high),
            toc_gaps=[n for a, b in gap_runs for n in range(a, b + 1)],
            absent=absent,
            discovered=discovered,
        )

    def _refusal(self, unresolved: list[tuple[int, int]], wholes: list[int]) -> str:
        reasons = []
        for a, b in unresolved:
            span = _span_text(a, b)
            verb = "is" if a == b else "are"
            if wholes and b < wholes[0]:
                reasons.append(
                    f"the site's chapter list starts at chapter {wholes[0]}, so {span} {verb} not available"
                )
            elif wholes and a > wholes[-1]:
                reasons.append(
                    f"the site's chapter list ends at chapter {wholes[-1]}, so {span} {verb} not available"
                )
            else:
                reasons.append(f"{span} could not be confirmed against the site's chapter list")
        asked = f"{self.start}-{self.end}" if self.end is not None else f"{self.start} onward"
        return (
            f"Chapters {asked} cannot be provided in full: " + "; ".join(reasons) + ". "
            "Nothing was fetched or written. Choose a range the site lists, or leave End blank to "
            "scrape through the last available chapter."
        )


def resolve_chapters(
    spec: SiteSpec,
    *,
    start: int,
    end: Optional[int],
    store: ChapterManifestStore,
    discover: Callable[..., list],
    log: LogFn,
    legacy_index: Optional[Path] = None,
    refresh: bool = False,
    cancel_event: object = None,
) -> Selection:
    """Decide the chapters of one run from the validated, source-specific manifest.

    ``end`` is ``None`` for an open-ended request. ``discover(start_hint=, end_hint=,
    resume_metas=, index_progress_cb=)`` runs the adapter's own discovery over one window and
    returns its chapter metas. Raises :class:`ChapterListRefused` when the request cannot be
    satisfied from verified data, and ``ScrapeCancelled`` (from ``discover`` or between steps)
    when the run is stopped; evidence already persisted is kept either way.
    """
    try:
        source = SourceIdentity.from_site_spec(spec)
    except SourceIdentityError as exc:
        raise ChapterListRefused(
            f"This website and novel cannot be identified safely, so no chapter list can be used for "
            f"it: {exc}"
        ) from exc
    return _Preflight(
        source, start=start, end=end, store=store, discover=discover, log=log,
        legacy_index=legacy_index, refresh=refresh, cancel_event=cancel_event,
    ).execute()
