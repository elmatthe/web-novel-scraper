"""Global, fair, per-host navigation rate limiter (0.2.0, plan §3.4).

One :class:`HostRateLimiter` is constructed **once per run** and shared by the
primary pipeline *and* the single rescue worker — never one per worker. It gates
every *top-level navigation* (an HTTP ``GET``, a cloudscraper ``GET``, a browser
``page.goto`` for origin warm-up or chapter nav, and each retry/fresh-context
nav) so that two navigations to the same host can never be issued closer together
than the effective interval. Cache reads and browser sub-resources are NOT gated
(the caller simply does not ``acquire`` for those).

Design points the plan pins down:

* **Key = normalized host** — lowercase hostname + effective port — so
  ``example.com`` and ``example.com:443`` share one slot but two genuinely
  different hosts don't block each other.
* **Monotonic clock**, injectable for deterministic tests (a fake clock that only
  advances when the injected ``sleep`` runs).
* **Initial effective interval = max(HOST_MIN_INTERVAL, job.delay)** — set by the
  caller — so a user delay of 10s actually spaces at 10s, not the 3s floor.
* **Fair / FIFO / no-starvation** via a ticket deque: the front waiter always wins
  the next slot, so no thread is starved by a busier one.
* The lock is **not held during the wait** — a waiting thread sleeps outside the
  critical section, so reserving the next slot stays cheap.
* **Positive jitter only**, added *after* the interval (never shortens spacing).
* The effective interval may be **raised while threads wait** (the pipeline's
  ``_Pacer`` bumps it on a detected block in Phase 3); it is never lowered.
* Waiting **honors a cancel_event** — a Stop request aborts the wait promptly with
  :class:`ScrapeCancelled` rather than sleeping out the full interval.
* A **host cooldown** (``note_rate_limited``) lets a 429 park every lane on that
  host until ``Retry-After`` elapses — distinct from a Cloudflare block (§3.9).

Standard library only.
"""

from __future__ import annotations

import collections
import random
import threading
import time
from typing import Callable, Deque, Dict, Optional
from urllib.parse import urlsplit

# Default cooldown applied on a 429 with no usable Retry-After. Modest by design:
# a rate limit is a "slow down", not a long ban, and the affected chapter is
# retried on the primary path within a bounded budget (§3.9, wired in Phase 3).
DEFAULT_RATE_LIMIT_COOLDOWN = 60.0

# How long a non-front waiter sleeps before re-checking whether it reached the
# front of the queue. Only used by genuinely concurrent threads (the front waiter
# sleeps for its exact interval); kept small so a freed slot is taken promptly.
_NON_FRONT_POLL = 0.1

# The front waiter sleeps for its host interval in slices no longer than this, so a
# Stop request aborts the wait within one slice rather than after the full interval.
_WAIT_SLICE = 0.25


def normalize_host(url: str) -> str:
    """Return the rate-limit key for ``url``: ``hostname[:port]``, lowercased.

    The port is included only when explicit, so a single host is one slot
    regardless of how its URLs are written.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    port = parts.port
    if port is None:
        return host
    return f"{host}:{port}"


class HostRateLimiter:
    """Fair, navigation-level, per-host pacing shared across primary + rescue."""

    def __init__(
        self,
        interval: float,
        *,
        jitter_ratio: float = 0.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        random_fn: Callable[[], float] = random.random,
        default_cooldown: float = DEFAULT_RATE_LIMIT_COOLDOWN,
    ) -> None:
        self._interval = max(0.0, float(interval))
        self._jitter_ratio = max(0.0, float(jitter_ratio))
        self._monotonic = monotonic
        self._sleep = sleep
        self._random = random_fn
        self._default_cooldown = max(0.0, float(default_cooldown))

        self._cond = threading.Condition(threading.Lock())
        self._waiters: Deque[object] = collections.deque()
        self._next_allowed: Dict[str, float] = {}
        self._blocked_until: Dict[str, float] = {}

    # ── introspection ────────────────────────────────────────────────────────
    @property
    def interval(self) -> float:
        with self._cond:
            return self._interval

    def blocked_until(self, url: str) -> float:
        """Monotonic time before which navigations to this host are parked."""
        host = normalize_host(url)
        with self._cond:
            return self._blocked_until.get(host, 0.0)

    # ── adaptive interval (used by _Pacer in Phase 3) ────────────────────────
    def raise_interval(self, new_interval: float) -> None:
        """Raise the effective interval (never lower it); applies to all hosts.

        A waiting thread picks up the larger interval on its next reservation, so
        an auto-slowdown detected by the pipeline slows the rescue lane too.
        """
        with self._cond:
            if new_interval > self._interval:
                self._interval = float(new_interval)
                self._cond.notify_all()

    # ── 429 host cooldown (§3.9) ──────────────────────────────────────────────
    def note_rate_limited(self, url: str, retry_after: Optional[float] = None) -> float:
        """Park every lane on this host until a cooldown elapses; return that time.

        ``retry_after`` (a valid, positive ``Retry-After`` in seconds) is honored;
        otherwise a modest default cooldown applies. Distinct from a Cloudflare
        block — this throttles, it does not escalate to browser rescue.
        """
        host = normalize_host(url)
        cooldown = (
            float(retry_after)
            if (retry_after is not None and retry_after > 0)
            else self._default_cooldown
        )
        with self._cond:
            now = self._monotonic()
            until = max(self._blocked_until.get(host, 0.0), now + cooldown)
            self._blocked_until[host] = until
            self._cond.notify_all()
            return until

    # ── acquire ───────────────────────────────────────────────────────────────
    def acquire(self, url: str, *, cancel_event: Optional[object] = None) -> None:
        """Block until a top-level navigation to ``url``'s host may proceed.

        On return the next slot for that host has been reserved (``now + interval
        + jitter``), so the *caller* should immediately perform the navigation and
        must NOT hold any lock meanwhile. Honors ``cancel_event`` while waiting,
        raising :class:`ScrapeCancelled` (imported lazily to avoid an import cycle)
        if Stop is pressed mid-wait.
        """
        host = normalize_host(url)
        ticket = object()
        with self._cond:
            self._waiters.append(ticket)
            self._cond.notify_all()
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    from .request_manager import ScrapeCancelled

                    raise ScrapeCancelled(
                        f"Rate-limiter wait cancelled before navigating {url}"
                    )
                with self._cond:
                    is_front = self._waiters[0] is ticket
                    now = self._monotonic()
                    allowed_at = max(
                        self._next_allowed.get(host, 0.0),
                        self._blocked_until.get(host, 0.0),
                    )
                    if is_front and now >= allowed_at:
                        self._next_allowed[host] = now + self._interval + self._jitter()
                        self._waiters.popleft()
                        self._cond.notify_all()
                        return
                    wait_for = (allowed_at - now) if is_front else _NON_FRONT_POLL
                # Wait OUTSIDE the lock so reserving stays cheap and other hosts
                # are never blocked by this host's pacing.
                if is_front:
                    self._wait(max(0.0, wait_for), cancel_event)
                else:
                    with self._cond:
                        self._cond.wait(timeout=_NON_FRONT_POLL)
        except BaseException:
            # Never leave a dangling ticket (it would starve the queue forever).
            with self._cond:
                try:
                    self._waiters.remove(ticket)
                except ValueError:
                    pass
                self._cond.notify_all()
            raise

    # ── helpers ───────────────────────────────────────────────────────────────
    def _jitter(self) -> float:
        if self._jitter_ratio <= 0.0:
            return 0.0
        # Positive only — never shortens the interval below the floor.
        return self._random() * self._jitter_ratio * self._interval

    def _wait(self, seconds: float, cancel_event: Optional[object]) -> None:
        """Sleep ``seconds`` via the injected ``sleep`` (one timing source, so a
        fake clock drives every wait deterministically), but in short slices that
        re-check ``cancel_event`` — so a Stop request aborts the wait promptly
        instead of sleeping out the whole interval (the "cancel_event, not a bare
        sleep" requirement of §3.4)."""
        remaining = seconds
        while remaining > 0.0:
            if cancel_event is not None and cancel_event.is_set():
                return  # the acquire loop re-checks is_set() and raises
            chunk = remaining if remaining < _WAIT_SLICE else _WAIT_SLICE
            self._sleep(chunk)
            remaining -= chunk
