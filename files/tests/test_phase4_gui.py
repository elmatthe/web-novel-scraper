"""Phase 4 / 0.2.0 GUI-wiring tests (offline, deterministic).

These cover the §4 wiring changes: the 3.0s delay default, the accurate Headless
hint copy, the retirement of the GUI-pre-built ``RequestManager`` (the pipeline now
OWNS/replaces the active manager via the factory seam), the non-daemon scrape worker,
and the window-close lifecycle (signal cancel, poll until the worker exits, *then*
destroy) — plus the matching pipeline change that lets the legacy path derive
``use_browser`` from the job and thread the job's timeout into a self-built manager.

No live network and no real browser launch. The end-to-end checks that need a Tk
display skip automatically when none is available (e.g. headless CI); the window-close
poll is driven through an injected ``after`` so it never waits on the real clock.
"""

from __future__ import annotations

import inspect
import threading

import pytest

import app
from webnovel_scraper import pipeline
from webnovel_scraper.models import ChapterContent, ChapterMeta, OutputMode, ScrapeJob


# ── Deterministic module-level assertions (no display needed) ─────────────────
def test_delay_default_is_three() -> None:
    # §4.1: the GUI delay default rises to 3.0s for 0.2.0.
    assert app.DEFAULT_DELAY == "3.0"


def test_gui_does_not_prebuild_a_manager_or_touch_retired_global() -> None:
    """§3.15/§4: the GUI must not construct a RequestManager, import it, mutate the
    shared catalog ``SiteSpec.use_browser``, or write the retired module-global
    ``FETCH_TIMEOUT`` — the pipeline owns the manager and per-run config now."""
    src = inspect.getsource(app)
    assert "RequestManager(" not in src, "GUI must not pre-build a RequestManager"
    assert "import RequestManager" not in src, "GUI must not import RequestManager"
    assert "FETCH_TIMEOUT" not in src, "GUI must not touch the retired global"
    assert "spec.use_browser =" not in src, "GUI must not mutate the catalog SiteSpec"
    # The worker is launched non-daemon so the pipeline teardown finally runs (§4.4).
    assert "daemon=False" in src


# ── Tk-instantiating checks (skip when no display) ────────────────────────────
def _make_app():
    tk = pytest.importorskip("tkinter")
    try:
        return app.ScraperApp()
    except tk.TclError as exc:  # no display (headless CI) — not applicable here
        pytest.skip(f"no Tk display available: {exc}")


def test_headless_hint_describes_the_visible_override() -> None:
    """§4.2: the Headless hint is accurate about the breaker override + visible
    rescue browser."""
    win = _make_app()
    try:
        text = win._headless_check.cget("text").lower()
        assert "visible" in text
        assert "rest of the run" in text
        assert "rescue" in text
    finally:
        win.destroy()


def test_worker_is_non_daemon_and_passes_job_without_a_manager(monkeypatch, tmp_path) -> None:
    """§4.4 + §3.15: Start launches a NON-daemon worker that hands the pipeline the
    ScrapeJob and no pre-built ``request_manager`` (the pipeline builds its own)."""
    win = _make_app()
    # Keep all marshalled callbacks off the real Tk loop (and thread-safe).
    monkeypatch.setattr(win, "after", lambda *a, **k: None)
    started = threading.Event()
    release = threading.Event()
    captured: dict = {}

    def fake_run_scrape(job, **kwargs):
        captured["job"] = job
        captured["kwargs"] = kwargs
        started.set()
        release.wait(5)

    monkeypatch.setattr(app.pipeline, "run_scrape", fake_run_scrape)
    try:
        win._output_parent = tmp_path  # never write under the real ~/Downloads
        win._on_start()
        assert started.wait(5), "worker never reached run_scrape"

        assert win._worker is not None
        assert win._worker.daemon is False  # non-daemon: teardown finally always runs

        # The pipeline owns the manager — the GUI passes none.
        assert "request_manager" not in captured["kwargs"]
        # Config travels on the immutable job.
        job = captured["job"]
        assert isinstance(job, ScrapeJob)
        assert job.rescue_workers == 1            # single-lane (0.2.0)
        assert job.delay == 3.0                   # the new default reached the job
        assert job.request_timeout == 30          # the Timeout field
        assert job.use_browser is True            # FWN + browser box on by default
        assert job.headless is False
    finally:
        release.set()
        if win._worker is not None:
            win._worker.join(5)
        win.destroy()


def test_close_polls_until_worker_exits_then_destroys(monkeypatch) -> None:
    """§4.4: closing while running signals cancel, marks closing, and polls (on the
    injected event loop) until the worker exits — destroying only then. Driven with
    a fake worker + injected ``after``, so the test never waits on the real clock."""
    win = _make_app()

    class _FakeWorker:
        def __init__(self) -> None:
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

    real_destroy = win.destroy
    scheduled: list = []
    destroyed: list = []
    monkeypatch.setattr(win, "after", lambda ms, cb=None, *a: scheduled.append((ms, cb)))
    monkeypatch.setattr(win, "destroy", lambda: (destroyed.append(True), real_destroy()))
    monkeypatch.setattr(app.messagebox, "askokcancel", lambda *a, **k: True)

    try:
        win._running = True
        win._cancel_event = threading.Event()
        worker = _FakeWorker()
        win._worker = worker

        win._on_close()
        # Worker still alive: cancel signalled, closing latched, NOT destroyed yet,
        # and exactly one poll re-scheduled on the (injected) event loop.
        assert win._cancel_event.is_set()
        assert win._closing is True
        assert destroyed == []
        assert len(scheduled) == 1
        assert scheduled[-1][0] == app.CLOSE_POLL_MS
        assert scheduled[-1][1] == win._poll_close

        # A second close request while tearing down is ignored (idempotent).
        win._on_close()
        assert len(scheduled) == 1

        # Worker exits → the next poll tick destroys the window.
        worker.alive = False
        scheduled[-1][1]()  # step the poll manually (no real clock)
        assert destroyed == [True]
    finally:
        if not destroyed:
            real_destroy()


def test_thread_callbacks_are_safe_after_destroy() -> None:
    """A non-daemon worker can emit a final log/progress line after the window is
    gone; the marshalling callbacks must swallow that rather than crash the thread."""
    win = _make_app()
    win.destroy()
    # Must not raise even though the window is destroyed.
    win._thread_log("late line from the worker")
    win._thread_progress(3, 10)


# ── Pipeline regression for the GUI-driven config change ──────────────────────
class _RecordingRM:
    """Captures the kwargs the legacy path builds a self-owned manager with."""

    kwargs: dict = {}

    def __init__(self, **kwargs) -> None:
        _RecordingRM.kwargs = kwargs
        self.cancel_event = None

    def start(self):
        return self

    def close(self):
        pass


class _RecordingAdapter:
    """Records the ``use_browser`` of the spec the pipeline drives it with."""

    seen: list = []

    def __init__(self, *, request_manager, log) -> None:
        self.rm = request_manager
        self.warnings: list[str] = []

    def build_chapter_index(self, spec):
        _RecordingAdapter.seen.append(("toc", spec.use_browser))
        return [ChapterMeta(index=1, url="https://example/1", source_id="1")]

    def fetch_chapter(self, meta, spec):
        _RecordingAdapter.seen.append(("fetch", spec.use_browser))
        return ChapterContent(
            index=meta.index,
            title=f"Title {meta.index}",
            paragraphs=[
                "First body paragraph, long enough to render as real prose.",
                "Second body paragraph, also clearly prose and not noise.",
            ],
        )


def test_legacy_path_threads_job_config_and_derives_use_browser(monkeypatch, tmp_path) -> None:
    """With the GUI no longer pre-building a manager, an adapter-less legacy run must
    (a) derive ``use_browser`` from the job (not the catalog row, which is True for
    FWN) and (b) thread the job's timeout/headless into the manager it builds."""
    _RecordingAdapter.seen = []
    _RecordingRM.kwargs = {}
    monkeypatch.setitem(pipeline.REGISTRY, "freewebnovel", _RecordingAdapter)
    monkeypatch.setattr(pipeline, "RequestManager", _RecordingRM)

    job = ScrapeJob(
        novel_slug="shadow-slave",
        adapter_key="freewebnovel",
        start=1,
        end=1,
        delay=0.0,
        output_mode=OutputMode.SEPARATE,
        use_cache=False,
        output_dir=tmp_path,
        use_browser=False,   # browser box UNCHECKED → legacy HTTP path
        headless=True,
        request_timeout=12.0,
    )
    report = pipeline.run_scrape(
        job,
        log=lambda _m: None,
        cancel_event=threading.Event(),
    )

    # use_browser came from the job (False), not the catalog SiteSpec (True).
    assert _RecordingAdapter.seen, "adapter was never driven"
    assert all(flag is False for _kind, flag in _RecordingAdapter.seen)
    # The self-built manager honoured the job's timeout/headless/cache/http_first.
    assert _RecordingRM.kwargs["http_timeout"] == 12.0
    assert _RecordingRM.kwargs["headless"] is True
    assert _RecordingRM.kwargs["use_cache"] is False
    assert _RecordingRM.kwargs["try_http_first"] is False
    # And the run actually produced output (one SEPARATE PDF for the chapter).
    assert len(report.written) == 1
