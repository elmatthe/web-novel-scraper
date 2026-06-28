@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

:: ============================================================================
::  Setup_and_Run-Web-Novel-Scraper  --  Windows setup + launcher
:: ============================================================================
::  WHAT THIS FILE DOES (per AI-WORKSPACE.md "Setup and Launch Files")
::  ------------------------------------------------------------------
::  One double-click does everything for a NON-TECHNICAL user:
::    1. Scans the PC for Python (the one unavoidable system dependency).
::    2. If Python is missing, asks Y/N and installs it FOR THE CURRENT USER
::       only (user scope, no admin).
::    3. Creates a fresh .venv IN THE REPO ROOT and installs all dependencies
::       (scripts\requirements.txt) into it (never system-wide).
::    4. Downloads the camoufox browser engine (the Cloudflare bypass this
::       project's retry ladder uses) for the Cloudflare/browser fetch path.
::    5. Launches the GUI (scripts\Universal\app.py). On every later run it acts
::       as the launcher.
::
::  SELF-HEALING: delete .venv (to move/shrink/reset the repo) and re-run -- it
::  rebuilds from scratch. Delete Python and re-run -- it detects the absence
::  and offers to reinstall. Re-running always returns you to a working state.
::
::  Goal: the MINIMUM installed on the PC; everything else contained in the repo
::  and the venv.
:: ============================================================================

:: ============================================================================
::  Configuration
:: ============================================================================
set "PROJECT_NAME=Web-Novel-Scraper"

:: Minimum Python major.minor this project supports (platform-neutral package).
set "PY_MIN_MAJOR=3"
set "PY_MIN_MINOR=10"
:: Version winget installs if Python must be installed from scratch.
set "PYTHON_WINGET_ID=Python.Python.3.12"
:: Official python.org fallback if winget is unavailable.
set "PYTHON_INSTALLER_URL=https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"

:: requirements.txt lives in scripts\ per AI-WORKSPACE.md.
set "REQUIREMENTS=scripts\requirements.txt"

:: Single-window GUI entry point (no separate launcher.py for this project).
set "MAIN_SCRIPT=scripts\Universal\app.py"

:: This project does not use ffmpeg.
set "USE_FFMPEG=0"

:: Where in-repo tools/binaries are kept. Self-contained, nothing installed.
set "BIN_DIR=%~dp0files\bin"

:: ============================================================================
::  Banner + first-run security note
:: ============================================================================
cls
echo ========================================
echo   %PROJECT_NAME% - Setup ^& Launcher
echo   Folder: %~dp0
echo ========================================
echo.
echo   This window sets up and launches the program. Setup keeps everything
echo   inside this project folder where it can. You should not need to install
echo   anything system-wide unless a required tool (Python) is completely
echo   missing from this PC -- and it will ask you first if so.
echo.
echo   FIRST-RUN NOTE: Because this file came from the internet, Windows (or
echo   your work security software) may warn you the first time. If you see
echo   "Windows protected your PC", click "More info" then "Run anyway".
echo   This is normal and only happens once.
echo.

:: ============================================================================
::  STEP 1 - Ensure Python (the only unavoidable system dependency)
:: ============================================================================
echo [Step 1 of 5] Checking for Python...
call :detect_python
if not defined PYTHON_OK (
    echo.
    echo   Python is not installed on this PC, and it is required to run this
    echo   program. This is the ONLY tool that has to be installed onto the
    echo   computer itself - everything else stays in this folder.
    echo.
    set "do_py="
    set /p do_py=Install Python now? ^(Y/N^):
    if /i "!do_py!"=="Y" (
        call :install_python
        rem PATH may be stale in this window after a fresh install; re-detect.
        call :detect_python
        if not defined PYTHON_OK (
            echo.
            echo   Python was installed but isn't visible in THIS window yet.
            echo   Close this window, re-open it, and run this file again so the
            echo   updated PATH takes effect.
            echo.
            pause
            exit /b 1
        )
    ) else (
        echo.
        echo   Python is required. You can install it manually from
        echo     https://www.python.org/downloads/
        echo   During install, check "Add python.exe to PATH", then run this
        echo   file again.
        echo.
        pause
        exit /b 1
    )
)

:: Warn (don't block) if the present Python is older than the project minimum.
call :check_python_version

:: ============================================================================
::  STEP 2 - Virtual environment (self-healing; everything below stays in repo)
:: ============================================================================
echo [Step 2 of 5] Preparing the in-folder virtual environment...
set "VENV_OK="
if exist ".venv\Scripts\activate.bat" set "VENV_OK=1"

:: NOTE: below, %PYTHON_CMD% is used UNQUOTED on purpose. It may be the two-token
:: command "py -3"; wrapping that in quotes makes cmd look for an executable
:: literally named "py -3" and fail with errorlevel 9009. We already cd'd into
:: %~dp0, so ".venv" is a relative, space-free path that needs no quoting.
if not defined VENV_OK (
    if exist ".venv" (
        echo   Existing .venv looks incomplete - removing it to rebuild cleanly...
        rmdir /s /q ".venv"
        if exist ".venv" (
            echo.
            echo   ERROR: Could not remove the old .venv folder. A previous run is
            echo   probably still open and holding it open ^("open in another
            echo   program"^). Close any open %PROJECT_NAME% windows, then in Task
            echo   Manager end any stray python.exe / pythonw.exe processes, and
            echo   run this file again.
            echo.
            pause
            exit /b 1
        )
    ) else (
        echo   Creating a new virtual environment in this folder...
    )
    %PYTHON_CMD% -m venv .venv
    if !errorlevel! neq 0 (
        echo.
        echo   ERROR: Failed to create the virtual environment using:
        echo       %PYTHON_CMD% -m venv .venv
        echo   Make sure Python installed correctly, then run this file again.
        echo.
        pause
        exit /b 1
    )
)

if not exist ".venv\Scripts\activate.bat" (
    echo.
    echo   ERROR: The virtual environment is missing its activation script.
    echo   Delete the .venv folder in this directory and run this file again.
    echo.
    pause
    exit /b 1
)
call ".venv\Scripts\activate.bat"

:: ============================================================================
::  STEP 3 - Dependencies (into the venv - never system-wide, installed quietly)
:: ============================================================================
:: Idempotent installs: only (re)install when requirements.txt has changed since
:: the last successful run, or when the venv was just rebuilt (no lock present).
:: A copy of requirements.txt is stored at .venv\requirements.lock after a good
:: install; a binary compare against it tells us whether anything changed. This
:: is what makes a second double-click skip straight to launch.
if exist "%REQUIREMENTS%" (
    set "NEED_DEPS=1"
    if exist ".venv\requirements.lock" (
        fc /b "%REQUIREMENTS%" ".venv\requirements.lock" >nul 2>&1
        if !errorlevel! equ 0 set "NEED_DEPS="
    )
    if defined NEED_DEPS (
        echo [Step 3 of 5] Installing dependencies into the project environment...
        python -m pip install --upgrade pip >nul 2>&1
        python -m pip install -r "%REQUIREMENTS%"
        if !errorlevel! neq 0 (
            echo   ERROR: Some dependencies failed to install. See messages above.
            pause
            exit /b 1
        )
        copy /y "%REQUIREMENTS%" ".venv\requirements.lock" >nul 2>&1
    ) else (
        echo [Step 3 of 5] Dependencies already up to date - skipping install.
    )
) else (
    echo [Step 3 of 5] Note: No requirements.txt at %REQUIREMENTS% - skipping deps.
)

:: ============================================================================
::  STEP 3b - Browser engine for the Cloudflare / browser fetch path
:: ============================================================================
:: FreeWebNovel sits behind Cloudflare. The bypass engine this project actually
:: uses is camoufox (an anti-detect Firefox) - see scripts\requirements.txt,
:: which pins camoufox[geoip] as "the only path that clears FreeWebNovel". Its
:: browser binary is downloaded with "python -m camoufox fetch" (~once, into the
:: per-user camoufox cache; no admin, nothing system-wide). A marker file in the
:: venv lets later launches skip straight past this.
if not exist ".venv\camoufox.fetched" (
    echo [Step 4 of 5] Downloading the camoufox browser engine ^(~once^)...
    python -m camoufox fetch
    if !errorlevel! neq 0 (
        echo   WARNING: The camoufox browser download did not complete. The plain
        echo            HTTP path still works, but the Cloudflare/browser bypass
        echo            ^(needed for FreeWebNovel^) will not until this succeeds.
        echo            Re-run this file to retry.
        echo.
    ) else (
        echo done> ".venv\camoufox.fetched"
    )
) else (
    echo [Step 4 of 5] camoufox browser engine already downloaded - skipping.
)

:: ============================================================================
::  STEP 4 - Optional self-contained ffmpeg (unused by this project)
:: ============================================================================
if "%USE_FFMPEG%"=="1" call :ensure_ffmpeg
if exist "%BIN_DIR%" set "PATH=%BIN_DIR%;%PATH%"

:: ============================================================================
::  STEP 5 - Launch the GUI
:: ============================================================================
if not exist "%MAIN_SCRIPT%" (
    echo.
    echo   ERROR: Could not find the program's entry point:
    echo     %MAIN_SCRIPT%
    echo   The repo may be incomplete - re-download it and try again.
    echo.
    pause
    exit /b 1
)

echo.
echo [Step 5 of 5] Launching %PROJECT_NAME%...
echo   ^(Keep this window open while you use the program. To stop, close the
echo    program's own window - do not just close this one, or a background
echo    process can be left holding the .venv folder open.^)
echo.
:: Launch with pythonw (the GUI/no-console Python) so the tkinter window is the
:: ONLY new window the user sees - no extra console pops up behind it. This setup
:: window stays open as the live log. Fall back to plain python only if the venv
:: somehow lacks pythonw.exe.
set "PYW=.venv\Scripts\pythonw.exe"
if exist "%PYW%" (
    "%PYW%" "%MAIN_SCRIPT%"
) else (
    python "%MAIN_SCRIPT%"
)
set "RUN_EXIT=%errorlevel%"

echo.
if not "%RUN_EXIT%"=="0" (
    echo Program exited with code %RUN_EXIT%.
) else (
    echo Program finished.
)
pause
endlocal
exit /b %RUN_EXIT%


:: ============================================================================
::  Helpers
:: ============================================================================

:: ----------------------------------------------------------------------------
:: detect_python - set PYTHON_OK and PYTHON_CMD if a usable Python is found.
:: Tries the "py" launcher first (most reliable on Windows), then "python",
:: then an existing in-repo venv interpreter.
:: ----------------------------------------------------------------------------
:detect_python
set "PYTHON_OK="
set "PYTHON_CMD="
py -%PY_MIN_MAJOR% --version >nul 2>&1
if !errorlevel! equ 0 (
    set "PYTHON_CMD=py -%PY_MIN_MAJOR%"
    set "PYTHON_OK=1"
    goto :eof
)
python --version >nul 2>&1
if !errorlevel! equ 0 (
    set "PYTHON_CMD=python"
    set "PYTHON_OK=1"
    goto :eof
)
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" --version >nul 2>&1
    if !errorlevel! equ 0 (
        set "PYTHON_CMD=.venv\Scripts\python.exe"
        set "PYTHON_OK=1"
    )
)
goto :eof

:: ----------------------------------------------------------------------------
:: check_python_version - warn if the detected Python is below the minimum.
:: ----------------------------------------------------------------------------
:check_python_version
:: "Python 3.13.12" -> tokens split on space and dot -> token 3 is the MINOR (13).
for /f "tokens=3 delims=. " %%a in ('%PYTHON_CMD% --version 2^>^&1') do set "FOUND_MINOR=%%a"
if defined FOUND_MINOR (
    if !FOUND_MINOR! lss %PY_MIN_MINOR% (
        echo   WARNING: Detected Python 3.!FOUND_MINOR!, but this project targets
        echo            %PY_MIN_MAJOR%.%PY_MIN_MINOR% or newer. Some features may not work.
        echo.
    )
)
goto :eof

:: ----------------------------------------------------------------------------
:: install_python - install Python for the current user only.
:: ----------------------------------------------------------------------------
:install_python
echo   Installing Python for the current user only (no admin)...
where winget >nul 2>&1
if %errorlevel% equ 0 (
    winget install -e --id %PYTHON_WINGET_ID% --scope user --accept-source-agreements --accept-package-agreements
    goto :eof
)

echo   Windows Package Manager (winget) is not available.
echo   Downloading the official Python installer from python.org...
set "PYTHON_INSTALLER=%TEMP%\python-3.12-user-installer.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri '%PYTHON_INSTALLER_URL%' -OutFile '%PYTHON_INSTALLER%'"
if !errorlevel! neq 0 (
    echo   ERROR: Could not download Python. You can install it manually from:
    echo     https://www.python.org/downloads/
    echo   During install, check "Add python.exe to PATH", then run this file again.
    echo.
    pause
    goto :eof
)
"%PYTHON_INSTALLER%" /passive InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_pip=1
goto :eof

:: ----------------------------------------------------------------------------
:: ensure_ffmpeg - prefer a self-contained in-repo copy; no system install.
:: (Unused by this project; kept for parity with the template.)
:: ----------------------------------------------------------------------------
:ensure_ffmpeg
where ffmpeg >nul 2>&1
if %errorlevel% equ 0 goto :eof
if exist "%BIN_DIR%\ffmpeg.exe" goto :eof
goto :eof
