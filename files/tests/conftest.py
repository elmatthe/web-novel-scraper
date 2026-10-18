"""pytest bootstrap for the webnovel-scraper suite.

The tests live in ``files/tests/`` (dev-only, not shipped) while the package
they exercise ships from ``scripts/webnovel_scraper/`` (flat layout since
v0.3.0 step 01.0; previously ``scripts/Universal/``). Insert the ``scripts``
directory onto ``sys.path`` so ``import webnovel_scraper`` and ``import app``
resolve no matter where pytest is invoked from.

Layout (relative to this file):
    <repo>/files/tests/conftest.py   <- this file  (parents[2] == <repo>)
    <repo>/scripts/                  <- package root added below
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "scripts"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


@pytest.fixture(autouse=True)
def _isolated_gui_settings(tmp_path, monkeypatch):
    """Point the GUI preferences file and session-log folder at this test's temp
    folder, so a window built without explicit paths (``app.ScraperApp()``) never
    reads or writes the real ``files/runtime-data/settings.json`` (v0.3.0 Checkpoint
    01) or ``files/runtime-data/logs/`` (Checkpoint 02.1)."""
    from webnovel_scraper.ui import settings as ui_settings

    monkeypatch.setattr(
        ui_settings, "SETTINGS_PATH", tmp_path / "runtime-data" / "settings.json"
    )
    monkeypatch.setattr(ui_settings, "LOGS_DIR", tmp_path / "runtime-data" / "logs")


@pytest.fixture(autouse=True)
def _no_unexpected_modal_dialogs(monkeypatch):
    """A modal dialog nobody answers would hang the whole suite (v0.3.0 03.3 added the Resume
    dialogs). Any dialog a test did not patch for itself fails that test at once instead."""
    from tkinter import filedialog, messagebox

    def refuse(name):
        def _raise(*args, **_kw):
            raise AssertionError(f"unexpected modal dialog in a test: {name}{args[:2]!r}")
        return _raise

    for name in ("showerror", "showinfo", "showwarning", "askyesno", "askokcancel",
                 "askyesnocancel", "askquestion", "askretrycancel"):
        monkeypatch.setattr(messagebox, name, refuse(f"messagebox.{name}"))
    for name in ("askdirectory", "askopenfilename", "asksaveasfilename"):
        monkeypatch.setattr(filedialog, name, refuse(f"filedialog.{name}"))


@pytest.fixture(autouse=True)
def _isolated_chapter_lists(tmp_path, monkeypatch):
    """Give every pipeline run its own empty chapter manifest store (v0.3.0 03.2a), so a test never
    reads or writes the real ``files/runtime-data/chapter-lists/`` and never sees another test's
    saved chapter list. Tests that exercise the store pass their own via ``manifest_store=``."""
    from webnovel_scraper import pipeline
    from webnovel_scraper.chapter_store import ChapterManifestStore

    store = ChapterManifestStore(
        baseline_root=tmp_path / "chapter-lists-baseline",
        runtime_root=tmp_path / "chapter-lists-runtime",
    )
    monkeypatch.setattr(pipeline, "default_manifest_store", lambda: store)
    return store
