#!/usr/bin/env python3
"""verify.py — the mechanical verify gate for webnovel-scraper.

Run from anywhere; paths are derived from this file's location so the repo
stays portable (no hardcoded user paths). Runs at every phase boundary,
before that phase's commit — starting with Phase 0's own commit.

Checks (all must PASS, else the script exits non-zero):
  1. pytest      — run the test suite; only exit 0 passes. Any failure,
                   collection error, OR "no tests collected" (exit 5) is a FAIL.
  2. deps        — every requirements.txt line must be pinned with exact '=='
                   (no bare names, no >= <= ~= != > <, no '-r' recursive includes).
  3. docs        — CHANGELOG.md and Briefing.md must be de-templated real content
                   with a real version entry (not a template stub/placeholder).

Usage:
    python scripts/verify.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
REQUIREMENTS = SCRIPTS_DIR / "requirements.txt"
# The pytest suite is dev-only and lives under files/tests/ (not shipped),
# while requirements.txt + this gate ship from scripts/.
TESTS_DIR = REPO_ROOT / "files" / "tests"
CHANGELOG = REPO_ROOT / "md-instructions" / "CHANGELOG.md"
BRIEFING = REPO_ROOT / "md-instructions" / "Briefing.md"

# Lines on this allowlist are exempt from the strict '==' rule. Documented
# exceptions only — for 0.1.0 there are none.
PINNING_ALLOWLIST: set[str] = set()

# Markers that betray an un-edited template/placeholder doc.
PLACEHOLDER_MARKERS = (
    "template —",
    "template -",
    "[describe",
    "[e.g.",
    "[list",
    "[what is planned",
    "[x.x.x]",
    "[yyyy-mm-dd",
)


def _fail(check: str, detail: str) -> tuple[str, bool, str]:
    return (check, False, detail)


def _pass(check: str, detail: str = "") -> tuple[str, bool, str]:
    return (check, True, detail)


def check_pytest() -> tuple[str, bool, str]:
    """Run pytest against files/tests/. Only exit 0 (all tests passed) is a
    PASS. Exit 5 ("no tests collected") is a FAIL — a verify gate with zero
    tests collected is not a valid gate. Any other code is a failure or
    collection error."""
    if not TESTS_DIR.exists():
        return _fail("pytest", f"tests dir missing: {TESTS_DIR}")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(TESTS_DIR), "-q"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    tail = (proc.stdout or proc.stderr or "").strip().splitlines()
    summary = tail[-1] if tail else "(no output)"
    if proc.returncode == 0:
        return _pass("pytest", summary)
    if proc.returncode == 5:
        return _fail("pytest", "no tests collected (an empty gate is not valid)")
    return _fail("pytest", f"exit {proc.returncode}: {summary}")


def check_requirements() -> tuple[str, bool, str]:
    if not REQUIREMENTS.exists():
        return _fail("deps", f"missing {REQUIREMENTS}")
    bad: list[str] = []
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line in PINNING_ALLOWLIST:
            continue
        # Recursive includes / editable / option lines are not allowed.
        if line.startswith("-"):
            bad.append(f"{line!r} (recursive include / option line)")
            continue
        # Reject any non-'==' version operator.
        if re.search(r"(>=|<=|~=|!=|>|<)", line):
            bad.append(f"{line!r} (non-'==' operator)")
            continue
        # Must be pinned with exactly one '=='.
        if "==" not in line:
            bad.append(f"{line!r} (bare/unpinned package)")
            continue
    if bad:
        return _fail("deps", "; ".join(bad))
    return _pass("deps", "all dependencies '=='-pinned")


def _is_templated(text: str) -> str | None:
    low = text.lower()
    for marker in PLACEHOLDER_MARKERS:
        if marker in low:
            return marker
    return None


def check_docs() -> tuple[str, bool, str]:
    if not CHANGELOG.exists():
        return _fail("docs", f"missing {CHANGELOG}")
    if not BRIEFING.exists():
        return _fail("docs", f"missing {BRIEFING}")

    changelog = CHANGELOG.read_text(encoding="utf-8")
    briefing = BRIEFING.read_text(encoding="utf-8")

    marker = _is_templated(changelog)
    if marker:
        return _fail("docs", f"CHANGELOG.md still has placeholder marker {marker!r}")
    marker = _is_templated(briefing)
    if marker:
        return _fail("docs", f"Briefing.md still has placeholder marker {marker!r}")

    # Require a real version entry: '## [X.Y.Z] - DATE' or '## vX.Y.Z'.
    if not re.search(r"^##\s*\[?v?\d+\.\d+\.\d+\]?", changelog, re.MULTILINE):
        return _fail("docs", "CHANGELOG.md has no real version entry (## [X.Y.Z])")

    return _pass("docs", "CHANGELOG.md + Briefing.md de-templated, version entry present")


def main() -> int:
    checks = [check_pytest(), check_requirements(), check_docs()]
    print("=" * 60)
    print("verify.py — webnovel-scraper gate")
    print("=" * 60)
    all_ok = True
    for name, ok, detail in checks:
        status = "PASS" if ok else "FAIL"
        all_ok = all_ok and ok
        line = f"  [{status}] {name:<8}"
        if detail:
            line += f" - {detail}"
        print(line)
    print("-" * 60)
    print(f"  RESULT: {'PASS' if all_ok else 'FAIL'}")
    print("=" * 60)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
