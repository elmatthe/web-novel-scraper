"""Checks the Windows launcher runs before starting the app (v0.3.0 Checkpoint 07).

No network access, no downloads, nothing written. Run from the repository root:

    python scripts\\setup_check.py python    0 = this interpreter can host the app (Python 3.12 or
                                            3.13, a final release, with venv, ensurepip, tkinter,
                                            sqlite3 and ssl); 3 = unsupported version;
                                            4 = a required standard module is missing
    python scripts\\setup_check.py base      as ``python``, and 5 = this is a virtual environment,
                                            not an installed Python to build one from
    python scripts\\setup_check.py deps      0 = every runtime dependency imports in this environment
    python scripts\\setup_check.py browsers  0 = ready, else a bitmask: 1 Camoufox browser missing or
                                            outdated, 2 Camoufox GeoIP database missing, 4 Chromium
                                            for Playwright missing

Each problem is printed as one plain line the launcher shows the user.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

# The versions the program is tested with; the launcher installs 3.12 when neither is present.
SUPPORTED = ((3, 12), (3, 13))
# Imported names of the pinned runtime dependencies (scripts/requirements.txt; pytest is dev-only).
RUNTIME_MODULES = ("requests", "bs4", "cloudscraper", "playwright", "playwright_stealth", "camoufox",
                   "reportlab", "pypdf", "pdfplumber", "geoip2")


def check_python(version: tuple[int, int] | None = None, release: str | None = None) -> int:
    version = tuple(sys.version_info[:2]) if version is None else tuple(version)
    release = sys.version_info.releaselevel if release is None else release
    if version not in SUPPORTED or release != "final":
        shown = f"{version[0]}.{version[1]}" + ("" if release == "final" else f" ({release})")
        print(f"Python {shown} at {sys.executable} is not supported; this program needs Python 3.12 "
              "or 3.13.")
        return 3
    missing = []
    for name in ("venv", "ensurepip", "tkinter", "sqlite3", "ssl"):
        try:
            importlib.import_module(name)
        except Exception:  # noqa: BLE001
            missing.append(name)
    if missing:
        print(f"Python at {sys.executable} lacks {', '.join(missing)}; reinstall Python from python.org "
              "with the default options (it includes them).")
        return 4
    return 0


def check_base() -> int:
    if sys.prefix != sys.base_prefix:
        print(f"{sys.executable} belongs to a virtual environment; the program's environment is built "
              "from an installed Python instead.")
        return 5
    return check_python()


def check_deps() -> int:
    problems = []
    for name in RUNTIME_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{name} ({type(exc).__name__})")
    if problems:
        print("Missing or broken packages: " + ", ".join(problems))
        return 1
    return 0


def check_browsers() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from webnovel_scraper.browser_env import browser_readiness

    mask, lines = browser_readiness()
    for line in lines:
        if line.startswith("MISSING"):
            print(line)
    return mask


def main(argv: list[str]) -> int:
    checks = {"python": check_python, "base": check_base, "deps": check_deps, "browsers": check_browsers}
    if len(argv) != 2 or argv[1] not in checks:
        print("usage: setup_check.py python|base|deps|browsers")
        return 64
    return checks[argv[1]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
