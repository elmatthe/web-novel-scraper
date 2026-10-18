"""Validated HTML cache (v0.3.0 Checkpoint 03.2b, plan §9.6). Offline and deterministic.

Covers the cache module itself (provenance, isolation, corruption, truncation, atomic publication,
legacy copy, invalidation), the request manager's use of it (fresh index pages, explicit refresh),
the adapters' validate-then-keep rule, and both pipeline paths end to end: a second run of a known
range touches the network zero times, and a stale index can never hide newly published chapters.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

import pytest

from webnovel_scraper import catalog, html_cache, pipeline
from webnovel_scraper import rescue_pool as rp
from webnovel_scraper.adapters.base import extract_validated
from webnovel_scraper.adapters.freewebnovel import FreeWebNovelAdapter
from webnovel_scraper.chapter_store import ChapterManifestStore
from webnovel_scraper.host_rate_limiter import HostRateLimiter
from webnovel_scraper.html_cache import HtmlCache
from webnovel_scraper.models import (
    ChapterContent,
    ChapterMeta,
    ChapterMismatchError,
    EmptyExtractionError,
    OutputMode,
    ScrapeJob,
)
from webnovel_scraper.request_manager import RequestManager, ScrapeCancelled

COM = "https://freewebnovel.com/novel/shadow-slave/chapter-1"
NET = "https://freewebnovel.net/shadow-slave/chapter-1-crimson.html"
PAGE = "<html><head><title>Chapter 1</title></head><body><p>" + "Real prose. " * 30 + "</p></body></html>"
CHALLENGE = "<html><head><title>Just a moment...</title></head><body>cf-browser-verification</body></html>"
SPEC = catalog.get_spec("shadow-slave", "freewebnovel")


def cache(tmp_path, slug="shadow-slave", **kw) -> HtmlCache:
    logs: list[str] = []
    store = HtmlCache(tmp_path / "cache", slug, log=logs.append, clock=lambda: "2026-10-18T12:00:00Z", **kw)
    store.logs = logs
    return store


# ── provenance + round trip ───────────────────────────────────────────────────
def test_a_stored_page_reads_back_with_a_complete_provenance_record(tmp_path):
    c = cache(tmp_path)
    assert c.store(COM, PAGE, strategy="camoufox")
    assert c.read(COM) == (PAGE, "network")
    sidecar = json.loads(c.sidecar_path(COM).read_text(encoding="utf-8"))
    data = PAGE.encode("utf-8")
    assert sidecar == {
        "schema_version": 1, "kind": "html-cache-entry", "url": COM, "final_url": COM,
        "host": "freewebnovel.com", "novel_slug": "shadow-slave", "fetched_utc": "2026-10-18T12:00:00Z",
        "byte_len": len(data), "sha256": hashlib.sha256(data).hexdigest(), "classification": "valid",
        "strategy": "camoufox", "origin": "network", "app_version": "0.3.0",
    }
    assert c.html_path(COM) == tmp_path / "cache" / "freewebnovel.com" / "shadow-slave" / (
        html_cache.cache_key_for(COM) + ".html")


def test_unicode_pages_round_trip_byte_for_byte(tmp_path):
    c = cache(tmp_path)
    page = PAGE.replace("Real prose.", "Ünïcödé — “quoted” 中文 prose.")
    assert c.store(COM, page, strategy="http")
    assert c.read(COM)[0] == page


# ── isolation: host, novel, source ────────────────────────────────────────────
def test_net_and_com_pages_live_in_separate_folders(tmp_path):
    c = cache(tmp_path)
    assert c.store(COM, PAGE, strategy="http") and c.store(NET, PAGE, strategy="http")
    assert c.html_path(COM).parent.parent.name == "freewebnovel.com"
    assert c.html_path(NET).parent.parent.name == "freewebnovel.net"


def test_a_source_scoped_cache_never_holds_another_hosts_pages(tmp_path):
    com_only = cache(tmp_path, site_key="freewebnovel.com")
    assert not com_only.store(NET, PAGE, strategy="http")
    assert com_only.read(NET) is None
    assert not (tmp_path / "cache" / "freewebnovel.net").exists()


def test_two_novels_never_share_an_entry_even_for_the_same_url(tmp_path):
    a, b = cache(tmp_path, "shadow-slave"), cache(tmp_path, "supreme-magus")
    assert a.store(COM, PAGE, strategy="http")
    assert b.read(COM) is None
    # a sidecar copied into another novel's folder is recognised and refused
    b.html_path(COM).parent.mkdir(parents=True)
    b.html_path(COM).write_bytes(a.html_path(COM).read_bytes())
    b.sidecar_path(COM).write_bytes(a.sidecar_path(COM).read_bytes())
    assert b.read(COM) is None
    assert any("another novel" in line for line in b.logs)


def test_a_sidecar_for_another_page_is_refused(tmp_path):
    c = cache(tmp_path)
    other = COM.replace("chapter-1", "chapter-2")
    assert c.store(other, PAGE, strategy="http")
    c.html_path(COM).parent.mkdir(parents=True, exist_ok=True)
    c.html_path(COM).write_bytes(c.html_path(other).read_bytes())
    c.sidecar_path(COM).write_bytes(c.sidecar_path(other).read_bytes())
    assert c.read(COM) is None
    assert any("another page" in line for line in c.logs)


# ── what is never stored ──────────────────────────────────────────────────────
@pytest.mark.parametrize("bad,why", [
    (CHALLENGE, "Cloudflare challenge"),
    ("<html><body><p>" + "x" * 500, "truncated"),
    ("", "empty"),
    ("�" * 400, "undecodable"),
])
def test_unusable_pages_are_never_stored(tmp_path, bad, why):
    c = cache(tmp_path)
    assert not c.store(COM, bad, strategy="http")
    assert not c.html_path(COM).exists()
    assert any(why in line for line in c.logs)


def test_a_redirect_to_another_page_or_host_is_not_cached_under_the_requested_url(tmp_path):
    c = cache(tmp_path)
    assert not c.store(COM, PAGE, strategy="http", final_url="https://freewebnovel.com/")
    assert not c.store(COM, PAGE, strategy="http", final_url="https://evil.example/novel/shadow-slave/chapter-1")
    assert not c.html_path(COM).exists()
    assert c.store(COM, PAGE, strategy="http", final_url=COM + "/")    # a trailing slash is the same page


# ── what is never served ──────────────────────────────────────────────────────
def test_an_html_file_without_its_provenance_record_is_an_interrupted_save(tmp_path):
    c = cache(tmp_path)
    c.html_path(COM).parent.mkdir(parents=True)
    c.html_path(COM).write_text(PAGE, encoding="utf-8")
    assert c.read(COM) is None
    assert any("interrupted save" in line for line in c.logs)


@pytest.mark.parametrize("damage", ["truncate", "flip", "append"])
def test_a_truncated_or_corrupted_entry_is_not_served(tmp_path, damage):
    c = cache(tmp_path)
    assert c.store(COM, PAGE, strategy="http")
    path = c.html_path(COM)
    data = path.read_bytes()
    path.write_bytes({"truncate": data[:-40], "flip": data[:10] + b"X" + data[11:],
                      "append": data + b"<p>injected</p>"}[damage])
    assert c.read(COM) is None


def test_a_challenge_with_valid_provenance_is_still_never_served(tmp_path):
    c = cache(tmp_path)
    assert c.store(COM, PAGE, strategy="http")
    data = CHALLENGE.encode()
    c.html_path(COM).write_bytes(data)
    record = json.loads(c.sidecar_path(COM).read_text(encoding="utf-8"))
    record.update(byte_len=len(data), sha256=hashlib.sha256(data).hexdigest())
    c.sidecar_path(COM).write_text(json.dumps(record), encoding="utf-8")
    assert c.read(COM) is None


@pytest.mark.parametrize("edit", [
    {"schema_version": 2}, {"kind": "other"}, {"classification": "challenge"}, {"origin": "guess"},
    {"byte_len": "12"}, {"sha256": "not-a-hash"}, {"extra_key": 1},
])
def test_a_malformed_provenance_record_is_refused(tmp_path, edit):
    c = cache(tmp_path)
    assert c.store(COM, PAGE, strategy="http")
    record = json.loads(c.sidecar_path(COM).read_text(encoding="utf-8"))
    record.update(edit)
    c.sidecar_path(COM).write_text(json.dumps(record), encoding="utf-8")
    assert c.read(COM) is None


def test_a_crash_between_the_html_and_its_sidecar_is_never_served(tmp_path, monkeypatch):
    c = cache(tmp_path)
    assert c.store(COM, PAGE, strategy="http")               # an older valid pair exists
    real_replace = os.replace

    def crash_on_sidecar(src, dst):
        if str(dst).endswith(".json"):
            raise OSError("power cut")
        return real_replace(src, dst)

    monkeypatch.setattr(html_cache.os, "replace", crash_on_sidecar)
    newer = PAGE.replace("Real prose.", "Newer prose.")
    assert not c.store(COM, newer, strategy="http")
    monkeypatch.setattr(html_cache.os, "replace", real_replace)
    assert c.read(COM) is None                               # new html + old sidecar: refused
    assert not list(c.html_path(COM).parent.glob("*.tmp"))  # no temp files left behind


# ── invalidation ──────────────────────────────────────────────────────────────
def test_invalidate_sets_the_page_aside_and_a_newer_copy_supersedes_it(tmp_path):
    c = cache(tmp_path)
    assert c.store(COM, PAGE, strategy="http")
    assert c.invalidate(COM, "no chapter body")
    assert c.read(COM) is None and c.invalid_path(COM).read_text(encoding="utf-8") == PAGE
    assert not c.sidecar_path(COM).exists()
    assert c.store(COM, PAGE, strategy="http")
    assert c.read(COM) == (PAGE, "network") and not c.invalid_path(COM).exists()


# ── legacy migration: copy, never move ────────────────────────────────────────
def _legacy(tmp_path, url, text):
    path = tmp_path / "cache" / "shadow-slave" / html_cache.cache_key_for(url)
    path = path.with_suffix(".html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_legacy_page_is_copied_into_the_new_layout_and_left_untouched(tmp_path):
    legacy = _legacy(tmp_path, COM, PAGE)
    before = legacy.read_bytes()
    c = cache(tmp_path, legacy_root=tmp_path / "cache")
    assert c.read(COM) == (PAGE, "legacy-copy")
    assert legacy.read_bytes() == before                      # never moved or changed
    record = json.loads(c.sidecar_path(COM).read_text(encoding="utf-8"))
    assert record["origin"] == "legacy-copy" and record["strategy"] == "legacy"
    assert c.read(COM) == (PAGE, "legacy-copy")               # now served from the new layout


def test_a_legacy_challenge_or_garbage_is_never_copied(tmp_path):
    _legacy(tmp_path, COM, CHALLENGE)
    c = cache(tmp_path, legacy_root=tmp_path / "cache")
    assert c.read(COM) is None and not c.html_path(COM).exists()


def test_an_invalidated_legacy_page_is_not_imported_again(tmp_path):
    legacy = _legacy(tmp_path, COM, PAGE)
    c = cache(tmp_path, legacy_root=tmp_path / "cache")
    assert c.read(COM) is not None
    assert c.invalidate(COM, "wrong chapter")
    assert c.read(COM) is None                                # not re-copied from the legacy file
    assert legacy.read_text(encoding="utf-8") == PAGE         # which is still exactly as it was


# ── the request manager ───────────────────────────────────────────────────────
class _Network:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def __call__(self, url, strategy):
        self.calls.append(url)
        page = self.pages[url]
        return page() if callable(page) else page


def manager(tmp_path, pages, **kw) -> tuple[RequestManager, _Network]:
    rm = RequestManager("shadow-slave", cache_root=tmp_path / "cache", sleep_fn=lambda _s: None,
                        retry_jitter_ratio=0.0, max_retries=0, **kw)
    net = _Network(pages)
    rm._fetch_uncached_strategy = net
    return rm, net


def test_a_valid_hit_makes_no_network_call(tmp_path):
    rm, net = manager(tmp_path, {COM: PAGE})
    assert rm.fetch(COM) == PAGE and rm.fetch(COM) == PAGE
    assert net.calls == [COM]
    assert rm.last_fetch_info.from_cache is True


def test_use_cache_false_neither_reads_nor_writes(tmp_path):
    rm, net = manager(tmp_path, {COM: PAGE})
    rm.fetch(COM, use_cache=False)
    rm.fetch(COM, use_cache=False)
    assert net.calls == [COM, COM] and not rm.cache_path_for(COM).exists()


def test_explicit_refresh_refetches_and_replaces_the_cached_page(tmp_path):
    rm, _ = manager(tmp_path, {COM: PAGE})
    rm.fetch(COM)
    newer = PAGE.replace("Real prose.", "Revised prose.")
    fresh, net = manager(tmp_path, {COM: newer}, refresh_cache=True)
    assert fresh.fetch(COM) == newer and net.calls == [COM]
    plain, net2 = manager(tmp_path, {COM: PAGE})
    assert plain.fetch(COM) == newer and net2.calls == []      # the refreshed copy is what is kept


def test_a_manager_scoped_to_one_source_never_caches_another_host(tmp_path):
    rm, net = manager(tmp_path, {NET: PAGE}, site_key="freewebnovel.com")
    rm.fetch(NET)
    rm.fetch(NET)
    assert net.calls == [NET, NET] and not (tmp_path / "cache" / "freewebnovel.net").exists()


# ── validate-then-keep in the adapters ────────────────────────────────────────
def _meta(n=1):
    return ChapterMeta(index=n, url=COM.replace("chapter-1", f"chapter-{n}"), slug=n)


def test_a_cached_page_without_a_body_is_set_aside_and_fetched_once_more(tmp_path):
    rm, net = manager(tmp_path, {COM: PAGE})
    rm.fetch(COM)                                              # cached
    attempts = []

    def extract(html, meta):
        attempts.append(rm.last_fetch_info.from_cache)
        if len(attempts) == 1:
            raise EmptyExtractionError("no body")
        return ChapterContent(1, "T", ["body"])

    content = extract_validated(rm, _meta(), lambda: rm.fetch(COM), extract, lambda _m: None)
    assert content.paragraphs == ["body"]
    assert attempts == [True, False]                           # cache, then the site
    assert net.calls == [COM, COM]
    assert rm.cache.invalid_path(COM).exists() is False        # the good copy superseded it
    assert rm.fetch(COM) and rm.last_fetch_info.from_cache     # and is what is cached now


def test_a_page_that_fails_twice_fails_the_chapter_without_a_loop(tmp_path):
    rm, net = manager(tmp_path, {COM: PAGE})
    rm.fetch(COM)

    def extract(_html, _meta):
        raise ChapterMismatchError("shows chapter 9")

    with pytest.raises(ChapterMismatchError):
        extract_validated(rm, _meta(), lambda: rm.fetch(COM), extract, lambda _m: None)
    assert net.calls == [COM, COM]                             # exactly one extra request
    assert rm._read_cache(COM) is None                         # nothing unusable left to serve


def test_a_network_page_that_fails_extraction_is_not_kept(tmp_path):
    rm, net = manager(tmp_path, {COM: PAGE})

    def extract(_html, _meta):
        raise EmptyExtractionError("no body")

    with pytest.raises(EmptyExtractionError):
        extract_validated(rm, _meta(), lambda: rm.fetch(COM), extract, lambda _m: None)
    assert net.calls == [COM] and rm._read_cache(COM) is None


def test_cancellation_is_never_swallowed_by_the_cache_retry(tmp_path):
    rm, _ = manager(tmp_path, {COM: PAGE})

    def fetch():
        raise ScrapeCancelled("stop")

    with pytest.raises(ScrapeCancelled):
        extract_validated(rm, _meta(), fetch, lambda h, m: None, lambda _m: None)


# ── a synthetic FreeWebNovel site through the real adapter and both pipeline paths ──
def chapter_page(n, *, shows=None):
    shown = n if shows is None else shows
    body = "".join(f"<p>Paragraph {i} of chapter {shown}, written as ordinary story prose.</p>"
                   for i in range(4))
    return (f"<html><head><title>Chapter {shown}: Title {shown}</title></head><body>"
            f"<span class='chapter'>Chapter {shown}: Title {shown}</span>"
            f"<div id='article'>{body}</div></body></html>")


def index_page(count):
    links = "".join(f"<a href='{SPEC.url}/chapter-{n}'>Chapter {n}: Title {n}</a>"
                    for n in range(max(1, count - 5), count + 1))
    return f"<html><body>{links}</body></html>"


class Site:
    """Index + chapter pages; ``count`` can grow between runs (newly published chapters)."""

    def __init__(self, count):
        self.count, self.calls = count, []

    def __call__(self, url, strategy):
        self.calls.append(url)
        if url == SPEC.url:
            return index_page(self.count)
        n = int(url.rsplit("-", 1)[1])
        if n > self.count:
            raise RuntimeError("HTTP 404")
        return chapter_page(n)


def _job(tmp_path, folder, start, end, **kw):
    return ScrapeJob(novel_slug="shadow-slave", adapter_key="freewebnovel", start=start, end=end,
                     delay=0.0, output_mode=OutputMode.SEPARATE, use_cache=True,
                     output_dir=tmp_path / folder, **kw)


def _real_manager(tmp_path, site, **kw):
    rm = RequestManager("shadow-slave", cache_root=tmp_path / "cache", sleep_fn=lambda _s: None,
                        retry_jitter_ratio=0.0, max_retries=0, site_key="freewebnovel.com", **kw)
    rm._fetch_uncached_strategy = site
    return rm


def legacy_run(tmp_path, store, site, folder, start, end, **kw):
    rm = _real_manager(tmp_path, site)
    return pipeline.run_scrape(_job(tmp_path, folder, start, end, **kw),
                               adapter=FreeWebNovelAdapter(request_manager=rm, log=lambda _m: None),
                               request_manager=rm, log=lambda _m: None, manifest_store=store)


def rescue_run(tmp_path, store, site, folder, start, end, **kw):
    clock = [0.0]
    cancel = threading.Event()
    limiter = HostRateLimiter(0.0, monotonic=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s))
    return pipeline.run_scrape(
        _job(tmp_path, folder, start, end, use_browser=True, headless=False, **kw),
        log=lambda _m: None, cancel_event=cancel, host_limiter=limiter, manifest_store=store,
        sleep_fn=lambda _s: None, monotonic_fn=lambda: clock[0],
        request_manager_factory=lambda *, headless: _real_manager(tmp_path, site),
        primary_adapter_factory=lambda m: FreeWebNovelAdapter(request_manager=m, log=lambda _m: None),
        rescue_pool_factory=lambda: rp.RescuePool(
            primary_headless=False, host_limiter=limiter, cancel_event=cancel,
            monotonic=lambda: clock[0], sleep=lambda _s: None,
            fetch_fn=lambda meta, **_k: pytest.fail("no chapter should need rescue")),
    )


@pytest.fixture(params=["legacy", "rescue"])
def runner(request):
    return legacy_run if request.param == "legacy" else rescue_run


def test_a_second_run_of_a_known_range_makes_no_network_request(tmp_path, runner):
    store = ChapterManifestStore(tmp_path / "b", tmp_path / "r")
    site = Site(12)
    first = runner(tmp_path, store, site, "one", 1, 10)
    assert len(first.written) == 10
    assert site.calls.count(SPEC.url) == 1 and len(site.calls) == 11     # index + ten chapters
    site.calls.clear()
    second = runner(tmp_path, store, site, "two", 1, 10)
    assert len(second.written) == 10 and site.calls == []                # manifest + cache only


def test_a_stale_index_can_never_hide_newly_published_chapters(tmp_path, runner):
    """Before 03.2b the index page itself was cached, so an open-ended re-run kept seeing the
    old last chapter. The index is now always fetched fresh; chapter pages stay cached."""
    store = ChapterManifestStore(tmp_path / "b", tmp_path / "r")
    site = Site(8)
    first = runner(tmp_path, store, site, "one", 1, 10**9)
    assert first.effective_range == (1, 8)
    site.count = 11                                                      # three new chapters
    site.calls.clear()
    second = runner(tmp_path, store, site, "two", 1, 10**9)
    assert second.effective_range == (1, 11)
    fetched_chapters = sorted(int(u.rsplit("-", 1)[1]) for u in site.calls if u != SPEC.url)
    assert site.calls.count(SPEC.url) == 1 and fetched_chapters == [9, 10, 11]
    assert len(second.written) == 11                                     # 1-8 from the cache


def test_an_oversized_end_is_clamped_from_a_fresh_index(tmp_path, runner):
    store = ChapterManifestStore(tmp_path / "b", tmp_path / "r")
    site = Site(6)
    report = runner(tmp_path, store, site, "one", 1, 5000)
    assert report.requested_range == (1, 5000) and report.effective_range == (1, 6)
    assert len(report.written) == 6
    site.count = 9
    again = runner(tmp_path, store, site, "two", 1, 5000)               # the novel grew since
    assert again.effective_range == (1, 9)


def test_a_cached_page_that_now_shows_another_chapter_is_refetched(tmp_path):
    store = ChapterManifestStore(tmp_path / "b", tmp_path / "r")
    site = Site(4)
    legacy_run(tmp_path, store, site, "one", 1, 4)
    # Tamper with chapter 3's cached page so it shows chapter 7 (valid provenance, wrong content).
    rm = _real_manager(tmp_path, site)
    url3 = f"{SPEC.url}/chapter-3"
    assert rm.cache.store(url3, chapter_page(3, shows=7), strategy="http")
    site.calls.clear()
    report = legacy_run(tmp_path, store, site, "two", 1, 4)
    assert report.failed == [] and len(report.written) == 4
    assert site.calls == [url3]                                          # only the bad page again
    assert "Title 3" in rm.cache.read(url3)[0]


def test_a_rescued_page_is_cached_only_after_it_extracts_as_the_right_chapter(tmp_path):
    from webnovel_scraper.request_manager import RequestManager as Real

    rm = Real("shadow-slave", cache_root=tmp_path / "cache", site_key="freewebnovel.com")
    pool = rp.RescuePool(primary_headless=False, use_cache=True, cache_root=tmp_path / "cache")
    adapter = FreeWebNovelAdapter(request_manager=rm)
    good, bad = _meta(2), _meta(3)
    rm._fetch_uncached_strategy = lambda url, _s: chapter_page(2) if url == good.url else chapter_page(3, shows=4)
    pool._default_fetch(good, manager=rm, adapter=adapter, mode=rp.HEADFUL_CAMOUFOX, fresh=False, budget=5)
    with pytest.raises(ChapterMismatchError):
        pool._default_fetch(bad, manager=rm, adapter=adapter, mode=rp.HEADFUL_CAMOUFOX, fresh=False, budget=5)
    assert rm._read_cache(good.url) is not None and rm._read_cache(bad.url) is None
