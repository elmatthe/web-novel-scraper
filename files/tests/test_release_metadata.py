"""Phase 5 / 0.2.0 release-metadata check (offline, no imports of the package).

A light guard that the docs agree on the current version: the TOP version heading in
CHANGELOG.md must be the same X.Y.Z that Briefing.md's "Current Version" section states.
This catches a CHANGELOG bumped without the Briefing (or vice-versa). At release time
0.2.0's heading is stamped from ``## [0.2.0] — Unreleased`` to ``## [0.2.0] — YYYY-MM-DD``
once the manual live pass signs off; the post-release guard below asserts that stamped,
dated state. ``scripts/verify.py``'s ``## [X.Y.Z]`` docs pattern accepts either form.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = REPO_ROOT / "md-instructions" / "CHANGELOG.md"
BRIEFING = REPO_ROOT / "md-instructions" / "Briefing.md"

_VERSION = r"(\d+\.\d+\.\d+)"


def _top_changelog_version() -> str:
    """The first '## [X.Y.Z]' (date optional) heading in the changelog."""
    text = CHANGELOG.read_text(encoding="utf-8")
    for line in text.splitlines():
        m = re.match(rf"^##\s*\[?v?{_VERSION}\]?", line)
        if m:
            return m.group(1)
    raise AssertionError("no '## [X.Y.Z]' version heading found in CHANGELOG.md")


def _briefing_current_version() -> str:
    """The first X.Y.Z appearing in Briefing.md's 'Current Version' section."""
    text = BRIEFING.read_text(encoding="utf-8")
    m = re.search(r"##\s*Current Version\s*(.+?)(?:\n##\s|\Z)", text, re.DOTALL)
    assert m, "Briefing.md has no '## Current Version' section"
    v = re.search(_VERSION, m.group(1))
    assert v, "Briefing.md 'Current Version' section states no X.Y.Z version"
    return v.group(1)


def test_changelog_top_version_is_0_2_0() -> None:
    assert _top_changelog_version() == "0.2.0"


def test_briefing_current_version_is_0_2_0() -> None:
    assert _briefing_current_version() == "0.2.0"


def test_changelog_and_briefing_agree_on_version() -> None:
    assert _top_changelog_version() == _briefing_current_version()


def test_0_2_0_is_released_and_dated() -> None:
    """0.2.0 is released: the manual live pass is signed off and the heading has been
    stamped — it must carry a YYYY-MM-DD release date and must no longer say
    Unreleased."""
    text = CHANGELOG.read_text(encoding="utf-8")
    m = re.search(r"^##\s*\[0\.2\.0\][^\n]*", text, re.MULTILINE)
    assert m, "no '## [0.2.0]' heading in CHANGELOG.md"
    heading = m.group(0)
    assert "Unreleased" not in heading, (
        f"0.2.0 should be released, not Unreleased: {heading!r}"
    )
    assert re.search(r"\d{4}-\d{2}-\d{2}", heading), (
        f"0.2.0 release heading must carry a date: {heading!r}"
    )
