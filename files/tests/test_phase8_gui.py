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
