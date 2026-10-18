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
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

# When launched as ``python scripts/app.py`` the script's own
# directory (scripts/) is sys.path[0], so the package import resolves.
# Add it explicitly too, so launching via an absolute path also works.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from webnovel_scraper import catalog, pipeline  # noqa: E402
from webnovel_scraper.models import OutputMode, ScrapeJob  # noqa: E402
from webnovel_scraper.registry import AdapterDisabledError  # noqa: E402
from webnovel_scraper.ui import appearance, run_activity  # noqa: E402
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
# Default parent for scraped output: the user's Downloads folder. Resolved via
# Path.home() so it is platform-neutral (never a hardcoded path). The novel slug
# (or a custom name) plus a "-N" auto-increment is appended by the pipeline.
DEFAULT_OUTPUT_PARENT = Path.home() / "Downloads"
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
NO_SITE_HINT = "All sources for this novel are coming soon — nothing to run yet."
FOLDER_HINT = "Blank = novel name. A number is added so nothing is overwritten."
BROWSER_HINT = "Opens a visible browser window, which clears Cloudflare."
# The accurate Headless description (0.2.0 §4.2): the breaker may switch a headless
# run to a visible browser for the rest of the run, and the rescue lane is visible.
HEADLESS_HINT = (
    "If Cloudflare broadly blocks headless mode, the app may automatically open and "
    "keep a visible browser for the rest of the run; hard chapters may also open a "
    "visible rescue browser."
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
    "Choose a novel and a website, then press Start.\n"
    "Run milestones, warnings and results will appear here.\n"
)
DETAILED_PLACEHOLDER = "Technical details (fetches, retries, cache, browser) will appear here.\n"
# Lines kept in each displayed view; older lines scroll away (the session log keeps all).
MAX_LOG_LINES = 5000

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
        # The two Activity views, whether each still shows its placeholder, and whether
        # each follows new lines (it stops while the user reads further up).
        self._log_texts: dict[str, tk.Text] = {}
        self._placeholders = {SUMMARY_TAB: True, DETAILED_TAB: True}
        self._follow = {SUMMARY_TAB: True, DETAILED_TAB: True}
        # Set once the window is being destroyed: the pump is stopped and the Activity
        # channel refuses events, so nothing can touch the window afterwards.
        self._shut_down = False

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

        # Novel slug list in catalog order; titles for display.
        self._novel_slugs = catalog.all_novel_slugs()

        # The chosen output PARENT directory. Defaults to ~/Downloads and is only
        # ever changed by the Browse… picker — never by a resolved output dir, so a
        # prior run's output folder can never become the next run's parent (the
        # 0.1.1 nesting bug). The read-only field shows the resolved TARGET, not
        # this parent; Start passes this Path, never the displayed target string.
        self._output_parent: Path = DEFAULT_OUTPUT_PARENT

        self._build_ui()
        self._novel_combo.current(0)
        self._on_novel_change()
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
        e_folder = self._build_run(self._sections[2])
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
            (e_folder, "normal"),
            (rb_sep, "normal"),
            (rb_chunk, "normal"),
            (rb_single, "normal"),
            (self._cache_check, "normal"),
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

        # Novel dropdown. A fixed character width: a long title never widens the window.
        self._field_label(frame, "Novel", 0)
        self._novel_combo = ttk.Combobox(
            frame,
            state="readonly",
            style=s["combobox"],
            width=NOVEL_FIELD_CHARS,
            values=[
                catalog.get_adapters_for_novel(slug)[0].novel_title
                for slug in self._novel_slugs
            ],
        )
        self._novel_combo.grid(row=0, column=1, sticky="ew")
        self._novel_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self._on_novel_change()
        )

        # Website selector (a Menubutton + Menu so individual disabled sites can be
        # greyed and made non-selectable — a Combobox can't disable single items).
        # It lists the sites for the selected novel, so it sits below Novel.
        self._field_label(frame, "Website", 1)
        self._site_key = tk.StringVar(value="")
        # A fixed character width, like the Novel field: a long site name is clipped
        # inside the picker and never widens the window.
        self._site_menubtn = ttk.Menubutton(
            frame, text="(select a novel)", style=s["menubutton"], width=NOVEL_FIELD_CHARS
        )
        self._site_menu = tk.Menu(self._site_menubtn, tearoff=0)
        self._site_menubtn["menu"] = self._site_menu
        self._site_menubtn.grid(row=1, column=1, sticky="ew", pady=row_pad)

        # Shown only when the novel has no runnable site (gridded on demand).
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
            command=self._on_mode_change, style=s["radiobutton"],
        )
        rb_sep.grid(row=1, column=1, sticky="w", pady=(m["gap_sm"], 0))
        rb_single = ttk.Radiobutton(
            frame, text=GROUPING_LABELS[OutputMode.SINGLE],
            value=OutputMode.SINGLE.value, variable=self._mode_var,
            command=self._on_mode_change, style=s["radiobutton"],
        )
        rb_single.grid(row=2, column=1, sticky="w")
        chunk_row = ttk.Frame(frame, style=s["surface"])
        chunk_row.grid(row=3, column=1, sticky="w")
        rb_chunk = ttk.Radiobutton(
            chunk_row, text=GROUPING_LABELS[OutputMode.CHUNKED],
            value=OutputMode.CHUNKED.value, variable=self._mode_var,
            command=self._on_mode_change, style=s["radiobutton"],
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

    def _build_run(self, frame: ttk.Labelframe) -> ttk.Entry:
        s, m = self._bundle["styles"], self._bundle["metrics"]
        row_pad = (m["gap_sm"], 0)

        # Output location. The read-only field shows the resolved TARGET folder
        # (where the PDFs will actually land); "Browse…" picks the PARENT folder,
        # and the optional name field overrides the folder name (blank = novel
        # slug). A "-N" auto-increment is appended so nothing is overwritten. All
        # path resolution stays in pipeline.resolve_output_dir — this is a thin
        # GUI shell. The displayed target is NEVER fed back as the parent.
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

        self._field_label(frame, "Folder name", 1)
        self._folder_name_var = tk.StringVar(value="")
        e_folder = ttk.Entry(
            frame, textvariable=self._folder_name_var, width=12, style=s["entry"]
        )
        e_folder.grid(row=1, column=1, columnspan=2, sticky="ew", pady=row_pad)
        e_folder.bind("<KeyRelease>", lambda _e: self._refresh_output_preview())
        self._folder_hint = self._note(frame, FOLDER_HINT)
        self._folder_hint.grid(row=2, column=0, columnspan=3, sticky="w", pady=(m["gap_xs"], 0))

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
        info = ttk.Frame(frame, style=s["surface"])
        info.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(m["gap_xs"], 0))
        info.columnconfigure(0, weight=1)
        self._run_status_var = tk.StringVar(value="Idle")
        ttk.Label(
            info, textvariable=self._run_status_var, style=s["secondary_label"], width=1
        ).grid(row=0, column=0, sticky="ew")
        self._counts_var = tk.StringVar(value="—")
        ttk.Label(info, textvariable=self._counts_var, style=s["secondary_label"]).grid(
            row=0, column=1, sticky="e", padx=(m["gap_md"], 0)
        )
        return e_folder

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
            cache, text="Use HTML cache (resume re-runs)",
            variable=self._cache_var, style=s["checkbutton"],
        )
        self._cache_check.grid(row=0, column=0, columnspan=2, sticky="w")

        ttk.Label(
            outer, text=ADVANCED_NOTE, style=s["status_label"], wraplength=wrap,
            justify="left",
        ).grid(row=3, column=0, sticky="w", pady=(m["gap_md"], 0))
        self._advanced_close = ttk.Button(
            outer, text="Close", command=self.close_advanced_settings, style=s["button"]
        )
        self._advanced_close.grid(row=4, column=0, sticky="e", pady=(m["gap_md"], 0))
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

        # Minimum: the log at its floor and the no-site hint shown, so showing that
        # hint later can never clip the Run section.
        self._site_hint.configure(text=NO_SITE_HINT)
        self._site_hint.grid()
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
        self.update_idletasks()
        min_width, min_height = self.winfo_reqwidth(), self.winfo_reqheight()

        # Default: a roomier log; the hint as the current selection needs it.
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

    # ── Selection handlers ───────────────────────────────────────────────────
    def _current_slug(self) -> str:
        idx = self._novel_combo.current()
        return self._novel_slugs[idx if idx >= 0 else 0]

    def _current_spec(self):
        """The resolved SiteSpec for the current novel+site, or None if no
        enabled site is selected."""
        key = self._site_key.get()
        if not key:
            return None
        try:
            return catalog.get_spec(self._current_slug(), key)
        except KeyError:
            return None

    def _on_novel_change(self) -> None:
        """Repopulate the Website menu for the newly selected novel."""
        self._site_menu.delete(0, "end")
        specs = catalog.get_adapters_for_novel(self._current_slug())
        first_enabled: str | None = None
        for spec in specs:
            if spec.enabled:
                self._site_menu.add_radiobutton(
                    label=spec.display_name,
                    value=spec.adapter_key,
                    variable=self._site_key,
                    command=self._on_site_change,
                )
                if first_enabled is None:
                    first_enabled = spec.adapter_key
            else:
                # Greyed + non-selectable: visible but unrunnable.
                self._site_menu.add_command(
                    label=f"{spec.display_name} (coming soon)", state="disabled"
                )
        self._site_key.set(first_enabled or "")
        self._on_site_change()
        self._refresh_output_preview()

    def _on_site_change(self) -> None:
        spec = self._current_spec()
        if spec is not None:
            self._site_menubtn.configure(text=spec.display_name)
            self._set_site_hint("")
        else:
            self._site_menubtn.configure(text="(no site available)")
            self._set_site_hint(NO_SITE_HINT)
        self._refresh_browser_state()
        self._refresh_start_state()

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

    def _resolve_output_dir(self) -> Path:
        """Resolve the output dir from the current parent + name. The single place
        the GUI calls resolve_output_dir, so the rules (default ~/Downloads parent,
        {slug}-N name, -N no-overwrite increment) live entirely in the pipeline."""
        name = self._folder_name_var.get().strip()
        return pipeline.resolve_output_dir(
            self._current_slug(),
            parent_dir=self._output_parent,
            base_name=name or None,
        )

    def _refresh_output_preview(self) -> None:
        """Update the read-only target display to where this run would write. This
        is display-only — the resolved target is NEVER passed back as a parent."""
        try:
            target = self._resolve_output_dir()
        except Exception:
            # Fall back to a best-effort preview if resolution ever fails.
            target = self._output_parent / self._current_slug()
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
            title="Choose the folder to save the novel folder into",
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

        # Output location: the parent (default ~/Downloads, or a Browse… choice)
        # plus an optional custom name (blank => novel slug). resolve_output_dir
        # applies the "-N" no-overwrite increment. Resolved ONCE here from the
        # stored parent Path — never from the displayed target — so a prior output
        # dir can never become this run's parent.
        output_dir = self._resolve_output_dir()
        job = ScrapeJob(
            novel_slug=spec.novel_slug,
            adapter_key=spec.adapter_key,
            start=start,
            end=end,
            delay=delay,
            output_mode=mode,
            use_cache=self._cache_var.get(),
            output_dir=output_dir,
            chunk_size=chunk,
            http_first=http_first,
            # 0.2.0 run-config (§3.14/§3.15): the run's behaviour now travels on the
            # immutable job, not a mutated module global. request_timeout is the GUI
            # Timeout field (ordinary HTTP/nav timeout); the 180s rescue deadline is a
            # separate internal ceiling. rescue_workers stays 1 (single-lane).
            use_browser=use_browser,
            headless=bool(self._headless_var.get()),
            request_timeout=timeout,
        )

        # The GUI no longer pre-builds a RequestManager (§3.15): the pipeline OWNS
        # and may REPLACE the active primary manager (TOC fallback / breaker switch),
        # so it constructs it from the job via the request_manager_factory seam. The
        # legacy WND/HTTP path likewise builds its own manager from the same job.
        self._cancel_event = threading.Event()

        self._set_running(True)
        self._progress.configure(value=0, maximum=1)
        self._run_status_var.set("Running…")
        self._counts_var.set("—")
        browser = "off"
        if use_browser:
            browser = f"on ({'headless' if job.headless else 'visible'} primary)"
        self._events.report_many(run_activity.run_started_events(
            novel=spec.novel_title, site=spec.display_name, start=start,
            end=None if end >= ALL_CHAPTERS else end,
            grouping=_grouping_text(mode, chunk), output_dir=output_dir,
            settings=(
                f"delay {delay:g} s, timeout {timeout} s, browser mode {browser}, "
                f"HTTP first {'on' if http_first else 'off'}, "
                f"HTML cache {'on' if job.use_cache else 'off'}."
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

    def _collect_params(self):
        """Validate the entry fields. Returns a tuple or None (after an error)."""
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
        try:
            report = pipeline.run_scrape(
                job,
                log=self._thread_log,
                cancel_event=self._cancel_event,
                progress_cb=progress,
            )
        except AdapterDisabledError as exc:
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
            except Exception as exc:  # noqa: BLE001 - the end of the run must still show
                events = [ActivityEvent(
                    run_activity.ORIGIN_RUN,
                    f"The run outcome could not be summarised: {type(exc).__name__}: {exc}",
                    ERROR, summary=True,
                )]
            for event in events:
                self._events.emit(event)
            self._events.emit(run_activity.finished_event(outcome))

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
        self._run_status_var.set("Stopping after the current chapter…")
        self._stop_btn.configure(state="disabled")

    def _on_run_finished(self) -> None:
        if self._closing:
            # The close poll destroys the window once the worker has exited; keep Start
            # locked rather than offering a new run in the meantime.
            self._running = False
            return
        self._set_running(False)
        self._run_status_var.set("Run ended.")
        # The run created its {name}-N folder; refresh the preview so the next run
        # shows the next free -N (still a sibling under the same parent).
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

    # ── Activity (Tk thread) ─────────────────────────────────────────────────
    def _on_job_event(self, event: ActivityEvent) -> None:
        """Progress and run-finished events, in order with the log lines around them."""
        payload = event.payload
        if isinstance(payload, run_activity.ProgressUpdate):
            self._set_progress(payload.done, payload.total)
        elif isinstance(payload, run_activity.RunFinished):
            self._on_run_finished()

    def _render_activity(self, update: RenderUpdate) -> None:
        """Draw an Activity update into the two read-only views. A view follows new
        lines while it is scrolled to the end; scrolled up, it stays where it is. Only
        a visible view can tell (a hidden Text has no real scroll position), so a
        hidden tab keeps what it was doing when last shown."""
        if self._shut_down:
            return
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

    def _set_progress(self, done: int, total: int) -> None:
        self._progress.configure(maximum=max(total, 1), value=done)
        self._counts_var.set(f"{done} / {total} chapters")

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
        self._events.close()  # refuses later events and closes the session log

    def destroy(self) -> None:
        """Every route that closes the window (idle close, the close poll, tests) comes
        through here, so the pump never fires on a destroyed window."""
        self._shutdown()
        super().destroy()


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
