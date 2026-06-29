#!/bin/bash
# ============================================================================
#  Setup_and_Run-Web-Novel-Scraper  --  macOS setup + launcher
# ============================================================================
#  NOTE: macOS is a later pass for this project. This launcher mirrors the
#  Windows .bat flow and is written to be correct, but it has NOT yet been
#  runtime-tested on a Mac. The Python package is platform-neutral, so this
#  file is the only macOS-specific piece. Keep the .command extension (opens
#  on double-click from Finder) and the executable bit:
#      chmod +x Setup_and_Run-Web-Novel-Scraper.command
#
#  WHAT THIS FILE DOES (per AI-WORKSPACE.md "Setup and Launch Files")
#  ------------------------------------------------------------------
#  One double-click does everything for a NON-TECHNICAL user:
#    1. Scans the Mac for Python 3 (the one unavoidable system dependency).
#    2. If Python is missing, asks Y/N and installs it FOR THE CURRENT USER
#       only (user scope via Homebrew, no admin password).
#    3. Creates a fresh .venv IN THE REPO ROOT and installs all dependencies
#       (scripts/requirements.txt) into it (never system-wide).
#    4. Downloads the Playwright Chromium browser INTO the project
#       (files/bin/ms-playwright) for the Cloudflare/browser fetch path.
#    5. Launches the GUI (scripts/Universal/app.py).
#
#  SELF-HEALING: delete .venv and re-run -- it rebuilds from scratch.
#  Goal: the MINIMUM installed on the Mac; everything else contained in the repo.
# ============================================================================

cd "$(dirname "$0")" || exit 1

# ============================================================================
#  Configuration
# ============================================================================
PROJECT_NAME="Web-Novel-Scraper"

# Minimum Python major.minor this project supports.
PY_MIN_MAJOR="3"
PY_MIN_MINOR="10"
# Homebrew formula used if Python must be installed from scratch (user scope).
PYTHON_BREW_FORMULA="python@3.12"

# requirements.txt lives in scripts/ per AI-WORKSPACE.md.
REQUIREMENTS="scripts/requirements.txt"

# Single-window GUI entry point (no separate launcher.py for this project).
MAIN_SCRIPT="scripts/Universal/app.py"

# This project does not use ffmpeg.
USE_FFMPEG=0

# Where in-repo tools/binaries are kept. Self-contained, no install.
BIN_DIR="$(pwd)/files/bin"

# Keep the Playwright Chromium download INSIDE the project (gitignored).
export PLAYWRIGHT_BROWSERS_PATH="$(pwd)/files/bin/ms-playwright"

PYTHON_CMD=""

# ============================================================================
#  Banner + first-run security note
# ============================================================================
clear 2>/dev/null
echo "========================================"
echo "  ${PROJECT_NAME} - Setup & Launcher"
echo "  Folder: $(pwd)"
echo "========================================"
echo
echo "  This window sets up and launches the program. Setup keeps everything"
echo "  inside this project folder where it can. You should not need to install"
echo "  anything system-wide unless a required tool (Python) is completely"
echo "  missing from this Mac -- and it will ask you first if so."
echo
echo "  FIRST-RUN NOTE: Because this file came from the internet, macOS may"
echo "  block it the first time. If you see \"cannot be opened\", go to"
echo "  System Settings > Privacy & Security, scroll down, and click"
echo "  \"Open Anyway\". This is normal and only happens once."
echo

# ============================================================================
#  Helpers
# ============================================================================

# Find a usable Python 3 and store it in PYTHON_CMD. Returns 0 if found.
detect_python() {
    PYTHON_CMD=""
    if command -v python3 &> /dev/null; then
        PYTHON_CMD="python3"
        return 0
    fi
    return 1
}

# Warn (don't block) if the detected Python is below the project minimum.
check_python_version() {
    local minor
    minor="$("$PYTHON_CMD" -c 'import sys; print(sys.version_info[1])' 2>/dev/null)"
    if [ -n "$minor" ] && [ "$minor" -lt "$PY_MIN_MINOR" ] 2>/dev/null; then
        echo "  WARNING: Detected Python 3.${minor}, but this project targets"
        echo "           ${PY_MIN_MAJOR}.${PY_MIN_MINOR} or newer. Some features may not work."
        echo
    fi
}

# Ensure Homebrew (user-local, no sudo) is available. Returns 0 on success.
ensure_homebrew() {
    if command -v brew &> /dev/null; then
        return 0
    fi
    echo
    echo "  Homebrew (the macOS installer tool) is not installed."
    echo "  Installing Homebrew into your user account as part of Python setup..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    if [ -x /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [ -x /usr/local/bin/brew ]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
    command -v brew &> /dev/null && return 0
    return 1
}

# ============================================================================
#  STEP 1 - Ensure Python (the only unavoidable system dependency)
# ============================================================================
echo "Checking for Python..."
if ! detect_python; then
    echo
    echo "  Python 3 is not installed on this Mac, and it is required to run this"
    echo "  program. This is the ONLY tool that has to be installed onto the"
    echo "  computer itself - everything else stays in this folder."
    echo
    read -p "  Install Python now? (Y/N): " do_py
    if [[ "$do_py" =~ ^[Yy]$ ]]; then
        if ensure_homebrew; then
            echo "  Installing Python via Homebrew (no admin password)..."
            brew install "$PYTHON_BREW_FORMULA"
        else
            echo "  Skipped. Python is required to run this program."
            read -p "Press Enter to exit..."
            exit 1
        fi
    else
        echo "  Python is required. Install it from:"
        echo "    https://www.python.org/downloads/macos/"
        echo "  then run this file again."
        read -p "Press Enter to exit..."
        exit 1
    fi

    # Re-detect; a fresh install may not be on PATH in this same window.
    if ! detect_python; then
        echo
        echo "  Python was installed but isn't visible in THIS window yet. Close"
        echo "  this window, re-open it, and run this file again so the updated"
        echo "  PATH takes effect."
        read -p "Press Enter to exit..."
        exit 1
    fi
fi

# Warn (don't block) if the present Python is older than the project minimum.
check_python_version

# ============================================================================
#  STEP 2 - Virtual environment (self-healing; everything below stays in repo)
# ============================================================================
if [ ! -f ".venv/bin/activate" ]; then
    if [ -d ".venv" ]; then
        echo "Existing .venv looks incomplete - rebuilding it from scratch..."
        rm -rf ".venv"
    else
        echo "Creating a new virtual environment in this folder..."
    fi
    if ! "$PYTHON_CMD" -m venv .venv; then
        echo "  ERROR: Failed to create the virtual environment."
        read -p "Press Enter to exit..."
        exit 1
    fi
fi

# shellcheck disable=SC1091
source .venv/bin/activate
if [ ! -f ".venv/bin/activate" ]; then
    echo "  ERROR: Virtual environment activation script missing after setup."
    read -p "Press Enter to exit..."
    exit 1
fi

# ============================================================================
#  STEP 3 - Dependencies (into the venv - never system-wide, installed quietly)
# ============================================================================
if [ -f "$REQUIREMENTS" ]; then
    echo "Installing dependencies into the project environment..."
    pip install --upgrade pip >/dev/null 2>&1
    if ! pip install -r "$REQUIREMENTS"; then
        echo "  ERROR: Some dependencies failed to install. See messages above."
        read -p "Press Enter to exit..."
        exit 1
    fi
else
    echo "  Note: No requirements.txt at $REQUIREMENTS - skipping dependencies."
fi

# ============================================================================
#  STEP 3b - camoufox browser engine (the primary Cloudflare bypass rung)
# ============================================================================
# FreeWebNovel sits behind Cloudflare. The primary browser rung is camoufox (an
# anti-detect Firefox); its binary is downloaded with "python -m camoufox fetch"
# into the per-user camoufox cache (~once, no admin). A sentinel lets later runs
# skip it.
if [ ! -f ".venv/camoufox.fetched" ]; then
    echo "Downloading the camoufox browser engine (~once)..."
    if python -m camoufox fetch; then
        echo "done" > ".venv/camoufox.fetched"
    else
        echo "  WARNING: The camoufox download did not complete. The plain HTTP"
        echo "           path still works, but the Cloudflare/browser bypass"
        echo "           (needed for FreeWebNovel) will not until this succeeds."
        echo "           Re-run this file to retry."
        echo
    fi
fi

# ============================================================================
#  STEP 3c - Playwright Chromium (the playwright-stealth last-resort rungs)
# ============================================================================
# The last-resort rungs (playwright_stealth / playwright_stealth_fresh) drive a
# Chromium through Playwright. Installed CHROMIUM ONLY (never full Chrome) and
# contained INSIDE the project (PLAYWRIGHT_BROWSERS_PATH above), ~once.
if [ ! -d "$PLAYWRIGHT_BROWSERS_PATH" ]; then
    echo "Downloading the Playwright Chromium browser into the project (~once)..."
    if ! python -m playwright install chromium; then
        echo "  WARNING: Chromium download did not complete. camoufox (the primary"
        echo "           bypass) still works; only the last-resort playwright-stealth"
        echo "           rungs are unavailable until this succeeds. Re-run to retry."
        echo
    fi
fi

# ============================================================================
#  STEP 4 - ffmpeg (unused by this project)
# ============================================================================
if [ -d "$BIN_DIR" ]; then
    export PATH="$BIN_DIR:$PATH"
fi

# ============================================================================
#  STEP 5 - Launch the GUI
# ============================================================================
if [ ! -f "$MAIN_SCRIPT" ]; then
    echo
    echo "  ERROR: Could not find the program's entry point:"
    echo "    $MAIN_SCRIPT"
    echo "  The repo may be incomplete - re-download it and try again."
    echo
    read -p "Press Enter to exit..."
    exit 1
fi

echo
echo "Launching ${PROJECT_NAME}..."
echo
python "$MAIN_SCRIPT"
RUN_EXIT=$?

echo
if [ "$RUN_EXIT" -ne 0 ]; then
    echo "Program exited with code ${RUN_EXIT}."
else
    echo "Program finished."
fi
read -p "Press Enter to close..."
exit $RUN_EXIT
