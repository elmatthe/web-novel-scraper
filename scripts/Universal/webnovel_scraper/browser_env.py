"""Point Playwright at the in-repo, contained browser cache.

The launchers install Chromium with
``PLAYWRIGHT_BROWSERS_PATH=<repo>/files/bin/ms-playwright`` so the browser binary
is contained in the repo (portable, no admin — CSPW-PC constraint) instead of the
default per-user cache. When the launcher starts the GUI it exports that variable
and the child process inherits it.

But the program can also be started *outside* the launcher — a developer running
``app.py`` directly, or the pytest suite importing the package. In that case the
variable would be unset and Playwright would look in its default per-user cache,
where setup never installed anything, so the ``playwright_stealth`` rungs would
fail to launch even though Chromium is sitting in ``files/bin/ms-playwright``.

Setting the same contained path as a *default* at import time fixes that: any code
path that imports the fetch layer ends up pointing at the contained cache.
``os.environ.setdefault`` means an explicit value (from the launcher, or a
developer who wants a different cache) always wins — we only fill in the default
when nothing is set.
"""

from __future__ import annotations

import os
from pathlib import Path

_ENV_VAR = "PLAYWRIGHT_BROWSERS_PATH"

# request_manager/cf_bypass live at scripts/Universal/webnovel_scraper/, so
# parents[3] is the repo root — the same depth the cache root uses.
CONTAINED_BROWSERS_PATH = (
    Path(__file__).resolve().parents[3] / "files" / "bin" / "ms-playwright"
)


def ensure_browsers_path() -> str:
    """Default ``PLAYWRIGHT_BROWSERS_PATH`` to the contained in-repo cache.

    Returns the effective value (the pre-existing one if already set). Safe to
    call repeatedly; it never overrides an explicit value.
    """
    os.environ.setdefault(_ENV_VAR, str(CONTAINED_BROWSERS_PATH))
    return os.environ[_ENV_VAR]


# Apply on import so merely importing the fetch layer is enough.
ensure_browsers_path()
