"""v0.3.0 Checkpoint 02.1: the Activity event infrastructure. Offline, no Tk.

``webnovel_scraper.ui.activity`` is adapted from PDF-Splitter: a bounded, locked channel
from worker threads, a Summary/Detailed display model, a per-session technical log and a
pump driven by an ``after``-shaped scheduler. These tests drive it with plain callables
and real threads, never a clock: a fake scheduler records ticks and the test runs them.
"""

from __future__ import annotations

import inspect
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from webnovel_scraper.ui import activity as act
from webnovel_scraper.ui import settings as ui_settings
from webnovel_scraper.ui.activity import (
    ERROR,
    INFO,
    WARNING,
    Activity,
    ActivityChannel,
    ActivityEvent,
    ActivityModel,
    ActivityPump,
    SessionLog,
    format_event,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXED = datetime(2026, 10, 18, 9, 30, 5)


def _clock() -> datetime:
    return FIXED


def _ev(message: str, severity: str = INFO, summary: bool = False, **kwargs) -> ActivityEvent:
    return ActivityEvent("test", message, severity, summary, **kwargs)


def _job(payload: object, **kwargs) -> ActivityEvent:
    return ActivityEvent("test", kind="job", payload=payload, **kwargs)


class _Views:
    """Applies RenderUpdates the way the window's two Text views do."""

    def __init__(self) -> None:
        self.summary: list[str] = []
        self.detailed: list[str] = []
        self.severities: dict[str, str] = {}
        self.updates = 0

    def __call__(self, update) -> None:
        self.updates += 1
        if update.cleared:
            self.summary.clear()
            self.detailed.clear()
        for line in update.summary:
            self.summary.append(line.text)
        for line in update.detailed:
            self.detailed.append(line.text)
            self.severities[line.text] = line.severity


def _lines(log: SessionLog) -> list[str]:
    return log.path.read_text(encoding="utf-8").splitlines()


def _open_log(tmp_path) -> SessionLog:
    log = SessionLog(tmp_path / "logs", clock=_clock)
    assert log.open()
    return log


# ── Locations ────────────────────────────────────────────────────────────────
def test_session_logs_live_in_the_ignored_runtime_folder() -> None:
    # conftest redirects LOGS_DIR per test; the shipped value is defined beside the settings.
    assert 'LOGS_DIR = RUNTIME_DIR / "logs"' in inspect.getsource(ui_settings)
    assert ui_settings.RUNTIME_DIR == REPO_ROOT / "files" / "runtime-data"
    ignored = [line.strip() for line in
               (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert "files/runtime-data/" in ignored  # covers logs/


def test_the_suite_never_writes_the_real_logs_folder(tmp_path) -> None:
    assert ui_settings.LOGS_DIR != ui_settings.RUNTIME_DIR / "logs"
    assert ui_settings.LOGS_DIR.is_relative_to(tmp_path)


def test_the_module_imports_no_tk() -> None:
    source = inspect.getsource(act)
    assert "import tkinter" not in source and "from tkinter" not in source


# ── Display text ─────────────────────────────────────────────────────────────
def test_warnings_and_errors_are_labelled_in_words() -> None:
    assert format_event(_ev("all good")).text == "all good"
    assert format_event(_ev("site slow", WARNING)).text == "Warning: site slow"
    assert format_event(_ev("crashed", ERROR)).text == "Error: crashed"
    # Never doubled when the pipeline already says so.
    assert format_event(_ev("WARNING: toc gap", WARNING)).text == "WARNING: toc gap"
    # ...but a sentence that merely starts with the word is still labelled.
    assert format_event(_ev("warning lights", WARNING)).text == "Warning: warning lights"
    # A raw pipeline line keeps its indentation in front of the label.
    assert format_event(_ev("  chapter 5 FAILED: 403", WARNING)).text == "  Warning: chapter 5 FAILED: 403"


def test_summary_events_reach_both_views_and_routine_detail_only_detailed() -> None:
    model = ActivityModel()
    assert model.add(_ev("  fetch attempt 1/3")) == (None, act.ActivityLine("  fetch attempt 1/3"))
    s_line, d_line = model.add(_ev("  chapter 5 FAILED: 403", WARNING, summary=True))
    assert s_line.text == "Warning: chapter 5 FAILED: 403"  # Summary drops the step indent
    assert d_line.text == "  Warning: chapter 5 FAILED: 403"
    s_line, _ = model.add(ActivityEvent("run", "-----", kind="separator"))
    assert s_line is not None  # run dividers appear in both views
    assert [line.text for line in model.summary] == ["Warning: chapter 5 FAILED: 403", "-----"]
    assert len(model.detailed) == 3


def test_display_history_is_capped_per_view() -> None:
    model = ActivityModel(line_cap=3)
    for i in range(5):
        model.add(_ev(f"line {i}", summary=True))
    assert [line.text for line in model.detailed] == ["line 2", "line 3", "line 4"]
    assert [line.text for line in model.summary] == ["line 2", "line 3", "line 4"]


# ── Channel: bounded, coalescing, never losing what matters ──────────────────
def test_channel_drops_only_routine_detail_and_counts_it() -> None:
    channel = ActivityChannel(max_pending=3)
    assert all(channel.emit(_ev(f"routine {i}")) for i in range(3))
    assert channel.emit(_ev("routine 3")) is False
    assert channel.dropped == 1
    # Full, yet none of these may be dropped.
    for event in (_ev("warn", WARNING), _ev("err", ERROR), _ev("milestone", summary=True),
                  ActivityEvent("run", "---", kind="separator"), _job("finished")):
        assert channel.emit(event) is True
    assert channel.dropped == 1
    assert [e.message or e.payload for e in channel.drain(None)] == [
        "routine 0", "routine 1", "routine 2", "warn", "err", "milestone", "---", "finished"]


def test_progress_coalesces_in_place_and_keeps_the_newest() -> None:
    channel = ActivityChannel(max_pending=2)
    channel.emit(_job((1, 10), coalesce_key="progress"))
    channel.emit(_ev("line A"))
    for done in range(2, 11):  # the channel is full, but a keyed event is never dropped
        assert channel.emit(_job((done, 10), coalesce_key="progress"))
    channel.emit(_job("finished"))
    drained = channel.drain(None)
    assert [e.payload or e.message for e in drained] == [(10, 10), "line A", "finished"]
    assert channel.coalesced == 9 and channel.dropped == 0
    # Once drained, the key starts a fresh entry.
    channel.emit(_job((1, 3), coalesce_key="progress"))
    assert channel.pending() == 1


def test_drain_is_bounded_and_ordered() -> None:
    channel = ActivityChannel()
    for i in range(10):
        channel.emit(_ev(f"{i}", summary=True))
    assert [e.message for e in channel.drain(4)] == ["0", "1", "2", "3"]
    assert [e.message for e in channel.drain(None)] == [str(i) for i in range(4, 10)]


def test_closed_channel_refuses_events() -> None:
    channel = ActivityChannel()
    channel.emit(_ev("pending"))
    channel.close()
    assert channel.pending() == 0
    assert channel.emit(_ev("late", ERROR)) is False


def test_channel_rejects_a_zero_capacity() -> None:
    with pytest.raises(ValueError):
        ActivityChannel(max_pending=0)


# ── Session log ──────────────────────────────────────────────────────────────
def test_session_log_is_one_named_utf8_file_with_timestamped_lines(tmp_path) -> None:
    log = _open_log(tmp_path)
    assert log.path == tmp_path / "logs" / "session-20261018-093005.log"
    assert log.write(_ev("Lord of Mysteries — chapter “1” ✓", WARNING))
    log.write(ActivityEvent("pipeline", "Run complete.\n  Written: 2 PDF(s)"))
    log.close()
    lines = _lines(log)
    assert lines[0].startswith("2026-10-18 09:30:05 [info] Web Novel Scraper session started (Python ")
    assert lines[1] == "2026-10-18 09:30:05 [warning] test: Lord of Mysteries — chapter “1” ✓"
    assert lines[2] == "2026-10-18 09:30:05 [info] pipeline: Run complete."
    assert lines[3] == "      Written: 2 PDF(s)"  # continuation lines stay under their entry
    assert lines[-1] == "2026-10-18 09:30:05 [info] Web Novel Scraper session closed"
    assert not log.is_open


def test_two_sessions_in_the_same_second_get_separate_files(tmp_path) -> None:
    first, second = _open_log(tmp_path), _open_log(tmp_path)
    assert first.path != second.path
    assert second.path.name == "session-20261018-093005-2.log"
    first.close()
    second.close()


def test_secrets_are_redacted_and_huge_messages_capped(tmp_path) -> None:
    log = _open_log(tmp_path)
    log.write(_ev("request failed; Cookie: cf_clearance=abc123; __cf_bm=zzz"))
    log.write(_ev("retry with cf_clearance=SECRETVALUE and __cf_bm=OTHER, url ok"))
    log.write(_ev("Authorization: Bearer topsecret"))
    log.write(_ev("x" * (act.MAX_LOGGED_CHARS + 50)))
    log.close()
    text = log.path.read_text(encoding="utf-8")
    for secret in ("abc123", "zzz", "SECRETVALUE", "OTHER", "topsecret"):
        assert secret not in text
    assert "Cookie: [redacted]" in text and "cf_clearance=[redacted]" in text
    assert "url ok" in text  # only the values are removed
    assert "[truncated 50 characters]" in text


def test_an_unwritable_log_folder_fails_once_without_raising(tmp_path) -> None:
    blocker = tmp_path / "logs"
    blocker.write_text("a file where the logs folder should be", encoding="utf-8")
    log = SessionLog(blocker, clock=_clock)
    assert log.open() is False
    assert log.failed and log.path is None and log.last_error
    assert log.write(_ev("anything")) is False
    assert log.open() is False  # no retry storm
    log.close()  # harmless


def test_a_failing_write_stops_logging_instead_of_raising(tmp_path) -> None:
    log = _open_log(tmp_path)

    class _Broken:
        def write(self, _text):
            raise OSError(28, "No space left on device")

        def close(self):
            raise OSError("still broken")

    log._handle = _Broken()
    assert log.write(_ev("lost line")) is False
    assert log.failed and "No space left" in log.last_error
    assert log.write(_ev("later line")) is False
    log.close()


def test_concurrent_writes_from_many_threads_stay_whole(tmp_path) -> None:
    log = _open_log(tmp_path)
    barrier = threading.Barrier(8)

    def writer(n: int) -> None:
        barrier.wait()
        for i in range(200):
            log.write(_ev(f"thread {n} line {i} " + "x" * 40))

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    log.close()
    body = [line for line in _lines(log) if "] test: thread " in line]
    assert len(body) == 8 * 200
    assert all(line.endswith("x" * 40) for line in body)  # no interleaved fragments


# ── Activity: routing, order, failures, Clear Log ────────────────────────────
def test_emit_is_queued_until_the_ui_thread_pumps(tmp_path) -> None:
    views = _Views()
    log = _open_log(tmp_path)
    activity = Activity(session_log=log, on_render=views)
    worker = threading.Thread(target=lambda: [
        activity.emit(_ev("milestone", summary=True)), activity.emit(_ev("  fetch detail"))])
    worker.start()
    worker.join(5)
    assert views.updates == 0  # nothing drawn from the worker thread
    assert activity.pump() == 2
    assert views.summary == ["milestone"]
    assert views.detailed == ["milestone", "  fetch detail"]
    # Written when emitted, before the window drew anything.
    assert [line.split("] ")[1] for line in _lines(log)[1:]] == ["test: milestone", "test:   fetch detail"]


def test_report_refuses_other_threads() -> None:
    activity = Activity()
    errors = []

    def call() -> None:
        try:
            activity.report(_ev("from a worker"))
        except RuntimeError as exc:
            errors.append(exc)

    thread = threading.Thread(target=call)
    thread.start()
    thread.join(5)
    assert errors and "UI thread" in str(errors[0])


def test_report_shows_everything_queued_before_it() -> None:
    views = _Views()
    activity = Activity(on_render=views)
    activity.emit(_ev("worker line 1", summary=True))
    activity.emit(_ev("worker line 2", summary=True))
    activity.report(_ev("Stop requested", summary=True))
    assert views.summary == ["worker line 1", "worker line 2", "Stop requested"]


def test_lines_before_a_job_event_are_drawn_before_it_runs() -> None:
    views = _Views()
    seen = []

    def sink(event) -> None:
        seen.append((event.payload, list(views.detailed)))
        if event.payload == "finished":  # the window reports while handling a job event
            activity.report(_ev("handled finish", summary=True))

    activity = Activity(on_render=views, job_sink=sink)
    activity.emit(_ev("before"))
    activity.emit(_job("finished"))
    activity.emit(_ev("after"))
    activity.pump_all()
    assert seen == [("finished", ["before"])]
    assert views.detailed == ["before", "handled finish", "after"]


def test_session_log_order_matches_emission_across_threads(tmp_path) -> None:
    log = _open_log(tmp_path)
    activity = Activity(session_log=log)
    activity.report(_ev("ui: run started", summary=True))
    worker = threading.Thread(target=lambda: activity.emit(_ev("worker: chapter 1")))
    worker.start()
    worker.join(5)
    activity.report(_ev("ui: stop requested", summary=True))
    activity.close()
    messages = [line.split("] test: ")[1] for line in _lines(log) if "] test: " in line]
    assert messages == ["ui: run started", "worker: chapter 1", "ui: stop requested"]


def test_clear_log_empties_the_views_but_keeps_the_session_log(tmp_path) -> None:
    views = _Views()
    log = _open_log(tmp_path)
    activity = Activity(session_log=log, on_render=views)
    activity.report(_ev("before clear", summary=True))
    activity.emit(_ev("queued before clear"))
    activity.clear_display()
    assert views.summary == [] and views.detailed == []
    assert not activity.model.summary and not activity.model.detailed
    activity.pump_all()
    activity.report(_ev("after clear", summary=True))
    assert views.summary == ["after clear"]
    assert views.detailed == ["queued before clear", "after clear"]
    activity.close()
    text = log.path.read_text(encoding="utf-8")
    for message in ("before clear", "queued before clear", "after clear"):
        assert message in text


def test_a_log_failure_is_one_summary_warning_never_written_back(tmp_path) -> None:
    views = _Views()
    log = _open_log(tmp_path)
    activity = Activity(session_log=log, on_render=views)

    class _Broken:
        def write(self, _text):
            raise OSError(13, "Permission denied")

        def close(self):
            pass

    log._handle = _Broken()
    thread = threading.Thread(target=lambda: [activity.emit(_ev(f"line {i}")) for i in range(3)])
    thread.start()
    thread.join(5)
    activity.pump_all()
    activity.report(_ev("later", summary=True))
    warnings = [line for line in views.summary if "session log could not be written" in line]
    assert len(warnings) == 1 and "Permission denied" in warnings[0]
    assert views.detailed[:3] == ["line 0", "line 1", "line 2"]  # the run's lines still show


def test_an_unavailable_log_is_announced_once_at_startup(tmp_path) -> None:
    views = _Views()
    blocker = tmp_path / "logs"
    blocker.write_text("not a folder", encoding="utf-8")
    log = SessionLog(blocker, clock=_clock)
    activity = Activity(session_log=log, on_render=views)
    assert log.open() is False
    activity.note_log_open_failure()
    activity.note_log_open_failure()
    activity.report(_ev("the window still works", summary=True))
    assert len([s for s in views.summary if "could not be created" in s]) == 1
    assert views.summary[-1] == "the window still works"


def test_display_drops_are_announced_and_the_log_keeps_every_line(tmp_path) -> None:
    views = _Views()
    log = _open_log(tmp_path)
    activity = Activity(session_log=log, on_render=views, max_pending=10)
    for i in range(25):
        activity.emit(_ev(f"routine {i}"))
    activity.pump_all()
    assert activity.channel.dropped == 15
    notice = [line for line in views.detailed if "left out of this view" in line]
    assert len(notice) == 1 and notice[0].startswith("Warning: 15 routine detail line(s)")
    assert "session log has every line" in notice[0]
    activity.close()
    text = log.path.read_text(encoding="utf-8")
    assert all(f"routine {i}\n" in text for i in range(25))


def test_completion_is_never_lost_under_load(tmp_path) -> None:
    """A worker floods routine lines and progress while a tiny queue is drained in
    small slices: every warning, the final progress and exactly one finish arrive, in
    order, and the session log still has every routine line."""
    views = _Views()
    log = _open_log(tmp_path)
    jobs: list[object] = []
    activity = Activity(session_log=log, on_render=views,
                        job_sink=lambda e: jobs.append(e.payload), max_pending=50, drain_limit=7)
    done = threading.Event()

    def worker() -> None:
        for i in range(3000):
            activity.emit(_ev(f"routine {i}"))
            activity.emit(_job(("progress", i + 1, 3000), coalesce_key="progress"))
            if i % 500 == 0:
                activity.emit(_ev(f"warning at {i}", WARNING, summary=True))
        activity.emit(_ev("Completed.", summary=True))
        activity.emit(_job("finished"))
        done.set()

    thread = threading.Thread(target=worker)
    thread.start()
    while not done.is_set() or activity.channel.pending():
        activity.pump()  # the UI thread's bounded ticks, racing the worker
        time.sleep(0.0005)  # yield like the Tk event loop does between ticks (pacing only)
    thread.join(5)
    assert jobs.count("finished") == 1 and jobs[-1] == "finished"
    progress = [p for p in jobs if isinstance(p, tuple)]
    assert progress[-1] == ("progress", 3000, 3000)
    assert [p[1] for p in progress] == sorted(p[1] for p in progress)  # never goes backwards
    assert [s for s in views.summary if s.startswith("Warning: warning at")] == [
        f"Warning: warning at {i}" for i in range(0, 3000, 500)]
    assert views.summary[-1] == "Completed."
    activity.close()
    text = log.path.read_text(encoding="utf-8")
    assert all(f"routine {i}\n" in text for i in range(3000))


def test_close_refuses_later_events_and_closes_the_log(tmp_path) -> None:
    views = _Views()
    log = _open_log(tmp_path)
    activity = Activity(session_log=log, on_render=views)
    activity.emit(_ev("queued when the window closed", summary=True))
    activity.close()
    assert activity.closed and not log.is_open
    assert activity.emit(_ev("late worker line", ERROR)) is False
    assert activity.report(_ev("late ui line")) is False
    assert activity.pump() == 0 and activity.pump_all() == 0
    activity.clear_display()
    assert views.updates == 0
    text = log.path.read_text(encoding="utf-8")
    assert "queued when the window closed" in text  # emitted before close: on disk
    assert "late worker line" not in text
    assert text.rstrip().endswith("session closed")


# ── Pump ─────────────────────────────────────────────────────────────────────
class _Scheduler:
    """An ``after``/``after_cancel`` pair whose callbacks the test runs by hand."""

    def __init__(self) -> None:
        self.pending: dict[str, tuple[int, object]] = {}
        self.cancelled: list[str] = []
        self._n = 0

    def schedule(self, ms, func):
        self._n += 1
        handle = f"after#{self._n}"
        self.pending[handle] = (ms, func)
        return handle

    def cancel(self, handle) -> None:
        self.cancelled.append(handle)
        self.pending.pop(handle, None)

    def run_next(self) -> int:
        handle = next(iter(self.pending))
        ms, func = self.pending.pop(handle)
        func()
        return ms


def test_pump_drains_bounded_slices_and_hurries_while_a_backlog_remains() -> None:
    views = _Views()
    activity = Activity(on_render=views, drain_limit=4)
    sched = _Scheduler()
    pump = ActivityPump(activity, sched.schedule, sched.cancel, lambda: False,
                        interval_ms=80, always_on=True)
    pump.start()
    pump.start()  # idempotent: one outstanding callback at most
    assert len(sched.pending) == 1
    for i in range(10):
        activity.emit(_ev(str(i)))
    assert sched.run_next() == 80
    assert views.detailed == ["0", "1", "2", "3"]
    assert list(sched.pending.values())[0][0] == 1  # more pending: come back at once
    sched.run_next()
    sched.run_next()
    assert views.detailed == [str(i) for i in range(10)]
    assert list(sched.pending.values())[0][0] == 80  # caught up: back to the idle interval
    assert len(sched.pending) == 1


def test_pump_without_always_on_stops_when_idle_and_restarts() -> None:
    activity = Activity()
    sched = _Scheduler()
    active = [True]
    pump = ActivityPump(activity, sched.schedule, sched.cancel, lambda: active[0])
    pump.start()
    sched.run_next()
    assert pump.running and len(sched.pending) == 1
    active[0] = False
    sched.run_next()
    assert not pump.running and not sched.pending
    pump.start()
    assert pump.running and len(sched.pending) == 1


def test_pump_stop_cancels_and_never_reschedules_after_close() -> None:
    activity = Activity()
    sched = _Scheduler()
    pump = ActivityPump(activity, sched.schedule, sched.cancel, lambda: True, always_on=True)
    pump.start()
    handle = next(iter(sched.pending))
    pump.stop()
    assert sched.cancelled == [handle] and not pump.running
    activity.close()
    pump.start()
    assert not pump.running and not sched.pending


def test_pump_stop_survives_a_dead_interpreter() -> None:
    activity = Activity()

    def cancel(_handle):
        raise RuntimeError("application has been destroyed")

    pump = ActivityPump(activity, lambda ms, f: "after#1", cancel, lambda: True, always_on=True)
    pump.start()
    pump.stop()  # must not raise
    assert not pump.running


def test_a_tick_after_close_does_nothing() -> None:
    views = _Views()
    activity = Activity(on_render=views)
    sched = _Scheduler()
    pump = ActivityPump(activity, sched.schedule, sched.cancel, lambda: True, always_on=True)
    pump.start()
    activity.emit(_ev("pending"))
    activity.close()
    sched.run_next()
    assert views.updates == 0 and not sched.pending and not pump.running
