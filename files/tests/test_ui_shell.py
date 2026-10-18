"""v0.3.0 Checkpoint 01: the three-section window shell, Light/Dark switching,
measured window geometry, and the unchanged run lifecycle on the new layout.

Offline and deterministic: no network, no real scrape (``pipeline.run_scrape`` is
replaced). Since Checkpoint 02.1 the worker only enqueues Activity events; a test
joins the worker and then calls ``flush_activity()`` (what the Tk-thread pump does
on its timer), so nothing waits on the real clock. Every test builds a real window
and skips automatically when no Tk display exists (e.g. headless CI). The guarantees
pinned by ``test_phase4_gui.py`` / ``test_phase8_gui.py`` still run unchanged.
"""

from __future__ import annotations

import gc
import json
import sys
import threading

import pytest

import app
from webnovel_scraper import catalog
from webnovel_scraper.models import OutputMode
from webnovel_scraper.ui import settings as ui_settings
from webnovel_scraper.ui.activity import ActivityEvent
from webnovel_scraper.ui.appearance import DARK_COLORS
from webnovel_scraper.ui.settings import SettingsStore


# ── Helpers ──────────────────────────────────────────────────────────────────
def _make_app(store: SettingsStore | None = None, **kwargs):
    tk = pytest.importorskip("tkinter")
    gc.collect()  # earlier windows' garbage, on the main thread (see test_ui_activity_panel)
    try:
        return app.ScraperApp(store, **kwargs)
    except tk.TclError as exc:  # no display (headless CI) — not applicable here
        pytest.skip(f"no Tk display available: {exc}")


def _store(tmp_path, name: str = "settings.json") -> SettingsStore:
    store = SettingsStore(tmp_path / "runtime-data" / name)
    store.load()
    return store


def _descendants(widget) -> list:
    found = []
    for child in widget.winfo_children():
        found.append(child)
        found.extend(_descendants(child))
    return found


def _inside(widget, container) -> bool:
    return str(widget).startswith(str(container) + ".")


def _entry_for(win, variable):
    from tkinter import ttk

    matches = [
        w for w in _descendants(win)
        if isinstance(w, ttk.Entry) and str(w.cget("textvariable")) == str(variable)
    ]
    assert len(matches) == 1
    return matches[0]


def _views(win) -> tuple[str, str]:
    """The Summary and Detailed Activity text."""
    return (win._log_texts["Summary"].get("1.0", "end"),
            win._log_texts["Detailed"].get("1.0", "end"))


# ── Shell structure ──────────────────────────────────────────────────────────
def test_three_numbered_sections_on_the_left_and_activity_on_the_right() -> None:
    from tkinter import ttk

    win = _make_app()
    try:
        frames = [c for c in win._left.winfo_children() if isinstance(c, ttk.Labelframe)]
        assert [str(f.cget("text")) for f in frames] == [
            "1. Web Novel Selection", "2. Output Options", "3. Run"]
        assert frames == win._sections
        for row, frame in enumerate(frames):
            info = frame.grid_info()
            assert (int(info["row"]), int(info["column"])) == (row, 0)
            assert str(frame.cget("style")) == win._bundle["styles"]["labelframe"]

        # Controls column (fixed) on the left, Activity (stretches) on the right.
        assert int(win._left.grid_info()["column"]) == 0
        assert int(win._activity.grid_info()["column"]) == 1
        assert int(win._body.grid_columnconfigure(0)["weight"]) == 0
        assert int(win._body.grid_columnconfigure(1)["weight"]) == 1
        assert int(win._body.grid_rowconfigure(0)["weight"]) == 1
        assert str(win._activity.cget("text")) == "Activity"
        assert _inside(win._notebook, win._activity)
        assert all(_inside(text, win._notebook) for text in win._log_texts.values())

        selection, output, run = frames
        for widget in (win._novel_combo, win._site_menubtn, win._site_hint):
            assert _inside(widget, selection)
        for widget in (win._pdf_check, win._txt_check, win._chunk_entry):
            assert _inside(widget, output)
        for widget in (win._browse_btn, win._start_btn, win._stop_btn, win._progress,
                       win._advanced_btn):
            assert _inside(widget, run)
        assert _inside(_entry_for(win, win._start_var), selection)
        assert _inside(_entry_for(win, win._end_var), selection)
        assert _inside(_entry_for(win, win._output_target_var), run)
        assert _inside(win._resume_btn, run) and not hasattr(win, "_folder_name_var")
    finally:
        win.destroy()


def test_selection_is_website_first_and_keeps_the_existing_catalog_behaviour() -> None:
    win = _make_app()
    try:
        # Website comes first, then Novel, then the chapter range (02.3).
        assert win._site_menubtn.grid_info()["row"] < win._novel_combo.grid_info()["row"]
        assert win._website_var.get() == "freewebnovel.com"
        assert win._current_slug() == "shadow-slave"
        assert str(win._site_menubtn.cget("text")) == "Free Web Novel"
        menu = win._site_menu
        entries = [
            (str(menu.entrycget(i, "label")), str(menu.entrycget(i, "state")))
            for i in range(menu.index("end") + 1)
        ]
        stubs = [state for label, state in entries if label.endswith("(coming soon)")]
        assert stubs and all(state == "disabled" for state in stubs)
        assert str(win._start_btn.cget("state")) == "normal"
        assert str(win._browser_check.cget("state")) == "normal"
        assert win._site_hint.grid_info() == {}  # hidden: there is a runnable site

        # The Noble Queen runs over plain HTTP, so the browser options are inert.
        win._website_var.set("dynamic.webnovel.com")
        win._on_website_change()
        assert win._current_slug() == "the-noble-queen"
        assert win._current_spec().adapter_key == "webnovel_dynamic"
        assert str(win._browser_check.cget("state")) == "disabled"
        assert str(win._headless_check.cget("state")) == "disabled"
        assert str(win._start_btn.cget("state")) == "normal"
    finally:
        win.destroy()


def test_no_runnable_site_shows_the_hint_and_blocks_start() -> None:
    win = _make_app()
    try:
        disabled_only = [s for s in catalog.all_specs() if not s.enabled]
        win._load_catalog(disabled_only)
        assert win._website_var.get() == ""
        assert win._current_spec() is None
        assert win._site_hint.grid_info() != {}
        assert str(win._site_hint.cget("text")) == app.NO_SITE_HINT
        assert str(win._start_btn.cget("state")) == "disabled"
        # The hint's row was part of the measured minimum, so showing it clips nothing.
        win.update_idletasks()
        min_w, min_h = win.geometry_info["minimum"]
        assert win.winfo_reqwidth() <= min_w
        assert win.winfo_reqheight() <= min_h
    finally:
        win.destroy()


def test_output_options_pdf_on_txt_unavailable_and_exactly_one_grouping() -> None:
    from tkinter import ttk

    win = _make_app()
    try:
        assert win._pdf_var.get() is True and win._txt_var.get() is False
        # Neither format box pretends to change the run: PDF is locked on, TXT is
        # visibly unavailable until a TXT renderer exists.
        assert win._pdf_check.instate(["disabled"])
        assert win._txt_check.instate(["disabled"])
        assert "TXT" in str(win._format_note.cget("text"))

        radios = [w for w in _descendants(win._sections[1]) if isinstance(w, ttk.Radiobutton)]
        assert [str(r.cget("text")) for r in radios] == [
            "Individual chapters", "Whole selected range", "Every"]
        assert [str(r.cget("value")) for r in radios] == [
            OutputMode.SEPARATE.value, OutputMode.SINGLE.value, OutputMode.CHUNKED.value]
        # One shared variable: the choices are mutually exclusive.
        assert {str(r.cget("variable")) for r in radios} == {str(win._mode_var)}
        assert win._mode_var.get() == OutputMode.SEPARATE.value
        assert win._chunk_entry.instate(["disabled"])
        radios[2].invoke()
        assert win._mode_var.get() == OutputMode.CHUNKED.value
        assert not win._chunk_entry.instate(["disabled"])
        radios[1].invoke()
        assert win._mode_var.get() == OutputMode.SINGLE.value
        assert win._chunk_entry.instate(["disabled"])
    finally:
        win.destroy()


def test_advanced_settings_window_keeps_every_existing_control() -> None:
    from tkinter import ttk

    win = _make_app()
    try:
        dialog = win._advanced
        assert dialog.state() == "withdrawn"  # built with the window, hidden
        for check in (win._browser_check, win._headless_check, win._http_first_check,
                      win._cache_check):
            assert check.winfo_toplevel() is dialog
        entries = {
            str(e.cget("textvariable"))
            for e in _descendants(dialog) if isinstance(e, ttk.Entry)
        }
        assert entries == {str(win._delay_var), str(win._timeout_var)}
        assert win._delay_var.get() == app.DEFAULT_DELAY
        assert win._timeout_var.get() == app.DEFAULT_TIMEOUT
        assert win._cache_var.get() is True

        assert win.open_advanced_settings() is dialog
        win.update()
        assert dialog.state() == "normal"
        win._delay_var.set("5.5")
        win.close_advanced_settings()
        win.update()
        assert dialog.state() == "withdrawn"
        assert win._delay_var.get() == "5.5"  # closing only hides it
    finally:
        win.destroy()


# ── Light/Dark switching and persistence ─────────────────────────────────────
def test_switch_preserves_values_selection_focus_and_widgets(tmp_path) -> None:
    win = _make_app(_store(tmp_path))
    try:
        win._website_var.set("dynamic.webnovel.com")
        win._on_website_change()
        win._start_var.set("5")
        win._end_var.set("9")
        win._mode_var.set(OutputMode.CHUNKED.value)
        win._on_mode_change()
        win._chunk_var.set("25")
        win._delay_var.set("4.5")
        win._headless_var.set(True)
        win._events.report(
            ActivityEvent("test", "a line that must survive the switch", summary=True))
        entry = _entry_for(win, win._start_var)
        entry.focus_set()
        entry.selection_range(0, "end")
        win.update()

        def snapshot():
            return (
                win._novel_combo.current(), win._website_var.get(), win._start_var.get(),
                win._end_var.get(), win._mode_var.get(),
                win._chunk_var.get(), win._delay_var.get(), win._timeout_var.get(),
                win._headless_var.get(), win._browser_var.get(), win._cache_var.get(),
                win._output_target_var.get(), str(win._chunk_entry.cget("state")),
                _views(win),
            )

        before = snapshot()
        widgets = _descendants(win)
        focus = win.focus_get()
        size = (win.winfo_reqwidth(), win.winfo_reqheight())

        win.toggle_appearance()
        win.update()
        assert win._bundle["appearance"] == "dark"
        assert win._style.lookup("Compact.TFrame", "background") == DARK_COLORS["surface"]
        assert str(win.cget("background")) == DARK_COLORS["window"]
        assert str(win._advanced.cget("background")) == DARK_COLORS["window"]
        for text in win._log_texts.values():
            assert str(text.cget("background")) == DARK_COLORS["field"]
        assert str(win._site_menu.cget("background")) == DARK_COLORS["field"]
        assert str(win._appearance_btn.cget("text")) == "Light mode"
        assert snapshot() == before
        assert _descendants(win) == widgets  # restyled in place, nothing rebuilt
        assert win.focus_get() == focus
        assert entry.selection_present()
        assert (win.winfo_reqwidth(), win.winfo_reqheight()) == size  # no resize

        win.toggle_appearance()
        win.update()
        assert win._bundle["appearance"] == "light"
        assert str(win._appearance_btn.cget("text")) == "Dark mode"
        assert snapshot() == before
    finally:
        win.destroy()


def test_switch_during_a_run_keeps_the_run_state(tmp_path) -> None:
    win = _make_app(_store(tmp_path))
    try:
        win._set_running(True)
        win._cancel_event = threading.Event()
        locked = [(widget, str(widget.cget("state"))) for widget, _ in win._locked_inputs]
        assert all(state == "disabled" for _, state in locked)

        win.toggle_appearance()
        assert win._running is True
        assert not win._cancel_event.is_set()
        assert str(win._start_btn.cget("state")) == "disabled"
        assert str(win._stop_btn.cget("state")) == "normal"
        assert [(w, str(w.cget("state"))) for w, _ in win._locked_inputs] == locked
        assert str(win._appearance_btn.cget("state")) == "normal"  # always available
    finally:
        win._running = False
        win.destroy()


def test_appearance_is_saved_and_restored_on_the_next_start(tmp_path) -> None:
    path = tmp_path / "runtime-data" / "settings.json"
    win = _make_app(_store(tmp_path))
    try:
        assert win._bundle["appearance"] == "light"
        win.toggle_appearance()
        assert win._status_var.get() == "Appearance: Dark."
    finally:
        win.destroy()
    assert json.loads(path.read_text(encoding="utf-8"))["appearance"] == "dark"

    again = _make_app(_store(tmp_path))
    try:
        assert again._bundle["appearance"] == "dark"
        assert str(again.cget("background")) == DARK_COLORS["window"]
        assert str(again._appearance_btn.cget("text")) == "Light mode"
        assert again._status_var.get() == "Ready."
    finally:
        again.destroy()


def test_corrupt_settings_open_in_light_and_say_so(tmp_path) -> None:
    path = tmp_path / "runtime-data" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    win = _make_app(_store(tmp_path))
    try:
        assert win._bundle["appearance"] == "light"
        assert win._status_var.get() == (
            "Settings problem (settings file is not valid JSON); safe defaults in use.")
        win.toggle_appearance()
        assert win._status_var.get() == "Appearance: Dark."
    finally:
        win.destroy()
    assert len(list(path.parent.glob("settings.unusable-*.json"))) == 1


def test_unsaved_choice_is_reported_and_kept_for_the_session(tmp_path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the settings folder should be", encoding="utf-8")
    store = SettingsStore(blocker / "settings.json")
    store.load()
    win = _make_app(store)
    try:
        win.toggle_appearance()
        assert win._bundle["appearance"] == "dark"
        assert "for this session only" in win._status_var.get()
    finally:
        win.destroy()


def test_window_without_a_store_reads_the_default_file_and_writes_nothing() -> None:
    win = _make_app()
    try:
        assert win._store.path == ui_settings.SETTINGS_PATH  # redirected by conftest
        assert win._store.last_load.status == "defaults"
        assert not win._store.path.exists()
    finally:
        win.destroy()


def test_macos_branch_builds_with_native_controls(tmp_path) -> None:
    """The Aqua code path (run here on the host Tk): native-inheriting styles, the
    native default-button cue on Start, and a measured window that fits."""
    win = _make_app(_store(tmp_path), platform="darwin")
    try:
        assert win._bundle["ttk_active"] is False
        assert str(win._start_btn.cget("default")) == "active"
        assert all(str(s.cget("style")).startswith("CompactAqua.") for s in win._sections)
        assert win.geometry_info["fits_screen"] is True
        win.toggle_appearance()
        assert win._bundle["appearance"] == "dark"
    finally:
        win.destroy()


# ── Measured window geometry ─────────────────────────────────────────────────
def test_window_minimum_is_measured_from_the_widgets() -> None:
    win = _make_app()
    try:
        info = win.geometry_info
        min_w, min_h = info["minimum"]
        assert tuple(win.minsize()) == (min_w, min_h)
        win.update_idletasks()
        # The minimum is the window's own requested size with the log at its floor
        # (plus the reserved site-hint row), not a hardcoded number.
        assert win.winfo_reqwidth() == min_w
        assert win.winfo_reqheight() <= min_h
        assert info["controls_width"] == win._left.winfo_reqwidth()
        assert int(win._body.grid_columnconfigure(0)["minsize"]) == info["controls_width"]
        assert info["fits_screen"] is True

        screen_w, screen_h = info["screen"]
        default_w, default_h = info["default"]
        assert min_w <= default_w <= max(min_w, int(screen_w * app.SCREEN_FRACTION))
        assert min_h <= default_h <= max(min_h, int(screen_h * app.SCREEN_FRACTION))
        if int(screen_w * app.SCREEN_FRACTION) > min_w:
            assert default_w > min_w  # a roomier Activity when the screen allows
    finally:
        win.destroy()


def test_nothing_is_clipped_at_the_minimum_size() -> None:
    win = _make_app()
    try:
        min_w, min_h = win.geometry_info["minimum"]
        win.geometry(f"{min_w}x{min_h}")
        win.update()
        assert (win.winfo_width(), win.winfo_height()) == (min_w, min_h)
        right = win.winfo_rootx() + win.winfo_width()
        bottom = win.winfo_rooty() + win.winfo_height()
        for widget in (*win._sections, win._activity, win._novel_combo, win._site_menubtn,
                       win._browse_btn, win._start_btn, win._stop_btn, win._advanced_btn,
                       win._progress, win._activity_note, win._clear_log_btn, win._notebook,
                       win._log_texts["Summary"], win._appearance_btn):
            assert widget.winfo_ismapped(), widget
            assert widget.winfo_rootx() + widget.winfo_width() <= right, widget
            assert widget.winfo_rooty() + widget.winfo_height() <= bottom, widget
        for section in win._sections:
            assert section.winfo_width() >= section.winfo_reqwidth()
            assert section.winfo_height() >= section.winfo_reqheight()
    finally:
        win.destroy()


def test_activity_absorbs_extra_width_and_height() -> None:
    win = _make_app()
    try:
        min_w, min_h = win.geometry_info["minimum"]
        win.geometry(f"{min_w}x{min_h}")
        win.update()
        left = win._left.winfo_width()
        sections = [s.winfo_width() for s in win._sections]
        activity = (win._activity.winfo_width(), win._activity.winfo_height())
        log_height = win._log_texts["Summary"].winfo_height()

        win.geometry(f"{min_w + 300}x{min_h + 150}")
        win.update()
        if (win.winfo_width(), win.winfo_height()) != (min_w + 300, min_h + 150):
            pytest.skip("this display cannot fit the enlarged test window")
        assert win._left.winfo_width() == left
        assert [s.winfo_width() for s in win._sections] == sections
        assert win._activity.winfo_width() == activity[0] + 300
        assert win._activity.winfo_height() == activity[1] + 150
        assert win._log_texts["Summary"].winfo_height() > log_height
    finally:
        win.destroy()


def test_long_names_and_paths_do_not_enlarge_the_window() -> None:
    win = _make_app()
    try:
        win.update_idletasks()
        before = (win.winfo_reqwidth(), win.winfo_reqheight())
        long_title = "Lord of Mysteries 2: Circle of Inevitability " * 6
        win._novel_combo.configure(values=[long_title])
        win._novel_combo.set(long_title)
        win._site_menubtn.configure(text="Free Web Novel (.net) unusually long site name " * 4)
        win._output_target_var.set(
            "C:/" + "a-very-long-folder-name/" * 30 + "lord-of-mysteries-2-1")
        win._run_status_var.set("Stopping after the current chapter… " * 10)
        win._set_status("A long status message " * 30)
        win.update_idletasks()
        assert (win.winfo_reqwidth(), win.winfo_reqheight()) == before
    finally:
        win.destroy()


@pytest.mark.skipif(sys.platform != "win32", reason="'zoomed' is the Windows maximize state")
def test_maximize_and_restore_keep_the_controls_column_fixed() -> None:
    win = _make_app()
    try:
        win.update()
        restored = (win.winfo_width(), win.winfo_height())
        left = win._left.winfo_width()
        activity = win._activity.winfo_width()

        win.state("zoomed")
        win.update()
        assert win.winfo_width() > restored[0]
        assert win._left.winfo_width() == left
        assert win._activity.winfo_width() > activity

        win.state("normal")
        win.update()
        assert (win.winfo_width(), win.winfo_height()) == restored
        assert win._left.winfo_width() == left
        assert win._activity.winfo_width() == activity
    finally:
        win.destroy()


# ── Run lifecycle on the new shell ───────────────────────────────────────────
def test_start_and_stop_drive_the_unchanged_run_lifecycle(monkeypatch, tmp_path) -> None:
    win = _make_app()
    started = threading.Event()

    def fake_run_scrape(job, *, log, cancel_event, progress_cb, phase_cb=None):
        log("fetching chapter 1")
        progress_cb(1, 5)
        started.set()
        cancel_event.wait(5)
        log("stopped cleanly")
        return app.pipeline.RunReport(output_dir=job.output_dir, cancelled=cancel_event.is_set())

    monkeypatch.setattr(app.pipeline, "run_scrape", fake_run_scrape)
    try:
        win._output_parent = tmp_path  # never write under the real ~/Downloads
        win._on_start()
        assert started.wait(5), "worker never reached run_scrape"
        assert win._running is True
        assert str(win._start_btn.cget("state")) == "disabled"
        assert str(win._stop_btn.cget("state")) == "normal"
        # Nothing has said what phase the run is in yet: an honest "preparing", no percentage.
        assert win._run_status_var.get() == "Preparing chapter list…"
        assert win._counts_var.get() == "—" and str(win._progress.cget("mode")) == "indeterminate"
        assert all(str(w.cget("state")) == "disabled" for w, _ in win._locked_inputs)

        win.flush_activity()  # what the Tk-thread pump does on its timer
        summary, detailed = _views(win)
        assert "Run started: Shadow Slave from Free Web Novel." in summary
        assert "fetching chapter 1" in detailed
        assert "fetching chapter 1" not in summary  # a routine line is Detailed only
        assert win._counts_var.get() == "1 / 5 · 20%"
        assert win._run_status_var.get() == "Scraping chapters"
        assert str(win._progress.cget("mode")) == "determinate"
        assert float(str(win._progress.cget("value"))) == 1.0
        assert float(str(win._progress.cget("maximum"))) == 5.0

        win._on_stop()
        assert win._cancel_event.is_set()
        assert str(win._stop_btn.cget("state")) == "disabled"
        assert win._run_status_var.get().startswith("Stopping")
        win._worker.join(5)
        assert not win._worker.is_alive()

        win.flush_activity()
        assert win._running is False
        assert str(win._start_btn.cget("state")) == "normal"
        assert all(str(w.cget("state")) == normal for w, normal in win._locked_inputs)
        assert win._run_status_var.get() == "Stopped"
        summary, detailed = _views(win)
        assert "Stop requested" in summary
        assert "Stopped before the end of the selected range, as requested." in summary
        assert "stopped cleanly" in detailed and "— run ended —" in detailed
    finally:
        if win._cancel_event is not None:
            win._cancel_event.set()
        if win._worker is not None:
            win._worker.join(5)
        win.destroy()


def test_worker_thread_never_touches_tk(monkeypatch, tmp_path) -> None:
    """The worker only enqueues Activity events: it never calls ``after`` or any other
    Tk method, and no widget or Tk variable is touched off the main thread, including
    on the error path. (Checkpoint 01 still let the worker schedule three callbacks
    through ``after``; since 02.1 even that is gone, so this check is stricter.)"""
    win = _make_app()
    touched: list[tuple[str, bool]] = []

    def spy(obj, name: str) -> None:
        original = getattr(obj, name)

        def wrapper(*args, **kwargs):
            touched.append((name, threading.current_thread() is threading.main_thread()))
            return original(*args, **kwargs)

        monkeypatch.setattr(obj, name, wrapper)

    targets = [(win, "after"), (win, "after_idle"), (win, "event_generate"),
               (win._progress, "configure"), (win._start_btn, "configure"),
               (win._stop_btn, "configure"), (win._counts_var, "set"),
               (win._run_status_var, "set"), (win._detail_var, "set"),
               (win._eta_var, "set"), (win._progress, "start"), (win._progress, "stop")]
    targets += [(text, method) for text in win._log_texts.values()
                for method in ("insert", "configure", "delete", "see")]
    for obj, name in targets:
        spy(obj, name)

    def fake_run_scrape(job, *, log, cancel_event, progress_cb, phase_cb=None):
        for i in range(1, 4):
            log(f"chapter {i}")
            progress_cb(i, 3)
        raise RuntimeError("boom")

    monkeypatch.setattr(app.pipeline, "run_scrape", fake_run_scrape)
    try:
        win._output_parent = tmp_path
        win._on_start()
        win._worker.join(5)
        assert not win._worker.is_alive()
        # Start's own updates only, all on the main thread; nothing from the worker.
        assert touched and all(on_main for _, on_main in touched)

        touched.clear()
        win.flush_activity()
        assert touched and all(on_main for _, on_main in touched)
        summary, detailed = _views(win)
        assert ("Error: The run stopped because of an unexpected error: RuntimeError: boom"
                in summary)
        assert "Traceback (most recent call last)" in detailed
        assert win._counts_var.get() == "3 / 3 · 100%"
        assert win._running is False
    finally:
        win.destroy()
