"""app.py — single-window tkinter GUI entry point (Phase 6).

This is a *thin shell* over the already-tested package modules: the catalog
drives the dropdowns, and a Start press builds a :class:`ScrapeJob`, resolves
the output directory via :func:`pipeline.resolve_output_dir`, and hands the job
to :func:`pipeline.run_scrape` on a daemon thread. Stop sets a
``threading.Event`` the pipeline/request-manager honour between chapters.

No scraping, parsing, or PDF logic lives here — all of that is in the
``webnovel_scraper`` package and is covered by the offline test suite. The GUI
only collects inputs, marshals every UI update back onto the Tk thread via
``self.after(0, ...)``, and shows the log/progress the pipeline emits.

Defense in depth on disabled sites: the catalog greys them in the Site menu and
Start cannot dispatch one (this file), the pipeline refuses a disabled row
before building an adapter, and the stub adapter raises as a final backstop.
"""

from __future__ import annotations

import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

# When launched as ``python scripts/Universal/app.py`` the script's own
# directory (scripts/Universal) is sys.path[0], so the package import resolves.
# Add it explicitly too, so launching via an absolute path also works.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from webnovel_scraper import catalog, pipeline  # noqa: E402
from webnovel_scraper import request_manager as rm_module  # noqa: E402
from webnovel_scraper.models import OutputMode, ScrapeJob  # noqa: E402
from webnovel_scraper.registry import AdapterDisabledError  # noqa: E402
from webnovel_scraper.request_manager import RequestManager  # noqa: E402

# Adapters that can use the Playwright browser path (Cloudflare). Every other
# adapter is HTTP-only, so the browser checkboxes are inert when one of those is
# selected (e.g. webnovel_dynamic, which reads __NEXT_DATA__ over plain HTTP).
_BROWSER_CAPABLE_ADAPTERS = {"freewebnovel"}

# Inter-fetch delay (seconds) the user sets as the anti-detection / politeness
# rate-limit knob. 2.0s is a conservative default — high enough to look human,
# low enough not to crawl. The pipeline can auto-raise it further if the site
# starts blocking (adaptive auto-slowdown).
DEFAULT_DELAY = "2.0"
DEFAULT_TIMEOUT = "30"     # per-request timeout (matches request_manager default)
DEFAULT_CHUNK = "10"       # chapters per PDF in CHUNKED mode
# Headless browser ON by default — matches the README ("Leave Headless on unless
# you want to watch the browser work") so a non-technical user doesn't get a
# browser window popping up unexpectedly when they enable browser mode.
DEFAULT_HEADLESS = True
# "End = all": a sentinel far above any real chapter count. The pipeline clamps
# the requested range down to the available TOC, so this means "to the end".
ALL_CHAPTERS = 10 ** 9


class ScraperApp(tk.Tk):
    """The single scraper window. All real work happens on a daemon thread."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Web Novel Scraper")
        self.minsize(640, 620)

        # Run state (all mutated only on the Tk thread except cancel_event).
        self._worker: threading.Thread | None = None
        self._cancel_event: threading.Event | None = None
        self._running = False
        # Widgets that must be locked while a run is in progress.
        self._locked_inputs: list[tuple[tk.Widget, str]] = []

        # Novel slug list in catalog order; titles for display.
        self._novel_slugs = catalog.all_novel_slugs()

        self._build_ui()
        self._novel_combo.current(0)
        self._on_novel_change()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI construction ──────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        r = 0

        # Novel dropdown.
        ttk.Label(root, text="Novel:").grid(row=r, column=0, sticky="w", **pad)
        self._novel_combo = ttk.Combobox(
            root,
            state="readonly",
            values=[
                catalog.get_adapters_for_novel(slug)[0].novel_title
                for slug in self._novel_slugs
            ],
        )
        self._novel_combo.grid(row=r, column=1, columnspan=2, sticky="ew", **pad)
        self._novel_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self._on_novel_change()
        )
        r += 1

        # Site selector (a Menubutton + Menu so individual disabled sites can be
        # greyed and made non-selectable — a Combobox can't disable single items).
        ttk.Label(root, text="Site:").grid(row=r, column=0, sticky="w", **pad)
        self._site_key = tk.StringVar(value="")
        self._site_menubtn = ttk.Menubutton(root, text="(select a novel)")
        self._site_menu = tk.Menu(self._site_menubtn, tearoff=0)
        self._site_menubtn["menu"] = self._site_menu
        self._site_menubtn.grid(row=r, column=1, columnspan=2, sticky="ew", **pad)
        r += 1

        self._site_hint = ttk.Label(root, text="", foreground="#a05000")
        self._site_hint.grid(row=r, column=1, columnspan=2, sticky="w", padx=8)
        r += 1

        # Chapter range.
        ttk.Label(root, text="Start chapter:").grid(
            row=r, column=0, sticky="w", **pad
        )
        self._start_var = tk.StringVar(value="1")
        e_start = ttk.Entry(root, textvariable=self._start_var, width=12)
        e_start.grid(row=r, column=1, sticky="w", **pad)
        r += 1

        ttk.Label(root, text="End chapter:").grid(row=r, column=0, sticky="w", **pad)
        self._end_var = tk.StringVar(value="")
        e_end = ttk.Entry(root, textvariable=self._end_var, width=12)
        e_end.grid(row=r, column=1, sticky="w", **pad)
        ttk.Label(root, text="(blank = all chapters)").grid(
            row=r, column=2, sticky="w", **pad
        )
        r += 1

        # Delay + timeout. The delay is the user-facing rate-limit / anti-detection
        # knob (Phase 9A): higher = slower but less likely to be blocked.
        ttk.Label(root, text="Delay between fetches (seconds):").grid(
            row=r, column=0, sticky="w", **pad
        )
        self._delay_var = tk.StringVar(value=DEFAULT_DELAY)
        e_delay = ttk.Entry(root, textvariable=self._delay_var, width=12)
        e_delay.grid(row=r, column=1, sticky="w", **pad)
        ttk.Label(
            root,
            text="(anti-detection: higher = slower but less likely to be blocked)",
            foreground="#555555",
        ).grid(row=r, column=2, sticky="w", **pad)
        r += 1

        ttk.Label(root, text="Timeout (s):").grid(row=r, column=0, sticky="w", **pad)
        self._timeout_var = tk.StringVar(value=DEFAULT_TIMEOUT)
        e_timeout = ttk.Entry(root, textvariable=self._timeout_var, width=12)
        e_timeout.grid(row=r, column=1, sticky="w", **pad)
        r += 1

        # Output mode.
        ttk.Label(root, text="Output mode:").grid(
            row=r, column=0, sticky="nw", **pad
        )
        mode_frame = ttk.Frame(root)
        mode_frame.grid(row=r, column=1, columnspan=2, sticky="w", **pad)
        self._mode_var = tk.StringVar(value=OutputMode.SEPARATE.value)
        rb_sep = ttk.Radiobutton(
            mode_frame, text="Separate (one PDF per chapter)",
            value=OutputMode.SEPARATE.value, variable=self._mode_var,
            command=self._on_mode_change,
        )
        rb_sep.grid(row=0, column=0, sticky="w")
        rb_chunk = ttk.Radiobutton(
            mode_frame, text="Chunked", value=OutputMode.CHUNKED.value,
            variable=self._mode_var, command=self._on_mode_change,
        )
        rb_chunk.grid(row=1, column=0, sticky="w")
        ttk.Label(mode_frame, text="chapters per PDF:").grid(
            row=1, column=1, sticky="w", padx=(8, 2)
        )
        self._chunk_var = tk.StringVar(value=DEFAULT_CHUNK)
        self._chunk_entry = ttk.Entry(
            mode_frame, textvariable=self._chunk_var, width=8
        )
        self._chunk_entry.grid(row=1, column=2, sticky="w")
        rb_single = ttk.Radiobutton(
            mode_frame, text="Single (one PDF for the whole range)",
            value=OutputMode.SINGLE.value, variable=self._mode_var,
            command=self._on_mode_change,
        )
        rb_single.grid(row=2, column=0, sticky="w")
        r += 1

        # Browser mode + cache toggles.
        opt_frame = ttk.Frame(root)
        opt_frame.grid(row=r, column=0, columnspan=3, sticky="w", **pad)
        self._browser_var = tk.BooleanVar(value=False)
        self._browser_check = ttk.Checkbutton(
            opt_frame,
            text="Use Playwright browser mode (optional — forces the browser from the first request)",
            variable=self._browser_var,
            command=self._on_browser_toggle,
        )
        self._browser_check.grid(row=0, column=0, sticky="w")
        self._headless_var = tk.BooleanVar(value=DEFAULT_HEADLESS)
        self._headless_check = ttk.Checkbutton(
            opt_frame, text="Headless browser", variable=self._headless_var
        )
        self._headless_check.grid(row=1, column=0, sticky="w", padx=(20, 0))
        self._cache_var = tk.BooleanVar(value=True)
        self._cache_check = ttk.Checkbutton(
            opt_frame, text="Use HTML cache (resume re-runs)",
            variable=self._cache_var,
        )
        self._cache_check.grid(row=2, column=0, sticky="w")
        r += 1

        # Start / Stop.
        btn_frame = ttk.Frame(root)
        btn_frame.grid(row=r, column=0, columnspan=3, sticky="w", **pad)
        self._start_btn = ttk.Button(
            btn_frame, text="Start", command=self._on_start
        )
        self._start_btn.grid(row=0, column=0, padx=(0, 8))
        self._stop_btn = ttk.Button(
            btn_frame, text="Stop", command=self._on_stop, state="disabled"
        )
        self._stop_btn.grid(row=0, column=1)
        r += 1

        # Progress bar.
        self._progress = ttk.Progressbar(root, mode="determinate", maximum=1)
        self._progress.grid(row=r, column=0, columnspan=3, sticky="ew", **pad)
        r += 1

        # Log pane.
        root.rowconfigure(r, weight=1)
        self._log_text = scrolledtext.ScrolledText(
            root, height=14, wrap="word", state="disabled"
        )
        self._log_text.grid(row=r, column=0, columnspan=3, sticky="nsew", **pad)

        # Inputs locked during a run (state to restore is the normal state).
        self._locked_inputs = [
            (self._novel_combo, "readonly"),
            (self._site_menubtn, "normal"),
            (e_start, "normal"),
            (e_end, "normal"),
            (e_delay, "normal"),
            (e_timeout, "normal"),
            (rb_sep, "normal"),
            (rb_chunk, "normal"),
            (rb_single, "normal"),
            (self._cache_check, "normal"),
        ]

        self._on_mode_change()

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
        """Repopulate the Site menu for the newly selected novel."""
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

    def _on_site_change(self) -> None:
        spec = self._current_spec()
        if spec is not None:
            self._site_menubtn.configure(text=spec.display_name)
            self._site_hint.configure(text="")
        else:
            self._site_menubtn.configure(text="(no site available)")
            self._site_hint.configure(
                text="All sources for this novel are coming soon — nothing to run yet."
            )
        self._refresh_browser_state()
        self._refresh_start_state()

    def _on_mode_change(self) -> None:
        chunked = self._mode_var.get() == OutputMode.CHUNKED.value
        self._chunk_entry.configure(state="normal" if chunked else "disabled")

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
        headless_on = capable and self._browser_var.get()
        self._headless_check.configure(
            state="normal" if headless_on else "disabled"
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

        # Honour the timeout field: FETCH_TIMEOUT is read from module globals at
        # call time in RequestManager._get_text, so setting it here takes effect
        # without modifying the tested request-manager module.
        rm_module.FETCH_TIMEOUT = timeout

        # The browser toggle only applies to browser-capable adapters. Set it on
        # the resolved catalog row for this run (the pipeline re-resolves the same
        # instance). Reset every run so a prior selection never leaks.
        spec.use_browser = (
            self._browser_var.get()
            and spec.adapter_key in _BROWSER_CAPABLE_ADAPTERS
        )

        output_dir = pipeline.resolve_output_dir(spec.novel_slug)
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
        )

        rm = RequestManager(
            slug=spec.novel_slug,
            use_cache=self._cache_var.get(),
            headless=self._headless_var.get(),
            log_fn=self._thread_log,
            max_retries=job.max_retries,
            retry_base_delay=job.retry_base_delay,
        )
        self._cancel_event = threading.Event()

        self._set_running(True)
        self._progress.configure(value=0, maximum=1)
        self._append_log("─" * 50)
        self._append_log(
            f"Starting: {spec.novel_title} via {spec.display_name} "
            f"(chapters {start}-{'all' if end >= ALL_CHAPTERS else end}, "
            f"{mode.value}{', browser' if spec.use_browser else ''})"
        )
        self._append_log(f"Output: {output_dir}")

        self._worker = threading.Thread(
            target=self._run_worker, args=(job, rm), daemon=True
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

    def _run_worker(self, job: ScrapeJob, rm: RequestManager) -> None:
        """Daemon-thread body: drive the pipeline, then hand the RM back for
        teardown. Every UI touch goes through ``self.after`` via the callbacks."""
        try:
            pipeline.run_scrape(
                job,
                request_manager=rm,
                log=self._thread_log,
                cancel_event=self._cancel_event,
                progress_cb=self._thread_progress,
            )
        except AdapterDisabledError as exc:
            self._thread_log(f"Refused: {exc}")
        except Exception as exc:  # never let the worker thread die silently
            self._thread_log(f"ERROR: {exc}")
        finally:
            try:
                rm.close()
            except Exception:
                pass
            self.after(0, self._on_run_finished)

    def _on_stop(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
        self._append_log(
            "Stop requested — finishing the current chapter, then halting…"
        )
        self._stop_btn.configure(state="disabled")

    def _on_run_finished(self) -> None:
        self._append_log("— run ended —")
        self._set_running(False)

    # ── Thread-safe UI callbacks (called from the worker thread) ─────────────
    def _thread_log(self, msg: str) -> None:
        self.after(0, self._append_log, str(msg))

    def _thread_progress(self, done: int, total: int) -> None:
        self.after(0, self._set_progress, done, total)

    def _append_log(self, msg: str) -> None:
        self._log_text.configure(state="normal")
        self._log_text.insert("end", msg + "\n")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    def _set_progress(self, done: int, total: int) -> None:
        self._progress.configure(maximum=max(total, 1), value=done)

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
        if self._running:
            if not messagebox.askokcancel(
                "Quit",
                "A scrape is still running. Stop it and quit?",
            ):
                return
            if self._cancel_event is not None:
                self._cancel_event.set()
        self.destroy()


def main() -> None:
    """GUI entry point — launched by Setup_and_Run / the package."""
    app = ScraperApp()
    app.mainloop()


if __name__ == "__main__":
    main()
