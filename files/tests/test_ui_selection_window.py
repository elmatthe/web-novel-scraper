"""v0.3.0 Checkpoint 02.3: Section 1 on screen — website-first selection — plus the
Resume Existing Run affordance and the final-counter layout.

Offline and deterministic: no network, no real scrape (``pipeline.run_scrape`` is replaced).
Every test builds a real window and skips when no Tk display exists. The preference
persistence tests live in ``test_ui_preferences_window.py``.
"""

from __future__ import annotations

import gc
import sys
import threading

import pytest

import app
from webnovel_scraper import catalog, pipeline
from webnovel_scraper.models import SiteSpec
from webnovel_scraper.ui import appearance, run_activity as ra, run_progress as rpg, selection
from webnovel_scraper.ui.settings import SettingsStore


# ── Helpers ──────────────────────────────────────────────────────────────────
def _make_app(store: SettingsStore | None = None, **kwargs):
    tk = pytest.importorskip("tkinter")
    gc.collect()  # earlier windows' garbage, on the main thread (see test_ui_activity_panel)
    try:
        return app.ScraperApp(store, **kwargs)
    except tk.TclError as exc:  # no display (headless CI) — not applicable here
        pytest.skip(f"no Tk display available: {exc}")


def _store(tmp_path) -> SettingsStore:
    store = SettingsStore(tmp_path / "runtime-data" / "settings.json")
    store.load()
    return store


def _spec(slug, host, *, title=None, enabled=True, adapter="freewebnovel", name=None):
    return SiteSpec(
        novel_slug=slug, novel_title=title or slug.title(), adapter_key=adapter,
        display_name=name or host, enabled=enabled, url=f"https://{host}/novel/{slug}",
        base_url=f"https://{host}",
    )


def _pick_website(win, key: str) -> None:
    """What choosing a Website menu entry does."""
    win._website_var.set(key)
    win._on_website_change()


def _pick_novel(win, title: str) -> None:
    """What choosing a Novel list entry does."""
    win._novel_combo.set(title)
    win._on_novel_change()


def _run_fake(win, monkeypatch, tmp_path):
    """Press Start with a scrape that returns at once; returns the job it was given."""
    jobs = []

    def fake_run_scrape(job, *, log, cancel_event, progress_cb, phase_cb=None):
        jobs.append(job)
        return pipeline.RunReport(output_dir=job.output_dir)

    monkeypatch.setattr(app.pipeline, "run_scrape", fake_run_scrape)
    win._output_parent = tmp_path  # never write under the real ~/Downloads
    win._on_start()
    if win._worker is not None:
        win._worker.join(5)
        assert not win._worker.is_alive()
    win.flush_activity()
    return jobs


def _novel_titles(win) -> list[str]:
    return list(win._novel_combo.cget("values"))


def _scaled_app(monkeypatch, percent: int):
    """A window built under a simulated display scaling (100% = Tk's 96/72)."""
    real = app.ttk.Style

    class Scaled(real):
        def __init__(self, master=None, **kw):
            master.tk.call("tk", "scaling", 96 / 72 * percent / 100)
            super().__init__(master, **kw)

    monkeypatch.setattr(app.ttk, "Style", Scaled)
    return _make_app()


# ── Website first ────────────────────────────────────────────────────────────
def test_section_one_order_is_website_novel_start_end() -> None:
    from tkinter import ttk

    win = _make_app()
    try:
        selection_frame = win._sections[0]
        labels = {
            int(w.grid_info()["row"]): str(w.cget("text"))
            for w in selection_frame.winfo_children()
            if isinstance(w, ttk.Label) and int(w.grid_info().get("column", -1)) == 0
            and str(w.cget("text")) in ("Website", "Novel", "Start chapter", "End chapter")
        }
        assert [labels[row] for row in sorted(labels)] == [
            "Website", "Novel", "Start chapter", "End chapter"]
        assert win._site_menubtn.grid_info()["row"] == 0
        assert win._novel_combo.grid_info()["row"] == 1
    finally:
        win.destroy()


def test_keyboard_order_follows_the_visual_order() -> None:
    win = _make_app()
    try:
        win.update()
        order = [win._site_menubtn]
        for _ in range(3):
            order.append(win.nametowidget(win.tk.call("tk_focusNext", str(order[-1]))))
        assert order[1] is win._novel_combo
        assert str(order[2].cget("textvariable")) == str(win._start_var)
        assert str(order[3].cget("textvariable")) == str(win._end_var)
        # Resume Existing Run is a real control now (03.3): reachable by keyboard, after Start.
        seen, widget = [], win._site_menubtn
        for _ in range(60):
            widget = win.nametowidget(win.tk.call("tk_focusNext", str(widget)))
            seen.append(widget)
        assert win._resume_btn in seen and seen.index(win._start_btn) < seen.index(win._resume_btn)
    finally:
        win.destroy()


def test_the_website_menu_lists_catalog_websites_and_greys_unavailable_ones() -> None:
    win = _make_app()
    try:
        menu = win._site_menu
        entries = [
            (str(menu.entrycget(i, "label")), str(menu.entrycget(i, "state")))
            for i in range(menu.index("end") + 1)
        ]
        assert entries == [
            ("Free Web Novel (.com)", "normal"),
            ("Empire Novel (coming soon)", "disabled"),
            ("Telegraph (coming soon)", "disabled"),
            ("Novel Bin (coming soon)", "disabled"),
            ("WebNovel (Dynamic)", "normal"),
            ("Free Web Novel (.net)", "normal"),
        ]
        # Choosing a disabled entry (a click, or Enter on it) does nothing.
        before = (win._website_var.get(), win._current_slug())
        for index in (1, 2, 3):
            menu.invoke(index)
        assert (win._website_var.get(), win._current_slug()) == before
        assert win._current_spec() is catalog.get_spec("shadow-slave", "freewebnovel")
    finally:
        win.destroy()


def test_the_novel_list_follows_the_chosen_website() -> None:
    win = _make_app()
    try:
        assert _novel_titles(win) == [
            "Shadow Slave", "Reverend Insanity", "Renegade Immortal", "Supreme Magus",
            "Lord of the Mysteries", "Lord of Mysteries 2: Circle of Inevitability"]
        _pick_website(win, "freewebnovel.net")
        assert _novel_titles(win) == [
            "Lord of the Mysteries", "Lord of Mysteries 2: Circle of Inevitability"]
        assert win._current_spec().adapter_key == "freewebnovel_net"
        _pick_website(win, "dynamic.webnovel.com")
        assert _novel_titles(win) == ["The Noble Queen"]
        assert win._novel_combo.get() == "The Noble Queen"
        assert str(win._site_menubtn.cget("text")) == "WebNovel (Dynamic)"
        _pick_website(win, "freewebnovel.com")
        # The Noble Queen is not on Free Web Novel, so the website's first novel is chosen.
        assert win._novel_combo.get() == "Shadow Slave"
        assert win._current_slug() == "shadow-slave"
    finally:
        win.destroy()


def test_changing_website_keeps_the_novel_when_the_new_website_offers_it() -> None:
    win = _make_app()
    try:
        win._load_catalog(
            [_spec("alpha", "one.example"), _spec("beta", "one.example"),
             _spec("alpha", "two.example"), _spec("gamma", "two.example")],
            "one.example", "beta",
        )
        assert (win._website_var.get(), win._current_slug()) == ("one.example", "beta")
        _pick_website(win, "two.example")  # does not offer "beta": first novel instead
        assert win._current_slug() == "alpha"
        _pick_novel(win, "Gamma")
        _pick_website(win, "one.example")  # does not offer "gamma": first novel
        assert win._current_slug() == "alpha"
        _pick_website(win, "two.example")  # alpha is offered on both: kept
        assert win._current_slug() == "alpha"
        assert win._current_spec().base_url == "https://two.example"
    finally:
        win.destroy()


def test_two_websites_with_one_adapter_stay_two_separate_choices() -> None:
    """The adapter does not identify the website (future .net / .com editions)."""
    win = _make_app()
    try:
        com = _spec("novel-x", "site.com", adapter="freewebnovel", name="Site")
        net = _spec("novel-x", "site.net", adapter="freewebnovel", name="Site")
        win._load_catalog([com, net], "site.net", "novel-x")
        labels = [str(win._site_menu.entrycget(i, "label"))
                  for i in range(win._site_menu.index("end") + 1)]
        assert labels == ["Site (site.com)", "Site (site.net)"]
        assert win._current_spec() is net
        _pick_website(win, "site.com")
        assert win._current_spec() is com
        assert str(win._site_menubtn.cget("text")) == "Site (site.com)"
    finally:
        win.destroy()


def test_changing_the_selection_refreshes_every_dependent_detail(tmp_path) -> None:
    win = _make_app()
    try:
        win._output_parent = tmp_path
        win._refresh_output_preview()
        assert win._output_target_var.get() == str(tmp_path / "shadow-slave-1")
        assert str(win._browser_check.cget("state")) == "normal"
        assert str(win._start_btn.cget("state")) == "normal"

        _pick_website(win, "dynamic.webnovel.com")  # plain HTTP: browser options are inert
        assert win._output_target_var.get() == str(tmp_path / "the-noble-queen-1")
        assert str(win._browser_check.cget("state")) == "disabled"
        assert str(win._headless_check.cget("state")) == "disabled"
        assert str(win._http_first_check.cget("state")) == "disabled"
        assert str(win._start_btn.cget("state")) == "normal"
        assert str(win._site_menubtn.cget("text")) == "WebNovel (Dynamic)"
        assert win._site_hint.grid_info() == {}

        _pick_website(win, "freewebnovel.com")
        _pick_novel(win, "Supreme Magus")
        assert win._output_target_var.get() == str(tmp_path / "supreme-magus-1")
        assert str(win._browser_check.cget("state")) == "normal"
        # The existing -N numbering still applies to the preview (that folder now exists).
        (tmp_path / "supreme-magus-1").mkdir()
        win._refresh_output_preview()
        assert win._output_target_var.get() == str(tmp_path / "supreme-magus-2")
    finally:
        win.destroy()


@pytest.mark.parametrize(
    "website, title, slug, adapter",
    [
        ("freewebnovel.com", "Shadow Slave", "shadow-slave", "freewebnovel"),
        ("freewebnovel.com", "Reverend Insanity", "reverend-insanity", "freewebnovel"),
        ("freewebnovel.com", "Renegade Immortal", "renegade-immortal", "freewebnovel"),
        ("freewebnovel.com", "Supreme Magus", "supreme-magus", "freewebnovel"),
        ("dynamic.webnovel.com", "The Noble Queen", "the-noble-queen", "webnovel_dynamic"),
    ],
)
def test_start_passes_the_existing_site_spec_for_the_choice(
        monkeypatch, tmp_path, website, title, slug, adapter) -> None:
    win = _make_app()
    try:
        _pick_website(win, website)
        _pick_novel(win, title)
        spec = win._current_spec()
        assert spec is catalog.get_spec(slug, adapter)  # the catalog's own row, not a copy
        jobs = _run_fake(win, monkeypatch, tmp_path)
        assert len(jobs) == 1
        assert (jobs[0].novel_slug, jobs[0].adapter_key) == (slug, adapter)
        assert catalog.get_spec(jobs[0].novel_slug, jobs[0].adapter_key) is spec
        summary = win._log_texts["Summary"].get("1.0", "end")
        assert f"Run started: {title} from {spec.display_name}." in summary
    finally:
        win.destroy()


def test_start_refuses_when_nothing_can_run(monkeypatch, tmp_path) -> None:
    win = _make_app()
    try:
        errors = []
        monkeypatch.setattr(app.messagebox, "showerror", lambda *a, **k: errors.append(a))
        called = []
        monkeypatch.setattr(app.pipeline, "run_scrape", lambda *a, **k: called.append(1))
        win._load_catalog([s for s in catalog.all_specs() if not s.enabled])
        assert win._current_spec() is None
        assert str(win._start_btn.cget("state")) == "disabled"
        win._on_start()  # even forced past the disabled button
        assert errors and not called and win._worker is None and not win._running
    finally:
        win.destroy()


def test_a_disabled_combination_cannot_be_resolved_or_started(monkeypatch, tmp_path) -> None:
    win = _make_app()
    try:
        called = []
        monkeypatch.setattr(app.pipeline, "run_scrape", lambda *a, **k: called.append(1))
        monkeypatch.setattr(app.messagebox, "showerror", lambda *a, **k: None)
        # Force the variables onto a disabled source, as a bug or a stale value might.
        win._website_var.set("empirenovel.com")
        assert win._current_spec() is None
        win._on_start()
        assert not called and win._worker is None
        # A novel the website does not offer resolves to nothing as well.
        win._website_var.set("dynamic.webnovel.com")
        win._novel_combo.set("Shadow Slave")
        win._novels = selection.novels_for("freewebnovel.com", win._specs)
        win._novel_combo.configure(values=[n.title for n in win._novels])
        assert win._current_spec() is None
    finally:
        win.destroy()


def test_selection_is_locked_during_a_run_and_restored_after(monkeypatch, tmp_path) -> None:
    win = _make_app()
    release = threading.Event()
    started = threading.Event()

    def fake_run_scrape(job, *, log, cancel_event, progress_cb, phase_cb=None):
        started.set()
        release.wait(5)
        return pipeline.RunReport(output_dir=job.output_dir)

    monkeypatch.setattr(app.pipeline, "run_scrape", fake_run_scrape)
    try:
        _pick_website(win, "freewebnovel.com")
        _pick_novel(win, "Reverend Insanity")
        win._output_parent = tmp_path
        win._on_start()
        assert started.wait(5)
        assert str(win._site_menubtn.cget("state")) == "disabled"
        assert str(win._novel_combo.cget("state")) == "disabled"
        assert str(win._resume_btn.cget("state")) == "disabled"
        release.set()
        win._worker.join(5)
        win.flush_activity()
        assert str(win._site_menubtn.cget("state")) == "normal"
        assert str(win._novel_combo.cget("state")) == "readonly"
        assert str(win._resume_btn.cget("state")) == "normal"
        assert (win._website_var.get(), win._current_slug()) == (
            "freewebnovel.com", "reverend-insanity")
    finally:
        release.set()
        if win._worker is not None:
            win._worker.join(5)
        win.destroy()


def test_selection_survives_light_dark_and_advanced_settings() -> None:
    win = _make_app()
    try:
        _pick_website(win, "freewebnovel.com")
        _pick_novel(win, "Supreme Magus")
        chosen = (win._website_var.get(), win._current_slug(), win._novel_combo.current(),
                  str(win._site_menubtn.cget("text")), win._current_spec())
        for _ in range(2):  # Light -> Dark -> Light
            win.toggle_appearance()
            win.update()
            assert (win._website_var.get(), win._current_slug(), win._novel_combo.current(),
                    str(win._site_menubtn.cget("text")), win._current_spec()) == chosen
        win.open_advanced_settings()
        win.update()
        win._delay_var.set("6")
        win.close_advanced_settings()
        win.update()
        assert (win._website_var.get(), win._current_slug(), win._novel_combo.current(),
                str(win._site_menubtn.cget("text")), win._current_spec()) == chosen
    finally:
        win.destroy()


def test_the_website_menu_is_legible_in_both_themes() -> None:
    win = _make_app()
    try:
        for expected in ("light", "dark"):
            if win._bundle["appearance"] != expected:
                win.toggle_appearance()
            colors = win._bundle["colors"]
            menu = win._site_menu
            assert str(menu.cget("background")) == colors["field"]
            assert str(menu.cget("foreground")) == colors["text"]
            assert str(menu.cget("disabledforeground")) == colors["disabled"]
            assert str(menu.cget("activebackground")) == colors["selection"]
            assert str(menu.cget("activeforeground")) == colors["selection_text"]
            assert appearance.contrast_ratio(colors["text"], colors["field"]) >= 4.5
            # "(coming soon)" entries are dim but still readable on the menu background.
            assert appearance.contrast_ratio(colors["disabled"], colors["field"]) >= 3.0
            assert appearance.contrast_ratio(
                colors["selection_text"], colors["selection"]) >= 4.5
    finally:
        win.destroy()


def _unavailable_entries(win):
    menu = win._site_menu
    return [i for i in range(menu.index("end") + 1)
            if str(menu.entrycget(i, "label")).endswith("(coming soon)")]


def test_a_catalog_loaded_while_dark_is_styled_the_same_way() -> None:
    """Entries added after a theme switch must not fall back to the embossed default."""
    win = _make_app()
    try:
        win.toggle_appearance()
        win._load_catalog(catalog.all_specs())
        menu = win._site_menu
        colors = win._bundle["colors"]
        entries = _unavailable_entries(win)
        assert len(entries) == 3
        for i in entries:
            if sys.platform == "win32":
                assert str(menu.entrycget(i, "state")) == "normal"
                assert str(menu.entrycget(i, "foreground")) == colors["disabled"]
            else:
                assert str(menu.entrycget(i, "state")) == "disabled"
            menu.invoke(i)
        assert win._current_spec() is catalog.get_spec("shadow-slave", "freewebnovel")
    finally:
        win.destroy()


def test_coming_soon_entries_stay_dim_and_inert_in_dark_mode() -> None:
    """Windows draws a disabled menu entry embossed, which in Dark reads brighter than an
    enabled one; there the entries are drawn dim and do nothing. Elsewhere (and in Light)
    they are ordinary disabled entries. Either way they are visibly dimmer and cannot be chosen."""
    win = _make_app()
    try:
        menu = win._site_menu
        entries = _unavailable_entries(win)
        assert len(entries) == 3
        for theme in ("dark", "light", "dark"):
            if win._bundle["appearance"] != theme:
                win.toggle_appearance()
            colors = win._bundle["colors"]
            dimmed_by_hand = sys.platform == "win32" and theme == "dark"
            for i in entries:
                if dimmed_by_hand:
                    assert str(menu.entrycget(i, "state")) == "normal"
                    assert str(menu.entrycget(i, "foreground")) == colors["disabled"]
                    # Hovering does not highlight it like a real choice.
                    assert str(menu.entrycget(i, "activebackground")) == colors["field"]
                else:
                    assert str(menu.entrycget(i, "state")) == "disabled"
            # Never selectable, whichever way it is drawn.
            before = (win._website_var.get(), win._current_slug())
            for i in entries:
                menu.invoke(i)
            assert (win._website_var.get(), win._current_slug()) == before
            assert win._current_spec() is catalog.get_spec("shadow-slave", "freewebnovel")
            assert appearance.contrast_ratio(colors["disabled"], colors["field"]) >= 3.0
            # The real choices keep the normal text colour.
            real = [i for i in range(menu.index("end") + 1) if i not in entries]
            assert real and all(str(menu.entrycget(i, "state")) == "normal" for i in real)
    finally:
        win.destroy()


# ── Resume Existing Run (v0.3.0 03.3) ────────────────────────────────────────
def _real_run_folder(tmp_path, mode=None):
    """A real, finished-but-resumable run folder made by the pipeline with a fake site."""
    from webnovel_scraper.chapter_store import ChapterManifestStore
    from webnovel_scraper.models import ChapterContent, ChapterMeta, OutputMode, ScrapeJob

    spec = catalog.get_spec("shadow-slave", "freewebnovel")

    class Site:
        warnings: list = []

        def build_chapter_index(self, spec, **_kw):
            return [ChapterMeta(index=n, url=f"{spec.url}/chapter-{n}", slug=n) for n in (1, 2, 3)]

        def fetch_chapter(self, meta, spec, **_kw):
            if meta.index == 3:
                raise RuntimeError("HTTP 503")
            return ChapterContent(meta.index, f"T{meta.index}", ["Body text of the chapter."])

    report = pipeline.run_scrape(
        ScrapeJob(novel_slug="shadow-slave", adapter_key="freewebnovel", start=1, end=3, delay=0.0,
                  output_mode=mode or OutputMode.CHUNKED, chunk_size=2, use_cache=False,
                  output_dir=tmp_path, allocate_run=True),
        adapter=Site(), log=lambda _m: None, sleep_fn=lambda _s: None,
        manifest_store=ChapterManifestStore(tmp_path / "b", tmp_path / "r"))
    return report.output_dir


def test_resume_existing_run_is_shown_in_the_run_section_and_enabled_when_idle() -> None:
    win = _make_app()
    try:
        run = win._sections[2]
        assert str(win._resume_btn.cget("text")) == "Resume Existing Run"
        assert str(win._resume_btn.cget("state")) == "normal"
        assert str(win._resume_note.cget("text")) == app.RESUME_NOTE
        for widget in (win._resume_btn, win._resume_note):
            assert str(widget).startswith(str(run) + ".")
        win.update()
        assert win._resume_btn.winfo_rooty() > win._start_btn.winfo_rooty()
        assert win._resume_btn.winfo_rooty() > win._run_status_label.winfo_rooty()
        # Locked with the other inputs while a run is in progress.
        assert any(widget is win._resume_btn for widget, _ in win._locked_inputs)
    finally:
        win.destroy()


def test_resume_with_nothing_to_resume_starts_nothing_and_touches_no_folder(monkeypatch, tmp_path) -> None:
    win = _make_app()
    try:
        called, asked = [], []
        monkeypatch.setattr(app.pipeline, "run_scrape", lambda *a, **k: called.append(1))
        monkeypatch.setattr(app.messagebox, "askyesno", lambda *a, **k: asked.append(a) or False)
        out = tmp_path / "out"
        out.mkdir()
        win._output_parent = out
        win._resume_btn.invoke()
        win.update()
        assert asked and "There is no unfinished run" in asked[0][1]
        assert not called and win._worker is None and not win._running
        assert list(out.iterdir()) == []
    finally:
        win.destroy()


def test_resume_continues_the_chosen_run_exactly_as_it_was_set_up(monkeypatch, tmp_path) -> None:
    folder = _real_run_folder(tmp_path)
    win = _make_app()
    jobs = []

    def fake(job, **_kw):
        jobs.append(job)
        return pipeline.RunReport(output_dir=job.output_dir)

    monkeypatch.setattr(app.pipeline, "run_scrape", fake)
    offered = []
    monkeypatch.setattr(win, "_choose_run_to_resume", lambda runs: offered.append(runs) or runs[0].path)
    try:
        win._output_parent = tmp_path
        win._mode_var.set("separate")                      # the window's own choices do not matter
        win._resume_btn.invoke()
        win._worker.join(5)
        win.flush_activity()
        assert [s.path for s in offered[0]] == [folder]
        job = jobs[-1]
        assert job.resume is True and not job.allocate_run and job.output_dir == folder
        assert (job.novel_slug, job.adapter_key) == ("shadow-slave", "freewebnovel")
        assert (job.output_mode.value, job.chunk_size, job.formats) == ("chunked", 2, ("pdf",))
        assert (job.start, job.end) == (1, 3)
        summary = win._log_texts[app.SUMMARY_TAB].get("1.0", "end-1c")
        assert "Run resumed: Shadow Slave" in summary and "1 of 2 files already saved" in summary
        assert str(win._resume_btn.cget("state")) == "normal"
    finally:
        win.destroy()


def test_resume_explains_an_unusable_folder_and_changes_nothing(monkeypatch, tmp_path) -> None:
    old = tmp_path / "shadow-slave-1"
    old.mkdir()
    (old / "Chapter 1_ Old.pdf").write_bytes(b"pre-0.3.0")
    win = _make_app()
    errors, called = [], []
    monkeypatch.setattr(app.messagebox, "showerror", lambda *a, **k: errors.append(a))
    monkeypatch.setattr(app.pipeline, "run_scrape", lambda *a, **k: called.append(1))
    try:
        assert win.start_resume(old) is False
        assert errors and "reservation marker" in errors[0][1]
        assert not called and not win._running
        assert sorted(p.name for p in old.iterdir()) == ["Chapter 1_ Old.pdf"]
    finally:
        win.destroy()


# ── Final counters: wrapped, never clipped ───────────────────────────────────
ALL_COUNTERS = rpg.FinalCounts(
    pdfs=9999, already_present=999, failed=999, recovered=999, not_listed=999)


def _finish_with_all_counters(win) -> None:
    win._tracker.start()
    win._render_run()
    win._events.emit(ra.phase_event("scraping", 3000, 999, at=0.0))
    win._events.emit(ra.finished_event("completed_with_problems", (3000, 3000), ALL_COUNTERS))
    win.flush_activity()


def _assert_counters_fully_visible(win) -> None:
    win.update()
    label = win._detail_label
    text = win._detail_var.get()
    for part in ("9999 PDFs", "999 failed", "999 recovered", "999 already present",
                 "999 not on site list"):
        assert part in text, (part, text)
    assert label.winfo_ismapped()
    right = win.winfo_rootx() + win.winfo_width()
    bottom = win.winfo_rooty() + win.winfo_height()
    run = win._sections[2]
    for widget in (label, win._counts_label, win._run_status_label, win._resume_btn,
                   win._resume_note, win._progress):
        assert widget.winfo_ismapped(), widget
        assert widget.winfo_rootx() + widget.winfo_width() <= right, widget
        assert widget.winfo_rooty() + widget.winfo_height() <= bottom, widget
        assert widget.winfo_rootx() + widget.winfo_width() <= run.winfo_rootx() + run.winfo_width()
        assert widget.winfo_rooty() + widget.winfo_height() <= run.winfo_rooty() + run.winfo_height()
    # The wrapped text gets all the room it asks for (a clipped label would be smaller).
    assert label.winfo_height() >= label.winfo_reqheight()
    assert label.winfo_width() >= label.winfo_reqwidth()
    # The wrap width is room the section really has (a line can never run past its edge).
    assert int(str(label.cget("wraplength"))) <= win._run_info.winfo_width()
    assert win._counts_label.winfo_width() >= win._counts_label.winfo_reqwidth()
    # Wrapping shows the whole line: more than one line, and it all fits the label.
    assert label.winfo_height() > 1.5 * win._counts_label.winfo_height()


def test_every_final_counter_is_readable_at_the_minimum_size() -> None:
    win = _make_app()
    try:
        min_w, min_h = win.geometry_info["minimum"]
        win.geometry(f"{min_w}x{min_h}")
        win.update()
        _finish_with_all_counters(win)
        assert (win.winfo_width(), win.winfo_height()) == (min_w, min_h)
        _assert_counters_fully_visible(win)
        # The window did not have to grow to show them.
        assert (win.winfo_width(), win.winfo_height()) == (min_w, min_h)
        assert (win.winfo_reqwidth(), win.winfo_reqheight()) <= (min_w, min_h)
    finally:
        win.destroy()


def test_nothing_below_the_counters_moves_when_they_appear_or_wrap() -> None:
    win = _make_app()
    try:
        min_w, min_h = win.geometry_info["minimum"]
        win.geometry(f"{min_w}x{min_h}")
        win.update()
        idle = (win._resume_btn.winfo_rooty(), win._sections[2].winfo_height())
        win._tracker.start()
        win._events.emit(ra.phase_event("scraping", 3000, 120, at=0.0))
        win.flush_activity()
        win.update()
        assert (win._resume_btn.winfo_rooty(), win._sections[2].winfo_height()) == idle
        _finish_with_all_counters(win)
        win.update()
        assert (win._resume_btn.winfo_rooty(), win._sections[2].winfo_height()) == idle
    finally:
        win.destroy()


def test_counters_wrap_in_the_remaining_room_while_an_eta_shows() -> None:
    win = _make_app()
    try:
        win.update()
        win._tracker.start()
        win._events.emit(ra.phase_event("scraping", 3000, 120, at=0.0))
        win.flush_activity()
        assert win._detail_var.get() == "120 already present"
        assert win._eta_var.get() == "ETA: —"
        beside_eta = int(str(win._detail_label.cget("wraplength")))
        assert win._detail_label.grid_info()["columnspan"] == 1
        _finish_with_all_counters(win)
        assert win._eta_var.get() == ""
        assert win._detail_label.grid_info()["columnspan"] == 2
        assert int(str(win._detail_label.cget("wraplength"))) > beside_eta
    finally:
        win.destroy()


def test_unknown_counters_stay_honest_while_the_run_is_not_measured() -> None:
    win = _make_app()
    try:
        win._tracker.start()
        win._render_run()
        assert win._run_status_var.get() == "Preparing chapter list…"
        assert win._counts_var.get() == "—"
        assert win._detail_var.get() == ""  # nothing is known, so nothing is claimed
        assert win._eta_var.get() == "ETA: —"
    finally:
        win.destroy()


def test_counters_fit_in_dark_mode_and_after_returning_to_light() -> None:
    win = _make_app()
    try:
        min_w, min_h = win.geometry_info["minimum"]
        win.geometry(f"{min_w}x{min_h}")
        win.update()
        _finish_with_all_counters(win)
        for _ in range(2):
            win.toggle_appearance()
            win.update()
            _assert_counters_fully_visible(win)
            assert win._detail_var.get() == rpg.format_final_counts(ALL_COUNTERS)
    finally:
        win.destroy()


def test_counters_fit_at_the_default_and_a_larger_size() -> None:
    win = _make_app()
    try:
        _finish_with_all_counters(win)
        _assert_counters_fully_visible(win)  # the default size
        w, h = win.winfo_width(), win.winfo_height()
        win.geometry(f"{w + 200}x{h + 120}")
        win.update()
        if (win.winfo_width(), win.winfo_height()) != (w + 200, h + 120):
            pytest.skip("this display cannot fit the enlarged test window")
        _assert_counters_fully_visible(win)
        # Activity still takes every extra pixel; the controls column did not move.
        assert win._left.winfo_width() == win.geometry_info["controls_width"]
        assert int(win._body.grid_columnconfigure(1)["weight"]) == 1
    finally:
        win.destroy()


@pytest.mark.skipif(sys.platform != "win32", reason="'zoomed' is the Windows maximize state")
def test_counters_fit_maximized_and_after_restoring() -> None:
    win = _make_app()
    try:
        _finish_with_all_counters(win)
        win.update()
        restored = (win.winfo_width(), win.winfo_height())
        win.state("zoomed")
        win.update()
        _assert_counters_fully_visible(win)
        win.state("normal")
        win.update()
        assert (win.winfo_width(), win.winfo_height()) == restored
        _assert_counters_fully_visible(win)
    finally:
        win.destroy()


@pytest.mark.parametrize("percent", [125, 150])
def test_layout_holds_at_simulated_display_scaling(monkeypatch, percent) -> None:
    win = _scaled_app(monkeypatch, percent)
    try:
        info = win.geometry_info
        assert info["scale_percent"] == percent
        if not info["fits_screen"]:
            pytest.skip("this display is too small for the scaled minimum size")
        min_w, min_h = info["minimum"]
        win.geometry(f"{min_w}x{min_h}")
        win.update()
        _finish_with_all_counters(win)
        assert (win.winfo_width(), win.winfo_height()) == (min_w, min_h)
        _assert_counters_fully_visible(win)
        # The structure is intact: three sections left, Activity the expanding column.
        assert [str(f.cget("text")) for f in win._sections] == [
            "1. Web Novel Selection", "2. Output Options", "3. Run"]
        assert int(win._body.grid_columnconfigure(0)["weight"]) == 0
        assert int(win._body.grid_columnconfigure(1)["weight"]) == 1
        for widget in (win._site_menubtn, win._novel_combo, win._start_btn, win._stop_btn,
                       win._advanced_btn, win._browse_btn, win._clear_log_btn):
            assert widget.winfo_ismapped()
            assert widget.winfo_rootx() + widget.winfo_width() <= win.winfo_rootx() + min_w
            assert widget.winfo_rooty() + widget.winfo_height() <= win.winfo_rooty() + min_h
    finally:
        win.destroy()


def test_the_new_minimum_width_is_no_larger_than_before_the_resume_row() -> None:
    """The extra rows cost height only: the minimum width stays at the 02.2 value, and the
    controls column is not widened to fit the Resume control or the wrapped counters."""
    win = _make_app()
    try:
        min_w, min_h = win.geometry_info["minimum"]
        assert min_w <= 635 * win.geometry_info["scale_percent"] // 100 + 8
        assert min_h <= 680
        assert win.geometry_info["controls_width"] <= 300
    finally:
        win.destroy()


def _press_in_dialog(win, label, *, title="Resume Existing Run"):
    """Click *label* in the open dialog once it exists (the dialog is modal)."""
    import tkinter as tk
    from tkinter import ttk

    def walk(widget):
        for child in widget.winfo_children():
            yield child
            yield from walk(child)

    def attempt():
        for top in win.winfo_children():
            if isinstance(top, tk.Toplevel) and top.title() == title and top.winfo_exists():
                for widget in walk(top):
                    if isinstance(widget, ttk.Button) and str(widget.cget("text")) == label:
                        widget.invoke()
                        return
        win.after(30, attempt)

    win.after(60, attempt)


def test_the_resume_list_offers_unfinished_runs_and_returns_the_choice(tmp_path) -> None:
    from webnovel_scraper import run_index

    folder = _real_run_folder(tmp_path)
    win = _make_app()
    try:
        runs = run_index.list_runs(tmp_path)
        assert [s.path for s in runs] == [folder]
        _press_in_dialog(win, "Resume")
        assert win._choose_run_to_resume(runs) == folder
        _press_in_dialog(win, "Cancel")
        assert win._choose_run_to_resume(runs) is None
    finally:
        win.destroy()
