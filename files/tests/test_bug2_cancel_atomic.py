"""0.2.0 live-test BUG-2 fixes — cancel-aware legacy backoff, warm-up
ScrapeCancelled re-raise, and atomic PDF writes.

All offline + deterministic: a fake clock for logical waits, test-only fakes for
the browser/engine seams, and monkeypatched failure injection for the atomic-write
path. No live network, no real browser, and no test builds more than one rescue
worker. These lock in:

  * the legacy inter-attempt backoff is sliced (<=0.25s) and aborts within one
    slice on cancel_event — with a real-clock regression guard that HANGS if the
    backoff ever reverts to a monolithic sleep;
  * _warm_camoufox_session re-raises ScrapeCancelled instead of swallowing it in
    its broad except (and the main ladder's re-raise is unchanged);
  * pdf_builder.create_pdf publishes atomically (temp-then-os.replace), leaving no
    corrupt final file and no .part artifact on a mid-write failure — across
    SEPARATE / CHUNKED / SINGLE and on the FWN rescue path.
"""

from __future__ import annotations

import threading
import time

import pytest

from webnovel_scraper import cf_bypass, pdf_builder, pipeline
from webnovel_scraper import request_manager as rm
from webnovel_scraper.models import (
    ChapterContent,
    ChapterMeta,
    OutputMode,
    ScrapeJob,
)
from webnovel_scraper.request_manager import (
    ChallengeFetchError,
    FetchInfo,
    RequestManager,
    ScrapeCancelled,
)

WND_URL = "https://dynamic.webnovel.com/story/28684090500376805"


# ════════════════════════════════════════════════════════════════════════════
# 1. Cancel-aware legacy inter-attempt backoff (BUG-2 core)
# ════════════════════════════════════════════════════════════════════════════
def test_legacy_backoff_is_sliced_and_cancel_aborts_within_one_slice(tmp_path) -> None:
    """A Stop pressed during a long (120s) backoff takes effect within ONE 0.25s
    slice — driven entirely by a fake clock, so the suite never waits."""
    sleeps: list[float] = []
    ev = threading.Event()
    state = {"slices": 0}

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        state["slices"] += 1
        if state["slices"] == 1:  # Stop pressed during the very first slice
            ev.set()

    mgr = RequestManager(
        "the-noble-queen", use_cache=False, cache_root=tmp_path,
        max_retries=6, retry_base_delay=120.0, retry_jitter_ratio=0.0,
        sleep_fn=fake_sleep,
    )
    mgr.cancel_event = ev
    mgr._fetch_uncached_strategy = lambda url, strategy: (
        _ for _ in ()
    ).throw(ChallengeFetchError(f"blocked by {strategy}"))

    with pytest.raises(ScrapeCancelled):
        mgr.fetch(WND_URL, use_browser=False)

    # Broke out after exactly one slice; every slice was <= the slice cap; the full
    # 120s was never elapsed.
    assert sleeps == [pytest.approx(rm.BACKOFF_WAIT_SLICE)]
    assert all(s <= rm.BACKOFF_WAIT_SLICE for s in sleeps)
    assert sum(sleeps) < 1.0


def test_legacy_backoff_real_clock_regression_guard(tmp_path) -> None:
    """Regression guard: a sleep_fn that REALLY sleeps each slice it is handed.
    With the sliced cancel-aware backoff this returns in ~one 0.25s slice once
    cancel is set; if the backoff regressed to a monolithic real-clock sleep of the
    full 120s delay this test would HANG (the verify watchdog would kill it) — the
    same hang-on-regression discipline as the Phase-1 limiter guard."""
    ev = threading.Event()
    first = {"done": False}

    def real_slice_sleep(seconds: float) -> None:
        time.sleep(seconds)          # genuinely sleep this slice
        if not first["done"]:        # Stop pressed during the first slice
            first["done"] = True
            ev.set()

    mgr = RequestManager(
        "the-noble-queen", use_cache=False, cache_root=tmp_path,
        max_retries=6, retry_base_delay=120.0, retry_jitter_ratio=0.0,
        sleep_fn=real_slice_sleep,
    )
    mgr.cancel_event = ev
    mgr._fetch_uncached_strategy = lambda url, strategy: (
        _ for _ in ()
    ).throw(ChallengeFetchError("blocked"))

    t0 = time.monotonic()
    with pytest.raises(ScrapeCancelled):
        mgr.fetch(WND_URL, use_browser=False)
    elapsed = time.monotonic() - t0
    # Nowhere near the 120s a monolithic sleep would block for.
    assert elapsed < 5.0


def test_legacy_backoff_full_wait_preserved_when_not_cancelled(tmp_path) -> None:
    """Without a cancel the sliced backoff still waits the FULL delay (the slices
    sum to the computed backoff) — slicing changes interruptibility, not duration."""
    sleeps: list[float] = []
    calls = {"n": 0}

    def strategy(url, strat):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ChallengeFetchError("blocked once")
        return "<html><body><div class='cha-content'><p>" + ("real prose " * 20) + "</p></div></body></html>"

    mgr = RequestManager(
        "the-noble-queen", use_cache=False, cache_root=tmp_path,
        max_retries=6, retry_base_delay=5.0, retry_jitter_ratio=0.0,
        sleep_fn=sleeps.append,
    )
    mgr._fetch_uncached_strategy = strategy
    mgr.fetch(WND_URL, use_browser=False)

    # First backoff is 5.0s; 5.0 / 0.25 = 20 slices, each <= the cap, summing to 5.0.
    assert all(s <= rm.BACKOFF_WAIT_SLICE for s in sleeps)
    assert sum(sleeps) == pytest.approx(5.0)
    assert len(sleeps) == 20


# ════════════════════════════════════════════════════════════════════════════
# 2. Warm-up ScrapeCancelled re-raise (+ main ladder re-raise unchanged)
# ════════════════════════════════════════════════════════════════════════════
def test_warm_camoufox_session_reraises_scrape_cancelled(tmp_path, monkeypatch) -> None:
    """A ScrapeCancelled raised inside the warm-up (e.g. the shared limiter aborting
    the warm-up nav) must propagate, NOT be swallowed by the broad except."""
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)

    def boom(page, url, **_k):
        raise ScrapeCancelled("cancelled during warm-up navigation")

    monkeypatch.setattr(cf_bypass, "fetch_camoufox", boom)
    with pytest.raises(ScrapeCancelled):
        mgr._warm_camoufox_session(object(), "https://freewebnovel.com/novel/x/c-1")
    # A non-cancel warm-up failure is still swallowed (best-effort) and marks warmed.
    monkeypatch.setattr(
        cf_bypass, "fetch_camoufox",
        lambda page, url, **_k: (_ for _ in ()).throw(RuntimeError("transient")),
    )
    mgr._warm_camoufox_session(object(), "https://freewebnovel.com/novel/x/c-1")
    assert "freewebnovel.com" in mgr._cf_warmed_hosts


def test_main_ladder_reraises_scrape_cancelled_unchanged(tmp_path) -> None:
    """The ladder's existing ScrapeCancelled re-raise is intact: a strategy raising
    ScrapeCancelled propagates as-is (never wrapped into a generic FetchError)."""
    mgr = RequestManager(
        "s", use_cache=False, cache_root=tmp_path, sleep_fn=lambda _s: None,
    )
    mgr._fetch_uncached_strategy = lambda url, strategy: (
        _ for _ in ()
    ).throw(ScrapeCancelled("cancelled mid-attempt"))
    with pytest.raises(ScrapeCancelled):
        mgr.fetch(WND_URL, use_browser=False)


# ════════════════════════════════════════════════════════════════════════════
# 3. Atomic PDF writes (BUG-2 partial-cleanup; cross-cutting FWN + WND)
# ════════════════════════════════════════════════════════════════════════════
def _chapter(i: int) -> ChapterContent:
    return ChapterContent(
        index=i, title=f"Title {i}",
        paragraphs=[f"A real body paragraph for chapter {i}, long enough to render."],
    )


def test_create_pdf_success_produces_valid_final_and_no_part(tmp_path) -> None:
    from pypdf import PdfReader

    out = tmp_path / "Chapter 1_ Title 1..pdf"
    pdf_builder.create_pdf([_chapter(1)], out, title="Novel")

    assert out.is_file() and out.stat().st_size > 0
    assert len(PdfReader(str(out)).pages) >= 1
    assert list(tmp_path.glob("*.part")) == []   # nothing left staged


def test_create_pdf_failure_at_replace_leaves_no_corrupt_final(tmp_path, monkeypatch) -> None:
    """Simulate a crash BETWEEN the temp build and the atomic publish: os.replace
    raises. The final file must not exist and no .part artifact may remain."""
    out = tmp_path / "Chapter 1_ Title 1..pdf"

    def boom(*_a, **_k):
        raise RuntimeError("simulated crash between temp-write and publish")

    monkeypatch.setattr(pdf_builder.os, "replace", boom)
    with pytest.raises(RuntimeError):
        pdf_builder.create_pdf([_chapter(1)], out, title="Novel")

    assert not out.exists()                       # no partial/corrupt final
    assert list(tmp_path.glob("*.part")) == []    # temp cleaned up


def test_create_pdf_failure_does_not_clobber_existing_final(tmp_path, monkeypatch) -> None:
    """If a good final already exists (e.g. a prior run), a failed re-write leaves
    the ORIGINAL intact — the atomic publish never half-overwrites it."""
    out = tmp_path / "Chapter 1_ Title 1..pdf"
    pdf_builder.create_pdf([_chapter(1)], out, title="Novel")
    original = out.read_bytes()

    monkeypatch.setattr(
        pdf_builder.os, "replace",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError):
        pdf_builder.create_pdf([_chapter(1)], out, title="Novel")

    assert out.read_bytes() == original           # untouched
    assert list(tmp_path.glob("*.part")) == []


# ── all three output modes still produce final PDFs, no .part leftovers ───────
class _LegacyFakeAdapter:
    """An injected (non-FWN) adapter so run_scrape takes the legacy _drive path."""

    def __init__(self, count: int) -> None:
        self.count = count
        self.warnings: list[str] = []

    def build_chapter_index(self, spec):
        return [
            ChapterMeta(index=n, url=f"https://dynamic.webnovel.com/story/x/{n}",
                        title=f"Title {n}")
            for n in range(1, self.count + 1)
        ]

    def fetch_chapter(self, meta, spec):
        return _chapter(meta.index)


def _wnd_job(tmp_path, mode: OutputMode) -> ScrapeJob:
    return ScrapeJob(
        novel_slug="the-noble-queen", adapter_key="webnovel_dynamic",
        start=1, end=4, delay=0.0, output_mode=mode, use_cache=False,
        output_dir=tmp_path, chunk_size=2, use_browser=False,
    )


@pytest.mark.parametrize("mode", [OutputMode.SEPARATE, OutputMode.CHUNKED, OutputMode.SINGLE])
def test_all_output_modes_write_finals_with_no_part_leftovers(tmp_path, mode) -> None:
    report = pipeline.run_scrape(
        _wnd_job(tmp_path, mode), adapter=_LegacyFakeAdapter(4),
        log=lambda _m: None, sleep_fn=lambda _s: None,
    )
    assert report.failed == []
    assert len(report.written) >= 1
    pdfs = list(tmp_path.glob("*.pdf"))
    assert pdfs, "expected at least one final PDF"
    assert all(p.stat().st_size > 0 for p in pdfs)
    assert list(tmp_path.glob("*.part")) == []   # nothing left staged in any mode


# ════════════════════════════════════════════════════════════════════════════
# 4. FWN-path regression: healthy run still writes all chapters, scope gate intact
# ════════════════════════════════════════════════════════════════════════════
class _FakeFwnManager:
    def __init__(self, headless: bool) -> None:
        self.headless = headless
        self.last_fetch_info = None

    def close(self) -> None:
        pass


class _FakeFwnAdapter:
    """Healthy FWN primary: every chapter clears on the fast primary, so the rescue
    pool is never built (mirrors the live Shadow Slave pass)."""

    def __init__(self, manager, count: int) -> None:
        self.manager = manager
        self.count = count
        self.warnings: list[str] = []

    def build_chapter_index(self, spec, *, fast_path=False):
        return [
            ChapterMeta(index=n, url=f"https://freewebnovel.com/novel/shadow-slave/chapter-{n}")
            for n in range(1, self.count + 1)
        ]

    def fetch_chapter(self, meta, spec, *, fast_path=False):
        self.manager.last_fetch_info = FetchInfo(
            from_cache=False, classification="success", strategy="camoufox"
        )
        return _chapter(meta.index)


def test_fwn_healthy_run_writes_all_no_part_and_scope_gate_intact(tmp_path) -> None:
    job = ScrapeJob(
        novel_slug="shadow-slave", adapter_key="freewebnovel",
        start=1, end=5, delay=0.0, output_mode=OutputMode.SEPARATE,
        use_cache=False, output_dir=tmp_path, use_browser=True, headless=False,
    )
    # Scope gate unchanged: this IS a conductor run; WND/HTTP is not.
    assert pipeline._rescue_enabled(job) is True

    def manager_factory(*, headless):
        return _FakeFwnManager(headless)

    def adapter_factory(manager):
        return _FakeFwnAdapter(manager, count=5)

    def pool_factory():
        raise AssertionError("a healthy FWN run must never build a rescue pool")

    report = pipeline.run_scrape(
        job, log=lambda _m: None, sleep_fn=lambda _s: None,
        request_manager_factory=manager_factory,
        primary_adapter_factory=adapter_factory,
        rescue_pool_factory=pool_factory,
    )

    assert report.cancelled is False
    assert report.failed == []
    assert report.rescued == []
    assert len(report.written) == 5
    assert len(list(tmp_path.glob("*.pdf"))) == 5
    assert list(tmp_path.glob("*.part")) == []   # atomic write left nothing staged
