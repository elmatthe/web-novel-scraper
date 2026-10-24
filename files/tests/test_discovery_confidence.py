"""Checkpoint 2: offline identity observations, never an inferred live novel map."""
from __future__ import annotations

import gc
import json
import re
import threading
from collections import Counter
from pathlib import Path

import pytest

from webnovel_scraper import catalog
from webnovel_scraper.adapters import freewebnovel as fwn
from webnovel_scraper.chapter_preflight import ChapterListRefused, resolve_chapters
from webnovel_scraper.chapter_store import ChapterManifestStore
from webnovel_scraper.models import ChapterIndex, ChapterMeta, DiscoveryEvidence
from webnovel_scraper.request_manager import NotFoundFetchError, RateLimitedFetchError, ScrapeCancelled
from webnovel_scraper.source_identity import ChapterLabel, SourceIdentity

SPEC = catalog.get_spec("supreme-magus", "freewebnovel")
SOURCE = SourceIdentity.from_site_spec(SPEC)


def page(label):
    return (f"<html><body><h1>Chapter {label}: Synthetic title</h1>"
            "<div id='article'><p>Synthetic diagnostic prose for identity testing.</p>"
            "</div></body></html>")


class Pages:
    def __init__(self, labels, count=None):
        self.labels = labels
        self.count = count or max(labels)
        self.calls = []
        self.cancel_event = threading.Event()

    def fetch(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url == SPEC.url:
            return f"<a href='{SPEC.url}/chapter-{self.count}'>x</a>"
        slug = int(re.search(r"chapter-(\d+)$", url)[1])
        value = self.labels[slug]
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, str) and value.startswith("<") else page(value)


def labels_of(metas):
    return {m.slug: ChapterLabel.from_meta(m).text for m in metas}


@pytest.mark.parametrize("length", [1, 2, 3, 8, 15])
@pytest.mark.parametrize("alignment", range(16))
def test_short_excursions_at_every_former_stride_alignment(length, alignment):
    labels = {s: s - 2 for s in range(3, 68)}
    begin = 20 + alignment
    labels.update({s: s + 3 for s in range(begin, begin + length)})
    rm = Pages(labels)
    result = fwn.FreeWebNovelAdapter(rm)._reconcile_window(rm, SPEC, 3, 67, {})
    assert labels_of(result) == {s: str(n) for s, n in labels.items()}


def test_fraction_between_equal_offsets_is_not_inferred_away():
    labels = {s: s - 2 for s in range(3, 36)}
    labels[10] = "627.10"
    rm = Pages(labels)
    result = fwn.FreeWebNovelAdapter(rm)._reconcile_window(rm, SPEC, 3, 35, {})
    assert labels_of(result)[10] == "627.10"


def test_populated_stale_seeds_are_not_page_observations():
    rm = Pages({s: s - 2 for s in range(1, 35)})
    seeds = [ChapterMeta(index=s + 900, url=fwn.chapter_url_for(SPEC, s), slug=s)
             for s in range(1, 35)]
    result = fwn.FreeWebNovelAdapter(rm).build_chapter_index(SPEC, resume_metas=seeds)
    assert labels_of(result)[10] == "8"
    assert len(rm.calls) == 35


def test_saved_probe_without_html_is_refetched():
    rm = Pages({10: 8})
    known = {10: (None, 999)}
    assert fwn.FreeWebNovelAdapter(rm)._fetch_and_read(rm, SPEC, 10, known) == 8
    assert len(rm.calls) == 1


def test_long_reverse_sequence_outside_old_padding_is_found():
    labels = {s: s - 2 for s in range(1, 91)}
    labels.update({s: 50 - (s - 60) for s in range(60, 82)})
    rm = Pages(labels)
    metas = fwn.FreeWebNovelAdapter(rm).build_chapter_index(SPEC, start_hint=50, end_hint=50)
    assert [m.slug for m in metas if m.index == 50] == [52, 60]


def test_preflight_refuses_old_saved_state_without_rewriting_it(tmp_path):
    store = ChapterManifestStore(tmp_path / "baseline", tmp_path / "runtime")
    identities = [SOURCE.chapter_identity(fwn.chapter_url_for(SPEC, s), ChapterLabel.parse(str(s)), slug=s)
                  for s in range(1, 5)]
    from webnovel_scraper.chapter_manifest import ChapterManifest, Provenance
    old = ChapterManifest.create(layer="runtime", source=SOURCE,
        provenance=Provenance(None, None, "0.3.0", "walk"), chapters=identities, spans=((1, 4),))
    path = store.runtime_path(SOURCE)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(old.to_json()))
    before = path.read_bytes()
    with pytest.raises(ChapterListRefused, match="revalidation"):
        resolve_chapters(SPEC, start=1, end=4, store=store,
                         discover=lambda **_: pytest.fail("old state bypassed refusal"),
                         log=lambda _: None)
    assert path.read_bytes() == before


def test_multiple_changes_equal_endpoints_and_return_after_long_excursion():
    labels = {s: s - 2 for s in range(3, 121)}
    labels.update({s: s + 6 for s in range(30, 100)})
    labels.update({31: 20, 32: 25, 33: 20, 34: "627.1", 35: "627.10"})
    rm = Pages(labels)
    result = fwn.FreeWebNovelAdapter(rm)._reconcile_window(rm, SPEC, 3, 120, {})
    assert labels_of(result) == {s: str(n) for s, n in labels.items()}
    assert result.discovery_complete


def test_preobserved_interior_constraint_is_not_overwritten_by_equal_anchors():
    rm = Pages({s: s - 2 for s in range(3, 36)})
    rm.labels[10] = "627.10"
    adapter, known = fwn.FreeWebNovelAdapter(rm), {}
    adapter._fetch_and_read(rm, SPEC, 10, known)
    # Later scan must preserve the independently observed interior page exactly.
    rm.labels[10] = 8
    result = adapter._reconcile_window(rm, SPEC, 3, 35, known)
    assert labels_of(result)[10] == "627.10"
    assert Counter(url for url, _ in rm.calls)[fwn.chapter_url_for(SPEC, 10)] == 1


@pytest.mark.parametrize("value,status", [
    ("<html><h1>No numbered heading</h1><div id='article'><p>Diagnostic prose.</p></div></html>", "unknown"),
    ("<html><h1>Chapter 8</h1></html>", "unreadable"),
    ("<html><h1>Error 1015</h1><p>You are being rate limited</p></html>", "inaccessible"),
    ("<html><title>Just a moment...</title></html>", "inaccessible"),
    ("<html><h1>Chapter 8</h1><div id='article'><p>Chapter 9: Conflicting heading</p></div></html>", "conflict"),
    (NotFoundFetchError("missing", status=404), "inaccessible"),
    (NotFoundFetchError("gone", status=410), "inaccessible"),
    (RateLimitedFetchError("429", status=429), "inaccessible"),
])
def test_unresolved_pages_are_not_extras_or_source_absence(value, status):
    rm = Pages({9: 7, 10: value, 11: 9})
    result = fwn.FreeWebNovelAdapter(rm)._reconcile_window(rm, SPEC, 9, 11, {})
    assert set(labels_of(result)) == {9, 11}
    assert result.evidence[fwn.chapter_url_for(SPEC, 10)].status == status
    assert not result.discovery_complete and not result.tail_confirmed
    assert all(e.status != "source_absent" for e in result.evidence.values())


@pytest.mark.parametrize("heading", ["-2 Joke Chapter. Do not read before chap 57", "Chapter -1 Volume 2 Glossary"])
def test_only_positively_recognized_extras_can_complete_a_scan(heading):
    rm = Pages({1: f"<html><h1>{heading}</h1></html>", 2: 1})
    result = fwn.FreeWebNovelAdapter(rm)._reconcile_window(rm, SPEC, 1, 2, {})
    assert result.evidence[fwn.chapter_url_for(SPEC, 1)].status == "extra"
    assert labels_of(result) == {2: "1"}
    assert result.discovery_complete


def test_identity_slice_does_not_allow_supreme_magus_fast_path():
    class EqualSlice(Pages):
        def fetch(self, url, **kwargs):
            if url == SPEC.url:
                self.calls.append((url, kwargs))
                return f"<a href='{SPEC.url}/chapter-20'>Chapter 20</a>"
            return super().fetch(url, **kwargs)
    rm = EqualSlice({s: s for s in range(1, 21)})
    rm.labels[7] = "6.10"
    result = fwn.FreeWebNovelAdapter(rm).build_chapter_index(SPEC)
    assert labels_of(result)[7] == "6.10"
    assert len(rm.calls) == 21


@pytest.mark.parametrize("old_number", [1950, 1953])
def test_historical_html_conflict_is_visible_and_not_resolved_by_generation(old_number):
    rm = Pages({1967: 1956})
    known = {1967: (page(old_number), old_number)}
    result = fwn.FreeWebNovelAdapter(rm)._reconcile_window(rm, SPEC, 1967, 1967, known)
    assert result == []
    fact = result.evidence[fwn.chapter_url_for(SPEC, 1967)]
    assert fact.status == "conflict"
    assert fact.labels == (str(old_number), "1956")
    assert not fact.verified


def test_checksum_valid_historical_cache_is_bypassed_without_read_or_write(tmp_path):
    from webnovel_scraper.html_cache import HtmlCache
    cache = HtmlCache(tmp_path / "cache", "supreme-magus", site_key="freewebnovel.com")
    url = fwn.chapter_url_for(SPEC, 1967)
    cache.store(url, page(1953), strategy="historical")
    assert cache.read(url)[0] == page(1953)
    before = {p: p.read_bytes() for p in (tmp_path / "cache").rglob("*") if p.is_file()}

    class Cached(Pages):
        def fetch(self, url, **kwargs):
            if kwargs.get("use_cache") is not False and cache.read(url):
                return cache.read(url)[0]
            return super().fetch(url, **kwargs)

    rm = Cached({1967: 1956})
    result = fwn.FreeWebNovelAdapter(rm)._reconcile_window(rm, SPEC, 1967, 1967, {})
    assert labels_of(result) == {1967: "1956"}
    assert all(p.read_bytes() == data for p, data in before.items())


@pytest.mark.parametrize("value", [8, "<h1>-2 Joke Chapter</h1>"])
def test_unexpected_cached_response_cannot_be_certified_current(value):
    rm = Pages({10: value})
    rm.last_fetch_info = type("Info", (), {"from_cache": True})()
    result = fwn.FreeWebNovelAdapter(rm)._reconcile_window(rm, SPEC, 10, 10, {})
    assert result == []
    assert result.evidence[fwn.chapter_url_for(SPEC, 10)].status == "saved_unknown"


@pytest.mark.parametrize("status", ["candidate", "saved_unknown", "conflict", "unknown", "inferred"])
def test_inferred_or_unknown_progress_cannot_create_verified_coverage(tmp_path, status):
    from webnovel_scraper.chapter_preflight import _Preflight
    store = ChapterManifestStore(tmp_path / "b", tmp_path / "r")
    preflight = _Preflight(SOURCE, start=1, end=4, store=store, discover=None,
                          log=lambda _: None, legacy_index=None, refresh=False, list_mode="auto",
                          cancel_event=None)
    meta = ChapterMeta(1, fwn.chapter_url_for(SPEC, 3), slug=3,
                       extra={"discovery_evidence": DiscoveryEvidence(status, "offset-arithmetic", ("1",))})
    assert preflight._identities([meta], report=True) == []
    preflight._checkpoint([meta], 3, 1, 4)
    assert not store.runtime_path(SOURCE).exists()
    # Even a non-SM preflight must not promote a tagged candidate.
    ordinary = SourceIdentity.from_site_spec(catalog.get_spec("shadow-slave", "freewebnovel"))
    preflight.source = ordinary
    meta.url = ordinary.index_url + "/chapter-3"
    preflight._checkpoint([meta], 3, 1, 4)
    assert not store.load_effective(ordinary).effective.spans


def test_inferred_tuple_followed_by_actual_page_does_not_override_observation():
    rm = Pages({10: "627.10"})
    known = {10: (None, 8)}
    result = fwn.FreeWebNovelAdapter(rm)._reconcile_window(rm, SPEC, 10, 10, known)
    assert labels_of(result) == {10: "627.10"}
    fact = result.evidence[fwn.chapter_url_for(SPEC, 10)]
    assert fact.verified and "unverified candidate" in fact.detail


def test_partial_cancellation_restart_and_spool_reuse(tmp_path):
    store = ChapterManifestStore(tmp_path / "b", tmp_path / "r")
    labels = {s: s for s in range(1, 66)}
    rm, partials = Pages(labels), []
    adapter = fwn.FreeWebNovelAdapter(rm)

    def stop(partial, through, total):
        partials.append((partial, through, total))
        if through >= 16:
            rm.cancel_event.set()

    with pytest.raises(ScrapeCancelled):
        adapter.build_chapter_index(SPEC, index_progress_cb=stop)
    partial, through, total = partials[-1]
    assert len(partials) == 1
    assert through == 16 and total == 65
    assert len(partial) == 16 and not partial.discovery_complete and not partial.tail_confirmed
    assert all(e.verified for e in partial.evidence.values())
    assert len(rm.calls) == 17
    assert not store.runtime_path(SOURCE).exists()
    rm.cancel_event.clear()
    restarted = adapter.build_chapter_index(SPEC, resume_metas=partial)
    assert len(restarted) == 65 and restarted.discovery_complete
    before = len(rm.calls)
    refs = [m.extra[fwn._PREFETCHED_HTML_KEY] for m in restarted]
    assert len({id(ref.spool) for ref in refs}) == 1
    for meta in restarted:
        assert adapter.fetch_chapter(meta, SPEC).index == meta.index
    assert len(rm.calls) == before  # content extraction makes zero duplicate requests
    assert all(len(m.extra) == 3 for m in restarted)
    assert all(not isinstance(m.extra[fwn._PREFETCHED_HTML_KEY], str) for m in restarted)


def test_spool_closes_when_last_page_reference_is_released():
    rm = Pages({1: 1, 2: 2})
    result = fwn.FreeWebNovelAdapter(rm)._reconcile_window(rm, SPEC, 1, 2, {})
    stream = result[0].extra[fwn._PREFETCHED_HTML_KEY].spool.file
    assert not stream.closed
    del result
    gc.collect()
    assert stream.closed


@pytest.mark.parametrize("index", [
    "<a href='https://other.example/novel/supreme-magus-novel/chapter-5'>Chapter 5</a>",
    "<a href='https://freewebnovel.com/novel/other-novel/chapter-5'>Chapter 5</a>",
    "<a>Chapter 5</a>",
])
def test_unscoped_index_bound_cannot_establish_supreme_magus_completeness(index):
    class Unscoped(Pages):
        def fetch(self, url, **kwargs):
            assert url == SPEC.url
            return index
    with pytest.raises(RuntimeError, match="source-scoped URL bound"):
        fwn.FreeWebNovelAdapter(Unscoped({1: 1})).build_chapter_index(SPEC)


def test_budget_exhaustion_publishes_only_observed_prefix():
    rm, partials, budget = Pages({s: s for s in range(1, 40)}), [], {"count": 0, "limit": 7}
    with pytest.raises(RuntimeError, match="safety ceiling"):
        fwn.FreeWebNovelAdapter(rm)._reconcile_window(
            rm, SPEC, 1, 39, {}, fetch_budget=budget,
            index_progress_cb=lambda part, through, total: partials.append((part, through)))
    assert budget["count"] == len(rm.calls) == 7
    assert partials[-1][1] == 7
    assert len(partials[-1][0]) == 7
    assert not partials[-1][0].discovery_complete


def test_failed_progress_callback_is_not_repeated_or_promoted_to_completion():
    rm, throughs = Pages({s: s for s in range(1, 40)}), []

    def fail(partial, through, total):
        throughs.append(through)
        assert len(partial) == through == 16
        assert not partial.discovery_complete
        raise RuntimeError("callback stopped discovery")

    with pytest.raises(RuntimeError, match="callback stopped"):
        fwn.FreeWebNovelAdapter(rm)._reconcile_window(rm, SPEC, 1, 39, {}, index_progress_cb=fail)
    assert throughs == [16]
    assert len(rm.calls) == 16


def test_fresh_preflight_persists_observations_and_reuses_same_run_spool(tmp_path):
    store = ChapterManifestStore(tmp_path / "b", tmp_path / "r")
    rm = Pages({1: "<h1>-2 Joke Chapter</h1>", 2: "<h1>Chapter -1 Volume 2 Glossary</h1>",
                3: 1, 4: 2, 5: "2.10", 6: 3})
    adapter = fwn.FreeWebNovelAdapter(rm)
    selection = resolve_chapters(SPEC, start=1, end=3, store=store,
                                 discover=lambda **kwargs: adapter.build_chapter_index(SPEC, **kwargs),
                                 log=lambda _: None)
    assert [ChapterLabel.from_meta(m).text for m in selection.chapters] == ["1", "2", "2.10", "3"]
    assert not selection.absent and not selection.toc_gaps
    assert store.load_effective(SOURCE).effective.covers(1, 3)
    assert not store.baseline_path(SOURCE).exists()
    before = len(rm.calls)
    for meta in selection.chapters:
        adapter.fetch_chapter(meta, SPEC)
    assert len(rm.calls) == before == 7


def test_catalog_bound_disagreement_cannot_hide_an_unscanned_reverse_suffix(tmp_path):
    from dataclasses import replace
    spec = replace(SPEC, chapter_count=2)
    rm = Pages({1: 1, 2: 2, 3: 1})
    adapter = fwn.FreeWebNovelAdapter(rm)
    store = ChapterManifestStore(tmp_path / "b", tmp_path / "r")
    with pytest.raises(ChapterListRefused, match="unproven"):
        resolve_chapters(spec, start=1, end=2, store=store,
                         discover=lambda **kwargs: adapter.build_chapter_index(spec, **kwargs), log=lambda _: None)
    assert len(store.load_effective(SOURCE).effective.chapters) == 2
    assert not store.load_effective(SOURCE).effective.spans


def test_preflight_cancellation_does_not_write_partial_coverage(tmp_path):
    rm = Pages({s: s for s in range(1, 66)})
    store = ChapterManifestStore(tmp_path / "b", tmp_path / "r")
    adapter = fwn.FreeWebNovelAdapter(rm)

    def discover(**kwargs):
        checkpoint = kwargs.pop("index_progress_cb")

        def stop(partial, through, total):
            checkpoint(partial, through, total)
            if through >= 16:
                rm.cancel_event.set()

        return adapter.build_chapter_index(SPEC, index_progress_cb=stop, **kwargs)

    with pytest.raises(ScrapeCancelled):
        resolve_chapters(SPEC, start=1, end=65, store=store, discover=discover,
                         log=lambda _: None, cancel_event=rm.cancel_event)
    assert len(rm.calls) == 17
    assert len(store.load_effective(SOURCE).effective.chapters) == 16
    assert not store.load_effective(SOURCE).effective.spans


@pytest.mark.parametrize("missing", ["unknown", "404", "gap"])
def test_preflight_fails_closed_for_unresolved_chapters(tmp_path, missing):
    store = ChapterManifestStore(tmp_path / "b", tmp_path / "r")
    value = {"unknown": "<p>Unnumbered document</p>",
             "404": NotFoundFetchError("missing", status=404), "gap": 4}[missing]
    rm = Pages({1: 1, 2: value, 3: 3})
    adapter = fwn.FreeWebNovelAdapter(rm)
    with pytest.raises(ChapterListRefused, match="unresolved|unproven"):
        resolve_chapters(SPEC, start=1, end=3, store=store,
                         discover=lambda **kwargs: adapter.build_chapter_index(SPEC, **kwargs), log=lambda _: None)
    saved = store.load_effective(SOURCE).effective
    assert not saved.covers(1, 3) and not saved.absent


@pytest.mark.parametrize("kind", ["baseline", "runtime", "legacy"])
@pytest.mark.parametrize("refresh", [False, True])
def test_old_state_refusal_precedes_refresh_import_and_quarantine(tmp_path, kind, refresh):
    store = ChapterManifestStore(tmp_path / "b", tmp_path / "r")
    legacy = tmp_path / "old-index.json"
    path = {"baseline": store.baseline_path(SOURCE), "runtime": store.runtime_path(SOURCE), "legacy": legacy}[kind]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"{invalid old state kept byte for byte}")
    with pytest.raises(ChapterListRefused, match="revalidation"):
        resolve_chapters(SPEC, start=1, end=3, store=store, refresh=refresh, legacy_index=legacy,
                         discover=lambda **_: pytest.fail("must refuse before discovery"), log=lambda _: None)
    assert path.read_bytes() == b"{invalid old state kept byte for byte}"
    assert list(tmp_path.rglob("*.bak*")) == []
    assert list(tmp_path.rglob("*.corrupt*")) == []


SNAPSHOTS = json.loads((Path(__file__).parents[1] / "test-files" / "supreme_magus_discovery_snapshots.json").read_text())
REGIONS = [(629, 635), (959, 962), (1470, 1475), (1651, 1655), (1932, 1938), (1964, 1967),
           (2565, 2566), (2596, 2596), (2760, 2760), (3419, 3419), (3678, 3679),
           (3988, 3988), (4273, 4273), (4413, 4415)]


@pytest.mark.parametrize("generation", ["legacy_L", "host_C_active", "host_C_rejected"])
@pytest.mark.parametrize("low,high", REGIONS)
def test_source_snapshot_generations_are_separate_structural_evidence(generation, low, high):
    snapshot = SNAPSHOTS[generation]
    labels = {s: snapshot.get(str(s), "<h1>Unknown snapshot; no source absence proof</h1>")
              for s in range(low, high + 1)}
    rm = Pages(labels)
    result = fwn.FreeWebNovelAdapter(rm)._reconcile_window(rm, SPEC, low, high, {})
    expected = {s: snapshot[str(s)] for s in range(low, high + 1) if str(s) in snapshot}
    assert labels_of(result) == expected
    for slug in labels.keys() - expected.keys():
        assert result.evidence[fwn.chapter_url_for(SPEC, slug)].status == "unknown"
    if generation.startswith("host_C") and low == 1651:
        assert "1642" not in labels_of(result).values()
        assert "1643" not in labels_of(result).values()
    assert all(e.status != "source_absent" for e in result.evidence.values())
