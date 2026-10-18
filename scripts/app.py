"""app.py — single-window tkinter GUI entry point.

This is a *thin shell* over the already-tested package modules: the catalog
drives the dropdowns, and a Start press builds a :class:`ScrapeJob`, resolves
the output directory via :func:`pipeline.resolve_output_dir`, and hands the job
to :func:`pipeline.run_scrape` on a non-daemon worker thread. Stop sets a
``threading.Event`` the pipeline/request-manager honour between chapters; window
close polls until the worker exits before destroying the Tk root.

No scraping, parsing, or PDF logic lives here — all of that is in the
``webnovel_scraper`` package and is covered by the offline test suite. The GUI
only collects inputs and shows the log/progress the pipeline emits.

Activity (v0.3.0 Checkpoint 02.1; :mod:`webnovel_scraper.ui.activity`, adapted from
PDF-Splitter): the worker thread never calls Tk, not even ``after``. The pipeline's
``log``/``progress_cb`` callbacks and the run outcome only *enqueue* immutable events
on a bounded, locked channel (and write the session log); a pump owned by the Tk
thread drains it on a timer into the Summary/Detailed tabs, the progress bar and the
run-finished handler. Once the window is destroyed the channel refuses events, so a
late worker line is a harmless no-op. Each application session writes one technical
log under the ignored ``files/runtime-data/logs/``; Clear Log never touches it.

Run section (v0.3.0 Checkpoint 02.2; :mod:`webnovel_scraper.ui.run_progress`): a
``RunTracker`` on the Tk thread turns the pipeline's ``progress_cb`` and optional
``phase_cb`` (delivered through the same Activity events) into the phase, the
"done / total · %" count, the already-present and result counters and an ETA measured on
the monotonic clock; the final state comes from the returned ``RunReport``, never from the
percentage, and nothing that arrives after the run's end can change it.

Website-first selection and preferences (v0.3.0 Checkpoint 02.3;
:mod:`webnovel_scraper.ui.selection`, :mod:`webnovel_scraper.ui.settings`): Section 1 asks for
the Website first, then a Novel that website can run, both taken from the unchanged catalog;
Start still passes the resolved catalog row's novel and adapter to the pipeline. The stable
choices (website and novel, grouping, chunk size, delay, timeout, the browser, headless,
HTTP-first and cache switches) are restored from the settings file at start-up and saved at
discrete moments (a selection or grouping change, closing Advanced Settings, Start, closing the
window) — never per keystroke. "Resume Existing Run" is shown disabled; it does nothing until
Checkpoint 03 provides the validation it needs.

Defense in depth on disabled sites: the catalog greys them in the Website menu and
Start cannot dispatch one (this file), the pipeline refuses a disabled row
before building an adapter, and the stub adapter raises as a final backstop.

Window layout (v0.3.0 Checkpoint 01; adapted from the PDF-Splitter shell, local
working copy supplied 2026-10-17, newer than GitHub ``edcbd4f``):

* a header, a body and a bottom status bar holding the Light/Dark toggle;
* the body has three numbered, bordered sections stacked on the left — 1. Web Novel
  Selection, 2. Output Options, 3. Run — at their *measured natural width* (grid
  weight 0 + minsize), and the Activity panel on the right (weight 1), so every
  extra pixel of a wider or maximized window goes to Activity;
* the rarely changed fetch settings (delay, timeout, browser, headless, HTTP-first,
  HTML cache) live in an "Advanced Settings" window so the main window stays short
  enough for a scaled laptop screen; their values and behaviour are unchanged;
* the window minimum and default size are derived from the widgets' requested sizes,
  font metrics and display scaling (``_fit_window``), never from fixed pixel sizes;
* long novel names and paths never enlarge the window (fields have character-width
  requests; notes wrap to the measured column width).

Appearance comes from :mod:`webnovel_scraper.ui.appearance` (named ``Compact.*`` ttk
styles refreshed in place, so a Light/Dark switch keeps every value, selection, focus
and a running scrape). The choice is saved by :mod:`webnovel_scraper.ui.settings` in
the ignored ``files/runtime-data/settings.json``.
"""

from __future__ import annotations

import sys
import threading
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

# When launched as ``python scripts/app.py`` the script's own
# directory (scripts/) is sys.path[0], so the package import resolves.
# Add it explicitly too, so launching via an absolute path also works.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from webnovel_scraper import catalog, pipeline, run_index, run_storage  # noqa: E402
from webnovel_scraper.models import OutputMode, ScrapeJob  # noqa: E402
from webnovel_scraper.chapter_preflight import ChapterListRefused  # noqa: E402
from webnovel_scraper.registry import AdapterDisabledError  # noqa: E402
from webnovel_scraper.run_journal import JournalError  # noqa: E402
from webnovel_scraper.run_storage import RunStorageError  # noqa: E402
from webnovel_scraper.ui import appearance, run_activity, run_progress, selection  # noqa: E402
from webnovel_scraper.ui import settings as ui_settings  # noqa: E402
from webnovel_scraper.ui.activity import (  # noqa: E402
    ERROR, INFO, WARNING, Activity, ActivityEvent, ActivityPump, RenderUpdate, SessionLog,
)
from webnovel_scraper.ui.settings import SettingsStore  # noqa: E402

# Adapters that can use the Playwright browser path (Cloudflare). Every other
# adapter is HTTP-only, so the browser checkboxes are inert when one of those is
# selected (e.g. webnovel_dynamic, which reads __NEXT_DATA__ over plain HTTP).
_BROWSER_CAPABLE_ADAPTERS = {"freewebnovel"}

# Inter-fetch delay (seconds) the user sets as the anti-detection / politeness
# rate-limit knob. 3.0s is a conservative default (0.2.0) — high enough to look
# human, low enough not to crawl. The shared host limiter (and the pipeline's
# adaptive auto-slowdown) can raise it further if the site starts blocking.
DEFAULT_DELAY = "3.0"
DEFAULT_TIMEOUT = "30"     # per-request timeout (matches request_manager default)
DEFAULT_CHUNK = "10"       # chapters per PDF in CHUNKED mode
# Headless browser OFF by default (0.1.3). The FreeWebNovel primary path is a
# persistent VISIBLE camoufox browser — the legacy-matched configuration Cloudflare
# clears for (a headless browser is exactly what it blocks). A visible browser
# window WILL appear during a FreeWebNovel scrape; that is expected (like the old
# tool). An advanced user can still tick "Headless browser" to force it.
DEFAULT_HEADLESS = False
# Browser mode ON by default for Cloudflare-protected sites (FreeWebNovel). The
# checkbox is only active for browser-capable adapters; for the HTTP-only
# WebNovel-dynamic it is inert and the browser is never forced.
DEFAULT_BROWSER_MODE = True
# "Try fast HTTP first" is OPT-IN (default off): plain HTTP trips FreeWebNovel's
# Cloudflare, which is the whole reason the browser is primary. When enabled it
# tries a couple of cheap HTTP rungs before falling back to camoufox.
DEFAULT_HTTP_FIRST = False
# Where new run folders go (plan decision 4): ~/Downloads/Web-Novel-Scraper-Outputs (the home
# folder if there is no Downloads). Each new run reserves its own "<novel-slug>-N" folder in it.
DEFAULT_OUTPUT_PARENT = run_storage.default_output_root()
# "End = all": a sentinel far above any real chapter count. The pipeline clamps
# the requested range down to the available TOC, so this means "to the end".
ALL_CHAPTERS = 10 ** 9
# How often (ms) the window-close sequence re-checks whether the (non-daemon)
# scrape worker has exited before destroying the window (§4.4). Polled on the Tk
# event loop via ``self.after`` — never a blocking sleep — so the UI stays alive.
CLOSE_POLL_MS = 50

APP_TITLE = "Web Novel Scraper"
SUBTITLE = "Save web-novel chapters as clean, print-ready PDFs."
SECTION_TITLES = ("1. Web Novel Selection", "2. Output Options", "3. Run")
ACTIVITY_TITLE = "Activity"
# User-facing names for the three existing grouping modes (plan §8 C11): only the
# labels changed; the OutputMode values the job carries are the same as before.
GROUPING_LABELS = {
    OutputMode.SEPARATE: "Individual chapters",
    OutputMode.SINGLE: "Whole selected range",
    OutputMode.CHUNKED: "Every",  # followed by the chapters-per-PDF field
}
FORMAT_NOTE = "(TXT not available yet)"
NO_SITE_HINT = "No website is available yet — nothing to run."
NO_WEBSITE_TEXT = "(no website available)"
# Resume Existing Run (v0.3.0 03.3): continues an unfinished run in its own folder with the
# run's own chapters, formats and grouping; only what is missing or fails its checksum is redone.
RESUME_LABEL = "Resume Existing Run"
RESUME_NOTE = "Continues a stopped or unfinished run; saved files are checked, not redone."
OUTPUT_HINT = "Each run gets its own numbered folder here, so nothing is ever overwritten."
NO_RESUMABLE_TEXT = "There is no unfinished run in\n{root}\n\nChoose another run folder?"
BROWSER_HINT = "Opens a visible browser window, which clears Cloudflare."
# The accurate Headless description (0.2.0 §4.2): the breaker may switch a headless
# run to a visible browser for the rest of the run, and the rescue lane is visible.
HEADLESS_HINT = (
    "If Cloudflare broadly blocks headless mode, the app may automatically open and "
    "keep a visible browser for the rest of the run; hard chapters may also open a "
    "visible rescue browser."
)
# Chapter list (v0.3.0 03.2a): chapter lists are saved per website and novel and reused, so a
# run needs no table-of-contents request for chapters it already knows. This one-shot box asks the
# next run to rebuild the saved list from the site; the old list is kept as a backup file and the
# box clears itself after every run, so a refresh is never silent or repeated.
REFRESH_LABEL = "Refresh chapter list on the next run"
REFRESH_HINT = (
    "Rebuilds the saved chapter list from the site. The current saved list is kept as a "
    ".bak backup file. Applies to the next run only."
)
# HTML cache (v0.3.0 03.2b): chapter pages are saved with a checksum and reused until they are
# found unusable. This one-shot box asks the next run to fetch every selected chapter page from
# the site again (the fresh pages replace the saved ones); it clears itself after every Start.
REFETCH_LABEL = "Re-download saved chapter pages on the next run"
REFETCH_HINT = (
    "Fetches every selected chapter from the site again instead of using saved pages, and saves "
    "the new copies. Applies to the next run only."
)
ADVANCED_NOTE = (
    "These settings are read when you press Start and are locked while a run is "
    "in progress."
)
# Activity panel (Checkpoint 02.1; PDF-Splitter's Summary/Detailed notebook).
SUMMARY_TAB = "Summary"
DETAILED_TAB = "Detailed"
ACTIVITY_NOTE = "Summary: progress and results.\nDetailed: every step."
SUMMARY_PLACEHOLDER = (
    "Choose a website and a novel, then press Start.\n"
    "Run milestones, warnings and results will appear here.\n"
)
DETAILED_PLACEHOLDER = "Technical details (fetches, retries, cache, browser) will appear here.\n"
# Lines kept in each displayed view; older lines scroll away (the session log keeps all).
MAX_LOG_LINES = 5000

# The longest final counter line (every counter present, large numbers) and the widest ETA,
# used only to reserve room in the measured minimum window size.
WORST_CASE_DETAIL = run_progress.format_final_counts(run_progress.FinalCounts(
    pdfs=9999, already_present=999, failed=999, recovered=999, not_listed=999))
WIDEST_ETA = "ETA: 99 h 59 min"

# Sizing intents, expressed in characters / lines (never pixels).
NOVEL_FIELD_CHARS = 28
NUMBER_FIELD_CHARS = 8
ACTIVITY_FLOOR_CHARS = 40
ACTIVITY_PREFERRED_CHARS = 72
LOG_FLOOR_LINES = 8
LOG_PREFERRED_LINES = 24
ADVANCED_WRAP_CHARS = 50
SCREEN_FRACTION = 0.92


class ScraperApp(tk.Tk):
    """The single scraper window. All real work happens on a non-daemon worker."""

    def __init__(
        self, store: SettingsStore | None = None, *, platform: str | None = None,
        logs_dir: str | Path | None = None,
    ) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self._platform = sys.platform if platform is None else platform

        # Preferences (appearance only at Checkpoint 01). A missing, unreadable or
        # invalid file never stops the window: the store falls back to defaults and
        # the status bar says so.
        if store is None:
            store = SettingsStore()
            store.load()
        self._store = store
        self._style = ttk.Style(self)
        self._bundle = appearance.build_bundle(
            self._style, store.settings.appearance, root=self, platform=self._platform
        )

        # Run state (all mutated only on the Tk thread except cancel_event).
        self._worker: threading.Thread | None = None
        self._cancel_event: threading.Event | None = None
        self._running = False
        # Set once a window-close is in progress so the close handler is idempotent
        # and a late worker callback knows the window is going away (§4.4).
        self._closing = False
        # Widgets that must be locked while a run is in progress.
        self._locked_inputs: list[tuple[tk.Widget, str]] = []
        # Notes that wrap to the measured control-column width (see _fit_window).
        self._wrap_labels: list[ttk.Label] = []
        # Classic Tk widgets ttk styles cannot reach, with their appearance role.
        self._text_widgets: list[tuple[tk.Text, str]] = []
        self.geometry_info: dict[str, object] = {}
        # Room (px) the Run section's counters may wrap into; measured in ``_fit_window``.
        self._run_interior = 16 * self._unit()
        self._eta_reserve = 0
        # The two Activity views, whether each still shows its placeholder, and whether
        # each follows new lines (it stops while the user reads further up).
        self._log_texts: dict[str, tk.Text] = {}
        self._placeholders = {SUMMARY_TAB: True, DETAILED_TAB: True}
        self._follow = {SUMMARY_TAB: True, DETAILED_TAB: True}
        # Set once the window is being destroyed: the pump is stopped and the Activity
        # channel refuses events, so nothing can touch the window afterwards.
        self._shut_down = False
        # The Run section's phase, counts and ETA for the current run (Tk thread only).
        self._tracker = run_progress.RunTracker()

        # Activity and the session log. Workers talk to them only through Activity.emit
        # (a locked queue); the Tk thread drains it with the pump. The pump keeps the
        # real ``after`` it was given, and runs for the window's whole life: a pipeline
        # helper thread may still log after the worker has returned.
        self._session_log = SessionLog(
            ui_settings.LOGS_DIR if logs_dir is None else Path(logs_dir), app_name=APP_TITLE
        )
        self._events = Activity(
            session_log=self._session_log, on_render=self._render_activity,
            job_sink=self._on_job_event,
        )
        self._pump = ActivityPump(
            self._events, self.after, self.after_cancel, lambda: True, always_on=True
        )

        # The catalog rows the Website and Novel choices are built from (read once, so a
        # test can substitute a catalog with ``_load_catalog``), the websites they define,
        # and the novels the chosen website offers.
        self._specs: list = []
        self._websites: list[selection.Website] = []
        self._novels: list[selection.NovelChoice] = []
        # Set once a save has failed, so the status bar names the problem only once.
        self._prefs_warned = False
        # Menu positions of the "coming soon" websites (see ``_style_unavailable_entries``).
        self._unavailable_entries: list[int] = []
        # Whether the user has chosen a website or novel this session. Until then the saved
        # choice (possibly empty, or no longer runnable) is left exactly as it is, so merely
        # opening and closing the window rewrites nothing.
        self._selection_chosen = False

        # The chosen output PARENT directory. Defaults to ~/Downloads and is only
        # ever changed by the Browse… picker — never by a resolved output dir, so a
        # prior run's output folder can never become the next run's parent (the
        # 0.1.1 nesting bug). The read-only field shows the resolved TARGET, not
        # this parent; Start passes this Path, never the displayed target string.
        self._output_parent: Path = DEFAULT_OUTPUT_PARENT

        self._build_ui()
        self._restore_preferences()
        self._apply_bundle(self._bundle)
        self._fit_window()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # A log that cannot be created is one Summary warning; the window works without it.
        if self._session_log.open():
            self._events.report(
                ActivityEvent("activity", f"Session log: {self._session_log.path}")
            )
        self._events.note_log_open_failure()
        if store.diagnostics:
            problem = f"Settings problem ({store.diagnostics[0]}); safe defaults in use."
            self._set_status(problem)
            self._events.report(ActivityEvent("settings", problem, WARNING, summary=True))
        self._pump.start()

    # ── UI construction ──────────────────────────────────────────────────────
    def _unit(self) -> int:
        """Pixel width of one body-font character (the sizing unit)."""
        return max(
            1, tkfont.Font(root=self, font=self._bundle["fonts"]["body"]).measure("0")
        )

    def _build_ui(self) -> None:
        s, m = self._bundle["styles"], self._bundle["metrics"]
        self._gap = int(m["gap_md"])
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # Header: title and subtitle share one row to save vertical space.
        header = ttk.Frame(
            self, style=s["window"],
            padding=(m["content_pad"], m["gap_md"], m["content_pad"], m["gap_sm"]),
        )
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text=APP_TITLE, style=s["title"], anchor="w").grid(
            row=0, column=0, sticky="sw"
        )
        # width=1 so the subtitle can never force the window wider; it still fills.
        ttk.Label(
            header, text=SUBTITLE, style=s["status_label"], anchor="w", width=1
        ).grid(row=0, column=1, sticky="sew", padx=(m["gap_lg"], 0), pady=(0, m["gap_xs"]))
        ttk.Frame(self, style=s["divider"], height=1).grid(row=0, column=0, sticky="sew")

        # Body: the three control sections (left) + Activity (right).
        self._body = ttk.Frame(self, style=s["window"], padding=m["gap_sm"])
        self._body.grid(row=1, column=0, sticky="nsew")
        self._body.columnconfigure(0, weight=0)
        self._body.columnconfigure(1, weight=1)
        self._body.rowconfigure(0, weight=1)
        self._left = ttk.Frame(self._body, style=s["window"])
        self._left.grid(row=0, column=0, sticky="nsew", padx=(0, self._gap // 2))
        self._left.columnconfigure(0, weight=1)
        # Spare height in a tall window stays below the Run section.
        self._left.rowconfigure(len(SECTION_TITLES), weight=1)
        self._activity = ttk.Labelframe(
            self._body, text=ACTIVITY_TITLE, style=s["labelframe"], padding=m["card_pad"]
        )
        self._activity.grid(row=0, column=1, sticky="nsew", padx=(self._gap // 2, 0))
        self._activity.columnconfigure(0, weight=1)
        self._activity.rowconfigure(1, weight=1)  # row 0: note + Clear Log; row 1: the tabs

        self._sections = [self._section(i) for i in range(len(SECTION_TITLES))]
        e_start, e_end = self._build_selection(self._sections[0])
        rb_sep, rb_single, rb_chunk = self._build_output_options(self._sections[1])
        self._build_run(self._sections[2])
        self._build_activity()
        self._build_status_bar()
        e_delay, e_timeout = self._build_advanced()

        # Inputs locked during a run (state to restore is the normal state).
        self._locked_inputs = [
            (self._novel_combo, "readonly"),
            (self._site_menubtn, "normal"),
            (e_start, "normal"),
            (e_end, "normal"),
            (e_delay, "normal"),
            (e_timeout, "normal"),
            (self._browse_btn, "normal"),
            (self._resume_btn, "normal"),
            (rb_sep, "normal"),
            (rb_chunk, "normal"),
            (rb_single, "normal"),
            (self._cache_check, "normal"),
            (self._refetch_check, "normal"),
            (self._refresh_check, "normal"),
        ]

        self._on_mode_change()

    def _section(self, index: int) -> ttk.Labelframe:
        s, m = self._bundle["styles"], self._bundle["metrics"]
        frame = ttk.Labelframe(
            self._left, text=SECTION_TITLES[index], style=s["labelframe"],
            padding=m["card_pad"],
        )
        frame.grid(row=index, column=0, sticky="ew", pady=(0 if index == 0 else self._gap, 0))
        frame.columnconfigure(1, weight=1)
        return frame

    def _field_label(self, frame: tk.Misc, text: str, row: int) -> ttk.Label:
        s, m = self._bundle["styles"], self._bundle["metrics"]
        label = ttk.Label(frame, text=text, style=s["label"])
        label.grid(
            row=row, column=0, sticky="w", padx=(0, m["gap_md"]),
            pady=(0 if row == 0 else m["gap_sm"], 0),
        )
        return label

    def _note(self, frame: tk.Misc, text: str, style_key: str = "secondary_label") -> ttk.Label:
        """A note that wraps to the measured column width (set in ``_fit_window``)."""
        label = ttk.Label(
            frame, text=text, style=self._bundle["styles"][style_key],
            wraplength=16 * self._unit(), justify="left",
        )
        self._wrap_labels.append(label)
        return label

    def _build_selection(self, frame: ttk.Labelframe) -> tuple[ttk.Entry, ttk.Entry]:
        s, m = self._bundle["styles"], self._bundle["metrics"]
        row_pad = (m["gap_sm"], 0)

        # Website selector (a Menubutton + Menu so individual disabled websites can be
        # greyed and made non-selectable — a Combobox can't disable single items). The
        # website comes first: it decides which novels can be offered below it. A fixed
        # character width: a long website name is clipped and never widens the window.
        self._field_label(frame, "Website", 0)
        self._website_var = tk.StringVar(value="")
        self._site_menubtn = ttk.Menubutton(
            frame, text=NO_WEBSITE_TEXT, style=s["menubutton"], width=NOVEL_FIELD_CHARS
        )
        self._site_menu = tk.Menu(self._site_menubtn, tearoff=0)
        self._site_menubtn["menu"] = self._site_menu
        self._site_menubtn.grid(row=0, column=1, sticky="ew")

        # Novel dropdown: only the novels the chosen website can run. A fixed character
        # width: a long title never widens the window.
        self._field_label(frame, "Novel", 1)
        self._novel_combo = ttk.Combobox(
            frame, state="readonly", style=s["combobox"], width=NOVEL_FIELD_CHARS, values=[],
        )
        self._novel_combo.grid(row=1, column=1, sticky="ew", pady=row_pad)
        self._novel_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_novel_change())

        # Shown only when no website can run (gridded on demand).
        self._site_hint = self._note(frame, "", "warning_label")
        self._site_hint.grid(row=2, column=0, columnspan=2, sticky="w", pady=(m["gap_xs"], 0))
        self._site_hint.grid_remove()

        # Chapter range.
        self._field_label(frame, "Start chapter", 3)
        self._start_var = tk.StringVar(value="1")
        e_start = ttk.Entry(
            frame, textvariable=self._start_var, width=NUMBER_FIELD_CHARS, style=s["entry"]
        )
        e_start.grid(row=3, column=1, sticky="w", pady=row_pad)

        self._field_label(frame, "End chapter", 4)
        end_row = ttk.Frame(frame, style=s["surface"])
        end_row.grid(row=4, column=1, sticky="w", pady=row_pad)
        self._end_var = tk.StringVar(value="")
        e_end = ttk.Entry(
            end_row, textvariable=self._end_var, width=NUMBER_FIELD_CHARS, style=s["entry"]
        )
        e_end.grid(row=0, column=0, sticky="w")
        ttk.Label(end_row, text="blank = all chapters", style=s["secondary_label"]).grid(
            row=0, column=1, sticky="w", padx=(m["gap_md"], 0)
        )
        return e_start, e_end

    def _build_output_options(
        self, frame: ttk.Labelframe
    ) -> tuple[ttk.Radiobutton, ttk.Radiobutton, ttk.Radiobutton]:
        s, m = self._bundle["styles"], self._bundle["metrics"]

        # Formats. PDF is the only renderer today and both formats may never be off,
        # so PDF shows ticked and locked, and TXT shows visibly unavailable. Neither
        # pretends to change the run; TXT is enabled when a TXT renderer exists.
        self._field_label(frame, "Format", 0)
        formats = ttk.Frame(frame, style=s["surface"])
        formats.grid(row=0, column=1, sticky="w")
        self._pdf_var = tk.BooleanVar(value=True)
        self._pdf_check = ttk.Checkbutton(
            formats, text="PDF", variable=self._pdf_var, style=s["checkbutton"],
            state="disabled",
        )
        self._pdf_check.grid(row=0, column=0, sticky="w")
        self._txt_var = tk.BooleanVar(value=False)
        self._txt_check = ttk.Checkbutton(
            formats, text="TXT", variable=self._txt_var, style=s["checkbutton"],
            state="disabled",
        )
        self._txt_check.grid(row=0, column=1, sticky="w", padx=(m["gap_lg"], 0))
        # Beside the boxes (not on a row of its own) so it never makes the section taller.
        self._format_note = ttk.Label(formats, text=FORMAT_NOTE, style=s["secondary_label"])
        self._format_note.grid(row=0, column=2, sticky="w", padx=(m["gap_sm"], 0))

        # Grouping: exactly one of the three existing output modes.
        self._field_label(frame, "Grouping", 1)
        self._mode_var = tk.StringVar(value=OutputMode.SEPARATE.value)
        rb_sep = ttk.Radiobutton(
            frame, text=GROUPING_LABELS[OutputMode.SEPARATE],
            value=OutputMode.SEPARATE.value, variable=self._mode_var,
            command=self._on_grouping_picked, style=s["radiobutton"],
        )
        rb_sep.grid(row=1, column=1, sticky="w", pady=(m["gap_sm"], 0))
        rb_single = ttk.Radiobutton(
            frame, text=GROUPING_LABELS[OutputMode.SINGLE],
            value=OutputMode.SINGLE.value, variable=self._mode_var,
            command=self._on_grouping_picked, style=s["radiobutton"],
        )
        rb_single.grid(row=2, column=1, sticky="w")
        chunk_row = ttk.Frame(frame, style=s["surface"])
        chunk_row.grid(row=3, column=1, sticky="w")
        rb_chunk = ttk.Radiobutton(
            chunk_row, text=GROUPING_LABELS[OutputMode.CHUNKED],
            value=OutputMode.CHUNKED.value, variable=self._mode_var,
            command=self._on_grouping_picked, style=s["radiobutton"],
        )
        rb_chunk.grid(row=0, column=0, sticky="w")
        self._chunk_var = tk.StringVar(value=DEFAULT_CHUNK)
        self._chunk_entry = ttk.Entry(
            chunk_row, textvariable=self._chunk_var, width=5, style=s["entry"]
        )
        self._chunk_entry.grid(row=0, column=1, sticky="w", padx=(m["gap_sm"], m["gap_sm"]))
        ttk.Label(chunk_row, text="chapters per PDF", style=s["label"]).grid(
            row=0, column=2, sticky="w"
        )
        return rb_sep, rb_single, rb_chunk

    def _build_run(self, frame: ttk.Labelframe) -> None:
        s, m = self._bundle["styles"], self._bundle["metrics"]
        row_pad = (m["gap_sm"], 0)

        # Output location (v0.3.0 03.3). The read-only field previews the run folder a new
        # run would reserve ("<novel-slug>-N" in the output root); "Browse…" picks the ROOT.
        # The pipeline reserves the folder atomically when the run actually starts, so the
        # displayed target is a preview only and is NEVER fed back as the root.
        self._field_label(frame, "Output folder", 0)
        self._output_target_var = tk.StringVar(value="")
        # width=12 + sticky "ew": a long path is scrolled inside the field and never
        # widens the window.
        e_output = ttk.Entry(
            frame, textvariable=self._output_target_var, state="readonly", width=12,
            style=s["entry"],
        )
        e_output.grid(row=0, column=1, sticky="ew")
        self._browse_btn = ttk.Button(
            frame, text="Browse…", command=self._on_browse, style=s["button"]
        )
        self._browse_btn.grid(row=0, column=2, sticky="e", padx=(m["gap_md"], 0))

        self._output_hint = self._note(frame, OUTPUT_HINT)
        self._output_hint.grid(row=1, column=0, columnspan=3, sticky="w", pady=(m["gap_xs"], 0))

        # Start / Stop (+ the Advanced Settings window on the right).
        actions = ttk.Frame(frame, style=s["surface"])
        actions.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(m["gap_md"], 0))
        actions.columnconfigure(2, weight=1)
        self._start_btn = ttk.Button(
            actions, text="Start", command=self._on_start, style=s["primary_button"]
        )
        if self._platform == "darwin":
            # Aqua ignores custom button fills; its native default-button cue
            # supplies the primary accent instead (as in PDF-Splitter).
            self._start_btn.configure(default="active")
        self._start_btn.grid(row=0, column=0, sticky="w")
        self._stop_btn = ttk.Button(
            actions, text="Stop", command=self._on_stop, state="disabled",
            style=s["danger_button"],
        )
        self._stop_btn.grid(row=0, column=1, sticky="w", padx=(m["gap_sm"], 0))
        self._advanced_btn = ttk.Button(
            actions, text="Advanced Settings…", command=self.open_advanced_settings,
            style=s["button"],
        )
        self._advanced_btn.grid(row=0, column=2, sticky="e", padx=(m["gap_md"], 0))

        # Progress bar + the run's state and chapter count.
        self._progress = ttk.Progressbar(
            frame, mode="determinate", maximum=1, style=s["progressbar"]
        )
        self._progress.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(m["gap_md"], 0))
        info = self._run_info = ttk.Frame(frame, style=s["surface"])
        info.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(m["gap_xs"], 0))
        info.columnconfigure(0, weight=1)
        # Two compact rows (PDF-Splitter's status / counts / ETA, split so each fits the
        # fixed control column): the phase and "done / total · %", then the secondary
        # counters and the ETA. Width-1 labels clip rather than widen the window.
        self._run_status_var = tk.StringVar(value="Idle")
        self._run_status_label = ttk.Label(
            info, textvariable=self._run_status_var, style=s["secondary_label"], width=1
        )
        self._run_status_label.grid(row=0, column=0, sticky="ew")
        self._counts_var = tk.StringVar(value="—")
        self._counts_label = ttk.Label(info, textvariable=self._counts_var,
                                       style=s["secondary_label"])
        self._counts_label.grid(row=0, column=1, sticky="e", padx=(m["gap_md"], 0))
        # The counters wrap onto further lines instead of clipping (the worst case is
        # reserved in the measured minimum height, see ``_fit_window``). While an ETA is
        # showing the counters share its row; once the run has ended it is blank and they
        # use the full width (``_render_run``).
        self._detail_var = tk.StringVar(value="")
        self._detail_label = ttk.Label(
            info, textvariable=self._detail_var, style=s["secondary_label"],
            wraplength=16 * self._unit(), justify="left", anchor="nw",
        )
        self._detail_label.grid(row=1, column=0, sticky="nw")
        self._eta_var = tk.StringVar(value=run_progress.format_eta(None))
        self._eta_label = ttk.Label(info, textvariable=self._eta_var, style=s["secondary_label"])
        self._eta_label.grid(row=1, column=1, sticky="ne", padx=(m["gap_md"], 0))

        # Resume Existing Run (03.3): offers the unfinished runs in the output root; the
        # chosen run's journal, not the fields above, decides what is fetched and written.
        resume = ttk.Frame(frame, style=s["surface"])
        resume.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(m["gap_md"], 0))
        resume.columnconfigure(1, weight=1)
        self._resume_btn = ttk.Button(
            resume, text=RESUME_LABEL, command=self._on_resume, style=s["button"]
        )
        self._resume_btn.grid(row=0, column=0, sticky="nw")
        self._resume_note = ttk.Label(
            resume, text=RESUME_NOTE, style=s["secondary_label"],
            wraplength=8 * self._unit(), justify="left",
        )
        self._resume_note.grid(row=0, column=1, sticky="w", padx=(m["gap_md"], 0))

    def _build_activity(self) -> None:
        """The Activity panel (PDF-Splitter's design): a short note and Clear Log, then
        a Summary/Detailed notebook of read-only, monospaced, scrollable logs. Each tab
        keeps its own history, so switching tabs or appearance loses nothing."""
        s, m = self._bundle["styles"], self._bundle["metrics"]
        top = ttk.Frame(self._activity, style=s["surface"])
        top.grid(row=0, column=0, sticky="ew", pady=(0, m["gap_sm"]))
        top.columnconfigure(0, weight=1)
        # Wrapped once, to the room the panel has at the window minimum (_fit_window).
        # PDF-Splitter re-wraps it on every resize; here a wider wrap would let the note
        # raise the window's requested width above its measured minimum.
        self._activity_note = ttk.Label(
            top, text=ACTIVITY_NOTE, style=s["secondary_label"],
            wraplength=16 * self._unit(), justify="left",
        )
        self._activity_note.grid(row=0, column=0, sticky="w")
        self._clear_log_btn = ttk.Button(
            top, text="Clear Log", style=s["button"], command=self.clear_log
        )
        self._clear_log_btn.grid(row=0, column=1, sticky="ne", padx=(m["gap_md"], 0))

        self._notebook = ttk.Notebook(self._activity, style=s["notebook"])
        self._notebook.grid(row=1, column=0, sticky="nsew")
        for title, placeholder in (
            (SUMMARY_TAB, SUMMARY_PLACEHOLDER), (DETAILED_TAB, DETAILED_PLACEHOLDER)
        ):
            page = ttk.Frame(self._notebook, style=s["surface"])
            page.columnconfigure(0, weight=1)
            page.rowconfigure(0, weight=1)
            text = tk.Text(
                page, width=ACTIVITY_FLOOR_CHARS, height=LOG_FLOOR_LINES, wrap="word",
                font=self._bundle["fonts"]["mono"], padx=6, pady=4,
            )
            text.grid(row=0, column=0, sticky="nsew")
            bar = ttk.Scrollbar(
                page, orient="vertical", command=text.yview, style=s["vscrollbar"]
            )
            bar.grid(row=0, column=1, sticky="ns")
            text.configure(
                yscrollcommand=lambda first, last, view=title, bar=bar:
                self._on_log_scrolled(view, bar, first, last)
            )
            text.insert("1.0", placeholder)
            text.configure(state="disabled")
            self._notebook.add(page, text=title)
            self._log_texts[title] = text
            self._text_widgets.append((text, "log"))
            self._enable_native_select_all(text)
            # A tab filled while hidden opens at its newest line, and a resize (which Tk
            # makes from the top line) keeps a following view at its newest line.
            for sequence in ("<Map>", "<Configure>"):
                text.bind(sequence, lambda _e, view=title: self._keep_following(view))

    def _enable_native_select_all(self, widget: tk.Misc) -> None:
        """Make Control+A select all text in a log on Windows/Linux, where Tk has no class
        binding for it (Aqua already maps Command+A). From PDF-Splitter."""
        if self._platform == "darwin":
            return
        for sequence in ("<Control-a>", "<Control-A>"):
            widget.bind(
                sequence, lambda _e, w=widget: (w.event_generate("<<SelectAll>>"), "break")[1]
            )

    def _build_status_bar(self) -> None:
        s, m = self._bundle["styles"], self._bundle["metrics"]
        outer = ttk.Frame(self, style=s["window"])
        outer.grid(row=2, column=0, sticky="ew")
        ttk.Frame(outer, style=s["divider"], height=1).pack(fill="x")
        bar = ttk.Frame(outer, style=s["window"], padding=m["status_pad"])
        bar.pack(fill="x")
        self._status_var = tk.StringVar(value="Ready.")
        # width=1 keeps a long message from enlarging the window; it still fills the row.
        ttk.Label(
            bar, textvariable=self._status_var, style=s["status_label"], anchor="w", width=1
        ).pack(side="left", fill="x", expand=True)
        self._appearance_btn = ttk.Button(
            bar, text="", style=s["ghost_button"], command=self.toggle_appearance,
            takefocus=True,
        )
        self._appearance_btn.pack(side="right", padx=(m["gap_md"], 0))

    def _build_advanced(self) -> tuple[ttk.Entry, ttk.Entry]:
        """The Advanced Settings window: built once with the main window and kept
        hidden, so its Tk variables and widgets always exist (Start reads them, the
        run lock disables them) whether or not it is open. Close only hides it."""
        s, m = self._bundle["styles"], self._bundle["metrics"]
        wrap = ADVANCED_WRAP_CHARS * self._unit()
        dialog = tk.Toplevel(self)
        dialog.withdraw()
        dialog.title("Advanced Settings")
        dialog.transient(self)
        dialog.resizable(False, False)
        dialog.protocol("WM_DELETE_WINDOW", self.close_advanced_settings)
        dialog.bind("<Escape>", lambda _e: (self.close_advanced_settings(), "break")[1])
        self._advanced = dialog

        outer = ttk.Frame(dialog, style=s["window"], padding=m["content_pad"])
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)

        def group(title: str, row: int) -> ttk.Labelframe:
            frame = ttk.Labelframe(
                outer, text=title, style=s["labelframe"], padding=m["card_pad"]
            )
            frame.grid(row=row, column=0, sticky="ew", pady=(0 if row == 0 else self._gap, 0))
            frame.columnconfigure(1, weight=1)
            return frame

        def hint(parent: tk.Misc, text: str, row: int, indent: int = 0) -> ttk.Label:
            label = ttk.Label(
                parent, text=text, style=s["secondary_label"], wraplength=wrap - indent,
                justify="left",
            )
            label.grid(row=row, column=0, columnspan=2, sticky="w", padx=(indent, 0))
            return label

        # Delay + timeout. The delay is the user-facing rate-limit / anti-detection
        # knob (Phase 9A): higher = slower but less likely to be blocked.
        speed = group("Speed", 0)
        self._field_label(speed, "Delay between fetches (seconds)", 0)
        self._delay_var = tk.StringVar(value=DEFAULT_DELAY)
        e_delay = ttk.Entry(
            speed, textvariable=self._delay_var, width=NUMBER_FIELD_CHARS, style=s["entry"]
        )
        e_delay.grid(row=0, column=1, sticky="w")
        self._delay_hint = hint(
            speed, "Anti-detection: higher = slower but less likely to be blocked.", 1
        )
        self._field_label(speed, "Timeout (seconds)", 2)
        self._timeout_var = tk.StringVar(value=DEFAULT_TIMEOUT)
        e_timeout = ttk.Entry(
            speed, textvariable=self._timeout_var, width=NUMBER_FIELD_CHARS, style=s["entry"]
        )
        e_timeout.grid(row=2, column=1, sticky="w", pady=(m["gap_sm"], 0))

        # Browser mode + its modifiers (Free Web Novel only; inert for HTTP sites).
        indent = 20
        browser = group("Browser", 1)
        self._browser_var = tk.BooleanVar(value=DEFAULT_BROWSER_MODE)
        self._browser_check = ttk.Checkbutton(
            browser,
            text="Use browser mode (recommended for Free Web Novel)",
            variable=self._browser_var,
            command=self._on_browser_toggle,
            style=s["checkbutton"],
        )
        self._browser_check.grid(row=0, column=0, columnspan=2, sticky="w")
        self._browser_hint = hint(browser, BROWSER_HINT, 1, indent)
        self._headless_var = tk.BooleanVar(value=DEFAULT_HEADLESS)
        self._headless_check = ttk.Checkbutton(
            browser,
            text="Headless primary browser (advanced)",
            variable=self._headless_var,
            style=s["checkbutton"],
        )
        self._headless_check.grid(
            row=2, column=0, columnspan=2, sticky="w", padx=(indent, 0), pady=(m["gap_sm"], 0)
        )
        self._headless_hint = hint(browser, HEADLESS_HINT, 3, 2 * indent)
        self._http_first_var = tk.BooleanVar(value=DEFAULT_HTTP_FIRST)
        self._http_first_check = ttk.Checkbutton(
            browser,
            text="Try fast HTTP first (may trip Cloudflare — off by default)",
            variable=self._http_first_var,
            style=s["checkbutton"],
        )
        self._http_first_check.grid(
            row=4, column=0, columnspan=2, sticky="w", padx=(indent, 0), pady=(m["gap_sm"], 0)
        )

        cache = group("Cache", 2)
        self._cache_var = tk.BooleanVar(value=True)
        self._cache_check = ttk.Checkbutton(
            cache, text="Use HTML cache (saved chapter pages are reused)",
            variable=self._cache_var, style=s["checkbutton"],
        )
        self._cache_check.grid(row=0, column=0, columnspan=2, sticky="w")
        self._refetch_var = tk.BooleanVar(value=False)
        self._refetch_check = ttk.Checkbutton(
            cache, text=REFETCH_LABEL, variable=self._refetch_var, style=s["checkbutton"],
        )
        self._refetch_check.grid(row=1, column=0, columnspan=2, sticky="w", pady=(m["gap_sm"], 0))
        hint(cache, REFETCH_HINT, 2, indent)

        chapters = group("Chapter list", 3)
        self._refresh_var = tk.BooleanVar(value=False)
        self._refresh_check = ttk.Checkbutton(
            chapters, text=REFRESH_LABEL, variable=self._refresh_var, style=s["checkbutton"],
        )
        self._refresh_check.grid(row=0, column=0, columnspan=2, sticky="w")
        hint(chapters, REFRESH_HINT, 1, indent)

        ttk.Label(
            outer, text=ADVANCED_NOTE, style=s["status_label"], wraplength=wrap,
            justify="left",
        ).grid(row=4, column=0, sticky="w", pady=(m["gap_md"], 0))
        self._advanced_close = ttk.Button(
            outer, text="Close", command=self.close_advanced_settings, style=s["button"]
        )
        self._advanced_close.grid(row=5, column=0, sticky="e", pady=(m["gap_md"], 0))
        return e_delay, e_timeout

    # ── Appearance + window fit ──────────────────────────────────────────────
    def toggle_appearance(self) -> None:
        """Switch Light/Dark in place and save the choice. Named styles are
        reconfigured, never rebuilt, so values, selections, focus and a running
        scrape are untouched. A failed save keeps the choice for this session."""
        new = appearance.toggled(self._store.settings.appearance)
        result = self._store.update(appearance=new)
        bundle = appearance.build_bundle(
            self._style, new, platform=self._platform, root=self
        )
        appearance.notify_listeners(bundle)
        self._apply_bundle(bundle)
        name = new.capitalize()
        self._set_status(
            f"Appearance: {name}." if result.ok
            else f"Appearance: {name} for this session only (not saved: {result.message})."
        )

    def _apply_bundle(self, bundle: dict) -> None:
        """Apply *bundle* to everything ttk styles cannot reach."""
        self._bundle = bundle
        colors = bundle["colors"]
        self.configure(background=colors["window"])
        self._advanced.configure(background=colors["window"])
        ring = max(1, round(bundle["metrics"]["border_width"]))
        for widget, role in self._text_widgets:
            appearance.style_tk_widget(widget, bundle, role, highlightthickness=ring)
        if bundle.get("ttk_active"):
            # On Aqua the Website menu and the dropdown list stay native.
            appearance.style_tk_widget(self._site_menu, bundle, "menu")
            appearance.style_combobox_popdown(self._novel_combo, bundle)
            self._style_unavailable_entries()
        # Warnings and errors are told apart from ordinary lines by colour as well as words.
        for widget in self._log_texts.values():
            widget.tag_configure(WARNING, foreground=colors["warning"])
            widget.tag_configure(ERROR, foreground=colors["danger"])
        other = "Light" if bundle["appearance"] == appearance.DARK else "Dark"
        self._appearance_btn.configure(text=f"{other} mode")

    def _set_status(self, message: str) -> None:
        self._status_var.set(message)

    def _set_log_size(self, *, chars: int, lines: int) -> None:
        for text in self._log_texts.values():
            text.configure(width=chars, height=lines)

    def _fit_window(self) -> None:
        """Derive the minimum and default geometry from measured widget sizes
        (PDF-Splitter's measure -> minimum-size -> default technique)."""
        unit = self._unit()
        m = self._bundle["metrics"]
        hint_text = str(self._site_hint.cget("text"))

        # Minimum: the log at its floor, the no-site hint shown and the longest final
        # counter line (wrapped) in place, so showing either later can never clip the Run
        # section. The ETA text is measured at its widest to know how much room the
        # counters share a row with while a run is going.
        self._site_hint.configure(text=NO_SITE_HINT)
        self._site_hint.grid()
        detail_text, eta_text = self._detail_var.get(), self._eta_var.get()
        self._detail_var.set(WORST_CASE_DETAIL)
        self._eta_var.set(WIDEST_ETA)
        self._set_log_size(chars=ACTIVITY_FLOOR_CHARS, lines=LOG_FLOOR_LINES)
        self.update_idletasks()
        # The Activity note wraps beside Clear Log within the tabs' floor width, so it
        # never widens the panel.
        note_room = (self._notebook.winfo_reqwidth() - self._clear_log_btn.winfo_reqwidth()
                     - int(m["gap_md"]))
        self._activity_note.configure(wraplength=max(8 * unit, note_room))
        self.update_idletasks()
        left_width = self._left.winfo_reqwidth()
        self._body.columnconfigure(0, weight=0, minsize=left_width)
        # A section's interior is the column minus its padding and 1px borders.
        interior = left_width - 2 * (int(m["card_pad"]) + int(m["border_width"]) + 1)
        for label in self._wrap_labels:
            label.configure(wraplength=max(8 * unit, interior))
        self._run_interior = interior
        self._eta_reserve = self._eta_label.winfo_reqwidth() + int(m["gap_md"])
        self._wrap_run_detail(with_eta=False)
        # Hold that row's height in every state, so nothing below it moves when the
        # counters appear or wrap.
        self.update_idletasks()
        self._run_info.rowconfigure(1, minsize=self._detail_label.winfo_reqheight())
        self._resume_note.configure(wraplength=max(
            6 * unit, interior - self._resume_btn.winfo_reqwidth() - int(m["gap_md"])))
        self.update_idletasks()
        min_width, min_height = self.winfo_reqwidth(), self.winfo_reqheight()

        # Default: a roomier log; the hint as the current selection needs it.
        self._detail_var.set(detail_text)
        self._eta_var.set(eta_text)
        self._set_site_hint(hint_text)
        self._set_log_size(chars=ACTIVITY_PREFERRED_CHARS, lines=LOG_PREFERRED_LINES)
        self.update_idletasks()
        want_width, want_height = self.winfo_reqwidth(), self.winfo_reqheight()

        # Back to the floor so the window's own requested size is the minimum; the
        # explicit geometry below decides the real size and Activity absorbs the rest.
        self._set_log_size(chars=ACTIVITY_FLOOR_CHARS, lines=LOG_FLOOR_LINES)
        self.update_idletasks()

        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        width = max(min_width, min(want_width, int(screen_w * SCREEN_FRACTION)))
        height = max(min_height, min(want_height, int(screen_h * SCREEN_FRACTION)))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.minsize(min_width, min_height)
        self.geometry(f"{width}x{height}+{x}+{y}")
        fits = min_width <= screen_w and min_height <= screen_h
        scaling = float(self.tk.call("tk", "scaling"))
        self.geometry_info = {
            "minimum": (min_width, min_height), "default": (width, height),
            "screen": (screen_w, screen_h), "tk_scaling": round(scaling, 3),
            "scale_percent": round(scaling / (96 / 72) * 100),
            "controls_width": left_width, "unit_px": unit, "fits_screen": fits,
        }
        if not fits:
            self._set_status("This display is too small for the minimum window size.")

    # ── Advanced Settings window ─────────────────────────────────────────────
    def open_advanced_settings(self) -> tk.Toplevel:
        """Show the Advanced Settings window centred over the main window."""
        dialog = self._advanced
        dialog.update_idletasks()
        width, height = dialog.winfo_reqwidth(), dialog.winfo_reqheight()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - width) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - height) // 3)
        x = max(0, min(x, self.winfo_screenwidth() - width))
        y = max(0, min(y, self.winfo_screenheight() - height))
        dialog.geometry(f"+{x}+{y}")
        dialog.deiconify()
        dialog.lift()
        dialog.focus_set()
        return dialog

    def close_advanced_settings(self) -> None:
        self._advanced.withdraw()
        self._save_preferences()  # the one moment its values are known to be settled

    # ── Selection handlers ───────────────────────────────────────────────────
    def _current_slug(self) -> str:
        """The selected novel's slug, or "" when the website offers no novel."""
        idx = self._novel_combo.current()
        if not self._novels:
            return ""
        return self._novels[idx if 0 <= idx < len(self._novels) else 0].slug

    def _current_spec(self):
        """The catalog row for the current website and novel, or None when there is no
        runnable combination. This row, and nothing the screen merely displays, is what
        Start passes on."""
        key = self._website_var.get()
        if not key:
            return None
        return selection.resolve(key, self._current_slug(), self._specs)

    def _load_catalog(self, specs, website: str = "", novel: str = "") -> None:
        """Build the Website menu from *specs* (the unchanged catalog rows) and settle on
        the nearest valid (website, novel) to the one asked for; no preference is saved."""
        self._specs = list(specs)
        self._websites = selection.websites(self._specs)
        self._site_menu.delete(0, "end")
        self._unavailable_entries = []
        for site in self._websites:
            if site.enabled:
                self._site_menu.add_radiobutton(
                    label=site.label, value=site.key, variable=self._website_var,
                    command=self._on_website_change,
                )
            else:
                # Greyed + non-selectable: visible but unrunnable.
                self._site_menu.add_command(
                    label=f"{site.label} (coming soon)", state="disabled", command=_ignore
                )
                self._unavailable_entries.append(self._site_menu.index("end"))
        self._style_unavailable_entries()
        self._select(website, novel)

    def _style_unavailable_entries(self) -> None:
        """Keep the "coming soon" entries clearly dimmer than the real choices. Windows draws
        a disabled menu entry embossed, which in Dark mode reads brighter and bolder than an
        enabled one, so there (Dark only) they are drawn as dim entries that do nothing when
        chosen; everywhere else they are ordinary disabled entries."""
        colors = self._bundle["colors"]
        dim = (self._platform == "win32" and self._bundle.get("ttk_active")
               and self._bundle["appearance"] == appearance.DARK)
        for index in self._unavailable_entries:
            if dim:
                self._site_menu.entryconfigure(
                    index, state="normal", foreground=colors["disabled"],
                    activeforeground=colors["disabled"], activebackground=colors["field"],
                )
            else:
                self._site_menu.entryconfigure(
                    index, state="disabled", foreground="", activeforeground="",
                    activebackground="",
                )

    def _select(self, website: str, novel: str) -> None:
        """Show the nearest valid (website, novel) to the one asked for: a website that
        cannot run gives way to the first that can, and the novel is kept if that website
        offers it (otherwise its first). Refreshes everything that depends on the choice."""
        pair = selection.settle(website, novel, self._specs)
        if pair is None:
            self._website_var.set("")
            self._novels = []
            self._novel_combo.configure(values=[])
            self._novel_combo.set("")
        else:
            self._website_var.set(pair[0])
            self._novels = selection.novels_for(pair[0], self._specs)
            self._novel_combo.configure(values=[n.title for n in self._novels])
            self._novel_combo.current(
                next(i for i, n in enumerate(self._novels) if n.slug == pair[1])
            )
        self._on_selection_changed()

    def _on_website_change(self) -> None:
        """A website was picked: keep the novel if it offers it, else its first novel."""
        self._selection_chosen = True
        self._select(self._website_var.get(), self._current_slug())
        self._save_preferences()

    def _on_novel_change(self) -> None:
        """A novel was picked from the list of the current website."""
        self._selection_chosen = True
        self._select(self._website_var.get(), self._current_slug())
        self._save_preferences()

    def _on_selection_changed(self) -> None:
        """Refresh everything the (website, novel) choice decides."""
        spec = self._current_spec()
        site = next((w for w in self._websites if w.key == self._website_var.get()), None)
        self._site_menubtn.configure(text=site.label if site else NO_WEBSITE_TEXT)
        self._set_site_hint("" if spec is not None else NO_SITE_HINT)
        self._refresh_browser_state()
        self._refresh_start_state()
        self._refresh_output_preview()

    def _set_site_hint(self, text: str) -> None:
        """Show the hint only when it has something to say (its row is reserved in
        the measured minimum, so showing it never clips the sections below)."""
        self._site_hint.configure(text=text)
        if text:
            self._site_hint.grid()
        else:
            self._site_hint.grid_remove()

    def _on_mode_change(self) -> None:
        chunked = self._mode_var.get() == OutputMode.CHUNKED.value
        self._chunk_entry.configure(state="normal" if chunked else "disabled")

    def _on_grouping_picked(self) -> None:
        """A grouping radio button was chosen (not merely refreshed)."""
        self._on_mode_change()
        self._save_preferences()

    def _resolve_output_dir(self) -> Path:
        """The run folder a new run would reserve right now (a preview: the pipeline reserves
        the real one atomically, so another run taking this number first is harmless)."""
        return run_storage.preview_next_run_dir(self._output_parent, self._current_slug())

    def _refresh_output_preview(self) -> None:
        """Update the read-only target display to where this run would write. This
        is display-only — the resolved target is NEVER passed back as a parent."""
        slug = self._current_slug()
        if not slug:  # nothing selected, so nothing would be written
            self._output_target_var.set("")
            return
        try:
            target = self._resolve_output_dir()
        except Exception:
            # Fall back to a best-effort preview if resolution ever fails.
            target = self._output_parent / slug
        self._output_target_var.set(str(target))

    def _on_browse(self) -> None:
        """Open the native folder picker for the output PARENT directory.

        The selection becomes the parent that the novel's ``{name}-N`` folder is
        created inside — it is stored in ``self._output_parent`` (a Path), never
        in the read-only target display, so a folder picked here can never be
        re-used as a parent on the next run (the 0.1.1 nesting bug)."""
        initial = (
            str(self._output_parent)
            if self._output_parent.is_dir()
            else str(Path.home())
        )
        chosen = filedialog.askdirectory(
            title="Choose the folder that holds your run folders",
            initialdir=initial,
        )
        if chosen:  # empty string => the user cancelled; keep the current value
            self._output_parent = Path(chosen)
            self._refresh_output_preview()

    def _on_browser_toggle(self) -> None:
        self._refresh_browser_state()

    def _refresh_browser_state(self) -> None:
        """Enable the browser checkboxes only for browser-capable adapters; for
        HTTP-only adapters (e.g. webnovel_dynamic) they are inert."""
        spec = self._current_spec()
        capable = spec is not None and spec.adapter_key in _BROWSER_CAPABLE_ADAPTERS
        if self._running:
            capable = False
        self._browser_check.configure(state="normal" if capable else "disabled")
        # Headless only matters when browser mode is both available and on.
        browser_on = capable and self._browser_var.get()
        self._headless_check.configure(
            state="normal" if browser_on else "disabled"
        )
        # HTTP-first is a modifier on the browser-primary path, so it is only
        # meaningful for a browser-capable adapter with browser mode on.
        self._http_first_check.configure(
            state="normal" if browser_on else "disabled"
        )

    def _refresh_start_state(self) -> None:
        spec = self._current_spec()
        can_start = spec is not None and spec.enabled and not self._running
        self._start_btn.configure(state="normal" if can_start else "disabled")

    # ── Preferences ──────────────────────────────────────────────────────────
    def _restore_preferences(self) -> None:
        """Start-up: show the saved choices. Anything missing, invalid or no longer in the
        catalog was already replaced by a safe default (the store) or the nearest valid
        website and novel (``_select``). Nothing is written here."""
        p = self._store.settings
        self._mode_var.set(p.output_mode)
        self._chunk_var.set(str(p.chunk_size))
        self._delay_var.set(str(p.delay))
        self._timeout_var.set(str(p.timeout))
        self._browser_var.set(p.browser_mode)
        self._headless_var.set(p.headless)
        self._http_first_var.set(p.http_first)
        self._cache_var.set(p.use_cache)
        self._on_mode_change()
        self._load_catalog(catalog.all_specs(), p.website, p.novel)

    def _preferences(self) -> ui_settings.Settings:
        """The stable choices as they stand now. A number field that is not currently a
        valid, in-range number keeps its saved value rather than saving garbage."""
        current = self._store.settings

        def number(var: tk.StringVar, kind, bounds, saved):
            try:
                value = kind(var.get().strip())
            except ValueError:
                return saved
            if value != value or not bounds[0] <= value <= bounds[1]:
                return saved
            return value

        website, novel = current.website, current.novel
        if self._selection_chosen and self._website_var.get():
            website, novel = self._website_var.get(), self._current_slug()
        return replace(
            current,
            website=website, novel=novel,
            output_mode=self._mode_var.get(),
            chunk_size=number(self._chunk_var, int, ui_settings.CHUNK_SIZE_RANGE,
                              current.chunk_size),
            delay=number(self._delay_var, float, ui_settings.DELAY_RANGE, current.delay),
            timeout=number(self._timeout_var, int, ui_settings.TIMEOUT_RANGE, current.timeout),
            browser_mode=bool(self._browser_var.get()),
            headless=bool(self._headless_var.get()),
            http_first=bool(self._http_first_var.get()),
            use_cache=bool(self._cache_var.get()),
        )

    def _save_preferences(self) -> None:
        """Save the stable choices (only when something changed). Called at discrete
        moments, never per keystroke. A failed save leaves the window fully working and
        is named once in the status bar."""
        if self._shut_down:
            return
        try:
            result = self._store.apply(self._preferences())
        except (ValueError, tk.TclError):
            return  # never let preferences get in the way of the window
        if not result.ok and not self._prefs_warned:
            self._prefs_warned = True
            self._set_status(f"Preferences not saved: {result.message}.")

    # ── Run lifecycle ────────────────────────────────────────────────────────
    def _on_start(self) -> None:
        spec = self._current_spec()
        if spec is None or not spec.enabled:
            messagebox.showerror(
                "No runnable site",
                "Pick a novel and an available site before starting.",
            )
            return

        params = self._collect_params()
        if params is None:
            return
        start, end, delay, timeout, mode, chunk = params
        self._save_preferences()  # valid now; the next launch starts from these choices

        # The browser toggle only applies to browser-capable adapters. The choice
        # travels on the immutable ScrapeJob (the pipeline builds a per-run SiteSpec
        # copy via runtime_site_spec) — the GUI no longer mutates the shared catalog
        # row (§3.14). Recomputed every run so a prior selection never leaks.
        use_browser = (
            self._browser_var.get()
            and spec.adapter_key in _BROWSER_CAPABLE_ADAPTERS
        )
        # HTTP-first only modifies the browser-primary path; it is inert when the
        # browser is not the primary engine for this run.
        http_first = bool(self._http_first_var.get()) and use_browser

        # Output location (03.3): the root (default ~/Downloads/Web-Novel-Scraper-Outputs, or a
        # Browse… choice). The pipeline reserves "<slug>-N" inside it once the chapter list is
        # settled, so a refused run leaves no folder behind. Never the displayed preview.
        output_dir = self._output_parent
        job = ScrapeJob(
            novel_slug=spec.novel_slug,
            adapter_key=spec.adapter_key,
            start=start,
            end=end,
            delay=delay,
            output_mode=mode,
            use_cache=self._cache_var.get(),
            output_dir=output_dir,
            allocate_run=True,
            chunk_size=chunk,
            http_first=http_first,
            # 0.2.0 run-config (§3.14/§3.15): the run's behaviour now travels on the
            # immutable job, not a mutated module global. request_timeout is the GUI
            # Timeout field (ordinary HTTP/nav timeout); the 180s rescue deadline is a
            # separate internal ceiling. rescue_workers stays 1 (single-lane).
            use_browser=use_browser,
            headless=bool(self._headless_var.get()),
            request_timeout=timeout,
            refresh_chapter_list=bool(self._refresh_var.get()),
            refresh_html_cache=bool(self._refetch_var.get()) and bool(self._cache_var.get()),
        )
        # One-shot: the request now travels on the job, so clear the boxes. A refresh is never
        # repeated on a later run unless the user asks for it again.
        self._refresh_var.set(False)
        self._refetch_var.set(False)
        self._launch(job, spec, saving_to=self._resolve_output_dir())

    def _launch(self, job: ScrapeJob, spec, *, saving_to: Path, resuming: str = "") -> None:
        """Start the worker for *job* (a new run or a resume) and report what it will do."""
        use_browser, http_first = job.use_browser, job.http_first
        delay, timeout = job.delay, job.request_timeout
        start, end, mode, chunk = job.start, job.end, job.output_mode, job.chunk_size

        # The GUI no longer pre-builds a RequestManager (§3.15): the pipeline OWNS
        # and may REPLACE the active primary manager (TOC fallback / breaker switch),
        # so it constructs it from the job via the request_manager_factory seam. The
        # legacy WND/HTTP path likewise builds its own manager from the same job.
        self._cancel_event = threading.Event()

        self._set_running(True)
        self._tracker.start()
        self._render_run()
        browser = "off"
        if use_browser:
            browser = f"on ({'headless' if job.headless else 'visible'} primary)"
        self._events.report_many(run_activity.run_started_events(
            novel=spec.novel_title, site=spec.display_name, start=start,
            end=None if end >= ALL_CHAPTERS else end,
            grouping=_grouping_text(mode, chunk), output_dir=saving_to, resuming=resuming,
            formats=tuple(job.formats),
            settings=(
                f"delay {delay:g} s, timeout {timeout} s, browser mode {browser}, "
                f"HTTP first {'on' if http_first else 'off'}, "
                f"HTML cache {'on' if job.use_cache else 'off'}"
                f"{', saved chapter pages re-downloaded' if job.refresh_html_cache else ''}"
                f"{', chapter list refresh requested' if job.refresh_chapter_list else ''}."
            ),
        ))

        # Non-daemon (§4.4): a daemon worker could be killed at interpreter exit
        # before the pipeline's teardown ``finally`` closes the browser / rescue
        # pool. The window-close sequence instead signals cancel and waits for this
        # thread to exit (so teardown always runs) before destroying the window.
        self._worker = threading.Thread(
            target=self._run_worker, args=(job,), daemon=False
        )
        self._worker.start()

    # ── Resume Existing Run (v0.3.0 03.3) ────────────────────────────────────
    def _on_resume(self) -> None:
        """Offer the unfinished runs in the output root (or any run folder the user picks) and
        resume the chosen one exactly as it was set up."""
        if self._running:
            return
        runs = run_index.list_runs(self._output_parent)
        if runs:
            chosen = self._choose_run_to_resume(runs)
        else:
            chosen = None
            if messagebox.askyesno(RESUME_LABEL,
                                   NO_RESUMABLE_TEXT.format(root=self._output_parent)):
                folder = filedialog.askdirectory(title="Choose the run folder to resume",
                                                 initialdir=str(self._output_parent))
                chosen = Path(folder) if folder else None
        if chosen is not None:
            self.start_resume(chosen)

    def _choose_run_to_resume(self, runs: list) -> Path | None:
        """A small modal list of resumable runs; returns the chosen folder or None."""
        s, m = self._bundle["styles"], self._bundle["metrics"]
        titles = {spec.novel_slug: spec.novel_title for spec in self._specs}
        dialog = tk.Toplevel(self)
        dialog.title(RESUME_LABEL)
        dialog.transient(self)
        result: list[Path | None] = [None]
        frame = ttk.Frame(dialog, style=s["window"], padding=m["content_pad"])
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Unfinished runs (newest first):", style=s["label"]).pack(anchor="w")
        listbox = tk.Listbox(frame, height=min(10, max(3, len(runs))), width=90,
                             activestyle="dotbox", exportselection=False)
        appearance.style_tk_widget(listbox, self._bundle, "list")
        colors = self._bundle.get("colors")
        if colors:
            dialog.configure(background=colors["window"])
        for summary in runs:
            listbox.insert("end", summary.describe(titles.get(summary.header.novel_slug)))
        listbox.selection_set(0)
        listbox.pack(fill="both", expand=True, pady=(m["gap_sm"], m["gap_sm"]))
        buttons = ttk.Frame(frame, style=s["window"])
        buttons.pack(fill="x")

        def finish(value):
            result[0] = value
            dialog.destroy()

        def pick_selected():
            picked = listbox.curselection()
            finish(runs[picked[0]].path if picked else None)

        def pick_other():
            folder = filedialog.askdirectory(parent=dialog, title="Choose the run folder to resume",
                                             initialdir=str(self._output_parent))
            if folder:
                finish(Path(folder))

        ttk.Button(buttons, text="Resume", command=pick_selected, style=s["primary_button"]).pack(side="left")
        ttk.Button(buttons, text="Choose another folder…", command=pick_other,
                   style=s["button"]).pack(side="left", padx=(m["gap_sm"], 0))
        ttk.Button(buttons, text="Cancel", command=lambda: finish(None),
                   style=s["button"]).pack(side="right")
        listbox.bind("<Double-Button-1>", lambda _e: pick_selected())
        dialog.bind("<Escape>", lambda _e: finish(None))
        dialog.grab_set()
        self.wait_window(dialog)
        return result[0]

    def start_resume(self, run_dir: Path) -> bool:
        """Validate *run_dir* and resume it with its own chapters, formats and grouping. Returns
        whether a run started (an invalid folder is explained and nothing is touched)."""
        if self._running:
            return False
        try:
            summary = run_index.describe_run(run_dir)
        except (RunStorageError, JournalError, OSError, ValueError) as exc:
            messagebox.showerror("Cannot resume this folder", str(exc))
            return False
        header = summary.header
        site = selection.website_key_for_host(summary.host)
        spec = selection.resolve(site, header.novel_slug, self._specs)
        if spec is None:
            messagebox.showerror(
                "Cannot resume this run",
                f"{header.novel_slug} from {summary.host} is not available in this version, so "
                "this run cannot be resumed.")
            return False
        params = self._collect_params(validate_range=False)
        if params is None:
            return False
        _start, _end, delay, timeout, _mode, _chunk = params
        use_browser = self._browser_var.get() and spec.adapter_key in _BROWSER_CAPABLE_ADAPTERS
        job = ScrapeJob(
            novel_slug=spec.novel_slug, adapter_key=spec.adapter_key,
            start=header.requested[0], end=header.requested[1], delay=delay,
            output_mode=OutputMode(header.grouping_mode), use_cache=self._cache_var.get(),
            output_dir=summary.path, chunk_size=header.chunk_size, formats=header.formats,
            resume=True, http_first=bool(self._http_first_var.get()) and use_browser,
            use_browser=use_browser, headless=bool(self._headless_var.get()),
            request_timeout=timeout,
        )
        self._launch(job, spec, saving_to=summary.path,
                     resuming=f"{summary.files_done} of {summary.files_total} files already saved")
        return True

    def _collect_params(self, *, validate_range: bool = True):
        """Validate the entry fields. Returns a tuple or None (after an error). A resume takes
        its range from the run, so it skips the range fields (``validate_range=False``)."""
        if not validate_range:
            start, end_raw = 1, ""
        else:
            try:
                start = int(self._start_var.get().strip())
                if start < 1:
                    raise ValueError("Start chapter must be 1 or greater.")
            except ValueError as exc:
                messagebox.showerror("Invalid start chapter", str(exc))
                return None
            end_raw = self._end_var.get().strip()
        if end_raw == "":
            end = ALL_CHAPTERS
        else:
            try:
                end = int(end_raw)
            except ValueError:
                messagebox.showerror(
                    "Invalid end chapter", "End chapter must be a whole number or blank."
                )
                return None
            if end < start:
                messagebox.showerror(
                    "Invalid range",
                    "End chapter cannot be less than the start chapter.",
                )
                return None

        try:
            delay = float(self._delay_var.get().strip())
            if delay < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Invalid delay", "Delay must be a number of seconds (0 or more)."
            )
            return None

        try:
            timeout = int(self._timeout_var.get().strip())
            if timeout <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Invalid timeout", "Timeout must be a whole number of seconds (1+)."
            )
            return None

        mode = OutputMode(self._mode_var.get())
        chunk = int(DEFAULT_CHUNK)
        if mode is OutputMode.CHUNKED:
            try:
                chunk = int(self._chunk_var.get().strip())
                if chunk < 1:
                    raise ValueError
            except ValueError:
                messagebox.showerror(
                    "Invalid chunk size",
                    "Chapters per PDF must be a whole number (1 or more).",
                )
                return None

        return start, end, delay, timeout, mode, chunk

    def _run_worker(self, job: ScrapeJob) -> None:
        """Worker-thread body: drive the pipeline. The pipeline now OWNS and tears
        down the request manager(s) itself (§3.15) — the GUI no longer pre-builds
        one. This thread never touches Tk: the callbacks and the outcome below only
        enqueue Activity events, which the window's pump draws on the Tk thread. They
        are refused once the window is destroyed, so a late finish never raises.

        The outcome is read from the pipeline's RunReport (or the exception), never
        from the progress count, and it is always followed by the run-finished event."""
        processed: list[tuple[int, int]] = []

        def progress(done: int, total: int) -> None:
            processed[:] = [(done, total)]
            self._thread_progress(done, total)

        report = error = refused = None
        counts = None
        try:
            report = pipeline.run_scrape(
                job,
                log=self._thread_log,
                cancel_event=self._cancel_event,
                progress_cb=progress,
                phase_cb=self._thread_phase,
            )
        except (AdapterDisabledError, ChapterListRefused, RunStorageError, JournalError) as exc:
            refused = exc
        except Exception as exc:  # never let the worker thread die silently
            error = exc
        finally:
            outcome = "failed"
            try:
                outcome, events = run_activity.outcome_events(
                    report, processed=processed[0] if processed else None,
                    error=error, refused=refused,
                )
                counts = run_activity.final_counts(report)
            except Exception as exc:  # noqa: BLE001 - the end of the run must still show
                events = [ActivityEvent(
                    run_activity.ORIGIN_RUN,
                    f"The run outcome could not be summarised: {type(exc).__name__}: {exc}",
                    ERROR, summary=True,
                )]
            for event in events:
                self._events.emit(event)
            self._events.emit(run_activity.finished_event(
                outcome, processed[0] if processed else None, counts))

    def _on_stop(self) -> None:
        # Show what the run has already said first; it may have just finished.
        self.flush_activity()
        if not self._running:
            return
        # Reported before the cancel is signalled, so the request always precedes
        # whatever the stop causes, on screen and in the session log.
        self._events.report(ActivityEvent(
            run_activity.ORIGIN_RUN,
            "Stop requested — finishing the current chapter, then halting…", summary=True,
        ))
        if self._cancel_event is not None:
            self._cancel_event.set()
        self._tracker.stop_requested()
        self._render_run()
        self._stop_btn.configure(state="disabled")

    def _on_run_finished(self) -> None:
        if self._closing:
            # The close poll destroys the window once the worker has exited; keep Start
            # locked rather than offering a new run in the meantime.
            self._running = False
            return
        self._set_running(False)
        # The run reserved its <slug>-N folder; refresh the preview so the next run
        # shows the next free -N (still a sibling under the same root).
        self._refresh_output_preview()

    # ── Worker-thread callbacks: enqueue only, never Tk ──────────────────────
    def _thread_log(self, msg: str) -> None:
        """The pipeline's ``log`` callback (worker or pipeline helper threads). After
        the window is destroyed the channel refuses the line, so it is a no-op."""
        try:
            self._events.emit(run_activity.pipeline_event(str(msg)))
        except Exception:  # noqa: BLE001 - a log line must never break the scrape
            pass

    def _thread_progress(self, done: int, total: int) -> None:
        """The pipeline's ``progress_cb``; a pending update is replaced by a newer one."""
        try:
            self._events.emit(run_activity.progress_event(done, total))
        except Exception:  # noqa: BLE001
            pass

    def _thread_phase(self, phase: str, total: int, already_present: int) -> None:
        """The pipeline's optional ``phase_cb``: where the run is. Enqueue only."""
        try:
            self._events.emit(run_activity.phase_event(phase, total, already_present))
        except Exception:  # noqa: BLE001
            pass

    # ── Activity (Tk thread) ─────────────────────────────────────────────────
    def _on_job_event(self, event: ActivityEvent) -> None:
        """Progress, phase and run-finished events, in order with the log lines around
        them. The tracker ignores anything that arrives after the run's end, so a
        late routine update can never overwrite the final outcome."""
        payload = event.payload
        if isinstance(payload, run_activity.ProgressUpdate):
            self._tracker.progress(payload.done, payload.total, payload.at)
            self._render_run()
        elif isinstance(payload, run_activity.PhaseUpdate):
            self._tracker.phase(payload.phase, payload.total, payload.already_present,
                                payload.at)
            self._render_run()
        elif isinstance(payload, run_activity.RunFinished):
            self._tracker.finish(payload.outcome, processed=payload.processed,
                                 counts=payload.counts)
            self._render_run()
            self._on_run_finished()

    def _render_activity(self, update: RenderUpdate) -> None:
        """Draw an Activity update into the two read-only views. A view follows new
        lines while it is scrolled to the end; scrolled up, it stays where it is. Only
        a visible view can tell (a hidden Text has no real scroll position), so a
        hidden tab keeps what it was doing when last shown."""
        if self._shut_down:
            return
        self._refresh_eta()
        for view, lines in ((SUMMARY_TAB, update.summary), (DETAILED_TAB, update.detailed)):
            if not (lines or update.cleared):
                continue
            widget = self._log_texts[view]
            try:
                if widget.winfo_ismapped():
                    self._follow[view] = widget.yview()[1] >= 0.999
                widget.configure(state="normal")
                if update.cleared or self._placeholders[view]:
                    # A clear, or the first real line, replaces the placeholder.
                    widget.delete("1.0", "end")
                    self._placeholders[view] = False
                    self._follow[view] = True
                for line in lines:
                    tags = () if line.severity == INFO else (line.severity,)
                    widget.insert("end", line.text + "\n", tags)
                overflow = int(widget.index("end-1c").split(".")[0]) - MAX_LOG_LINES
                if overflow > 0:
                    widget.delete("1.0", f"{overflow + 1}.0")
                if self._follow[view]:
                    widget.see("end")
                widget.configure(state="disabled")
            except tk.TclError:
                return

    def _keep_following(self, view: str) -> None:
        """A log tab was shown or resized: keep its newest line in view if it is
        following them (before Tk redraws it, so the follow state survives)."""
        if not self._shut_down and self._follow[view]:
            self._log_texts[view].see("end")

    def _on_log_scrolled(self, view: str, bar: ttk.Scrollbar, first: str, last: str) -> None:
        """The view moved (the user scrolled, or new lines were drawn): update its
        scrollbar and, while it is visible, whether it is following the end."""
        bar.set(first, last)
        if self._log_texts[view].winfo_ismapped():
            self._follow[view] = float(last) >= 0.999

    def flush_activity(self) -> int:
        """Tk thread: draw every queued Activity event now (the pump does this on its
        timer). Returns how many events were routed."""
        return self._events.pump_all()

    def clear_log(self) -> None:
        """Clear Log: empty the displayed Summary and Detailed history only. The session
        log on disk and any running scrape are untouched."""
        self._events.clear_display()
        self._set_status("Activity display cleared. The session log on disk is unchanged.")

    def _render_run(self) -> None:
        """Draw the tracker's view into the Run section (Tk thread)."""
        if self._shut_down:
            return
        view = self._tracker.view()
        if not self._closing:  # "Closing…" stays until the window goes
            self._run_status_var.set(view.status)
        style_key = {"warning": "warning_label", "error": "danger_label"}.get(
            view.severity, "secondary_label")
        self._run_status_label.configure(style=self._bundle["styles"][style_key])
        self._counts_var.set(view.counts)
        self._detail_var.set(view.detail)
        self._eta_var.set(view.eta)
        self._wrap_run_detail(with_eta=bool(view.eta))
        bar = self._progress
        if view.indeterminate:
            # No chapter total yet (the chapter list is being prepared): no fake percentage.
            if str(bar.cget("mode")) != "indeterminate":
                bar.configure(mode="indeterminate")
                bar.start()
            return
        if str(bar.cget("mode")) == "indeterminate":
            bar.stop()
            bar.configure(mode="determinate")
        bar.configure(maximum=max(view.total or 1, 1), value=view.done or 0)

    def _wrap_run_detail(self, *, with_eta: bool) -> None:
        """Wrap the counters to the room they have: beside the ETA while one is showing,
        the whole row once the run has ended (and the ETA is blank)."""
        room = max(self._run_interior - (self._eta_reserve if with_eta else 0), 8 * self._unit())
        self._detail_label.configure(wraplength=room)
        self._detail_label.grid_configure(columnspan=1 if with_eta else 2)

    def _refresh_eta(self) -> None:
        """Re-read the ETA when the window redraws anything (no timer): a long silent
        stretch turns a stale estimate back into "—"."""
        if self._tracker.active:
            text = run_progress.format_eta(self._tracker.eta_seconds())
            if text != self._eta_var.get():
                self._eta_var.set(text)

    # ── Enable/disable while running ─────────────────────────────────────────
    def _set_running(self, running: bool) -> None:
        self._running = running
        for widget, normal_state in self._locked_inputs:
            widget.configure(state="disabled" if running else normal_state)
        self._stop_btn.configure(state="normal" if running else "disabled")
        if running:
            self._start_btn.configure(state="disabled")
        else:
            self._refresh_start_state()
            self._on_mode_change()
        self._refresh_browser_state()

    def _on_close(self) -> None:
        """WM_DELETE_WINDOW handler (§4.4). If a scrape is live, confirm, then start
        a *graceful* close: signal cancel and poll until the non-daemon worker has
        exited before destroying the window, so the pipeline's teardown ``finally``
        (browser + rescue-pool close) always runs. Idempotent — a second close while
        already tearing down is ignored."""
        if self._closing:
            return
        if self._running:
            if not messagebox.askokcancel(
                "Quit",
                "A scrape is still running. Stop it and quit?",
            ):
                return
            self._begin_close()
        else:
            self.destroy()

    def _begin_close(self) -> None:
        """Signal cancellation, mark the window closing, and begin polling until the
        worker thread exits. Separated from ``_on_close`` so a test can drive the
        poll loop without the modal confirm dialog."""
        self._closing = True
        # Reported before the cancel is signalled (see _on_stop).
        self._events.report(ActivityEvent(
            run_activity.ORIGIN_RUN,
            "Closing — waiting for the current chapter to finish…", summary=True,
        ))
        if self._cancel_event is not None:
            self._cancel_event.set()
        self._tracker.stop_requested()
        self._render_run()
        self._run_status_var.set("Closing after the current chapter…")
        # Lock the buttons; the window stays up (responsive) while we wait.
        for btn in (self._stop_btn, self._start_btn):
            try:
                btn.configure(state="disabled")
            except Exception:
                pass
        self._poll_close()

    def _poll_close(self) -> None:
        """Re-scheduled via ``self.after`` (the Tk event loop — no blocking sleep)
        until the worker thread is gone, then destroy. A test injects a fake
        ``after`` and steps the callback, so coverage never waits on the real clock."""
        worker = self._worker
        if worker is None or not worker.is_alive():
            self.destroy()
            return
        self.after(CLOSE_POLL_MS, self._poll_close)

    def _shutdown(self) -> None:
        """Retire everything that could touch the window later. Idempotent. The worker's
        final lines are already in the session log (it is written as events are emitted),
        so closing it here loses nothing; later events are refused."""
        if self._shut_down:
            return
        self._shut_down = True
        self._pump.stop()
        try:
            self._progress.stop()  # an indeterminate bar owns a Tk timer
        except tk.TclError:
            pass
        self._events.close()  # refuses later events and closes the session log

    def destroy(self) -> None:
        """Every route that closes the window (idle close, the close poll, tests) comes
        through here, so the pump never fires on a destroyed window."""
        self._save_preferences()
        self._shutdown()
        super().destroy()


def _ignore() -> None:
    """The command of a "coming soon" menu entry: choosing one does nothing."""


def _grouping_text(mode: OutputMode, chunk: int) -> str:
    """The grouping as the Activity 'Output' line words it."""
    if mode is OutputMode.CHUNKED:
        return f"every {chunk} chapters per PDF"
    if mode is OutputMode.SINGLE:
        return "the whole selected range in one PDF"
    return "one PDF per chapter"


def main() -> None:
    """GUI entry point — launched by Setup_and_Run / the package."""
    store = SettingsStore()
    store.load()
    app = ScraperApp(store)
    app.mainloop()


if __name__ == "__main__":
    main()
