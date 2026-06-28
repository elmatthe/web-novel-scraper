"""Tests for the playwright-stealth rescue rungs + the strengthened end-of-run
sweep (live-tuned after the first genuine FreeWebNovel Cloudflare challenge, which
camoufox alone failed to clear).

All offline/deterministic: the camoufox + Chromium-stealth browser calls are
mocked at the ``cf_bypass`` seam or by monkeypatching ``_fetch_uncached_strategy``
— no real browser is ever launched and no network is touched.
"""

from __future__ import annotations

import pytest

from webnovel_scraper import cf_bypass, pipeline
from webnovel_scraper import request_manager as rm
from webnovel_scraper.models import (
    ChapterContent,
    ChapterMeta,
    OutputMode,
    ScrapeJob,
)
from webnovel_scraper.request_manager import RequestManager

HTTP = rm.FETCH_STRATEGY_HTTP
CS = rm.FETCH_STRATEGY_CLOUDSCRAPER
CAMO = rm.FETCH_STRATEGY_CAMOUFOX
CAMO_FRESH = rm.FETCH_STRATEGY_CAMOUFOX_FRESH
STEALTH = rm.FETCH_STRATEGY_PLAYWRIGHT_STEALTH
STEALTH_FRESH = rm.FETCH_STRATEGY_PLAYWRIGHT_STEALTH_FRESH


# ── Ladder wiring ────────────────────────────────────────────────────────────
def test_ladder_includes_stealth_rungs_after_camoufox() -> None:
    assert rm.DEFAULT_ESCALATION_LADDER == (
        HTTP, CS, CAMO, CAMO_FRESH, STEALTH, STEALTH_FRESH,
    )
    # The two stealth rungs are the LAST resort, after camoufox is exhausted.
    assert rm.DEFAULT_ESCALATION_LADDER[-2:] == (STEALTH, STEALTH_FRESH)
    assert rm.DEFAULT_ESCALATION_LADDER.index(CAMO_FRESH) < \
        rm.DEFAULT_ESCALATION_LADDER.index(STEALTH)
    # Browser-mode ladder also ends with the stealth rescue rungs.
    assert rm.BROWSER_ESCALATION_LADDER == (CAMO, CAMO_FRESH, STEALTH, STEALTH_FRESH)


def test_chapter_advances_from_camoufox_fresh_to_stealth(tmp_path, monkeypatch) -> None:
    """A chapter that fails through camoufox_fresh advances to playwright_stealth
    and is cleared there (the rescue camoufox-only never had)."""
    recorded: list[str] = []

    def fake_strategy(url, strategy):
        recorded.append(strategy)
        if strategy == STEALTH:
            return "<html>cleared by stealth</html>"
        raise RuntimeError("Cloudflare challenge still active")

    mgr = RequestManager(
        "s", use_cache=False, cache_root=tmp_path, max_retries=5,
        retry_jitter_ratio=0.0, sleep_fn=lambda _s: None,
    )
    monkeypatch.setattr(mgr, "_fetch_uncached_strategy", fake_strategy)

    assert mgr.fetch("https://example.com/ch") == "<html>cleared by stealth</html>"
    # Walked every rung up to and including the first stealth rung.
    assert recorded == [HTTP, CS, CAMO, CAMO_FRESH, STEALTH]


# ── One-engine-per-thread teardown (camoufox + Chromium can't coexist) ─────────
def test_camoufox_start_tears_down_live_chromium(tmp_path, monkeypatch) -> None:
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)
    stopped = {"pw": 0}

    class _PW:
        def stop(self) -> None:
            stopped["pw"] += 1

    class _Closeable:
        def close(self) -> None:
            pass

    # Simulate a live Chromium stealth engine.
    mgr._pw = _PW()
    mgr._page = _Closeable()
    mgr._context = _Closeable()
    mgr._browser = _Closeable()

    built = {"n": 0}

    class _CFCM:
        def __exit__(self, *exc) -> None:
            pass

    def fake_create_camoufox_browser(*, headless):
        built["n"] += 1
        return _CFCM(), object()

    monkeypatch.setattr(cf_bypass, "create_camoufox_browser", fake_create_camoufox_browser)
    monkeypatch.setattr(cf_bypass, "fetch_camoufox", lambda page, url, **k: "<html>ok</html>")

    assert mgr._fetch_camoufox_once("https://x/1", fresh_context=False) == "<html>ok</html>"
    assert stopped["pw"] == 1            # the Chromium sync-Playwright driver was stopped
    assert mgr._pw is None and mgr._page is None
    assert built["n"] == 1               # camoufox started after the teardown


def test_chromium_start_tears_down_live_camoufox(tmp_path, monkeypatch) -> None:
    import playwright.sync_api as pw_sync

    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)
    exited = {"n": 0}

    class _CFCM:
        def __exit__(self, *exc) -> None:
            exited["n"] += 1

    # Simulate a live camoufox engine.
    mgr._cf_cm = _CFCM()
    mgr._cf_page = object()

    class _PW:
        def stop(self) -> None:
            pass

    class _SyncPW:
        def start(self):
            return _PW()

    class _Closeable:
        def close(self) -> None:
            pass

    class _Ctx:
        def new_page(self):
            return _Closeable()

        def close(self) -> None:
            pass

    monkeypatch.setattr(pw_sync, "sync_playwright", lambda: _SyncPW())
    monkeypatch.setattr(
        cf_bypass, "create_stealth_browser",
        lambda pw, *, headless: (_Closeable(), _Ctx()),
    )
    monkeypatch.setattr(cf_bypass, "fetch_with_stealth", lambda page, url, **k: "<html>ok</html>")

    assert mgr._fetch_browser_once("https://x/1", fresh_context=False) == "<html>ok</html>"
    assert exited["n"] == 1              # camoufox context torn down before Chromium started
    assert mgr._cf_cm is None and mgr._cf_page is None


# ── End-to-end: the end-of-run sweep re-walks the FULL ladder and a stealth rung
#    rescues a chapter camoufox could not clear ────────────────────────────────
class _RealRmAdapter:
    """A minimal adapter that drives a REAL RequestManager (so the run exercises
    the actual escalation ladder) and returns a trivial chapter body on success."""

    def __init__(self, manager: RequestManager) -> None:
        self._rm = manager
        self.warnings: list[str] = []

    def build_chapter_index(self, spec):
        return [ChapterMeta(index=1, url="https://example.com/ch1")]

    def fetch_chapter(self, meta, spec):
        self._rm.fetch(meta.url)  # walks the full ladder; raises FetchError if all fail
        return ChapterContent(
            index=meta.index, title="T",
            paragraphs=["A body paragraph long enough to render in the PDF."],
        )


def _stealth_clears_on_sweep_strategy(recorded: list[str]):
    """Fake strategy fn: every rung fails on the main pass (stealth is hit twice,
    hits 1 & 2), and stealth only clears on its 3rd hit — i.e. on the sweep's
    re-walk. Proves the sweep re-walks the full ladder and is rescued by stealth."""
    stealth_hits = {"n": 0}

    def fake_strategy(url, strategy):
        recorded.append(strategy)
        if strategy in (STEALTH, STEALTH_FRESH):
            stealth_hits["n"] += 1
            if stealth_hits["n"] >= 3:
                return "<html>cleared on the sweep</html>"
        raise RuntimeError("Cloudflare challenge still active")

    return fake_strategy


def _real_rm(tmp_path, monkeypatch, recorded):
    mgr = RequestManager(
        "ss", use_cache=False, cache_root=tmp_path, max_retries=5,
        retry_jitter_ratio=0.0, sleep_fn=lambda _s: None,
    )
    monkeypatch.setattr(
        mgr, "_fetch_uncached_strategy", _stealth_clears_on_sweep_strategy(recorded)
    )
    return mgr


def _job(tmp_path, mode: OutputMode) -> ScrapeJob:
    return ScrapeJob(
        novel_slug="shadow-slave",
        adapter_key="freewebnovel",
        start=1,
        end=1,
        delay=0.0,
        output_mode=mode,
        use_cache=False,
        output_dir=tmp_path,
    )


@pytest.mark.parametrize("mode", [OutputMode.SEPARATE, OutputMode.CHUNKED, OutputMode.SINGLE])
def test_sweep_reaches_stealth_and_rescues_in_all_modes(tmp_path, monkeypatch, mode) -> None:
    recorded: list[str] = []
    adapter = _RealRmAdapter(_real_rm(tmp_path, monkeypatch, recorded))
    report = pipeline.run_scrape(
        _job(tmp_path, mode), adapter=adapter, log=lambda m: None,
        sleep_fn=lambda _s: None,
    )

    # Cleared only on the end-of-run sweep, via a stealth rung.
    assert report.rescued == [1]
    assert report.failed == []
    assert len(report.written) == 1
    assert len(list(tmp_path.glob("*.pdf"))) == 1
    # The main pass reached both stealth rungs (2 hits) and the sweep reached a
    # stealth rung again (3rd hit) — proving the sweep re-walks the FULL ladder.
    assert recorded.count(STEALTH) + recorded.count(STEALTH_FRESH) >= 3
    assert STEALTH in recorded


def test_chapter_failing_every_rung_stays_failed_and_in_summary(tmp_path, monkeypatch) -> None:
    recorded: list[str] = []

    def always_fail(url, strategy):
        recorded.append(strategy)
        raise RuntimeError("Cloudflare challenge still active")

    mgr = RequestManager(
        "ss", use_cache=False, cache_root=tmp_path, max_retries=5,
        retry_jitter_ratio=0.0, sleep_fn=lambda _s: None,
    )
    monkeypatch.setattr(mgr, "_fetch_uncached_strategy", always_fail)
    adapter = _RealRmAdapter(mgr)

    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.SEPARATE), adapter=adapter, log=lambda m: None,
        sleep_fn=lambda _s: None,
    )

    assert report.rescued == []
    assert report.failed == [1]
    assert report.permanent_failed == []      # a CF block is not permanent
    assert report.extraction_failed == []     # nor an extraction failure
    # The chapter is clearly surfaced for the user to re-run.
    summary = report.summary()
    assert "failed chapters: 1" in summary
    assert "re-run with the SAME output folder" in summary
    # Both the main pass and the sweep walked the full ladder, reaching stealth.
    assert STEALTH in recorded and STEALTH_FRESH in recorded
