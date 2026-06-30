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


class _FakeBrowsers:
    """Mocks BOTH browser engines (camoufox + stealth-Chromium) at the cf_bypass /
    sync_playwright seams — so a test can drive the real RequestManager two-engine
    fallback logic (create-once, reuse, headful) without launching anything.

    Configure per-engine outcomes: ``camoufox_clears`` / ``stealth_clears`` decide
    whether each engine returns clean HTML or a Cloudflare challenge page. A callable
    may be passed for ``stealth_clears`` to vary by attempt (for sweep tests).
    ``stealth_launch_raises`` simulates a missing/unlaunchable Chromium.
    """

    def __init__(self, *, camoufox_clears=False, stealth_clears=True,
                 stealth_launch_raises: Exception | None = None) -> None:
        self.camoufox_created = 0
        self.camoufox_exited = 0
        self.stealth_created = 0
        self.camoufox_nav: list[str] = []
        self.stealth_nav: list[str] = []
        self.camoufox_headless = None
        self.stealth_headless = None
        self._camoufox_clears = camoufox_clears
        self._stealth_clears = stealth_clears
        self._stealth_launch_raises = stealth_launch_raises

    def install(self, monkeypatch) -> None:
        import playwright.sync_api as pw_sync

        outer = self

        # ── camoufox ──
        class _CM:
            def __exit__(self, *exc) -> None:
                outer.camoufox_exited += 1

        def create_camoufox(*, headless, **_k):
            outer.camoufox_created += 1
            outer.camoufox_headless = headless
            return _CM(), object()

        def fetch_camoufox(page, url, **_k):
            outer.camoufox_nav.append(url)
            return _CLEAN if outer._camoufox_clears else _CHALLENGE

        monkeypatch.setattr(cf_bypass, "create_camoufox_browser", create_camoufox)
        monkeypatch.setattr(cf_bypass, "fetch_camoufox", fetch_camoufox)

        # ── stealth-Chromium ──
        class _PW:
            def stop(self) -> None:
                pass

        class _SyncPW:
            def start(self):
                return _PW()

        class _Page:
            def close(self) -> None:
                pass

        class _Ctx:
            def new_page(self):
                return _Page()

            def close(self) -> None:
                pass

        class _Browser:
            def close(self) -> None:
                pass

        def create_stealth(pw, *, headless, **_k):
            if outer._stealth_launch_raises is not None:
                raise outer._stealth_launch_raises
            outer.stealth_created += 1
            outer.stealth_headless = headless
            return _Browser(), _Ctx()

        def fetch_with_stealth(page, url, **_k):
            outer.stealth_nav.append(url)
            clears = outer._stealth_clears
            if callable(clears):
                clears = clears(len(outer.stealth_nav))
            return _CLEAN if clears else _CHALLENGE

        monkeypatch.setattr(pw_sync, "sync_playwright", lambda: _SyncPW())
        monkeypatch.setattr(cf_bypass, "create_stealth_browser", create_stealth)
        monkeypatch.setattr(cf_bypass, "fetch_with_stealth", fetch_with_stealth)


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


# ── Bounded failure recovery + headful stealth-Chromium fallback ─────────────
def test_blocked_chapter_escalates_to_stealth_fallback_exactly_once(tmp_path, monkeypatch) -> None:
    """A chapter camoufox cannot clear walks the bounded two-engine ladder
    (camoufox, camoufox, camoufox_fresh) and escalates to the headful
    stealth-Chromium fallback EXACTLY ONCE — never a six-engine storm."""
    strategies: list[str] = []

    def fake_strategy(url, strategy):
        strategies.append(strategy)
        if strategy == STEALTH:
            return _CLEAN          # stealth-Chromium clears what camoufox couldn't
        raise RuntimeError("Cloudflare challenge still present")

    mgr = RequestManager(
        "s", use_cache=False, cache_root=tmp_path, max_retries=6,
        retry_jitter_ratio=0.0, sleep_fn=lambda _s: None,
    )
    monkeypatch.setattr(mgr, "_fetch_uncached_strategy", fake_strategy)
    assert mgr.fetch("https://freewebnovel.com/c/1", use_browser=True) == _CLEAN

    assert strategies == [CAMO, CAMO, CAMO_FRESH, STEALTH]
    assert strategies.count(CAMO_FRESH) == 1          # ≤1 fresh camoufox recovery
    assert strategies.count(STEALTH) == 1             # exactly ONE stealth escalation
    assert STEALTH_FRESH not in strategies            # no stealth_fresh storm
    assert len(strategies) == 4                        # the per-chapter cap


def test_stealth_fallback_is_headful(tmp_path, monkeypatch) -> None:
    """The stealth-Chromium fallback runs VISIBLE (headless=False) — the exact legacy
    engine/config — as does camoufox."""
    fake = _FakeBrowsers(camoufox_clears=False, stealth_clears=True)
    fake.install(monkeypatch)
    mgr = RequestManager(
        "s", use_cache=False, cache_root=tmp_path, max_retries=6,
        retry_jitter_ratio=0.0, sleep_fn=lambda _s: None,
    )
    chapter = "https://freewebnovel.com/novel/x/chapter-1"
    assert mgr.fetch(chapter, use_browser=True) == _CLEAN
    assert fake.stealth_created == 1
    assert fake.stealth_headless is False            # headful stealth-Chromium
    assert fake.camoufox_headless is False           # headful camoufox too
    # The stealth engine fetched the chapter exactly once (the single fallback).
    assert fake.stealth_nav.count(chapter) == 1


def test_stealth_fallback_browser_created_once_and_reused(tmp_path, monkeypatch) -> None:
    """When camoufox can't clear the site, the stealth-Chromium fallback browser is
    created ONCE per run and reused across every later fallback chapter — never
    relaunched per chapter."""
    fake = _FakeBrowsers(camoufox_clears=False, stealth_clears=True)
    fake.install(monkeypatch)
    mgr = RequestManager(
        "ss", use_cache=False, cache_root=tmp_path, max_retries=6,
        retry_jitter_ratio=0.0, sleep_fn=lambda _s: None,
    )
    adapter = _BrowserAdapter(mgr, count=3)
    report = pipeline.run_scrape(
        _job(tmp_path, count=3), adapter=adapter, log=lambda _m: None,
        sleep_fn=lambda _s: None,
    )

    assert report.failed == []
    assert len(report.written) == 3
    # ONE stealth-Chromium browser for the whole run, reused across all 3 chapters.
    assert fake.stealth_created == 1
    assert fake.stealth_nav.count("https://freewebnovel.com/novel/x/chapter-1") == 1
    assert fake.stealth_nav.count("https://freewebnovel.com/novel/x/chapter-2") == 1
    assert fake.stealth_nav.count("https://freewebnovel.com/novel/x/chapter-3") == 1
    # camoufox was only created while it was still being tried (before the latch);
    # it is NOT relaunched for the latched chapters.
    assert fake.camoufox_created <= 2


def test_run_latches_to_stealth_after_first_fallback(tmp_path, monkeypatch) -> None:
    """After camoufox is exhausted once, later browser-primary fetches go straight to
    the persistent stealth engine (no camoufox replay → no Chromium relaunch)."""
    mgr = RequestManager(
        "s", use_cache=False, cache_root=tmp_path, max_retries=6,
        retry_jitter_ratio=0.0, sleep_fn=lambda _s: None,
    )
    seq: list[str] = []

    def fake_strategy(url, strategy):
        seq.append(strategy)
        if strategy in (STEALTH, STEALTH_FRESH):
            return _CLEAN
        raise RuntimeError("Cloudflare challenge still present")

    monkeypatch.setattr(mgr, "_fetch_uncached_strategy", fake_strategy)
    assert mgr.fetch("https://freewebnovel.com/c/1", use_browser=True) == _CLEAN
    assert mgr._camoufox_exhausted is True
    seq.clear()
    # Second chapter: latched → stealth-first, no camoufox at all.
    assert mgr.fetch("https://freewebnovel.com/c/2", use_browser=True) == _CLEAN
    assert seq == [STEALTH]
    assert CAMO not in seq and CAMO_FRESH not in seq


def test_stealth_launch_failure_is_non_blocking(tmp_path, monkeypatch) -> None:
    """A missing/unlaunchable Chromium on the stealth fallback rung is an immediate
    strategy failure (no long backoff), the chapter is recorded failed, the run
    continues."""
    sleeps: list[float] = []

    def fake_strategy(url, strategy):
        if strategy == STEALTH:
            raise RuntimeError(
                "BrowserType.launch: Executable doesn't exist — playwright install"
            )
        raise RuntimeError("Cloudflare challenge still present")

    mgr = RequestManager(
        "s", use_cache=False, cache_root=tmp_path, max_retries=6,
        retry_jitter_ratio=0.0, sleep_fn=sleeps.append,
    )
    monkeypatch.setattr(mgr, "_fetch_uncached_strategy", fake_strategy)
    with pytest.raises(rm.FetchError):
        mgr.fetch("https://freewebnovel.com/c/1", use_browser=True)
    # Camoufox rungs backed off (5+15+45 = 65s total), but the stealth LAUNCH failure
    # added no sleep — so no 100-second freeze on a missing Chromium. The backoff is
    # now slept in cancel-aware slices (<=0.25s — BUG-2); assert the TOTAL + slice cap.
    assert all(s <= rm.BACKOFF_WAIT_SLICE for s in sleeps)
    assert sum(sleeps) == pytest.approx(65.0)


def test_stealth_launch_failure_run_continues(tmp_path, monkeypatch) -> None:
    """Pipeline level: stealth Chromium can't launch and camoufox can't clear — both
    chapters are recorded failed but the run does not abort."""
    fake = _FakeBrowsers(
        camoufox_clears=False,
        stealth_launch_raises=FileNotFoundError("ms-playwright chromium missing"),
    )
    fake.install(monkeypatch)
    mgr = RequestManager(
        "ss", use_cache=False, cache_root=tmp_path, max_retries=6,
        retry_jitter_ratio=0.0, sleep_fn=lambda _s: None,
    )
    adapter = _BrowserAdapter(mgr, count=2)
    report = pipeline.run_scrape(
        _job(tmp_path, count=2), adapter=adapter, log=lambda _m: None,
        sleep_fn=lambda _s: None,
    )
    assert not report.cancelled
    assert sorted(report.failed) == [1, 2]
    assert report.written == []
    assert fake.stealth_created == 0          # launch never succeeded


def test_sweep_can_use_stealth_fallback(tmp_path, monkeypatch) -> None:
    """The end-of-run sweep can rescue a chapter via the camoufox→stealth-Chromium
    fallback: camoufox never clears, stealth clears only on the sweep attempt."""
    # stealth fails on its first two hits (the main pass), clears on the 3rd (sweep).
    fake = _FakeBrowsers(
        camoufox_clears=False,
        stealth_clears=lambda hit: hit >= 3,
    )
    fake.install(monkeypatch)
    mgr = RequestManager(
        "ss", use_cache=False, cache_root=tmp_path, max_retries=6,
        retry_jitter_ratio=0.0, sleep_fn=lambda _s: None,
    )
    adapter = _BrowserAdapter(mgr, count=1)
    report = pipeline.run_scrape(
        _job(tmp_path, count=1), adapter=adapter, log=lambda _m: None,
        sleep_fn=lambda _s: None,
    )
    # Rescued on the sweep via the stealth fallback; one PDF written; browser reused.
    assert report.rescued == [1]
    assert report.failed == []
    assert len(report.written) == 1
    assert fake.stealth_created == 1          # the one persistent stealth browser
    assert len(fake.stealth_nav) >= 3         # main-pass tries + the sweep rescue


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
