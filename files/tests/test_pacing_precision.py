"""Pacing tests use fake clocks and transports only."""
import pytest

from files.tests.test_option_b_runner import Clock, Reply, Wire, no_network, page
import option_b_runner as runner
from webnovel_scraper.host_rate_limiter import HostRateLimiter


def test_monotonic_deadline_survives_early_wakeups():
    clock = Clock()
    sleeps = []

    def early_sleep(seconds):
        sleeps.append(seconds)
        clock.now += max(0.001, seconds - 0.007)

    limiter = HostRateLimiter(3.0, sleep=early_sleep, monotonic=clock.monotonic)
    starts = []
    for _ in range(20):
        limiter.acquire("https://example.test/chapter")
        starts.append(clock.now)
    assert sleeps
    assert all(b - a >= 3.02 for a, b in zip(starts, starts[1:]))


def test_transport_setup_and_durable_evidence_cannot_shorten_actual_spacing(tmp_path):
    ws = runner.Workspace.open(runner.Config(root=tmp_path / "acceptance"))
    clock = Clock()
    wire = Wire([Reply(page(i)) for i in (1, 2, 3)])
    starts, attempts = [], []
    delays = iter((0.08, 0.001, 0.001))

    def connection(host, **kwargs):
        conn = wire(host, **kwargs)
        original = conn.request

        def request(*args, **kw):
            starts.append(clock.now)
            return original(*args, **kw)

        conn.request = request
        return conn

    def sink(kind, event):
        if kind == "attempt":
            attempts.append(clock.now)
            clock.now += next(delays)  # variable fsync latency before actual dispatch

    urls = [f"{runner.SPEC.url}/chapter-{i}" for i in (1, 2, 3)]
    manager = runner.RestrictedManager(ws, cap=3, allowed=dict.fromkeys(urls, 1),
        connection_factory=connection, sleep=clock.sleep, monotonic=clock.monotonic,
        event_sink=sink)
    try:
        for url in urls:
            manager.fetch(url)
    finally:
        manager.close()
    assert len(wire.calls) == 3
    assert all(b - a >= 3.02 for a, b in zip(starts, starts[1:]))
    assert all(b - a >= 3.02 for a, b in zip(attempts, attempts[1:]))
