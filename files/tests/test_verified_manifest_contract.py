"""CP3 strict contract, store boundaries and publication fault injection (offline)."""
from dataclasses import replace
import hashlib
import json
import threading

import pytest

from webnovel_scraper import atomic_io, chapter_store
from webnovel_scraper.chapter_manifest import ChapterManifest, ManifestError, Provenance, parse_manifest_bytes
from webnovel_scraper.chapter_preflight import ChapterListRefused, resolve_chapters
from webnovel_scraper.chapter_store import ChapterManifestStore, ManifestStoreError, merge_manifests
from webnovel_scraper.chapter_verification import PageObservation, Verification
from webnovel_scraper.request_manager import ScrapeCancelled, NotFoundFetchError
from webnovel_scraper.source_identity import ChapterLabel
from .test_verified_manifest_lifecycle import (
    SPEC, SOURCE, Pages, run, store_at, old_manifest, files_under, chapter_url_for,
)

NOW = "2026-10-18T10:00:00Z"


def certified(*, labels=("1", "2", "3"), layer="runtime"):
    chapters = [SOURCE.chapter_identity(chapter_url_for(SPEC, s), ChapterLabel(label), slug=s)
                for s, label in enumerate(labels, 1)]
    pages = [PageObservation(c.page_url, "observed", "current-page", (c.label.text,), NOW)
             for c in chapters]
    verification = Verification.create(bound=len(labels), bound_utc=NOW, bound_confirmed=True, pages=pages)
    from webnovel_scraper.chapter_preflight import clipped_spans
    return ChapterManifest.create(layer=layer, source=SOURCE, chapters=chapters,
        provenance=Provenance(NOW, NOW, "0.3.0", "walk"),
        spans=clipped_spans(chapters, None, None), verification=verification)


@pytest.mark.parametrize("field", ["provenance", "bound-time", "observation-time", "notes", "completion"])
def test_schema_two_integrity_includes_provenance_bound_and_evidence(field):
    manifest = certified()
    raw = manifest.to_json()
    if field == "provenance":
        raw["provenance"]["app_version"] = "0.3.1"
    elif field == "bound-time":
        raw["verification"]["bound_utc"] = "2026-10-18T11:00:00Z"
    elif field == "observation-time":
        raw["verification"]["pages"][0]["observed_utc"] = "2026-10-18T11:00:00Z"
    elif field == "notes":
        raw["provenance"]["notes"] = "altered acquisition context"
    else:
        raw["coverage"]["toc_complete"] = True
    with pytest.raises(ManifestError):
        ChapterManifest.from_json(raw, expected_source=SOURCE, expected_layer="runtime")


@pytest.mark.parametrize("status", ["unknown", "inaccessible", "unreadable", "saved_unknown", "candidate", "conflict"])
def test_unresolved_identity_cannot_certify_a_page_or_full_coverage(status):
    manifest = certified()
    fact = replace(manifest.verification.pages[0], status=status)
    verification = replace(manifest.verification, pages=(fact,) + manifest.verification.pages[1:])
    with pytest.raises(ManifestError):
        replace(manifest, verification=verification)
    partial = replace(manifest, chapters=manifest.chapters[1:], spans=(), verification=verification)
    assert not partial.covers(1, 3)
    assert not partial.toc_complete


def test_verified_extra_is_distinct_from_unavailable_and_source_absence():
    manifest = certified()
    extra = replace(manifest.verification.pages[0], status="extra", labels=())
    verification = replace(manifest.verification, pages=(extra,) + manifest.verification.pages[1:])
    valid = replace(manifest, chapters=manifest.chapters[1:], spans=((2, 3),), verification=verification)
    assert valid.verification.complete_for(SOURCE) and not valid.absent
    with pytest.raises(ManifestError):
        replace(extra, status="source_absent")


@pytest.mark.parametrize("change", ["host", "adapter", "novel"])
def test_wrong_source_never_accepts_new_policy(change):
    manifest = certified()
    if change == "host":
        source = replace(SOURCE, origin="https://freewebnovel.net", index_url=SOURCE.index_url.replace(".com/", ".net/"))
    elif change == "adapter":
        raw = manifest.to_json()
        raw["source"]["adapter_key"] = "webnovel_dynamic"
        with pytest.raises(ManifestError):
            ChapterManifest.from_json(raw, expected_source=SOURCE, expected_layer="runtime")
        return
    else:
        source = replace(SOURCE, novel_slug="shadow-slave")
    with pytest.raises((ManifestError, ValueError)):
        ChapterManifest.from_json(manifest.to_json(), expected_source=source, expected_layer="runtime")


def test_same_page_two_exact_labels_and_missing_observations_are_rejected():
    manifest = certified()
    verification = manifest.verification
    with pytest.raises(ManifestError):
        replace(verification, pages=verification.pages + (replace(verification.pages[0], labels=("9",)),))
    with pytest.raises(ManifestError):
        replace(manifest, verification=replace(verification, pages=verification.pages[:-1]))
    with pytest.raises(ManifestError):
        replace(manifest, verification=replace(verification, bound_confirmed=False))
    with pytest.raises(ManifestError):
        replace(verification.pages[0], prior_labels=("9",), prior_observed_utc=NOW)


@pytest.mark.parametrize("operation", ["load", "lock", "save", "record", "refresh", "restore", "import", "layer", "merge"])
def test_valid_legacy_state_is_read_only_at_every_store_entry(tmp_path, operation):
    store = store_at(tmp_path)
    path = store.runtime_path(SOURCE)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(old_manifest("runtime")))
    before = files_under(tmp_path)
    manifest = certified()
    if operation == "import":
        assert store.import_legacy_index(SOURCE, tmp_path / "index.json").status == "rejected"
    elif operation == "layer":
        assert store.load_layer(SOURCE, "runtime").status == "incompatible"
    elif operation == "merge":
        legacy = parse_manifest_bytes(path.read_bytes(), expected_source=SOURCE, expected_layer="runtime")
        with pytest.raises(ManifestError):
            merge_manifests(SOURCE, None, legacy)
    else:
        with pytest.raises(ManifestStoreError):
            if operation == "load":
                store.load_effective(SOURCE)
            elif operation == "lock":
                with store.writer_lock(SOURCE):
                    pytest.fail("legacy state acquired mutating lock")
            elif operation == "save":
                store.save_runtime(manifest)
            elif operation == "record":
                store.record_discovery(SOURCE, list(manifest.chapters), manifest.spans)
            elif operation == "refresh":
                store.refresh_runtime(SOURCE)
            else:
                store.restore_backup(SOURCE, tmp_path / "old.bak")
    assert files_under(tmp_path) == before


def test_migration_preview_is_deterministic_and_proposes_unique_exact_byte_backup(tmp_path):
    store = store_at(tmp_path)
    path = store.runtime_path(SOURCE)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(old_manifest("runtime")))
    first = store.migration_preview(SOURCE)
    assert first == store.migration_preview(SOURCE)
    entry = first["files"][0]
    assert entry["schema"] == 1 and entry["policy"] is None
    assert entry["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    from pathlib import Path
    backup = Path(entry["proposed_backup"])
    backup.write_bytes(b"preexisting unrelated backup")
    assert store.migration_preview(SOURCE)["files"][0]["proposed_backup"] != str(backup)
    assert backup.read_bytes() == b"preexisting unrelated backup"


def test_runtime_invalidation_cannot_reintroduce_verified_baseline(tmp_path):
    store = store_at(tmp_path)
    baseline = certified(layer="baseline")
    path = store.baseline_path(SOURCE)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(baseline.to_json()))
    before = path.read_bytes()
    class Interrupted(Pages):
        def fetch(self, url, **kwargs):
            raise ScrapeCancelled("before first refreshed observation")
    with pytest.raises(ScrapeCancelled):
        run(store, Interrupted({1: 1, 2: 2, 3: 3}), refresh=True)
    effective = store.load_effective(SOURCE).effective
    assert not effective.chapters and not effective.spans
    assert path.read_bytes() == before


def test_verified_baseline_runtime_disagreement_is_inspectable_and_refused(tmp_path):
    store = store_at(tmp_path)
    path = store.baseline_path(SOURCE)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(certified(layer="baseline").to_json()))
    store.save_runtime(certified(labels=("2", "1", "3")))
    before = files_under(tmp_path)
    assert store.load_effective(SOURCE).effective.has_conflicts
    with pytest.raises(ChapterListRefused, match="conflict"):
        run(store, Pages({}))
    from webnovel_scraper.observed_preflight import guard_observed_resume_store
    from webnovel_scraper.run_journal import JournalError
    with pytest.raises(JournalError):
        guard_observed_resume_store(SOURCE, store)
    assert files_under(tmp_path) == before


@pytest.mark.parametrize("after", [0, 7, 16, 23])
def test_refresh_interruption_keeps_new_observations_without_restoring_old_authority(tmp_path, after):
    store = store_at(tmp_path)
    run(store, Pages({s: s for s in range(1, 41)}), end=40)
    before = store.runtime_path(SOURCE).read_bytes()
    class Interrupted(Pages):
        def fetch(self, url, **kwargs):
            if url != SPEC.url and sum(self.chapter_calls.values()) >= after:
                raise ScrapeCancelled("synthetic refresh interruption")
            return super().fetch(url, **kwargs)
    with pytest.raises(ScrapeCancelled):
        run(store_at(tmp_path), Interrupted({s: s for s in range(1, 41)}), end=40, refresh=True)
    loaded = store.load_effective(SOURCE).effective
    assert len(loaded.chapters) == after and not loaded.spans
    backups = list(store.runtime_path(SOURCE).parent.glob("*.bak-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    assert not store.restore_backup(SOURCE, backups[0])
    assert len(store.load_effective(SOURCE).effective.chapters) == after


def test_failed_atomic_replace_does_not_leave_half_manifest(tmp_path, monkeypatch):
    store = store_at(tmp_path)
    store.save_runtime(certified())
    path = store.runtime_path(SOURCE)
    before = path.read_bytes()
    def fail(*args):
        raise OSError("injected atomic replacement failure")
    monkeypatch.setattr(atomic_io.os, "replace", fail)
    with pytest.raises(OSError):
        store.save_runtime(certified(labels=("1", "2", "3", "4")))
    assert path.read_bytes() == before and not list(path.parent.glob("*.tmp"))


def test_failure_after_refresh_backup_preserves_exact_backup_and_old_bytes(tmp_path, monkeypatch):
    store = store_at(tmp_path)
    run(store, Pages({1: 1, 2: 2, 3: 3}))
    path = store.runtime_path(SOURCE)
    before = path.read_bytes()
    def fail(*args):
        raise OSError("injected invalidation publication failure")
    monkeypatch.setattr(chapter_store, "write_json_atomic", fail)
    fresh = Pages({1: 1, 2: 2, 3: 3})
    with pytest.raises(ChapterListRefused, match="publication failure"):
        run(store, fresh, refresh=True)
    assert fresh.calls == [] and path.read_bytes() == before
    assert [p.read_bytes() for p in path.parent.glob("*.bak-*")] == [before]


def test_conflict_publication_failure_cannot_restore_old_tail_authority(tmp_path, monkeypatch):
    store = store_at(tmp_path)
    run(store, Pages({1: 1, 2: 2, 3: 3}))
    real = chapter_store.write_json_atomic
    def fail_conflict(path, payload):
        if any(p["status"] == "conflict" for p in payload["verification"]["pages"]):
            raise OSError("injected conflict publication failure")
        real(path, payload)
    monkeypatch.setattr(chapter_store, "write_json_atomic", fail_conflict)
    with pytest.raises(ChapterListRefused, match="publication failure"):
        run(store, Pages({1: 2, 2: 1, 3: 3}), end=None)
    manifest = store.load_layer(SOURCE, "runtime", quarantine=False).manifest
    assert not manifest.spans and not manifest.chapters
    assert any(p.prior_labels for p in manifest.verification.pages)


def test_writer_contention_allows_finite_read_but_never_destructive_fallback(tmp_path):
    store = store_at(tmp_path)
    run(store, Pages({1: 1, 2: 2, 3: 3}))
    locked, release = threading.Event(), threading.Event()
    def hold():
        with store.writer_lock(SOURCE):
            locked.set()
            release.wait(10)
    worker = threading.Thread(target=hold)
    worker.start()
    assert locked.wait(5)
    try:
        second = store_at(tmp_path)
        before = second.runtime_path(SOURCE).read_bytes()
        selected, _ = run(second, Pages({}))
        assert len(selected.chapters) == 3
        for kwargs in ({"refresh": True}, {"end": None}):
            with pytest.raises(ChapterListRefused, match="Another run"):
                run(second, Pages({}), **kwargs)
        assert second.runtime_path(SOURCE).read_bytes() == before
    finally:
        release.set()
        worker.join(5)


def test_changed_tail_bound_does_not_restore_saved_end_or_coverage(tmp_path):
    store = store_at(tmp_path)
    run(store, Pages({1: 1, 2: 2, 3: 3}))
    with pytest.raises(ChapterListRefused, match="bound decreased"):
        run(store, Pages({1: 1, 2: 2}), end=None)
    saved = store.load_layer(SOURCE, "runtime", quarantine=False).manifest
    assert not saved.spans and not saved.chapters
    assert not saved.verification.bound_confirmed
    with pytest.raises(ChapterListRefused, match="conflict"):
        run(store, Pages({}), end=1)


def test_unresolved_retry_preserves_previous_identity_comparison(tmp_path):
    store = store_at(tmp_path)
    run(store, Pages({1: 1, 2: 2, 3: 3}))
    with pytest.raises(ChapterListRefused, match="unresolved"):
        run(store, Pages({1: 1, 2: NotFoundFetchError("404", status=404), 3: 3}), end=None)
    with pytest.raises(ChapterListRefused, match="conflict"):
        run(store, Pages({1: 1, 2: 9, 3: 3}))


@pytest.mark.parametrize("rescue", [False, True])
def test_both_pipeline_paths_persist_and_reuse_policy_snapshots(tmp_path, rescue):
    from webnovel_scraper import pipeline, rescue_pool as rp
    from webnovel_scraper.adapters.freewebnovel import FreeWebNovelAdapter
    from webnovel_scraper.models import ScrapeJob, OutputMode
    from .test_chapter_preflight import _Clock, HostRateLimiter
    clock = _Clock()
    limiter = HostRateLimiter(0, monotonic=clock.now, sleep=clock.sleep)
    store = store_at(tmp_path)
    pages = Pages({1: 1, 2: 2, 3: 3})
    pages.headless, pages.last_fetch_info = True, None
    pages.close = lambda: None
    def perform(folder):
        job = ScrapeJob(novel_slug=SOURCE.novel_slug, adapter_key=SOURCE.adapter_key, start=1, end=3,
                        delay=0, output_mode=OutputMode.SEPARATE, use_cache=False, use_browser=rescue,
                        output_dir=tmp_path / folder, formats=("txt",))
        kwargs = dict(manifest_store=store, log=lambda _: None, sleep_fn=clock.sleep,
                      monotonic_fn=clock.now, host_limiter=limiter)
        if rescue:
            kwargs.update(request_manager_factory=lambda **_: pages,
                          primary_adapter_factory=lambda manager: FreeWebNovelAdapter(manager),
                          rescue_pool_factory=lambda: rp.RescuePool(
                              primary_headless=True, host_limiter=limiter, cancel_event=pages.cancel_event,
                              monotonic=clock.now, sleep=clock.sleep,
                              fetch_fn=lambda *a, **k: pytest.fail("unexpected rescue request"),
                              manager_factory=lambda **_: pages, adapter_factory=lambda manager: object(),
                              log=lambda _: None))
        else:
            kwargs["adapter"] = FreeWebNovelAdapter(pages)
        return pipeline.run_scrape(job, **kwargs)
    first = perform("first")
    assert len(first.written) == 3 and not first.failed
    assert sum(pages.chapter_calls.values()) == 3
    second = perform("second")
    assert len(second.written) == 3 and not second.failed
    assert sum(pages.chapter_calls.values()) == 6  # second run validates actual content afresh
    assert sum(url == SPEC.url for url, _ in pages.calls) == 1
