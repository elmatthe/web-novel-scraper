"""v0.3.0 step 01.0: flat ``scripts/`` layout regression guards. All offline.

The package moved from ``scripts/Universal/`` to ``scripts/``. These checks pin
what that move could silently break: the import root, the repo-root derivation
behind the HTML cache and the contained Playwright browsers (one ``parents[]``
level too many puts both *outside* the repository), and the launchers' entry
point. A last end-to-end check drives a real fixture chapter through the real
FreeWebNovel parser and pipeline into a real PDF.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from pypdf import PdfReader

import app
import webnovel_scraper
from webnovel_scraper import browser_env, pipeline, request_manager
from webnovel_scraper.adapters.freewebnovel import FreeWebNovelAdapter, chapter_url_for
from webnovel_scraper.models import ChapterMeta, OutputMode, ScrapeJob
from webnovel_scraper.request_manager import RequestManager

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
FIXTURES = REPO_ROOT / "files" / "test-files"
BAT = REPO_ROOT / "Setup_and_Run-Web-Novel-Scraper.bat"
COMMAND = REPO_ROOT / "Setup_and_Run-Web-Novel-Scraper.command"


# ── Import root ──────────────────────────────────────────────────────────────
def test_package_and_app_import_from_flat_scripts_dir() -> None:
    assert Path(webnovel_scraper.__file__).resolve() == (
        SCRIPTS / "webnovel_scraper" / "__init__.py"
    )
    assert Path(app.__file__).resolve() == SCRIPTS / "app.py"
    assert not (SCRIPTS / "Universal").exists()


def test_app_entry_point_imports_without_test_sys_path() -> None:
    """Load scripts/app.py the way the launcher does: a fresh isolated interpreter
    (``-I``: no PYTHONPATH, no script dir) with cwd at the repo root, so only
    app.py's own ``sys.path`` line can make ``webnovel_scraper`` importable.
    Loaded as a module, not ``__main__``, so no window opens."""
    code = (
        "import importlib.util, pathlib\n"
        "spec = importlib.util.spec_from_file_location('app', 'scripts/app.py')\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "import webnovel_scraper\n"
        "print(pathlib.Path(webnovel_scraper.__file__).resolve())\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == SCRIPTS / "webnovel_scraper" / "__init__.py"


# ── Repo-contained runtime paths ─────────────────────────────────────────────
def test_html_cache_root_resolves_inside_repo() -> None:
    assert request_manager._REPO_ROOT == REPO_ROOT
    assert request_manager.DEFAULT_CACHE_ROOT == REPO_ROOT / "files" / "cache"
    manager = RequestManager("shadow-slave", use_cache=False)
    page = manager.cache_path_for("https://freewebnovel.com/novel/shadow-slave/chapter-1")
    assert page.parent == REPO_ROOT / "files" / "cache" / "freewebnovel.com" / "shadow-slave"
    assert page.is_relative_to(REPO_ROOT)
    # the pre-0.3.0 folder is still where legacy copies are read from
    assert manager.legacy_cache_dir == REPO_ROOT / "files" / "cache" / "shadow-slave"


def test_contained_browsers_path_resolves_inside_repo(monkeypatch) -> None:
    expected = REPO_ROOT / "files" / "bin" / "ms-playwright"
    assert browser_env.CONTAINED_BROWSERS_PATH == expected
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    assert Path(browser_env.ensure_browsers_path()) == expected


# ── Launcher entry points ────────────────────────────────────────────────────
def _assignment(launcher: Path, pattern: str) -> str:
    m = re.search(pattern, launcher.read_text(encoding="utf-8"), re.MULTILINE)
    assert m, f"no MAIN_SCRIPT assignment in {launcher.name}"
    return m.group(1)


def test_windows_launcher_targets_flat_app() -> None:
    main_script = _assignment(BAT, r'^set "MAIN_SCRIPT=([^"]+)"')
    assert main_script == r"scripts\app.py"
    assert (REPO_ROOT / main_script.replace("\\", "/")).resolve() == SCRIPTS / "app.py"


def test_macos_launcher_targets_flat_app() -> None:
    main_script = _assignment(COMMAND, r'^MAIN_SCRIPT="([^"]+)"')
    assert main_script == "scripts/app.py"
    assert (REPO_ROOT / main_script).resolve() == SCRIPTS / "app.py"


def test_launchers_have_no_stale_universal_paths() -> None:
    for launcher in (BAT, COMMAND):
        assert "Universal" not in launcher.read_text(encoding="utf-8"), launcher.name


# ── Retrieval -> PDF through the new layout ──────────────────────────────────
class _FixtureRM:
    """Offline request manager: every fetch returns the same fixture page."""

    def __init__(self, html: str) -> None:
        self.html = html

    def fetch(self, url, *, use_browser=False, use_cache=None) -> str:
        return self.html


class _OneChapterFWN(FreeWebNovelAdapter):
    """Real FWN chapter parsing behind a one-entry TOC (no index crawl)."""

    def build_chapter_index(self, spec) -> list[ChapterMeta]:
        return [ChapterMeta(index=5, url=chapter_url_for(spec, 5), source_id="5")]


def test_fixture_chapter_parses_and_renders_pdf(tmp_path) -> None:
    html = (FIXTURES / "fwn_chapter_clean.html").read_text(encoding="utf-8")
    job = ScrapeJob(
        novel_slug="shadow-slave",
        adapter_key="freewebnovel",
        start=5,
        end=5,
        delay=0.0,
        output_mode=OutputMode.SEPARATE,
        use_cache=False,
        output_dir=tmp_path,
    )
    report = pipeline.run_scrape(
        job, adapter=_OneChapterFWN(request_manager=_FixtureRM(html)), log=lambda m: None
    )

    assert report.failed == []
    pdfs = sorted(tmp_path.rglob("*.pdf"))
    assert [p.name for p in pdfs] == ["Chapter 5_ The Test.pdf"]
    text = " ".join(page.extract_text() or "" for page in PdfReader(str(pdfs[0])).pages)
    assert "The Test" in text
    assert "Sunny opened his eyes" in text
