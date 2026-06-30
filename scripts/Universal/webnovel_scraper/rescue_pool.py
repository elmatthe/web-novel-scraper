"""Single-lane hard-chapter rescue pool (0.2.0, plan §3.5–3.8, §3.12).

When the fast primary path hits a genuinely hard chapter (Cloudflare actually
challenges it) the conductor (Phase 3) sets that chapter aside and keeps going,
handing it to **one** dedicated, long-lived, monotonically-escalating rescue
worker built here. This module owns *only* that worker and its queue/result
plumbing — it **exposes** state for the pipeline-owned circuit breaker (§3.9) but
does not own the breaker, never mutates ``RunReport``, and never writes files.

**0.2.0 is strictly single-lane** (invariant #1): exactly one worker thread,
``RESCUE_MAX_WORKERS == 1`` as a hard cap. The user-selectable 1–5 toggle is
DEFERRED to 0.2.1 (§9); the only forward-looking work permitted here is keeping
the worker count a single ``ScrapeJob.rescue_workers`` field (validated ``== 1``)
and worker construction in one place, so 0.2.1 adds workers without a rewrite.

Design points the plan pins down and this module upholds:

* **A dedicated ``threading.Thread``, not a ``ThreadPoolExecutor``** (§3.5) — an
  executor gives no specific-worker-gets-specific-job guarantee and no clean
  per-thread browser finalizer. The worker creates its **own** ``RequestManager``
  *and* its **own** ``FreeWebNovelAdapter`` bound to it (a shared adapter's
  ``self._rm`` binding would route through the primary's manager), uses them on
  this one thread, and closes the browser in ``finally`` on this same thread.
* **Ladder-as-data + monotonic escalation + initial-mode-follows-primary** (§3.6):
  ``HEADLESS_CAMOUFOX → HEADFUL_CAMOUFOX → HEADFUL_CHROMIUM`` — never de-escalate.
  A visible-primary run starts at ``HEADFUL_CAMOUFOX`` (skips the headless steps)
  so rescue is never weaker than the primary. A worker latched to a higher mode
  skips lower steps on later chapters. ``headless`` is fixed at manager
  construction, so the worker escalates the headless→headful boundary by
  *recreating* its manager, never by flipping a live field.
* **Enforceable per-chapter processing deadline** (§3.7): ``started_at`` is when
  the worker *dequeues* the job (queue wait is recorded separately, not charged).
  Before each attempt it computes ``remaining`` and passes ``min(attempt_timeout,
  remaining)`` into the fetch; it refuses to begin an attempt with too little time
  left. Because each attempt is bounded by ``remaining``, the simulated processing
  time is provably ``<= RESCUE_MAX_ELAPSED_PER_CHAPTER`` under the deterministic
  fake clock. (Honest caveat — see ``_default_fetch``: a *real* attempt passes the
  budget to nav AND the CF wait, so real wall-time per attempt can approach twice
  the budget; the deadline check still stops the ladder, but the real ceiling is
  ~deadline + one attempt's nav/CF overshoot, and an in-flight ``page.goto`` may
  run to its navigation timeout — cancellation is prompt *between* polls, §3.12.)
* **Bounded backlog + dedupe + cancellation-aware backpressure** (§3.8): a
  ``queue.Queue(maxsize=RESCUE_MAX_PENDING)``; a chapter is never submitted twice
  (by index or URL); ``submit`` blocks (intentional backpressure) when the backlog
  is full rather than silently dropping a chapter or bypassing the cap, and aborts
  promptly if the run is cancelled while it waits.
* **One terminal result per submitted job** (§3.12): every job accepted by
  ``submit`` yields exactly one immutable :class:`RescueResult` — including jobs
  queued then cancelled before they run (a ``cancelled`` result) — or the
  pipeline's final drain would hang.
* **EVERY blocking wait the worker performs runs off ONE injected timing source**
  (the same ``sleep``/``monotonic`` seam the host limiter uses), sliced into small
  cancel-checked chunks (``_cancelable_sleep``). No worker wait may reach a real
  ``time.sleep`` or a real-clock ``cancel_event.wait`` that a fake clock cannot
  advance — that was the Phase-1 limiter-hang lesson, carried forward. (Thread
  *coordination* — ``queue.get``/``put`` with short real timeouts — is fine: the
  worker makes real progress, so those never hang a fake-clock test.)

Standard library only for the concurrency/queueing.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .models import ChapterContent, ChapterMeta, EmptyExtractionError, SiteSpec
from .request_manager import (
    FAST_PATH_ATTEMPT_TIMEOUT,
    FETCH_STRATEGY_CAMOUFOX,
    FETCH_STRATEGY_CAMOUFOX_FRESH,
    FETCH_STRATEGY_PLAYWRIGHT_STEALTH,
    FETCH_STRATEGY_PLAYWRIGHT_STEALTH_FRESH,
    RESCUE_MAX_ELAPSED_PER_CHAPTER,
    RESCUE_MAX_PENDING,
    RESCUE_MAX_WORKERS,
    RESCUE_WORKERS,
    FetchError,
    NotFoundFetchError,
    ScrapeCancelled,
)

# ── escalation modes (§3.6) ───────────────────────────────────────────────────
# The worker's persistent mode only ever escalates along this rank order; it never
# returns to a lower mode within its life.
HEADLESS_CAMOUFOX = "headless_camoufox"
HEADFUL_CAMOUFOX = "headful_camoufox"
HEADFUL_CHROMIUM = "headful_chromium"

_MODE_RANK = {HEADLESS_CAMOUFOX: 0, HEADFUL_CAMOUFOX: 1, HEADFUL_CHROMIUM: 2}
# ``headless`` is fixed at RequestManager construction; only the headless→headful
# boundary forces a manager recreate. The camoufox→Chromium escalation within a
# headful manager is an engine switch the manager already handles (it tears the
# other engine down), so both headful modes map to a non-headless manager.
_MODE_HEADLESS = {
    HEADLESS_CAMOUFOX: True,
    HEADFUL_CAMOUFOX: False,
    HEADFUL_CHROMIUM: False,
}
# mode -> {fresh: concrete RequestManager strategy}. A ``fresh`` step recreates the
# engine context for its first attempt (a new fingerprint).
_MODE_STRATEGY = {
    HEADLESS_CAMOUFOX: {False: FETCH_STRATEGY_CAMOUFOX, True: FETCH_STRATEGY_CAMOUFOX_FRESH},
    HEADFUL_CAMOUFOX: {False: FETCH_STRATEGY_CAMOUFOX, True: FETCH_STRATEGY_CAMOUFOX_FRESH},
    HEADFUL_CHROMIUM: {
        False: FETCH_STRATEGY_PLAYWRIGHT_STEALTH,
        True: FETCH_STRATEGY_PLAYWRIGHT_STEALTH_FRESH,
    },
}


@dataclass(frozen=True)
class RescueStep:
    """One rung of the data-driven rescue ladder (§3.6)."""

    mode: str
    fresh: bool
    attempts: int


# The fixed rescue ladder (§3.6). The first HEADLESS step reuses the warmed browser
# (fresh=False); the second is a fresh-context recovery. The first HEADFUL step is
# fresh=True (no headful browser exists yet — a separate worker/fingerprint anyway).
RESCUE_LADDER: tuple[RescueStep, ...] = (
    RescueStep(mode=HEADLESS_CAMOUFOX, fresh=False, attempts=2),  # skipped if visible-primary
    RescueStep(mode=HEADLESS_CAMOUFOX, fresh=True, attempts=1),   # skipped if visible-primary
    RescueStep(mode=HEADFUL_CAMOUFOX, fresh=True, attempts=2),
    RescueStep(mode=HEADFUL_CHROMIUM, fresh=True, attempts=2),
)

# ── terminal result statuses (§3.12/§3.13) ────────────────────────────────────
RESCUED = "rescued"
RESCUE_EXHAUSTED = "rescue_exhausted"
CANCELLED = "cancelled"
NOT_FOUND = "not_found"             # pipeline maps to permanent_failed
EXTRACTION_FAILED = "extraction_failed"
POOL_FAILED = "pool_failed"         # worker-crash / init failure (§3.12)

# Refuse to begin a rescue attempt with less than this many seconds left on the
# per-chapter deadline (§3.7) — there is no point starting work that cannot finish.
RESCUE_MIN_ATTEMPT_BUDGET = 1.0
# Slice length for the worker's cancel-aware waits (mirrors the host limiter).
_WAIT_SLICE = 0.25
# How long the worker blocks on the input queue before re-checking stop/cancel.
_GET_POLL = 0.05
# How long ``submit`` blocks on a full queue before re-checking cancel (backpressure).
_PUT_POLL = 0.05


@dataclass(frozen=True)
class RescueResult:
    """Immutable terminal outcome for one rescued chapter (§3.12).

    Workers NEVER mutate reports or write files — they only emit one of these per
    submitted job. ``content`` is the extracted chapter on ``RESCUED`` and ``None``
    otherwise; ``strategy`` is the escalation mode that produced a rescue;
    ``attempts`` is how many fetch attempts ran; ``error`` carries the last failure
    text for a non-rescued terminal state.
    """

    meta: ChapterMeta
    content: Optional[ChapterContent]
    status: str
    strategy: Optional[str] = None
    attempts: int = 0
    error: Optional[str] = None


class _PoolFailure(Exception):
    """Internal: the worker could not continue (manager/adapter init failed or an
    unexpected crash). Surfaced to the pipeline as a pool-level failure (§3.12),
    never silently swallowed."""


# Default rescue fetch function. Kept module-level so the pipeline (Phase 3) and the
# tests can substitute a deterministic fake without touching pool internals.
RescueFetchFn = Callable[..., ChapterContent]


class RescuePool:
    """One dedicated, persistent, monotonically-escalating rescue worker.

    Lifecycle: ``start()`` spawns the worker; ``submit(meta)`` enqueues a hard
    chapter (cancellation-aware backpressure, dedupe); ``poll_results()`` drains
    completed :class:`RescueResult`s non-blockingly; ``finish()`` then ``join()``
    drains the backlog gracefully (the pipeline's final drain); ``cancel()`` (or
    ``close()``) stops promptly, cancelling queued jobs.
    """

    def __init__(
        self,
        *,
        primary_headless: bool,
        slug: str = "rescue",
        runtime_spec: Optional[SiteSpec] = None,
        host_limiter: object = None,
        cancel_event: Optional[threading.Event] = None,
        cache_root: object = None,
        use_cache: bool = False,
        request_timeout: float = 30.0,
        log: Optional[Callable[[str], None]] = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        deadline_per_chapter: float = RESCUE_MAX_ELAPSED_PER_CHAPTER,
        attempt_timeout: float = FAST_PATH_ATTEMPT_TIMEOUT,
        min_attempt_budget: float = RESCUE_MIN_ATTEMPT_BUDGET,
        ladder: tuple[RescueStep, ...] = RESCUE_LADDER,
        max_pending: int = RESCUE_MAX_PENDING,
        workers: int = RESCUE_WORKERS,
        manager_factory: Optional[Callable[..., object]] = None,
        adapter_factory: Optional[Callable[[object], object]] = None,
        fetch_fn: Optional[RescueFetchFn] = None,
        daemon: bool = False,
    ) -> None:
        # Invariant #1 — strictly single-lane. Reject any attempt to build more than
        # one worker, even hidden behind a default (the 1–5 toggle is 0.2.1, §9).
        if workers != 1 or RESCUE_WORKERS != 1 or RESCUE_MAX_WORKERS != 1:
            raise ValueError(
                "0.2.0 is single-lane: exactly one rescue worker "
                f"(workers={workers!r}, RESCUE_WORKERS={RESCUE_WORKERS}, "
                f"RESCUE_MAX_WORKERS={RESCUE_MAX_WORKERS}); 1–5 is deferred to 0.2.1."
            )

        self._slug = slug
        self._runtime_spec = runtime_spec
        self._host_limiter = host_limiter
        self._cancel_event = cancel_event if cancel_event is not None else threading.Event()
        self._cache_root = cache_root
        self._use_cache = use_cache
        self._request_timeout = float(request_timeout)
        self._log: Callable[[str], None] = log or (lambda _m: None)
        self._monotonic = monotonic
        self._sleep = sleep
        self._deadline_per_chapter = float(deadline_per_chapter)
        self._attempt_timeout = float(attempt_timeout)
        self._min_attempt_budget = float(min_attempt_budget)
        self._ladder = tuple(ladder)
        self._fetch_fn: RescueFetchFn = fetch_fn or self._default_fetch
        self._manager_factory = manager_factory or self._default_manager_factory
        self._adapter_factory = adapter_factory or self._default_adapter_factory
        self._daemon = daemon

        # Initial mode follows the primary (§3.6): rescue must never start weaker.
        self._start_rank = (
            _MODE_RANK[HEADLESS_CAMOUFOX] if primary_headless else _MODE_RANK[HEADFUL_CAMOUFOX]
        )
        self._latched_rank = self._start_rank

        self._in_q: "queue.Queue[ChapterMeta]" = queue.Queue(maxsize=max_pending)
        self._out_q: "queue.Queue[RescueResult]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None

        # Per-worker (thread-affine) browser state — created/used/closed all on the
        # one worker thread (§3.5). ``_manager_headless`` keys the live manager.
        self._manager: object = None
        self._adapter: object = None
        self._manager_headless: Optional[bool] = None
        self.worker_thread_ident: Optional[int] = None  # for the same-thread test

        # State + metrics (the breaker reads, the pipeline maps to RunReport §3.16).
        self._lock = threading.Lock()
        self._accepting = True
        self._stopping = False
        self._seen_indices: set[int] = set()
        self._seen_urls: set[str] = set()
        self.jobs_submitted = 0
        self.jobs_completed = 0
        self.jobs_completed_polled = 0
        self.queue_peak = 0
        self.worker_failed = False
        self.pool_error: Optional[str] = None

    # ── public lifecycle ──────────────────────────────────────────────────────
    def start(self) -> "RescuePool":
        if self._thread is not None:
            return self
        self._thread = threading.Thread(target=self._run, name="rescue-worker", daemon=self._daemon)
        self._thread.start()
        return self

    @property
    def pending(self) -> int:
        """Approximate number of jobs queued and not yet pulled (for §3.8/§3.9)."""
        return self._in_q.qsize()

    @property
    def is_full(self) -> bool:
        """Whether the bounded backlog is full (a breaker input, §3.8/§3.9)."""
        return self._in_q.full()

    @property
    def outstanding(self) -> int:
        """Accepted jobs whose terminal result has not yet been polled."""
        with self._lock:
            return self.jobs_submitted - self.jobs_completed_polled

    def submit(self, meta: ChapterMeta) -> bool:
        """Enqueue a hard chapter for rescue. Returns True if accepted.

        Cancellation-aware backpressure (§3.8): never silently drops a chapter and
        never bypasses the cap. A chapter already submitted (same index or URL) is
        ignored (returns False). When the backlog is full this **blocks** until a
        slot frees or the run is cancelled — intentional backpressure, the honest
        caveat being that the primary may pause here when rescue is saturated.
        """
        index = meta.index
        url = meta.url
        with self._lock:
            if not self._accepting or self._cancel_event.is_set():
                return False
            if index in self._seen_indices or url in self._seen_urls:
                return False
            self._seen_indices.add(index)
            self._seen_urls.add(url)

        while True:
            if self._cancel_event.is_set() or not self._accepting:
                with self._lock:
                    self._seen_indices.discard(index)
                    self._seen_urls.discard(url)
                return False
            try:
                self._in_q.put(meta, timeout=_PUT_POLL)
                break
            except queue.Full:
                continue  # backlog full → wait a slice and re-check cancel

        with self._lock:
            self.jobs_submitted += 1
            self.queue_peak = max(self.queue_peak, self._in_q.qsize())
        return True

    def poll_results(self) -> list[RescueResult]:
        """Non-blocking drain of completed terminal results (§3.12)."""
        out: list[RescueResult] = []
        while True:
            try:
                out.append(self._out_q.get_nowait())
            except queue.Empty:
                break
        if out:
            with self._lock:
                self.jobs_completed_polled += len(out)
        return out

    def finish(self) -> None:
        """Graceful shutdown: stop accepting new work and let the worker drain the
        backlog to completion (the pipeline's final blocking drain, §3.12). Does
        NOT cancel queued jobs."""
        with self._lock:
            self._accepting = False
            self._stopping = True

    def cancel(self) -> None:
        """Prompt shutdown (Stop, §3.12): stop accepting, signal the worker, and let
        it terminalize queued jobs as ``cancelled`` and the in-flight job at its next
        cancel-aware boundary."""
        self._cancel_event.set()
        with self._lock:
            self._accepting = False
            self._stopping = True

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def close(self) -> None:
        """Cancel and join — the GUI Stop / context-exit path."""
        self.cancel()
        self.join()

    def __enter__(self) -> "RescuePool":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── worker thread ───────────────────────────────────────────────────────--
    def _run(self) -> None:
        self.worker_thread_ident = threading.get_ident()
        pool_failed = False
        try:
            while True:
                try:
                    meta = self._in_q.get(timeout=_GET_POLL)
                except queue.Empty:
                    if self._stopping or self._cancel_event.is_set() or pool_failed:
                        break
                    continue

                if pool_failed:
                    self._emit(meta, POOL_FAILED, error=self.pool_error)
                    continue
                if self._cancel_event.is_set():
                    self._emit(meta, CANCELLED)
                    continue
                try:
                    self._process(meta)
                except _PoolFailure as pf:
                    # Worker-crash / init failure: terminalize the active job, stop
                    # accepting, and keep looping to terminalize the rest as
                    # pool_failed so every accepted job still yields one result.
                    pool_failed = True
                    self.worker_failed = True
                    self.pool_error = str(pf)
                    with self._lock:
                        self._accepting = False
                        self._stopping = True
                    self._log(f"  rescue pool FAILED: {pf}")
                    self._emit(meta, POOL_FAILED, error=str(pf))
        finally:
            self._teardown()

    def _process(self, meta: ChapterMeta) -> None:
        """Run the deadline-bounded escalation ladder for one chapter, emitting
        exactly one terminal result (or raising :class:`_PoolFailure` before any
        emit if the worker cannot continue)."""
        started_at = self._monotonic()          # queue wait NOT charged (§3.7)
        deadline = started_at + self._deadline_per_chapter
        attempts = 0
        last_error: Optional[str] = None

        for step in self._steps_for_current_latch():
            for attempt_in_step in range(step.attempts):
                if self._cancel_event.is_set():
                    self._emit(meta, CANCELLED, attempts=attempts)
                    return
                remaining = deadline - self._monotonic()
                if remaining < self._min_attempt_budget:
                    # Refuse to begin an attempt that cannot finish in budget (§3.7).
                    self._log(
                        f"  chapter {meta.index}: {remaining:.1f}s left (<"
                        f"{self._min_attempt_budget:.1f}s) — refusing further attempts."
                    )
                    self._emit(
                        meta, RESCUE_EXHAUSTED, attempts=attempts,
                        error=last_error or "per-chapter deadline budget exhausted",
                    )
                    return
                budget = min(self._attempt_timeout, remaining)

                # Escalate by RECREATING the manager at the headless→headful boundary
                # (headless is fixed at construction); may raise _PoolFailure.
                manager, adapter = self._ensure_manager_for(_MODE_HEADLESS[step.mode])
                self._latched_rank = max(self._latched_rank, _MODE_RANK[step.mode])
                attempts += 1
                fresh = step.fresh and attempt_in_step == 0

                try:
                    content = self._fetch_fn(
                        meta,
                        manager=manager,
                        adapter=adapter,
                        mode=step.mode,
                        fresh=fresh,
                        budget=budget,
                    )
                except ScrapeCancelled:
                    self._emit(meta, CANCELLED, attempts=attempts)
                    return
                except NotFoundFetchError as exc:
                    self._emit(meta, NOT_FOUND, attempts=attempts, error=str(exc))
                    return
                except EmptyExtractionError as exc:
                    self._emit(meta, EXTRACTION_FAILED, attempts=attempts, error=str(exc))
                    return
                except FetchError as exc:
                    # Challenge / transient / rate-limited — a failed attempt; the
                    # ladder escalates. (A 429 already parked the host on the shared
                    # limiter inside the manager, so the next nav waits it out.)
                    last_error = str(exc)
                    self._log(
                        f"  chapter {meta.index} rescue attempt {attempts} "
                        f"({step.mode}) failed: {exc}"
                    )
                    continue
                except _PoolFailure:
                    raise
                except Exception as exc:  # genuinely unexpected → pool-level failure
                    raise _PoolFailure(
                        f"rescue worker crashed on chapter {meta.index}: {exc!r}"
                    ) from exc
                else:
                    self._emit(
                        meta, RESCUED, content=content, strategy=step.mode, attempts=attempts
                    )
                    return

        self._emit(meta, RESCUE_EXHAUSTED, attempts=attempts, error=last_error)

    def _steps_for_current_latch(self) -> list[RescueStep]:
        """Ladder steps at or above the worker's current (only-ever-rising) mode —
        so a worker latched to a higher mode skips the lower steps on later
        chapters and never de-escalates (§3.6)."""
        floor = max(self._start_rank, self._latched_rank)
        return [s for s in self._ladder if _MODE_RANK[s.mode] >= floor]

    def _ensure_manager_for(self, headless: bool):
        """Return the worker's (manager, adapter) for ``headless``, recreating them
        on the worker thread when the headless mode changes (§3.5/§3.6). A factory
        failure is a pool-level failure, not a per-chapter one (§3.12)."""
        if self._manager is not None and self._manager_headless == headless:
            return self._manager, self._adapter
        if self._manager is not None:
            try:
                self._manager.close()
            except Exception as exc:  # teardown is best-effort
                self._log(f"  (rescue manager close on escalate: {exc})")
            self._manager = None
            self._adapter = None
        try:
            manager = self._manager_factory(headless=headless)
            adapter = self._adapter_factory(manager)
        except Exception as exc:
            raise _PoolFailure(
                f"rescue worker could not create its "
                f"{'headless' if headless else 'visible'} manager/adapter: {exc!r}"
            ) from exc
        self._manager = manager
        self._adapter = adapter
        self._manager_headless = headless
        return manager, adapter

    def _teardown(self) -> None:
        """Close the worker's browser on this same thread (§3.5)."""
        if self._manager is not None:
            try:
                self._manager.close()
            except Exception as exc:
                self._log(f"  (rescue worker teardown: {exc})")
            self._manager = None
            self._adapter = None

    def _emit(
        self,
        meta: ChapterMeta,
        status: str,
        *,
        content: Optional[ChapterContent] = None,
        strategy: Optional[str] = None,
        attempts: int = 0,
        error: Optional[str] = None,
    ) -> None:
        self._out_q.put(
            RescueResult(
                meta=meta,
                content=content,
                status=status,
                strategy=strategy,
                attempts=attempts,
                error=error,
            )
        )
        with self._lock:
            self.jobs_completed += 1

    # ── cancel-aware wait (ONE injected timing source) ────────────────────────
    def _cancelable_sleep(self, seconds: float) -> None:
        """Sleep ``seconds`` via the injected ``sleep`` (one timing source, so a fake
        clock drives it deterministically), sliced into ``_WAIT_SLICE`` chunks that
        re-check ``cancel_event`` — raising :class:`ScrapeCancelled` promptly on Stop.

        This is the ONLY blocking wait the worker performs on logical time; it must
        never reach a real ``time.sleep`` or a real-clock ``cancel_event.wait`` that a
        fake clock cannot advance (the Phase-1 limiter-hang lesson). Exposed so the
        rescue fetch can model its CF-poll wait through the same seam.
        """
        end = self._monotonic() + max(0.0, seconds)
        while True:
            if self._cancel_event.is_set():
                raise ScrapeCancelled("rescue wait cancelled")
            remaining = end - self._monotonic()
            if remaining <= 0.0:
                return
            self._sleep(min(_WAIT_SLICE, remaining))

    # ── default real implementations (the pipeline overrides via factories) ───
    def _default_manager_factory(self, *, headless: bool):
        """Build the worker's own RequestManager for ``headless`` (§3.5). Shares the
        run's host limiter + cancel_event so rescue paces with the primary and stops
        on the same Stop."""
        from .request_manager import RequestManager

        manager = RequestManager(
            self._slug,
            use_cache=self._use_cache,
            headless=headless,
            cache_root=self._cache_root,
            log_fn=self._log,
            host_limiter=self._host_limiter,
            http_timeout=self._request_timeout,
            sleep_fn=self._sleep,
        )
        # Share the run-wide cancel signal (the manager makes its own by default).
        manager.cancel_event = self._cancel_event
        return manager

    def _default_adapter_factory(self, manager):
        """Build the worker's OWN FreeWebNovelAdapter bound to its manager (§3.5) —
        a distinct instance from the primary's, so its ``self._rm`` routes through
        the rescue manager, not the primary's."""
        from .adapters.freewebnovel import FreeWebNovelAdapter

        return FreeWebNovelAdapter(request_manager=manager, log=self._log)

    def _default_fetch(
        self, meta: ChapterMeta, *, manager, adapter, mode: str, fresh: bool, budget: float
    ) -> ChapterContent:
        """One rescue fetch attempt: a single concrete engine strategy + extract.

        Honest caveat (§3.7): the budget is applied to BOTH the navigation timeout
        and the Cloudflare wait, so a single real attempt's wall-time can approach
        twice the budget; the worker's deadline check stops the ladder, but the real
        per-chapter ceiling is ~deadline + one attempt's overshoot, and an in-flight
        ``page.goto`` may run to its navigation timeout (cancellation is prompt
        between polls, not necessarily mid-navigation).
        """
        manager._browser_nav_timeout_ms = int(max(0.0, budget) * 1000)
        manager._cloudflare_timeout = max(0.0, budget)
        strategy = _MODE_STRATEGY[mode][bool(fresh)]
        html = manager._fetch_uncached_strategy(meta.url, strategy)
        return adapter._extract_chapter(html, meta)
