"""Saved/update list choices and interruption safety, entirely synthetic."""
from dataclasses import replace
import json

import pytest

from webnovel_scraper.adapters.freewebnovel import FreeWebNovelAdapter, chapter_url_for
from webnovel_scraper.chapter_manifest import ChapterManifest, Provenance
from webnovel_scraper.chapter_preflight import ChapterListRefused, resolve_chapters
from webnovel_scraper.chapter_verification import PageObservation, Verification
from webnovel_scraper.request_manager import ScrapeCancelled
from webnovel_scraper.source_identity import ChapterLabel
from .test_verified_manifest_lifecycle import SPEC, SOURCE, Pages, store_at

NOW = "2026-10-22T12:00:00Z"


def select(store, pages, *, mode="saved", refresh=False, end=5000, discover=None, logs=None):
    adapter = FreeWebNovelAdapter(pages)
    return resolve_chapters(SPEC, start=1, end=end, store=store, list_mode=mode, refresh=refresh,
        discover=discover or (lambda **kw: adapter.build_chapter_index(SPEC, **kw)),
        log=(logs if logs is not None else []).append, cancel_event=pages.cancel_event)


def test_complete_certified_4561_list_oversized_end_is_historical_and_read_only(tmp_path):
    store = store_at(tmp_path)
    labels = [str(n) for n in range(1, 4553)] + ["627.2", "627.3", "627.4", "627.5", "627.6",
                                                   "952.2", "952.3", "1923.1", "1923.2"]
    labels.sort(key=lambda label: ChapterLabel(label).sort_key)
    chapters, observations = [], []
    for slug in (1, 2):
        observations.append(PageObservation(chapter_url_for(SPEC, slug), "extra", "current-page", (), NOW))
    for slug, label in enumerate(labels, 3):
        url = chapter_url_for(SPEC, slug)
        chapters.append(SOURCE.chapter_identity(url, ChapterLabel(label), slug=slug))
        observations.append(PageObservation(url, "observed", "current-page", (label,), NOW))
    manifest = ChapterManifest.create(layer="runtime", source=SOURCE,
        provenance=Provenance(NOW, NOW, "0.3.0", "walk"), chapters=chapters, spans=((1,4552),),
        verification=Verification.create(bound=4563, bound_utc=NOW, bound_confirmed=True, pages=observations))
    store.save_runtime(manifest)
    before = store.runtime_path(SOURCE).read_bytes()
    for _restart in range(2):
        logs, pages = [], Pages({})
        selection = select(store_at(tmp_path), pages, logs=logs)
        assert len(selection.chapters) == 4561
        assert selection.effective_range == (1,4552) and not selection.discovered
        assert pages.calls == [] and any("newer chapters were not checked" in line for line in logs)
        assert not store_at(tmp_path).load_effective(SOURCE).effective.covers(4553,5000)
    assert store.runtime_path(SOURCE).read_bytes() == before


def test_update_only_rechecks_64_tail_slugs_and_two_new_slugs(tmp_path):
    store = store_at(tmp_path)
    select(store, Pages({n:n for n in range(1,101)}))
    fresh = Pages({n:n for n in range(1,103)})
    selection = select(store_at(tmp_path), fresh, mode="update", refresh=True)
    assert len(selection.chapters) == 102
    assert len(fresh.chapter_calls) == 66
    assert all(chapter_url_for(SPEC,n) not in fresh.chapter_calls for n in range(1,37))


def test_pending_tail_cannot_be_selected_fast_and_restart_retains_prior_comparison(tmp_path):
    store = store_at(tmp_path)
    select(store, Pages({n:n for n in range(1,101)}))
    pages = Pages({n:n for n in range(1,103)})
    adapter = FreeWebNovelAdapter(pages)
    def discover(**kwargs):
        callback = kwargs.pop("index_progress_cb")
        def stop(index, through, total):
            callback(index,through,total)
            if through >= 48:
                pages.cancel_event.set()
        return adapter.build_chapter_index(SPEC,index_progress_cb=stop,**kwargs)
    with pytest.raises(ScrapeCancelled):
        select(store, pages, mode="update", refresh=True, discover=discover)
    payload = json.loads(store.runtime_path(SOURCE).read_text())
    pending = [p for p in payload["verification"]["pages"] if p["status"] == "saved_unknown"]
    assert pending and all(p["prior_labels"] for p in pending)
    no_network = Pages({})
    with pytest.raises(ChapterListRefused,match="incomplete tail"):
        select(store_at(tmp_path),no_network)
    assert no_network.calls == []
    # No historical-completeness fallback is allowed. A missing tail must finish discovery.
    restart = Pages({n:n for n in range(1,103)})
    selection = select(store_at(tmp_path),restart,mode="update",refresh=True)
    assert len(selection.chapters) == 102
    assert all(chapter_url_for(SPEC,n) not in restart.chapter_calls for n in range(1,49))


def test_incremental_identity_shift_blocks_fast_mode_until_explicit_full_revalidation(tmp_path):
    store = store_at(tmp_path)
    select(store,Pages({n:n for n in range(1,101)}))
    shifted = {n:n for n in range(1,101)}
    shifted[99], shifted[100] = 100,99
    with pytest.raises(ChapterListRefused,match="conflict"):
        select(store_at(tmp_path),Pages(shifted),mode="update",refresh=True)
    untouched = Pages({})
    with pytest.raises(ChapterListRefused,match="conflict"):
        select(store_at(tmp_path),untouched)
    assert untouched.calls == []
    corrected = Pages({n:n for n in range(1,101)})
    assert len(select(store_at(tmp_path),corrected,mode="update",refresh=True).chapters) == 100
    assert len(corrected.chapter_calls) == 100


def test_regressed_source_bound_refuses_and_invalidates_previous_coverage(tmp_path):
    store = store_at(tmp_path)
    select(store,Pages({n:n for n in range(1,101)}))
    with pytest.raises(ChapterListRefused,match="bound decreased"):
        select(store_at(tmp_path),Pages({n:n for n in range(1,100)}),mode="update",refresh=True)
    with pytest.raises(ChapterListRefused,match="conflict"):
        select(store_at(tmp_path),Pages({}))
