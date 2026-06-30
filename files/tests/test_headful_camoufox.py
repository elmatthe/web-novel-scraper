"""0.1.3 tests: headful persistent camoufox as the primary FreeWebNovel fetch path.

All offline/deterministic — no real browser launch, no network, no real sleeps.
The camoufox browser is mocked at the ``cf_bypass`` seam (``create_camoufox_browser``
+ ``fetch_camoufox``) so the tests exercise the REAL RequestManager reuse/warm-up/
bounding logic without ever launching Firefox.

Maps to the instruction's TESTS checklist:
  * Default config — FWN browser-primary; headless default False; HTTP-first off;
    GUI/job/spec defaults agree.
  * Browser reuse — camoufox created ONCE per run; same page handles many chapters;
    a normal success never recreates; the BROWSER session (not just HTTP) is warmed
    once.
  * Bounded failure recovery — one failed chapter does NOT trigger the six-engine
    sequence; retries bounded; browser recreation ≤1; failed recorded + run
    continues; the end-of-run sweep does not repeat an uncontrolled escalation.
  * Overrides — headless can be forced; HTTP-first can be opted in; non-FWN keeps
    its HTTP fast path.
"""

from __future__ import annotations

import pytest

from webnovel_scraper import catalog, cf_bypass, pipeline
from webnovel_scraper import request_manager as rm
from webnovel_scraper.models import (
    ChapterContent,
    ChapterMeta,
    OutputMode,
    ScrapeJob,
)
from webnovel_scraper.request_manager import RequestManager

CAMO = rm.FETCH_STRATEGY_CAMOUFOX
CAMO_FRESH = rm.FETCH_STRATEGY_CAMOUFOX_FRESH
STEALTH = rm.FETCH_STRATEGY_PLAYWRIGHT_STEALTH
STEALTH_FRESH = rm.FETCH_STRATEGY_PLAYWRIGHT_STEALTH_FRESH

_CLEAN = (
    "<html><body><div class='txt'>"
    "<p>A real chapter body paragraph, long enough to render.</p>"
    "<p>A second paragraph of prose for the chapter body.</p>"
    "</div></body></html>"
)
_CHALLENGE = "<html><head><title>Just a moment...</title></head><body>cf_chl_opt</body></html>"


# ── A mock camoufox engine wired at the cf_bypass seam ────────────────────────
class _FakeCamoufox:
    """Records browser creations + every fetch_camoufox navigation, so a test can
    assert the browser is created once and which URLs were visited (warm-up first)."""

    def __init__(self, *, challenge_urls: set | None = None) -> None:
        self.created = 0
        self.exited = 0
        self.navigations: list[str] = []
        self._challenge_urls = challenge_urls or set()

    def install(self, monkeypatch) -> None:
        outer = self

        class _CM:
            def __exit__(self, *exc) -> None:
                outer.exited += 1

        def create(*, headless, **_k):
            outer.created += 1
            outer.last_headless = headless
            return _CM(), object()

        def fetch(page, url, **_k):
            outer.navigations.append(url)
            return _CHALLENGE if url in outer._challenge_urls else _CLEAN

        monkeypatch.setattr(cf_bypass, "create_camoufox_browser", create)
        monkeypatch.setattr(cf_bypass, "fetch_camoufox", fetch)


class _BrowserAdapter:
    """Minimal adapter that drives a REAL RequestManager over the browser-primary
    path (``use_browser=True``) for each chapter — so the run exercises the real
    reuse + warm-up + bounded-ladder logic."""

    def __init__(self, manager: RequestManager, count: int, *, fail: set | None = None) -> None:
        self._rm = manager
        self.count = count
        self.warnings: list[str] = []
        self._fail = fail or set()

    def build_chapter_index(self, spec):
        return [
            ChapterMeta(index=n, url=f"https://freewebnovel.com/novel/x/chapter-{n}")
            for n in range(1, self.count + 1)
        ]

    def fetch_chapter(self, meta, spec):
        self._rm.fetch(meta.url, use_browser=True)  # walks the bounded camoufox ladder
        return ChapterContent(
            index=meta.index, title=f"T{meta.index}",
            paragraphs=["A body paragraph long enough to render in the PDF."],
        )


def _job(tmp_path, *, count=3, mode=OutputMode.SEPARATE) -> ScrapeJob:
    return ScrapeJob(
        novel_slug="shadow-slave",
        adapter_key="freewebnovel",
        start=1,
        end=count,
        delay=0.0,
        output_mode=mode,
        use_cache=False,
        output_dir=tmp_path,
    )


# ── Default config ───────────────────────────────────────────────────────────
def test_freewebnovel_catalog_rows_are_browser_primary() -> None:
    fwn = [s for s in catalog.all_specs() if s.adapter_key == "freewebnovel"]
    assert fwn, "expected FreeWebNovel rows in the catalog"
    assert all(s.use_browser for s in fwn)


def test_non_cloudflare_sites_keep_http_fast_path() -> None:
    wnd = catalog.get_spec("the-noble-queen", "webnovel_dynamic")
    assert wnd.use_browser is False  # WebNovel-dynamic stays on plain HTTP


def test_request_manager_default_is_visible_and_http_first_off() -> None:
    mgr = RequestManager("s")
    assert mgr.headless is False          # visible by default (0.1.3)
    assert mgr.try_http_first is False    # HTTP-first is opt-in


def test_scrapejob_http_first_defaults_off() -> None:
    job = _job_tmp = ScrapeJob(
        novel_slug="shadow-slave", adapter_key="freewebnovel", start=1, end=1,
        delay=0.0, output_mode=OutputMode.SEPARATE, use_cache=False, output_dir=".",
    )
    assert job.http_first is False


# ── Browser reuse + one-time browser-session warm-up ─────────────────────────
def test_browser_created_once_and_session_warmed_once(tmp_path, monkeypatch) -> None:
    fake = _FakeCamoufox()
    fake.install(monkeypatch)
    mgr = RequestManager("ss", use_cache=False, cache_root=tmp_path, sleep_fn=lambda _s: None)
    adapter = _BrowserAdapter(mgr, count=3)

    report = pipeline.run_scrape(
        _job(tmp_path, count=3), adapter=adapter, log=lambda _m: None,
        sleep_fn=lambda _s: None,
    )

    assert report.failed == []
    assert len(report.written) == 3
    # The camoufox browser was created EXACTLY once for the whole run — a normal
    # successful chapter fetch never recreates it.
    assert fake.created == 1
    origin = "https://freewebnovel.com/"
    # The BROWSER session was warmed exactly once (an origin GET), before the first
    # chapter — proving the warm-up is on the browser, not just the HTTP session.
    assert fake.navigations.count(origin) == 1
    assert fake.navigations[0] == origin
    # Then every chapter was fetched through that same warmed page, in order.
    assert fake.navigations[1:] == [
        f"https://freewebnovel.com/novel/x/chapter-{n}" for n in (1, 2, 3)
    ]


def test_normal_success_uses_single_camoufox_attempt(tmp_path, monkeypatch) -> None:
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)
    strategies: list[str] = []
    monkeypatch.setattr(
        mgr, "_fetch_uncached_strategy",
        lambda url, strategy: strategies.append(strategy) or _CLEAN,
    )
    assert mgr.fetch("https://freewebnovel.com/c/1", use_browser=True) == _CLEAN
    assert strategies == [CAMO]   # one attempt, no escalation


# ── Bounded failure recovery ─────────────────────────────────────────────────
def test_blocked_chapter_is_bounded_and_never_runs_six_engines(tmp_path, monkeypatch) -> None:
    """A chapter that fails every camoufox attempt walks ONLY the short bounded
    ladder (camoufox, camoufox, camoufox_fresh) — at most ONE fresh recovery — and
    NEVER touches the Chromium playwright-stealth rungs (the killed storm)."""
    strategies: list[str] = []
    mgr = RequestManager(
        "s", use_cache=False, cache_root=tmp_path, max_retries=6,
        retry_jitter_ratio=0.0, sleep_fn=lambda _s: None,
    )
    monkeypatch.setattr(
        mgr, "_fetch_uncached_strategy",
        lambda url, strategy: strategies.append(strategy) or (_ for _ in ()).throw(
            RuntimeError("Cloudflare challenge still present")
        ),
    )
    with pytest.raises(rm.FetchError):
        mgr.fetch("https://freewebnovel.com/c/1", use_browser=True)

    assert strategies == [CAMO, CAMO, CAMO_FRESH]
    assert strategies.count(CAMO_FRESH) == 1          # ≤1 fresh recovery
    assert STEALTH not in strategies and STEALTH_FRESH not in strategies
    assert len(strategies) <= 3                        # retries bounded


def test_browser_recreation_is_bounded_to_one_fresh(tmp_path, monkeypatch) -> None:
    """At the engine seam: a fully-blocked chapter recreates the camoufox browser at
    most once (the single camoufox_fresh recovery)."""
    chapter = "https://freewebnovel.com/novel/x/chapter-1"
    fake = _FakeCamoufox(challenge_urls={chapter})  # chapter always challenges; origin clears
    fake.install(monkeypatch)
    mgr = RequestManager(
        "s", use_cache=False, cache_root=tmp_path, max_retries=6,
        retry_jitter_ratio=0.0, sleep_fn=lambda _s: None,
    )
    with pytest.raises(rm.FetchError):
        mgr.fetch(chapter, use_browser=True)
    # Initial browser + exactly ONE fresh recovery = 2 creations, never a storm.
    assert fake.created == 2


def test_one_failed_chapter_records_and_run_continues_with_bounded_sweep(
    tmp_path, monkeypatch
) -> None:
    """One blocked chapter is recorded failed, the run continues, and the end-of-run
    sweep re-attempts it with the SAME bounded ladder (no uncontrolled escalation)."""
    recorded: list[str] = []
    ch2 = "https://freewebnovel.com/novel/x/chapter-2"

    def fake_strategy(url, strategy):
        recorded.append(strategy)
        if url == ch2:
            raise RuntimeError("Cloudflare challenge still present")
        return _CLEAN

    mgr = RequestManager(
        "ss", use_cache=False, cache_root=tmp_path, max_retries=6,
        retry_jitter_ratio=0.0, sleep_fn=lambda _s: None,
    )
    monkeypatch.setattr(mgr, "_fetch_uncached_strategy", fake_strategy)
    adapter = _BrowserAdapter(mgr, count=3)

    report = pipeline.run_scrape(
        _job(tmp_path, count=3), adapter=adapter, log=lambda _m: None,
        sleep_fn=lambda _s: None,
    )

    # Chapter 2 failed and stayed failed; 1 and 3 were written; run did not abort.
    assert report.failed == [2]
    assert not report.cancelled
    assert len(report.written) == 2
    # Chapter 2 was attempted on the main pass AND the bounded sweep — each a short
    # 3-rung camoufox walk (6 total), and NOT ONE stealth rung was ever reached.
    ch2_attempts = [s for s in recorded if s in (CAMO, CAMO_FRESH)]
    assert recorded.count(STEALTH) == 0 and recorded.count(STEALTH_FRESH) == 0
    # main pass (3) + sweep (3) = 6 bounded camoufox attempts for the one bad chapter
    # (chapters 1 and 3 add one CAMO each on their single successful attempt).
    assert recorded.count(CAMO_FRESH) == 2          # one fresh recovery per bounded walk
    assert ch2_attempts.count(CAMO) >= 4


# ── Overrides ─────────────────────────────────────────────────────────────────
def test_user_can_force_headless(tmp_path, monkeypatch) -> None:
    fake = _FakeCamoufox()
    fake.install(monkeypatch)
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path, headless=True)
    mgr._fetch_camoufox_once("https://freewebnovel.com/c/1", fresh_context=False)
    assert fake.last_headless is True   # the headless override reached camoufox


def test_user_can_opt_into_http_first(tmp_path, monkeypatch) -> None:
    mgr = RequestManager(
        "s", use_cache=False, cache_root=tmp_path, try_http_first=True,
        retry_jitter_ratio=0.0, sleep_fn=lambda _s: None,
    )
    strategies: list[str] = []
    monkeypatch.setattr(
        mgr, "_fetch_uncached_strategy",
        lambda url, strategy: strategies.append(strategy) or _CLEAN,
    )
    assert mgr.fetch("https://freewebnovel.com/c/1", use_browser=True) == _CLEAN
    # HTTP-first: the first rung tried is plain HTTP, not camoufox.
    assert strategies[0] == rm.FETCH_STRATEGY_HTTP


def test_non_browser_path_starts_on_http_and_does_not_force_browser(tmp_path, monkeypatch) -> None:
    """WebNovel-dynamic-style fetch (use_browser=False) starts on the HTTP fast path
    and succeeds without ever touching a browser rung."""
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)
    strategies: list[str] = []
    monkeypatch.setattr(
        mgr, "_fetch_uncached_strategy",
        lambda url, strategy: strategies.append(strategy) or _CLEAN,
    )
    assert mgr.fetch("https://dynamic.webnovel.com/story/1") == _CLEAN
    assert strategies == [rm.FETCH_STRATEGY_HTTP]   # one HTTP attempt, no browser
