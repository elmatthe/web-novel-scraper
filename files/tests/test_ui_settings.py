"""v0.3.0 Checkpoint 01: the validated GUI preferences store. Offline, no Tk.

The store persists the Light/Dark choice in the ignored, repository-contained
``files/runtime-data/settings.json``. A missing, corrupt, foreign or newer file must
never stop the window: it loads safe defaults, never rewrites the file on load, and
keeps a copy of an unusable original before the first save replaces it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from webnovel_scraper.ui import settings as ui_settings
from webnovel_scraper.ui.settings import (
    DARK,
    LIGHT,
    SCHEMA_VERSION,
    Settings,
    SettingsStore,
    validate_settings,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── Containment ──────────────────────────────────────────────────────────────
def test_runtime_settings_resolve_inside_the_repository() -> None:
    assert ui_settings.REPO_ROOT == REPO_ROOT
    assert ui_settings.RUNTIME_DIR == REPO_ROOT / "files" / "runtime-data"
    real_default = ui_settings.RUNTIME_DIR / "settings.json"
    assert real_default.is_relative_to(REPO_ROOT)
    # ...and the folder is ignored by Git, so preferences are never committed.
    ignored = [
        line.strip()
        for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    ]
    assert "files/runtime-data/" in ignored


def test_the_suite_never_uses_the_real_settings_file(tmp_path) -> None:
    """conftest redirects the default path, so a window built without a store in
    any test reads and writes only that test's temp folder."""
    store = SettingsStore()
    assert store.path != ui_settings.RUNTIME_DIR / "settings.json"
    assert store.path.is_relative_to(tmp_path)


# ── Load ─────────────────────────────────────────────────────────────────────
def test_missing_file_loads_defaults_without_writing(tmp_path) -> None:
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    result = store.load()
    assert result.status == "defaults"
    assert store.settings == Settings()
    assert store.settings.appearance == LIGHT
    assert not path.exists()


@pytest.mark.parametrize(
    "text, status",
    [
        ("{not json", "unusable"),
        ("[1, 2]", "unusable"),
        ('{"appearance": "dark"}', "unusable"),  # no schema_version
        ('{"schema_version": true, "appearance": "dark"}', "unusable"),
        ('{"schema_version": 0, "appearance": "dark"}', "unusable"),
        ('{"schema_version": 99, "appearance": "dark"}', "unusable"),  # newer app
        ('{"schema_version": 1, "appearance": "purple"}', "repaired"),
        ('{"schema_version": 1, "appearance": 7}', "repaired"),
    ],
)
def test_corrupt_or_invalid_files_fall_back_to_light(tmp_path, text, status) -> None:
    path = tmp_path / "settings.json"
    path.write_text(text, encoding="utf-8")
    store = SettingsStore(path)
    result = store.load()
    assert result.status == status
    assert result.diagnostics, "a fallback must say why"
    assert store.settings.appearance == LIGHT
    assert path.read_text(encoding="utf-8") == text  # a load never rewrites


def test_undecodable_file_is_unusable_not_a_crash(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_bytes(b"\xff\xfe\x00\xd8 not utf-8")
    result = SettingsStore(path).load()
    assert result.status == "unusable"
    assert result.settings.appearance == LIGHT


def test_unknown_keys_are_ignored(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"schema_version": 1, "appearance": "dark", "later": {"x": 1}}),
        encoding="utf-8",
    )
    store = SettingsStore(path)
    assert store.load().status == "loaded"
    assert store.settings.appearance == DARK


def test_validate_settings_never_raises() -> None:
    for raw in (None, 3, "dark", [], {"schema_version": "1"}):
        assert validate_settings(raw).status == "unusable"
    assert validate_settings({"schema_version": 1}).settings == Settings()


# ── Save ─────────────────────────────────────────────────────────────────────
def test_appearance_round_trips_atomically(tmp_path) -> None:
    path = tmp_path / "nested" / "settings.json"
    store = SettingsStore(path)
    store.load()
    assert store.update(appearance=DARK).ok
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema_version": SCHEMA_VERSION,
        "appearance": "dark",
    }
    # No temp file is left beside it.
    assert sorted(p.name for p in path.parent.iterdir()) == ["settings.json"]

    again = SettingsStore(path)
    assert again.load().status == "loaded"
    assert again.settings.appearance == DARK


def test_unchanged_default_writes_nothing(tmp_path) -> None:
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.load()
    assert store.update(appearance=LIGHT).ok
    assert not path.exists()


def test_invalid_value_is_refused_and_current_choice_kept(tmp_path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    store.load()
    with pytest.raises(ValueError):
        store.update(appearance="purple")
    assert store.settings.appearance == LIGHT


def test_first_save_after_a_corrupt_load_keeps_the_original(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    store = SettingsStore(path)
    store.load()

    assert store.update(appearance=DARK).ok
    backups = list(tmp_path.glob("settings.unusable-*.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{not json"
    assert json.loads(path.read_text(encoding="utf-8"))["appearance"] == "dark"

    # Only the unusable original is preserved; a later ordinary save adds no copy.
    assert store.update(appearance=LIGHT).ok
    assert len(list(tmp_path.glob("settings.unusable-*.json"))) == 1


def test_failed_save_is_reported_and_kept_for_the_session(tmp_path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the settings folder should be", encoding="utf-8")
    store = SettingsStore(blocker / "settings.json")
    store.load()
    result = store.update(appearance=DARK)
    assert not result.ok
    assert result.message.startswith("could not")
    assert store.settings.appearance == DARK
