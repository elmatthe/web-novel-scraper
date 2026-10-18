"""Versioned, validated GUI preferences in the repository's ignored runtime folder.

Adapted from PDF-Splitter ``scripts/pdf_splitter/settings.py`` (the author's local
working copy supplied 2026-10-17, newer than GitHub ``edcbd4f``), which in turn took
its mechanics from the Audiobook Creation Tool v0.6.6: a forgiving load that never
raises, atomic writes (temp file in the same folder, then ``os.replace``), a path seam
for tests, a schema version, per-field validation, and a timestamped copy of an
unusable original before it is overwritten.

Trimmed for the scraper. Checkpoint 01 persisted only the appearance (schema 1); Checkpoint
02.3 adds the stable, ordinary choices (schema 2): website and novel, output grouping and
chunk size, delay and timeout, and the browser, headless, HTTP-first and cache switches. A
schema-1 file loads unchanged (its missing fields take the fresh-install defaults) and is
rewritten as schema 2 by the next save. Never stored: credentials, cookies, session data,
novel text, chapter ranges, output paths, progress or run results.

The website and novel are stored as plain identifiers; this module does not know the catalog.
The window checks them against the catalog and falls back to a supported pair (see
:mod:`webnovel_scraper.ui.selection`), so a removed or disabled source never breaks start-up.

Settings live in ``files/runtime-data/settings.json`` (ignored by Git, inside the
repository); the per-session technical logs live beside them in ``files/runtime-data/logs/``
(``LOGS_DIR``, written by :mod:`webnovel_scraper.ui.activity`). This module imports no Tk
and performs no I/O at import time.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

__all__ = [
    "REPO_ROOT", "RUNTIME_DIR", "SETTINGS_PATH", "LOGS_DIR", "SCHEMA_VERSION",
    "LIGHT", "DARK", "APPEARANCES", "DEFAULT_APPEARANCE",
    "OUTPUT_MODES", "DEFAULT_OUTPUT_MODE", "DEFAULT_CHUNK_SIZE", "CHUNK_SIZE_RANGE",
    "DEFAULT_DELAY", "DELAY_RANGE", "DEFAULT_TIMEOUT", "TIMEOUT_RANGE",
    "Settings", "LoadResult", "SaveResult", "SettingsStore", "validate_settings",
]

# This file is scripts/webnovel_scraper/ui/settings.py, so parents[3] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_DIR = REPO_ROOT / "files" / "runtime-data"
SETTINGS_PATH = RUNTIME_DIR / "settings.json"
LOGS_DIR = RUNTIME_DIR / "logs"

SCHEMA_VERSION = 2

LIGHT = "light"
DARK = "dark"
APPEARANCES = (LIGHT, DARK)
DEFAULT_APPEARANCE = LIGHT

# Fresh-install values for the ordinary preferences. They mirror the window's own defaults
# (``app.DEFAULT_*``; a test pins that they agree). Headless-first is the default since
# Checkpoint 04 (a saved choice is kept).
OUTPUT_MODES = ("separate", "chunked", "single")   # the OutputMode values, as strings
DEFAULT_OUTPUT_MODE = "separate"                   # Individual chapters
DEFAULT_CHUNK_SIZE = 10
CHUNK_SIZE_RANGE = (1, 10_000)
DEFAULT_DELAY = 3.0
DELAY_RANGE = (0.0, 600.0)
DEFAULT_TIMEOUT = 30
TIMEOUT_RANGE = (1, 600)
_ID_LIMIT = 80   # longest website / novel identifier accepted


@dataclass(frozen=True)
class Settings:
    """Every persisted preference. The dataclass defaults are the fresh-install values."""

    appearance: str = DEFAULT_APPEARANCE
    website: str = ""            # website identifier; "" = first supported website
    novel: str = ""              # novel slug; "" = first novel that website offers
    output_mode: str = DEFAULT_OUTPUT_MODE
    chunk_size: int = DEFAULT_CHUNK_SIZE
    delay: float = DEFAULT_DELAY
    timeout: int = DEFAULT_TIMEOUT
    browser_mode: bool = True
    headless: bool = True
    http_first: bool = False
    use_cache: bool = True

    def to_json(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "appearance": self.appearance,
            "website": self.website,
            "novel": self.novel,
            "output_mode": self.output_mode,
            "chunk_size": self.chunk_size,
            "delay": self.delay,
            "timeout": self.timeout,
            "browser_mode": self.browser_mode,
            "headless": self.headless,
            "http_first": self.http_first,
            "use_cache": self.use_cache,
        }

    def checked(self) -> "Settings":
        """Return ``self`` only if it survives full validation unchanged."""
        result = validate_settings(self.to_json())
        if result.status != "loaded" or result.settings != self:
            raise ValueError("; ".join(result.diagnostics) or "invalid settings")
        return self


@dataclass(frozen=True)
class LoadResult:
    """What a load produced. ``status`` is ``defaults`` (no file), ``loaded``,
    ``repaired`` (some fields replaced by defaults) or ``unusable`` (whole file ignored)."""

    settings: Settings
    status: str
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class SaveResult:
    ok: bool
    message: str = ""


def validate_settings(raw: object) -> LoadResult:
    """Validate decoded JSON. Never raises; bad data falls back to safe defaults.
    Unknown keys are ignored so a later checkpoint can add fields to the same schema."""
    if not isinstance(raw, dict):
        return LoadResult(
            Settings(), "unusable",
            (f"settings file must contain a JSON object, not {type(raw).__name__}",))
    version = raw.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        return LoadResult(Settings(), "unusable", ("settings file has no valid schema_version",))
    if version > SCHEMA_VERSION:
        return LoadResult(
            Settings(), "unusable",
            (f"settings file is from a newer version (schema {version}); using defaults",))
    diagnostics: list[str] = []
    defaults = Settings()

    def field(name: str, check) -> object:
        """The stored value if *check* accepts it, else the default (with a diagnostic).
        A missing key is not a problem: older files simply lack newer fields."""
        default = getattr(defaults, name)
        if name not in raw:
            return default
        value = raw[name]
        if check(value):
            return value
        diagnostics.append(f"ignored invalid {name} {_brief(value)}; using {default!r}")
        return default

    settings = Settings(
        appearance=field("appearance", lambda v: isinstance(v, str) and v in APPEARANCES),
        website=field("website", _is_identifier),
        novel=field("novel", _is_identifier),
        output_mode=field("output_mode", lambda v: isinstance(v, str) and v in OUTPUT_MODES),
        chunk_size=field("chunk_size", lambda v: _is_int_in(v, CHUNK_SIZE_RANGE)),
        delay=float(field("delay", _is_delay)),
        timeout=field("timeout", lambda v: _is_int_in(v, TIMEOUT_RANGE)),
        browser_mode=field("browser_mode", lambda v: isinstance(v, bool)),
        headless=field("headless", lambda v: isinstance(v, bool)),
        http_first=field("http_first", lambda v: isinstance(v, bool)),
        use_cache=field("use_cache", lambda v: isinstance(v, bool)),
    )
    return LoadResult(settings, "repaired" if diagnostics else "loaded", tuple(diagnostics))


def _brief(value: object) -> str:
    text = repr(value)
    return text if len(text) <= 40 else text[:37] + "..."


def _is_identifier(value: object) -> bool:
    """A website or novel identifier: empty (unset) or a short lowercase token. Anything else
    (paths, URLs, spaces, control characters) is not something this app ever stores."""
    if not isinstance(value, str):
        return False
    return value == "" or (
        len(value) <= _ID_LIMIT
        and value[0].isalnum() and value == value.lower()
        and all(ch.isalnum() or ch in "-._" for ch in value)
        and value.isascii()
    )


def _is_int_in(value: object, bounds: tuple[int, int]) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)
            and bounds[0] <= value <= bounds[1])


def _is_delay(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value == value and DELAY_RANGE[0] <= value <= DELAY_RANGE[1]


class SettingsStore:
    """Owns the current :class:`Settings` and their file.

    A load never rewrites the file. If the file was unusable or partly invalid, the
    first later save keeps a timestamped copy of the original beside it.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        # Read at call time (not bound as a default argument) so tests can redirect it.
        self.path = Path(path) if path is not None else SETTINGS_PATH
        self.settings = Settings()
        self.last_load = LoadResult(self.settings, "defaults")
        self._preserve_original = False
        self._save_pending = False

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return self.last_load.diagnostics

    def load(self) -> LoadResult:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            result = LoadResult(Settings(), "defaults")
        except (OSError, UnicodeDecodeError) as exc:
            self._preserve_original = True
            result = LoadResult(
                Settings(), "unusable", (f"settings file could not be read ({type(exc).__name__})",))
        else:
            try:
                result = validate_settings(json.loads(text))
            except ValueError:
                result = LoadResult(Settings(), "unusable", ("settings file is not valid JSON",))
            if result.status in ("unusable", "repaired"):
                self._preserve_original = True
        self.settings = result.settings
        self.last_load = result
        self._save_pending = False
        return result

    def apply(self, new: Settings) -> SaveResult:
        """Make *new* current and save it. The change stays in effect for this session even
        if saving fails; the failure is reported, never hidden. An unchanged unsaved
        choice retries the save; unchanged defaults or saved preferences write nothing."""
        new.checked()
        if new == self.settings and not self._save_pending:
            return SaveResult(True)
        self.settings = new
        return self.save()

    def update(self, **changes: object) -> SaveResult:
        """Replace fields (for example ``appearance``) and save."""
        return self.apply(replace(self.settings, **changes))

    def save(self) -> SaveResult:
        self._save_pending = True
        directory = self.path.parent
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return SaveResult(False, f"could not create {directory} ({type(exc).__name__})")
        if self._preserve_original and self.path.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = self.path.with_name(f"{self.path.stem}.unusable-{stamp}{self.path.suffix}")
            try:
                shutil.copy2(self.path, backup)
            except OSError as exc:
                # Keep both the original and the preservation requirement. The new
                # preference still applies in memory and the next change retries.
                return SaveResult(False, f"could not preserve original settings ({type(exc).__name__})")
            self._preserve_original = False
        try:
            fd, tmp_name = tempfile.mkstemp(prefix=".settings_", suffix=".tmp", dir=directory)
        except OSError as exc:
            return SaveResult(False, f"could not write settings ({type(exc).__name__})")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(self.settings.to_json(), handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        except OSError as exc:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            return SaveResult(False, f"could not write settings ({type(exc).__name__})")
        self._save_pending = False
        return SaveResult(True)
