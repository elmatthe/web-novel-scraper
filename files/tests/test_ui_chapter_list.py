"""v0.3.0 Checkpoint 03.2a: the GUI side of the chapter list.

* an explicit, one-shot "Refresh chapter list" box in Advanced Settings (never persisted, never
  silent, cleared by every Start, locked during a run);
* a chapter list the run cannot satisfy ends as the existing "Run refused" outcome, with the
  pipeline's reason in the Activity, instead of a generic failure.

Offline and deterministic: the pipeline is a fake ``run_scrape``. Skips without a Tk display.
"""

from __future__ import annotations

import gc
import threading

import pytest

import app
from webnovel_scraper.chapter_preflight import ChapterListRefused
from webnovel_scraper.models import ScrapeJob


def _make_app(**kwargs):
    tk = pytest.importorskip("tkinter")
    gc.collect()
    try:
        return app.ScraperApp(**kwargs)
    except tk.TclError as exc:
        pytest.skip(f"no Tk display available: {exc}")


def _log(win, view):
    return win._log_texts[view].get("1.0", "end-1c")


def _start_with(monkeypatch, tmp_path, win, fake):
    monkeypatch.setattr(app.pipeline, "run_scrape", fake)
    win._output_parent = tmp_path
    win._on_start()
    win._worker.join(5)
    win.flush_activity()


def test_the_refresh_box_is_in_advanced_settings_off_and_explained() -> None:
    win = _make_app()
    try:
        assert win._refresh_var.get() is False
        assert win._refresh_check.winfo_toplevel() is win._advanced
        assert "Refresh chapter list" in win._refresh_check.cget("text")
        assert ".bak" in app.REFRESH_HINT and "next run only" in app.REFRESH_HINT
    finally:
        win.destroy()


def test_a_refresh_request_reaches_the_job_once_and_clears_itself(monkeypatch, tmp_path) -> None:
    win = _make_app()
    jobs: list[ScrapeJob] = []

    def fake(job, **_kw):
        jobs.append(job)

    try:
        win._refresh_var.set(True)
        _start_with(monkeypatch, tmp_path, win, fake)
        assert jobs[-1].refresh_chapter_list is True
        assert win._refresh_var.get() is False        # one-shot
        win._on_start()
        win._worker.join(5)
        assert jobs[-1].refresh_chapter_list is False  # never repeated silently
    finally:
        win.destroy()


def test_the_refresh_box_is_not_a_saved_preference(monkeypatch, tmp_path) -> None:
    win = _make_app()
    try:
        win._refresh_var.set(True)
        win._save_preferences()
        assert not any("refresh" in name for name in vars(win._store.settings))
        assert "refresh" not in str(win._preferences()).lower()
    finally:
        win.destroy()
    second = _make_app()
    try:
        assert second._refresh_var.get() is False
    finally:
        second.destroy()


def test_the_refresh_box_is_locked_while_a_run_is_in_progress(monkeypatch, tmp_path) -> None:
    win = _make_app()
    started, release = threading.Event(), threading.Event()

    def fake(job, **_kw):
        started.set()
        release.wait(5)

    monkeypatch.setattr(app.pipeline, "run_scrape", fake)
    try:
        win._output_parent = tmp_path
        win._on_start()
        assert started.wait(5)
        assert str(win._refresh_check.cget("state")) == "disabled"
    finally:
        release.set()
        if win._worker is not None:
            win._worker.join(5)
        win.flush_activity()
        assert str(win._refresh_check.cget("state")) == "normal"
        win.destroy()


def test_the_start_summary_says_when_a_refresh_was_requested(monkeypatch, tmp_path) -> None:
    win = _make_app()
    try:
        win._refresh_var.set(True)
        _start_with(monkeypatch, tmp_path, win, lambda job, **_kw: None)
        assert "chapter list refresh requested" in _log(win, app.DETAILED_TAB)
        _start_with(monkeypatch, tmp_path, win, lambda job, **_kw: None)
        assert _log(win, app.DETAILED_TAB).count("chapter list refresh requested") == 1
    finally:
        win.destroy()


def test_a_refused_chapter_list_ends_as_run_refused_with_the_reason(monkeypatch, tmp_path) -> None:
    win = _make_app()

    def fake(job, **_kw):
        raise ChapterListRefused("Chapters 1-50 cannot be provided in full: the site's chapter list "
                                 "ends at chapter 30, so chapters 31-50 are not available.")

    try:
        _start_with(monkeypatch, tmp_path, win, fake)
        assert win._run_status_var.get() == "Run refused"
        assert win._running is False
        shown = _log(win, app.SUMMARY_TAB)
        assert "Run refused" in shown
        assert "ends at chapter 30" in shown
    finally:
        win.destroy()


# ── v0.3.0 03.2b: explicit, one-shot re-download of saved chapter pages ─────────────────
def test_the_redownload_box_is_in_advanced_settings_off_and_explained() -> None:
    win = _make_app()
    try:
        assert win._refetch_var.get() is False
        assert win._refetch_check.winfo_toplevel() is win._advanced
        assert "Re-download saved chapter pages" in win._refetch_check.cget("text")
        assert "next run only" in app.REFETCH_HINT
    finally:
        win.destroy()


def test_a_redownload_request_reaches_the_job_once_and_clears_itself(monkeypatch, tmp_path) -> None:
    win = _make_app()
    jobs: list[ScrapeJob] = []
    try:
        win._refetch_var.set(True)
        _start_with(monkeypatch, tmp_path, win, lambda job, **_kw: jobs.append(job))
        assert jobs[-1].refresh_html_cache is True and jobs[-1].use_cache is True
        assert win._refetch_var.get() is False
        assert "saved chapter pages re-downloaded" in _log(win, app.DETAILED_TAB)
        _start_with(monkeypatch, tmp_path, win, lambda job, **_kw: jobs.append(job))
        assert jobs[-1].refresh_html_cache is False
    finally:
        win.destroy()


def test_a_redownload_without_the_cache_is_meaningless_and_not_requested(monkeypatch, tmp_path) -> None:
    win = _make_app()
    jobs: list[ScrapeJob] = []
    try:
        win._cache_var.set(False)
        win._refetch_var.set(True)
        _start_with(monkeypatch, tmp_path, win, lambda job, **_kw: jobs.append(job))
        assert jobs[-1].use_cache is False and jobs[-1].refresh_html_cache is False
    finally:
        win.destroy()


def test_the_redownload_box_is_locked_during_a_run_and_never_saved(monkeypatch, tmp_path) -> None:
    win = _make_app()
    started, release = threading.Event(), threading.Event()

    def fake(job, **_kw):
        started.set()
        release.wait(5)

    monkeypatch.setattr(app.pipeline, "run_scrape", fake)
    try:
        win._refetch_var.set(True)
        win._save_preferences()
        assert "refetch" not in str(win._preferences()).lower()
        win._output_parent = tmp_path
        win._on_start()
        assert started.wait(5)
        assert str(win._refetch_check.cget("state")) == "disabled"
    finally:
        release.set()
        if win._worker is not None:
            win._worker.join(5)
        win.flush_activity()
        assert str(win._refetch_check.cget("state")) == "normal"
        win.destroy()
