"""BH-3 regression tests: HostRateLimiter FIFO is scoped per host."""

from __future__ import annotations

import threading
import time

from webnovel_scraper.host_rate_limiter import HostRateLimiter, normalize_host
from webnovel_scraper.request_manager import ScrapeCancelled


def _wait_until(predicate, *, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not reached before timeout")


def test_different_host_waiter_does_not_wait_for_host_a_cooldown() -> None:
    release_a = threading.Event()
    a_sleeping = threading.Event()
    cancel_a = threading.Event()
    results: list[str] = []

    def blocking_sleep(_seconds: float) -> None:
        a_sleeping.set()
        release_a.wait(1.0)

    limiter = HostRateLimiter(
        3.0,
        monotonic=lambda: 0.0,
        sleep=blocking_sleep,
    )
    limiter.note_rate_limited("https://a.example/chapter-1", retry_after=30.0)

    def wait_for_a() -> None:
        try:
            limiter.acquire("https://a.example/chapter-1", cancel_event=cancel_a)
        except ScrapeCancelled:
            results.append("a-cancelled")

    def wait_for_b() -> None:
        limiter.acquire("https://b.example/chapter-1")
        results.append("b-acquired")

    a_thread = threading.Thread(target=wait_for_a, name="wait-a")
    a_thread.start()
    assert a_sleeping.wait(1.0)

    b_thread = threading.Thread(target=wait_for_b, name="wait-b")
    b_thread.start()
    b_thread.join(0.5)

    assert results == ["b-acquired"]

    cancel_a.set()
    release_a.set()
    a_thread.join(1.0)
    assert not a_thread.is_alive()


def test_fifo_order_is_preserved_within_one_host() -> None:
    clock = [0.0]
    first_sleeping = threading.Event()
    release_first = threading.Event()
    order: list[str] = []
    url = "https://same.example/chapter"
    host = normalize_host(url)

    def now() -> float:
        return clock[0]

    def blocking_sleep(_seconds: float) -> None:
        first_sleeping.set()
        release_first.wait(1.0)

    limiter = HostRateLimiter(0.0, monotonic=now, sleep=blocking_sleep)
    limiter.note_rate_limited(url, retry_after=5.0)

    def first() -> None:
        limiter.acquire(url)
        order.append("first")

    def second() -> None:
        limiter.acquire(url)
        order.append("second")

    first_thread = threading.Thread(target=first, name="same-first")
    second_thread = threading.Thread(target=second, name="same-second")
    first_thread.start()
    assert first_sleeping.wait(1.0)
    second_thread.start()

    def both_queued() -> bool:
        with limiter._cond:  # private queue state is the behavior under test.
            return len(limiter._waiters.get(host, ())) == 2

    _wait_until(both_queued)
    clock[0] = 5.0
    release_first.set()
    first_thread.join(1.0)
    second_thread.join(1.0)

    assert order == ["first", "second"]


def test_single_host_interval_timing_is_unchanged() -> None:
    clock = [0.0]
    sleeps: list[float] = []

    def now() -> float:
        return clock[0]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    limiter = HostRateLimiter(10.0, monotonic=now, sleep=sleep)
    times: list[float] = []
    for _ in range(3):
        limiter.acquire("https://single.example/chapter")
        times.append(clock[0])

    assert times == [0.0, 10.0, 20.0]

