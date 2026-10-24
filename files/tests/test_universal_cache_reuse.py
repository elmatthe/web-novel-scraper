"""Offline GUI-equivalent runs against all enabled sources; no real transport."""
from dataclasses import replace
import json
import socket
import requests

import pytest

from webnovel_scraper import catalog, pipeline
from webnovel_scraper.registry import REGISTRY
from webnovel_scraper.chapter_store import ChapterManifestStore
from webnovel_scraper.html_cache import HtmlCache
from webnovel_scraper.host_rate_limiter import HostRateLimiter
from webnovel_scraper.models import OutputMode, ScrapeJob
from webnovel_scraper.request_manager import RequestManager
from webnovel_scraper.source_identity import SourceIdentity

SPECS = [s for s in catalog.all_specs() if s.enabled]


@pytest.fixture(autouse=True)
def forbid_live_requests_and_browser_launch(monkeypatch):
    def fail(*_args, **_kwargs):
        pytest.fail("live network/browser initialization is forbidden in this offline suite")
    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket, "getaddrinfo", fail)
    monkeypatch.setattr(requests.Session, "request", fail)
    monkeypatch.setattr(RequestManager, "_ensure_camoufox_page", fail)
    monkeypatch.setattr(RequestManager, "_ensure_browser_page", fail)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Source:
    def __init__(self, spec, count=2):
        self.spec, self.count = spec, count
        self.calls = []

    def url(self, n):
        s = self.spec
        if s.adapter_key == "webnovel_dynamic":
            return f"{s.url}/{900000 + n}"
        if s.adapter_key == "freewebnovel_net":
            return f"{s.base_url}/{s.novel_slug}/chapter-{n}-original.html"
        return f"{s.url}/chapter-{n}"

    def page(self, n):
        if self.spec.adapter_key == "webnovel_dynamic":
            info = dict(bookId=self.spec.book_id, chapterId=str(900000 + n), chapterIndex=n,
                        chapterName=f"Chapter {n}: Original title", contents=[dict(content=f"Original prose number {n}.")])
            return '<html><body><script id="__NEXT_DATA__" type="application/json">' + json.dumps(
                dict(props=dict(pageProps=dict(data=dict(chapterInfo=info))))) + '</script></body></html>'
        return (f'<html><body><h1>Chapter {n}: Original title</h1><div id="article" class="chr-c">'
                f'<p>Original prose number {n}.</p></div></body></html>')

    def index(self):
        if self.spec.adapter_key == "webnovel_dynamic":
            data = dict(bookInfo=dict(bookId=self.spec.book_id, chapterCnt=self.count),
                        volumeItems=[dict(chapterItems=[dict(chapterIndex=n, chapterId=str(900000+n),
                            chapterName=f"Chapter {n}: Original title") for n in range(1, self.count+1)])])
            return '<html><script id="__NEXT_DATA__">' + json.dumps(dict(props=dict(pageProps=dict(data=data)))) + '</script></html>'
        links = ''.join(f'<li><a href="{self.url(n)}">Chapter {n}: Original title</a></li>' for n in range(1, self.count+1))
        return '<html><body><div id="idData"><ul class="list-chapter">' + links + '</ul></div></body></html>'

    def manager(self, root, clock, logs, *, refresh=False):
        rm = RequestManager(self.spec.novel_slug, cache_root=root, site_key=SourceIdentity.from_site_spec(self.spec).host,
                            log_fn=logs.append, refresh_cache=refresh, sleep_fn=clock.sleep,
                            host_limiter=HostRateLimiter(3, monotonic=clock.monotonic, sleep=clock.sleep))
        def fetch(url, strategy):
            rm._acquire_nav(url)
            self.calls.append(url)
            rm._last_final_url = url
            if url == self.spec.url:
                return self.index()
            n = next(n for n in range(1, self.count+1) if url == self.url(n))
            if self.spec.adapter_key == "webnovel_dynamic":
                rm._last_final_url = f"https://www.webnovel.com/book/{self.spec.book_id}/{900000+n}"
            return self.page(n)
        rm._fetch_uncached_strategy = fetch
        return rm


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.novel_slug+"-"+s.adapter_key)
def test_three_new_output_runs_and_restart_reuse_html(tmp_path, monkeypatch, spec):
    from pypdf import PdfReader
    source = Source(spec)
    root = tmp_path / "cache"
    results = []
    for attempt in range(3):
        clock, logs = Clock(), []
        rm = source.manager(root, clock, logs)
        # Each run reconstructs the manager, adapter and manifest store (application restart).
        store = ChapterManifestStore(tmp_path / "baseline", tmp_path / "runtime")
        job = ScrapeJob(spec.novel_slug, spec.adapter_key, start=1, end=5000,
                        output_dir=tmp_path / f"out-{attempt}", output_mode=OutputMode.SINGLE,
                        use_cache=True, use_browser=spec.use_browser, formats=("txt", "pdf"), delay=3,
                        chapter_list_mode="saved")
        before = len(source.calls)
        adapter = REGISTRY[spec.adapter_key](request_manager=rm, log=logs.append)
        report = pipeline.run_scrape(job, adapter=adapter, manifest_store=store, log=logs.append,
                                    sleep_fn=clock.sleep, monotonic_fn=clock.monotonic)
        assert not report.failed and len(report.written) == 2
        txt = next(p for p in report.written if p.suffix == ".txt").read_bytes()
        pdf = next(p for p in report.written if p.suffix == ".pdf")
        text = ''.join(p.extract_text() for p in PdfReader(pdf).pages)
        results.append((txt, text))
        for n in (1, 2):
            assert rm.cache.read(source.url(n)) is not None
        if attempt:
            assert len(source.calls) == before
            assert clock.sleeps == []
            assert report.from_cache == 2
            assert any("newer chapters" in line.lower() for line in logs)
            assert report.performance["website_requests"] == 0
            assert report.performance["cache_hits"] == 2
            assert report.performance["cache_misses"] == 0
            assert report.performance["discovery_seconds"] == 0
            assert any("Performance:" in line for line in logs)
            from webnovel_scraper.ui.run_activity import _coverage_events
            messages = [event.message for event in _coverage_events(report)]
            assert any("newer chapters were not checked" in message for message in messages)
            assert not any("are not available" in message for message in messages)
    assert results[0] == results[1] == results[2]


def test_redirect_identity_is_not_a_general_cross_host_exception(tmp_path):
    s = catalog.get_spec("the-noble-queen", "webnovel_dynamic")
    source, clock, logs = Source(s), Clock(), []
    rm = source.manager(tmp_path, clock, logs)
    url = source.url(1)
    for final in (f"https://evil.example/book/{s.book_id}/900001",
                  f"https://www.webnovel.com/book/{s.book_id}/900002",
                  "https://www.webnovel.com/book/123/900001"):
        assert not rm.cache.store(url, source.page(1), strategy="http", final_url=final)
    assert not rm.cache.store(url, source.page(2), strategy="http",
        final_url=f"https://www.webnovel.com/book/{s.book_id}/900001")


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.novel_slug+"-"+s.adapter_key)
def test_corruption_refresh_isolation_and_new_chapters(tmp_path, spec):
    source = Source(spec)
    clock, logs = Clock(), []
    def run(number, refresh=False):
        rm = source.manager(tmp_path / "cache", clock, logs)
        adapter = REGISTRY[spec.adapter_key](rm, log=logs.append)
        job = ScrapeJob(spec.novel_slug, spec.adapter_key, start=1, end=5000,
                        output_dir=tmp_path / f"run-{number}", output_mode=OutputMode.SINGLE,
                        formats=("txt",), use_cache=True, delay=3, refresh_chapter_list=refresh,
                        chapter_list_mode="update" if refresh else "saved")
        report = pipeline.run_scrape(job, adapter=adapter,
            manifest_store=ChapterManifestStore(tmp_path / "b", tmp_path / "r"), log=logs.append,
            sleep_fn=clock.sleep, monotonic_fn=clock.monotonic)
        return report, rm, adapter
    first, rm, _ = run(1)
    assert first.effective_range == (1, 2)
    bad = rm.cache.html_path(source.url(1))
    bad.write_text("corrupt", encoding="utf-8")
    before = len(source.calls)
    second, rm, _ = run(2)
    assert second.from_cache == 1
    assert source.calls[before:] == [source.url(1)]
    other = HtmlCache(tmp_path / "cache", "another-novel", site_key=SourceIdentity.from_site_spec(spec).host)
    assert other.read(source.url(1)) is None
    assert rm.cache.read(source.url(1).replace("https://", "https://other.")) is None
    source.count = 4
    third, rm, adapter = run(3, refresh=True)
    assert third.effective_range == (1, 4)
    assert third.written[0].read_bytes().index(b'Original prose number 3') < third.written[0].read_bytes().index(b'Original prose number 4')
    before = len(source.calls)
    fresh_rm = source.manager(tmp_path / "cache", clock, logs, refresh=True)
    fresh_adapter = REGISTRY[spec.adapter_key](fresh_rm, log=logs.append)
    from webnovel_scraper.models import ChapterMeta
    content = fresh_adapter.fetch_chapter(ChapterMeta(1, source.url(1), source_id="900001" if spec.book_id else None), spec)
    assert content.paragraphs == ["Original prose number 1."]
    assert source.calls[before:] == [source.url(1)]


@pytest.mark.parametrize("spec", [s for s in SPECS if s.use_browser], ids=lambda s: s.novel_slug)
def test_gui_browser_conductor_restarts_without_launching_browser(tmp_path, spec):
    source, root = Source(spec), tmp_path / "cache"
    for attempt in range(3):
        clock, logs = Clock(), []
        def factory(**_):
            return source.manager(root, clock, logs)
        job = ScrapeJob(spec.novel_slug, spec.adapter_key, start=1, end=5000,
                        output_dir=tmp_path / f"out-{attempt}", output_mode=OutputMode.SINGLE,
                        formats=("txt",), use_cache=True, use_browser=True, delay=3, chapter_list_mode="saved")
        before = len(source.calls)
        report = pipeline.run_scrape(job, request_manager_factory=factory,
            manifest_store=ChapterManifestStore(tmp_path / "b", tmp_path / "r"), log=logs.append,
            sleep_fn=clock.sleep, monotonic_fn=clock.monotonic)
        assert not report.failed
        if attempt:
            assert len(source.calls) == before and clock.sleeps == []
            assert report.from_cache == 2


def test_dynamic_redirect_through_fake_http_response_survives_restart(tmp_path):
    from types import SimpleNamespace
    from webnovel_scraper.models import ChapterMeta
    spec = catalog.get_spec("the-noble-queen", "webnovel_dynamic")
    source, clock, logs, calls = Source(spec), Clock(), [], []
    url = source.url(1)
    final = f"https://www.webnovel.com/book/{spec.book_id}/900001"
    for attempt in range(3):
        rm = RequestManager(spec.novel_slug, cache_root=tmp_path / "cache", site_key="dynamic.webnovel.com",
                            sleep_fn=clock.sleep, log_fn=logs.append,
                            host_limiter=HostRateLimiter(3, monotonic=clock.monotonic,sleep=clock.sleep))
        def get(requested, **kwargs):
            assert attempt == 0
            calls.append(requested)
            return SimpleNamespace(text=source.page(1) if requested == url else "<html></html>",
                url=final if requested == url else requested, status_code=200, raise_for_status=lambda: None)
        rm._session = SimpleNamespace(get=get, headers={}, close=lambda: None)
        adapter = REGISTRY[spec.adapter_key](rm)
        before, sleeps = len(calls), len(clock.sleeps)
        assert adapter.fetch_chapter(ChapterMeta(1,url,source_id="900001"),spec).paragraphs == ["Original prose number 1."]
        if attempt:
            assert len(calls) == before and len(clock.sleeps) == sleeps
        rm.close()
    assert len(calls) == 2  # one origin warm-up and one chapter response


def test_rendering_and_fetch_timings_are_separate_and_per_page_stays_detailed(tmp_path,monkeypatch):
    from webnovel_scraper.ui.run_activity import pipeline_event
    clock, logs = Clock(), []
    spec = catalog.get_spec("shadow-slave", "freewebnovel")
    source = Source(spec)
    rm = source.manager(tmp_path / "cache",clock,logs)
    render = pipeline.render_output
    def timed_render(*args,**kwargs):
        clock.sleep(2)
        render(*args,**kwargs)
    monkeypatch.setattr(pipeline,"render_output",timed_render)
    report = pipeline.run_scrape(ScrapeJob(spec.novel_slug,spec.adapter_key,start=1,end=2,delay=0,
        output_mode=OutputMode.SINGLE,use_cache=True,formats=("txt","pdf"),output_dir=tmp_path / "out"),
        adapter=REGISTRY[spec.adapter_key](rm,log=logs.append),log=logs.append,
        sleep_fn=clock.sleep,monotonic_fn=clock.monotonic)
    assert report.performance["rendering_seconds"] == 4
    assert report.performance["website_requests"] == 3
    assert report.performance["cache_misses"] == 2
    for message in logs:
        if any(marker in message for marker in ("cache HIT:","cache MISS:","Actual network chapter", "rendering time:")):
            assert not pipeline_event(message).summary
    assert pipeline_event(next(message for message in logs if message.startswith("Performance:"))).summary


def test_identity_rejected_cached_html_is_not_counted_as_a_successful_hit(tmp_path):
    from webnovel_scraper.models import ChapterMeta
    from webnovel_scraper.run_performance import RunPerformance
    spec = catalog.get_spec("shadow-slave","freewebnovel")
    source,clock,logs = Source(spec),Clock(),[]
    rm = source.manager(tmp_path / "cache",clock,logs)
    rm.performance = RunPerformance(clock.monotonic)
    assert rm.cache.store(source.url(1),source.page(2),strategy="http")
    content = REGISTRY[spec.adapter_key](rm).fetch_chapter(ChapterMeta(1,source.url(1)),spec)
    assert content.paragraphs == ["Original prose number 1."]
    assert rm.performance.values["cache_hits"] == 0
    assert rm.performance.values["cache_misses"] == 1
    assert source.calls == [source.url(1)]


def test_fast_saved_mode_does_not_silently_fetch_unverified_coverage(tmp_path):
    from webnovel_scraper.chapter_preflight import ChapterListRefused
    spec = catalog.get_spec("shadow-slave","freewebnovel")
    source,clock,logs = Source(spec),Clock(),[]
    def run(number,end,refresh=False):
        rm = source.manager(tmp_path / "cache",clock,logs)
        return pipeline.run_scrape(ScrapeJob(spec.novel_slug,spec.adapter_key,start=1,end=end,delay=0,
            output_mode=OutputMode.SINGLE,use_cache=True,formats=("txt",),output_dir=tmp_path / str(number),
            chapter_list_mode="update" if refresh else "saved",refresh_chapter_list=refresh),
            adapter=REGISTRY[spec.adapter_key](rm),log=logs.append,
            manifest_store=ChapterManifestStore(tmp_path / "b",tmp_path / "r"),
            sleep_fn=clock.sleep,monotonic_fn=clock.monotonic)
    assert run(1,1).effective_range == (1,1)
    before = len(source.calls)
    with pytest.raises(ChapterListRefused,match="does not verify this whole range"):
        run(2,5000)
    assert len(source.calls) == before
    assert run(3,5000,refresh=True).effective_range == (1,2)
