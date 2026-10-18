@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

:: ============================================================================
::  Setup_and_Run-Web-Novel-Scraper  --  Windows setup, repair and launcher
:: ============================================================================
::  One double-click does everything for a NON-TECHNICAL user, and the same
::  double-click repairs whatever is missing:
::    1. Checks the in-folder Python environment (.venv). If it is missing or
::       broken, finds a working Python 3.12 or 3.13 on this PC (asking to
::       install Python 3.12 for the current user only if there is none) and
::       rebuilds .venv from it.
::    2. Installs the pinned packages (scripts\requirements.txt) into .venv when
::       they changed or no longer import - never system-wide.
::    3. Checks the browser engines that are actually on disk (Camoufox, its
::       GeoIP data, and Chromium for Playwright in files\bin\ms-playwright) and
::       downloads only what is missing.
::    4. Starts the program (scripts\app.py).
::
::  Every check is real (scripts\setup_check.py runs the interpreter, imports the
::  packages, looks for the browser files); no step trusts a marker file.
::  Delete .venv and run this file again to rebuild it from scratch.
::
::  (Delayed expansion is off on purpose: a folder name containing "!" or "&"
::  must not break this script.)
:: ============================================================================

set "PROJECT_NAME=Web-Novel-Scraper"
set "REQUIREMENTS=scripts\requirements.txt"
set "MAIN_SCRIPT=scripts\app.py"
set "CHECK=scripts\setup_check.py"
set "VENV_PY=.venv\Scripts\python.exe"
set "VENV_PYW=.venv\Scripts\pythonw.exe"
set "LOCK=.venv\requirements.lock"
set "LOG_DIR=files\runtime-data\logs"
set "LAUNCH_LOG=files\runtime-data\logs\last-launch-errors.log"
:: Python installed when none is usable (supported: 3.12 and 3.13): the Windows Package
:: Manager when it is there, else the verified python.org installer (scripts\install_python.ps1).
set "PYTHON_WINGET_ID=Python.Python.3.12"
set "PY_INSTALLER=scripts\install_python.ps1"
:: Browsers stay inside this folder (the program sets the same default itself).
set "PLAYWRIGHT_BROWSERS_PATH=%CD%\files\bin\ms-playwright"
:: An activated environment or a Python variable left by another tool must not decide which
:: Python is used or where it loads modules from (cleared for this window only).
set "VIRTUAL_ENV="
set "PYTHONHOME="
set "PYTHONPATH="
:: Windows cannot load a program part (.pyd/.dll/.exe) whose full path is longer than 259
:: characters, and the deepest part sits about 110 characters inside this folder.
set "MAX_FOLDER_LEN=140"

cls
echo ========================================
echo   %PROJECT_NAME% - Setup and Launcher
echo ========================================
echo   Folder: "%CD%"
echo.
echo   This window checks, repairs and starts the program. Everything is kept
echo   inside this folder except Python itself, which is only installed (after
echo   asking you) if no usable Python is on this PC.
echo.
echo   FIRST-RUN NOTE: Windows may say "Windows protected your PC" the first time.
echo   Click "More info" and then "Run anyway". This only happens once.
echo.

if not exist "%MAIN_SCRIPT%" goto :missing_program
if not exist "%CHECK%" goto :missing_program
call :measure_folder
if %FOLDER_LEN% GTR %MAX_FOLDER_LEN% goto :fail_long_path
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
(echo ok> "%LOG_DIR%\write-check.tmp") 2>nul
if not exist "%LOG_DIR%\write-check.tmp" goto :fail_readonly
del "%LOG_DIR%\write-check.tmp" >nul 2>&1

:: ============================================================================
::  STEP 1 - The in-folder Python environment
:: ============================================================================
echo [Step 1 of 4] Checking the program's Python environment...
call :venv_healthy
if not errorlevel 1 (
    echo   The environment is ready.
    goto :deps
)
echo   The environment is missing or incomplete; it will be (re)built.
call :find_python
if errorlevel 1 call :offer_python_install
if errorlevel 1 goto :fail_python
call :rebuild_venv
if errorlevel 1 goto :fail_venv

:: ============================================================================
::  STEP 2 - Packages (into .venv only)
:: ============================================================================
:deps
set "NEED_DEPS=1"
if exist "%LOCK%" (
    fc /b "%REQUIREMENTS%" "%LOCK%" >nul 2>&1
    if not errorlevel 1 set "NEED_DEPS="
)
if not defined NEED_DEPS (
    "%VENV_PY%" "%CHECK%" deps >nul 2>&1
    if errorlevel 1 set "NEED_DEPS=1"
)
if not defined NEED_DEPS (
    echo [Step 2 of 4] Packages are installed and up to date.
    goto :browsers
)
echo [Step 2 of 4] Installing the program's packages into this folder ^(a few minutes the first time^)...
"%VENV_PY%" -m pip install --disable-pip-version-check -r "%REQUIREMENTS%"
if errorlevel 1 goto :fail_deps
"%VENV_PY%" "%CHECK%" deps
if errorlevel 1 goto :fail_deps
copy /y "%REQUIREMENTS%" "%LOCK%" >nul 2>&1

:: ============================================================================
::  STEP 3 - Browser engines (checked on disk every start; only missing parts
::  are downloaded)
:: ============================================================================
:browsers
echo [Step 3 of 4] Checking the browser engines...
call :browser_mask
if "%BROWSER_MASK%"=="0" (
    echo   Browser engines are ready.
    goto :launch
)
call :repair_browsers
call :browser_mask
if "%BROWSER_MASK%"=="0" (
    echo   Browser engines are ready.
    goto :launch
)
echo.
echo   WARNING: a browser engine is still missing ^(details above^).
echo     - Free Web Novel ^(.com^) uses these engines and may fail until they are installed.
echo     - Free Web Novel ^(.net^) and WebNovel work without them.
echo   Check your internet connection and run this file again to retry.
echo.
echo   Press any key to start the program anyway, or close this window.
pause >nul

:: ============================================================================
::  STEP 4 - Start the program
:: ============================================================================
:launch
echo.
echo [Step 4 of 4] Starting %PROJECT_NAME%...
echo   ^(Keep this window open while you use the program. Close the program's own
echo    window when you are done; this window then closes after a key press.^)
echo.
if exist "%VENV_PYW%" (
    "%VENV_PYW%" "%MAIN_SCRIPT%" 2> "%LAUNCH_LOG%"
) else (
    "%VENV_PY%" "%MAIN_SCRIPT%" 2> "%LAUNCH_LOG%"
)
set "RUN_EXIT=%errorlevel%"
echo.
if "%RUN_EXIT%"=="0" (
    echo Program finished.
) else (
    echo The program stopped with an error ^(code %RUN_EXIT%^). Details:
    echo ----------------------------------------
    type "%LAUNCH_LOG%"
    echo ----------------------------------------
    echo These details are also saved in "%CD%\%LAUNCH_LOG%".
)
echo.
pause
endlocal & exit /b %RUN_EXIT%


:: ============================================================================
::  Failures (each says what to do)
:: ============================================================================
:missing_program
echo.
echo   ERROR: This folder is incomplete: "%MAIN_SCRIPT%" or "%CHECK%" is missing.
echo   If you opened this file from inside the downloaded ZIP, first extract the
echo   whole ZIP ^(right-click it, then "Extract All"^) and run this file from the
echo   extracted folder. Otherwise download the program again.
goto :fail_end

:fail_python
echo.
echo   ERROR: No usable Python 3.12 or 3.13 was found, so the program cannot run.
echo   Install Python 3.12 from https://www.python.org/downloads/release/python-31210/
echo   ^("Windows installer (64-bit)"^), then run this file again.
goto :fail_end

:fail_venv
echo.
echo   ERROR: The program's environment ^(.venv^) could not be built.
echo   If another copy of the program is open, close it first. Then delete the
echo   .venv folder in this folder and run this file again.
goto :fail_end

:fail_deps
echo.
echo   ERROR: The program's packages could not be installed ^(see the messages
echo   above^). Check your internet connection and run this file again. If it
echo   keeps failing, delete the .venv folder in this folder and try once more.
goto :fail_end

:fail_long_path
echo.
echo   ERROR: This folder's location is too long for Windows: %FOLDER_LEN% characters, and the
echo   program needs %MAX_FOLDER_LEN% or fewer ^(Windows cannot load program parts from deeper^).
echo   Move this folder somewhere shorter - for example straight into Downloads or onto the
echo   Desktop - and run this file again from there.
goto :fail_end

:fail_readonly
echo.
echo   ERROR: The program cannot save anything in this folder ^(it is read-only or
echo   protected, for example inside "Program Files"^). Move this folder to Downloads
echo   or the Desktop and run this file again from there.
goto :fail_end

:fail_end
echo.
pause
endlocal & exit /b 1


:: ============================================================================
::  Helpers
:: ============================================================================

:: measure_folder - FOLDER_LEN = the number of characters in this folder's full path.
:measure_folder
set "REST=%CD%"
set "FOLDER_LEN=0"
:measure_folder_loop
if not defined REST exit /b 0
set "REST=%REST:~1%"
set /a FOLDER_LEN+=1
goto :measure_folder_loop

:: venv_healthy - errorlevel 0 when .venv's interpreter runs, is 3.12 or 3.13, has
:: venv/ensurepip/tkinter, and has pip.
:venv_healthy
if not exist "%VENV_PY%" exit /b 1
if not exist ".venv\pyvenv.cfg" exit /b 1
"%VENV_PY%" "%CHECK%" python >nul 2>&1
if errorlevel 1 exit /b 1
"%VENV_PY%" -m pip --version >nul 2>&1
if errorlevel 1 exit /b 1
exit /b 0

:: find_python - set BASE_PY to the first working base Python 3.12 or 3.13 (3.13
:: first). Each candidate must actually run the check, which rejects Microsoft Store
:: "python" redirects, broken installs, unsupported versions and virtual environments
:: (the environment is never rebuilt from an interpreter inside one).
:find_python
set "BASE_PY="
for %%V in (3.13 3.12) do (
    if not defined BASE_PY (
        py -%%V "%CHECK%" base >nul 2>&1
        if not errorlevel 1 set "BASE_PY=py -%%V"
    )
)
if defined BASE_PY goto :found_python
:: Then the standard python.org install folders of the supported versions (also found right
:: after this window installed Python, before PATH is refreshed).
for %%D in ("%LOCALAPPDATA%\Programs\Python\Python313" "%LOCALAPPDATA%\Programs\Python\Python312" "%ProgramFiles%\Python313" "%ProgramFiles%\Python312") do (
    if not defined BASE_PY if exist "%%~D\python.exe" (
        "%%~D\python.exe" "%CHECK%" base >nul 2>&1
        if not errorlevel 1 set "BASE_PY="%%~D\python.exe""
    )
)
if defined BASE_PY goto :found_python
:: Last, whatever else is installed (accepted only if it is 3.12 or 3.13).
py -3 "%CHECK%" base >nul 2>&1
if not errorlevel 1 set "BASE_PY=py -3"
if defined BASE_PY goto :found_python
python "%CHECK%" base >nul 2>&1
if not errorlevel 1 set "BASE_PY=python"
if defined BASE_PY goto :found_python
exit /b 1
:found_python
%BASE_PY% -c "import sys; print('  Using Python', sys.version.split()[0], 'at', sys.executable)"
exit /b 0

:: offer_python_install - ask, install Python 3.12 for this user, then find it again.
:offer_python_install
echo.
echo   No usable Python 3.12 or 3.13 is installed on this PC. Python is the one
echo   tool this program needs on the computer itself.
set "DO_PY="
set /p "DO_PY=  Install Python 3.12 now, for your user only (no admin)? (Y/N): "
if /i not "%DO_PY%"=="Y" exit /b 1
where winget >nul 2>&1
if not errorlevel 1 (
    echo   Installing Python 3.12 with the Windows Package Manager...
    winget install -e --id %PYTHON_WINGET_ID% --scope user --accept-source-agreements --accept-package-agreements
    call :find_python
    if not errorlevel 1 exit /b 0
    echo   That did not give a usable Python; trying the official python.org installer.
)
:: Windows PowerShell cannot load its own modules when PSModulePath was set by PowerShell 7.
set "PSModulePath="
powershell -NoProfile -ExecutionPolicy Bypass -File "%PY_INSTALLER%"
if errorlevel 1 exit /b 1
call :find_python
exit /b %errorlevel%

:: rebuild_venv - replace a missing or broken .venv with a fresh one.
:rebuild_venv
if exist ".venv" (
    echo   Removing the old environment...
    rmdir /s /q ".venv" >nul 2>&1
    if exist ".venv" (
        echo   The old .venv is in use ^(is the program still open?^).
        exit /b 1
    )
)
echo   Creating the environment in this folder...
%BASE_PY% -m venv ".venv"
if errorlevel 1 exit /b 1
call :venv_healthy
if errorlevel 1 (
    "%VENV_PY%" -m ensurepip --upgrade >nul 2>&1
    call :venv_healthy
)
exit /b %errorlevel%

:: browser_mask - BROWSER_MASK = 0 when ready, else 1 Camoufox / 2 GeoIP data /
:: 4 Chromium (added together). Problems are printed by the check itself.
:browser_mask
"%VENV_PY%" "%CHECK%" browsers
set "BROWSER_MASK=%errorlevel%"
exit /b 0

:: repair_browsers - download only the missing parts.
:repair_browsers
set /a "NEED_CAMOUFOX=BROWSER_MASK & 1, NEED_GEOIP=BROWSER_MASK & 2, NEED_CHROMIUM=BROWSER_MASK & 4"
if not "%NEED_CAMOUFOX%"=="0" (
    echo   Downloading the Camoufox browser ^(about 150 MB, once^)...
    "%VENV_PY%" -m camoufox fetch
) else if not "%NEED_GEOIP%"=="0" (
    echo   Downloading Camoufox's GeoIP data...
    "%VENV_PY%" -c "from camoufox.locale import download_mmdb; download_mmdb()"
)
if not "%NEED_CHROMIUM%"=="0" (
    echo   Downloading Chromium for Playwright into this folder ^(about 150 MB, once^)...
    "%VENV_PY%" -m playwright install chromium
)
exit /b 0
