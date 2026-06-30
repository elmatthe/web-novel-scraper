"""Phase 5 / 0.2.0 release-metadata check (offline, no imports of the package).

A light guard that the docs agree on the current version: the TOP version heading in
CHANGELOG.md must be the same X.Y.Z that Briefing.md's "Current Version" section states.
This catches a CHANGELOG bumped without the Briefing (or vice-versa). It deliberately
does NOT assert a date — 0.2.0 ships ``## [0.2.0] — Unreleased`` (undated) until the
user's manual live pass, and ``scripts/verify.py``'s ``## [X.Y.Z]`` docs pattern already
accepts an undated heading, so no verify change was needed.
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


def test_0_2_0_is_unreleased_and_undated() -> None:
    """0.2.0 stays Unreleased/undated until the manual live pass — the heading must
    not have acquired a date, and must be marked Unreleased."""
    text = CHANGELOG.read_text(encoding="utf-8")
    m = re.search(r"^##\s*\[0\.2\.0\][^\n]*", text, re.MULTILINE)
    assert m, "no '## [0.2.0]' heading in CHANGELOG.md"
    heading = m.group(0)
    assert "Unreleased" in heading, f"0.2.0 heading should say Unreleased: {heading!r}"
    assert not re.search(r"\d{4}-\d{2}-\d{2}", heading), (
        f"0.2.0 must stay undated until the live pass: {heading!r}"
    )
