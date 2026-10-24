"""Explicit Windows acceptance smoke; never run by pytest or against owner runtime data.

Run with a working development Python: python files/tests/windows_clean_install_smoke.py
Creates ONE new, ignored source-only installation in files/run-temp/i (must not exist).
Runs the real batch launcher twice, observes/closes its actual GUI, and checks dependency reuse.
Browser downloads require network access. No live novel downloads are performed.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "files/run-temp/i"
EVIDENCE = ROOT / "files/run-temp/final030"
LAUNCHER = "Setup_and_Run-Web-Novel-Scraper.bat"


def gui_window(executable: Path):
    user, kernel = ctypes.windll.user32, ctypes.windll.kernel32
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    found = []
    # Windows venv pythonw.exe redirects to a child running the base pythonw.exe.
    rows = json.loads(subprocess.check_output([
        "powershell.exe", "-NoProfile", "-Command",
        "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
        "Select-Object ProcessId,ParentProcessId,ExecutablePath | ConvertTo-Json -Compress",
    ], text=True) or "[]")
    rows = rows if isinstance(rows, list) else [rows]
    parents = {r["ProcessId"] for r in rows if r.get("ExecutablePath")
               and Path(r["ExecutablePath"]) == executable}
    eligible = parents | {r["ProcessId"] for r in rows if r["ParentProcessId"] in parents}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        title = ctypes.create_unicode_buffer(512)
        user.GetWindowTextW(hwnd, title, len(title))
        if title.value != "Web Novel Scraper" or not user.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in eligible:
            return True
        handle = kernel.OpenProcess(0x1000, False, pid.value)
        if handle:
            path, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
            if kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)):
                if Path(path.value) == executable or pid.value in eligible:
                    found.append((hwnd, pid.value))
            kernel.CloseHandle(handle)
        return True

    user.EnumWindows(visit, 0)
    return found


def launch(number: int, env: dict) -> dict:
    log = EVIDENCE / f"clean-launch-{number}.log"
    with log.open("wb") as output:
        process = subprocess.Popen(["cmd.exe", "/d", "/c", LAUNCHER], cwd=INSTALL, env=env,
                                   stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        # Only the final PAUSE consumes this key on a successful setup.
        process.stdin.write(b"\r\n")
        process.stdin.flush()
        deadline = time.monotonic() + 1500
        while time.monotonic() < deadline:
            windows = gui_window(INSTALL / ".venv/Scripts/pythonw.exe")
            if windows:
                hwnd, pid = windows[0]
                time.sleep(4)  # allow the real GUI's startup/readiness pump to run
                ctypes.windll.user32.PostMessageW(wintypes.HWND(hwnd), 0x0010, 0, 0)
                result = process.wait(timeout=45)
                assert result == 0, f"Launcher exit {result}; see {log}"
                text = log.read_text(encoding="utf-8", errors="replace")
                assert "Browser engines are ready." in text, text[-3000:]
                assert "Program finished." in text, text[-1000:]
                assert not (INSTALL / "files/runtime-data/logs/last-launch-errors.log").read_bytes()
                return {"gui_opened": True, "pid": pid, "exit": result}
            if process.poll() is not None:
                raise AssertionError(f"Launcher exited before GUI appeared; see {log}")
            time.sleep(1)
        process.terminate()
        raise TimeoutError(f"Launcher did not open the GUI within 25 minutes; see {log}")


def main() -> None:
    assert sys.platform == "win32", "Windows smoke only"
    finish_existing = sys.argv[1:] == ["--finish-existing"]
    repair_existing = sys.argv[1:] == ["--repair-existing"]
    assert not INSTALL.exists() or finish_existing or repair_existing, f"Refusing to reuse or delete {INSTALL}"
    assert len(str(INSTALL)) <= 140
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    paths = subprocess.check_output(["git", "ls-files", "scripts"], cwd=ROOT, text=True).splitlines()
    if not finish_existing and not repair_existing:
        for relative in [LAUNCHER, *paths]:
            source, destination = ROOT / relative, INSTALL / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        assert not (INSTALL / ".venv").exists() and not (INSTALL / "files").exists()
        (EVIDENCE / "clean-initial-state.json").write_text(json.dumps({
            "initial_venv_absent": True, "initial_files_absent": True,
            "path_length": len(str(INSTALL)),
        }), encoding="utf-8")
    env = os.environ.copy()
    # platformdirs' supported override isolates Camoufox's per-user cache from owner data.
    env["WIN_PD_OVERRIDE_LOCAL_APPDATA"] = str(INSTALL / "files/bin/user-cache")
    env["PIP_CACHE_DIR"] = str(INSTALL / "files/cache/pip")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if repair_existing:
        results = json.loads((EVIDENCE / "clean-install-result.json").read_text(encoding="utf-8"))
        results["damaged_package_repaired"] = repair(env)
        (EVIDENCE / "clean-install-result.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(json.dumps(results, indent=2), flush=True)
        return
    results = {"initial_venv_absent": True, "initial_files_absent": True,
               "path_length": len(str(INSTALL))}
    if finish_existing:
        # Continue the SAME first installation if the observer needed correction; never rebuild it.
        windows = gui_window(INSTALL / ".venv/Scripts/pythonw.exe")
        assert len(windows) == 1, "Expected exactly one GUI from the isolated installation"
        hwnd, pid = windows[0]
        time.sleep(4)
        ctypes.windll.user32.PostMessageW(wintypes.HWND(hwnd), 0x0010, 0, 0)
        for _ in range(30):
            text = (EVIDENCE / "clean-launch-1.log").read_text(encoding="utf-8", errors="replace")
            if "Program finished." in text:
                break
            time.sleep(1)
        assert "Program finished." in text and "Browser engines are ready." in text
        results["first_launch"] = {"gui_opened": True, "pid": pid, "exit": 0,
                                   "observer_corrected_for_venv_redirector": True}
    else:
        results["first_launch"] = launch(1, env)
    python = INSTALL / ".venv/Scripts/python.exe"
    check = [str(python), "-B", "scripts/setup_check.py"]
    for kind in ("python", "deps", "browsers"):
        subprocess.run([*check, kind], cwd=INSTALL, env=env, check=True)
    subprocess.run([str(python), "-m", "pip", "check"], cwd=INSTALL, env=env, check=True)
    # Confirm cache/manifest/output creation on first use with one synthetic chapter.
    probe = r'''
import sys
sys.path.insert(0, 'scripts')
from pathlib import Path
from webnovel_scraper import pipeline
from webnovel_scraper.models import ChapterMeta, ChapterContent, OutputMode, ScrapeJob
from webnovel_scraper.html_cache import HtmlCache
from webnovel_scraper.browser_env import browser_readiness
import camoufox.pkgman as pkgman
assert Path(pkgman.INSTALL_DIR).resolve().is_relative_to(Path.cwd())
class Site:
    def build_chapter_index(self, spec, **kw):
        return [ChapterMeta(1, spec.url + '/chapter-1', title='Smoke')]
    def fetch_chapter(self, meta, spec, **kw):
        return ChapterContent(1, 'Smoke', ['Synthetic first-use text.'])
job = ScrapeJob(novel_slug='shadow-slave', adapter_key='freewebnovel', start=1, end=1,
                delay=0, output_mode=OutputMode.SEPARATE, formats=('pdf', 'txt'),
                output_dir=Path('files/output/smoke'), allocate_run=True, use_cache=False)
report = pipeline.run_scrape(job, adapter=Site(), log=print, sleep_fn=lambda s: None)
assert not report.failed and len(report.written) == 2
cache = HtmlCache(Path('files/cache/html'), 'shadow-slave', site_key='freewebnovel.com')
assert cache.store('https://freewebnovel.com/novel/shadow-slave/chapter-1',
                   '<html><body><h1>Chapter 1: Smoke</h1><div id="chr-content"><p>' +
                   'Synthetic first-use story paragraph. ' * 20 + '</p></div></body></html>', strategy='http')
assert list(Path('files/runtime-data/chapter-lists').rglob('*.json'))
assert browser_readiness()[0] == 0
print('SOURCE-ONLY FIRST USE: PASS')
'''
    subprocess.run([str(python), "-B", "-c", probe], cwd=INSTALL, env=env, check=True)
    lock = INSTALL / ".venv/requirements.lock"
    before = lock.stat().st_mtime_ns
    results["second_launch"] = launch(2, env)
    text = (EVIDENCE / "clean-launch-2.log").read_text(encoding="utf-8", errors="replace")
    assert "Packages are installed and up to date." in text and lock.stat().st_mtime_ns == before
    results["healthy_dependencies_reused"] = True
    results["damaged_package_repaired"] = repair(env)
    results["first_use_cache_manifest_and_output"] = True
    results["python"] = subprocess.check_output([str(python), "--version"], text=True).strip()
    results["result"] = "PASS"
    (EVIDENCE / "clean-install-result.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2), flush=True)


def repair(env: dict) -> bool:
    """Exercise actual self-repair in this same installation, preserving a test-only backup."""
    python = INSTALL / ".venv/Scripts/python.exe"
    package = INSTALL / ".venv/Lib/site-packages/pypdf"
    backup = package.with_name("_wns_smoke_pypdf_backup")
    assert package.resolve().is_relative_to(INSTALL.resolve()) and not backup.exists()
    package.rename(backup)
    check = [str(python), "-B", "scripts/setup_check.py", "deps"]
    assert subprocess.run(check, cwd=INSTALL, env=env).returncode == 1
    # Show why the old install command could not repair this: metadata still says installed.
    subprocess.run([str(python), "-m", "pip", "install", "--no-index", "--no-deps", "-r",
                    "scripts/requirements.txt"], cwd=INSTALL, env=env, check=True)
    assert subprocess.run(check, cwd=INSTALL, env=env).returncode == 1
    shutil.copy2(ROOT / LAUNCHER, INSTALL / LAUNCHER)
    result = launch(3, env)
    subprocess.run(check, cwd=INSTALL, env=env, check=True)
    subprocess.run([str(python), "-m", "pip", "check"], cwd=INSTALL, env=env, check=True)
    assert result["gui_opened"] and (package / "__init__.py").is_file()
    return True


if __name__ == "__main__":
    main()
