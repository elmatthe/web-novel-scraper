"""TOC-first orchestration: discovery, caching, resume, output modes, run report.

The pipeline is the one place that ties the catalog, an adapter, the request
manager, and the PDF builder together. It never parses HTML and never lays out a
PDF itself — it drives the adapter (``build_chapter_index`` / ``fetch_chapter``)
and the PDF builder (``create_pdf``), and owns only the run policy:

  1. Resolve the ``SiteSpec`` from the catalog and **refuse a disabled row**
     before touching the adapter (defense in depth — the GUI greys disabled
     sites, this guard is the pipeline-layer refusal, and the stub adapter's
     ``NotImplementedError`` is the final backstop).
  2. Resolve the chapter list from the validated, source-specific chapter manifest
     (``chapter_preflight``): a fully covered finite range needs no TOC request, an
     uncovered window is discovered through the adapter and persisted, and an open-ended
     range re-checks the tail. A request the list cannot satisfy is refused, never clamped.
  3. Select the requested chapters by source page URL (never displayed number alone).
  4. Reserve (or, for Resume, reopen and verify) the run folder and its journal; skip
     outputs whose recorded checksums still match, fetch the rest. A single failed chapter
     is recorded and skipped — never fatal. The per-fetch ``delay`` and a ``cancel_event``
     (Stop button) are honoured between fetches.
  5. Publish PDF and/or UTF-8 TXT per the one grouping (individual / every X / whole
     range) from the same canonical chapter content (``content``, ``pdf_builder``,
     ``txt_builder``, ``run_output``).
  6. Return a :class:`RunReport` (written / skipped / failed counts + a resume
     hint for any failures).

Networking, parsing, and layout all live in their own modules; the pipeline is
pure orchestration so it can be driven in tests with a fake adapter and no
network.
"""

from __future__ import annotations

import collections
import hashlib
import inspect
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import catalog
from . import chapter_preflight
from .atomic_io import write_json_atomic
from . import pdf_builder
from . import rescue_pool as rp
from . import txt_builder
from .content_integrity import FailureInfo, OUTCOME_POLICY, classify_failure
from .adapters.base import BaseAdapter
from .chapter_preflight import ChapterListRefused
from .chapter_store import ChapterManifestStore
from .host_rate_limiter import HostRateLimiter
from .models import (
    ChapterContent,
    ChapterMeta,
    EmptyExtractionError,
    OutputMode,
    ScrapeJob,
    SiteSpec,
    runtime_site_spec,
)
from .file_lock import FileLock, LockBusy
from .registry import REGISTRY, AdapterDisabledError
from .run_journal import (
    LOCK_FILENAME,
    JournalError,
    RunHeader,
    RunJournal,
    load_selection,
    replay_journal,
    selection_sha256,
)
from .run_output import (
    RunOutputs,
    duplicate_suffix,
    novel_stem,
    remove_orphan_temps,
    separate_stem,
)
from .run_storage import RunStorageError, allocate_run_dir, open_existing_run
from .source_identity import SourceIdentity, SourceIdentityError
from .request_manager import (
    HOST_MIN_INTERVAL,
    ChallengeFetchError,
    FetchError,
    NotFoundFetchError,
    RateLimitedFetchError,
    RequestManager,
    ScrapeCancelled,
    TransientFetchError,
)

logger = logging.getLogger(__name__)

# Written to every run folder at the end of each session: page-by-page expected versus saved,
# with output files and checksums, and no chapter text (v0.3.0 03.3).
AUDIT_FILENAME = "run-audit.json"

# The pre-0.3.0 output-scoped chapter index. It is never written any more and never trusted
# directly: ``ChapterManifestStore.import_legacy_index`` validates it strictly and, only when it
# passes, copies its chapters into the runtime manifest. The file itself is left untouched.
INDEX_FILENAME = "chapter_index.json"

# ── 0.2.0 circuit-breaker thresholds (pipeline-owned, headless-only — §3.9) ───
# The breaker watches PRIMARY network results and, when headless mode is broadly
# blocked, switches the primary to a visible browser for the rest of the run.
# v0.3.0 04 (plan decision 6, §8 C9): the headless primary is a short, bounded PROBE. The first
# Cloudflare challenge it meets switches the run to a visible browser for the rest of the run (that
# chapter is retried there at once); a headless run is never retried chapter after chapter. Live
# 2026-10-18: headless Camoufox cleared freewebnovel.com, so most runs never need the switch. (0.2.0
# tripped only after 5 consecutive or 9 of 20 challenges, sending each one to rescue meanwhile.)
BREAKER_CONSECUTIVE_CHALLENGES = 1   # consecutive primary network challenges → trip
BREAKER_WINDOW = 20                  # rolling window of primary network fetches
BREAKER_WINDOW_CHALLENGES = 1        # ≥ this many challenges in the window → trip
# Bounded primary retry budget after a 429 host cooldown (§3.9 tail). Past this the
# chapter is recorded as a transient failure for a later resume rather than launching
# a browser against a rate-limited host.
RATE_LIMIT_RETRY_BUDGET = 2
# Thread-coordination poll while the final rescue drain joins the worker thread. This
# is REAL time (the worker makes real progress, so it cannot hang a fake-clock test —
# the same rationale as the pool's internal queue polling); it is NOT a logical wait.
_RESCUE_DRAIN_POLL = 0.1


class ChapterIndexUnavailable(RuntimeError):
    """The TOC/index could not be built even after the visible-primary fallback
    (§3.11). Raised by the rescue conductor so ``run_scrape`` aborts cleanly with a
    clear message rather than proceeding with no chapter list."""

# Type aliases for the injectable seams the GUI/tests use.
LogFn = Callable[[str], None]
ProgressFn = Callable[[int, int], None]

# Observability only (v0.3.0 Checkpoint 02.2):
# ``phase_cb(phase, total, already_present, from_cache)``. It tells a front end where the run
# is, which ``progress_cb(done, total)`` cannot, and changes nothing the pipeline does.
# ``total`` is the number of selected chapters (source pages, however many PDFs they make),
# ``already_present`` how many of them resume has skipped so far and ``from_cache`` how many
# of the processed ones the saved-page cache served (no request, no delay; a front end keeps
# both out of its time estimate). Calls: PHASE_SCRAPING once the selected chapters are known
# (before the first fetch) and again, before the tick that counts it, whenever resume skips
# more of them or the cache serves another; PHASE_BUILDING just before CHUNKED/SINGLE output
# is assembled (SEPARATE writes each PDF as its chapter lands, so it has no build step). A
# raising observer is ignored: it can never fail or alter a run.
PhaseFn = Callable[[str, int, int, int], None]
PHASE_SCRAPING = "scraping"
PHASE_BUILDING = "building"

# ── Adaptive auto-slowdown (Phase 9B) ────────────────────────────────────────
# When chapter fetches start hitting blocks/challenges, the pipeline raises the
# *inter-fetch* delay for the rest of the run so the whole crawl backs off and
# stays under the site's radar. This is distinct from (and on top of) the
# request-manager's per-attempt exponential backoff inside a single chapter's
# retry ladder — both coexist. The escalation multiplies the current delay, is
# bounded by a ceiling, and is logged.
AUTO_SLOWDOWN_MULTIPLIER = 1.5
AUTO_SLOWDOWN_CEILING = 30.0   # max inter-fetch delay (seconds) auto-slowdown reaches
AUTO_SLOWDOWN_FLOOR = 2.0      # the delay the first block jumps to when base is small


class _Pacer:
    """Tracks the adaptive inter-fetch delay for one run (Phase 9B).

    Starts at the user-set base delay and raises the *effective* delay each time a
    chapter fetch is classified as a block/challenge, capped at a ceiling. Sleeps
    the current effective delay between fetches. ``sleep_fn`` is injectable so
    tests never actually wait.
    """

    def __init__(
        self,
        base_delay: float,
        *,
        multiplier: float = AUTO_SLOWDOWN_MULTIPLIER,
        ceiling: float = AUTO_SLOWDOWN_CEILING,
        floor: float = AUTO_SLOWDOWN_FLOOR,
        log: Optional[LogFn] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base = max(0.0, float(base_delay))
        self.multiplier = float(multiplier)
        self.ceiling = float(ceiling)
        # The ceiling is an ABSOLUTE cap on the effective inter-fetch delay,
        # including a large user-supplied base delay — clamp it from the start so
        # the effective delay (and report.effective_delay) never exceeds it.
        self.current = min(self.base, self.ceiling)
        self.floor = float(floor)
        self._log = log if log is not None else logger.info
        self._sleep = sleep_fn
        self.slowdowns = 0

    def register_block(self) -> None:
        """Raise the effective delay one step after a block/challenge failure."""
        prev = self.current
        if self.current >= self.ceiling:
            return
        if self.current < self.floor:
            self.current = self.floor
        else:
            self.current = self.current * self.multiplier
        self.current = min(self.current, self.ceiling)
        if self.current > prev:
            self.slowdowns += 1
            self._log(
                f"auto-slowdown: inter-fetch delay raised to {self.current:.1f}s "
                f"after repeated challenges (ceiling {self.ceiling:.0f}s)."
            )

    def sleep(self) -> None:
        """Sleep the current effective inter-fetch delay (no-op when it is 0)."""
        if self.current > 0:
            self._sleep(self.current)


# ── Run report ───────────────────────────────────────────────────────────────
@dataclass
class RunReport:
    """The outcome of one scrape run, returned to the caller (GUI/test)."""

    output_dir: Path
    requested_range: tuple[int, int] = (0, 0)
    effective_range: tuple[int, int] = (0, 0)
    written: list[Path] = field(default_factory=list)        # output files written (every format)
    formats: tuple = ("pdf",)                                # the formats this run produces
    skipped_existing: list[int] = field(default_factory=list)  # resume-skipped ch.
    failed: list[int] = field(default_factory=list)          # ch. still failed after sweep
    permanent_failed: list[int] = field(default_factory=list)  # 403/404 — not swept
    extraction_failed: list[int] = field(default_factory=list)  # fetched OK but no body — not swept, not a block
    toc_gaps: list[int] = field(default_factory=list)          # whole numbers the site's list skips inside the range
    range_notes: list[str] = field(default_factory=list)       # how the requested range was adjusted (End clamp, Start raise)
    absent_skipped: list[tuple[str, str]] = field(default_factory=list)  # (label, evidence): source-confirmed absent, skipped
    rescued: list[int] = field(default_factory=list)         # ch. saved by rescue (or 2nd-pass sweep)
    warnings: list[str] = field(default_factory=list)        # adapter + pipeline warnings
    identity_failed: list = field(default_factory=list)
    accounting: dict = field(default_factory=dict)          # page positions, requested formats, actual artifacts
    cancelled: bool = False
    resumed: bool = False            # this session continued an earlier run folder (v0.3.0 03.3)
    from_cache: int = 0              # chapter pages served by the saved-page cache (no request made)
    auto_slowdowns: int = 0          # how many times auto-slowdown raised the delay
    effective_delay: float = 0.0     # final inter-fetch delay; never exceeds the ceiling
    # ── 0.2.0 rescue-lane metrics (§3.16) ─────────────────────────────────────
    # ``rescue_exhausted`` ⊆ ``failed`` (a hard chapter the rescue lane could not
    # clear after its whole escalating ladder). ``rescued ∩ failed = ∅`` is upheld
    # by the conductor (a rescued chapter is never also recorded failed). Cancelled
    # chapters are NOT counted as ``rescue_exhausted``.
    rescue_exhausted: list[int] = field(default_factory=list)
    rescue_queue_peak: int = 0
    rescue_jobs_submitted: int = 0
    rescue_jobs_completed: int = 0
    rescue_worker_failures: int = 0
    circuit_breaker_tripped: bool = False
    primary_switched_visible: bool = False
    # index -> the escalation mode (rescue strategy) that finally cleared the chapter.
    rescue_strategy: dict = field(default_factory=dict)

    def summary(self) -> str:
        """A short, human-readable run report (for the GUI log / CHANGELOG)."""
        requested_end = (
            "last" if self.requested_range[1] >= _UNBOUNDED_END_THRESHOLD else self.requested_range[1]
        )
        lines = [
            f"Run complete{' (cancelled)' if self.cancelled else ''}.",
            f"  Output: {self.output_dir}",
            f"  Range:  requested {self.requested_range[0]}-{requested_end}, "
            f"scraped {self.effective_range[0]}-{self.effective_range[1]}",
            *(f"  Range adjusted: {note}." for note in self.range_notes),
            f"  Written: {_written_text(self.written, self.formats)}",
            f"  Skipped (already present): {len(self.skipped_existing)}",
            f"  Failed:  {self.accounting.get('requested_failure', len(self.failed))}",
        ]
        if self.from_cache:
            lines.append(f"  From the saved-page cache: {self.from_cache} chapter page(s), no request "
                         "to the site")
        if self.rescued:
            lines.append(
                f"  Rescued (hard chapters cleared by the rescue lane / sweep): "
                f"{len(self.rescued)}"
            )
        if self.primary_switched_visible:
            lines.append(
                "  Primary browser was switched to VISIBLE mid-run "
                f"(circuit breaker tripped: {self.circuit_breaker_tripped})."
            )
        if self.rescue_worker_failures:
            lines.append(
                f"  Rescue worker failures: {self.rescue_worker_failures} "
                "(affected chapters recorded for resume)."
            )
        if self.extraction_failed:
            lines.append(
                "  Extraction failures (page fetched but no body found): "
                f"{len(self.extraction_failed)}"
            )
        if self.identity_failed:
            lines.append(f"  Identity failures (mismatch/unknown/conflict): {len(self.identity_failed)}")
        if self.accounting:
            lines.append(f"  Pages: {self.accounting['selected']} selected, {self.accounting['attempted']} attempted, "
                         f"{self.accounting['not_attempted']} not attempted; "
                         f"{self.accounting['requested_success']} requested-format successes, "
                         f"{self.accounting['fallback_preserved']} with fallback artifacts preserved.")
        if self.toc_gaps:
            preview = ", ".join(str(i) for i in self.toc_gaps[:20])
            more = " ..." if len(self.toc_gaps) > 20 else ""
            lines.append(
                "  TOC gaps (chapter numbers the site's chapter list skips): "
                f"{preview}{more}"
            )
        if self.absent_skipped:
            lines.append(
                "  Skipped (confirmed absent at the source): "
                f"{', '.join(label for label, _ in self.absent_skipped[:20])}"
            )
        if self.auto_slowdowns:
            lines.append(
                f"  Auto-slowdown raised the inter-fetch delay to "
                f"{self.effective_delay:.1f}s ({self.auto_slowdowns}x)."
            )
        if self.failed:
            preview = ", ".join(str(i) for i in self.failed[:20])
            more = " …" if len(self.failed) > 20 else ""
            lines.append(f"    failed chapters: {preview}{more}")
            lines.append(
                "    Tip: use Resume Existing Run on this run folder to retry only the "
                "missing chapters; files already saved and verified are skipped."
            )
        if self.warnings:
            lines.append(f"  Warnings: {len(self.warnings)}")
            for warning in self.warnings[:5]:
                lines.append(f"    warning: {warning}")
            if len(self.warnings) > 5:
                lines.append(f"    ... {len(self.warnings) - 5} more warning(s)")
        return "\n".join(lines)


def _written_text(written: list, formats: tuple) -> str:
    """``3 PDF(s)`` for a PDF-only run (the established wording), else per format."""
    actual = {Path(p).suffix.lower().lstrip(".") for p in written}
    if tuple(formats) == ("pdf",) and actual <= {"pdf"}:
        return f"{len(written)} PDF(s)"
    formats = tuple(dict.fromkeys((*formats, *sorted(actual))))
    counts = [f"{sum(1 for p in written if Path(p).suffix.lower() == '.' + f)} {f.upper()}"
              for f in formats]
    return " + ".join(counts) + " file(s)"


# ── Output-dir resolution ────────────────────────────────────────────────────
def resolve_output_dir(
    novel_slug: str,
    *,
    downloads_root: Optional[Path] = None,
    parent_dir: Optional[Path] = None,
    base_name: Optional[str] = None,
) -> Path:
    """Return the next free ``{base_name or slug}-N`` dir under the parent folder.

    Defaults (the user touches nothing): ``~/Downloads/{slug}-N`` — e.g.
    ``~/Downloads/shadow-slave-1`` — where ``N`` auto-increments to the first
    folder that does not already exist, so a fresh run never clobbers a previous
    one.

    The GUI can override either side without moving any logic out of the pipeline:
      * ``parent_dir`` — a custom parent folder the user browsed to (else
        ``downloads_root`` for tests, else ``~/Downloads``).
      * ``base_name`` — a custom folder name the user typed (sanitised for the
        filesystem; falls back to the slug when blank/empty after sanitising).

    The ``-N`` auto-increment is applied against the chosen ``parent + name`` too,
    so a custom run also never overwrites an existing folder of the same name. The
    path is **not** created here — the pipeline creates ``job.output_dir`` when it
    runs. Resolved via ``Path.home()`` so it is platform-neutral (never a
    hardcoded ``C:\\Users\\...``).
    """
    if parent_dir is not None:
        root = Path(parent_dir)
    elif downloads_root is not None:
        root = Path(downloads_root)
    else:
        root = Path.home() / "Downloads"

    name = (base_name or "").strip()
    if name:
        # Sanitise a user-typed name (strips path separators / illegal chars); a
        # name that sanitises away entirely falls back to the slug.
        name = BaseAdapter.safe_filename(name) or novel_slug
    else:
        name = novel_slug

    n = 1
    while (root / f"{name}-{n}").exists():
        n += 1
    return root / f"{name}-{n}"


def _legacy_limiter(job: ScrapeJob, monotonic_fn, sleep_fn) -> Optional[HostRateLimiter]:
    """Pace every top-level request of a standard-path run, chapter-list pages included (a
    paginated list such as freewebnovel.net's would otherwise be read back to back). Only when
    the clock pair is consistent: a caller that fakes ``sleep`` but not ``monotonic`` (tests)
    would make the limiter spin on the real clock, so it gets none, as before."""
    if (sleep_fn is time.sleep) != (monotonic_fn is time.monotonic):
        return None
    return HostRateLimiter(max(HOST_MIN_INTERVAL, job.delay), monotonic=monotonic_fn, sleep=sleep_fn)


def _site_key(spec: SiteSpec) -> Optional[str]:
    """The one host a run's HTML cache may hold pages for (``None`` if it cannot be told)."""
    try:
        return SourceIdentity.from_site_spec(spec).site_key
    except SourceIdentityError:
        return None


def default_manifest_store() -> ChapterManifestStore:
    """The chapter manifest store a run uses unless one is injected (tests patch this)."""
    return ChapterManifestStore()


def _record_selection(
    selection: "chapter_preflight.Selection", report: RunReport, log: LogFn
) -> None:
    """Copy what the preflight learned onto the run report, with the existing TOC-gap wording."""
    report.effective_range = selection.effective_range
    report.range_notes = list(selection.range_notes)
    report.absent_skipped = list(selection.absent)
    for label, evidence in selection.absent:
        log(f"NOTE: chapter {label} was skipped: confirmed absent at the source ({evidence}).")
    if selection.toc_gaps:
        gaps = selection.toc_gaps
        report.toc_gaps = gaps
        preview = ", ".join(str(i) for i in gaps[:20])
        more = " ..." if len(gaps) > 20 else ""
        warning = (
            "The site's chapter list skips chapter number(s) inside the requested range: "
            f"{preview}{more}. These chapters will not be fetched; they are not recorded as "
            "absent, so a later run checks the site again."
        )
        if warning not in report.warnings:
            report.warnings.append(warning)
        log(f"WARNING: {warning}")


# ── fix-chapter-number-offset: bounded-window TOC hints ──────────────────────
# An end this large is effectively "no upper bound" (app.py's ALL_CHAPTERS
# sentinel is 10**9; this threshold is comfortably below that and far above any
# real chapter count), so it is passed through as ``None`` — a real end_hint
# this large would otherwise make FreeWebNovelAdapter's binary search converge
# on the whole discovered slug range anyway, but only after a handful of wasted
# probe fetches; treating it as "no hint" up front skips that waste for free.
_UNBOUNDED_END_THRESHOLD = 10**6


def _end_hint_for(job: ScrapeJob) -> Optional[int]:
    return None if job.end >= _UNBOUNDED_END_THRESHOLD else job.end


def _build_chapter_index(
    adapter: object,
    spec: SiteSpec,
    *,
    fast_path: Optional[bool] = None,
    start_hint: Optional[int] = None,
    end_hint: Optional[int] = None,
    resume_metas: Optional[list[ChapterMeta]] = None,
    index_progress_cb: Optional[
        Callable[[list[ChapterMeta], int, int], None]
    ] = None,
) -> list[ChapterMeta]:
    """Call ``adapter.build_chapter_index``, passing ``fast_path``/``start_hint``/
    ``end_hint`` ONLY when the adapter's own method actually accepts them.

    Every non-FreeWebNovel adapter (and every test fake across the suite) still
    implements the plain ``BaseAdapter`` contract, ``(self, spec)`` — this keeps
    every one of those working completely unchanged. Only ``FreeWebNovelAdapter``
    declares ``start_hint``/``end_hint`` (fix-chapter-number-offset Phase 2), so
    only it receives the user's requested range — letting its offset-resolution
    binary search + walk jump near the request instead of scanning unbounded
    from slug 1 on a genuinely offset novel. Introspection, not a broad
    try/except, so a real ``TypeError`` raised from inside the call is never
    silently swallowed."""
    try:
        params = inspect.signature(adapter.build_chapter_index).parameters
    except (TypeError, ValueError):
        params = {}
    kwargs: dict = {}
    if fast_path is not None and "fast_path" in params:
        kwargs["fast_path"] = fast_path
    if "start_hint" in params:
        kwargs["start_hint"] = start_hint
    if "end_hint" in params:
        kwargs["end_hint"] = end_hint
    if "resume_metas" in params:
        kwargs["resume_metas"] = resume_metas
    if "index_progress_cb" in params:
        kwargs["index_progress_cb"] = index_progress_cb
    return adapter.build_chapter_index(spec, **kwargs)  # type: ignore[attr-defined]


# ── Filename helpers ─────────────────────────────────────────────────────────
def _stem_for(spec: SiteSpec) -> str:
    """A safe, space-free filename stem for grouped output (e.g. ``Shadow_Slave``), derived
    from the display title with the slug as fallback."""
    return novel_stem(spec.novel_title, spec.novel_slug)


def _separate_pdf_name(content: ChapterContent, meta: ChapterMeta | None = None,
                       occurrence: tuple[int, int] = (1, 1)) -> str:
    """The individual-chapter PDF filename (the naming rule ``run_output`` publishes with)."""
    suffix = duplicate_suffix(meta, *occurrence) if meta is not None else ""
    return separate_stem(content) + suffix + ".pdf"


def _record_duplicate_output_numbers(
    metas: list[ChapterMeta],
    report: RunReport,
    log: LogFn,
) -> None:
    counts: dict[int | float, int] = {}
    for meta in metas:
        counts[meta.index] = counts.get(meta.index, 0) + 1
    for index, count in counts.items():
        if count < 2:
            continue
        warning = (
            f"displayed chapter {index} occurs {count} times in the chapter list; "
            "later source pages use slug-suffixed filenames so every file remains distinct."
        )
        if warning not in report.warnings:
            report.warnings.append(warning)
            log(f"WARNING: {warning}")


def render_output(fmt: str, contents: list[ChapterContent], path: Path, title: str,
                  cancel_event: Optional[threading.Event] = None) -> None:
    """Render one output file atomically (temp in the same folder, fsync, replace). A PDF layout
    stops at the next chapter when ``cancel_event`` is set (``ScrapeCancelled``)."""
    if fmt == "pdf":
        pdf_builder.create_pdf(contents, path, title=title, cancel_event=cancel_event)
    elif fmt == "txt":
        txt_builder.create_txt(contents, path)
    else:
        raise ValueError(f"unsupported output format {fmt!r}")


# ── 0.2.0 fast-primary + single-lane rescue conductor (§3.9–3.16) ─────────────
def _rescue_enabled(job: ScrapeJob) -> bool:
    """The FWN-browser scope gate (§3.14): only FreeWebNovel browser runs use the
    fast-primary + background rescue conductor. Every other path (WND HTTP, the
    opt-in HTTP path, any injected-adapter test) keeps the legacy ``_drive`` flow
    with the synchronous second-pass sweep — unchanged."""
    return job.adapter_key == "freewebnovel" and job.use_browser


class _CircuitBreaker:
    """Pipeline-owned, headless-only circuit breaker (§3.9).

    Counts ONLY uncached primary *network* fetches. ``is_challenge`` records a
    ChallengeFetchError; any other network outcome (success / not-found /
    transient) is a non-challenge that resets the consecutive streak. Cache hits,
    resume-skips, and 429s never reach here. ``armed`` is True only when the run
    started headless — a visible-primary run has nowhere to escalate.
    """

    def __init__(self, *, armed: bool) -> None:
        self.armed = armed
        self.consecutive = 0
        self.window: "collections.deque[bool]" = collections.deque(maxlen=BREAKER_WINDOW)
        self.tripped = False

    def record_network(self, *, is_challenge: bool) -> None:
        if is_challenge:
            self.consecutive += 1
        else:
            self.consecutive = 0
        self.window.append(is_challenge)

    def should_trip(self) -> bool:
        if not self.armed or self.tripped:
            return False
        if self.consecutive >= BREAKER_CONSECUTIVE_CHALLENGES:
            return True
        if sum(self.window) >= BREAKER_WINDOW_CHALLENGES:
            return True
        return False


class _PrimaryEngine:
    """The pipeline-owned active primary manager + adapter pair (§3.10/§3.15).

    ``headless`` is fixed at construction, so a headless→visible switch *recreates*
    the manager (and the adapter bound to it) rather than flipping a live field. The
    pipeline owns this so it can replace it during the TOC fallback or the breaker
    switch; every replaced manager is closed EXACTLY ONCE on the pipeline thread,
    and the final active manager is closed in ``run_scrape``'s ``finally``.
    """

    def __init__(
        self,
        *,
        headless: bool,
        runtime_spec: SiteSpec,
        manager_factory: Callable[..., object],
        adapter_factory: Callable[[object], object],
        log: LogFn,
    ) -> None:
        self.headless = headless
        self.runtime_spec = runtime_spec
        self._mf = manager_factory
        self._af = adapter_factory
        self._log = log
        self.switched_visible = False
        self.managers_closed = 0  # for the "closed exactly once" assertion
        self.manager = self._mf(headless=headless)
        self.adapter = self._af(self.manager)

    def build_toc(
        self,
        *,
        start_hint: Optional[int] = None,
        end_hint: Optional[int] = None,
        resume_metas: Optional[list[ChapterMeta]] = None,
        index_progress_cb: Optional[
            Callable[[list[ChapterMeta], int, int], None]
        ] = None,
    ) -> list[ChapterMeta]:
        return _build_chapter_index(
            self.adapter,
            self.runtime_spec,
            fast_path=True,
            start_hint=start_hint,
            end_hint=end_hint,
            resume_metas=resume_metas,
            index_progress_cb=index_progress_cb,
        )

    def fetch(self, meta: ChapterMeta) -> ChapterContent:
        return self.adapter.fetch_chapter(meta, self.runtime_spec, fast_path=True)  # type: ignore[attr-defined]

    @property
    def last_fetch_info(self):
        return getattr(self.manager, "last_fetch_info", None)

    def switch_to_visible(self) -> None:
        """Close the headless primary, recreate it visible, latch (§3.10). The new
        manager inherits the same shared limiter + cancel_event via the factory."""
        self._close_current()
        self.manager = self._mf(headless=False)
        self.adapter = self._af(self.manager)
        self.headless = False
        self.switched_visible = True

    def _close_current(self) -> None:
        try:
            self.manager.close()  # type: ignore[attr-defined]
        except Exception as exc:  # teardown is best-effort
            self._log(f"  (primary manager close: {exc})")
        finally:
            self.managers_closed += 1

    def close(self) -> None:
        self._close_current()


class _RescueConductor:
    """Drives the fast primary pass + the single background rescue lane for one FWN
    browser run (§3.9–3.16). Owns the per-chapter routing (primary success / hard →
    rescue / terminal), the headless-only breaker, the 429 policy, the continuous +
    final rescue drain, and folding rescued content back into output in index order.
    The rescue pool is created LAZILY on the first hard chapter, so an easy run never
    instantiates a worker."""

    def __init__(
        self,
        *,
        job: ScrapeJob,
        spec: SiteSpec,
        runtime_spec: SiteSpec,
        primary: _PrimaryEngine,
        breaker: _CircuitBreaker,
        pacer: _Pacer,
        limiter: HostRateLimiter,
        pool_factory: Callable[[], object],
        report: RunReport,
        log: LogFn,
        run_cancel: threading.Event,
        state: "_Progress",
        outputs: RunOutputs,
    ) -> None:
        self.job = job
        self.spec = spec
        self.runtime_spec = runtime_spec
        self.primary = primary
        self.breaker = breaker
        self.pacer = pacer
        self.limiter = limiter
        self._pool_factory = pool_factory
        self.report = report
        self.log = log
        self.run_cancel = run_cancel
        self.state = state
        self.outputs = outputs
        # Pages (by URL) currently recorded as failed: two pages can share a displayed number, so
        # clearing one page's failure must not clear its twin's (report lists are by number).
        self._failed_pages: dict[str, int | float] = {}
        self.pool: Optional[object] = None

    # ── pool lifecycle (lazy) ─────────────────────────────────────────────────
    def _ensure_pool(self) -> object:
        if self.pool is None:
            self.pool = self._pool_factory()
            self.pool.start()  # type: ignore[attr-defined]
        return self.pool

    def drain(self) -> None:
        """Non-blocking: terminalize every rescue result emitted so far (§3.12)."""
        if self.pool is None:
            return
        for res in self.pool.poll_results():  # type: ignore[attr-defined]
            self._terminalize_rescue(res)

    def final_drain(self, *, cancelled: bool) -> None:
        """Block until the worker exits, terminalizing every accepted job exactly
        once (graceful ``finish`` or prompt ``cancel``). Thread-coordination polling
        only — the worker makes real progress, so this cannot hang a fake clock."""
        if self.pool is None:
            return
        if cancelled:
            self.pool.cancel()  # type: ignore[attr-defined]
        else:
            self.pool.finish()  # type: ignore[attr-defined]
        thread = getattr(self.pool, "_thread", None)
        while thread is not None and thread.is_alive():
            self.pool.join(_RESCUE_DRAIN_POLL)  # type: ignore[attr-defined]
            self.drain()
        self.drain()  # sweep anything emitted just before the worker exited

    # ── primary classification ────────────────────────────────────────────────
    def _classify_primary(self, meta: ChapterMeta):
        """Return ``(kind, payload)`` for one fast-primary fetch. ``ScrapeCancelled``
        is allowed to propagate so the run ends cleanly."""
        self.outputs.attempt(meta)
        try:
            content = self.primary.fetch(meta)
        except ScrapeCancelled:
            raise
        except NotFoundFetchError as exc:
            return "not_found", exc
        except RateLimitedFetchError as exc:
            return "rate_limited", exc
        except EmptyExtractionError as exc:
            return "extraction" if exc.reason_code == "EMPTY_BODY" else "identity", exc
        except ChallengeFetchError as exc:
            return "challenge", exc
        except TransientFetchError as exc:
            return "transient", exc
        except FetchError as exc:
            # A bare FetchError (no typed subclass) — treat as transient: rescue it,
            # but do NOT count it as a breaker challenge.
            return "transient", exc
        except Exception as exc:  # an unexpected adapter/parse error — record + skip
            return "error", exc
        return "success", content

    def _from_cache(self) -> bool:
        fi = self.primary.last_fetch_info
        return bool(getattr(fi, "from_cache", False))

    # ── per-chapter routing ───────────────────────────────────────────────────
    def handle(self, meta: ChapterMeta, *, allow_breaker: bool) -> None:
        kind, payload = self._classify_primary(meta)
        if kind != "success":
            self.outputs.defer_failure(meta, payload)

        if kind == "success":
            if self._from_cache():
                self.report.from_cache += 1
            else:
                self.breaker.record_network(is_challenge=False)
            self._store_content(meta, payload)
            return
        if kind == "not_found":
            self.breaker.record_network(is_challenge=False)
            self._fail(meta, permanent=True, reason=classify_failure(payload))
            self.log(f"  chapter {meta.index} URL not found; displayed chapter absence is unproven.")
            return
        if kind in ("extraction", "identity"):
            self.breaker.record_network(is_challenge=False)
            self._fail(meta, extraction=kind == "extraction", reason=classify_failure(payload))
            if kind == "extraction":
                self.log(f"  chapter {meta.index} fetched but had no extractable body ({payload}).")
            return
        if kind == "error":
            # Not a fetch-layer signal — record a plain failure (resumeable), no rescue.
            self._fail(meta, reason=classify_failure(payload))
            self.log(f"  chapter {meta.index} FAILED: {payload}")
            return
        if kind == "rate_limited":
            self._handle_rate_limited(meta)
            return

        # ── hard chapter: challenge or transient ──────────────────────────────
        if kind == "challenge":
            self.breaker.record_network(is_challenge=True)
            if (
                allow_breaker
                and self.primary.headless
                and not self.primary.switched_visible
                and self.breaker.should_trip()
            ):
                self._trip_breaker_and_retry(meta)
                return
        else:  # transient
            self.breaker.record_network(is_challenge=False)

        # Auto-slowdown: raise the inter-fetch delay AND the shared limiter interval
        # so the rescue lane paces with the primary (§3.4 tail / §3.16). The
        # concurrent path does not separately sleep — the next nav waits the limiter.
        self.pacer.register_block()
        self.limiter.raise_interval(
            max(HOST_MIN_INTERVAL, self.job.delay, self.pacer.current)
        )
        self._submit_rescue(meta, kind)

    def _trip_breaker_and_retry(self, meta: ChapterMeta) -> None:
        """Breaker trip (§3.9): do NOT queue the triggering chapter; recreate the
        primary visible + latch (§3.10); retry that chapter SYNCHRONOUSLY on the new
        visible primary; continue the rest of the range visibly."""
        self.breaker.tripped = True
        self.report.circuit_breaker_tripped = True
        self.log(
            "  primary mode broadly blocked — switching to a visible browser for "
            "the rest of the run."
        )
        self.primary.switch_to_visible()
        self.report.primary_switched_visible = True
        # Retry the triggering chapter synchronously on the visible primary; the
        # breaker no longer applies (already switched).
        self.handle(meta, allow_breaker=False)

    def _handle_rate_limited(self, meta: ChapterMeta) -> None:
        """429 policy (§3.9 tail): the manager already parked the host on the shared
        limiter. Retry on the PRIMARY within a bounded budget (NOT browser rescue,
        NOT a breaker count); the limiter wait inside each nav serves the cooldown.
        Past the budget, record a transient failure for a later resume."""
        self.log(
            f"  chapter {meta.index} rate-limited (429); host cooldown applied — "
            "retrying on the primary path (not escalating to a browser)."
        )
        for _ in range(RATE_LIMIT_RETRY_BUDGET):
            if self.run_cancel.is_set():
                raise ScrapeCancelled("cancelled during rate-limit retry")
            kind, payload = self._classify_primary(meta)
            if kind != "success":
                self.outputs.defer_failure(meta, payload)
            if kind == "success":
                if self._from_cache():
                    self.report.from_cache += 1
                else:
                    self.breaker.record_network(is_challenge=False)
                self._store_content(meta, payload)
                return
            if kind == "rate_limited":
                continue  # still limited — the next nav waits the (re-applied) cooldown
            if kind == "not_found":
                self.breaker.record_network(is_challenge=False)
                self._fail(meta, permanent=True, reason=classify_failure(payload))
                return
            if kind in ("extraction", "identity"):
                self.breaker.record_network(is_challenge=False)
                self._fail(meta, extraction=kind == "extraction", reason=classify_failure(payload))
                return
            if kind == "error":
                self._fail(meta, reason=classify_failure(payload))
                return
            # A challenge/transient surfaced once the rate limit cleared → rescue it.
            if kind == "challenge":
                self.breaker.record_network(is_challenge=True)
            else:
                self.breaker.record_network(is_challenge=False)
            self.pacer.register_block()
            self.limiter.raise_interval(
                max(HOST_MIN_INTERVAL, self.job.delay, self.pacer.current)
            )
            self._submit_rescue(meta, kind)
            return
        self.log(
            f"  chapter {meta.index} still rate-limited after {RATE_LIMIT_RETRY_BUDGET} "
            "retries; recording a transient failure for resume."
        )
        self._fail(meta, reason=classify_failure(payload))

    def _submit_rescue(self, meta: ChapterMeta, kind: str) -> None:
        pool = self._ensure_pool()
        accepted = pool.submit(meta)  # type: ignore[attr-defined]
        if accepted:
            self.log(f"  chapter {meta.index} → rescue ({kind}).")
        elif self.run_cancel.is_set():
            # Refused because the run was cancelled — the job was never accepted, so
            # no rescue result will arrive; end the run (loop guard sets cancelled).
            raise ScrapeCancelled("cancelled while submitting to rescue")
        # Otherwise it was a duplicate already owned by rescue — its result will come.

    # ── terminal helpers ──────────────────────────────────────────────────────
    def _store_content(self, meta: ChapterMeta, content: ChapterContent) -> None:
        index = meta.index
        self._failed_pages.pop(meta.url, None)
        if index in self.report.failed and index not in self._failed_pages.values():
            self.report.failed.remove(index)  # a prior transient now cleared
        self.outputs.add(meta, content)  # individual chapters are published at once
        self.state.tick()

    def _mark_failed(self, meta: ChapterMeta, reason: str) -> None:
        self._failed_pages[meta.url] = meta.index
        if meta.index not in self.report.failed:
            self.report.failed.append(meta.index)
        self.outputs.fail(meta, reason)

    def _fail(
        self, meta: ChapterMeta, *, permanent: bool = False, extraction: bool = False,
        reason: str | FailureInfo = "failed",
    ) -> None:
        index = meta.index
        info = classify_failure(reason)
        self.log(f"  chapter {chapter_preflight.meta_label(meta)} FAILED [{info.reason_code}] at {meta.url}: {info.reason}")
        if info.reason_code.startswith("IDENTITY_") and index not in self.report.identity_failed:
            self.report.identity_failed.append(index)
        self._mark_failed(meta, reason)
        if permanent and index not in self.report.permanent_failed:
            self.report.permanent_failed.append(index)
        if extraction and index not in self.report.extraction_failed:
            self.report.extraction_failed.append(index)
        self.state.tick()

    def _terminalize_rescue(self, res) -> None:
        """Fold one immutable RescueResult into the report on the pipeline thread,
        ticking the chapter once at its terminal state (§3.12/§3.13/§3.16)."""
        idx = res.meta.index
        if res.status == rp.RESCUED:
            if idx not in self.report.rescued:
                self.report.rescued.append(idx)
            self.report.rescue_strategy[idx] = res.strategy
            self._store_content(res.meta, res.content)  # ticks + writes SEPARATE
            self.log(f"  chapter {idx} rescued ({res.strategy}).")
        elif res.status == rp.RESCUE_EXHAUSTED:
            self._mark_failed(res.meta, res.failure or FailureInfo("RESCUE_ERROR", res.error or "failed after every recovery method"))
            if idx not in self.report.rescue_exhausted:
                self.report.rescue_exhausted.append(idx)
            self.state.tick()
            self.log(f"  chapter {idx} failed after all methods.")
        elif res.status == rp.NOT_FOUND:
            self._fail(res.meta, permanent=True, reason=res.failure or FailureInfo("URL_NOT_FOUND", res.error or "URL not found (via rescue)"))
            self.log(f"  chapter {idx} URL not found via rescue; displayed chapter absence is unproven.")
        elif res.status in (rp.EXTRACTION_FAILED, rp.IDENTITY_FAILED):
            self._fail(res.meta, extraction=res.status == rp.EXTRACTION_FAILED,
                       reason=res.failure or FailureInfo("EMPTY_BODY" if res.status == rp.EXTRACTION_FAILED else "IDENTITY_MISMATCH", res.error or "extraction failed"))
            if res.status == rp.EXTRACTION_FAILED:
                self.log(f"  chapter {idx} fetched but had no extractable body (via rescue).")
        elif res.status == rp.POOL_FAILED:
            self._mark_failed(res.meta, res.failure or FailureInfo("RESCUE_ERROR", f"rescue pool error: {res.error}"))
            self.state.tick()
            self.log(f"  chapter {idx} failed (rescue pool error: {res.error}).")
        elif res.status == rp.CANCELLED:
            # Cancelled jobs are NOT rescue_exhausted and NOT failed — the run is
            # ending; the chapter is simply left for a resume. Do not tick.
            self.report.cancelled = True

    # ── metrics rollup (§3.16) ────────────────────────────────────────────────
    def record_metrics(self) -> None:
        if self.pool is None:
            return
        self.report.rescue_jobs_submitted = self.pool.jobs_submitted  # type: ignore[attr-defined]
        self.report.rescue_jobs_completed = self.pool.jobs_completed  # type: ignore[attr-defined]
        self.report.rescue_queue_peak = self.pool.queue_peak  # type: ignore[attr-defined]
        self.report.rescue_worker_failures = 1 if self.pool.worker_failed else 0  # type: ignore[attr-defined]


# ── Pipeline ─────────────────────────────────────────────────────────────────
def run_scrape(
    job: ScrapeJob,
    *,
    adapter: Optional[object] = None,
    request_manager: Optional[RequestManager] = None,
    log: Optional[LogFn] = None,
    cancel_event: Optional[threading.Event] = None,
    progress_cb: Optional[ProgressFn] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
    host_limiter: Optional[HostRateLimiter] = None,
    request_manager_factory: Optional[Callable[..., object]] = None,
    primary_adapter_factory: Optional[Callable[[object], object]] = None,
    rescue_pool_factory: Optional[Callable[[], object]] = None,
    phase_cb: Optional[PhaseFn] = None,
    manifest_store: Optional[ChapterManifestStore] = None,
) -> RunReport:
    """Run one scrape job and return a :class:`RunReport`.

    Args:
        job: the configured run (novel/site, range, delay, output mode, dir).
        adapter: inject an adapter (tests pass a fake); when None it is built
            from the registry with ``request_manager`` wired in.
        request_manager: inject a configured manager (the GUI passes one set up
            for browser/cache/cancel). Only used when ``adapter`` is built here;
            an injected adapter owns its own manager.
        log: ``(str) -> None`` sink for progress lines (defaults to the logger).
        cancel_event: the Stop-button event; checked between chapters/groups.
        progress_cb: ``(done, total) -> None`` for a determinate progress bar.
        phase_cb: optional ``(phase, total, already_present) -> None`` observer (see
            :data:`PhaseFn`); instrumentation only, it changes no behaviour.
        manifest_store: the chapter manifest store (tests inject one); defaults to
            :func:`default_manifest_store`.
        sleep_fn / monotonic_fn: the single injected timing source (the limiter and
            rescue worker both pace off these; tests pass a fake clock).
        host_limiter: the shared per-host limiter (§3.4); built from ``job.delay``
            when None. On the FWN-rescue path it is shared by primary + rescue.
        request_manager_factory / primary_adapter_factory / rescue_pool_factory:
            deterministic injection seams for the FWN-rescue conductor (§3.15) —
            ``request_manager_factory(headless=…) -> manager``,
            ``primary_adapter_factory(manager) -> adapter``, and a zero-arg pool
            factory. All default to real, shared-limiter implementations.

    Raises:
        AdapterDisabledError: if the resolved catalog row is disabled — the
            pipeline refuses before the adapter is built or called.
        ChapterListRefused: if the requested range cannot be provided from a verified
            chapter list (or the list is being updated by another run).
        ChapterIndexUnavailable: on the FWN-rescue path, if the TOC cannot be built
            even after the visible-primary fallback (§3.11).
    """
    log = log if log is not None else logger.info
    store = manifest_store if manifest_store is not None else default_manifest_store()

    # 1. Resolve the catalog row and refuse a disabled site BEFORE the adapter is
    #    built or touched (pipeline-layer defense in depth).
    spec = catalog.get_spec(job.novel_slug, job.adapter_key)
    if not spec.enabled:
        raise AdapterDisabledError(
            f"Site {spec.adapter_key!r} for novel {spec.novel_slug!r} is disabled "
            "(not yet available in this release); the pipeline will not run it."
        )

    # The run folder is created (or reserved, or reopened for a resume) only once the chapter
    # list is settled, so a refused run leaves nothing behind (v0.3.0 03.3).
    report = RunReport(
        output_dir=Path(job.output_dir),
        requested_range=(job.start, job.end),
        effective_range=(job.start, job.end),
        formats=tuple(job.formats),
    )

    # 2a. FWN-browser scope gate (§3.14): the fast-primary + single-lane rescue
    #     conductor. Only when no adapter was injected (an injected adapter keeps the
    #     legacy flow for back-compat / tests). The pipeline OWNS the primary manager
    #     here, building it via the factory so it can replace it on a TOC fallback or
    #     a breaker switch (§3.10/§3.15).
    if _rescue_enabled(job) and adapter is None:
        return _run_with_rescue(
            job, spec, report, log, cancel_event, progress_cb,
            phase_cb=phase_cb, sleep_fn=sleep_fn, monotonic_fn=monotonic_fn, host_limiter=host_limiter,
            store=store,
            request_manager_factory=request_manager_factory,
            primary_adapter_factory=primary_adapter_factory,
            rescue_pool_factory=rescue_pool_factory,
        )

    # 2b. Legacy path (WND HTTP, opt-in HTTP, injected-adapter tests): build (or
    #     accept) the adapter + request manager, drive with the synchronous sweep.
    owns_rm = False
    rm: Optional[RequestManager] = None
    drive_spec = spec
    if adapter is None:
        # The job — not a mutated catalog row — drives ``use_browser`` for this run.
        # Since the Phase-4 GUI no longer mutates ``SiteSpec`` or pre-builds the
        # manager, derive a per-run spec here and thread the job's timeout/headless
        # into the manager we own (§3.14/§3.15), so an FWN run with the browser
        # box unchecked correctly fetches over plain HTTP.
        drive_spec = runtime_site_spec(spec, job)
        rm = request_manager
        if rm is None:
            rm = RequestManager(
                slug=spec.novel_slug,
                use_cache=job.use_cache,
                headless=job.headless,
                try_http_first=job.http_first,
                log_fn=log,
                max_retries=job.max_retries,
                retry_base_delay=job.retry_base_delay,
                http_timeout=job.request_timeout,
                # Thread the run's injected timing source into the legacy ladder so
                # its cancel-aware sliced backoff (BUG-2) is driven by the same
                # (fake-in-tests) clock the rescue path already uses — instead of
                # falling through to real time.sleep.
                sleep_fn=sleep_fn,
                site_key=_site_key(spec),
                refresh_cache=job.refresh_html_cache,
                host_limiter=host_limiter or _legacy_limiter(job, monotonic_fn, sleep_fn),
            )
            owns_rm = True
        if cancel_event is not None:
            rm.cancel_event = cancel_event
        rm.start()
        cls = REGISTRY[spec.adapter_key]
        adapter = cls(request_manager=rm, log=log)
    elif request_manager is not None and cancel_event is not None:
        # Injected adapter + manager: still wire the Stop event.
        request_manager.cancel_event = cancel_event

    pacer = _Pacer(job.delay, log=log, sleep_fn=sleep_fn)
    try:
        return _drive(
            job, drive_spec, adapter, report, log, cancel_event,
            progress_cb, pacer, phase_cb, store=store,
        )
    except ScrapeCancelled as exc:
        # Cancelled while the chapter list was being settled (nothing was started yet).
        log(f"Run cancelled: {exc}")
        report.cancelled = True
        _collect_warnings(adapter, report)
        return report
    finally:
        if owns_rm and rm is not None:
            rm.close()


def _run_with_rescue(
    job: ScrapeJob,
    spec: SiteSpec,
    report: RunReport,
    log: LogFn,
    cancel_event: Optional[threading.Event],
    progress_cb: Optional[ProgressFn],
    *,
    phase_cb: Optional[PhaseFn] = None,
    store: ChapterManifestStore,
    sleep_fn: Callable[[float], None],
    monotonic_fn: Callable[[], float],
    host_limiter: Optional[HostRateLimiter],
    request_manager_factory: Optional[Callable[..., object]],
    primary_adapter_factory: Optional[Callable[[object], object]],
    rescue_pool_factory: Optional[Callable[[], object]],
) -> RunReport:
    """The FWN-browser fast-primary + single-lane rescue conductor (§3.9–3.16)."""
    runtime_spec = runtime_site_spec(spec, job)
    # One shared cancel signal for the primary, the limiter, and the rescue worker.
    run_cancel = cancel_event if cancel_event is not None else threading.Event()

    # One shared, fair per-host limiter (§3.4) — primary AND rescue pace through it.
    limiter = host_limiter if host_limiter is not None else HostRateLimiter(
        max(HOST_MIN_INTERVAL, job.delay),
        monotonic=monotonic_fn,
        sleep=sleep_fn,
    )

    # Default real factories (deterministic injection seams override them, §3.15).
    def _default_manager_factory(*, headless: bool) -> object:
        rm = RequestManager(
            slug=spec.novel_slug,
            use_cache=job.use_cache,
            headless=headless,
            try_http_first=job.http_first,
            log_fn=log,
            max_retries=job.max_retries,
            retry_base_delay=job.retry_base_delay,
            sleep_fn=sleep_fn,
            http_timeout=job.request_timeout,
            host_limiter=limiter,
            site_key=_site_key(spec),
            refresh_cache=job.refresh_html_cache,
        )
        rm.cancel_event = run_cancel
        rm.start()
        return rm

    def _default_primary_adapter_factory(manager: object) -> object:
        return REGISTRY[spec.adapter_key](request_manager=manager, log=log)

    def _default_pool_factory() -> object:
        return rp.RescuePool(
            primary_headless=job.headless,
            slug=spec.novel_slug,
            runtime_spec=runtime_spec,
            host_limiter=limiter,
            cancel_event=run_cancel,
            # Rescue always fetches from the site; a rescued page that extracts as the right
            # chapter is saved to the validated cache like any other (v0.3.0 03.2b).
            use_cache=job.use_cache,
            site_key=_site_key(spec),
            request_timeout=job.request_timeout,
            log=log,
            monotonic=monotonic_fn,
            sleep=sleep_fn,
        )

    manager_factory = request_manager_factory or _default_manager_factory
    primary_adapter_factory = primary_adapter_factory or _default_primary_adapter_factory
    pool_factory = rescue_pool_factory or _default_pool_factory

    pacer = _Pacer(job.delay, log=log, sleep_fn=sleep_fn)
    primary = _PrimaryEngine(
        headless=job.headless,
        runtime_spec=runtime_spec,
        manager_factory=manager_factory,
        adapter_factory=primary_adapter_factory,
        log=log,
    )
    breaker = _CircuitBreaker(armed=job.headless)

    conductor: Optional[_RescueConductor] = None
    try:
        # 3. Chapter list (§3.11 + v0.3.0 03.2a): the validated manifest decides what is
        #    discovered; the TOC bootstrap (with its headless -> visible fallback) is only
        #    the discovery step for a window the manifest does not cover.
        def discover(**window) -> list[ChapterMeta]:
            return _bootstrap_toc(primary, spec, report, log, run_cancel, **window)

        try:
            run = _prepare_run(
                job, spec, store, discover, report, log, run_cancel, progress_cb, phase_cb
            )
        except ScrapeCancelled as exc:
            log(f"Run cancelled: {exc}")
            report.cancelled = True
            _collect_warnings(primary.adapter, report)
            return report
        if run is None:
            _collect_warnings(primary.adapter, report)
            return report

        # 4. Drive the fast primary pass + rescue lane; outputs are published as groups fill.
        conductor = _RescueConductor(
            job=job, spec=spec, runtime_spec=runtime_spec, primary=primary,
            breaker=breaker, pacer=pacer, limiter=limiter, pool_factory=pool_factory,
            report=report, log=log, run_cancel=run_cancel, state=run.state,
            outputs=run.outputs,
        )
        try:
            try:
                for meta in run.fetch_list:
                    if _cancelled(run_cancel):
                        report.cancelled = True
                        break
                    conductor.drain()             # continuous result processing (§3.12)
                    conductor.handle(meta, allow_breaker=True)
            except ScrapeCancelled as exc:
                log(f"Run cancelled: {exc}")
                report.cancelled = True

            # 5. Final blocking drain (§3.12): every accepted job terminalizes exactly once.
            conductor.final_drain(cancelled=report.cancelled or _cancelled(run_cancel))
            conductor.record_metrics()
            if _cancelled(run_cancel):
                report.cancelled = True

            # 6. Publish the grouped files (rescued content folded in, in selection order).
            if not report.cancelled and job.output_mode is not OutputMode.SEPARATE:
                run.state.phase(PHASE_BUILDING)
            _finish_run(run, report, log)
        except BaseException:
            _abort_run(run, log)
            raise

        report.auto_slowdowns = pacer.slowdowns
        report.effective_delay = pacer.current
        _collect_warnings(primary.adapter, report)
        log(report.summary())
        return report
    finally:
        # The final active primary manager closes here (every replaced one was closed
        # exactly once on this thread inside ``switch_to_visible``). The pool is
        # always joined by ``final_drain`` above; close it defensively on any abort.
        if conductor is not None and conductor.pool is not None:
            try:
                conductor.pool.close()  # type: ignore[attr-defined]
            except Exception:
                pass
        primary.close()


def _bootstrap_toc(
    primary: _PrimaryEngine,
    spec: SiteSpec,
    report: RunReport,
    log: LogFn,
    run_cancel: threading.Event,
    *,
    start_hint: Optional[int] = None,
    end_hint: Optional[int] = None,
    resume_metas: Optional[list[ChapterMeta]] = None,
    index_progress_cb: Optional[
        Callable[[list[ChapterMeta], int, int], None]
    ] = None,
) -> list[ChapterMeta]:
    """Discover one window of the TOC on the primary mode; on a headless Cloudflare block,
    switch the primary visible and retry ONCE; if that still fails, abort cleanly (§3.11). A
    TOC request is NEVER handed to chapter rescue (it has no ``ChapterMeta``).

    The window's ``[start_hint, end_hint]`` is passed through as bounded-window hints
    (fix-chapter-number-offset Phase 3) so a genuinely offset FreeWebNovel novel's binary
    search + reconciliation walk jumps near the requested range instead of scanning unbounded
    from slug 1 — a no-op for every identity novel and for every non-FWN adapter
    (``_build_chapter_index`` only forwards the hints when the adapter's own method declares
    them). Which windows are needed is decided by ``chapter_preflight``, not here."""
    try:
        return primary.build_toc(
            start_hint=start_hint,
            end_hint=end_hint,
            resume_metas=resume_metas,
            index_progress_cb=index_progress_cb,
        )
    except ScrapeCancelled:
        raise
    except ChallengeFetchError as exc:
        if primary.headless and not primary.switched_visible:
            log(
                "  TOC blocked by Cloudflare while headless — switching to a visible "
                "browser and retrying the index once."
            )
            primary.switch_to_visible()
            report.primary_switched_visible = True
            try:
                return primary.build_toc(
                    start_hint=start_hint,
                    end_hint=end_hint,
                    resume_metas=resume_metas,
                    index_progress_cb=index_progress_cb,
                )
            except ScrapeCancelled:
                raise
            except Exception as exc2:
                raise ChapterIndexUnavailable(
                    f"could not build chapter index for {spec.novel_slug!r}: "
                    f"the visible-primary retry also failed ({exc2})."
                ) from exc2
        raise ChapterIndexUnavailable(
            f"could not build chapter index for {spec.novel_slug!r}: the primary is "
            f"already visible and is still blocked ({exc})."
        ) from exc
    except Exception as exc:
        # A non-challenge TOC failure (e.g. parse error) is not rescuable either.
        raise ChapterIndexUnavailable(
            f"could not build chapter index for {spec.novel_slug!r}: {exc}."
        ) from exc


@dataclass
class _Run:
    """One run folder in use: its journal, its outputs, the pages still to fetch, its lock."""

    run_dir: Path
    journal: RunJournal
    outputs: RunOutputs
    fetch_list: list
    state: "_Progress"
    lock: FileLock


def _lock_run(run_dir: Path) -> FileLock:
    lock = FileLock(run_dir / LOCK_FILENAME)
    try:
        lock.acquire()
    except LockBusy:
        raise RunStorageError(
            f"The run folder {run_dir.name} is already being used by another run or window. "
            "Nothing was changed."
        ) from None
    return lock


def _new_run_folder(job: ScrapeJob, spec: SiteSpec) -> tuple[Path, int]:
    """Reserve ``<root>/<slug>-N`` (``allocate_run``) or create the given folder."""
    if job.allocate_run:
        reserved = allocate_run_dir(job.output_dir, spec.novel_slug)
        return reserved.path, reserved.number
    folder = Path(job.output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    match = re.fullmatch(re.escape(spec.novel_slug) + r"-([1-9][0-9]*)", folder.name)
    return folder, int(match.group(1)) if match else 0


def _differing_source_fields(saved: SourceIdentity, current: SourceIdentity) -> list[str]:
    a, b = saved.to_json(), current.to_json()
    return [name for name in a if a[name] != b.get(name)]


def _open_resume(job: ScrapeJob, spec: SiteSpec, store: ChapterManifestStore, log: LogFn):
    """Validate an earlier run folder for Resume (read-only checks first, then the lock).

    Refuses, naming the reason, a folder this version did not create, a damaged journal or
    selection, another novel or source, other output formats or grouping, or a chapter list that
    now contradicts the run's pages. Returns ``(run_dir, lock, replay, chapters)``."""
    run_dir = Path(job.output_dir)
    found = open_existing_run(run_dir.parent, run_dir, expected_novel_slug=spec.novel_slug)
    from .observed_preflight import guard_observed_resume_store, validate_observed_resume
    guard_observed_resume_store(SourceIdentity.from_site_spec(spec), store)
    lock = _lock_run(found.path)
    try:
        replay = replay_journal(found.path / "run-journal.jsonl")
        header = replay.header
        if header.novel_slug != found.novel_slug or header.run_number != found.number:
            raise JournalError("The run journal describes another run folder.")
        current = SourceIdentity.from_site_spec(spec)
        if header.source != current:
            fields = ", ".join(_differing_source_fields(header.source, current)) or "identity"
            raise JournalError(
                f"This run was made from a different source than the selected website "
                f"({fields} differ). Choose the same website, or start a new run."
            )
        if tuple(job.formats) != header.formats:
            raise JournalError(
                f"This run saves {'/'.join(header.formats).upper()}, not "
                f"{'/'.join(job.formats).upper()} (output formats differ)."
            )
        if job.output_mode.value != header.grouping_mode or (
            header.grouping_mode == "chunked" and job.chunk_size != header.chunk_size
        ):
            raise JournalError("This run used a different grouping (or chunk size).")
        chapters = load_selection(found.path, header)
        loaded = store.load_effective(current, quarantine=False)
        validate_observed_resume(current, loaded, header, chapters, _manifest_digest(loaded.effective))
        known = {c.page_url: c for c in loaded.effective.chapters}
        for chapter in chapters:
            now = known.get(chapter.page_url)
            if now is not None and (
                now.label != chapter.label
                or (now.slug is not None and chapter.slug is not None and now.slug != chapter.slug)
            ):
                raise JournalError(
                    f"The site's chapter list changed since this run began: {chapter.page_url} was "
                    f"chapter {chapter.label} and is now chapter {now.label}. Start a new run."
                )
    except BaseException:
        lock.release()
        raise
    return found.path, lock, replay, chapters


def _prepare_run(
    job: ScrapeJob,
    spec: SiteSpec,
    store: ChapterManifestStore,
    discover: Callable[..., list],
    report: RunReport,
    log: LogFn,
    cancel_event: Optional[threading.Event],
    progress_cb: Optional[ProgressFn],
    phase_cb: Optional[PhaseFn],
) -> Optional[_Run]:
    """The one start-of-run step both execution paths share (v0.3.0 03.2a + 03.3).

    A new run settles its chapters from the validated manifest (refusing what cannot be
    confirmed, clamping an oversized End), then reserves its folder and writes the selection and
    journal header. A resume validates the earlier folder and uses the run's own fixed selection,
    with no table-of-contents request. Either way, complete outputs are skipped (verified by
    checksum) and the rest is returned to fetch. ``None``: nothing to do (an empty chapter list)."""
    resumed = bool(job.resume)
    if resumed:
        run_dir, lock, replay, chapters = _open_resume(job, spec, store, log)
        header = replay.header
        try:
            journal = RunJournal.resume(run_dir, replay)
            journal.resumed()
            for orphan in remove_orphan_temps(run_dir):
                log(f"  removed an interrupted temporary file: {orphan.name}")
        except BaseException:
            lock.release()
            raise
        metas = [chapter_preflight.meta_from_identity(c) for c in chapters]
        report.requested_range = header.requested
        report.effective_range = header.effective
        report.range_notes = list(header.notes)
        report.resumed = True
        log(
            f"Resuming {run_dir.name}: {len(chapters)} chapters ({header.first_label} to "
            f"{header.last_label}) exactly as the run selected them; no chapter-list request."
        )
    else:
        selection = chapter_preflight.resolve_chapters(
            spec,
            start=job.start,
            end=_end_hint_for(job),
            store=store,
            discover=discover,
            log=log,
            legacy_index=None if job.allocate_run else Path(job.output_dir) / INDEX_FILENAME,
            refresh=job.refresh_chapter_list,
            cancel_event=cancel_event,
        )
        if selection.empty_index:
            report.warnings.append("The chapter index came back empty; nothing to do.")
            return None
        _record_selection(selection, report, log)
        metas = selection.chapters
        if not metas:
            report.warnings.append("No chapter in the selected range can be saved; nothing to do.")
            return None
        source = SourceIdentity.from_site_spec(spec)
        chapters = [chapter_preflight.identity_from_meta(source, m) for m in metas]
        run_dir, number = _new_run_folder(job, spec)
        lock = _lock_run(run_dir)
        try:
            effective = store.load_effective(source, quarantine=False).effective
            notes = list(selection.range_notes)
            if selection.toc_gaps:
                notes.append(f"{len(selection.toc_gaps)} chapter number(s) inside the range are not "
                             "on the site's chapter list")
            header = RunHeader(
                novel_slug=spec.novel_slug, run_number=number, source=source,
                selection_sha256=selection_sha256(chapters), selection_count=len(chapters),
                first_label=chapters[0].label.text, last_label=chapters[-1].label.text,
                requested=(job.start, job.end), effective=tuple(selection.effective_range),
                formats=tuple(job.formats), grouping_mode=job.output_mode.value,
                chunk_size=int(job.chunk_size), manifest_sha256=_manifest_digest(effective),
                notes=tuple(notes), created_utc=store.now_utc(),
                outcome_policy=OUTCOME_POLICY,
            )
            journal = RunJournal.create(run_dir, header, chapters)
        except BaseException:
            lock.release()
            raise

    report.output_dir = run_dir
    labels = [c.label.text for c in chapters]
    outputs = RunOutputs(
        run_dir=run_dir, journal=journal, metas=metas, labels=labels, mode=job.output_mode,
        chunk_size=job.chunk_size, formats=tuple(job.formats), title=spec.novel_title,
        stem=_stem_for(spec), report=report, log=log,
        render=render_output,
        replay=replay if resumed else None, cancel_event=cancel_event,
    )
    try:
        if job.output_mode is OutputMode.SEPARATE:
            _record_duplicate_output_numbers(metas, report, log)
        fetch_list = outputs.plan_fetch()
        state = _Progress(total=len(metas), cb=progress_cb, phase_cb=phase_cb,
                          cached=lambda: report.from_cache)
        state.phase(PHASE_SCRAPING)
        done = outputs.resumed_positions
        if done:
            report.skipped_existing.extend(metas[p].index for p in done)
            state.tick(len(done), skipped=True)
            log(f"  {len(done)} chapter(s) already saved and verified by checksum; skipping them.")
    except BaseException:
        _end_journal(journal, "failed", log)
        lock.release()
        raise
    return _Run(run_dir=run_dir, journal=journal, outputs=outputs, fetch_list=fetch_list,
                state=state, lock=lock)


def _manifest_digest(effective: object) -> Optional[str]:
    """Selection provenance; Supreme Magus additionally binds Resume to policy evidence."""
    from .chapter_verification import requires_verification
    source = getattr(effective, "source", None)
    if source is not None and requires_verification(source):
        return getattr(effective, "runtime_sha256", None) or getattr(effective, "baseline_sha256", None)
    try:
        rows = [c.to_json() for c in effective.chapters]
        spans = [list(span) for span in effective.spans]
    except Exception:  # noqa: BLE001 - provenance only; never blocks a run
        return None
    payload = json.dumps({"chapters": rows, "spans": spans}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _end_journal(journal: RunJournal, state: str, log: LogFn, **counts: object) -> None:
    try:
        journal.end(state, **counts)
    except OSError as exc:
        log(f"WARNING: the run journal could not be updated ({exc}); a resume re-checks every file.")


def _finish_run(run: _Run, report: RunReport, log: LogFn) -> None:
    """Publish what is pending, record failures and how the session ended, write the audit and
    release the folder."""
    try:
        try:
            run.outputs.finish(cancelled=report.cancelled)
        except ScrapeCancelled:
            # Stop pressed while files were being built: every finished file is kept and recorded;
            # a resume builds the rest (nothing partial is published).
            report.cancelled = True
            log("Run cancelled: stopped while the output files were being built; the files already "
                "finished are kept and Resume Existing Run builds the rest.")
        outputs = run.outputs
        audit = outputs.audit()
        report.failed = list(dict.fromkeys(outputs.metas[p].index for p in sorted(outputs.failed)))
        if report.cancelled:
            state = "cancelled"
        elif outputs.failed or outputs.pending or report.accounting.get("integrity_suspicions"):
            state = "complete_with_problems"
        else:
            state = "complete"
        _end_journal(run.journal, state, log, files=len(report.written),
                     failed=report.accounting["requested_failure"],
                     attempted=report.accounting["attempted"], processed=report.accounting["processed"],
                     fallback_preserved=report.accounting["fallback_preserved"],
                     not_attempted=report.accounting["not_attempted"],
                     fetch_failures=report.accounting["fetch_failures"], incomplete_groups=len(outputs.pending))
        try:
            write_json_atomic(run.run_dir / AUDIT_FILENAME, audit)
        except OSError as exc:
            log(f"WARNING: the run audit could not be written ({exc}).")
    finally:
        run.lock.release()


def _abort_run(run: _Run, log: LogFn) -> None:
    """An unexpected error ended the run: record it (best effort) and release the folder."""
    try:
        run.outputs.flush_deferred_failures()
        _end_journal(run.journal, "failed", log)
    finally:
        run.lock.release()


def _cancelled(cancel_event: Optional[threading.Event]) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _collect_warnings(adapter: object, report: RunReport) -> None:
    warnings = getattr(adapter, "warnings", None)
    if warnings:
        for w in warnings:
            if w not in report.warnings:
                report.warnings.append(w)


def _drive(
    job: ScrapeJob,
    spec: SiteSpec,
    adapter: object,
    report: RunReport,
    log: LogFn,
    cancel_event: Optional[threading.Event],
    progress_cb: Optional[ProgressFn],
    pacer: _Pacer,
    phase_cb: Optional[PhaseFn] = None,
    *,
    store: ChapterManifestStore,
) -> RunReport:
    # 3. Chapter list: the validated manifest decides what (if anything) is discovered.
    def discover(**window) -> list[ChapterMeta]:
        return _build_chapter_index(adapter, spec, **window)

    run = _prepare_run(job, spec, store, discover, report, log, cancel_event, progress_cb, phase_cb)
    if run is None:
        _collect_warnings(adapter, report)
        return report

    reasons: dict[str, FailureInfo] = {}
    non_retryable: set[str] = set()
    try:
        obtained: set[str] = set()
        try:
            # 4. One pass over the pages still needed, then ONE second-pass sweep over the
            #    non-permanent failures (Phase 9C): a chapter that clears minutes later is still
            #    saved, into its own group, before any grouped file is written.
            for meta in run.fetch_list:
                if _cancelled(cancel_event):
                    report.cancelled = True
                    break
                run.outputs.attempt(meta)
                content = _fetch_one(adapter, meta, spec, report, log, job, cancel_event, pacer,
                                     sleep_after=True, reasons=reasons, failure_cb=run.outputs.fail,
                                     non_retryable=non_retryable)
                if content is not None:
                    obtained.add(meta.url)
                    run.outputs.add(meta, content)
                run.state.tick()

            sweep = [m for m in run.fetch_list if m.url not in obtained and m.url in reasons
                     and m.url not in non_retryable]
            if sweep and not report.cancelled and not _cancelled(cancel_event):
                log(f"Second-pass sweep: re-attempting {len(sweep)} failed chapter(s)…")
                for meta in sweep:
                    if _cancelled(cancel_event):
                        report.cancelled = True
                        break
                    content = _fetch_one(adapter, meta, spec, report, log, job, cancel_event,
                                         pacer, sleep_after=True, reasons=reasons,
                                         failure_cb=run.outputs.fail, non_retryable=non_retryable)
                    if content is not None:
                        obtained.add(meta.url)
                        if meta.index in report.failed:
                            report.failed.remove(meta.index)
                        report.rescued.append(meta.index)
                        run.outputs.add(meta, content)
        except ScrapeCancelled as exc:
            log(f"Run cancelled: {exc}")
            report.cancelled = True
        if _cancelled(cancel_event):
            report.cancelled = True

        for meta in run.fetch_list:
            if meta.url not in obtained and meta.url in reasons:
                run.outputs.fail(meta, reasons[meta.url])
        if not report.cancelled and job.output_mode is not OutputMode.SEPARATE and run.outputs.pending:
            run.state.phase(PHASE_BUILDING)
        _finish_run(run, report, log)
    except BaseException:
        _abort_run(run, log)
        raise

    report.auto_slowdowns = pacer.slowdowns
    report.effective_delay = pacer.current
    _collect_warnings(adapter, report)
    log(report.summary())
    return report


class _Progress:
    """Tiny done/total tracker that forwards to the GUI progress callback.

    ``done`` counts selected chapters that reached an end state (saved, failed or already
    present), each exactly once. ``skipped`` is the already-present share of ``done`` and
    ``cached`` how many chapter pages the saved-page cache served (read from the report)."""

    def __init__(
        self, total: int, cb: Optional[ProgressFn], phase_cb: Optional[PhaseFn] = None,
        cached: Optional[Callable[[], int]] = None,
    ) -> None:
        self.total = total
        self.done = 0
        self.skipped = 0
        self.cached = 0
        self._cb = cb
        self._phase_cb = phase_cb
        self._cached = cached

    def tick(self, n: int = 1, *, skipped: bool = False) -> None:
        self.done += n
        if skipped:
            self.skipped += n
        cached = self._cached() if self._cached is not None else self.cached
        if skipped or cached != self.cached:
            self.cached = cached
            self.phase(PHASE_SCRAPING)
        if self._cb is not None:
            self._cb(self.done, self.total)

    def phase(self, phase: str) -> None:
        """Tell the observer (if any) the run entered ``phase``. Never raises."""
        if self._phase_cb is None:
            return
        try:
            self._phase_cb(phase, self.total, self.skipped, self.cached)
        except Exception:  # noqa: BLE001 - an observer must never affect the run
            pass


def _is_permanent_failure(exc: Exception) -> bool:
    """URL failures excluded from this run's sweep, preserving the retry policy.

    Neither a missing URL nor a denied request proves a displayed chapter absent.
    """
    if isinstance(exc, NotFoundFetchError):
        return True
    msg = str(exc).lower()
    # Only 403 (forbidden) and 404 (not found) are permanent. 401 (unauthorized)
    # is transient here — it must be swept and trigger auto-slowdown, not skipped.
    return bool(re.search(r"\bhttp\s*(?:403|404)\b", msg))


def _fetch_one(
    adapter: object,
    meta: ChapterMeta,
    spec: SiteSpec,
    report: RunReport,
    log: LogFn,
    job: ScrapeJob,
    cancel_event: Optional[threading.Event],
    pacer: _Pacer,
    *,
    sleep_after: bool,
    reasons: Optional[dict] = None,
    failure_cb: Optional[Callable] = None,
    non_retryable: Optional[set] = None,
) -> Optional[ChapterContent]:
    """Fetch one chapter. A failure is recorded and returns None (never fatal);
    ``ScrapeCancelled`` is allowed to propagate so the run ends cleanly.

    A non-permanent failure (a block/challenge, not a true 404) registers an
    auto-slowdown step on the pacer; a permanent 403/404 is tracked separately so
    the second-pass sweep skips it. Records each failed index at most once so a
    sweep re-attempt does not double-count.

    A chapter served by the saved-page cache made no request, so it is counted and the
    inter-fetch delay is not slept after it (a repeat run of a cached novel would otherwise
    wait the full delay per chapter for nothing)."""
    rm = getattr(adapter, "_rm", None)
    if rm is not None and hasattr(rm, "last_fetch_info"):
        rm.last_fetch_info = None
    try:
        content = adapter.fetch_chapter(meta, spec)  # type: ignore[attr-defined]
    except ScrapeCancelled:
        raise
    except EmptyExtractionError as exc:
        # The page WAS fetched (a real, non-challenge response) but had no
        # extractable body. This is an extraction failure, NOT a block: record it
        # as a plain failure, but do NOT register an auto-slowdown step and do NOT
        # mark it sweepable (re-fetching the same page yields the same empty body).
        info = classify_failure(exc)
        log(f"  chapter {chapter_preflight.meta_label(meta)} FAILED [{info.reason_code}] at {meta.url}: {exc}")
        if reasons is not None:
            reasons[meta.url] = info
        if failure_cb is not None:
            failure_cb(meta, info)
        if non_retryable is not None:
            non_retryable.add(meta.url)
        if meta.index not in report.failed:
            report.failed.append(meta.index)
        if info.reason_code == "EMPTY_BODY" and meta.index not in report.extraction_failed:
            report.extraction_failed.append(meta.index)
        elif info.reason_code.startswith("IDENTITY_") and meta.index not in report.identity_failed:
            report.identity_failed.append(meta.index)
        # Still observe normal inter-fetch politeness (pacer was not raised).
        if sleep_after and not _cancelled(cancel_event):
            pacer.sleep()
        return None
    except Exception as exc:  # one bad chapter must never kill the run
        info = classify_failure(exc)
        log(f"  chapter {chapter_preflight.meta_label(meta)} FAILED [{info.reason_code}] at {meta.url}: {exc}")
        if reasons is not None:
            reasons[meta.url] = info
        if failure_cb is not None:
            failure_cb(meta, info)
        if meta.index not in report.failed:
            report.failed.append(meta.index)
        if _is_permanent_failure(exc):
            if non_retryable is not None:
                non_retryable.add(meta.url)
            if meta.index not in report.permanent_failed:
                report.permanent_failed.append(meta.index)
        else:
            # A block/challenge: raise the inter-fetch delay AND actually sleep it
            # before the next fetch/sweep retry, so the newly auto-slowed pacing is
            # applied immediately rather than only on the next success.
            pacer.register_block()
            if sleep_after and not _cancelled(cancel_event):
                pacer.sleep()
        return None
    if reasons is not None:
        reasons.pop(meta.url, None)
    served = getattr(getattr(adapter, "_rm", None), "last_fetch_info", None)  # looked up again: built lazily
    if getattr(served, "from_cache", False):
        report.from_cache += 1
    elif sleep_after and not _cancelled(cancel_event):
        pacer.sleep()
    return content


def _sweepable(report: RunReport) -> list[int]:
    """Indices eligible for the second-pass sweep: failed, but neither a permanent
    403/404 nor an extraction failure (a fetched-but-empty page does not change on
    an immediate re-fetch, so sweeping it only wastes a request)."""
    return [
        i
        for i in report.failed
        if i not in report.permanent_failed and i not in report.extraction_failed
    ]
