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

# request_manager/cf_bypass live at scripts/webnovel_scraper/, so
# parents[2] is the repo root — the same depth the cache root uses.
CONTAINED_BROWSERS_PATH = (
    Path(__file__).resolve().parents[2] / "files" / "bin" / "ms-playwright"
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


# ── Readiness (v0.3.0 07): what is actually installed, not what a marker file says ────────────
# Pure file checks: nothing is downloaded and no browser is started, so the launcher can run them
# on every start and the window can report a missing engine instead of failing mid-run.
MISSING_CAMOUFOX = 1
MISSING_GEOIP = 2
MISSING_CHROMIUM = 4


def camoufox_status() -> tuple[bool, str]:
    """Whether the Camoufox browser (the Free Web Novel .com engine) is installed and supported."""
    try:
        import camoufox.pkgman as pkgman

        folder = Path(pkgman.camoufox_path(download_if_missing=False))
        exe = folder / pkgman.LAUNCH_FILE[pkgman.OS_NAME]
    except Exception as exc:  # noqa: BLE001 - not installed, outdated or unreadable
        return False, f"Camoufox browser not ready ({type(exc).__name__}: {exc})"
    if not exe.is_file():
        return False, f"Camoufox browser executable missing ({exe})"
    return True, f"Camoufox browser {pkgman.installed_verstr()} at {folder}"


def geoip_status() -> tuple[bool, str]:
    """Whether Camoufox's GeoIP database (used with ``geoip=True``) is present in this environment."""
    try:
        from camoufox.locale import MMDB_FILE
    except Exception as exc:  # noqa: BLE001
        return False, f"Camoufox GeoIP support unavailable ({type(exc).__name__}: {exc})"
    path = Path(MMDB_FILE)
    if not path.is_file() or path.stat().st_size < 1_000_000:
        return False, f"Camoufox GeoIP database missing ({path})"
    return True, f"Camoufox GeoIP database at {path}"


def chromium_status(browsers_path: str | os.PathLike[str] | None = None) -> tuple[bool, str]:
    """Whether the Chromium builds this Playwright version expects are fully installed in the
    contained browsers folder (Playwright writes ``INSTALLATION_COMPLETE`` when a download ends)."""
    import json

    try:
        import playwright

        listing = Path(playwright.__file__).parent / "driver" / "package" / "browsers.json"
        browsers = {b["name"]: b["revision"] for b in json.loads(listing.read_text(encoding="utf-8"))["browsers"]}
    except Exception as exc:  # noqa: BLE001
        return False, f"Playwright is not installed correctly ({type(exc).__name__}: {exc})"
    root = Path(browsers_path or os.environ.get(_ENV_VAR) or CONTAINED_BROWSERS_PATH)
    problems = []
    for name, folder_name in (("chromium", "chromium"), ("chromium-headless-shell", "chromium_headless_shell")):
        revision = browsers.get(name)
        if revision is None:
            continue
        folder = root / f"{folder_name}-{revision}"
        if not (folder / "INSTALLATION_COMPLETE").is_file():
            problems.append(f"{name} {revision} missing in {root}")
    if problems:
        return False, "Chromium for Playwright not ready (" + "; ".join(problems) + ")"
    return True, f"Chromium for Playwright installed in {root}"


def browser_readiness() -> tuple[int, list[str]]:
    """``(bitmask of MISSING_* flags, human lines)`` for every browser component the app uses."""
    mask, lines = 0, []
    for flag, check in ((MISSING_CAMOUFOX, camoufox_status), (MISSING_GEOIP, geoip_status),
                        (MISSING_CHROMIUM, chromium_status)):
        ok, detail = check()
        lines.append(("OK   " if ok else "MISSING ") + detail)
        if not ok:
            mask |= flag
    return mask, lines
