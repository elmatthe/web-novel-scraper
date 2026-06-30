"""Phase 2 tests: request_manager + pdf_builder. Offline — no real network.

The browser path (Playwright) is not exercised here; it is covered by manual
smoke testing in later phases. Network is either pre-seeded via the cache or a
faked session, so these tests never touch the wire.
"""

from __future__ import annotations

import threading

import pytest
from pypdf import PdfReader

from webnovel_scraper import request_manager as rm
from webnovel_scraper.models import ChapterContent
from webnovel_scraper.pdf_builder import (
    _is_chapter_heading,
    chapters_to_text,
    create_pdf,
    create_pdf_from_text,
    remove_single_heading_pages,
)
from webnovel_scraper.request_manager import RequestManager, cache_key_for


# ── RequestManager ───────────────────────────────────────────────────────────
def test_request_manager_instantiates_and_derives_cache_dir(tmp_path) -> None:
    mgr = RequestManager("shadow-slave", cache_root=tmp_path)
    assert mgr.slug == "shadow-slave"
    assert mgr.use_cache is True
    assert mgr.cache_dir == tmp_path / "shadow-slave"


def test_cache_key_is_deterministic() -> None:
    url = "https://freewebnovel.com/novel/shadow-slave/chapter-5"
    assert cache_key_for(url) == cache_key_for(url)
    assert cache_key_for(url) != cache_key_for(url + "6")
    assert cache_key_for(url).endswith(".html")


def test_cache_path_lives_under_slug_dir(tmp_path) -> None:
    mgr = RequestManager("nq", cache_root=tmp_path)
    url = "https://example.com/x"
    path = mgr.cache_path_for(url)
    assert path.parent == tmp_path / "nq"
    assert path.name == cache_key_for(url)


def test_retry_constants_defined_and_positive() -> None:
    assert rm.MAX_RETRIES > 0
    assert rm.RETRY_SLEEP > 0
    assert rm.MAX_RETRY_SLEEP >= rm.RETRY_SLEEP
    assert rm.FETCH_TIMEOUT > 0
    assert rm.RETRY_STATUS_MIN >= 500
    assert rm.PERMANENT_STATUSES == (403, 404)


def test_cloudflare_challenge_detection() -> None:
    assert rm.is_cloudflare_challenge("<title>Just a moment...</title>")
    assert rm.is_cloudflare_challenge("<div class='cf-browser-verification'>")
    assert not rm.is_cloudflare_challenge("<html><body>real content</body></html>")


def test_fetch_html_serves_from_cache_without_network(tmp_path) -> None:
    mgr = RequestManager("slug", cache_root=tmp_path)
    url = "https://example.com/ch1"
    cache_path = mgr.cache_path_for(url)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("<html>cached body</html>", encoding="utf-8")
    # No session is created; a hit returns the cached text directly.
    assert mgr.fetch_html(url) == "<html>cached body</html>"


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.headers: dict = {}

    def get(self, url: str, **kwargs) -> _FakeResponse:
        return self._response

    def close(self) -> None:
        pass


def test_fetch_html_permanent_404_raises_fetcherror(tmp_path, monkeypatch) -> None:
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)
    monkeypatch.setattr(mgr, "_session", _FakeSession(_FakeResponse(404)))
    with pytest.raises(rm.FetchError):
        mgr.fetch_html("https://example.com/missing")


def test_fetch_html_success_via_faked_session(tmp_path, monkeypatch) -> None:
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)
    monkeypatch.setattr(
        mgr, "_session", _FakeSession(_FakeResponse(200, "<html>ok</html>"))
    )
    assert mgr.fetch_html("https://example.com/ok") == "<html>ok</html>"


# ── RequestManager: unified fetch facade + retry ladder ─────────────────────
def test_cancel_event_is_threading_event(tmp_path) -> None:
    mgr = RequestManager("s", cache_root=tmp_path)
    assert isinstance(mgr.cancel_event, threading.Event)
    assert not mgr.cancel_event.is_set()


def test_start_returns_self(tmp_path) -> None:
    mgr = RequestManager("s", cache_root=tmp_path)
    assert mgr.start() is mgr


def test_backoff_timing_without_jitter() -> None:
    delays = [
        rm.compute_backoff_delay(n, jitter_ratio=0.0)
        for n in range(1, 5)
    ]
    assert delays == [5.0, 15.0, 45.0, 120.0]


@pytest.mark.parametrize("success_attempt", [2, 3, 4])
def test_fetch_retry_succeeds_on_attempt_2_3_or_4(
    tmp_path, monkeypatch, success_attempt
) -> None:
    sleeps: list[float] = []
    mgr = RequestManager(
        "s",
        use_cache=False,
        cache_root=tmp_path,
        max_retries=4,
        retry_jitter_ratio=0.0,
        sleep_fn=sleeps.append,
    )
    strategies: list[str] = []

    def fake_strategy(url, strategy):
        strategies.append(strategy)
        if len(strategies) < success_attempt:
            raise RuntimeError("temporary network failure")
        return "<html>ok</html>"

    monkeypatch.setattr(mgr, "_fetch_uncached_strategy", fake_strategy)

    assert mgr.fetch("https://example.com/ch") == "<html>ok</html>"
    assert len(strategies) == success_attempt
    assert strategies == list(rm.DEFAULT_ESCALATION_LADDER[:success_attempt])
    assert sleeps == [5.0, 15.0, 45.0][: success_attempt - 1]


def test_fetch_escalation_ladder_advances_per_attempt(tmp_path, monkeypatch) -> None:
    # max_retries=5 -> 6 attempts, exactly enough to walk every rung of the 6-rung
    # ladder (http, cloudscraper, camoufox, camoufox_fresh, playwright_stealth,
    # playwright_stealth_fresh) once.
    sleeps: list[float] = []
    mgr = RequestManager(
        "s",
        use_cache=False,
        cache_root=tmp_path,
        max_retries=5,
        retry_jitter_ratio=0.0,
        sleep_fn=sleeps.append,
    )
    strategies: list[str] = []

    def always_blocked(url, strategy):
        strategies.append(strategy)
        raise RuntimeError("Cloudflare challenge still active")

    monkeypatch.setattr(mgr, "_fetch_uncached_strategy", always_blocked)

    with pytest.raises(rm.FetchError):
        mgr.fetch("https://example.com/cf")

    # Every rung walked, in order — including the two stealth rescue rungs.
    assert strategies == list(rm.DEFAULT_ESCALATION_LADDER)
    assert strategies[-2:] == [
        rm.FETCH_STRATEGY_PLAYWRIGHT_STEALTH,
        rm.FETCH_STRATEGY_PLAYWRIGHT_STEALTH_FRESH,
    ]
    # Backoff is computed/capped per retry: 5, 15, 45, 120 (capped), 120 (capped).
    assert sleeps == [5.0, 15.0, 45.0, 120.0, 120.0]


def test_fetch_use_browser_is_bounded_headful_camoufox(tmp_path, monkeypatch) -> None:
    """0.1.3: browser-primary (FreeWebNovel default) walks the SHORT bounded
    headful-camoufox ladder — a couple of same-page retries then ONE fresh-page
    recovery — and never the six-engine storm (no Chromium playwright-stealth)."""
    sleeps: list[float] = []
    mgr = RequestManager(
        "s",
        use_cache=False,
        cache_root=tmp_path,
        max_retries=6,           # generous job budget — the browser path caps itself
        retry_jitter_ratio=0.0,
        sleep_fn=sleeps.append,
    )
    strategies: list[str] = []

    def fake_strategy(url, strategy):
        strategies.append(strategy)
        raise RuntimeError("browser blocked")

    monkeypatch.setattr(mgr, "_fetch_uncached_strategy", fake_strategy)

    with pytest.raises(rm.FetchError):
        mgr.fetch("https://example.com/cf", use_browser=True)

    # Exactly the bounded ladder: camoufox, camoufox (same warmed page), then ONE
    # camoufox_fresh recovery. NOT the full BROWSER_ESCALATION_LADDER, and no
    # stealth rungs are ever reached on the browser-primary path.
    assert strategies == list(rm.HEADFUL_PRIMARY_LADDER)
    assert strategies == [
        rm.FETCH_STRATEGY_CAMOUFOX,
        rm.FETCH_STRATEGY_CAMOUFOX,
        rm.FETCH_STRATEGY_CAMOUFOX_FRESH,
    ]
    # At most ONE fresh-page recovery — the storm-killer guarantee.
    assert strategies.count(rm.FETCH_STRATEGY_CAMOUFOX_FRESH) == 1
    assert rm.FETCH_STRATEGY_PLAYWRIGHT_STEALTH not in strategies
    # Two backoffs between the three bounded attempts (5s, 15s); no 5+ retry storm.
    assert sleeps == [5.0, 15.0]


def test_fetch_browser_first_success_does_not_escalate(tmp_path, monkeypatch) -> None:
    """A normal successful browser-primary fetch uses ONE camoufox attempt and never
    escalates to a fresh page — the happy-path reuse the long run depends on."""
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)
    strategies: list[str] = []

    def fake_strategy(url, strategy):
        strategies.append(strategy)
        return "<html>browser ok</html>"

    monkeypatch.setattr(mgr, "_fetch_uncached_strategy", fake_strategy)
    assert mgr.fetch("https://example.com/cf", use_browser=True) == "<html>browser ok</html>"
    assert strategies == [rm.FETCH_STRATEGY_CAMOUFOX]


def test_fetch_browser_http_first_opt_in_tries_http_then_camoufox(tmp_path, monkeypatch) -> None:
    """With try_http_first the browser-primary path tries two cheap HTTP rungs before
    falling back to the bounded camoufox path (still no stealth storm)."""
    mgr = RequestManager(
        "s", use_cache=False, cache_root=tmp_path, try_http_first=True,
        max_retries=6, retry_jitter_ratio=0.0, sleep_fn=lambda _s: None,
    )
    strategies: list[str] = []

    def fake_strategy(url, strategy):
        strategies.append(strategy)
        raise RuntimeError("blocked")

    monkeypatch.setattr(mgr, "_fetch_uncached_strategy", fake_strategy)
    with pytest.raises(rm.FetchError):
        mgr.fetch("https://example.com/cf", use_browser=True)
    assert strategies == list(rm.HTTP_FIRST_PRIMARY_LADDER)
    assert strategies == [
        rm.FETCH_STRATEGY_HTTP,
        rm.FETCH_STRATEGY_CLOUDSCRAPER,
        rm.FETCH_STRATEGY_CAMOUFOX,
        rm.FETCH_STRATEGY_CAMOUFOX_FRESH,
    ]
    assert rm.FETCH_STRATEGY_PLAYWRIGHT_STEALTH not in strategies


def test_camoufox_fresh_reset_rebuilds_browser(tmp_path, monkeypatch) -> None:
    """The ``camoufox_fresh`` strategy must exit the live Camoufox context and
    build a new one; a plain camoufox attempt reuses it. Guards the lazy-reuse +
    fresh-reset lifecycle the long FreeWebNovel run depends on."""
    from webnovel_scraper import cf_bypass

    built = {"count": 0}
    exited = {"count": 0}

    class _FakeCM:
        def __exit__(self, *exc):
            exited["count"] += 1

    class _FakePage:
        pass

    def fake_create_camoufox_browser(*, headless):
        built["count"] += 1
        return _FakeCM(), _FakePage()

    def fake_fetch_camoufox(page, url, **kwargs):
        return "<html>camoufox body</html>"

    monkeypatch.setattr(cf_bypass, "create_camoufox_browser", fake_create_camoufox_browser)
    monkeypatch.setattr(cf_bypass, "fetch_camoufox", fake_fetch_camoufox)

    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)

    h1 = mgr._fetch_camoufox_once("https://example.com/ch1", fresh_context=False)
    h2 = mgr._fetch_camoufox_once("https://example.com/ch2", fresh_context=False)
    h3 = mgr._fetch_camoufox_once("https://example.com/ch3", fresh_context=True)

    assert h1 == h2 == h3 == "<html>camoufox body</html>"
    assert built["count"] == 2   # reused for ch1+ch2, rebuilt once for the fresh ch3
    assert exited["count"] == 1  # the fresh reset exited the first context


def test_camoufox_strategy_dispatch(tmp_path, monkeypatch) -> None:
    """``_fetch_uncached_strategy`` routes the camoufox strategies and treats a
    returned challenge page as retryable."""
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)
    calls: list[bool] = []

    def fake_camoufox_once(url, *, fresh_context):
        calls.append(fresh_context)
        return "<html>clean page</html>"

    monkeypatch.setattr(mgr, "_fetch_camoufox_once", fake_camoufox_once)

    assert mgr._fetch_uncached_strategy("u", rm.FETCH_STRATEGY_CAMOUFOX) == "<html>clean page</html>"
    assert mgr._fetch_uncached_strategy("u", rm.FETCH_STRATEGY_CAMOUFOX_FRESH) == "<html>clean page</html>"
    assert calls == [False, True]


def test_browser_fresh_reset_does_not_restart_playwright(tmp_path, monkeypatch) -> None:
    """Regression: the ``browser_fresh`` strategy must reuse the live Playwright
    driver, not start a second one.

    ``_reset_browser`` tears down the browser/context/page but keeps ``self._pw``;
    before the fix ``_ensure_browser_page`` then called ``sync_playwright().start()``
    again, which on the same thread raises "Sync API inside the asyncio loop" and
    poisoned every later browser attempt for the rest of a run (every chapter
    failed). Assert Playwright is started exactly once across a fresh-context reset.
    """
    import playwright.sync_api as pw_sync

    from webnovel_scraper import cf_bypass

    starts = {"count": 0}

    class _FakePage:
        def close(self) -> None:
            pass

    class _FakeContext:
        def new_page(self):
            return _FakePage()

        def close(self) -> None:
            pass

    class _FakeBrowser:
        def close(self) -> None:
            pass

    class _FakePW:
        def stop(self) -> None:
            pass

    class _FakeSyncPlaywright:
        def start(self):
            starts["count"] += 1
            return _FakePW()

    contexts_built = {"count": 0}

    def fake_create_stealth_browser(pw, *, headless):
        contexts_built["count"] += 1
        return _FakeBrowser(), _FakeContext()

    def fake_fetch_with_stealth(page, url, **kwargs):
        return "<html>chapter body</html>"

    monkeypatch.setattr(pw_sync, "sync_playwright", lambda: _FakeSyncPlaywright())
    monkeypatch.setattr(cf_bypass, "create_stealth_browser", fake_create_stealth_browser)
    monkeypatch.setattr(cf_bypass, "fetch_with_stealth", fake_fetch_with_stealth)

    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)

    html1 = mgr._fetch_browser_once("https://example.com/ch1", fresh_context=False)
    html2 = mgr._fetch_browser_once("https://example.com/ch1", fresh_context=True)

    assert html1 == "<html>chapter body</html>"
    assert html2 == "<html>chapter body</html>"
    assert starts["count"] == 1          # Playwright started once, not re-started
    assert contexts_built["count"] == 2  # but a fresh browser/context per fresh_context


def test_fetch_permanent_404_does_not_retry(tmp_path, monkeypatch) -> None:
    sleeps: list[float] = []
    mgr = RequestManager(
        "s",
        use_cache=False,
        cache_root=tmp_path,
        max_retries=4,
        retry_jitter_ratio=0.0,
        sleep_fn=sleeps.append,
    )
    strategies: list[str] = []

    def missing(url, strategy):
        strategies.append(strategy)
        raise RuntimeError("HTTP 404 for https://example.com/missing")

    monkeypatch.setattr(mgr, "_fetch_uncached_strategy", missing)

    with pytest.raises(rm.FetchError):
        mgr.fetch("https://example.com/missing")

    assert strategies == [rm.FETCH_STRATEGY_HTTP]
    assert sleeps == []


def test_fetch_defaults_use_cache_to_instance(tmp_path, monkeypatch) -> None:
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)
    seen: dict[str, bool] = {}

    def fake_ladder(url, *, use_cache, ladder, max_retries, retry_base_delay):
        seen["use_cache"] = use_cache
        return "ok"

    monkeypatch.setattr(mgr, "_fetch_with_retry_ladder", fake_ladder)
    mgr.fetch("https://example.com/x")
    assert seen["use_cache"] is False  # inherited from instance use_cache=False


def test_fetch_raises_scrapecancelled_when_cancel_set(tmp_path) -> None:
    mgr = RequestManager("s", cache_root=tmp_path)
    mgr.cancel_event.set()
    with pytest.raises(rm.ScrapeCancelled):
        mgr.fetch("https://example.com/anything")


# ── pdf_builder: heading detection ───────────────────────────────────────────
def test_is_chapter_heading_accepts_valid_headings() -> None:
    assert _is_chapter_heading("Chapter 5: The Gate.")
    assert _is_chapter_heading("Chapter 1818:.")          # empty title allowed
    assert _is_chapter_heading("chapter 7: lower case ok.")  # IGNORECASE


def test_is_chapter_heading_rejects_non_headings() -> None:
    assert not _is_chapter_heading("Chapter 5 The Gate")   # no colon / period
    assert not _is_chapter_heading("Just some body text.")
    assert not _is_chapter_heading("Chapter 5: " + "x" * 600 + ".")  # too long


# ── pdf_builder: feed text ───────────────────────────────────────────────────
def test_chapters_to_text_contains_heading_and_body() -> None:
    chapters = [
        ChapterContent(index=1, title="The Gate", paragraphs=["Body one.", "Body two."])
    ]
    text = chapters_to_text(chapters)
    assert "Chapter 1: The Gate." in text
    assert "Body one." in text
    assert "Body two." in text


def test_chapters_to_text_formfeed_between_chapters() -> None:
    chapters = [
        ChapterContent(index=1, title="A", paragraphs=["x"]),
        ChapterContent(index=2, title="B", paragraphs=["y"]),
    ]
    text = chapters_to_text(chapters)
    assert text.count("\f") == 1


# ── pdf_builder: rendering + post-processing ─────────────────────────────────
def test_create_pdf_produces_valid_file(tmp_path) -> None:
    chapters = [
        ChapterContent(index=1, title="The Gate", paragraphs=["Word " * 60]),
        ChapterContent(index=2, title="The Keeper", paragraphs=["Word " * 60]),
    ]
    out = tmp_path / "out.pdf"
    result = create_pdf(chapters, out, title="Test Novel")
    assert result == out
    assert out.is_file() and out.stat().st_size > 0
    assert len(PdfReader(str(out)).pages) >= 1


def test_remove_single_heading_pages_drops_heading_only_page(tmp_path) -> None:
    out = tmp_path / "h.pdf"
    text = (
        "Chapter 1: Lonely Heading.\f\n\n"
        "Chapter 2: Real.\n\n" + ("Body sentence here. " * 40)
    )
    create_pdf_from_text(text, out)
    before = len(PdfReader(str(out)).pages)
    remove_single_heading_pages(out)
    after = len(PdfReader(str(out)).pages)
    assert after < before  # the heading-only first page is removed
