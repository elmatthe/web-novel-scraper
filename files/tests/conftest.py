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
    """Point the GUI preferences file at this test's temp folder, so a window built
    without an explicit store (``app.ScraperApp()``) never reads or writes the real
    ``files/runtime-data/settings.json`` (v0.3.0 Checkpoint 01)."""
    from webnovel_scraper.ui import settings as ui_settings

    monkeypatch.setattr(
        ui_settings, "SETTINGS_PATH", tmp_path / "runtime-data" / "settings.json"
    )
