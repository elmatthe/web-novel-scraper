"""Phase 2 (0.2.0) — the single-lane hard-chapter RescuePool.

All offline + deterministic: a fake heavy-fetch function, fake manager/adapter
factories (no real browser ever launches), an injected fake ``monotonic`` + fake
``sleep`` for every logical wait, and short real timeouts only for thread
coordination. These lock in the rescue worker's lifecycle, escalation, deadline,
backpressure, one-terminal-result, worker-crash, and cancellation guarantees —
without ever building or testing more than one worker (invariant #1).
"""

from __future__ import annotations

import threading
import time

import pytest

from webnovel_scraper import rescue_pool as rp
from webnovel_scraper import request_manager as rm
from webnovel_scraper.models import ChapterContent, ChapterMeta
from webnovel_scraper.request_manager import (
    ChallengeFetchError,
    NotFoundFetchError,
    ScrapeCancelled,
)
from webnovel_scraper.rescue_pool import (
    CANCELLED,
    EXTRACTION_FAILED,
    HEADFUL_CAMOUFOX,
    HEADFUL_CHROMIUM,
    HEADLESS_CAMOUFOX,
    NOT_FOUND,
    POOL_FAILED,
    RESCUED,
    RESCUE_EXHAUSTED,
    RescuePool,
    RescueStep,
)

MAIN_IDENT = threading.get_ident()


# ── deterministic helpers / fakes ─────────────────────────────────────────────
class _Clock:
    """A shared mutable monotonic clock advanced only by the injected ``sleep``."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class _FakeManager:
    def __init__(self, headless: bool) -> None:
        self.headless = headless
        self.create_ident = threading.get_ident()
        self.close_ident: int | None = None
        # attributes the default fetch would set (harmless for the fakes)
        self._browser_nav_timeout_ms = 0
        self._cloudflare_timeout = 0.0

    def close(self) -> None:
        self.close_ident = threading.get_ident()


class _FakeAdapter:
    def __init__(self, manager: _FakeManager) -> None:
        self.manager = manager
        self.create_ident = threading.get_ident()


def _meta(i: int) -> ChapterMeta:
    return ChapterMeta(index=i, url=f"https://fwn/novel/x/chapter-{i}")


def _content(i: int) -> ChapterContent:
    return ChapterContent(index=i, title=f"Title {i}", paragraphs=[f"Body of chapter {i}. " * 5])


def _make_pool(*, fetch_fn, primary_headless: bool = True, clock: _Clock | None = None, **kw):
    """Build a pool with fake factories (no real browser) + an injected fetch."""
    kw.setdefault("manager_factory", lambda *, headless: _FakeManager(headless))
    kw.setdefault("adapter_factory", lambda m: _FakeAdapter(m))
    if clock is not None:
        kw.setdefault("monotonic", clock.now)
        kw.setdefault("sleep", clock.sleep)
    return RescuePool(
        primary_headless=primary_headless,
        fetch_fn=fetch_fn,
        log=lambda _m: None,
        **kw,
    )


def _drain_to_terminal(pool: RescuePool, *, graceful: bool = True, timeout: float = 5.0):
    """Finish (or cancel) the pool, join, and return all terminal results."""
    if graceful:
        pool.finish()
    else:
        pool.cancel()
    pool.join(timeout)
    assert pool._thread is None or not pool._thread.is_alive(), "worker did not exit"
    return pool.poll_results()


# ── single-lane invariant #1 ──────────────────────────────────────────────────
def test_single_lane_constants_and_pool_rejects_multi_worker() -> None:
    assert rm.RESCUE_WORKERS == 1
    assert rm.RESCUE_MAX_WORKERS == 1
    assert rm.RESCUE_MAX_PENDING == 16
    assert rm.RESCUE_MAX_ELAPSED_PER_CHAPTER == 180.0
    with pytest.raises(ValueError):
        RescuePool(primary_headless=True, fetch_fn=lambda *a, **k: None, workers=2)


def test_rescue_ladder_shape_and_first_headful_is_fresh() -> None:
    assert rp.RESCUE_LADDER == (
        RescueStep(mode=HEADLESS_CAMOUFOX, fresh=False, attempts=2),
        RescueStep(mode=HEADLESS_CAMOUFOX, fresh=True, attempts=1),
        RescueStep(mode=HEADFUL_CAMOUFOX, fresh=True, attempts=2),
        RescueStep(mode=HEADFUL_CHROMIUM, fresh=True, attempts=2),
    )
    first_headful = next(s for s in rp.RESCUE_LADDER if s.mode == HEADFUL_CAMOUFOX)
    assert first_headful.fresh is True


# ── one worker processes a backlog ────────────────────────────────────────────
def test_single_worker_processes_a_backlog() -> None:
    def fetch_fn(meta, **kw):
        return _content(meta.index)

    pool = _make_pool(fetch_fn=fetch_fn).start()
    for i in range(1, 6):
        assert pool.submit(_meta(i)) is True

    results = _drain_to_terminal(pool)
    assert len(results) == 5
    assert {r.meta.index for r in results} == {1, 2, 3, 4, 5}
    assert all(r.status == RESCUED for r in results)
    assert all(r.content is not None for r in results)
    assert pool.jobs_submitted == 5
    assert pool.jobs_completed == 5


# ── clears on a later ladder step vs never clears ─────────────────────────────
def test_clears_on_a_later_step_is_rescued() -> None:
    clock = _Clock()

    def fetch_fn(meta, *, mode, **kw):
        if mode != HEADFUL_CHROMIUM:
            raise ChallengeFetchError("still blocked")
        return _content(meta.index)

    pool = _make_pool(fetch_fn=fetch_fn, primary_headless=True, clock=clock).start()
    pool.submit(_meta(7))
    (result,) = _drain_to_terminal(pool)
    assert result.status == RESCUED
    assert result.strategy == HEADFUL_CHROMIUM
    assert result.content is not None


def test_never_clears_is_exhausted_within_full_ladder() -> None:
    clock = _Clock()  # fetch does not advance the clock → deadline never hit

    def fetch_fn(meta, **kw):
        raise ChallengeFetchError("still blocked")

    pool = _make_pool(fetch_fn=fetch_fn, primary_headless=True, clock=clock).start()
    pool.submit(_meta(9))
    (result,) = _drain_to_terminal(pool)
    assert result.status == RESCUE_EXHAUSTED
    # The full headless-start ladder ran every attempt: 2 + 1 + 2 + 2 = 7.
    assert result.attempts == 7
    assert result.content is None


# ── monotonic escalation + initial-mode-follows-primary ───────────────────────
def test_monotonic_escalation_latched_headful_never_returns_to_headless() -> None:
    calls: list[tuple[int, str, bool]] = []

    def fetch_fn(meta, *, mode, fresh, **kw):
        calls.append((meta.index, mode, fresh))
        if mode == HEADFUL_CHROMIUM:
            return _content(meta.index)
        raise ChallengeFetchError("still blocked")

    pool = _make_pool(fetch_fn=fetch_fn, primary_headless=True).start()
    pool.submit(_meta(1))
    pool.submit(_meta(2))
    results = {r.meta.index: r for r in _drain_to_terminal(pool)}

    assert results[1].status == RESCUED and results[1].strategy == HEADFUL_CHROMIUM
    assert results[2].status == RESCUED and results[2].strategy == HEADFUL_CHROMIUM

    ch1_modes = [m for (idx, m, _f) in calls if idx == 1]
    ch2_modes = [m for (idx, m, _f) in calls if idx == 2]
    # Chapter 1 walks the full ladder; chapter 2 is latched at HEADFUL_CHROMIUM and
    # NEVER returns to a headless step.
    assert HEADLESS_CAMOUFOX in ch1_modes
    assert ch2_modes == [HEADFUL_CHROMIUM]
    assert HEADLESS_CAMOUFOX not in ch2_modes
    # The first HEADFUL_CAMOUFOX attempt for chapter 1 is fresh=True.
    first_headful = next((idx, m, f) for (idx, m, f) in calls if idx == 1 and m == HEADFUL_CAMOUFOX)
    assert first_headful[2] is True


def test_initial_mode_follows_visible_primary_skips_headless_steps() -> None:
    modes: list[str] = []

    def fetch_fn(meta, *, mode, **kw):
        modes.append(mode)
        raise ChallengeFetchError("still blocked")  # force the whole ladder

    pool = _make_pool(fetch_fn=fetch_fn, primary_headless=False).start()
    pool.submit(_meta(3))
    (result,) = _drain_to_terminal(pool)

    assert result.status == RESCUE_EXHAUSTED
    # A visible-primary run starts at HEADFUL_CAMOUFOX: no headless step is ever run,
    # and rescue is never weaker than the primary.
    assert HEADLESS_CAMOUFOX not in modes
    assert modes == [HEADFUL_CAMOUFOX, HEADFUL_CAMOUFOX, HEADFUL_CHROMIUM, HEADFUL_CHROMIUM]


# ── no double-submit ──────────────────────────────────────────────────────────
def test_no_double_submit_by_index_or_url() -> None:
    def fetch_fn(meta, **kw):
        return _content(meta.index)

    pool = _make_pool(fetch_fn=fetch_fn).start()
    assert pool.submit(_meta(1)) is True
    assert pool.submit(_meta(1)) is False               # same index/URL ignored
    assert pool.submit(ChapterMeta(index=99, url=_meta(1).url)) is False  # same URL
    assert pool.submit(_meta(2)) is True

    results = _drain_to_terminal(pool)
    assert {r.meta.index for r in results} == {1, 2}
    assert pool.jobs_submitted == 2


# ── same-thread ownership; rescue adapter is a distinct instance ──────────────
def test_same_thread_ownership_and_distinct_adapter() -> None:
    primary_adapter = _FakeAdapter(_FakeManager(headless=True))
    made = {}
    fetch_ident = {}

    def manager_factory(*, headless):
        m = _FakeManager(headless)
        made["manager"] = m
        return m

    def adapter_factory(manager):
        a = _FakeAdapter(manager)
        made["adapter"] = a
        return a

    def fetch_fn(meta, *, adapter, **kw):
        fetch_ident["fetch"] = threading.get_ident()
        fetch_ident["adapter_is_primary"] = adapter is primary_adapter
        return _content(meta.index)

    pool = RescuePool(
        primary_headless=True,
        manager_factory=manager_factory,
        adapter_factory=adapter_factory,
        fetch_fn=fetch_fn,
        log=lambda _m: None,
    ).start()
    pool.submit(_meta(1))
    results = _drain_to_terminal(pool)

    assert results[0].status == RESCUED
    worker_ident = pool.worker_thread_ident
    assert worker_ident is not None and worker_ident != MAIN_IDENT
    # manager-create / adapter-create / fetch / teardown all on the one worker thread.
    assert made["manager"].create_ident == worker_ident
    assert made["adapter"].create_ident == worker_ident
    assert fetch_ident["fetch"] == worker_ident
    assert made["manager"].close_ident == worker_ident          # closed in finally
    # The rescue adapter is a DISTINCT instance from the primary's.
    assert made["adapter"] is not primary_adapter
    assert fetch_ident["adapter_is_primary"] is False


# ── deadline bounds total processing + refuses a too-short attempt ────────────
def test_deadline_bounds_total_processing_and_refuses_short_attempt() -> None:
    clock = _Clock()

    def fetch_fn(meta, *, budget, **kw):
        clock.t += budget                 # each attempt consumes its whole budget
        raise ChallengeFetchError("still blocked")

    pool = _make_pool(
        fetch_fn=fetch_fn,
        primary_headless=True,
        clock=clock,
        attempt_timeout=100.0,            # large, so budget is gated by `remaining`
        deadline_per_chapter=180.0,
        min_attempt_budget=1.0,
    ).start()
    pool.submit(_meta(11))
    (result,) = _drain_to_terminal(pool)

    assert result.status == RESCUE_EXHAUSTED
    # attempt 1 budget=min(100,180)=100 → t=100; attempt 2 budget=min(100,80)=80 →
    # t=180; attempt 3 refused (0s left < 1.0s). Two attempts ran, processing == 180.
    assert result.attempts == 2
    assert clock.t == 180.0
    assert clock.t <= 180.0


# ── every submitted job yields exactly one terminal result ────────────────────
def test_every_submitted_job_yields_exactly_one_result() -> None:
    def fetch_fn(meta, **kw):
        return _content(meta.index)

    pool = _make_pool(fetch_fn=fetch_fn).start()
    n = 8
    for i in range(1, n + 1):
        pool.submit(_meta(i))
    results = _drain_to_terminal(pool)
    assert len(results) == n
    assert sorted(r.meta.index for r in results) == list(range(1, n + 1))
    assert pool.jobs_completed == n


def test_queued_then_cancelled_jobs_each_emit_a_cancelled_result() -> None:
    cancel_event = threading.Event()
    release = threading.Event()

    def fetch_fn(meta, **kw):
        # Block the worker on the first job (cancel-aware) so the rest stay QUEUED.
        while not release.is_set():
            if cancel_event.is_set():
                raise ScrapeCancelled("stop")
            time.sleep(0.005)
        return _content(meta.index)

    pool = _make_pool(fetch_fn=fetch_fn, cancel_event=cancel_event).start()
    for i in range(1, 5):
        assert pool.submit(_meta(i)) is True
    # Wait until the worker has pulled the first job and is blocked in fetch_fn,
    # leaving 3 still queued.
    deadline = time.monotonic() + 2.0
    while pool.pending > 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    results = _drain_to_terminal(pool, graceful=False)   # cancel
    assert len(results) == 4                              # every accepted job terminalized
    assert all(r.status == CANCELLED for r in results)
    assert {r.meta.index for r in results} == {1, 2, 3, 4}
    # A cancelled run must not leave submissions accepted afterwards.
    assert pool.submit(_meta(99)) is False


# ── worker-init failure → clean pool-level failure ────────────────────────────
def test_worker_init_failure_is_a_clean_pool_level_failure() -> None:
    def boom(*, headless):
        raise RuntimeError("camoufox engine not installed")

    pool = RescuePool(
        primary_headless=True,
        manager_factory=boom,
        adapter_factory=lambda m: _FakeAdapter(m),
        fetch_fn=lambda *a, **k: _content(0),
        log=lambda _m: None,
    )
    # Queue work BEFORE starting so all three are present when the worker dies.
    for i in range(1, 4):
        assert pool.submit(_meta(i)) is True
    pool.start()

    results = _drain_to_terminal(pool)
    assert len(results) == 3
    assert all(r.status == POOL_FAILED for r in results)   # no job silently lost
    assert {r.meta.index for r in results} == {1, 2, 3}
    assert pool.worker_failed is True
    assert pool.pool_error and "manager/adapter" in pool.pool_error
    # Stops accepting work after the failure.
    assert pool.submit(_meta(4)) is False


# ── terminal classification for not-found / extraction failures ───────────────
def test_notfound_and_extraction_are_terminal_not_retried() -> None:
    from webnovel_scraper.models import EmptyExtractionError

    calls = {"n": 0}

    def fetch_fn(meta, **kw):
        calls["n"] += 1
        if meta.index == 1:
            raise NotFoundFetchError("HTTP 404", status=404)
        raise EmptyExtractionError("no body")

    pool = _make_pool(fetch_fn=fetch_fn).start()
    pool.submit(_meta(1))
    pool.submit(_meta(2))
    results = {r.meta.index: r for r in _drain_to_terminal(pool)}

    assert results[1].status == NOT_FOUND
    assert results[2].status == EXTRACTION_FAILED
    # Each terminalized on its FIRST attempt (no ladder escalation).
    assert results[1].attempts == 1
    assert results[2].attempts == 1
    assert calls["n"] == 2


# ── cancel mid-run, INSIDE a fake CF wait, stops promptly with a clean join ────
def test_cancel_interrupts_a_fake_cf_wait_mid_attempt() -> None:
    clock = _Clock()
    cancel_event = threading.Event()

    def sleep(seconds: float) -> None:
        clock.t += seconds
        if clock.t >= 5.0:           # fire Stop part-way through the long CF wait
            cancel_event.set()

    pool_box: dict = {}

    def fetch_fn(meta, **kw):
        # Model a long Cloudflare poll through the pool's cancel-aware wait — it must
        # abort when Stop fires, not run to completion.
        pool_box["pool"]._cancelable_sleep(100.0)
        return _content(meta.index)   # unreachable once cancelled

    pool = _make_pool(
        fetch_fn=fetch_fn,
        cancel_event=cancel_event,
        monotonic=clock.now,
        sleep=sleep,
    )
    pool_box["pool"] = pool
    pool.start()
    pool.submit(_meta(1))

    pool.join(5.0)
    assert not pool._thread.is_alive()
    (result,) = pool.poll_results()
    assert result.status == CANCELLED
    # Interrupted mid-wait (~5s), nowhere near the 100s the wait was asked for.
    assert 5.0 <= clock.t < 100.0


# ── timing-regression guard: a pool wait must run off the injected clock ──────
def test_pool_wait_runs_off_the_injected_clock_and_does_not_hang() -> None:
    """A long worker wait completes via the FAKE clock (instant in real time). If
    any pool wait reached a real ``time.sleep`` while reading the fake monotonic,
    the fake clock could never advance and this would hang on the real clock until
    the watchdog killed it — the Phase-1 limiter-hang lesson, carried forward."""
    clock = _Clock()
    pool_box: dict = {}

    def fetch_fn(meta, **kw):
        pool_box["pool"]._cancelable_sleep(600.0)   # 600 fake-seconds
        return _content(meta.index)

    pool = _make_pool(fetch_fn=fetch_fn, clock=clock)
    pool_box["pool"] = pool
    pool.start()
    pool.submit(_meta(1))

    started = time.monotonic()
    (result,) = _drain_to_terminal(pool)
    real_elapsed = time.monotonic() - started

    assert result.status == RESCUED
    assert clock.t >= 600.0          # the wait advanced via the injected fake sleep
    assert real_elapsed < 30.0       # ...but cost ~no real time (would hang on real clock)
