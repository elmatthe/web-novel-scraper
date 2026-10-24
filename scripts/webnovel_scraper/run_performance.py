"""Per-run offline-friendly timing and transport counters, shared with rescue."""
from contextlib import contextmanager, nullcontext
import threading


def measure_stage(report, name):
    metrics = getattr(report, "_performance", None)
    return metrics.measure(name) if metrics else nullcontext()


class RunPerformance:
    def __init__(self, clock):
        self.clock = clock
        self.started = clock()
        self._lock = threading.Lock()
        self.values = dict(discovery_seconds=0.0, fetching_seconds=0.0, rendering_seconds=0.0,
                           cache_hits=0, cache_misses=0, website_requests=0)

    def count(self, name, amount=1):
        with self._lock:
            self.values[name] += amount

    @contextmanager
    def measure(self, name):
        start = self.clock()
        try:
            yield
        finally:
            with self._lock:
                self.values[name] += max(0.0, self.clock() - start)

    def finish(self, report):
        report.performance = dict(self.values, total_seconds=max(0.0, self.clock() - self.started),
                                  saved_output_skips=len(report.skipped_existing),
                                  saved_bodies=report.from_bodies)


def performance_summary(values):
    return (f"Performance: total {values['total_seconds']:.2f}s; "
            f"TOC/discovery {values['discovery_seconds']:.2f}s; "
            f"chapter fetching {values['fetching_seconds']:.2f}s; "
            f"PDF/TXT rendering {values['rendering_seconds']:.2f}s; "
            f"HTML cache HIT/MISS {values['cache_hits']}/{values['cache_misses']}; "
            f"actual website requests (top-level) {values['website_requests']}; "
            f"verified output skips {values['saved_output_skips']}; saved-body reuse {values['saved_bodies']}.")
