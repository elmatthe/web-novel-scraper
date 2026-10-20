"""Manifest lifecycle for the explicitly scoped Supreme Magus observation policy.

Uses the existing preflight selection, store lock and atomic manifest publisher.
Legacy files are inspected before any operation that can mutate state.
"""
from __future__ import annotations

from dataclasses import replace

from .chapter_manifest import ChapterManifest, ManifestError, Provenance
from .chapter_store import EffectiveManifest, ManifestLockBusy, ManifestStoreError
from .chapter_verification import SourceBoundChanged, Verification
from .models import ChapterIndex
from .request_manager import ScrapeCancelled


def resolve_observed(preflight):
    from .chapter_preflight import ChapterListRefused
    p = preflight
    if p.end is not None and p.start > p.end:
        raise ChapterListRefused(f"The start chapter ({p.start}) is after the end chapter ({p.end}); nothing was fetched.")
    try:
        p.store.assert_compatible(p.source, p.legacy_index)
        if p._cancelled():
            raise ScrapeCancelled("cancelled before chapter-page discovery")
        with p.store.writer_lock(p.source):
            # Repeat under the writer lock: another publisher may have changed the file.
            p.store.assert_compatible(p.source, p.legacy_index)
            return _locked(p)
    except ManifestLockBusy as exc:
        if not p.refresh and p.end is not None:
            saved = _saved(p)
            if saved and saved.verification.complete_for(p.source) and saved.covers(p.start, p.end):
                p.log(f"NOTE: {exc} Using previous observations for this finite range.")
                return _select(p, saved, discovered=False)
        raise ChapterListRefused(str(exc)) from exc
    except (ManifestError, ManifestStoreError, OSError) as exc:
        raise ChapterListRefused(str(exc)) from exc


def _saved(p):
    loaded = p.store.load_effective(p.source, quarantine=False)
    if loaded.effective.has_conflicts:
        raise ManifestStoreError("Verified baseline and runtime observations conflict; inspect both files and approve a separate baseline disposition. "
                                 + " ".join(loaded.effective.notices()))
    # Schema-2 runtime is a complete observation snapshot, not an additive legacy overlay.
    return loaded.runtime.manifest or loaded.baseline.manifest


def validate_observed_resume(source, loaded, header, chapters, digest):
    """Conservative CP3 manifest gate, retained alongside CP5 publication admission."""
    from .chapter_verification import requires_verification
    from .run_journal import JournalError
    if not requires_verification(source):
        return
    manifest = loaded.runtime.manifest or loaded.baseline.manifest
    if (loaded.effective.has_conflicts or manifest is None or manifest.verification is None
            or not manifest.verification.complete_for(source)
            or header.manifest_sha256 is None or header.manifest_sha256 != digest):
        raise JournalError("Supreme Magus Resume requires the same complete current-policy verified manifest as the new run. Older or invalidated selections require revalidation and a new run; prior checksums cannot approve them.")
    known = {c.page_url: c for c in manifest.chapters}
    if any((now := known.get(c.page_url)) is None or now.label != c.label or now.slug != c.slug for c in chapters):
        raise JournalError("Supreme Magus Resume selection lacks matching verified page evidence. Start a new run.")


def guard_observed_resume_store(source, store):
    """Refuse legacy/invalidated state before even acquiring an old run's file lock."""
    from .chapter_verification import requires_verification
    from .run_journal import JournalError
    if not requires_verification(source):
        return
    try:
        loaded = store.load_effective(source, quarantine=False)
    except ManifestStoreError as exc:
        raise JournalError(str(exc)) from exc
    manifest = loaded.runtime.manifest or loaded.baseline.manifest
    if loaded.effective.has_conflicts or manifest is None or not manifest.verification.complete_for(source):
        raise JournalError("Supreme Magus Resume requires a complete current-policy manifest; missing or unresolved page evidence requires revalidation and a new run.")


def _select(p, manifest, *, discovered):
    from .chapter_preflight import ChapterListRefused, whole_number
    verification = manifest.verification
    if any(page.status == "conflict" for page in verification.pages):
        raise ChapterListRefused("Supreme Magus page observations conflict. Inspect the manifest and explicitly refresh the chapter list to revalidate; no older mapping is selected.")
    if not verification.complete_for(p.source):
        raise ChapterListRefused("Supreme Magus discovery has unresolved or inaccessible source pages; completeness is unproven. Verified partial observations were retained for retry, with no covered spans.")
    wholes = [n for c in manifest.chapters if (n := whole_number(c.label)) is not None]
    top = max(wholes, default=p.start)
    high = top if p.end is None else min(top, p.end)
    gaps = manifest.uncovered_spans(p.start, high) if p.start <= high else []
    if gaps:
        raise ChapterListRefused("Supreme Magus chapter identities remain unresolved: " + str(gaps)
                                 + ". Missing observations do not prove source-confirmed absence.")
    if not discovered:
        p.log(f"  loaded {len(manifest.chapters)} saved chapter identities from previous observations "
              f"under {verification.policy}; these are historical observations, not fresh website validation.")
    effective = EffectiveManifest(p.source, manifest.chapters, (), manifest.spans, False)
    return p._select(effective, (min(wholes), top) if discovered and wholes else None, discovered=discovered)


def _locked(p):
    from .chapter_preflight import ChapterListRefused, clipped_spans, identity_from_meta, meta_from_identity
    saved = _saved(p)
    if saved and any(page.status == "conflict" for page in saved.verification.pages) and not p.refresh:
        return _select(p, saved, discovered=False)
    if saved and not p.refresh and p.end is not None and saved.covers(p.start, p.end):
        return _select(p, saved, discovered=False)

    if p.refresh and saved:
        # Backup copying and invalidation use the same per-source writer lock. No restore
        # path runs for this policy, even on cancellation or an exception during discovery.
        backup = p.store.refresh_runtime(p.source)
        if backup is None:  # a verified read-only baseline still needs an invalidation overlay
            p.store.save_runtime(ChapterManifest.create(
                layer="runtime", source=p.source, provenance=saved.provenance,
                verification=saved.verification.invalidate("Explicit full chapter-list refresh.")))
        p.log("NOTE: chapter-list refresh invalidated previous observations; HTML-cache refresh is a separate control."
              + (f" Exact previous bytes retained at {backup}." if backup else ""))
        saved = _saved(p)

    seeds = ChapterIndex([meta_from_identity(c) for c in saved.chapters] if saved else [],
                         verified_manifest=saved,
                         recheck_tail=bool(saved and saved.verification.complete_for(p.source)))
    if seeds.recheck_tail:
        # Persist invalidation BEFORE any network read. If publication of a later conflict
        # fails, a subsequent process still cannot select the obsolete tail as verified.
        verification = saved.verification
        cutoff = max(0, verification.bound - 64)
        pending = verification.pending_tail(p.source)
        retained = [c for c in saved.chapters if c.slug <= cutoff]
        p.store.save_runtime(ChapterManifest.create(layer="runtime", source=p.source,
                                                   provenance=saved.provenance, chapters=retained,
                                                   verification=pending))
    last = None
    latest = None

    def publish(index, *_progress):
        nonlocal last, latest
        verification = getattr(index, "verification", None)
        if not isinstance(verification, Verification):
            raise ChapterListRefused("Supreme Magus discovery lacks current-policy persisted page evidence.")
        identities = [identity_from_meta(p.source, meta) for meta in index]
        candidate = ChapterManifest.create(
            layer="runtime", source=p.source,
            provenance=Provenance(verification.bound_utc, p.store.now_utc(), p.store.app_version, "walk"),
            chapters=identities,
            spans=clipped_spans(identities, None, None) if verification.complete_for(p.source) else (),
            verification=verification,
        )
        # A callback may be repeated by cancellation unwinding; publication is idempotent.
        # Acquisition times are part of integrity, but never break a content tie.
        key = (candidate.verification, candidate.chapters, candidate.spans)
        if key != last:
            p.store.save_runtime(candidate)
            last = key
        latest = candidate
        for meta in index:
            p.originals[meta.url] = meta  # same-run volatile HTML spool only

    try:
        index = p.discover(start_hint=p.start, end_hint=p.end, resume_metas=seeds,
                           index_progress_cb=publish)
    except SourceBoundChanged as exc:
        if saved:
            verification = saved.verification.invalidate(str(exc))
            verification = replace(verification, bound_confirmed=False,
                                   pages=tuple(replace(page, status="conflict") for page in verification.pages))
            p.store.save_runtime(ChapterManifest.create(layer="runtime", source=p.source,
                                                       provenance=saved.provenance, verification=verification))
        raise ChapterListRefused(str(exc)) from exc
    publish(index)
    if p._cancelled():
        raise ScrapeCancelled("cancelled after chapter-page discovery; verified observations retained")
    # Only this request's fresh reliable bound can justify clamping, never the saved flag.
    p.tail_confirmed = bool(getattr(index, "tail_confirmed", False) and latest.verification.complete_for(p.source))
    p.log(f"  saved {len(latest.chapters)} verified chapter observations; "
          f"{sum(not page.resolved for page in latest.verification.pages)} unresolved page outcomes.")
    return _select(p, latest, discovered=True)
