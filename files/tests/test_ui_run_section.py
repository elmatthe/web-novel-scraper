"""v0.3.0 Checkpoint 02.2: the Run section in the real window.

Phase, "done / total · %", counters, ETA and the final state, fed by the same events the
Activity pump delivers. Offline and deterministic: the worker is a fake ``run_scrape`` held
step by step with events (never with sleeps), the ETA clock is injected, and the queue is
drained with ``flush_activity()``. Every test builds a real window and skips when no Tk
display exists.
"""

from __future__ import annotations

import gc
import threading
from pathlib import Path

import pytest

import app
from webnovel_scraper import pipeline
from webnovel_scraper.ui import run_activity as ra
from webnovel_scraper.ui import run_progress as rpg
from webnovel_scraper.ui.activity import ActivityEvent
from webnovel_scraper.ui.appearance import DARK_COLORS, LIGHT_COLORS
from webnovel_scraper.ui.run_progress import FinalCounts
from webnovel_scraper.registry import AdapterDisabledError


def _make_app(**kwargs):
    tk = pytest.importorskip("tkinter")
    gc.collect()  # see test_ui_activity_panel._make_app: no Tk object may die on a worker
    try:
        return app.ScraperApp(**kwargs)
    except tk.TclError as exc:
        pytest.skip(f"no Tk display available: {exc}")


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t


def _view(win) -> tuple[str, str, str, str, str]:
    return (win._run_status_var.get(), win._counts_var.get(), win._detail_var.get(),
            win._eta_var.get(), str(win._progress.cget("mode")))


def _begin(win) -> None:
    """The window side of Start, without a worker: a run that is preparing."""
    win._tracker.start()
    win._render_run()


def _finish(win, outcome, processed=None, counts=None) -> None:
    win._events.emit(ra.finished_event(outcome, processed, counts))
    win.flush_activity()


# ------------------------------------------------------------------------------ structure

def test_the_run_section_starts_idle_and_compact() -> None:
    win = _make_app()
    try:
        assert _view(win) == ("Idle", "—", "", "ETA: —", "determinate")
        assert float(str(win._progress.cget("value"))) == 0.0
        info = win._counts_label.master
        assert info.winfo_manager() == "grid"
        # Two rows only: status/counts and detail/ETA.
        assert {w.grid_info()["row"] for w in info.winfo_children()} == {0, 1}
        assert win._run_status_label.winfo_manager() == "grid"
    finally:
        win.destroy()


def test_three_numbered_sections_and_the_activity_column_survive() -> None:
    win = _make_app()
    try:
        assert len(win._sections) == 3
        min_w, min_h = win.geometry_info["minimum"]
        win.update()
        left = win._left.winfo_width()
        win.geometry(f"{min_w + 260}x{min_h + 100}")
        win.update()
        if (win.winfo_width(), win.winfo_height()) != (min_w + 260, min_h + 100):
            pytest.skip("this display cannot fit the enlarged test window")
        assert win._left.winfo_width() == left          # Activity takes the growth
    finally:
        win.destroy()


# ------------------------------------------------------------------------------ lifecycle

def test_the_run_section_follows_preparing_scraping_building_complete(monkeypatch, tmp_path) -> None:
    win = _make_app()
    gates = {name: threading.Event() for name in ("one", "two", "three")}
    reached = {name: threading.Event() for name in ("prepare", "scrape", "build")}

    def fake_run_scrape(job, *, log, cancel_event, progress_cb, phase_cb=None):
        reached["prepare"].set()
        gates["one"].wait(5)
        phase_cb("scraping", 4, 0)
        progress_cb(1, 4)
        reached["scrape"].set()
        gates["two"].wait(5)
        progress_cb(4, 4)
        phase_cb("building", 4, 0)
        reached["build"].set()
        gates["three"].wait(5)
        return pipeline.RunReport(output_dir=job.output_dir, written=[Path("a.pdf")])

    monkeypatch.setattr(app.pipeline, "run_scrape", fake_run_scrape)
    try:
        win._output_parent = tmp_path
        win._on_start()
        assert reached["prepare"].wait(5)
        win.flush_activity()
        # No chapter total is known: an honest, indeterminate bar and no percentage.
        assert _view(win) == ("Preparing chapter list…", "—", "", "ETA: —", "indeterminate")

        gates["one"].set()
        assert reached["scrape"].wait(5)
        win.flush_activity()
        assert _view(win)[:2] == ("Scraping chapters", "1 / 4 · 25%")
        assert _view(win)[4] == "determinate"
        assert float(str(win._progress.cget("maximum"))) == 4.0
        assert float(str(win._progress.cget("value"))) == 1.0

        gates["two"].set()
        assert reached["build"].wait(5)
        win.flush_activity()
        assert _view(win)[:2] == ("Building output…", "4 / 4 · 100%")

        gates["three"].set()
        win._worker.join(5)
        win.flush_activity()
        assert _view(win) == ("Complete", "4 / 4 · 100%", "1 PDF", "", "determinate")
        assert win._running is False
        assert "Completed: 1 PDF written and no chapter failures recorded." in (
            win._log_texts["Summary"].get("1.0", "end"))
    finally:
        for gate in gates.values():
            gate.set()
        if win._worker is not None:
            win._worker.join(5)
        win.destroy()


def test_the_final_outcome_overrides_queued_routine_progress(monkeypatch, tmp_path) -> None:
    win = _make_app()

    def fake_run_scrape(job, *, log, cancel_event, progress_cb, phase_cb=None):
        phase_cb("scraping", 3, 0)
        for done in (1, 2, 3):
            progress_cb(done, 3)       # coalesced into one pending update
        return pipeline.RunReport(output_dir=job.output_dir, written=[Path("a.pdf")], failed=[2])

    monkeypatch.setattr(app.pipeline, "run_scrape", fake_run_scrape)
    try:
        win._output_parent = tmp_path
        win._on_start()
        win._worker.join(5)
        win.flush_activity()   # one drain: progress, then the outcome, then the finish
        assert _view(win) == ("Finished with problems", "3 / 3 · 100%",
                              "1 PDF · 1 failed", "", "determinate")
        before = _view(win)
        # A routine update that turns up after the run's end can never undo it.
        win._events.emit(ra.progress_event(1, 3))
        win._events.emit(ra.phase_event("scraping", 3, 2))
        win.flush_activity()
        assert _view(win) == before
    finally:
        win.destroy()


def test_100_percent_with_failures_is_a_warning_not_a_success_in_both_themes(tmp_path) -> None:
    win = _make_app()
    try:
        _begin(win)
        _finish(win, "completed_with_problems", (40, 40), FinalCounts(pdfs=38, failed=2))
        label = win._run_status_label

        def colour() -> str:
            return str(win._style.lookup(str(label.cget("style")), "foreground"))

        assert win._counts_var.get() == "40 / 40 · 100%"
        assert colour() == LIGHT_COLORS["warning"]
        win.toggle_appearance()
        win.update()
        assert colour() == DARK_COLORS["warning"]
        assert _view(win)[:3] == ("Finished with problems", "40 / 40 · 100%", "38 PDFs · 2 failed")
        win.toggle_appearance()

        _begin(win)
        _finish(win, "failed", (40, 40))
        assert colour() == LIGHT_COLORS["danger"] and win._run_status_var.get() == "Run failed"
        win.toggle_appearance()
        win.update()
        assert colour() == DARK_COLORS["danger"]

        _begin(win)
        _finish(win, "completed", (40, 40), FinalCounts(pdfs=40))
        plain = str(win._style.lookup(win._bundle["styles"]["secondary_label"], "foreground"))
        assert colour() == plain != DARK_COLORS["danger"]   # success is not coloured as a problem
    finally:
        win.destroy()


def test_theme_switch_during_a_run_keeps_the_run_section() -> None:
    win = _make_app()
    try:
        _begin(win)
        win._events.emit(ra.phase_event("scraping", 40, 5, at=0.0))
        win._events.emit(ra.progress_event(12, 40, at=1.0))
        win.flush_activity()
        before = _view(win)
        assert before[:3] == ("Scraping chapters", "12 / 40 · 30%", "5 already present")
        win.toggle_appearance()
        win.update()
        assert _view(win) == before
        win.toggle_appearance()
    finally:
        win.destroy()


# ----------------------------------------------------------------------------------- ETA

def test_the_eta_appears_with_enough_progress_and_is_unknown_again_after_a_stall() -> None:
    win = _make_app()
    clock = _Clock()
    win._tracker = rpg.RunTracker(clock=clock.now)
    try:
        _begin(win)
        win._events.emit(ra.phase_event("scraping", 40, 0, at=0.0))
        for n in (1, 2):
            win._events.emit(ra.progress_event(n, 40, at=10.0 * n))
        clock.t = 20.0
        win.flush_activity()
        assert win._eta_var.get() == "ETA: —"           # two chapters are not enough evidence
        win._events.emit(ra.progress_event(3, 40, at=30.0))
        clock.t = 30.0
        win.flush_activity()
        assert win._eta_var.get() == "ETA: 6 min 10 s"
        # A retry or recovery drags on: any redraw turns the stale estimate back into "—".
        clock.t = 30.0 + 41.0
        win._events.emit(ActivityEvent("pipeline", "  retrying …"))
        win.flush_activity()
        assert win._eta_var.get() == "ETA: —"
    finally:
        win.destroy()


def test_the_eta_goes_when_the_run_stops_and_when_it_ends() -> None:
    win = _make_app()
    clock = _Clock()
    win._tracker = rpg.RunTracker(clock=clock.now)
    try:
        _begin(win)
        win._events.emit(ra.phase_event("scraping", 40, 0, at=0.0))
        for n in (1, 2, 3):
            win._events.emit(ra.progress_event(n, 40, at=10.0 * n))
        clock.t = 30.0
        win.flush_activity()
        assert win._eta_var.get() != "ETA: —"
        win._tracker.stop_requested()
        win._render_run()
        assert win._eta_var.get() == "ETA: —"
        _finish(win, "stopped", (3, 40), FinalCounts(pdfs=3))
        assert _view(win) == ("Stopped", "3 / 40 · 7%", "3 PDFs", "", "determinate")
    finally:
        win.destroy()


# ------------------------------------------------------------------------- stop and close

def test_stop_while_the_chapter_list_is_prepared(monkeypatch, tmp_path) -> None:
    win = _make_app()
    reached = threading.Event()

    def fake_run_scrape(job, *, log, cancel_event, progress_cb, phase_cb=None):
        reached.set()
        cancel_event.wait(5)
        return pipeline.RunReport(output_dir=job.output_dir, cancelled=True)

    monkeypatch.setattr(app.pipeline, "run_scrape", fake_run_scrape)
    try:
        win._output_parent = tmp_path
        win._on_start()
        assert reached.wait(5)
        win._on_stop()
        assert _view(win) == ("Stopping…", "—", "", "ETA: —", "indeterminate")
        win._worker.join(5)
        win.flush_activity()
        assert _view(win) == ("Stopped", "—", "0 PDFs", "", "determinate")
        assert win._running is False
    finally:
        win.destroy()


def test_stop_while_scraping_says_after_the_current_chapter(monkeypatch, tmp_path) -> None:
    win = _make_app()
    reached = threading.Event()

    def fake_run_scrape(job, *, log, cancel_event, progress_cb, phase_cb=None):
        phase_cb("scraping", 10, 0)
        progress_cb(3, 10)
        reached.set()
        cancel_event.wait(5)
        return pipeline.RunReport(output_dir=job.output_dir, cancelled=True,
                                  written=[Path("a.pdf")] * 3)

    monkeypatch.setattr(app.pipeline, "run_scrape", fake_run_scrape)
    try:
        win._output_parent = tmp_path
        win._on_start()
        assert reached.wait(5)
        win.flush_activity()
        win._on_stop()
        assert win._run_status_var.get() == "Stopping after the current chapter…"
        win._worker.join(5)
        win.flush_activity()
        assert _view(win) == ("Stopped", "3 / 10 · 30%", "3 PDFs", "", "determinate")
        summary = win._log_texts["Summary"].get("1.0", "end")
        assert "Stopped before the end of the selected range, as requested." in summary
    finally:
        win.destroy()


def test_closing_keeps_its_own_status_while_events_still_arrive(monkeypatch, tmp_path) -> None:
    win = _make_app()
    reached, release = threading.Event(), threading.Event()

    def fake_run_scrape(job, *, log, cancel_event, progress_cb, phase_cb=None):
        phase_cb("scraping", 10, 0)
        reached.set()
        cancel_event.wait(5)
        progress_cb(4, 10)
        release.wait(5)
        return pipeline.RunReport(output_dir=job.output_dir, cancelled=True)

    monkeypatch.setattr(app.pipeline, "run_scrape", fake_run_scrape)
    monkeypatch.setattr(win, "after", lambda *a, **k: None)   # no real close poll
    monkeypatch.setattr(app.messagebox, "askokcancel", lambda *a, **k: True)
    try:
        win._output_parent = tmp_path
        win._on_start()
        assert reached.wait(5)
        win._on_close()
        assert win._run_status_var.get() == "Closing after the current chapter…"
        deadline = threading.Event()
        for _ in range(200):
            win.flush_activity()
            if win._counts_var.get() == "4 / 10 · 40%":
                break
            deadline.wait(0.01)
        assert win._counts_var.get() == "4 / 10 · 40%"
        assert win._run_status_var.get() == "Closing after the current chapter…"
        release.set()
        win._worker.join(5)
        win.flush_activity()
        assert win._run_status_var.get() == "Closing after the current chapter…"
        assert str(win._start_btn.cget("state")) == "disabled"
    finally:
        release.set()
        if win._worker is not None:
            win._worker.join(5)
        win.destroy()


# --------------------------------------------------------------------- failure and refusal

def test_an_unexpected_error_is_failed_and_a_disabled_site_is_refused(monkeypatch, tmp_path) -> None:
    win = _make_app()
    outcomes = iter([RuntimeError("boom"), AdapterDisabledError("site is disabled")])

    def fake_run_scrape(job, *, log, cancel_event, progress_cb, phase_cb=None):
        phase_cb("scraping", 3, 0)
        progress_cb(2, 3)
        raise next(outcomes)

    monkeypatch.setattr(app.pipeline, "run_scrape", fake_run_scrape)
    try:
        win._output_parent = tmp_path
        win._on_start()
        win._worker.join(5)
        win.flush_activity()
        assert _view(win) == ("Run failed", "2 / 3 · 66%", "", "", "determinate")

        win._on_start()
        win._worker.join(5)
        win.flush_activity()
        assert win._run_status_var.get() == "Run refused"
        assert win._counts_var.get() == "2 / 3 · 66%"   # this (fake) run reported 2 of 3 first
        assert win._running is False
    finally:
        win.destroy()


def test_a_new_run_starts_from_nothing(monkeypatch, tmp_path) -> None:
    win = _make_app()
    gate = threading.Event()
    calls = []

    def fake_run_scrape(job, *, log, cancel_event, progress_cb, phase_cb=None):
        calls.append(1)
        if len(calls) == 1:
            phase_cb("scraping", 5, 2)
            progress_cb(5, 5)
            return pipeline.RunReport(output_dir=job.output_dir, skipped_existing=[1, 2],
                                      written=[Path("a.pdf")])
        gate.wait(5)
        return pipeline.RunReport(output_dir=job.output_dir, cancelled=True)

    monkeypatch.setattr(app.pipeline, "run_scrape", fake_run_scrape)
    try:
        win._output_parent = tmp_path
        win._on_start()
        win._worker.join(5)
        win.flush_activity()
        assert _view(win)[:3] == ("Complete", "5 / 5 · 100%", "1 PDF · 2 already present")
        win._on_start()
        assert _view(win) == ("Preparing chapter list…", "—", "", "ETA: —", "indeterminate")
        gate.set()
        win._worker.join(5)
        win.flush_activity()
    finally:
        gate.set()
        win.destroy()


def test_destroying_the_window_while_the_bar_is_indeterminate_is_clean(monkeypatch, tmp_path) -> None:
    win = _make_app()
    reached = threading.Event()

    def fake_run_scrape(job, *, log, cancel_event, progress_cb, phase_cb=None):
        reached.set()
        cancel_event.wait(5)
        return pipeline.RunReport(output_dir=job.output_dir, cancelled=True)

    monkeypatch.setattr(app.pipeline, "run_scrape", fake_run_scrape)
    win._output_parent = tmp_path
    win._on_start()
    assert reached.wait(5)
    win.flush_activity()
    assert str(win._progress.cget("mode")) == "indeterminate"
    win._cancel_event.set()
    win._worker.join(5)
    win.destroy()
    assert win._shut_down
    win._render_run()                      # a late call is a no-op, not a TclError
    win._events.emit(ra.progress_event(1, 2))
    assert win._events.pump_all() == 0


# --------------------------------------------------------------- worker thread and layout

def test_phase_and_progress_callbacks_touch_no_widget_off_the_tk_thread(monkeypatch) -> None:
    win = _make_app()
    touched: list[tuple[str, bool]] = []

    def spy(obj, name: str) -> None:
        original = getattr(obj, name)

        def wrapper(*args, **kwargs):
            touched.append((name, threading.current_thread() is threading.main_thread()))
            return original(*args, **kwargs)

        monkeypatch.setattr(obj, name, wrapper)

    for obj, name in [(win, "after"), (win, "after_idle"), (win, "event_generate"),
                      (win._progress, "configure"), (win._progress, "start"),
                      (win._progress, "stop"), (win._run_status_var, "set"),
                      (win._counts_var, "set"), (win._detail_var, "set"),
                      (win._eta_var, "set"), (win._run_status_label, "configure")]:
        spy(obj, name)
    try:
        _begin(win)
        touched.clear()

        def worker() -> None:
            win._thread_phase("scraping", 500, 0)
            for done in range(1, 500):
                win._thread_progress(done, 500)
                if done % 100 == 0:
                    win._thread_phase("scraping", 500, done // 100)
            win._thread_phase("building", 500, 0)
            win._events.emit(ra.finished_event("completed", (500, 500), FinalCounts(pdfs=1)))

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(10)
        assert touched == []                           # nothing at all from the worker thread
        win.flush_activity()
        assert touched and all(on_main for _, on_main in touched)
        assert _view(win)[:3] == ("Complete", "500 / 500 · 100%", "1 PDF")
    finally:
        win.destroy()


def test_long_run_texts_never_widen_the_window_and_fit_at_the_minimum() -> None:
    win = _make_app()
    try:
        min_w, min_h = win.geometry_info["minimum"]
        win.geometry(f"{min_w}x{min_h}")
        win.update()
        assert (win.winfo_width(), win.winfo_height()) == (min_w, min_h)
        win.update_idletasks()
        base = (win.winfo_reqwidth(), win.winfo_reqheight())
        long_text = "Finished with problems — an unusually long status " * 4
        win._run_status_var.set(long_text)
        # The counters wrap onto further lines (02.3) instead of clipping, within the
        # height the minimum already reserves for their longest form.
        win._detail_var.set(app.WORST_CASE_DETAIL)
        win._eta_var.set("")
        win._wrap_run_detail(with_eta=False)
        win._counts_var.set("99999 / 99999 · 100%")
        win.update()
        assert (win.winfo_reqwidth(), win.winfo_reqheight()) == base
        win._eta_var.set("ETA: 123 h 59 min")
        win._detail_var.set("120 already present")
        win._wrap_run_detail(with_eta=True)
        win.update()
        assert (win.winfo_reqwidth(), win.winfo_reqheight()) == base
        right = win.winfo_rootx() + win.winfo_width()
        bottom = win.winfo_rooty() + win.winfo_height()
        for widget in (win._run_status_label, win._counts_label, win._detail_label,
                       win._eta_label, win._progress):
            assert widget.winfo_ismapped(), widget
            assert widget.winfo_rootx() + widget.winfo_width() <= right, widget
            assert widget.winfo_rooty() + widget.winfo_height() <= bottom, widget
        # The right-hand cells keep their text; only the left cells give way.
        assert win._counts_label.winfo_width() >= win._counts_label.winfo_reqwidth()
        assert win._eta_label.winfo_width() >= win._eta_label.winfo_reqwidth()
        assert (win.winfo_width(), win.winfo_height()) == (min_w, min_h)
    finally:
        win.destroy()


def test_the_run_section_fits_in_dark_mode_at_the_minimum_too() -> None:
    win = _make_app()
    try:
        win.toggle_appearance()
        min_w, min_h = win.geometry_info["minimum"]
        win.geometry(f"{min_w}x{min_h}")
        _begin(win)
        win._events.emit(ra.phase_event("scraping", 3664, 10, at=0.0))
        win._events.emit(ra.progress_event(3664, 3664, at=1.0))
        win.flush_activity()
        win.update()
        bottom = win.winfo_rooty() + win.winfo_height()
        for widget in (win._counts_label, win._detail_label, win._eta_label, win._progress,
                       win._activity):
            assert widget.winfo_rooty() + widget.winfo_height() <= bottom, widget
        assert win._counts_var.get() == "3664 / 3664 · 100%"
        assert win._counts_label.winfo_width() >= win._counts_label.winfo_reqwidth()
    finally:
        win.destroy()
