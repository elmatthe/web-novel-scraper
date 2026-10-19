"""CP3 offline lifecycle tests: all state is synthetic and repository-contained via basetemp."""
from __future__ import annotations

import json
import re
import threading
from collections import Counter

import pytest

from webnovel_scraper import catalog
from webnovel_scraper.adapters.freewebnovel import FreeWebNovelAdapter, chapter_url_for
from webnovel_scraper.chapter_manifest import ChapterManifest, Provenance
from webnovel_scraper.chapter_preflight import ChapterListRefused, resolve_chapters
from webnovel_scraper.chapter_store import ChapterManifestStore
from webnovel_scraper.models import ChapterMismatchError
from webnovel_scraper.request_manager import NotFoundFetchError, ScrapeCancelled
from webnovel_scraper.source_identity import ChapterLabel, SourceIdentity

SPEC = catalog.get_spec("supreme-magus", "freewebnovel")
SOURCE = SourceIdentity.from_site_spec(SPEC)
POLICY = "sm-com-page-identity-v1"


class Pages:
    """Synthetic source with an explicit same-source index bound and observable request counts."""

    def __init__(self, labels):
        self.labels = labels
        self.calls = []
        self.cancel_event = threading.Event()

    def fetch(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url == SPEC.url:
            return f"<a href='{SPEC.url}/chapter-{max(self.labels)}'>Last page</a>"
        slug = int(re.search(r"chapter-(\d+)$", url)[1])
        value = self.labels[slug]
        if isinstance(value, Exception):
            raise value
        if isinstance(value, str) and value.startswith("<"):
            return value
        return (f"<html><body><h1>Chapter {value}: Synthetic chapter</h1>"
                "<div id='article'><p>Offline synthetic chapter prose.</p></div></body></html>")

    @property
    def chapter_calls(self):
        return Counter(url for url, _ in self.calls if url != SPEC.url)


def store_at(tmp_path):
    return ChapterManifestStore(tmp_path / "baseline", tmp_path / "runtime")


def run(store, pages, *, start=1, end=3, refresh=False, discover=None, logs=None):
    adapter = FreeWebNovelAdapter(pages)
    selection = resolve_chapters(
        SPEC, start=start, end=end, store=store, refresh=refresh,
        discover=discover or (lambda **kw: adapter.build_chapter_index(SPEC, **kw)),
        log=(logs if logs is not None else []).append, cancel_event=pages.cancel_event,
    )
    return selection, adapter


def labels(selection):
    return [(m.slug, ChapterLabel.from_meta(m).text) for m in selection.chapters]


def files_under(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def old_manifest(layer):
    # Deliberately valid schema-one checksum and full claimed coverage, with wrong URL labels.
    return ChapterManifest.create(
        layer=layer, source=SOURCE,
        provenance=Provenance("2026-10-18T10:00:00Z", "2026-10-18T10:00:00Z", "0.3.0", "walk"),
        toc_complete=True, spans=((1, 3),),
        chapters=[SOURCE.chapter_identity(chapter_url_for(SPEC, s), ChapterLabel.parse(str(4 - s)), slug=s)
                  for s in range(1, 4)],
    ).to_json()


def test_clean_persistence_second_finite_run_and_same_run_spool(tmp_path):
    store, first = store_at(tmp_path), Pages({1: 1, 2: 2, 3: 3})
    selection, adapter = run(store, first)
    path = store.runtime_path(SOURCE)
    assert path.is_file()
    payload = json.loads(path.read_text())
    assert payload["schema_version"] == 2
    assert payload["verification"]["policy"] == POLICY
    assert payload["verification"]["bound"] == 3
    assert len(payload["verification"]["pages"]) == 3
    assert all(p["observed_utc"] and p["provenance"] for p in payload["verification"]["pages"])
    assert "spool" not in path.read_text().lower()
    before = len(first.calls)
    for meta in selection.chapters:
        assert adapter.fetch_chapter(meta, SPEC).index == meta.index
    assert len(first.calls) == before == 4

    logs = []
    second = Pages({1: 999, 2: 999, 3: 999})
    again, _ = run(store_at(tmp_path), second, start=2, end=3, logs=logs)
    assert labels(again) == [(2, "2"), (3, "3")]
    assert not again.discovered and second.calls == []
    assert any("saved" in line.lower() or "previous" in line.lower() for line in logs)


def test_exact_fraction_duplicate_urls_and_reverse_numbering_survive_reload(tmp_path):
    store = store_at(tmp_path)
    pages = Pages({1: 627, 2: "627.1", 3: "627.10", 4: 628, 5: 627})
    first, _ = run(store, pages, start=627, end=628)
    second, _ = run(store_at(tmp_path), Pages({}), start=627, end=628)
    expected = [(1, "627"), (5, "627"), (2, "627.1"), (3, "627.10"), (4, "628")]
    assert labels(first) == labels(second) == expected
    assert len({m.url for m in second.chapters}) == 5


@pytest.mark.parametrize("layer", ["baseline", "runtime"])
@pytest.mark.parametrize("refresh", [False, True])
def test_valid_checksum_legacy_refusal_is_read_only_before_refresh(tmp_path, layer, refresh):
    store = store_at(tmp_path)
    path = store.baseline_path(SOURCE) if layer == "baseline" else store.runtime_path(SOURCE)
    path.parent.mkdir(parents=True)
    payload = old_manifest(layer)
    assert payload["schema_version"] == 1
    path.write_text(json.dumps(payload))
    before = files_under(tmp_path)
    with pytest.raises(ChapterListRefused, match="revalidat|policy|untrusted"):
        run(store, Pages({}), refresh=refresh,
            discover=lambda **_: pytest.fail("must refuse before discovery"))
    assert files_under(tmp_path) == before


def test_cancelled_prefix_is_persisted_without_coverage_and_restart_reuses_observations(tmp_path):
    store, pages = store_at(tmp_path), Pages({s: s for s in range(1, 41)})
    adapter = FreeWebNovelAdapter(pages)

    def discover(**kwargs):
        checkpoint = kwargs.pop("index_progress_cb")

        def stop(partial, through, total):
            checkpoint(partial, through, total)
            checkpoint(partial, through, total)  # repeated callbacks must be harmless
            if through >= 16:
                pages.cancel_event.set()

        return adapter.build_chapter_index(SPEC, index_progress_cb=stop, **kwargs)

    with pytest.raises(ScrapeCancelled):
        run(store, pages, end=40, discover=discover)
    loaded = store_at(tmp_path).load_effective(SOURCE).effective
    assert len(loaded.chapters) == 16
    assert not loaded.covers(1, 40)
    assert not loaded.spans  # unknown later pages could duplicate/reverse earlier numbers
    assert sum(pages.chapter_calls.values()) == 16
    restarted = Pages({s: s for s in range(1, 41)})
    selection, _ = run(store_at(tmp_path), restarted, end=40)
    assert len(selection.chapters) == 40
    assert sum(restarted.chapter_calls.values()) == 24
    assert all(chapter_url_for(SPEC, s) not in restarted.chapter_calls for s in range(1, 17))


def test_unreadable_page_keeps_partial_observations_and_is_only_page_retried(tmp_path):
    store = store_at(tmp_path)
    pages = Pages({1: 1, 2: NotFoundFetchError("offline missing page", status=404), 3: 3})
    with pytest.raises(ChapterListRefused, match="unresolved|unproven|inaccessible"):
        run(store, pages)
    loaded = store_at(tmp_path).load_effective(SOURCE).effective
    assert len(loaded.chapters) == 2
    assert not loaded.absent and not loaded.spans
    # An unresolved source page could carry any displayed number, including a duplicate 1.
    with pytest.raises(ChapterListRefused, match="unresolved|unproven|inaccessible"):
        run(store_at(tmp_path), Pages({1: 1, 2: NotFoundFetchError("still missing", status=404), 3: 3}), end=1)
    retry = Pages({1: 1, 2: 2, 3: 3})
    selection, _ = run(store_at(tmp_path), retry)
    assert labels(selection) == [(1, "1"), (2, "2"), (3, "3")]
    assert retry.chapter_calls == Counter({chapter_url_for(SPEC, 2): 1})


def test_open_request_checks_new_tail_and_does_not_treat_saved_end_as_current(tmp_path):
    store = store_at(tmp_path)
    run(store, Pages({1: 1, 2: 2, 3: 3}))
    extended = Pages({1: 1, 2: 2, 3: 3, 4: 4})
    selection, _ = run(store_at(tmp_path), extended, end=None)
    assert labels(selection) == [(1, "1"), (2, "2"), (3, "3"), (4, "4")]
    assert selection.discovered
    assert extended.calls[0][0] == SPEC.url
    assert chapter_url_for(SPEC, 4) in extended.chapter_calls


def test_normal_refresh_reobserves_every_page_and_does_not_seed_saved_authority(tmp_path):
    store = store_at(tmp_path)
    run(store, Pages({1: 1, 2: 2, 3: 3}))
    fresh = Pages({1: 1, 2: 2, 3: 3})
    selection, _ = run(store_at(tmp_path), fresh, refresh=True)
    assert selection.discovered and len(selection.chapters) == 3
    assert fresh.chapter_calls == Counter({chapter_url_for(SPEC, s): 1 for s in range(1, 4)})


def test_conflict_invalidates_old_mapping_until_explicit_fresh_refresh(tmp_path):
    store = store_at(tmp_path)
    run(store, Pages({1: 1, 2: 2, 3: 3}))
    with pytest.raises(ChapterListRefused, match="conflict|contradict"):
        run(store_at(tmp_path), Pages({1: 1, 2: 9, 3: 3}), end=None)
    payload = json.loads(store.runtime_path(SOURCE).read_text())
    conflicts = [p for p in payload["verification"]["pages"] if p["status"] == "conflict"]
    assert len(conflicts) == 1
    assert set(conflicts[0]["labels"]) == {"2", "9"}
    with pytest.raises(ChapterListRefused, match="conflict|contradict|unresolved"):
        run(store_at(tmp_path), Pages({}), start=2, end=2,
            discover=lambda **_: pytest.fail("conflict must require explicit refresh"))
    corrected, _ = run(store_at(tmp_path), Pages({1: 1, 2: 2, 3: 3}), refresh=True)
    assert labels(corrected) == [(1, "1"), (2, "2"), (3, "3")]


@pytest.mark.parametrize("tamper", ["policy", "provenance", "label", "host", "missing-policy",
                                    "status-type", "provenance-type", "url-type"])
def test_tampered_verified_state_is_refused_without_quarantine_or_refresh(tmp_path, tamper):
    store = store_at(tmp_path)
    run(store, Pages({1: 1, 2: 2, 3: 3}))
    path = store.runtime_path(SOURCE)
    payload = json.loads(path.read_text())
    if tamper == "policy":
        payload["verification"]["policy"] = "obsolete-sm-policy"
    elif tamper == "provenance":
        payload["verification"]["pages"][0]["provenance"] = "saved-unknown"
    elif tamper == "label":
        payload["verification"]["pages"][0]["labels"] = ["999"]
    elif tamper == "host":
        payload["verification"]["pages"][0]["url"] = payload["verification"]["pages"][0]["url"].replace(".com/", ".net/")
    elif tamper == "missing-policy":
        del payload["verification"]["policy"]
    elif tamper == "status-type":
        payload["verification"]["pages"][0]["status"] = []
    elif tamper == "provenance-type":
        payload["verification"]["pages"][0]["provenance"] = {}
    else:
        payload["verification"]["pages"][0]["url"] = []
    path.write_text(json.dumps(payload))
    before = files_under(tmp_path)
    with pytest.raises(ChapterListRefused, match="revalidat|policy|evidence|untrusted|integrity|checksum"):
        run(store_at(tmp_path), Pages({}), refresh=True,
            discover=lambda **_: pytest.fail("invalid state must be refused before discovery"))
    assert files_under(tmp_path) == before


def test_loaded_manifest_never_skips_independent_content_identity_guard(tmp_path):
    store = store_at(tmp_path)
    run(store, Pages({1: 1, 2: 2, 3: 3}))
    changed = Pages({1: 1, 2: 999, 3: 3})
    selection, adapter = run(store_at(tmp_path), changed, start=2, end=2)
    assert changed.calls == []
    with pytest.raises(ChapterMismatchError):
        adapter.fetch_chapter(selection.chapters[0], SPEC)
    assert changed.chapter_calls[chapter_url_for(SPEC, 2)] >= 1


def test_cancel_before_first_observation_does_not_publish_manifest(tmp_path):
    store, pages = store_at(tmp_path), Pages({1: 1, 2: 2, 3: 3})
    pages.cancel_event.set()
    with pytest.raises(ScrapeCancelled):
        run(store, pages)
    assert not store.runtime_path(SOURCE).exists()
    assert pages.calls == []


@pytest.mark.parametrize("has_verified_manifest", [False, True])
def test_valid_legacy_output_index_cannot_certify_or_mix_with_new_policy(tmp_path, has_verified_manifest):
    store = store_at(tmp_path)
    if has_verified_manifest:
        run(store, Pages({1: 1, 2: 2, 3: 3}))
    legacy = tmp_path / "previous-output" / "chapter_index.json"
    legacy.parent.mkdir()
    legacy.write_text(json.dumps({
        "schema_version": 2, "complete": True,
        "identity": {"novel_slug": SOURCE.novel_slug, "adapter_key": SOURCE.adapter_key,
                     "source_url": SOURCE.index_url, "base_url": SOURCE.origin},
        "chapters": [{"index": s, "url": chapter_url_for(SPEC, s), "slug": s} for s in range(1, 4)],
    }))
    before = files_under(tmp_path)
    with pytest.raises(ChapterListRefused, match="revalidat|policy|untrusted|legacy"):
        resolve_chapters(SPEC, start=1, end=3, store=store_at(tmp_path), legacy_index=legacy,
                         discover=lambda **_: pytest.fail("legacy evidence must be refused first"),
                         log=lambda _: None)
    assert files_under(tmp_path) == before


def test_interrupted_tail_recheck_cannot_erase_prior_conflict_comparison(tmp_path):
    store = store_at(tmp_path)
    run(store, Pages({1: 1, 2: 2, 3: 3}))

    class FailedIndex(Pages):
        def fetch(self, url, **kwargs):
            if url == SPEC.url:
                raise RuntimeError("synthetic interrupted index fetch")
            return super().fetch(url, **kwargs)

    with pytest.raises(RuntimeError, match="interrupted index"):
        run(store_at(tmp_path), FailedIndex({1: 1, 2: 2, 3: 3}), end=None)
    # Pending revalidation must retain the old facts for contradiction detection across restart.
    # A permutation retains complete displayed-number coverage, so gaps cannot mask this defect.
    with pytest.raises(ChapterListRefused, match="conflict|contradict"):
        run(store_at(tmp_path), Pages({1: 2, 2: 1, 3: 3}))


@pytest.mark.parametrize("state", ["empty", "conflict", "current-with-old-header"])
def test_prior_run_selection_cannot_bypass_missing_or_invalidated_manifest_authority(tmp_path, state):
    from webnovel_scraper import pipeline
    from webnovel_scraper.models import OutputMode, ScrapeJob
    from webnovel_scraper.run_journal import JournalError, RunHeader, RunJournal, selection_sha256
    from webnovel_scraper.run_storage import allocate_run_dir

    store = store_at(tmp_path)
    if state != "empty":
        run(store, Pages({1: 1, 2: 2, 3: 3}))
    if state == "conflict":
        with pytest.raises(ChapterListRefused, match="conflict|contradict"):
            run(store, Pages({1: 1, 2: 9, 3: 3}), end=None)
    chapters = [SOURCE.chapter_identity(chapter_url_for(SPEC, s), ChapterLabel.parse(str(s)), slug=s)
                for s in range(1, 4)]
    reserved = allocate_run_dir(tmp_path / "synthetic-prior-output", SOURCE.novel_slug)
    header = RunHeader(
        novel_slug=SOURCE.novel_slug, run_number=reserved.number, source=SOURCE,
        selection_sha256=selection_sha256(chapters), selection_count=len(chapters),
        first_label="1", last_label="3", requested=(1, 3), effective=(1, 3),
        formats=("txt",), grouping_mode="separate", chunk_size=10,
    )
    RunJournal.create(reserved.path, header, chapters)
    job = ScrapeJob(novel_slug=SOURCE.novel_slug, adapter_key=SOURCE.adapter_key, start=1, end=3,
                    delay=0, output_mode=OutputMode.SEPARATE, use_cache=False,
                    output_dir=reserved.path, formats=("txt",), resume=True)
    with pytest.raises(JournalError, match="policy|revalidat|Resume|resume|verified|evidence"):
        result = pipeline._open_resume(job, SPEC, store, lambda _: None)
        result[1].release()  # release the run lock even when demonstrating the unsafe success


def test_new_policy_run_can_resume_only_its_exact_certified_snapshot(tmp_path):
    from webnovel_scraper import pipeline
    from webnovel_scraper.models import OutputMode, ScrapeJob
    from webnovel_scraper.run_journal import RunHeader, RunJournal, selection_sha256
    from webnovel_scraper.run_storage import allocate_run_dir

    store = store_at(tmp_path)
    run(store, Pages({1: 1, 2: 2, 3: 3}))
    effective = store.load_effective(SOURCE).effective
    chapters = list(effective.chapters)
    reserved = allocate_run_dir(tmp_path / "new-policy-output", SOURCE.novel_slug)
    header = RunHeader(
        novel_slug=SOURCE.novel_slug, run_number=reserved.number, source=SOURCE,
        selection_sha256=selection_sha256(chapters), selection_count=len(chapters),
        first_label="1", last_label="3", requested=(1, 3), effective=(1, 3),
        formats=("txt",), grouping_mode="separate", chunk_size=10,
        manifest_sha256=pipeline._manifest_digest(effective),
    )
    RunJournal.create(reserved.path, header, chapters)
    job = ScrapeJob(novel_slug=SOURCE.novel_slug, adapter_key=SOURCE.adapter_key, start=1, end=3,
                    delay=0, output_mode=OutputMode.SEPARATE, use_cache=False,
                    output_dir=reserved.path, formats=("txt",), resume=True)
    result = pipeline._open_resume(job, SPEC, store, lambda _: None)
    try:
        assert result[3] == chapters
    finally:
        result[1].release()
