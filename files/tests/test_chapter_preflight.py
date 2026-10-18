"""Manifest-backed chapter preflight (v0.3.0 Checkpoint 03.2a). Offline and deterministic.

Every manifest and adapter here is synthetic and lives under ``tmp_path``; no network, no browser.
Both pipeline execution paths are exercised: the legacy synchronous path (an injected adapter) and
the FreeWebNovel rescue conductor (injected primary manager/adapter factories).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from webnovel_scraper import catalog, pipeline
from webnovel_scraper import chapter_preflight as pre
from webnovel_scraper import rescue_pool as rp
from webnovel_scraper.chapter_manifest import AbsentChapter, ChapterManifest, Provenance
from webnovel_scraper.chapter_preflight import ChapterListRefused
from webnovel_scraper.chapter_store import ChapterManifestStore
from webnovel_scraper.host_rate_limiter import HostRateLimiter
from webnovel_scraper.models import ChapterContent, ChapterMeta, OutputMode, ScrapeJob, SiteSpec
from webnovel_scraper.request_manager import ScrapeCancelled
from webnovel_scraper.source_identity import ChapterLabel, SourceIdentity

NOVEL, KEY = "shadow-slave", "freewebnovel"
SPEC = catalog.get_spec(NOVEL, KEY)
SOURCE = SourceIdentity.from_site_spec(SPEC)
NOW = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)


def url(n, host="freewebnovel.com") -> str:
    return f"https://{host}/novel/shadow-slave/chapter-{n}"


def cmeta(n, *, slug=None, title=None) -> ChapterMeta:
    return ChapterMeta(index=n, url=url(slug if slug is not None else n), title=title,
                       source_id=str(n), slug=slug if slug is not None else n)


def ident(label, slug, title=None):
    return SOURCE.chapter_identity(url(slug), ChapterLabel.parse(label), slug=slug, title=title)


def prov(method="toc"):
    return Provenance(generated_utc="2026-10-18T10:00:00Z", validated_utc="2026-10-18T11:00:00Z",
                      app_version="0.3.0", method=method)


@pytest.fixture
def store(tmp_path):
    return ChapterManifestStore(tmp_path / "baseline", tmp_path / "runtime", clock=lambda: NOW)


def write_baseline(store, numbers, *, absent=(), extra=(), spans=None, toc_complete=False):
    chapters = [ident(str(n), n) for n in numbers] + list(extra)
    manifest = ChapterManifest.create(
        layer="baseline", source=SOURCE, provenance=prov(), toc_complete=toc_complete,
        spans=spans if spans is not None else [(min(numbers), max(numbers))],
        chapters=chapters, absent=[AbsentChapter(ChapterLabel(str(n)), why) for n, why in absent],
    )
    path = store.baseline_path(SOURCE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest.to_json(), indent=2), encoding="utf-8")
    return path


def write_overlay(store, numbers, spans=None):
    chapters = [ident(str(n), n) for n in numbers]
    store.save_runtime(ChapterManifest.create(
        layer="runtime", source=SOURCE, provenance=prov(),
        spans=spans if spans is not None else [(min(numbers), max(numbers))], chapters=chapters,
    ))


class WindowAdapter:
    """A synthetic site with ``numbers`` chapters. A hinted call returns the hinted window plus two
    chapters of padding each side (as the real offset reconciliation does); an unhinted or
    open-ended call returns everything from the start hint up."""

    def __init__(self, numbers, *, pad=2, windowed=True, fail_with=None, labels=None):
        self.numbers = list(numbers)
        self.pad, self.windowed, self.fail_with = pad, windowed, fail_with
        self.labels = labels or {}
        self.calls: list[dict] = []
        self.fetched: list[str] = []
        self.warnings: list[str] = []

    def build_chapter_index(self, spec, *, start_hint=None, end_hint=None, resume_metas=None,
                            index_progress_cb=None, fast_path=False):
        self.calls.append(dict(start_hint=start_hint, end_hint=end_hint,
                               resume=[m.index for m in (resume_metas or [])]))
        if self.fail_with is not None:
            raise self.fail_with
        out = self.numbers
        if self.windowed and start_hint is not None:
            lo = start_hint - self.pad
            hi = float("inf") if end_hint is None else end_hint + self.pad
            out = [n for n in out if lo <= n <= hi]
        return [cmeta(n) for n in out]

    def fetch_chapter(self, meta, spec, **_kw):
        self.fetched.append(meta.url)
        return ChapterContent(index=meta.index, title=f"T{meta.index}",
                              paragraphs=[f"Body of chapter {meta.index}, long enough to render."])


def job(tmp_path, start, end, *, mode=OutputMode.SEPARATE, refresh=False, **kw) -> ScrapeJob:
    return ScrapeJob(novel_slug=NOVEL, adapter_key=KEY, start=start, end=end, delay=0.0,
                     output_mode=mode, use_cache=False, output_dir=tmp_path / "out",
                     refresh_chapter_list=refresh, **kw)


def run(tmp_path, store, adapter, start, end, **kw):
    logs: list[str] = []
    report = pipeline.run_scrape(job(tmp_path, start, end, **kw), adapter=adapter, log=logs.append,
                                 manifest_store=store)
    return report, logs


def effective(store):
    return store.load_effective(SOURCE).effective


def whole(effective_manifest):
    return [c.label.text for c in effective_manifest.chapters]


# ── pure helpers ──────────────────────────────────────────────────────────────
def test_clipped_spans_claim_only_the_asked_window_and_unbroken_runs():
    chapters = [ident(str(n), n) for n in (3, 4, 5, 6, 8, 9, 10)]
    assert pre.clipped_spans(chapters, 4, 9) == ((4, 6), (8, 9))     # padding at the edges claims nothing
    assert pre.clipped_spans(chapters, None, None) == ((3, 6), (8, 10))
    assert pre.clipped_spans(chapters, 11, 20) == ()
    assert pre.clipped_spans([], 1, 5) == ()


def test_fractional_labels_survive_the_meta_bridge_as_text_and_as_distinct_pages():
    a = SOURCE.chapter_identity(url(640), ChapterLabel("627.1"), slug=640)
    b = SOURCE.chapter_identity(url(650), ChapterLabel("627.10"), slug=650)
    metas = [pre.meta_from_identity(a), pre.meta_from_identity(b)]
    assert metas[0].index == metas[1].index == 627.1                  # the legacy float cannot tell them apart
    assert [m.extra[pre.LABEL_KEY] for m in metas] == ["627.1", "627.10"]
    assert [m.url for m in metas] == [url(640), url(650)]
    back = [pre.identity_from_meta(SOURCE, m) for m in metas]         # ... but the bridge round-trips the text
    assert [c.label.text for c in back] == ["627.1", "627.10"]
    assert back[0] != back[1]                                         # page URL is the identity


def test_a_label_in_extra_that_disagrees_with_the_index_is_ignored():
    meta = ChapterMeta(index=5, url=url(5), extra={pre.LABEL_KEY: "6"})
    assert pre.meta_label(meta).text == "5"


def test_off_host_and_malformed_discoveries_are_not_valid_evidence():
    for bad in (ChapterMeta(index=1, url="https://example/1"),
                ChapterMeta(index=1, url="http://freewebnovel.com/novel/shadow-slave/chapter-1"),
                ChapterMeta(index=-1, url=url(1)),
                ChapterMeta(index=1, url="https://freewebnovel.com/novel/../chapter-1")):
        with pytest.raises(Exception):
            pre.identity_from_meta(SOURCE, bad)


def test_titles_are_cleaned_of_control_characters_before_they_are_stored():
    meta = ChapterMeta(index=2, url=url(2), title="Cr\x00imson\n", source_id="2")
    assert pre.identity_from_meta(SOURCE, meta).title == "Cr imson"


# ── coverage: reuse without network, extend only what is missing ──────────────
def test_a_covered_finite_range_makes_no_toc_request_and_writes_nothing(tmp_path, store):
    baseline = write_baseline(store, range(1, 51))
    before = baseline.read_bytes()
    adapter = WindowAdapter(range(1, 51))

    report, logs = run(tmp_path, store, adapter, 10, 14)

    assert adapter.calls == []
    assert report.effective_range == (10, 14)
    assert len(report.written) == 5
    assert not store.runtime_path(SOURCE).exists()
    assert baseline.read_bytes() == before
    assert any(line.startswith("  loaded 5 chapters from the saved chapter list") for line in logs)


def test_only_the_uncovered_window_is_discovered_and_the_baseline_is_untouched(tmp_path, store):
    baseline = write_baseline(store, range(1, 51))
    before = baseline.read_bytes()
    adapter = WindowAdapter(range(1, 101))

    report, _ = run(tmp_path, store, adapter, 45, 60)

    assert [(c["start_hint"], c["end_hint"]) for c in adapter.calls] == [(51, 60)]
    assert adapter.calls[0]["resume"] == list(range(1, 51))     # everything known seeds the adapter
    assert report.effective_range == (45, 60) and len(report.written) == 16
    merged = effective(store)
    assert merged.covers(1, 60) and not merged.covers(1, 100)
    assert baseline.read_bytes() == before
    assert whole(effective(store))[:3] == ["1", "2", "3"]


def test_extending_keeps_every_earlier_overlay_chapter(tmp_path, store):
    write_overlay(store, range(1, 11))
    adapter = WindowAdapter(range(1, 101))

    run(tmp_path, store, adapter, 5, 20)
    assert [(c["start_hint"], c["end_hint"]) for c in adapter.calls] == [(11, 20)]
    persisted = store.load_layer(SOURCE, "runtime").manifest
    assert persisted is not None
    assert [c.label.text for c in persisted.chapters][:12] == [str(n) for n in range(1, 13)]
    assert persisted.covers(1, 20)
    # a later request inside what is now covered needs no traffic
    run(tmp_path, store, adapter, 1, 20)
    assert len(adapter.calls) == 1


def test_a_discovery_never_claims_the_padding_it_happened_to_return(tmp_path, store):
    adapter = WindowAdapter(range(1, 101))
    run(tmp_path, store, adapter, 30, 40)
    manifest = effective(store)
    assert manifest.covers(30, 40)
    assert not manifest.covers(28, 29) and not manifest.covers(41, 42)
    assert "42" in whole(manifest)        # still stored as evidence


def test_an_empty_store_discovers_the_whole_selected_range_automatically(tmp_path, store):
    adapter = WindowAdapter(range(1, 31))
    report, _ = run(tmp_path, store, adapter, 1, 30)
    assert [(c["start_hint"], c["end_hint"]) for c in adapter.calls] == [(1, 30)]
    assert len(report.written) == 30


# ── open-ended requests: bounded tail re-check ────────────────────────────────
def test_an_open_end_rechecks_the_tail_and_finds_new_chapters(tmp_path, store):
    write_overlay(store, range(1, 101))
    adapter = WindowAdapter(range(1, 104))
    report, _ = run(tmp_path, store, adapter, 98, 10**9)

    assert [(c["start_hint"], c["end_hint"]) for c in adapter.calls] == [(98, None)]
    assert report.effective_range == (98, 103)
    assert [Path(u).name for u in adapter.fetched][-3:] == ["chapter-101", "chapter-102", "chapter-103"]
    assert effective(store).covers(1, 103)


def test_the_tail_window_is_bounded_below_the_saved_last_chapter(tmp_path, store):
    write_overlay(store, range(1, 201))
    adapter = WindowAdapter(range(1, 201))
    run(tmp_path, store, adapter, 1, 10**9, mode=OutputMode.SINGLE)
    assert [(c["start_hint"], c["end_hint"]) for c in adapter.calls] == [(200 - pre.TAIL_RECHECK_MARGIN, None)]


def test_a_finite_range_is_never_given_a_tail_recheck(tmp_path, store):
    write_overlay(store, range(1, 101))
    adapter = WindowAdapter(range(1, 150))
    run(tmp_path, store, adapter, 90, 100)
    assert adapter.calls == []


def test_an_open_end_past_the_list_is_refused(tmp_path, store):
    write_overlay(store, range(1, 11))
    adapter = WindowAdapter(range(1, 11))
    with pytest.raises(ChapterListRefused, match="past the available chapters"):
        run(tmp_path, store, adapter, 50, 10**9)
    assert adapter.fetched == []


# ── false-success prevention ──────────────────────────────────────────────────
def test_a_finite_range_beyond_the_tail_refuses_and_fetches_nothing(tmp_path, store):
    adapter = WindowAdapter(range(1, 31))
    with pytest.raises(ChapterListRefused) as refused:
        run(tmp_path, store, adapter, 1, 50)
    message = str(refused.value)
    assert "ends at chapter 30" in message and "31-50" in message
    assert "Nothing was fetched or written" in message
    assert adapter.fetched == []
    assert list((tmp_path / "out").glob("*.pdf")) == []


def test_a_start_below_the_first_listed_chapter_is_refused(tmp_path, store):
    adapter = WindowAdapter(range(5, 31))
    with pytest.raises(ChapterListRefused, match="starts at chapter 5"):
        run(tmp_path, store, adapter, 1, 10)


def test_a_discovery_that_returns_nothing_for_the_window_is_not_a_confirmed_absence(tmp_path, store):
    write_overlay(store, range(1, 11))
    adapter = WindowAdapter([])                 # the site answered, but listed nothing
    with pytest.raises(ChapterListRefused, match="could not be confirmed|ends at chapter 10"):
        run(tmp_path, store, adapter, 5, 20)
    assert not effective(store).covers(5, 20)
    assert adapter.fetched == []


def test_a_failed_request_leaves_the_saved_evidence_and_covers_nothing_new(tmp_path, store):
    write_overlay(store, range(1, 11))
    before = store.runtime_path(SOURCE).read_bytes()
    adapter = WindowAdapter(range(1, 50), fail_with=RuntimeError("HTTP 503"))
    with pytest.raises(RuntimeError, match="503"):
        run(tmp_path, store, adapter, 5, 40)
    assert store.runtime_path(SOURCE).read_bytes() == before
    assert not effective(store).covers(5, 40)


def test_an_interrupted_discovery_claims_only_the_labels_it_resolved(tmp_path, store):
    class Interrupted(WindowAdapter):
        def build_chapter_index(self, spec, *, start_hint=None, end_hint=None, resume_metas=None,
                                index_progress_cb=None, **kw):
            partial = [cmeta(n) for n in (1, 2, 3, 4)]
            index_progress_cb(partial, 2, 9)         # only slugs <= 2 are resolved so far
            raise RuntimeError("synthetic crash")

    with pytest.raises(RuntimeError, match="synthetic crash"):
        run(tmp_path, store, Interrupted([]), 1, 9)
    partial = effective(store)
    assert whole(partial) == ["1", "2", "3", "4"]       # all four are evidence ...
    assert partial.spans == ((1, 2),)                   # ... but only 1-2 are claimed

    # Restart: the adapter is seeded with what was kept and the window is rediscovered.
    adapter = WindowAdapter(range(1, 10))
    report, _ = run(tmp_path, store, adapter, 1, 9)
    assert adapter.calls[0]["resume"] == [1, 2, 3, 4]
    assert adapter.calls[0]["start_hint"] == 3          # only the unclaimed part is asked for again
    assert report.effective_range == (1, 9) and effective(store).covers(1, 9)


def test_a_sparse_toc_gap_is_reported_but_never_recorded_as_absent(tmp_path, store):
    adapter = WindowAdapter([1, 2, 4, 5], windowed=False)
    report, logs = run(tmp_path, store, adapter, 1, 5)

    assert report.toc_gaps == [3]
    assert report.failed == [] and len(report.written) == 4
    assert any("chapter list skips chapter number(s)" in line for line in logs)
    manifest = effective(store)
    assert manifest.absent == ()                  # not inferred from a sparse list
    assert not manifest.covers(1, 5)              # and not claimed covered either
    assert manifest.spans == ((1, 2), (4, 5))


def test_a_huge_hole_in_the_returned_list_looks_like_a_failed_discovery_and_is_refused(tmp_path, store):
    adapter = WindowAdapter([1, 2, 3, 500, 501], windowed=False)
    with pytest.raises(ChapterListRefused, match="could not be confirmed"):
        run(tmp_path, store, adapter, 1, 501)
    assert adapter.fetched == []


def test_a_source_confirmed_absence_is_a_reasoned_skip_not_a_gap(tmp_path, store):
    write_baseline(store, [1, 2, 4, 5], absent=[(3, "page 404; neighbours present")],
                   spans=[(1, 5)])
    adapter = WindowAdapter(range(1, 6))
    report, logs = run(tmp_path, store, adapter, 1, 5)

    assert adapter.calls == []                                  # covered: no TOC request
    assert report.absent_skipped == [("3", "page 404; neighbours present")]
    assert report.toc_gaps == [] and report.failed == []
    assert len(report.written) == 4
    assert any("chapter 3 was skipped: confirmed absent at the source" in line for line in logs)
    assert "Skipped (confirmed absent at the source): 3" in report.summary()


# ── identity: duplicate labels, fractional labels, cross-host ─────────────────
def test_duplicate_displayed_numbers_stay_two_pages_through_the_manifest(tmp_path, store):
    dup = [SOURCE.chapter_identity(url(s), ChapterLabel("3664"), slug=s, title="Twisted Reflection")
           for s in (3678, 3679)]
    write_baseline(store, [3663, 3665], extra=dup, spans=[(3663, 3665)])
    adapter = WindowAdapter([])
    report, _ = run(tmp_path, store, adapter, 3664, 3664)

    assert adapter.calls == []
    assert len(report.written) == 2 and len(set(report.written)) == 2
    assert sorted(Path(u).name for u in adapter.fetched) == ["chapter-3678", "chapter-3679"]
    assert len(effective(store).duplicate_labels()[ChapterLabel("3664")]) == 2


def test_627_1_and_627_10_are_two_pages_neither_merged_nor_omitted(tmp_path, store):
    fractional = [SOURCE.chapter_identity(url(640), ChapterLabel("627.1"), slug=640),
                  SOURCE.chapter_identity(url(650), ChapterLabel("627.10"), slug=650)]
    write_baseline(store, [627, 628], extra=fractional, spans=[(627, 628)])
    adapter = WindowAdapter([])
    report, _ = run(tmp_path, store, adapter, 627, 628)

    assert adapter.calls == []
    assert sorted(Path(u).name for u in adapter.fetched) == [
        "chapter-627", "chapter-628", "chapter-640", "chapter-650"]
    assert len(report.written) == 4


def test_a_fractional_label_inside_the_range_but_past_the_end_number_is_not_selected(tmp_path, store):
    fractional = [SOURCE.chapter_identity(url(640), ChapterLabel("628.5"), slug=640)]
    write_baseline(store, [627, 628, 629], extra=fractional, spans=[(627, 629)])
    adapter = WindowAdapter([])
    run(tmp_path, store, adapter, 627, 628)
    assert sorted(Path(u).name for u in adapter.fetched) == ["chapter-627", "chapter-628"]


def test_the_two_hosts_never_share_a_chapter_list(tmp_path, store):
    com = SiteSpec(novel_slug="demo", novel_title="Demo", adapter_key="freewebnovel",
                   display_name="Free Web Novel (.com)", enabled=True,
                   url="https://freewebnovel.com/novel/demo", base_url="https://freewebnovel.com")
    net = SiteSpec(novel_slug="demo", novel_title="Demo", adapter_key="freewebnovel",
                   display_name="Free Web Novel (.net)", enabled=True,
                   url="https://freewebnovel.net/demo", base_url="https://freewebnovel.net")

    def discover_for(spec, calls):
        host = SourceIdentity.from_site_spec(spec).host

        def discover(**kw):
            calls.append(host)
            return [ChapterMeta(index=n, url=f"https://{host}/demo/chapter-{n}", slug=n)
                    for n in range(1, 6)]
        return discover

    calls: list[str] = []
    for spec in (com, net):
        selection = pre.resolve_chapters(spec, start=1, end=5, store=store,
                                         discover=discover_for(spec, calls), log=lambda _m: None)
        assert {urlparse_host(m.url) for m in selection.chapters} == {SourceIdentity.from_site_spec(spec).host}
    assert calls == ["freewebnovel.com", "freewebnovel.net"]          # .net did not reuse .com's list
    for spec in (com, net):                                            # second pass: both served from disk
        pre.resolve_chapters(spec, start=1, end=5, store=store, discover=lambda **_k: pytest.fail("network"),
                             log=lambda _m: None)
    paths = {store.runtime_path(SourceIdentity.from_site_spec(s)) for s in (com, net)}
    assert len(paths) == 2 and all(p.is_file() for p in paths)


def urlparse_host(value):
    from urllib.parse import urlsplit
    return urlsplit(value).netloc


def test_a_discovery_from_the_wrong_host_is_ignored_and_never_persisted(tmp_path, store):
    class WrongHost(WindowAdapter):
        def build_chapter_index(self, spec, **kw):
            return [ChapterMeta(index=n, url=f"https://freewebnovel.net/novel/shadow-slave/chapter-{n}")
                    for n in range(1, 6)]

    with pytest.raises(ChapterListRefused):
        run(tmp_path, store, WrongHost([]), 1, 5)
    assert not store.runtime_path(SOURCE).exists()


# ── corrupt / conflicting stores ──────────────────────────────────────────────
def test_a_corrupt_overlay_is_set_aside_with_a_warning_and_rediscovered(tmp_path, store):
    path = store.runtime_path(SOURCE)
    path.parent.mkdir(parents=True)
    path.write_text("{ definitely not a manifest", encoding="utf-8")
    adapter = WindowAdapter(range(1, 11))

    report, logs = run(tmp_path, store, adapter, 1, 10)

    assert len(adapter.calls) == 1 and len(report.written) == 10
    assert any(line.startswith("WARNING:") and "was rejected" in line for line in logs)
    assert list(path.parent.glob("*.corrupt-*"))
    assert effective(store).covers(1, 10)


def test_a_corrupt_baseline_is_reported_and_left_exactly_as_found(tmp_path, store):
    baseline = store.baseline_path(SOURCE)
    baseline.parent.mkdir(parents=True)
    baseline.write_text("not json", encoding="utf-8")
    adapter = WindowAdapter(range(1, 6))

    _, logs = run(tmp_path, store, adapter, 1, 5)

    assert baseline.read_text(encoding="utf-8") == "not json"
    assert not list(baseline.parent.glob("*.corrupt-*"))
    assert any("tracked baseline" in line and line.startswith("WARNING:") for line in logs)
    assert len(adapter.calls) == 1


def test_a_discovery_that_contradicts_the_baseline_is_refused_not_saved(tmp_path, store):
    baseline = write_baseline(store, range(1, 11))
    before = baseline.read_bytes()

    class Contradicting(WindowAdapter):
        def build_chapter_index(self, spec, **kw):
            # page .../chapter-3 now claims to be chapter 30: the baseline says 3
            return [ChapterMeta(index=30, url=url(3), slug=3)] + [cmeta(n) for n in range(11, 21)]

    with pytest.raises(ChapterListRefused):
        run(tmp_path, store, Contradicting([]), 1, 20)
    assert baseline.read_bytes() == before
    assert not store.runtime_path(SOURCE).exists()


# ── concurrency ───────────────────────────────────────────────────────────────
def test_a_second_writer_is_refused_visibly_and_changes_nothing(tmp_path, store):
    other = ChapterManifestStore(store._baseline_root, store._runtime_root, clock=lambda: NOW)
    adapter = WindowAdapter(range(1, 11))
    with other.writer_lock(SOURCE):
        with pytest.raises(ChapterListRefused, match="another run or program instance"):
            run(tmp_path, store, adapter, 1, 10)
    assert adapter.calls == [] and not store.runtime_path(SOURCE).exists()
    # the lock is free again, so the same run now proceeds
    report, _ = run(tmp_path, store, adapter, 1, 10)
    assert len(report.written) == 10


def test_a_reader_can_still_use_a_covered_list_while_another_writer_holds_the_lock(tmp_path, store):
    write_overlay(store, range(1, 11))
    other = ChapterManifestStore(store._baseline_root, store._runtime_root, clock=lambda: NOW)
    adapter = WindowAdapter(range(1, 11))
    with other.writer_lock(SOURCE):
        report, logs = run(tmp_path, store, adapter, 1, 10)
    assert adapter.calls == [] and len(report.written) == 10
    assert any("Reading the saved chapter list only" in line for line in logs)


def test_a_read_only_run_never_quarantines_an_overlay(tmp_path, store):
    path = store.runtime_path(SOURCE)
    path.parent.mkdir(parents=True)
    path.write_text("garbage", encoding="utf-8")
    other = ChapterManifestStore(store._baseline_root, store._runtime_root, clock=lambda: NOW)
    with other.writer_lock(SOURCE):
        with pytest.raises(ChapterListRefused):
            run(tmp_path, store, WindowAdapter(range(1, 4)), 1, 3)
    assert path.read_text(encoding="utf-8") == "garbage"
    assert not list(path.parent.glob("*.corrupt-*"))


# ── explicit refresh ──────────────────────────────────────────────────────────
def test_refresh_backs_up_the_overlay_keeps_the_baseline_and_rediscovers(tmp_path, store):
    baseline = write_baseline(store, range(1, 6))
    base_before = baseline.read_bytes()
    write_overlay(store, range(6, 11))
    overlay_before = store.runtime_path(SOURCE).read_bytes()
    adapter = WindowAdapter(range(1, 21))

    report, logs = run(tmp_path, store, adapter, 1, 10, refresh=True)

    backups = list(store.runtime_path(SOURCE).parent.glob("*.bak-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == overlay_before
    assert baseline.read_bytes() == base_before
    assert [(c["start_hint"], c["end_hint"]) for c in adapter.calls] == [(6, 10)]    # baseline still trusted
    assert adapter.calls[0]["resume"] == [1, 2, 3, 4, 5]                             # backup is not a seed
    assert len(report.written) == 10
    assert any(line.startswith("NOTE: chapter list refresh:") and backups[0].name in line for line in logs)


def test_refresh_without_a_saved_list_says_so_and_still_discovers(tmp_path, store):
    adapter = WindowAdapter(range(1, 6))
    _, logs = run(tmp_path, store, adapter, 1, 5, refresh=True)
    assert any("no saved list existed" in line for line in logs) and len(adapter.calls) == 1


def test_two_refreshes_in_one_second_get_unique_backup_names(tmp_path, store):
    names = set()
    for _ in range(3):
        write_overlay(store, range(1, 6))
        run(tmp_path / "x", store, WindowAdapter(range(1, 6)), 1, 5, refresh=True)
        names |= {p.name for p in store.runtime_path(SOURCE).parent.glob("*.bak-*")}
    assert len(names) == 3


def test_a_failed_refresh_puts_the_previous_list_back(tmp_path, store):
    write_overlay(store, range(1, 11))
    before = store.runtime_path(SOURCE).read_bytes()
    with pytest.raises(RuntimeError, match="503"):
        run(tmp_path, store, WindowAdapter(range(1, 11), fail_with=RuntimeError("HTTP 503")),
            1, 10, refresh=True)
    assert store.runtime_path(SOURCE).read_bytes() == before


def test_a_cancelled_refresh_puts_the_previous_list_back(tmp_path, store):
    write_overlay(store, range(1, 11))
    before = store.runtime_path(SOURCE).read_bytes()
    adapter = WindowAdapter(range(1, 11), fail_with=ScrapeCancelled("stop"))
    report, logs = run(tmp_path, store, adapter, 1, 10, refresh=True)
    assert report.cancelled
    assert store.runtime_path(SOURCE).read_bytes() == before
    assert any("previous saved list" in line and "was put back" in line for line in logs)


def test_refresh_does_not_reimport_a_stale_output_folder_index(tmp_path, store):
    out = tmp_path / "out"
    out.mkdir()
    rows = [{"index": n, "url": url(n), "title": None, "source_id": str(n), "slug": n} for n in range(1, 6)]
    legacy = out / pipeline.INDEX_FILENAME
    legacy.write_text(json.dumps({
        "schema_version": 2, "complete": True,
        "identity": {"novel_slug": NOVEL, "adapter_key": KEY, "source_url": SPEC.url,
                     "base_url": SPEC.base_url},
        "chapters": rows}), encoding="utf-8")
    before = legacy.read_bytes()
    adapter = WindowAdapter(range(1, 6))
    run(tmp_path, store, adapter, 1, 5, refresh=True)
    assert len(adapter.calls) == 1               # rebuilt from the site, not from the old file
    assert legacy.read_bytes() == before


def test_refresh_is_refused_when_another_writer_holds_the_list(tmp_path, store):
    write_overlay(store, range(1, 6))
    before = store.runtime_path(SOURCE).read_bytes()
    other = ChapterManifestStore(store._baseline_root, store._runtime_root, clock=lambda: NOW)
    with other.writer_lock(SOURCE):
        with pytest.raises(ChapterListRefused, match="cannot be refreshed"):
            run(tmp_path, store, WindowAdapter(range(1, 6)), 1, 5, refresh=True)
    assert store.runtime_path(SOURCE).read_bytes() == before
    assert not list(store.runtime_path(SOURCE).parent.glob("*.bak-*"))


# ── cancellation and empty discovery on the legacy path ───────────────────────
def test_cancel_before_discovery_ends_cleanly_with_nothing_saved(tmp_path, store):
    cancel = threading.Event()
    cancel.set()
    logs: list[str] = []
    adapter = WindowAdapter(range(1, 6))
    report = pipeline.run_scrape(job(tmp_path, 1, 5), adapter=adapter, log=logs.append,
                                 cancel_event=cancel, manifest_store=store)
    assert report.cancelled and adapter.calls == [] and not store.runtime_path(SOURCE).exists()


def test_an_empty_chapter_list_from_the_site_keeps_the_old_warning(tmp_path, store):
    report, _ = run(tmp_path, store, WindowAdapter([]), 1, 5)
    assert report.warnings == ["The chapter index came back empty; nothing to do."]
    assert report.written == [] and not store.runtime_path(SOURCE).exists()


# ── the FreeWebNovel rescue path uses the same preflight ──────────────────────
class _Clock:
    t = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += max(0.0, seconds)


class _PrimaryManager:
    def __init__(self, headless):
        self.headless, self.last_fetch_info, self.closed = headless, None, 0

    def close(self):
        self.closed += 1


class _PrimaryAdapter(WindowAdapter):
    def __init__(self, manager, shared):
        super().__init__(shared.numbers, windowed=shared.windowed, fail_with=shared.fail_with)
        self.manager, self.calls, self.fetched = manager, shared.calls, shared.fetched
        self.warnings = []

    def fetch_chapter(self, meta, spec, *, fast_path=False):
        return super().fetch_chapter(meta, spec)


def rescue_run(tmp_path, store, shared, start, end, *, refresh=False, cancel=None, headless=True):
    clock = _Clock()
    cancel = cancel or threading.Event()
    limiter = HostRateLimiter(pipeline.HOST_MIN_INTERVAL, monotonic=clock.now, sleep=clock.sleep)
    logs: list[str] = []
    the_job = job(tmp_path, start, end, refresh=refresh, use_browser=True, headless=headless)
    report = pipeline.run_scrape(
        the_job, log=logs.append, cancel_event=cancel, sleep_fn=clock.sleep, monotonic_fn=clock.now,
        host_limiter=limiter, manifest_store=store,
        request_manager_factory=lambda *, headless: _PrimaryManager(headless),
        primary_adapter_factory=lambda manager: _PrimaryAdapter(manager, shared),
        rescue_pool_factory=lambda: rp.RescuePool(
            primary_headless=headless, host_limiter=limiter, cancel_event=cancel,
            monotonic=clock.now, sleep=clock.sleep,
            fetch_fn=lambda meta, **_k: ChapterContent(meta.index, "T", ["rescued body paragraph"]),
            manager_factory=lambda *, headless: _PrimaryManager(headless),
            adapter_factory=lambda m: object(), log=logs.append),
    )
    return report, logs


def test_rescue_path_reuses_a_covered_list_without_a_toc_request(tmp_path, store):
    write_overlay(store, range(1, 21))
    shared = WindowAdapter(range(1, 21))
    report, _ = rescue_run(tmp_path, store, shared, 5, 9)
    assert shared.calls == [] and report.effective_range == (5, 9) and len(report.written) == 5


def test_rescue_path_discovers_only_the_missing_window_and_persists_it(tmp_path, store):
    write_overlay(store, range(1, 11))
    shared = WindowAdapter(range(1, 41))
    report, _ = rescue_run(tmp_path, store, shared, 8, 25)
    assert [(c["start_hint"], c["end_hint"]) for c in shared.calls] == [(11, 25)]
    assert len(report.written) == 18 and effective(store).covers(1, 25)


def test_rescue_path_refuses_instead_of_clamping(tmp_path, store):
    shared = WindowAdapter(range(1, 11))
    with pytest.raises(ChapterListRefused, match="ends at chapter 10"):
        rescue_run(tmp_path, store, shared, 1, 99)
    assert shared.fetched == [] and list((tmp_path / "out").glob("*.pdf")) == []


def test_rescue_path_honours_refresh_and_open_ended_tail_recheck(tmp_path, store):
    write_overlay(store, range(1, 11))
    shared = WindowAdapter(range(1, 13))
    report, logs = rescue_run(tmp_path, store, shared, 9, 10**9, refresh=True)
    assert any("chapter list refresh" in line for line in logs)
    assert list(store.runtime_path(SOURCE).parent.glob("*.bak-*"))
    assert report.effective_range == (9, 12) and len(report.written) == 4


def test_rescue_path_cancel_during_discovery_returns_a_cancelled_report(tmp_path, store):
    shared = WindowAdapter(range(1, 6), fail_with=ScrapeCancelled("stop"))
    report, _ = rescue_run(tmp_path, store, shared, 1, 5)
    assert report.cancelled and report.written == []


@pytest.mark.parametrize("start,end", [(1, 10), (4, 8), (7, 10**9)])
def test_both_paths_select_identically_and_persist_the_same_list(tmp_path, start, end):
    results = []
    for name, runner in (("legacy", None), ("rescue", rescue_run)):
        folder = tmp_path / name
        folder.mkdir()
        local = ChapterManifestStore(folder / "b", folder / "r", clock=lambda: NOW)
        shared = WindowAdapter(range(1, 11))
        if runner is None:
            report, _ = run(folder, local, shared, start, end)
        else:
            report, _ = runner(folder, local, shared, start, end)
        results.append((report.effective_range, len(report.written), sorted(Path(u).name for u in shared.fetched),
                        whole(local.load_effective(SOURCE).effective),
                        local.load_effective(SOURCE).effective.spans))
    assert results[0] == results[1]


# ── the real FreeWebNovel adapter through the real preflight ──────────────────
def _offset_novel(label_fn=None):
    """A 500-slug synthetic offset novel served by the repo's existing slug-synthesizing fake."""
    from .test_chapter_number_offset import SM_SPEC, SlugSynthesizingRM, _synth_displayed_label
    from webnovel_scraper.adapters.freewebnovel import FreeWebNovelAdapter

    index_url = SM_SPEC.url
    index_html = "<html><body>" + "".join(
        f"<a href='{index_url}/chapter-{n}'>x</a>" for n in (498, 499, 500)) + "</body></html>"
    rm = SlugSynthesizingRM(index_html, index_url, label_fn=label_fn or _synth_displayed_label)
    return SM_SPEC, rm, FreeWebNovelAdapter(request_manager=rm)


def _sm_run(tmp_path, store, adapter, start, end, folder="out", **kw):
    the_job = ScrapeJob(novel_slug="supreme-magus", adapter_key="freewebnovel", start=start, end=end,
                        delay=0.0, output_mode=OutputMode.SEPARATE, use_cache=False,
                        output_dir=tmp_path / folder, **kw)
    logs: list[str] = []
    report = pipeline.run_scrape(the_job, adapter=adapter, log=logs.append, manifest_store=store)
    return report, logs


def test_real_adapter_offset_discovery_is_persisted_and_then_served_without_the_toc(tmp_path, store):
    spec, rm, adapter = _offset_novel()
    first, _ = _sm_run(tmp_path, store, adapter, 114, 118)
    assert first.effective_range == (114, 118) and first.toc_gaps == []
    assert len(first.written) == 6                           # 114-118 plus the 116.5 insert
    sm_source = SourceIdentity.from_site_spec(spec)
    saved = store.load_effective(sm_source).effective
    assert saved.covers(114, 118)
    by_label = {c.label.text: c.slug for c in saved.chapters}
    assert by_label["116"] == 119 and by_label["116.5"] == 120

    walked = len(rm.fetched)
    toc_before = rm.fetched.count(spec.url)
    second, _ = _sm_run(tmp_path, store, adapter, 115, 117, folder="second")
    assert rm.fetched.count(spec.url) == toc_before           # no index page, no probes, no walk
    assert [u for u in rm.fetched[walked:] if "/chapter-" not in u] == []
    assert second.effective_range == (115, 117)


def test_real_adapter_extension_reuses_what_is_known_and_only_walks_the_new_window(tmp_path, store):
    spec, rm, adapter = _offset_novel()
    _sm_run(tmp_path, store, adapter, 100, 104)
    sm_source = SourceIdentity.from_site_spec(spec)
    first_pages = set(c.page_url for c in store.load_effective(sm_source).effective.chapters)

    before = len(rm.fetched)
    _sm_run(tmp_path, store, adapter, 100, 140, folder="wider")
    after = store.load_effective(sm_source).effective
    assert after.covers(100, 140)
    assert first_pages <= {c.page_url for c in after.chapters}   # nothing earlier was lost
    assert len(rm.fetched) - before < 90                         # bounded, not a full-novel walk


def test_real_adapter_duplicate_displayed_number_stays_two_pages_end_to_end(tmp_path, store):
    def label(slug):
        if slug <= 3:
            return None
        if slug < 50:
            return str(slug - 3)
        if slug in (50, 51):
            return "47"                      # one displayed number, two source pages
        return str(slug - 4)

    spec, rm, adapter = _offset_novel(label)
    report, logs = _sm_run(tmp_path, store, adapter, 45, 50)
    sm_source = SourceIdentity.from_site_spec(spec)
    saved = store.load_effective(sm_source).effective
    assert [c.slug for c in saved.duplicate_labels()[ChapterLabel("47")]] == [50, 51]
    names = sorted(p.name for p in report.written)
    assert len(names) == len(set(names)) == 7              # 45,46,47,47,48,49,50: nothing merged
    assert any("[slug 51]" in n for n in names)
    assert any("displayed chapter 47 occurs 2 times" in line for line in logs)


def test_real_adapter_open_ended_run_reaches_the_tail_of_the_offset_novel(tmp_path, store):
    spec, rm, adapter = _offset_novel()
    report, _ = _sm_run(tmp_path, store, adapter, 490, 10**9)
    assert report.effective_range == (490, 496) and report.toc_gaps == []
    assert len(report.written) == 7
