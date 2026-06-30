"""Phase 8 / 0.1.3 regression tests for GUI defaults (offline).

0.1.3 flips the FreeWebNovel fetch architecture to **headful camoufox primary**:
the "Headless browser" checkbox now defaults **OFF** (visible) — a visible
persistent camoufox browser is the legacy-matched config Cloudflare clears for,
whereas a headless browser is what it blocks. Browser mode defaults **ON** and
"Try fast HTTP first" defaults **OFF** (opt-in).

The GUI is a thin shell. The deterministic assertions are on the module-level
default constants (no display needed). The end-to-end checks that instantiate the
Tk window **skip automatically when no display is available** (e.g. headless CI),
so the suite stays deterministic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app


def test_headless_default_constant_is_off() -> None:
    # Visible browser by default (0.1.3 headful-camoufox-primary architecture).
    assert app.DEFAULT_HEADLESS is False


def test_browser_and_http_first_default_constants() -> None:
    # FreeWebNovel is browser-primary by default; HTTP-first is opt-in.
    assert app.DEFAULT_BROWSER_MODE is True
    assert app.DEFAULT_HTTP_FIRST is False


def test_headless_checkbox_initialises_off() -> None:
    tk = pytest.importorskip("tkinter")
    try:
        win = app.ScraperApp()
    except tk.TclError as exc:  # no display (headless CI) — not applicable here
        pytest.skip(f"no Tk display available: {exc}")
    try:
        assert win._headless_var.get() is False
        # Browser mode on, HTTP-first off — GUI defaults agree with the constants.
        assert win._browser_var.get() is True
        assert win._http_first_var.get() is False
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
