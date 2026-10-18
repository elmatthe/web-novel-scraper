"""v0.3.0 Checkpoint 02.3: the ordinary preferences in the settings store (schema 2).

Pure and offline (no Tk). ``test_ui_settings.py`` keeps pinning the Checkpoint 01 behaviour
(appearance, atomic saves, corrupt files, failed saves); this file covers what 02.3 adds:
website and novel, grouping, chunk size, delay, timeout and the browser, headless, HTTP-first
and cache switches, and the safe upgrade of an existing schema-1 file.
"""

from __future__ import annotations

import json

import pytest

import app
from webnovel_scraper.ui import settings as ui_settings
from webnovel_scraper.ui.settings import (
    DARK,
    SCHEMA_VERSION,
    Settings,
    SettingsStore,
    validate_settings,
)

ALL_FIELDS = {
    "schema_version": 2, "appearance": "dark", "website": "dynamic.webnovel.com",
    "novel": "the-noble-queen", "output_mode": "chunked", "chunk_size": 25, "delay": 4.5,
    "timeout": 45, "browser_mode": False, "headless": True, "http_first": True,
    "use_cache": False,
}


def test_fresh_install_defaults_are_the_documented_startup_values() -> None:
    d = Settings()
    assert (d.appearance, d.website, d.novel) == ("light", "", "")
    assert (d.output_mode, d.chunk_size, d.delay, d.timeout) == ("separate", 10, 3.0, 30)
    # Headless stays OFF until the headless-first work (Checkpoint 04); PDF/TXT are not stored.
    assert (d.browser_mode, d.headless, d.http_first, d.use_cache) == (True, False, False, True)
    assert SCHEMA_VERSION == 2


def test_the_window_defaults_and_the_settings_defaults_agree() -> None:
    d = Settings()
    assert d.delay == float(app.DEFAULT_DELAY)
    assert d.timeout == int(app.DEFAULT_TIMEOUT)
    assert d.chunk_size == int(app.DEFAULT_CHUNK)
    assert d.headless is app.DEFAULT_HEADLESS and app.DEFAULT_HEADLESS is False
    assert d.browser_mode is app.DEFAULT_BROWSER_MODE
    assert d.http_first is app.DEFAULT_HTTP_FIRST
    assert d.output_mode == app.OutputMode.SEPARATE.value
    assert set(ui_settings.OUTPUT_MODES) == {m.value for m in app.OutputMode}


def test_a_schema_1_file_upgrades_safely(tmp_path) -> None:
    path = tmp_path / "settings.json"
    original = '{"schema_version": 1, "appearance": "dark"}'
    path.write_text(original, encoding="utf-8")
    store = SettingsStore(path)
    result = store.load()
    assert result.status == "loaded" and result.diagnostics == ()
    assert store.settings == Settings(appearance=DARK)  # new fields take the defaults
    assert path.read_text(encoding="utf-8") == original  # a load never rewrites the file

    # The next save writes schema 2, keeping the appearance and the other defaults.
    assert store.update(website="freewebnovel.com", novel="supreme-magus").ok
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 2 and saved["appearance"] == "dark"
    assert saved["website"] == "freewebnovel.com" and saved["novel"] == "supreme-magus"
    # An ordinary upgrade is not an "unusable original": no backup copy is made.
    assert list(tmp_path.glob("settings.unusable-*.json")) == []


def test_every_preference_round_trips(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(ALL_FIELDS), encoding="utf-8")
    store = SettingsStore(path)
    assert store.load().status == "loaded"
    s = store.settings
    assert (s.appearance, s.website, s.novel) == ("dark", "dynamic.webnovel.com", "the-noble-queen")
    assert (s.output_mode, s.chunk_size, s.delay, s.timeout) == ("chunked", 25, 4.5, 45)
    assert (s.browser_mode, s.headless, s.http_first, s.use_cache) == (False, True, True, False)
    assert s.to_json() == ALL_FIELDS
    assert s.checked() == s


@pytest.mark.parametrize(
    "field, bad",
    [
        ("output_mode", "everything"), ("output_mode", 3), ("output_mode", None),
        ("chunk_size", 0), ("chunk_size", 10_001), ("chunk_size", "10"), ("chunk_size", 2.5),
        ("chunk_size", True), ("chunk_size", None),
        ("delay", -1), ("delay", 601), ("delay", "3"), ("delay", True), ("delay", None),
        ("delay", float("nan")), ("delay", float("inf")),
        ("timeout", 0), ("timeout", 601), ("timeout", "30"), ("timeout", 1.5), ("timeout", False),
        ("browser_mode", 1), ("browser_mode", "yes"), ("headless", 0), ("headless", None),
        ("http_first", "false"), ("use_cache", 1),
        ("website", 7), ("website", "Free Web Novel"), ("website", "https://x.example/p"),
        ("website", "a" * 81), ("website", "../etc"), ("novel", ["shadow-slave"]),
        ("novel", "Shadow Slave"), ("novel", "line\nbreak"), ("novel", "\u00fcn\u00ef"),
    ],
)
def test_an_invalid_field_is_replaced_alone_with_its_default(field, bad) -> None:
    raw = dict(ALL_FIELDS, **{field: bad})
    result = validate_settings(raw)
    assert result.status == "repaired"
    assert len(result.diagnostics) == 1 and field in result.diagnostics[0]
    assert getattr(result.settings, field) == getattr(Settings(), field)
    # Every other field keeps its stored value.
    for name, value in ALL_FIELDS.items():
        if name not in ("schema_version", field):
            assert getattr(result.settings, name) == value


def test_boundary_values_are_accepted_and_integer_delays_become_floats() -> None:
    low = validate_settings({"schema_version": 2, "chunk_size": 1, "delay": 0, "timeout": 1})
    assert low.status == "loaded"
    assert (low.settings.chunk_size, low.settings.delay, low.settings.timeout) == (1, 0.0, 1)
    assert isinstance(low.settings.delay, float)
    high = validate_settings(
        {"schema_version": 2, "chunk_size": 10_000, "delay": 600, "timeout": 600})
    assert high.status == "loaded"
    assert (high.settings.chunk_size, high.settings.delay, high.settings.timeout) == (
        10_000, 600.0, 600)


def test_a_newer_schema_is_ignored_wholesale() -> None:
    result = validate_settings(dict(ALL_FIELDS, schema_version=3))
    assert result.status == "unusable" and result.settings == Settings()


def test_a_partly_corrupt_file_keeps_the_good_fields_and_the_original(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(dict(ALL_FIELDS, delay="soon", novel=5)), encoding="utf-8")
    store = SettingsStore(path)
    assert store.load().status == "repaired"
    assert store.settings.timeout == 45 and store.settings.delay == 3.0
    assert store.update(timeout=50).ok
    assert len(list(tmp_path.glob("settings.unusable-*.json"))) == 1


def test_validation_never_raises_on_hostile_json() -> None:
    hostile = [
        {"schema_version": 2, "delay": [1], "timeout": {}, "chunk_size": [], "website": {}},
        {"schema_version": 2, "appearance": ["dark"], "output_mode": ["separate"]},
        {"schema_version": 2, "headless": [], "use_cache": {}, "novel": 1e400},
    ]
    for raw in hostile:
        assert validate_settings(raw).status in ("loaded", "repaired")


def test_only_stable_preferences_can_be_stored() -> None:
    """Nothing transient or sensitive has a field: no credentials, cookies, session data,
    novel text, chapter range, output location, progress or run result."""
    assert set(Settings().to_json()) == {
        "schema_version", "appearance", "website", "novel", "output_mode", "chunk_size",
        "delay", "timeout", "browser_mode", "headless", "http_first", "use_cache"}


def test_updating_one_preference_keeps_the_others(tmp_path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    store.load()
    assert store.update(output_mode="single", chunk_size=7, headless=True).ok
    assert store.update(appearance=DARK).ok  # a theme change resets nothing else
    again = SettingsStore(tmp_path / "settings.json")
    again.load()
    assert (again.settings.output_mode, again.settings.chunk_size, again.settings.headless) == (
        "single", 7, True)
    assert again.settings.appearance == DARK


def test_an_out_of_range_update_is_refused_and_nothing_changes(tmp_path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    store.load()
    for change in ({"chunk_size": 0}, {"delay": -2.0}, {"timeout": 9999}, {"output_mode": "x"},
                   {"website": "A B"}):
        with pytest.raises(ValueError):
            store.update(**change)
    assert store.settings == Settings()
    assert not (tmp_path / "settings.json").exists()


def test_the_settings_file_holds_no_path_or_secret_even_after_a_full_save(tmp_path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    store.load()
    assert store.update(website="freewebnovel.com", novel="shadow-slave", output_mode="single",
                        chunk_size=3, delay=1.5, timeout=20, headless=True).ok
    text = (tmp_path / "settings.json").read_text(encoding="utf-8")
    for forbidden in ("Downloads", "output_dir", "output_parent", "cookie", "password", "token", "C:\\", "/home/"):
        assert forbidden.lower() not in text.lower()
