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

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "scripts"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
