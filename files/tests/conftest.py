"""pytest bootstrap for the webnovel-scraper suite.

The tests live in ``files/tests/`` (dev-only, not shipped) while the package
they exercise ships from ``scripts/Universal/webnovel_scraper/``. Insert that
``Universal`` directory onto ``sys.path`` so ``import webnovel_scraper`` resolves
no matter where pytest is invoked from.

Layout (relative to this file):
    <repo>/files/tests/conftest.py   <- this file  (parents[2] == <repo>)
    <repo>/scripts/Universal/        <- package root added below
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_UNIVERSAL = _REPO_ROOT / "scripts" / "Universal"

if str(_UNIVERSAL) not in sys.path:
    sys.path.insert(0, str(_UNIVERSAL))
