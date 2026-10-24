"""Release-metadata checks (offline, no imports of the package).

0.3.0 is the current Windows implementation; GitHub publication remains deferred. The release
guard checks that the historical 0.2.0 release remains stamped and dated below it, and that the
package version and every module that stamps it into saved files say 0.3.0.
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


def _changelog_heading(version: str) -> str:
    text = CHANGELOG.read_text(encoding="utf-8")
    pattern = rf"^##\s*\[{re.escape(version)}\][^\n]*"
    m = re.search(pattern, text, re.MULTILINE)
    assert m, f"no '## [{version}]' heading in CHANGELOG.md"
    return m.group(0)


def _briefing_current_version() -> str:
    """The first X.Y.Z appearing in Briefing.md's 'Current Version' section."""
    text = BRIEFING.read_text(encoding="utf-8")
    m = re.search(r"##\s*Current Version\s*(.+?)(?:\n##\s|\Z)", text, re.DOTALL)
    assert m, "Briefing.md has no '## Current Version' section"
    v = re.search(_VERSION, m.group(1))
    assert v, "Briefing.md 'Current Version' section states no X.Y.Z version"
    return v.group(1)


def test_changelog_top_version_is_0_3_0_unreleased() -> None:
    assert _top_changelog_version() == "0.3.0"
    assert "Unreleased" in _changelog_heading("0.3.0")


def _assigned(path: Path, name: str) -> str:
    m = re.search(rf'^{name} = "([^"]+)"', path.read_text(encoding="utf-8"), re.MULTILINE)
    assert m, f"no {name} in {path.name}"
    return m.group(1)


def test_the_package_and_every_saved_file_stamp_the_changelog_version() -> None:
    package = REPO_ROOT / "scripts" / "webnovel_scraper"
    version = _assigned(package / "__init__.py", "__version__")
    assert version == _top_changelog_version()
    for module in ("chapter_store.py", "html_cache.py", "run_journal.py"):
        assert _assigned(package / module, "APP_VERSION") == version, module


def test_briefing_current_version_is_0_3_0() -> None:
    assert _briefing_current_version() == "0.3.0"


def test_changelog_current_version_and_briefing_agree() -> None:
    assert _briefing_current_version() == _top_changelog_version()


def test_0_2_0_is_released_and_dated() -> None:
    """0.2.0 is released: the manual live pass is signed off and the heading has been
    stamped — it must carry a YYYY-MM-DD release date and must no longer say
    Unreleased."""
    heading = _changelog_heading("0.2.0")
    assert "Unreleased" not in heading, (
        f"0.2.0 should be released, not Unreleased: {heading!r}"
    )
    assert re.search(r"\d{4}-\d{2}-\d{2}", heading), (
        f"0.2.0 release heading must carry a date: {heading!r}"
    )
