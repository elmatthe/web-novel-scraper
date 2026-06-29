"""0.1.2 tests: HTTP-layer Cloudflare avoidance + non-blocking browser launch.

All offline — no real network, no real browser launch, no real sleeps.

Covers:
  Task 1 — the primary (attempt-1) HTTP path warms the session on the host origin
           once, reuses one persistent session (cookies carry across chapters),
           and sends host-derived Referer + correct Sec-Fetch-Site (none on the
           warm-up address-bar GET, same-origin on subsequent in-site GETs).
  Task 2 — PLAYWRIGHT_BROWSERS_PATH defaults to the contained in-repo cache.
  Task 3 — a browser "Executable doesn't exist / playwright install" error is an
           immediate, non-retryable strategy failure: the ladder advances to the
           next rung WITHOUT the long exponential backoff sleep, and a chapter that
           exhausts the ladder is recorded failed while the run continues.
"""

from __future__ import annotations

import os

import pytest

from webnovel_scraper import browser_env, catalog, pipeline
from webnovel_scraper import request_manager as rm
from webnovel_scraper.models import ChapterContent, ChapterMeta, OutputMode, ScrapeJob
from webnovel_scraper.request_manager import RequestManager

ENABLED_NOVEL = "shadow-slave"
ENABLED_KEY = "freewebnovel"

# A real (non-challenge, non-garbled) chapter body so the HTTP path returns
# success instead of escalating.
_REAL_HTML = (
    "<html><body><div id='article'>"
    "<p>This is a real chapter body paragraph, long enough to count.</p>"
    "<p>And a second real paragraph of prose for the chapter.</p>"
    "</div></body></html>"
)

_LAUNCH_ERROR_MESSAGE = (
    "BrowserType.launch: Executable doesn't exist at "
    r"C:\Users\me\AppData\Local\ms-playwright\chromium_headless_shell-1180\chrome.exe"
    "\nLooks like Playwright was just installed or updated. Please run the "
    "following command to download new browsers: playwright install"
)


def _noop(*_a, **_k) -> None:
    return None


# ── Task 1: warm-up + persistent session + headers ──────────────────────────
class _RecordingResponse:
    def __init__(self, status_code: int = 200, text: str = _REAL_HTML) -> None:
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _RecordingSession:
    """Fake session that records each GET with a snapshot of headers + cookies and
    carries a cookie jar across calls — exactly the two things a persistent
    ``requests.Session`` does (cookie reuse + header continuity)."""

    def __init__(self, set_cookie_on: str | None = None) -> None:
        self.headers: dict = {}
        self.cookies: dict = {}
        self.calls: list[tuple] = []  # (url, headers_snapshot, cookies_snapshot)
        self._set_cookie_on = set_cookie_on

    def get(self, url: str, **kwargs) -> _RecordingResponse:
        self.calls.append((url, dict(self.headers), dict(self.cookies)))
        # Simulate Cloudflare issuing a clearance cookie on the homepage warm-up.
        if self._set_cookie_on is not None and url == self._set_cookie_on:
            self.cookies["cf_clearance"] = "warmed"
        return _RecordingResponse()

    def close(self) -> None:
        pass


def test_http_path_warms_host_once_and_chains_referer_and_cookies(tmp_path) -> None:
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)
    origin = "https://freewebnovel.com/"
    sess = _RecordingSession(set_cookie_on=origin)
    ch1 = "https://freewebnovel.com/novel/shadow-slave/chapter-1"
    ch2 = "https://freewebnovel.com/novel/shadow-slave/chapter-2"

    assert mgr._http_get(sess, ch1) == _REAL_HTML
    assert mgr._http_get(sess, ch2) == _REAL_HTML

    urls = [c[0] for c in sess.calls]
    # The warm-up homepage GET happens exactly once, before the first chapter; the
    # second chapter reuses the already-warmed host (no second warm-up).
    assert urls == [origin, ch1, ch2]

    # Warm-up looks like an address-bar navigation.
    _, warm_headers, _ = sess.calls[0]
    assert warm_headers["Sec-Fetch-Site"] == "none"
    assert "Referer" not in warm_headers

    # Chapter requests look like in-site clicks: same-origin + host-derived Referer.
    _, ch1_headers, ch1_cookies = sess.calls[1]
    assert ch1_headers["Sec-Fetch-Site"] == "same-origin"
    assert ch1_headers["Referer"] == origin
    # The clearance cookie set on warm-up persists into the chapter request: proof
    # the SAME persistent session (and its cookie jar) is reused across chapters.
    assert ch1_cookies.get("cf_clearance") == "warmed"

    _, ch2_headers, ch2_cookies = sess.calls[2]
    assert ch2_headers["Sec-Fetch-Site"] == "same-origin"
    assert ch2_headers["Referer"] == origin
    assert ch2_cookies.get("cf_clearance") == "warmed"


def test_warmup_failure_does_not_break_the_real_fetch(tmp_path) -> None:
    """A warm-up GET that errors must be swallowed; the real fetch still runs."""

    class _WarmupFailsSession(_RecordingSession):
        def get(self, url: str, **kwargs):
            self.calls.append((url, dict(self.headers), dict(self.cookies)))
            if url.endswith("/"):  # the origin warm-up
                raise RuntimeError("warm-up boom")
            return _RecordingResponse()

    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)
    sess = _WarmupFailsSession()
    ch = "https://freewebnovel.com/novel/shadow-slave/chapter-1"
    assert mgr._http_get(sess, ch) == _REAL_HTML
    # Both the failed warm-up and the successful chapter GET were attempted.
    assert [c[0] for c in sess.calls] == ["https://freewebnovel.com/", ch]


def test_browser_headers_are_browserlike_without_brotli_or_static_referer() -> None:
    h = rm.BROWSER_HEADERS
    assert "br" not in h["Accept-Encoding"]            # brotli fix preserved
    assert h["User-Agent"].startswith("Mozilla/5.0")   # realistic UA
    assert h["Sec-Ch-Ua-Platform"] == '"Windows"'
    # The old hardcoded cross-site Referer (webnovel.com on every request) is gone;
    # the correct host-derived Referer is set per request.
    assert "Referer" not in h


def test_session_is_persistent_and_reused(tmp_path) -> None:
    mgr = RequestManager("s", cache_root=tmp_path)
    assert mgr.session is mgr.session            # same object across accesses
    assert mgr.session.headers["User-Agent"] == rm.USER_AGENT


# ── Task 2: contained browsers path ──────────────────────────────────────────
def test_ensure_browsers_path_points_into_repo(monkeypatch) -> None:
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    value = browser_env.ensure_browsers_path()
    assert value == str(browser_env.CONTAINED_BROWSERS_PATH)
    assert value.replace("\\", "/").endswith("files/bin/ms-playwright")
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == value


def test_ensure_browsers_path_does_not_override_explicit(monkeypatch) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/custom/elsewhere")
    assert browser_env.ensure_browsers_path() == "/custom/elsewhere"


# ── Task 3: browser launch failure is immediate + non-blocking ───────────────
def test_launch_failure_classifier() -> None:
    assert rm._looks_like_browser_launch_failure(RuntimeError(_LAUNCH_ERROR_MESSAGE))
    assert rm._looks_like_browser_launch_failure(
        RuntimeError("playwright install")
    )
    assert rm._looks_like_browser_launch_failure(ModuleNotFoundError("No module named 'camoufox'"))
    assert rm._looks_like_browser_launch_failure(FileNotFoundError("missing binary"))
    # A genuine transient block is NOT a launch failure (must keep the backoff).
    assert not rm._looks_like_browser_launch_failure(
        RuntimeError("Cloudflare challenge still present")
    )
    assert not rm._looks_like_browser_launch_failure(RuntimeError("HTTP 503"))


def test_launch_failure_skips_backoff_and_advances_the_ladder(
    tmp_path, monkeypatch
) -> None:
    sleeps: list[float] = []
    mgr = RequestManager(
        "s",
        use_cache=False,
        cache_root=tmp_path,
        max_retries=5,           # 6 attempts = one per ladder rung
        retry_jitter_ratio=0.0,
        sleep_fn=sleeps.append,
    )
    strategies: list[str] = []

    def launch_fails(url, strategy):
        strategies.append(strategy)
        raise RuntimeError(_LAUNCH_ERROR_MESSAGE)

    monkeypatch.setattr(mgr, "_fetch_uncached_strategy", launch_fails)

    with pytest.raises(rm.FetchError):
        mgr.fetch("https://example.com/ch")

    # Every rung was attempted, in order — the ladder ADVANCED past each missing
    # engine instead of stalling on one.
    assert strategies == list(rm.DEFAULT_ESCALATION_LADDER)
    # And NOT ONE long backoff sleep happened — the freeze ("retrying in 102.7s
    # with playwright_stealth_fresh") cannot occur for a structural launch failure.
    assert sleeps == []


def test_launch_failure_still_backs_off_real_transient_blocks(
    tmp_path, monkeypatch
) -> None:
    """Guard against over-classifying: a genuine transient block on the HTTP rungs
    must still back off; only the launch-failure rungs skip the sleep."""
    sleeps: list[float] = []
    mgr = RequestManager(
        "s", use_cache=False, cache_root=tmp_path,
        max_retries=5, retry_jitter_ratio=0.0, sleep_fn=sleeps.append,
    )

    def mixed(url, strategy):
        # http + cloudscraper: a real challenge (transient -> must back off).
        if strategy in (rm.FETCH_STRATEGY_HTTP, rm.FETCH_STRATEGY_CLOUDSCRAPER):
            raise RuntimeError("Cloudflare challenge still present")
        # browser rungs: engine missing (launch failure -> no sleep).
        raise RuntimeError(_LAUNCH_ERROR_MESSAGE)

    monkeypatch.setattr(mgr, "_fetch_uncached_strategy", mixed)
    with pytest.raises(rm.FetchError):
        mgr.fetch("https://example.com/ch")

    # Only the two transient HTTP-rung failures produced a backoff (5s, 15s); the
    # browser-rung launch failures added no sleeps.
    assert sleeps == [5.0, 15.0]


class _LaunchFailAdapter:
    """Adapter whose chapter 2 always fails with a browser-launch error (engine not
    installed), the rest succeed — to prove the run CONTINUES past it."""

    def __init__(self, count: int) -> None:
        self.count = count
        self.warnings: list[str] = []

    def build_chapter_index(self, spec):
        return [ChapterMeta(index=n, url=f"https://example/{n}") for n in range(1, self.count + 1)]

    def fetch_chapter(self, meta, spec):
        if meta.index == 2:
            raise rm.FetchError(f"Giving up on https://example/2: {_LAUNCH_ERROR_MESSAGE}")
        return ChapterContent(
            index=meta.index,
            title=f"Title {meta.index}",
            paragraphs=[f"Body paragraph for chapter {meta.index}, long enough to keep."],
        )


def test_run_continues_when_a_chapter_exhausts_on_launch_failure(tmp_path) -> None:
    job = ScrapeJob(
        novel_slug=ENABLED_NOVEL,
        adapter_key=ENABLED_KEY,
        start=1,
        end=3,
        delay=0.0,
        output_mode=OutputMode.SEPARATE,
        use_cache=False,
        output_dir=tmp_path,
    )
    report = pipeline.run_scrape(
        job, adapter=_LaunchFailAdapter(count=3), log=_noop, sleep_fn=_noop
    )
    assert not report.cancelled
    assert 2 in report.failed              # the launch-failure chapter recorded
    assert 1 not in report.failed and 3 not in report.failed
    # The run kept going: chapters 1 and 3 were written despite chapter 2 failing.
    assert len(report.written) == 2
    written_names = " ".join(p.name for p in report.written)
    assert "Chapter 1" in written_names and "Chapter 3" in written_names
