"""v0.3.0 Checkpoint 02.3: ordinary preferences in the window — restored at start-up, saved at
discrete moments, safe when the file is bad or cannot be written.

Offline and deterministic. Every test builds a real window against a temporary settings file
(the suite's conftest also redirects the default path) and skips when no Tk display exists.
"""

from __future__ import annotations

import gc
import json

import pytest

import app
from webnovel_scraper import pipeline
from webnovel_scraper.ui.settings import Settings, SettingsStore


# ── Helpers ──────────────────────────────────────────────────────────────────
def _make_app(store: SettingsStore | None = None, **kwargs):
    tk = pytest.importorskip("tkinter")
    gc.collect()
    try:
        return app.ScraperApp(store, **kwargs)
    except tk.TclError as exc:
        pytest.skip(f"no Tk display available: {exc}")


def _path(tmp_path):
    return tmp_path / "runtime-data" / "settings.json"


def _store(tmp_path) -> SettingsStore:
    store = SettingsStore(_path(tmp_path))
    store.load()
    return store


def _write(tmp_path, data) -> None:
    path = _path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def _saved(tmp_path) -> dict:
    return json.loads(_path(tmp_path).read_text(encoding="utf-8"))


def _pick_website(win, key: str) -> None:
    win._website_var.set(key)
    win._on_website_change()


def _pick_novel(win, title: str) -> None:
    win._novel_combo.set(title)
    win._on_novel_change()


def _count_saves(win, monkeypatch) -> list:
    calls: list = []
    real = win._store.save

    def spy():
        calls.append(1)
        return real()

    monkeypatch.setattr(win._store, "save", spy)
    return calls


def _fake_scrape(monkeypatch):
    jobs = []

    def fake_run_scrape(job, *, log, cancel_event, progress_cb, phase_cb=None):
        jobs.append(job)
        return pipeline.RunReport(output_dir=job.output_dir)

    monkeypatch.setattr(app.pipeline, "run_scrape", fake_run_scrape)
    return jobs


FULL = {
    "schema_version": 2, "appearance": "dark", "website": "freewebnovel.com",
    "novel": "supreme-magus", "output_mode": "chunked", "chunk_size": 25, "delay": 4.5,
    "timeout": 45, "browser_mode": False, "headless": True, "http_first": True,
    "use_cache": False,
}


# ── Fresh install ────────────────────────────────────────────────────────────
def test_a_fresh_install_starts_with_the_documented_defaults_and_writes_nothing(tmp_path) -> None:
    win = _make_app(_store(tmp_path))
    try:
        assert win._bundle["appearance"] == "light"
        assert (win._website_var.get(), win._current_slug()) == ("freewebnovel.com", "shadow-slave")
        assert win._pdf_var.get() is True and win._txt_var.get() is False
        assert win._mode_var.get() == "separate"  # Individual chapters
        assert win._chunk_var.get() == app.DEFAULT_CHUNK
        assert (win._delay_var.get(), win._timeout_var.get()) == ("3.0", "30")
        assert win._browser_var.get() is True
        assert win._headless_var.get() is False and app.DEFAULT_HEADLESS is False
        assert win._http_first_var.get() is False and win._cache_var.get() is True
        assert str(win._chunk_entry.cget("state")) == "disabled"
        assert win._start_var.get() == "1" and win._end_var.get() == ""
    finally:
        win.destroy()
    assert not _path(tmp_path).exists()  # looking at the window saved nothing


# ── Restoring each group ─────────────────────────────────────────────────────
def test_every_saved_preference_is_restored(tmp_path) -> None:
    _write(tmp_path, FULL)
    win = _make_app(_store(tmp_path))
    try:
        assert win._bundle["appearance"] == "dark"
        assert (win._website_var.get(), win._current_slug()) == (
            "freewebnovel.com", "supreme-magus")
        assert win._novel_combo.get() == "Supreme Magus"
        assert win._mode_var.get() == "chunked" and win._chunk_var.get() == "25"
        assert str(win._chunk_entry.cget("state")) == "normal"
        assert (win._delay_var.get(), win._timeout_var.get()) == ("4.5", "45")
        assert (win._browser_var.get(), win._headless_var.get(),
                win._http_first_var.get(), win._cache_var.get()) == (False, True, True, False)
        assert win._current_spec().novel_slug == "supreme-magus"
    finally:
        win.destroy()


def test_website_and_novel_are_saved_when_chosen_and_restored(tmp_path) -> None:
    win = _make_app(_store(tmp_path))
    try:
        _pick_website(win, "freewebnovel.com")
        _pick_novel(win, "Renegade Immortal")
    finally:
        win.destroy()
    assert (_saved(tmp_path)["website"], _saved(tmp_path)["novel"]) == (
        "freewebnovel.com", "renegade-immortal")
    again = _make_app(_store(tmp_path))
    try:
        assert (again._website_var.get(), again._current_slug()) == (
            "freewebnovel.com", "renegade-immortal")
    finally:
        again.destroy()

    other = _make_app(_store(tmp_path))
    try:
        _pick_website(other, "dynamic.webnovel.com")
    finally:
        other.destroy()
    third = _make_app(_store(tmp_path))
    try:
        assert (third._website_var.get(), third._current_slug()) == (
            "dynamic.webnovel.com", "the-noble-queen")
        assert str(third._browser_check.cget("state")) == "disabled"
    finally:
        third.destroy()


def test_grouping_and_chunk_size_are_restored(tmp_path) -> None:
    win = _make_app(_store(tmp_path))
    try:
        win._mode_var.set("chunked")
        win._chunk_var.set("40")
        win._on_grouping_picked()
    finally:
        win.destroy()
    assert (_saved(tmp_path)["output_mode"], _saved(tmp_path)["chunk_size"]) == ("chunked", 40)
    again = _make_app(_store(tmp_path))
    try:
        assert again._mode_var.get() == "chunked" and again._chunk_var.get() == "40"
        assert str(again._chunk_entry.cget("state")) == "normal"
        again._mode_var.set("single")
        again._on_grouping_picked()
    finally:
        again.destroy()
    final = _make_app(_store(tmp_path))
    try:
        assert final._mode_var.get() == "single"
        assert str(final._chunk_entry.cget("state")) == "disabled"
        assert final._chunk_var.get() == "40"  # the chunk size is kept for later
    finally:
        final.destroy()


def test_advanced_settings_are_saved_on_close_and_restored(tmp_path) -> None:
    win = _make_app(_store(tmp_path))
    try:
        win.open_advanced_settings()
        win._delay_var.set("7.5")
        win._timeout_var.set("90")
        win._browser_var.set(False)
        win._headless_var.set(True)
        win._http_first_var.set(True)
        win._cache_var.set(False)
        win.close_advanced_settings()
        saved = _saved(tmp_path)
        assert (saved["delay"], saved["timeout"]) == (7.5, 90)
        assert (saved["browser_mode"], saved["headless"], saved["http_first"],
                saved["use_cache"]) == (False, True, True, False)
    finally:
        win.destroy()
    again = _make_app(_store(tmp_path))
    try:
        assert (again._delay_var.get(), again._timeout_var.get()) == ("7.5", "90")
        assert (again._browser_var.get(), again._headless_var.get(),
                again._http_first_var.get(), again._cache_var.get()) == (False, True, True, False)
    finally:
        again.destroy()


def test_the_current_headless_choice_survives_but_the_default_stays_off(tmp_path) -> None:
    fresh = _make_app(_store(tmp_path))
    try:
        assert fresh._headless_var.get() is False
        fresh._headless_var.set(True)
        fresh.close_advanced_settings()
    finally:
        fresh.destroy()
    again = _make_app(_store(tmp_path))
    try:
        assert again._headless_var.get() is True
    finally:
        again.destroy()
    assert Settings().headless is False  # a missing value still means "off"


# ── Upgrade and invalid data ─────────────────────────────────────────────────
def test_a_schema_1_appearance_file_opens_unchanged_and_upgrades_on_the_next_save(tmp_path) -> None:
    original = '{"schema_version": 1, "appearance": "dark"}'
    _write(tmp_path, original)
    store = _store(tmp_path)
    assert store.last_load.status == "loaded" and store.diagnostics == ()
    win = _make_app(store)
    try:
        assert win._bundle["appearance"] == "dark"
        assert (win._website_var.get(), win._current_slug()) == ("freewebnovel.com", "shadow-slave")
        assert win._status_var.get() == "Ready."  # no "settings problem" for an old file
        assert _path(tmp_path).read_text(encoding="utf-8") == original  # opening wrote nothing
        _pick_novel(win, "Reverend Insanity")
        saved = _saved(tmp_path)
        assert saved["schema_version"] == 2 and saved["appearance"] == "dark"
        assert saved["novel"] == "reverend-insanity"
    finally:
        win.destroy()
    assert list(_path(tmp_path).parent.glob("settings.unusable-*.json")) == []


@pytest.mark.parametrize(
    "website, novel, expected",
    [
        ("empirenovel.com", "shadow-slave", ("freewebnovel.com", "shadow-slave")),  # disabled
        ("gone.example", "supreme-magus", ("freewebnovel.com", "supreme-magus")),     # unknown
        ("freewebnovel.com", "removed-novel", ("freewebnovel.com", "shadow-slave")),
        ("freewebnovel.com", "the-noble-queen", ("freewebnovel.com", "shadow-slave")),
        ("dynamic.webnovel.com", "shadow-slave", ("dynamic.webnovel.com", "the-noble-queen")),
        ("", "reverend-insanity", ("freewebnovel.com", "reverend-insanity")),
    ],
)
def test_an_unavailable_saved_selection_falls_back_to_a_supported_one(
        tmp_path, website, novel, expected) -> None:
    _write(tmp_path, dict(FULL, website=website, novel=novel))
    win = _make_app(_store(tmp_path))
    try:
        assert (win._website_var.get(), win._current_slug()) == expected
        assert win._current_spec() is not None and win._current_spec().enabled
        assert str(win._start_btn.cget("state")) == "normal"
    finally:
        win.destroy()


def test_a_corrupt_file_opens_with_defaults_and_keeps_a_copy(tmp_path) -> None:
    _write(tmp_path, "{this is not json")
    win = _make_app(_store(tmp_path))
    try:
        assert "Settings problem" in win._status_var.get()
        assert (win._website_var.get(), win._current_slug()) == ("freewebnovel.com", "shadow-slave")
        assert win._mode_var.get() == "separate" and win._delay_var.get() == "3.0"
        _pick_novel(win, "Supreme Magus")  # the next save keeps the unusable original
    finally:
        win.destroy()
    backups = list(_path(tmp_path).parent.glob("settings.unusable-*.json"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == "{this is not json"
    assert _saved(tmp_path)["novel"] == "supreme-magus"


def test_a_partly_invalid_file_keeps_its_good_values(tmp_path) -> None:
    _write(tmp_path, dict(FULL, delay="soon", chunk_size=0, output_mode="all", timeout=50))
    win = _make_app(_store(tmp_path))
    try:
        assert "Settings problem" in win._status_var.get()
        assert win._delay_var.get() == "3.0" and win._chunk_var.get() == "10"
        assert win._mode_var.get() == "separate"
        assert win._timeout_var.get() == "50" and win._headless_var.get() is True
        assert win._current_slug() == "supreme-magus"
    finally:
        win.destroy()


def test_a_newer_schema_is_ignored_and_the_window_still_works(tmp_path) -> None:
    _write(tmp_path, dict(FULL, schema_version=9))
    win = _make_app(_store(tmp_path))
    try:
        assert "Settings problem" in win._status_var.get()
        assert win._bundle["appearance"] == "light" and win._headless_var.get() is False
        assert win._current_spec() is not None
    finally:
        win.destroy()


# ── When it saves, and when it does not ──────────────────────────────────────
def test_typing_never_saves_only_discrete_moments_do(tmp_path, monkeypatch) -> None:
    win = _make_app(_store(tmp_path))
    try:
        saves = _count_saves(win, monkeypatch)
        for text in ("1", "12", "123", "1234"):  # keystrokes in the number fields
            win._chunk_var.set(text)
            win._delay_var.set(text)
            win._timeout_var.set(text)
            win._start_var.set(text)
            win._end_var.set(text)
            win.update()
        win.open_advanced_settings()  # opening and focusing it saves nothing either
        win.update()
        assert saves == [] and not _path(tmp_path).exists()

        win.close_advanced_settings()  # now the values are settled
        assert len(saves) == 1
        win.close_advanced_settings()  # nothing changed since: no second write
        assert len(saves) == 1
        saved = _saved(tmp_path)  # 1234 is a valid chunk size but too big a delay or timeout
        assert (saved["chunk_size"], saved["delay"], saved["timeout"]) == (1234, 3.0, 30)
    finally:
        win.destroy()


def test_nothing_transient_is_persisted(tmp_path, monkeypatch) -> None:
    jobs = _fake_scrape(monkeypatch)
    win = _make_app(_store(tmp_path))
    try:
        win._start_var.set("5")
        win._end_var.set("9")
        win._delay_var.set("2.5")  # one real preference change, so a file is written
        out = tmp_path / "chosen-output-parent"
        out.mkdir()
        win._output_parent = out
        win._on_start()
        win._worker.join(5)
        win.flush_activity()
        assert len(jobs) == 1
    finally:
        win.destroy()
    text = _path(tmp_path).read_text(encoding="utf-8")
    saved = json.loads(text)
    assert set(saved) == set(Settings().to_json())
    for transient in ("chosen-output-parent", str(tmp_path), "Downloads",
                      "Complete", "Finished", "ETA", "PDF"):
        assert transient not in text
    again = _make_app(_store(tmp_path))
    try:
        assert again._output_parent == app.DEFAULT_OUTPUT_PARENT  # never made permanent
        assert (again._start_var.get(), again._end_var.get()) == ("1", "")
        assert again._run_status_var.get() == "Idle"
    finally:
        again.destroy()


def test_start_saves_the_validated_values_and_passes_them_on(tmp_path, monkeypatch) -> None:
    jobs = _fake_scrape(monkeypatch)
    win = _make_app(_store(tmp_path))
    try:
        win._delay_var.set("1.5")
        win._timeout_var.set("20")
        win._mode_var.set("chunked")
        win._chunk_var.set("12")
        win._output_parent = tmp_path
        win._on_start()
        win._worker.join(5)
        win.flush_activity()
        assert (jobs[0].delay, jobs[0].request_timeout, jobs[0].chunk_size) == (1.5, 20, 12)
        saved = _saved(tmp_path)
        assert (saved["delay"], saved["timeout"], saved["chunk_size"], saved["output_mode"]) == (
            1.5, 20, 12, "chunked")
    finally:
        win.destroy()


def test_an_invalid_start_saves_nothing_and_does_not_start(tmp_path, monkeypatch) -> None:
    jobs = _fake_scrape(monkeypatch)
    errors = []
    monkeypatch.setattr(app.messagebox, "showerror", lambda *a, **k: errors.append(a))
    win = _make_app(_store(tmp_path))
    try:
        win._delay_var.set("fast")
        win._on_start()
        assert errors and not jobs and win._worker is None
        assert not _path(tmp_path).exists()
    finally:
        win.destroy()


def test_a_number_that_is_not_valid_never_overwrites_the_saved_value(tmp_path) -> None:
    _write(tmp_path, FULL)
    win = _make_app(_store(tmp_path))
    try:
        win._delay_var.set("abc")
        win._timeout_var.set("99999")  # a whole number, but beyond the bound
        win._chunk_var.set("-4")
        win._mode_var.set("single")
        win.close_advanced_settings()
        saved = _saved(tmp_path)
        assert (saved["delay"], saved["timeout"], saved["chunk_size"]) == (4.5, 45, 25)
        assert saved["output_mode"] == "single"  # the valid choice still saves
        win._delay_var.set("nan")
        win._timeout_var.set("2.5")
        win.close_advanced_settings()
        assert (_saved(tmp_path)["delay"], _saved(tmp_path)["timeout"]) == (4.5, 45)
    finally:
        win.destroy()


def test_closing_the_window_saves_what_is_settled(tmp_path) -> None:
    win = _make_app(_store(tmp_path))
    try:
        win._timeout_var.set("75")
        win._cache_var.set(False)
    finally:
        win.destroy()
    saved = _saved(tmp_path)
    assert saved["timeout"] == 75 and saved["use_cache"] is False


def test_a_theme_change_resets_no_other_preference(tmp_path) -> None:
    _write(tmp_path, dict(FULL, appearance="light"))
    win = _make_app(_store(tmp_path))
    try:
        win.toggle_appearance()
        win.toggle_appearance()
        win.toggle_appearance()
    finally:
        win.destroy()
    assert _saved(tmp_path) == dict(FULL, appearance="dark")
    again = _make_app(_store(tmp_path))
    try:
        assert again._bundle["appearance"] == "dark"
        assert (again._current_slug(), again._mode_var.get(), again._chunk_var.get(),
                again._delay_var.get(), again._headless_var.get()) == (
            "supreme-magus", "chunked", "25", "4.5", True)
    finally:
        again.destroy()


def test_a_theme_change_does_not_save_unsettled_edits(tmp_path) -> None:
    win = _make_app(_store(tmp_path))
    try:
        win._delay_var.set("9")  # typed, not yet committed
        win.toggle_appearance()
        assert _saved(tmp_path)["delay"] == 3.0  # only the appearance changed
        assert _saved(tmp_path)["appearance"] == "dark"
    finally:
        win.destroy()


def test_a_failed_save_never_stops_the_window_from_working(tmp_path, monkeypatch) -> None:
    blocker = tmp_path / "runtime-data"
    blocker.write_text("a file where the settings folder should be", encoding="utf-8")
    jobs = _fake_scrape(monkeypatch)
    store = SettingsStore(blocker / "settings.json")
    store.load()
    win = _make_app(store)
    try:
        _pick_website(win, "dynamic.webnovel.com")
        status = win._status_var.get()
        assert status.startswith("Preferences not saved")
        assert win._current_slug() == "the-noble-queen"  # the choice still applies
        win._status_var.set("something else")
        _pick_website(win, "freewebnovel.com")  # a second failure is not repeated
        assert win._status_var.get() == "something else"
        assert win._current_slug() == "shadow-slave"
        win.toggle_appearance()
        win._output_parent = tmp_path
        win._on_start()
        win._worker.join(5)
        win.flush_activity()
        assert len(jobs) == 1 and jobs[0].novel_slug == "shadow-slave"
        assert win._run_status_var.get() in ("Complete", "Finished — nothing written")
    finally:
        win.destroy()


def test_preferences_are_not_written_after_the_window_has_shut_down(tmp_path) -> None:
    win = _make_app(_store(tmp_path))
    win._delay_var.set("8")
    win.destroy()
    assert _saved(tmp_path)["delay"] == 8.0
    before = _path(tmp_path).read_text(encoding="utf-8")
    win._save_preferences()  # a late call on a destroyed window is a harmless no-op
    assert _path(tmp_path).read_text(encoding="utf-8") == before
