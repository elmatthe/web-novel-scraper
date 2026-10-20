"""v0.3.0 Checkpoint 02.1: the Activity panel in the real window.

Summary/Detailed tabs, Clear Log, severity colours across Light/Dark, the worker-to-UI
event bridge, the session log's lifecycle and the graceful close. Offline and
deterministic: no network or browser; the worker is joined and the queue is drained
with ``flush_activity()`` (what the Tk-thread pump does on its timer), never by waiting
on the real clock. Every test builds a real window and skips when no Tk display exists.
"""

from __future__ import annotations

import ast
import gc
import inspect
import textwrap
import threading
from pathlib import Path

import pytest

import app
from webnovel_scraper import pipeline
from webnovel_scraper.models import ChapterContent, ChapterMeta, EmptyExtractionError
from webnovel_scraper.ui import settings as ui_settings
from webnovel_scraper.ui.activity import ERROR, WARNING, ActivityEvent, RenderUpdate
from webnovel_scraper.ui.appearance import DARK_COLORS, LIGHT_COLORS
from webnovel_scraper.ui.settings import SettingsStore


def _make_app(store: SettingsStore | None = None, **kwargs):
    tk = pytest.importorskip("tkinter")
    # Earlier tests' destroyed windows are cyclic garbage. Collect it here, on the main
    # thread: if a worker thread's allocations triggered that collection instead, each
    # Tk object's __del__ would wait ~1 s for a main loop and then raise.
    gc.collect()
    try:
        return app.ScraperApp(store, **kwargs)
    except tk.TclError as exc:  # no display (headless CI) — not applicable here
        pytest.skip(f"no Tk display available: {exc}")


def _store(tmp_path) -> SettingsStore:
    store = SettingsStore(tmp_path / "runtime-data" / "settings.json")
    store.load()
    return store


def _text(win, view: str) -> str:
    return win._log_texts[view].get("1.0", "end-1c")


def _tags_of(win, view: str, needle: str) -> tuple[str, ...]:
    widget = win._log_texts[view]
    index = widget.search(needle, "1.0", "end")
    assert index, f"{needle!r} not in {view}"
    return tuple(str(tag) for tag in widget.tag_names(index))


def _session_lines(win) -> list[str]:
    return Path(win._session_log.path).read_text(encoding="utf-8").splitlines()


def _from_a_worker(*calls) -> None:
    """Run the callables on a real worker thread and wait for it."""
    thread = threading.Thread(target=lambda: [call() for call in calls])
    thread.start()
    thread.join(5)
    assert not thread.is_alive()


# ── The panel ────────────────────────────────────────────────────────────────
def test_activity_has_summary_and_detailed_tabs_and_clear_log() -> None:
    from tkinter import font as tkfont
    from tkinter import ttk

    win = _make_app()
    try:
        notebook = win._notebook
        assert str(notebook.cget("style")) == win._bundle["styles"]["notebook"]
        assert [str(notebook.tab(tab, "text")) for tab in notebook.tabs()] == [
            "Summary", "Detailed"]
        assert notebook.index(notebook.select()) == 0  # Summary first
        for view, text in win._log_texts.items():
            assert str(text.cget("state")) == "disabled"  # read-only
            actual = tkfont.Font(root=win, font=text.cget("font")).actual()
            mono = tkfont.Font(root=win, font=win._bundle["fonts"]["mono"]).actual()
            assert (actual["family"], actual["size"]) == (mono["family"], mono["size"])
            bars = [w for w in text.master.winfo_children() if isinstance(w, ttk.Scrollbar)]
            assert len(bars) == 1 and str(text.cget("yscrollcommand"))
        assert str(win._clear_log_btn.cget("text")) == "Clear Log"
        assert str(win._clear_log_btn).startswith(str(win._activity) + ".")
        assert "Summary" in str(win._activity_note.cget("text"))
        assert _text(win, "Summary") == app.SUMMARY_PLACEHOLDER
        # Detailed opens with where this session's technical log is.
        assert _text(win, "Detailed") == f"Session log: {win._session_log.path}\n"
        assert Path(win._session_log.path).parent == ui_settings.LOGS_DIR
    finally:
        win.destroy()


def test_one_session_log_per_window_opened_and_closed() -> None:
    win = _make_app()
    path = Path(win._session_log.path)
    try:
        assert list(ui_settings.LOGS_DIR.glob("session-*.log")) == [path]
        assert "Web Novel Scraper session started" in _session_lines(win)[0]
    finally:
        win.destroy()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[-1].endswith("Web Novel Scraper session closed")


# ── Routing, severity and order ──────────────────────────────────────────────
def test_pipeline_lines_route_to_summary_and_detailed_with_severity() -> None:
    win = _make_app()
    try:
        _from_a_worker(
            lambda: win._thread_log("Building chapter index (TOC) …"),
            lambda: win._thread_log("  fetch attempt 1/3 via camoufox"),
            lambda: win._thread_log("  chapter 5 FAILED: HTTP 403"),
            lambda: win._thread_log("WARNING: chapter_index.json belongs to a different novel"),
        )
        assert _text(win, "Summary") == app.SUMMARY_PLACEHOLDER  # nothing drawn off-thread
        win.flush_activity()
        win._events.report(ActivityEvent("run", "Run refused: test", ERROR, summary=True))
        summary, detailed = _text(win, "Summary"), _text(win, "Detailed")
        assert summary.splitlines() == [
            "Building chapter index (TOC) …",
            "Warning: chapter 5 FAILED: HTTP 403",
            "WARNING: chapter_index.json belongs to a different novel",
            "Error: Run refused: test",
        ]
        assert detailed.splitlines()[1:] == [
            "Building chapter index (TOC) …",
            "  fetch attempt 1/3 via camoufox",
            "  Warning: chapter 5 FAILED: HTTP 403",
            "WARNING: chapter_index.json belongs to a different novel",
            "Error: Run refused: test",
        ]
        # Warnings and errors are coloured differently from ordinary lines.
        assert WARNING in _tags_of(win, "Summary", "chapter 5 FAILED")
        assert ERROR in _tags_of(win, "Summary", "Run refused")
        assert _tags_of(win, "Detailed", "fetch attempt") == ()
        for view in ("Summary", "Detailed"):
            widget = win._log_texts[view]
            assert str(widget.tag_cget(WARNING, "foreground")) == LIGHT_COLORS["warning"]
            assert str(widget.tag_cget(ERROR, "foreground")) == LIGHT_COLORS["danger"]
    finally:
        win.destroy()


def test_new_lines_follow_the_end_unless_the_user_scrolled_up() -> None:
    win = _make_app()
    try:
        win.update()
        _from_a_worker(*[lambda i=i: win._thread_log(f"WARNING: line {i}") for i in range(200)])
        win.flush_activity()
        win.update()
        summary = win._log_texts["Summary"]
        assert summary.yview()[1] == 1.0  # scrolled to the newest line
        summary.yview_moveto(0.0)
        win.update()
        _from_a_worker(lambda: win._thread_log("WARNING: one more"))
        win.flush_activity()
        win.update()
        assert summary.yview()[0] == 0.0  # reading older lines: not yanked away
        assert "one more" in _text(win, "Summary")
    finally:
        win.destroy()


def test_a_tab_filled_while_hidden_opens_at_its_newest_line() -> None:
    win = _make_app()
    try:
        win.update()  # Summary is showing; Detailed is hidden
        _from_a_worker(*[lambda i=i: win._thread_log(f"  detail {i}") for i in range(200)])
        win.flush_activity()
        win._notebook.select(1)
        win.update()
        detailed = win._log_texts["Detailed"]
        assert detailed.yview()[1] == 1.0
        assert detailed.bbox("end-2c") is not None  # the last line is on screen

        # Shrinking the window keeps the newest line in view, and keeps following.
        min_w, min_h = win.geometry_info["minimum"]
        win.geometry(f"{min_w}x{min_h}")
        win.update()
        assert detailed.yview()[1] == 1.0
        _from_a_worker(lambda: win._thread_log("  detail after the resize"))
        win.flush_activity()
        win.update()
        assert detailed.yview()[1] == 1.0 and detailed.bbox("end-2c") is not None

        # Scrolled up in Detailed, then away and back: it stays where the user left it.
        detailed.yview_moveto(0.0)
        win.update()
        win._notebook.select(0)
        win.update()
        _from_a_worker(lambda: win._thread_log("  detail 200"))
        win.flush_activity()
        win._notebook.select(1)
        win.update()
        assert detailed.yview()[0] == 0.0
    finally:
        win.destroy()


def test_switching_tabs_keeps_each_history() -> None:
    win = _make_app()
    try:
        win._events.report(ActivityEvent("test", "milestone", summary=True))
        win._events.report(ActivityEvent("test", "  detail only"))
        before = (_text(win, "Summary"), _text(win, "Detailed"))
        for tab in (1, 0, 1):
            win._notebook.select(tab)
            win.update()
            assert (_text(win, "Summary"), _text(win, "Detailed")) == before
        assert "detail only" not in before[0] and "detail only" in before[1]
    finally:
        win.destroy()


def test_light_dark_switch_keeps_tab_selection_history_and_recolours_both_logs(tmp_path) -> None:
    win = _make_app(_store(tmp_path))
    try:
        win._events.report(ActivityEvent("test", "a warning", WARNING, summary=True))
        win._events.report(ActivityEvent("test", "an error", ERROR, summary=True))
        win._notebook.select(1)
        win.update()
        before = (_text(win, "Summary"), _text(win, "Detailed"), win._notebook.select())

        win.toggle_appearance()
        win.update()
        assert (_text(win, "Summary"), _text(win, "Detailed"), win._notebook.select()) == before
        for widget in win._log_texts.values():
            assert str(widget.cget("background")) == DARK_COLORS["field"]
            assert str(widget.tag_cget(WARNING, "foreground")) == DARK_COLORS["warning"]
            assert str(widget.tag_cget(ERROR, "foreground")) == DARK_COLORS["danger"]
        assert win._style.lookup(win._bundle["styles"]["notebook"] + ".Tab", "foreground",
                                 ["selected"]) == DARK_COLORS["text"]

        win.toggle_appearance()
        win.update()
        assert (_text(win, "Summary"), _text(win, "Detailed"), win._notebook.select()) == before
        assert str(win._log_texts["Summary"].cget("background")) == LIGHT_COLORS["field"]
    finally:
        win.destroy()


# ── Clear Log ────────────────────────────────────────────────────────────────
def test_clear_log_clears_the_display_but_not_the_session_log_or_the_run() -> None:
    win = _make_app()
    try:
        win._events.report(ActivityEvent("test", "before clear", WARNING, summary=True))
        _from_a_worker(lambda: win._thread_log("  queued detail"))
        win._set_running(True)
        stop_state = str(win._stop_btn.cget("state"))

        win._clear_log_btn.invoke()
        assert _text(win, "Summary") == "" and _text(win, "Detailed") == ""
        assert win._status_var.get() == (
            "Activity display cleared. The session log on disk is unchanged.")
        assert win._running is True and str(win._stop_btn.cget("state")) == stop_state

        win.flush_activity()  # a line queued before the clear still shows afterwards
        win._events.report(ActivityEvent("test", "after clear", summary=True))
        assert _text(win, "Summary") == "after clear\n"
        assert _text(win, "Detailed") == "  queued detail\nafter clear\n"
        log = "\n".join(_session_lines(win))
        for message in ("before clear", "queued detail", "after clear"):
            assert message in log
    finally:
        win._running = False
        win.destroy()


# ── The worker never touches Tk ──────────────────────────────────────────────
def _self_attributes(function) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    return {node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "self"}


def test_worker_side_code_only_reaches_the_event_queue() -> None:
    """Structural guard: the code that runs on the worker thread reads nothing of the
    window but the Activity queue, the cancel flag and the two enqueue-only callbacks."""
    allowed = {"_events", "_cancel_event", "_thread_log", "_thread_progress", "_thread_phase"}
    for method in (app.ScraperApp._run_worker, app.ScraperApp._thread_log,
                   app.ScraperApp._thread_progress, app.ScraperApp._thread_phase):
        used = _self_attributes(method)
        assert used <= allowed, (method.__name__, used - allowed)
        assert "after" not in inspect.getsource(method).replace("_after", "")


# ── End-to-end through the real pipeline (offline) ───────────────────────────
BODY = "SECRET-BODY-SENTENCE: the chapter text itself must never reach a log."


class _FakeRM:
    def __init__(self, **kwargs) -> None:
        self.cancel_event = None

    def start(self):
        return self

    def close(self) -> None:
        pass


class _FakeAdapter:
    """Chapter 1 has prose; chapter 2's page has no body (a recorded failure)."""

    def __init__(self, *, request_manager, log) -> None:
        self.warnings: list[str] = []

    def build_chapter_index(self, spec):
        return [ChapterMeta(index=i, url=f"https://freewebnovel.com/novel/shadow-slave/chapter-{i}", source_id=str(i))
                for i in (1, 2)]

    def fetch_chapter(self, meta, spec):
        if meta.index == 2:
            raise EmptyExtractionError("no chapter body on the page")
        return ChapterContent(index=1, title="Title 1", paragraphs=[
            BODY, "A second paragraph of ordinary prose for the PDF renderer."])


def test_a_real_offline_run_reports_the_outcome_from_the_run_report(monkeypatch, tmp_path) -> None:
    monkeypatch.setitem(pipeline.REGISTRY, "freewebnovel", _FakeAdapter)
    monkeypatch.setattr(pipeline, "RequestManager", _FakeRM)
    win = _make_app()
    try:
        win._browser_var.set(False)  # the HTTP path drives the registry adapter
        win._delay_var.set("0")
        win._cache_var.set(False)
        win._output_parent = tmp_path
        win._on_start()
        win._worker.join(60)
        assert not win._worker.is_alive()
        win.flush_activity()

        summary, detailed = _text(win, "Summary"), _text(win, "Detailed")
        assert "Run started: Shadow Slave from Free Web Novel (.com)." in summary
        assert "Building chapter index (TOC) …" in summary
        assert "Warning: chapter 2 FAILED [EMPTY_BODY]" in summary
        # Both chapters were processed, yet the run is not called a success.
        assert win._counts_var.get() == "2 / 2 · 100%"
        assert "Warning: Finished with problems: 1 chapter could not be saved." in summary
        assert "Completed" not in summary
        assert "Warning: Failed chapters: 2." in summary
        assert "Warning: Fetched but no chapter text found: 2." in summary
        assert "PDFs written: 1. Already present (skipped): 0." in summary
        assert "Run complete." in detailed and "Run complete." not in summary
        assert win._running is False and win._run_status_var.get() == "Finished with problems"
        written = list(tmp_path.rglob("*.pdf"))
        assert len(written) == 1

        log = "\n".join(_session_lines(win))
        assert "Finished with problems" in log and "chapter 2 FAILED" in log
        assert "SECRET-BODY-SENTENCE" not in log  # no novel text in the technical log
        assert "SECRET-BODY-SENTENCE" not in summary + detailed
    finally:
        win.destroy()


# ── Stop, close, destruction and late events ─────────────────────────────────
def test_stop_request_precedes_everything_the_stop_causes(monkeypatch, tmp_path) -> None:
    """Forces the worst interleaving: the cancel event's ``set()`` returns only after the
    worker has fully reacted (its last line, outcome and finish all queued). "Stop
    requested" must still come first, on screen and in the session log."""
    reacted, reached, stop_seen = threading.Event(), threading.Event(), threading.Event()

    class _ReactsAtOnce(threading.Event):
        """Stands in for the run's cancel event in the window (Stop sets it)."""

        def set(self) -> None:
            super().set()
            stop_seen.set()
            assert reacted.wait(5), "the worker never finished reacting to the stop"

    def fake_run_scrape(job, *, log, cancel_event, progress_cb, phase_cb=None):
        reached.set()
        assert stop_seen.wait(5)
        log("Run cancelled: stop requested")
        return pipeline.RunReport(output_dir=job.output_dir, cancelled=True)

    monkeypatch.setattr(app.pipeline, "run_scrape", fake_run_scrape)
    win = _make_app()
    original_emit = win._events.emit

    def emit(event):
        accepted = original_emit(event)
        if isinstance(event.payload, app.run_activity.RunFinished):
            reacted.set()
        return accepted

    monkeypatch.setattr(win._events, "emit", emit)
    try:
        win._output_parent = tmp_path
        win._on_start()
        assert reached.wait(5)
        win._cancel_event = _ReactsAtOnce()
        win._on_stop()
        win._worker.join(5)
        win.flush_activity()
        summary = _text(win, "Summary").splitlines()
        order = ["Stop requested", "Run cancelled: stop requested",
                 "Stopped before the end of the selected range"]
        positions = [next(i for i, line in enumerate(summary) if line.startswith(n)) for n in order]
        assert positions == sorted(positions)
        log = _session_lines(win)
        positions = [next(i for i, line in enumerate(log) if n in line) for n in order]
        assert positions == sorted(positions)
        assert win._running is False and win._run_status_var.get() == "Stopped"
    finally:
        win.destroy()



def test_graceful_close_keeps_the_runs_final_events_in_the_session_log(monkeypatch, tmp_path) -> None:
    win = _make_app()
    path = Path(win._session_log.path)
    reached, release = threading.Event(), threading.Event()

    def fake_run_scrape(job, *, log, cancel_event, progress_cb, phase_cb=None):
        progress_cb(1, 4)
        reached.set()
        cancel_event.wait(5)
        log("teardown: browser closed")
        release.wait(5)  # still tearing down until the test lets it finish
        return pipeline.RunReport(output_dir=job.output_dir, cancelled=True)

    monkeypatch.setattr(app.pipeline, "run_scrape", fake_run_scrape)
    scheduled: list = []
    monkeypatch.setattr(win, "after", lambda ms, cb=None, *a: scheduled.append((ms, cb)))
    monkeypatch.setattr(app.messagebox, "askokcancel", lambda *a, **k: True)
    try:
        win._output_parent = tmp_path
        win._on_start()
        assert reached.wait(5)
        win._on_close()
        # The worker is still tearing down: the window stays, locked, and polls.
        assert not win._shut_down
        assert str(win._start_btn.cget("state")) == "disabled"
        assert scheduled and scheduled[-1][1] == win._poll_close
        release.set()
        win._worker.join(5)
        assert not win._worker.is_alive()
        win.flush_activity()  # the run's finish arrives while closing: Start stays locked
        assert str(win._start_btn.cget("state")) == "disabled"
        scheduled[-1][1]()  # the next poll sees the worker gone and destroys
    finally:
        if not win._shut_down:
            win.destroy()
    assert win._shut_down and win._events.closed and not win._pump.running
    messages = path.read_text(encoding="utf-8").splitlines()
    order = ["Closing — waiting for the current chapter to finish…", "teardown: browser closed",
             "Stopped before the end of the selected range, as requested.",
             app.run_activity.RUN_ENDED_LINE, "Web Novel Scraper session closed"]
    positions = [next(i for i, line in enumerate(messages) if needle in line) for needle in order]
    assert positions == sorted(positions)
    assert messages[-1].endswith("session closed")


def test_nothing_reaches_the_window_after_it_is_destroyed() -> None:
    win = _make_app()
    path = Path(win._session_log.path)
    win.destroy()
    size = path.stat().st_size
    assert not win._pump.running and win._pump._handle is None
    # A non-daemon worker may still finish after the window is gone.
    _from_a_worker(lambda: win._thread_log("late line from the worker"),
                   lambda: win._thread_progress(3, 10),
                   lambda: win._events.emit(ActivityEvent("run", "late", ERROR, summary=True)))
    assert win._events.channel.pending() == 0
    assert win.flush_activity() == 0
    win._render_activity(RenderUpdate(cleared=True))  # a stray render is a no-op
    assert path.stat().st_size == size  # nothing written after "session closed"


def test_an_unwritable_logs_folder_is_announced_and_the_window_still_works(monkeypatch, tmp_path) -> None:
    blocker = tmp_path / "logs-blocker"
    blocker.write_text("a file where the logs folder should be", encoding="utf-8")

    def fake_run_scrape(job, *, log, cancel_event, progress_cb, phase_cb=None):
        log("WARNING: something worth seeing")
        return pipeline.RunReport(output_dir=job.output_dir, skipped_existing=[1])

    monkeypatch.setattr(app.pipeline, "run_scrape", fake_run_scrape)
    win = _make_app(logs_dir=blocker)
    try:
        assert win._session_log.failed and win._session_log.path is None
        summary = _text(win, "Summary")
        assert summary.startswith("Warning: The session log could not be created (")
        assert "Session log:" not in _text(win, "Detailed")
        win._output_parent = tmp_path
        win._on_start()
        win._worker.join(5)
        win.flush_activity()
        summary = _text(win, "Summary")
        assert "WARNING: something worth seeing" in summary
        assert ("Completed: every selected chapter was already saved in the run folder, so no "
                "new PDFs were written.") in summary
        assert summary.count("session log could not be") == 1
    finally:
        win.destroy()


def test_a_settings_problem_is_also_a_summary_warning(tmp_path) -> None:
    path = tmp_path / "runtime-data" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    win = _make_app(_store(tmp_path))
    try:
        assert _text(win, "Summary") == (
            "Warning: Settings problem (settings file is not valid JSON); safe defaults in use.\n")
    finally:
        win.destroy()


# ── Layout ───────────────────────────────────────────────────────────────────
def test_both_logs_take_the_extra_space_and_fit_at_the_minimum() -> None:
    win = _make_app()
    try:
        min_w, min_h = win.geometry_info["minimum"]
        win.geometry(f"{min_w}x{min_h}")
        win._notebook.select(1)
        win.update()
        detailed = win._log_texts["Detailed"]
        right = win.winfo_rootx() + win.winfo_width()
        bottom = win.winfo_rooty() + win.winfo_height()
        for widget in (detailed, win._clear_log_btn, win._activity_note, win._notebook):
            assert widget.winfo_ismapped(), widget
            assert widget.winfo_rootx() + widget.winfo_width() <= right, widget
            assert widget.winfo_rooty() + widget.winfo_height() <= bottom, widget
        # The note wraps beside Clear Log within the tabs' floor width, so it never
        # raises the window's requested width, at the minimum or once the window grows.
        gap = int(win._bundle["metrics"]["gap_md"])
        assert (win._activity_note.winfo_reqwidth() + gap + win._clear_log_btn.winfo_reqwidth()
                <= win._notebook.winfo_reqwidth())
        win.update_idletasks()
        assert win.winfo_reqwidth() == min_w
        small = (detailed.winfo_width(), detailed.winfo_height())

        win.geometry(f"{min_w + 240}x{min_h + 120}")
        win.update()
        if (win.winfo_width(), win.winfo_height()) != (min_w + 240, min_h + 120):
            pytest.skip("this display cannot fit the enlarged test window")
        assert detailed.winfo_width() == small[0] + 240
        assert detailed.winfo_height() == small[1] + 120
        win.update_idletasks()
        assert win.winfo_reqwidth() == min_w
        win._notebook.select(0)
        win.update()
        assert win._log_texts["Summary"].winfo_width() == small[0] + 240
    finally:
        win.destroy()
