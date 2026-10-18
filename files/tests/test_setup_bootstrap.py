"""Windows setup and launch readiness (v0.3.0 Checkpoint 07). Offline: nothing is downloaded,
no browser is started and the real ``.venv`` is never touched.

``scripts/setup_check.py`` is what the launcher asks before it trusts the environment (a working
Python 3.12+, importable packages, browser engines actually on disk), so its exit codes are the
contract tested here, together with the launcher's own structure and the window's start-up
warning for a missing engine.
"""

from __future__ import annotations

import gc
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import setup_check
from webnovel_scraper import browser_env

REPO_ROOT = Path(__file__).resolve().parents[2]
BAT = REPO_ROOT / "Setup_and_Run-Web-Novel-Scraper.bat"
CHECK = REPO_ROOT / "scripts" / "setup_check.py"


# ── setup_check: Python ───────────────────────────────────────────────────────
def test_this_interpreter_can_host_the_app():
    assert setup_check.check_python() == 0


@pytest.mark.parametrize("version,release", [((3, 11), "final"), ((3, 14), "final"), ((3, 10), "final"),
                                             ((3, 13), "candidate"), ((3, 12), "beta")])
def test_only_final_python_3_12_and_3_13_are_accepted(version, release, capsys):
    """A newer Python than the tested ones counts as missing, so the launcher offers 3.12 instead of
    failing later when a pinned package has no build for it."""
    assert setup_check.check_python(version, release) == 3
    assert "needs Python 3.12 or 3.13" in capsys.readouterr().out
    assert setup_check.check_python((3, 12), "final") == 0
    assert setup_check.check_python((3, 13), "final") == 0


def test_an_environment_is_never_built_from_a_virtual_environment(monkeypatch, capsys):
    monkeypatch.setattr(setup_check.sys, "prefix", r"C:\project\.venv")
    monkeypatch.setattr(setup_check.sys, "base_prefix", r"C:\Python313")
    assert setup_check.check_base() == 5
    assert "belongs to a virtual environment" in capsys.readouterr().out
    monkeypatch.setattr(setup_check.sys, "prefix", r"C:\Python313")
    assert setup_check.check_base() == 0


def test_the_base_check_runs_as_the_launcher_calls_it():
    result = subprocess.run([sys.executable, str(CHECK), "base"], cwd=REPO_ROOT, capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == (5 if sys.prefix != sys.base_prefix else 0), result.stdout


def test_a_python_without_tkinter_is_refused(monkeypatch, capsys):
    real = setup_check.importlib.import_module

    def without_tk(name, *a):
        if name == "tkinter":
            raise ImportError("no _tkinter")
        return real(name, *a)

    monkeypatch.setattr(setup_check.importlib, "import_module", without_tk)
    assert setup_check.check_python() == 4
    assert "lacks tkinter" in capsys.readouterr().out


# ── setup_check: packages ─────────────────────────────────────────────────────
_IMPORT_NAMES = {"beautifulsoup4": ["bs4"], "playwright-stealth": ["playwright_stealth"],
                 "camoufox[geoip]": ["camoufox", "geoip2"]}


def test_every_pinned_runtime_package_is_import_checked():
    pinned = [line.split("==")[0].strip() for line in
              (REPO_ROOT / "scripts" / "requirements.txt").read_text(encoding="utf-8").splitlines()
              if "==" in line and not line.lstrip().startswith("#")]
    expected = [name for pkg in pinned if pkg != "pytest" for name in _IMPORT_NAMES.get(pkg, [pkg])]
    assert sorted(expected) == sorted(setup_check.RUNTIME_MODULES)
    assert setup_check.check_deps() == 0


def test_a_missing_package_is_reported_by_name(monkeypatch, capsys):
    monkeypatch.setattr(setup_check, "RUNTIME_MODULES", ("requests", "no_such_module_wns"))
    assert setup_check.check_deps() == 1
    assert "no_such_module_wns (ModuleNotFoundError)" in capsys.readouterr().out


def test_an_unknown_check_prints_usage():
    assert setup_check.main(["setup_check.py"]) == 64
    assert setup_check.main(["setup_check.py", "everything"]) == 64


# ── browser readiness ─────────────────────────────────────────────────────────
def _revisions():
    import json

    import playwright
    listing = Path(playwright.__file__).parent / "driver" / "package" / "browsers.json"
    return {b["name"]: b["revision"] for b in json.loads(listing.read_text(encoding="utf-8"))["browsers"]}


def test_chromium_counts_only_when_both_builds_finished_installing(tmp_path):
    rev = _revisions()
    ok, detail = browser_env.chromium_status(tmp_path)
    assert not ok and f"chromium {rev['chromium']} missing" in detail
    (tmp_path / f"chromium-{rev['chromium']}").mkdir()
    (tmp_path / f"chromium-{rev['chromium']}" / "INSTALLATION_COMPLETE").write_text("")
    (tmp_path / f"chromium_headless_shell-{rev['chromium-headless-shell']}").mkdir()
    ok, detail = browser_env.chromium_status(tmp_path)
    assert not ok and "chromium-headless-shell" in detail                 # a half-finished download
    (tmp_path / f"chromium_headless_shell-{rev['chromium-headless-shell']}" / "INSTALLATION_COMPLETE").write_text("")
    assert browser_env.chromium_status(tmp_path) == (True, f"Chromium for Playwright installed in {tmp_path}")


def test_camoufox_counts_only_when_its_executable_is_on_disk(tmp_path, monkeypatch):
    import camoufox.pkgman as pkgman

    def not_installed(**_kw):
        raise FileNotFoundError("Camoufox is not installed")

    monkeypatch.setattr(pkgman, "camoufox_path", not_installed)
    ok, detail = browser_env.camoufox_status()
    assert not ok and "FileNotFoundError" in detail
    calls = []
    monkeypatch.setattr(pkgman, "camoufox_path", lambda **kw: calls.append(kw) or tmp_path)
    assert browser_env.camoufox_status()[0] is False                     # folder without the browser
    (tmp_path / pkgman.LAUNCH_FILE[pkgman.OS_NAME]).write_bytes(b"MZ")
    monkeypatch.setattr(pkgman, "installed_verstr", lambda: "135.0.1-beta.24")
    assert browser_env.camoufox_status()[0] is True
    assert all(kw == {"download_if_missing": False} for kw in calls)       # a check never downloads


def test_the_geoip_database_must_be_present_and_plausible(tmp_path, monkeypatch):
    import camoufox.locale as locale

    small = tmp_path / "GeoLite2-City.mmdb"
    small.write_bytes(b"x" * 100)
    monkeypatch.setattr(locale, "MMDB_FILE", small)
    assert browser_env.geoip_status()[0] is False
    with open(small, "r+b") as handle:
        handle.truncate(2_000_000)
    assert browser_env.geoip_status()[0] is True


def test_readiness_adds_up_the_missing_engines(monkeypatch):
    monkeypatch.setattr(browser_env, "camoufox_status", lambda: (False, "Camoufox browser missing"))
    monkeypatch.setattr(browser_env, "geoip_status", lambda: (True, "GeoIP ok"))
    monkeypatch.setattr(browser_env, "chromium_status", lambda: (False, "Chromium missing"))
    mask, lines = browser_env.browser_readiness()
    assert mask == browser_env.MISSING_CAMOUFOX | browser_env.MISSING_CHROMIUM == 5
    assert lines == ["MISSING Camoufox browser missing", "OK   GeoIP ok", "MISSING Chromium missing"]


def test_the_launcher_check_reports_a_missing_chromium_as_bit_4(tmp_path):
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(tmp_path))
    result = subprocess.run([sys.executable, str(CHECK), "browsers"], cwd=REPO_ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode & browser_env.MISSING_CHROMIUM
    assert "MISSING Chromium for Playwright not ready" in result.stdout


# ── the launcher ──────────────────────────────────────────────────────────────
def _bat() -> str:
    return BAT.read_text(encoding="utf-8")


def test_the_launcher_requires_a_validated_python_3_12_or_newer():
    text = _bat()
    assert "for %%V in (3.13 3.12) do" in text                         # tested versions first
    assert '"%CHECK%" python' in text                                  # each candidate actually runs
    assert "PY_MIN" not in text                                        # no bare version probe:
    assert not re.search(r"(?:py|python)(?: -[0-9.]+)? --version", text)   # a Store stub "runs" too


def test_the_launcher_trusts_no_marker_files_and_repairs_only_what_is_missing():
    text = _bat()
    assert ".fetched" not in text
    assert '"%CHECK%" browsers' in text and '"%CHECK%" deps' in text
    assert '-m camoufox fetch' in text and "download_mmdb()" in text
    assert "-m playwright install chromium" in text
    assert 'set "PLAYWRIGHT_BROWSERS_PATH=%CD%\\files\\bin\\ms-playwright"' in text


def test_the_launcher_keeps_start_up_errors_and_shows_them():
    text = _bat()
    assert '"%VENV_PYW%" "%MAIN_SCRIPT%" 2> "%LAUNCH_LOG%"' in text
    assert 'type "%LAUNCH_LOG%"' in text


def test_the_launcher_survives_unusual_folder_names():
    text = _bat()
    assert "DisableDelayedExpansion" in text and "EnableDelayedExpansion" not in text
    assert not re.search(r"![A-Za-z_]+!", text)                       # no delayed-expansion variables
    assert "echo   Folder: \"%CD%\"" in text


def test_the_launcher_refuses_a_folder_too_deep_for_windows_to_load_from():
    """Windows loads no .pyd/.dll/.exe from a path over 259 characters (long-path support does not
    cover it); the deepest one measured was 107 characters inside the folder (Chromium's
    headless shell). Found by running the launcher from a deep folder: imports failed with
    "The filename or extension is too long"."""
    text = _bat()
    limit = int(re.search(r'^set "MAX_FOLDER_LEN=(\d+)"', text, re.MULTILINE).group(1))
    assert limit + 110 <= 259
    assert text.index("call :measure_folder") < text.index("[Step 1 of 4]")


def test_python_variables_left_by_other_tools_are_ignored():
    text = _bat()
    for name in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH"):
        assert f'set "{name}="' in text
    assert text.index('set "VIRTUAL_ENV="') < text.index("[Step 1 of 4]")


def test_tested_python_installs_are_preferred_over_whatever_python_comes_first_on_path():
    text = _bat()
    finder = text[text.index("\n:find_python\n"):text.index("\n:found_python\n")]
    order = [finder.index(marker) for marker in
             ("py -%%V", r"Programs\Python\Python313", r"Programs\Python\Python312", "py -3 ", "python \"%CHECK%\"")]
    assert order == sorted(order)


def test_every_launcher_jump_has_a_target_and_every_check_exists():
    text = _bat()
    labels = set(re.findall(r"^:([A-Za-z_]+)\s*$", text, re.MULTILINE))
    targets = set(re.findall(r"(?:goto|call) :([A-Za-z_]+)", text, re.IGNORECASE)) - {"eof"}
    assert targets <= labels, targets - labels
    checks = set(re.findall(r'"%CHECK%" ([a-z]+)(?=\s*(?:>|$))', text, re.MULTILINE))
    assert checks == {"python", "base", "deps", "browsers"}
    finder = text[text.index("\n:find_python\n"):text.index("\n:found_python\n")]
    assert '"%CHECK%" python' not in finder                            # every candidate: base


def test_python_is_installed_with_winget_or_the_verified_python_org_installer():
    text = _bat()
    offer = text[text.index("\n:offer_python_install\n"):text.index("\n:: rebuild_venv")]
    assert offer.index("winget install") < offer.index('-File "%PY_INSTALLER%"')   # fallback after it
    assert offer.index('set "PSModulePath="') < offer.index("powershell")
    assert offer.rstrip().endswith("exit /b %errorlevel%") and "call :find_python" in offer
    script = (REPO_ROOT / "scripts" / "install_python.ps1").read_text(encoding="utf-8")
    assert "python-3.12.10-amd64.exe" in script
    assert "67B5635E80EA51072B87941312D00EC8927C4DB9BA18938F7AD2D27B328B95FB" in script
    assert "Get-AuthenticodeSignature" in script and "Python Software Foundation" in script
    for argument in ("InstallAllUsers=0", "PrependPath=0", "Include_tcltk=1"):
        assert argument in script
    assert "files\\runtime-data\\bootstrap" in script                     # inside the folder, ignored


def test_a_read_only_folder_and_a_launcher_run_from_inside_the_zip_are_explained():
    text = _bat()
    assert text.index("goto :fail_readonly") < text.index("[Step 1 of 4]")
    assert "Extract All" in text


def test_the_launcher_is_checked_out_with_windows_line_endings():
    assert b"\r\n" in BAT.read_bytes() and b"\n" not in BAT.read_bytes().replace(b"\r\n", b"")


# ── the window ────────────────────────────────────────────────────────────────
def test_the_window_warns_at_start_up_about_a_missing_engine(tmp_path, monkeypatch):
    import app
    from webnovel_scraper.ui.settings import SettingsStore

    tk = pytest.importorskip("tkinter")
    monkeypatch.setattr(browser_env, "browser_readiness",
                        lambda: (4, ["OK   Camoufox", "MISSING Chromium for Playwright not ready (x)"]))
    store = SettingsStore(tmp_path / "runtime-data" / "settings.json")
    store.load()
    gc.collect()
    try:
        win = app.ScraperApp(store, check_browsers=True)
    except tk.TclError as exc:
        pytest.skip(f"no Tk display available: {exc}")
    try:
        for thread in [t for t in __import__("threading").enumerate() if t.name == "browser-readiness"]:
            thread.join(5)
        win.flush_activity()
        summary = win._log_texts[app.SUMMARY_TAB].get("1.0", "end-1c")
        assert "Chromium for Playwright not ready (x). Free Web Novel (.com) uses it" in summary
        assert "Camoufox" not in summary
    finally:
        win.destroy()


def test_a_window_opened_by_tests_or_tools_does_not_check_browsers(monkeypatch, tmp_path):
    import inspect

    import app
    assert inspect.signature(app.ScraperApp).parameters["check_browsers"].default is False
    assert "check_browsers=True" in inspect.getsource(app.main)


def test_dpi_awareness_is_a_no_op_off_windows(monkeypatch):
    import app
    monkeypatch.setattr(app.sys, "platform", "linux")
    app._enable_dpi_awareness()                                        # must not touch ctypes.windll
