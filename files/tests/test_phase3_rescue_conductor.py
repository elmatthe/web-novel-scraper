"""Phase 3 (0.2.0) — the fast-primary + single-lane rescue CONDUCTOR.

All offline + deterministic: fake primary manager/adapter factories (no real
browser, no network), a real :class:`RescuePool` driven by a fake heavy-fetch, an
injected fake ``monotonic`` + fake ``sleep`` for every logical wait, and the same
shared ``HostRateLimiter`` + ``cancel_event`` the production conductor wires up.
These lock in: the TOC bootstrap fallback (§3.11), the headless-only circuit
breaker + headless→visible recreate/latch (§3.9/§3.10), the 429 policy, the
continuous + final rescue drain folded into all three output modes (§3.12), the
sweep-replacement scope gate (§3.13/§3.14), the RunReport invariants/metrics
(§3.16), and end-to-end cancellation — without ever building more than one worker.
"""

from __future__ import annotations

import threading

import pytest

from webnovel_scraper import pipeline
from webnovel_scraper import rescue_pool as rp
from webnovel_scraper.host_rate_limiter import HostRateLimiter
from webnovel_scraper.models import ChapterContent, ChapterMeta, OutputMode, ScrapeJob
from webnovel_scraper.pipeline import _CircuitBreaker
from webnovel_scraper.request_manager import (
    ChallengeFetchError,
    FetchInfo,
    NotFoundFetchError,
    RateLimitedFetchError,
    ScrapeCancelled,
    TransientFetchError,
)

FWN = "freewebnovel"
NOVEL = "shadow-slave"  # a real enabled FWN catalog row


# ── deterministic clock ───────────────────────────────────────────────────────
class _Clock:
    """Shared mutable monotonic clock advanced only by the injected ``sleep``."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(0.0, seconds)


# ── fake PRIMARY engine (manager + adapter) ───────────────────────────────────
class FakePrimaryManager:
    """Stand-in for the pipeline-owned primary RequestManager. Records close()
    calls (for the close-exactly-once test) and carries ``last_fetch_info`` the
    conductor reads for the breaker's cache-vs-network distinction."""

    def __init__(self, headless: bool, limiter, cancel_event) -> None:
        self.headless = headless
        self.host_limiter = limiter           # the shared limiter (inheritance proof)
        self.cancel_event = cancel_event      # the shared cancel signal (inheritance proof)
        self.last_fetch_info = None
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


_CLASS_FOR = {
    ChallengeFetchError: "challenge",
    TransientFetchError: "transient",
    NotFoundFetchError: "not_found",
    RateLimitedFetchError: "rate_limited",
}


class FakePrimaryAdapter:
    """Fake FWN adapter bound to a fake manager. ``behave(index, manager)`` and
    ``build(manager)`` are test-supplied; the adapter sets ``manager.last_fetch_info``
    to mirror the real manager so the conductor's breaker counts correctly."""

    def __init__(self, manager, *, behave, build, record) -> None:
        self.manager = manager
        self._behave = behave
        self._build = build
        self._record = record
        self.warnings: list[str] = []

    def build_chapter_index(self, spec, *, fast_path=False):
        return self._build(self.manager)

    def fetch_chapter(self, meta, spec, *, fast_path=False):
        self._record.append((meta.index, self.manager.headless))
        outcome = self._behave(meta.index, self.manager)
        kind = outcome[0]
        if kind == "ok":
            self.manager.last_fetch_info = FetchInfo(
                from_cache=False, classification="success", strategy="camoufox"
            )
            return outcome[1]
        if kind == "cache":
            self.manager.last_fetch_info = FetchInfo(from_cache=True, classification="cache")
            return outcome[1]
        # kind == "raise"
        exc = outcome[1]
        self.manager.last_fetch_info = FetchInfo(
            from_cache=False, classification=_CLASS_FOR.get(type(exc), "challenge")
        )
        raise exc


# ── fake browser for the rescue pool (no real engine) ─────────────────────────
class _FakePoolManager:
    def __init__(self, headless: bool) -> None:
        self.headless = headless

    def close(self) -> None:
        pass


def _meta(i: int) -> ChapterMeta:
    return ChapterMeta(index=i, url=f"https://freewebnovel.com/novel/shadow-slave/chapter-{i}")


def _content(i: int) -> ChapterContent:
    return ChapterContent(
        index=i, title=f"Title {i}",
        paragraphs=[f"First body paragraph for chapter {i}, long enough to render."],
    )


def _metas(n: int):
    return [_meta(i) for i in range(1, n + 1)]


# ── job + harness ─────────────────────────────────────────────────────────────
def _job(tmp_path, *, mode=OutputMode.SEPARATE, headless=True, start=1, end=10,
         delay=3.0, chunk_size=2) -> ScrapeJob:
    return ScrapeJob(
        novel_slug=NOVEL, adapter_key=FWN, start=start, end=end, delay=delay,
        output_mode=mode, use_cache=False, output_dir=tmp_path, chunk_size=chunk_size,
        use_browser=True, headless=headless,
    )


def _run(job, *, behave, build, rescue_fetch, clock=None, cancel=None, limiter=None,
         record=None, created=None, log=None):
    """Drive ``run_scrape`` on the FWN-rescue conductor with fully injected fakes."""
    clock = clock if clock is not None else _Clock()
    cancel = cancel if cancel is not None else threading.Event()
    limiter = limiter if limiter is not None else HostRateLimiter(
        max(pipeline.HOST_MIN_INTERVAL, job.delay), monotonic=clock.now, sleep=clock.sleep
    )
    record = record if record is not None else []
    created = created if created is not None else []
    log = log if log is not None else (lambda _m: None)

    def manager_factory(*, headless):
        m = FakePrimaryManager(headless, limiter, cancel)
        created.append(m)
        return m

    def adapter_factory(m):
        return FakePrimaryAdapter(m, behave=behave, build=build, record=record)

    def pool_factory():
        return rp.RescuePool(
            primary_headless=job.headless, host_limiter=limiter, cancel_event=cancel,
            monotonic=clock.now, sleep=clock.sleep, fetch_fn=rescue_fetch,
            manager_factory=lambda *, headless: _FakePoolManager(headless),
            adapter_factory=lambda m: object(), log=log,
        )

    report = pipeline.run_scrape(
        job, log=log, cancel_event=cancel, sleep_fn=clock.sleep, monotonic_fn=clock.now,
        host_limiter=limiter, request_manager_factory=manager_factory,
        primary_adapter_factory=adapter_factory, rescue_pool_factory=pool_factory,
    )
    return report


def _pdfs(tmp_path):
    return sorted(p.name for p in tmp_path.glob("*.pdf"))


# common behaviours
def _build_ok(n):
    return lambda manager: _metas(n)


def _all_ok(index, manager):
    return ("ok", _content(index))


def _rescue_ok(meta, *, manager, adapter, mode, fresh, budget):
    return _content(meta.index)


def _rescue_fail(meta, *, manager, adapter, mode, fresh, budget):
    raise ChallengeFetchError(f"rescue could not clear chapter {meta.index}")


# ── scope gate ────────────────────────────────────────────────────────────────
def test_scope_gate_only_fwn_browser_runs_use_the_conductor() -> None:
    base = dict(novel_slug=NOVEL, adapter_key=FWN, start=1, end=5, delay=0.0,
                output_mode=OutputMode.SEPARATE, use_cache=False, output_dir=".")
    assert pipeline._rescue_enabled(ScrapeJob(**base, use_browser=True)) is True
    assert pipeline._rescue_enabled(ScrapeJob(**base, use_browser=False)) is False
    assert pipeline._rescue_enabled(
        ScrapeJob(**{**base, "adapter_key": "webnovel_dynamic"}, use_browser=True)
    ) is False


def test_wnd_regression_legacy_path_never_instantiates_a_pool(tmp_path) -> None:
    """A non-rescue run (HTTP path / injected adapter) keeps the legacy sweep flow:
    the rescue pool factory is never consulted, no browser conductor engages."""

    class _LegacyFakeAdapter:
        def __init__(self) -> None:
            self.fetched: list[int] = []
            self.warnings: list[str] = []

        def build_chapter_index(self, spec):
            return _metas(3)

        def fetch_chapter(self, meta, spec):
            self.fetched.append(meta.index)
            return _content(meta.index)

    def _explode():
        raise AssertionError("rescue pool must not be built on the legacy path")

    job = ScrapeJob(
        novel_slug=NOVEL, adapter_key=FWN, start=1, end=3, delay=0.0,
        output_mode=OutputMode.SEPARATE, use_cache=False, output_dir=tmp_path,
        use_browser=False,  # HTTP path → legacy
    )
    adapter = _LegacyFakeAdapter()
    report = pipeline.run_scrape(
        job, adapter=adapter, log=lambda m: None, rescue_pool_factory=_explode,
    )
    assert adapter.fetched == [1, 2, 3]
    assert report.failed == []
    assert report.rescue_jobs_submitted == 0  # metric stays zero on the legacy path


# ── easy run: no rescue ───────────────────────────────────────────────────────
def test_easy_run_never_instantiates_rescue(tmp_path) -> None:
    created_pools: list = []

    def pool_factory():
        created_pools.append(1)
        raise AssertionError("an easy run must not build a rescue worker")

    clock = _Clock()
    cancel = threading.Event()
    limiter = HostRateLimiter(3.0, monotonic=clock.now, sleep=clock.sleep)
    job = _job(tmp_path, end=5)

    def manager_factory(*, headless):
        return FakePrimaryManager(headless, limiter, cancel)

    report = pipeline.run_scrape(
        job, log=lambda m: None, cancel_event=cancel, sleep_fn=clock.sleep,
        monotonic_fn=clock.now, host_limiter=limiter,
        request_manager_factory=manager_factory,
        primary_adapter_factory=lambda m: FakePrimaryAdapter(
            m, behave=_all_ok, build=_build_ok(5), record=[]
        ),
        rescue_pool_factory=pool_factory,
    )
    assert created_pools == []                       # pool never built
    assert sorted(report.written and [p.name for p in report.written]) == _pdfs(tmp_path)
    assert len(report.written) == 5
    assert report.failed == []
    assert report.rescue_jobs_submitted == 0


# ── TOC bootstrap fallback (§3.11) ────────────────────────────────────────────
def test_toc_headless_block_then_visible_retry_succeeds(tmp_path) -> None:
    created: list = []

    def build(manager):
        if manager.headless:
            raise ChallengeFetchError("TOC blocked while headless")
        return _metas(3)

    report = _run(_job(tmp_path, end=3), behave=_all_ok, build=build,
                  rescue_fetch=_rescue_ok, created=created)
    assert report.primary_switched_visible is True
    assert len(report.written) == 3
    assert report.failed == []
    # the headless manager was recreated visible exactly once (2 managers, each closed once)
    assert len(created) == 2
    assert [m.closed for m in created] == [1, 1]


def test_toc_visible_still_fails_aborts_cleanly(tmp_path) -> None:
    def build(manager):
        raise ChallengeFetchError("blocked in every mode")

    with pytest.raises(pipeline.ChapterIndexUnavailable):
        _run(_job(tmp_path, end=3), behave=_all_ok, build=build, rescue_fetch=_rescue_ok)


def test_toc_visible_primary_block_aborts_without_a_retry(tmp_path) -> None:
    """A visible-primary run has nowhere to escalate the TOC: a block aborts at once."""
    calls = {"n": 0}

    def build(manager):
        calls["n"] += 1
        raise ChallengeFetchError("blocked")

    with pytest.raises(pipeline.ChapterIndexUnavailable):
        _run(_job(tmp_path, end=3, headless=False), behave=_all_ok, build=build,
             rescue_fetch=_rescue_ok)
    assert calls["n"] == 1  # no second (visible) attempt — it was already visible


# ── hard chapters → rescue → folded into output (all three modes) ─────────────
def _behave_hard(hard_indices):
    def behave(index, manager):
        if index in hard_indices:
            return ("raise", ChallengeFetchError(f"hard chapter {index}"))
        return ("ok", _content(index))
    return behave


def test_separate_mode_folds_rescued_chapters(tmp_path) -> None:
    report = _run(_job(tmp_path, mode=OutputMode.SEPARATE, end=5, headless=False),
                  behave=_behave_hard({2, 4}), build=_build_ok(5), rescue_fetch=_rescue_ok)
    assert sorted(report.rescued) == [2, 4]
    assert report.failed == []
    assert len(_pdfs(tmp_path)) == 5            # every chapter written, incl. rescued
    assert all(n.startswith("Chapter ") for n in _pdfs(tmp_path))
    # rescued chapters are never also in failed (§3.16)
    assert set(report.rescued).isdisjoint(report.failed)


def test_chunked_mode_folds_rescued_in_index_order(tmp_path) -> None:
    report = _run(_job(tmp_path, mode=OutputMode.CHUNKED, end=4, chunk_size=2,
                       headless=False),
                  behave=_behave_hard({2, 3}), build=_build_ok(4), rescue_fetch=_rescue_ok)
    assert sorted(report.rescued) == [2, 3]
    assert _pdfs(tmp_path) == [
        "Shadow_Slave_Chapters_1-2.pdf",
        "Shadow_Slave_Chapters_3-4.pdf",
    ]
    assert report.failed == []


def test_single_mode_folds_rescued(tmp_path) -> None:
    report = _run(_job(tmp_path, mode=OutputMode.SINGLE, end=4, headless=False),
                  behave=_behave_hard({1, 4}), build=_build_ok(4), rescue_fetch=_rescue_ok)
    assert sorted(report.rescued) == [1, 4]
    assert _pdfs(tmp_path) == ["Shadow_Slave_All_Chapters.pdf"]
    assert report.failed == []


def test_permanent_not_found_recorded_and_run_completes(tmp_path) -> None:
    def behave(index, manager):
        if index == 3:
            return ("raise", NotFoundFetchError("HTTP 404", status=404))
        return ("ok", _content(index))

    report = _run(_job(tmp_path, mode=OutputMode.SEPARATE, end=5, headless=False),
                  behave=behave, build=_build_ok(5), rescue_fetch=_rescue_ok)
    assert report.permanent_failed == [3]
    assert 3 in report.failed
    assert set(report.permanent_failed).issubset(report.failed)
    assert len(report.written) == 4              # the run completed past the dead chapter


def test_rescue_exhausted_is_failed_not_rescued(tmp_path) -> None:
    report = _run(_job(tmp_path, end=3, headless=False),
                  behave=_behave_hard({2}), build=_build_ok(3), rescue_fetch=_rescue_fail)
    assert report.rescued == []
    assert report.rescue_exhausted == [2]
    assert 2 in report.failed
    assert set(report.rescue_exhausted).issubset(report.failed)
    assert set(report.rescued).isdisjoint(report.failed)


# ── circuit breaker (§3.9/§3.10) ──────────────────────────────────────────────
def test_breaker_unit_thresholds() -> None:
    # consecutive ≥ 5 trips
    b = _CircuitBreaker(armed=True)
    for _ in range(4):
        b.record_network(is_challenge=True)
    assert b.should_trip() is False
    b.record_network(is_challenge=True)
    assert b.should_trip() is True

    # a non-challenge network fetch (success / 5xx / not-found) resets the streak
    b2 = _CircuitBreaker(armed=True)
    for _ in range(4):
        b2.record_network(is_challenge=True)
    b2.record_network(is_challenge=False)        # e.g. a transient 5xx or a success
    assert b2.consecutive == 0
    assert b2.should_trip() is False

    # ≥ 9 challenges within the rolling window trips even without 5 in a row
    b3 = _CircuitBreaker(armed=True)
    pattern_tripped = False
    for i in range(1, 21):
        b3.record_network(is_challenge=(i % 5 != 0))  # 4-in-a-row max, 16/20 challenges
        if b3.should_trip():
            pattern_tripped = True
            assert b3.consecutive < pipeline.BREAKER_CONSECUTIVE_CHALLENGES
            break
    assert pattern_tripped is True

    # not armed (visible primary) never trips
    b4 = _CircuitBreaker(armed=False)
    for _ in range(10):
        b4.record_network(is_challenge=True)
    assert b4.should_trip() is False


def test_breaker_trips_switches_visible_retries_sync_and_latches(tmp_path) -> None:
    created: list = []
    record: list = []

    def behave(index, manager):
        # Headless: chapters 1-5 are blocked (5 consecutive → trips on #5).
        # Visible (after the switch): everything succeeds.
        if manager.headless:
            return ("raise", ChallengeFetchError(f"headless block {index}"))
        return ("ok", _content(index))

    report = _run(_job(tmp_path, mode=OutputMode.SEPARATE, end=8, headless=True),
                  behave=behave, build=_build_ok(8), rescue_fetch=_rescue_ok,
                  created=created, record=record)

    assert report.circuit_breaker_tripped is True
    assert report.primary_switched_visible is True
    # exactly one switch → two managers, each closed exactly once (§3.15)
    assert len(created) == 2
    assert [m.closed for m in created] == [1, 1]
    # the triggering chapter (5) was retried synchronously on the VISIBLE primary
    assert (5, False) in record
    # chapters 6-8 ran on the visible primary (latched), never headless
    assert (6, False) in record and (7, False) in record and (8, False) in record
    assert (6, True) not in record
    # chapters 1-4 were owned by rescue (rescued); 5-8 by the visible primary
    assert set(report.rescued) == {1, 2, 3, 4}
    assert report.failed == []
    assert len(_pdfs(tmp_path)) == 8


def test_breaker_not_armed_on_visible_primary_run(tmp_path) -> None:
    created: list = []
    record: list = []

    def behave(index, manager):
        # Always a challenge: a headless run would trip; a visible run must not.
        return ("raise", ChallengeFetchError(f"block {index}"))

    report = _run(_job(tmp_path, end=8, headless=False), behave=behave,
                  build=_build_ok(8), rescue_fetch=_rescue_ok, created=created, record=record)

    assert report.circuit_breaker_tripped is False
    assert report.primary_switched_visible is False
    assert len(created) == 1                     # no recreate of an already-visible browser
    assert created[0].closed == 1
    assert sorted(report.rescued) == [1, 2, 3, 4, 5, 6, 7, 8]  # all rescued, none synchronous


# ── 429 policy (§3.9 tail) ────────────────────────────────────────────────────
def test_rate_limit_cooldown_observed_no_breaker_resolves_on_primary(tmp_path) -> None:
    clock = _Clock()
    cancel = threading.Event()
    limiter = HostRateLimiter(3.0, monotonic=clock.now, sleep=clock.sleep)
    attempts = {"n": 0}

    def behave(index, manager):
        if index == 2:
            attempts["n"] += 1
            if attempts["n"] == 1:
                # mirror the real manager: a 429 parks the host on the shared limiter
                limiter.note_rate_limited(_meta(2).url, 5.0)
                return ("raise", RateLimitedFetchError("HTTP 429", status=429, retry_after=5.0))
            return ("ok", _content(2))           # the retry on the primary succeeds
        return ("ok", _content(index))

    def rescue_fetch(meta, *, manager, adapter, mode, fresh, budget):
        raise AssertionError("a 429 must NOT be escalated to browser rescue")

    report = _run(_job(tmp_path, end=3, headless=True), behave=behave, build=_build_ok(3),
                  rescue_fetch=rescue_fetch, clock=clock, cancel=cancel, limiter=limiter)

    # the cooldown was registered on the shared limiter (primary AND rescue observe it)
    assert limiter.blocked_until(_meta(2).url) > 0.0
    # resolved on the primary (no rescue, no permanent/extraction failure)
    assert report.failed == []
    assert len(report.written) == 3
    assert report.rescue_jobs_submitted == 0     # never handed to the rescue lane


def test_rate_limit_persists_records_transient_for_resume(tmp_path) -> None:
    def behave(index, manager):
        if index == 2:
            return ("raise", RateLimitedFetchError("HTTP 429", status=429, retry_after=1.0))
        return ("ok", _content(index))

    def rescue_fetch(meta, *, manager, adapter, mode, fresh, budget):
        raise AssertionError("a persistent 429 must not launch a browser")

    report = _run(_job(tmp_path, end=3, headless=False), behave=behave, build=_build_ok(3),
                  rescue_fetch=rescue_fetch)
    assert 2 in report.failed                    # recorded as a (transient) failure for resume
    assert 2 not in report.permanent_failed
    assert 2 not in report.rescued
    assert report.rescue_jobs_submitted == 0


# ── _Pacer → shared limiter interval (§3.4 tail / §3.16) ──────────────────────
def test_pacer_block_raises_shared_limiter_interval_seen_by_rescue(tmp_path) -> None:
    clock = _Clock()
    cancel = threading.Event()
    limiter = HostRateLimiter(3.0, monotonic=clock.now, sleep=clock.sleep)
    assert limiter.interval == 3.0

    report = _run(_job(tmp_path, end=1, delay=3.0, headless=False),
                  behave=_behave_hard({1}), build=_build_ok(1), rescue_fetch=_rescue_ok,
                  clock=clock, cancel=cancel, limiter=limiter)

    # the primary block raised the SHARED limiter interval; the rescue lane, which
    # acquires through the same limiter, now paces at the larger interval too.
    assert limiter.interval > 3.0
    assert report.rescued == [1]


# ── RunReport invariants under a mixed scenario (§3.16) ───────────────────────
def test_runreport_invariants_mixed_scenario(tmp_path) -> None:
    def behave(index, manager):
        if index == 2:
            return ("raise", ChallengeFetchError("hard → rescued"))
        if index == 3:
            return ("raise", NotFoundFetchError("HTTP 404", status=404))
        if index == 4:
            return ("raise", ChallengeFetchError("hard → exhausted"))
        if index == 5:
            from webnovel_scraper.models import EmptyExtractionError
            return ("raise", EmptyExtractionError("no body"))
        return ("ok", _content(index))

    def rescue_fetch(meta, *, manager, adapter, mode, fresh, budget):
        if meta.index == 4:
            raise ChallengeFetchError("rescue could not clear 4")
        return _content(meta.index)

    report = _run(_job(tmp_path, mode=OutputMode.SEPARATE, end=5, headless=False),
                  behave=behave, build=_build_ok(5), rescue_fetch=rescue_fetch)

    assert report.rescued == [2]
    assert set(report.permanent_failed) == {3}
    assert set(report.extraction_failed) == {5}
    assert set(report.rescue_exhausted) == {4}
    # invariants
    assert set(report.permanent_failed).issubset(report.failed)
    assert set(report.extraction_failed).issubset(report.failed)
    assert set(report.rescue_exhausted).issubset(report.failed)
    assert set(report.rescued).isdisjoint(report.failed)
    assert set(report.failed) == {3, 4, 5}


# ── cancellation end-to-end (§3.12) ───────────────────────────────────────────
def test_stop_cancels_loop_and_pool_every_accepted_job_terminalizes(tmp_path) -> None:
    clock = _Clock()
    cancel = threading.Event()
    limiter = HostRateLimiter(3.0, monotonic=clock.now, sleep=clock.sleep)

    def behave(index, manager):
        # every chapter is hard → submitted to rescue; press Stop while submitting #4
        if index == 4:
            cancel.set()
        return ("raise", ChallengeFetchError(f"hard {index}"))

    def rescue_fetch(meta, *, manager, adapter, mode, fresh, budget):
        # a long fake CF wait that only ends when Stop is pressed (interrupt mid-wait)
        end = clock.now() + 1000.0
        while clock.now() < end:
            if cancel.is_set():
                raise ScrapeCancelled("rescue cancelled mid-CF-wait")
            clock.sleep(0.25)
        return _content(meta.index)

    job = _job(tmp_path, end=10, headless=True)
    pool_holder: dict = {}

    def manager_factory(*, headless):
        return FakePrimaryManager(headless, limiter, cancel)

    def pool_factory():
        pool = rp.RescuePool(
            primary_headless=job.headless, host_limiter=limiter, cancel_event=cancel,
            monotonic=clock.now, sleep=clock.sleep, fetch_fn=rescue_fetch,
            manager_factory=lambda *, headless: _FakePoolManager(headless),
            adapter_factory=lambda m: object(), log=lambda _m: None,
        )
        pool_holder["pool"] = pool
        return pool

    report = pipeline.run_scrape(
        job, log=lambda m: None, cancel_event=cancel, sleep_fn=clock.sleep,
        monotonic_fn=clock.now, host_limiter=limiter,
        request_manager_factory=manager_factory,
        primary_adapter_factory=lambda m: FakePrimaryAdapter(
            m, behave=behave, build=_build_ok(10), record=[]
        ),
        rescue_pool_factory=pool_factory,
    )

    pool = pool_holder["pool"]
    assert report.cancelled is True
    # the worker exited (no deadlock — the watchdog never had to kill us)
    assert pool._thread is None or not pool._thread.is_alive()
    # one terminal result per ACCEPTED job — nothing left outstanding (§3.12)
    assert pool.jobs_completed == pool.jobs_submitted
    assert pool.outstanding == 0
    # cancelled chapters are NOT counted as rescue_exhausted (§3.16)
    assert report.rescue_exhausted == []
