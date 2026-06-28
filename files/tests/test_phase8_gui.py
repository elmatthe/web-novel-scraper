"""Phase 8 bug-hunt regression tests for GUI defaults (offline).

Covers fix #2: the "Headless browser" checkbox must default **ON**, matching the
README ("Leave Headless on unless you want to watch the browser work"), so a
non-technical user who enables browser mode never gets a browser window popping
up unexpectedly.

The GUI is a thin shell. The deterministic assertion is on the module-level
``DEFAULT_HEADLESS`` constant (no display needed). An end-to-end check that
actually instantiates the Tk window is included but **skips automatically when no
display is available** (e.g. headless CI), so the suite stays deterministic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app


def test_headless_default_constant_is_on() -> None:
    assert app.DEFAULT_HEADLESS is True


def test_headless_checkbox_initialises_on() -> None:
    tk = pytest.importorskip("tkinter")
    try:
        win = app.ScraperApp()
    except tk.TclError as exc:  # no display (headless CI) — not applicable here
        pytest.skip(f"no Tk display available: {exc}")
    try:
        assert win._headless_var.get() is True
    finally:
        win.destroy()


def test_output_location_defaults() -> None:
    """The output-folder controls default to the ~/Downloads parent with a blank
    custom name (so the pipeline falls back to the novel slug)."""
    tk = pytest.importorskip("tkinter")
    try:
        win = app.ScraperApp()
    except tk.TclError as exc:  # no display (headless CI)
        pytest.skip(f"no Tk display available: {exc}")
    try:
        # The chosen PARENT is the Downloads base, not a prior output path.
        assert win._output_parent == app.DEFAULT_OUTPUT_PARENT
        assert win._output_parent == Path.home() / "Downloads"
        assert win._folder_name_var.get() == ""
        assert callable(win._on_browse)
    finally:
        win.destroy()


def test_output_target_preview_is_single_level_under_downloads() -> None:
    """Regression for the 0.1.1 doubled-folder bug: the resolved target shown by
    the GUI sits directly under the Downloads parent (one level deep), never
    nested inside a prior output folder, and the parent passed is the Downloads
    base — not the displayed target."""
    tk = pytest.importorskip("tkinter")
    try:
        win = app.ScraperApp()
    except tk.TclError as exc:  # no display (headless CI)
        pytest.skip(f"no Tk display available: {exc}")
    try:
        target = Path(win._output_target_var.get())
        # The target's parent is exactly the Downloads base — one directory deep.
        assert target.parent == app.DEFAULT_OUTPUT_PARENT
        # And the parent the run will actually use is the Downloads base, never the
        # resolved target itself (which would nest a folder per run).
        assert win._output_parent == app.DEFAULT_OUTPUT_PARENT
        assert win._output_parent != target
    finally:
        win.destroy()
