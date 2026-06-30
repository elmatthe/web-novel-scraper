"""TOC-first orchestration: discovery, caching, resume, output modes, run report.

The pipeline is the one place that ties the catalog, an adapter, the request
manager, and the PDF builder together. It never parses HTML and never lays out a
PDF itself — it drives the adapter (``build_chapter_index`` / ``fetch_chapter``)
and the PDF builder (``create_pdf``), and owns only the run policy:

  1. Resolve the ``SiteSpec`` from the catalog and **refuse a disabled row**
     before touching the adapter (defense in depth — the GUI greys disabled
     sites, this guard is the pipeline-layer refusal, and the stub adapter's
     ``NotImplementedError`` is the final backstop).
  2. Build the chapter index once and persist it to ``chapter_index.json`` in the
     output dir; on a re-run into the same dir it is loaded instead of refetched.
  3. Clamp the requested ``[start, end]`` to the available TOC range (logged).
  4. For each chapter: skip when its PDF already exists (resume), otherwise fetch
     and collect it. A single failed chapter is recorded and skipped — never
     fatal. The per-fetch ``delay`` and a ``cancel_event`` (Stop button) are
     honoured between fetches.
  5. Emit PDFs per the output mode (separate / chunked / single), each through
     ``pdf_builder.create_pdf`` (which also strips heading-only pages).
  6. Return a :class:`RunReport` (written / skipped / failed counts + a resume
     hint for any failures).

Networking, parsing, and layout all live in their own modules; the pipeline is
pure orchestration so it can be driven in tests with a fake adapter and no
network.
"""

from __future__ import annotations

import collections
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import catalog
from . import pdf_builder
from . import rescue_pool as rp
from .adapters.base import BaseAdapter
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
from .registry import REGISTRY, AdapterDisabledError
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

INDEX_FILENAME = "chapter_index.json"

# ── 0.2.0 circuit-breaker thresholds (pipeline-owned, headless-only — §3.9) ───
# The breaker watches PRIMARY network results and, when headless mode is broadly
# blocked, switches the primary to a visible browser for the rest of the run.
BREAKER_CONSECUTIVE_CHALLENGES = 5   # consecutive primary network challenges → trip
BREAKER_WINDOW = 20                  # rolling window of primary network fetches
BREAKER_WINDOW_CHALLENGES = 9        # ≥ this many challenges in the window → trip (>40%)
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
    written: list[Path] = field(default_factory=list)        # PDF files written
    skipped_existing: list[int] = field(default_factory=list)  # resume-skipped ch.
    failed: list[int] = field(default_factory=list)          # ch. still failed after sweep
    permanent_failed: list[int] = field(default_factory=list)  # 403/404 — not swept
    extraction_failed: list[int] = field(default_factory=list)  # fetched OK but no body — not swept, not a block
    rescued: list[int] = field(default_factory=list)         # ch. saved by rescue (or 2nd-pass sweep)
    warnings: list[str] = field(default_factory=list)        # adapter + pipeline warnings
    cancelled: bool = False
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
        lines = [
            f"Run complete{' (cancelled)' if self.cancelled else ''}.",
            f"  Output: {self.output_dir}",
            f"  Range:  requested {self.requested_range[0]}-{self.requested_range[1]}, "
            f"scraped {self.effective_range[0]}-{self.effective_range[1]}",
            f"  Written: {len(self.written)} PDF(s)",
            f"  Skipped (already present): {len(self.skipped_existing)}",
            f"  Failed:  {len(self.failed)}",
        ]
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
                "    Tip: re-run with the SAME output folder to retry only the "
                "missing chapters — already-written PDFs are skipped."
            )
        if self.warnings:
            lines.append(f"  Warnings: {len(self.warnings)}")
        return "\n".join(lines)


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


# ── Chapter-index persistence (pipeline-level, output-dir scoped) ─────────────
def _save_index(index_path: Path, metas: list[ChapterMeta]) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "index": m.index,
            "url": m.url,
            "title": m.title,
            "source_id": m.source_id,
        }
        for m in metas
    ]
    index_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_index(index_path: Path) -> Optional[list[ChapterMeta]]:
    if not index_path.is_file():
        return None
    try:
        raw = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, list) or not raw:
        return None
    return [
        ChapterMeta(
            index=int(d["index"]),
            url=str(d["url"]),
            title=d.get("title"),
            source_id=d.get("source_id"),
        )
        for d in raw
    ]


# ── Filename helpers ─────────────────────────────────────────────────────────
def _stem_for(spec: SiteSpec) -> str:
    """A safe, space-free filename stem for chunked/single output (e.g.
    ``Shadow_Slave``), derived from the display title with the slug as fallback."""
    safe = BaseAdapter.safe_filename(spec.novel_title)
    return re.sub(r"\s+", "_", safe).strip("_") or spec.novel_slug


def _separate_pdf_name(content: ChapterContent) -> str:
    """The SEPARATE-mode filename: ``safe_filename(heading).pdf``."""
    return BaseAdapter.safe_filename(content.heading) + ".pdf"


def _existing_separate_pdf(output_dir: Path, index: int) -> Optional[Path]:
    """Find an already-written SEPARATE PDF for chapter ``index`` without
    fetching. Every heading begins with ``Chapter N`` followed by a non-digit
    (``:`` → ``_`` after ``safe_filename``, or ``.`` when degraded), so the index
    can be matched off the filename alone — that is what makes resume skip the
    network for chapters already on disk."""
    if not output_dir.is_dir():
        return None
    pat = re.compile(rf"^Chapter\s+{index}(?:[^0-9].*)?\.pdf$", re.IGNORECASE)
    for p in sorted(output_dir.glob("*.pdf")):
        if pat.match(p.name):
            return p
    return None


def _chunks(metas: list[ChapterMeta], size: int) -> Iterable[list[ChapterMeta]]:
    size = max(1, int(size))
    for i in range(0, len(metas), size):
        yield metas[i : i + size]


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

    def build_toc(self) -> list[ChapterMeta]:
        return self.adapter.build_chapter_index(self.runtime_spec, fast_path=True)  # type: ignore[attr-defined]

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
        on_content: Callable[[int, ChapterContent], None],
        contents: dict,
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
        self.on_content = on_content
        self.contents = contents
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
        try:
            content = self.primary.fetch(meta)
        except ScrapeCancelled:
            raise
        except NotFoundFetchError as exc:
            return "not_found", exc
        except RateLimitedFetchError as exc:
            return "rate_limited", exc
        except EmptyExtractionError as exc:
            return "extraction", exc
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

        if kind == "success":
            if not self._from_cache():
                self.breaker.record_network(is_challenge=False)
            self._store_content(meta.index, payload)
            return
        if kind == "not_found":
            self.breaker.record_network(is_challenge=False)
            self._fail(meta.index, permanent=True)
            self.log(f"  chapter {meta.index} permanently unavailable (not found).")
            return
        if kind == "extraction":
            self.breaker.record_network(is_challenge=False)
            self._fail(meta.index, extraction=True)
            self.log(f"  chapter {meta.index} fetched but had no extractable body.")
            return
        if kind == "error":
            # Not a fetch-layer signal — record a plain failure (resumeable), no rescue.
            self._fail(meta.index)
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
            if kind == "success":
                if not self._from_cache():
                    self.breaker.record_network(is_challenge=False)
                self._store_content(meta.index, payload)
                return
            if kind == "rate_limited":
                continue  # still limited — the next nav waits the (re-applied) cooldown
            if kind == "not_found":
                self.breaker.record_network(is_challenge=False)
                self._fail(meta.index, permanent=True)
                return
            if kind == "extraction":
                self.breaker.record_network(is_challenge=False)
                self._fail(meta.index, extraction=True)
                return
            if kind == "error":
                self._fail(meta.index)
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
        self._fail(meta.index)  # plain failed — not permanent, not rescued, not a block

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
    def _store_content(self, index: int, content: ChapterContent) -> None:
        self.contents[index] = content
        if index in self.report.failed:
            self.report.failed.remove(index)  # a prior transient now cleared
        self.on_content(index, content)  # SEPARATE writes promptly; others store
        self.state.tick()

    def _fail(self, index: int, *, permanent: bool = False, extraction: bool = False) -> None:
        if index not in self.report.failed:
            self.report.failed.append(index)
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
            self._store_content(idx, res.content)  # ticks + writes SEPARATE
            self.log(f"  chapter {idx} rescued ({res.strategy}).")
        elif res.status == rp.RESCUE_EXHAUSTED:
            if idx not in self.report.failed:
                self.report.failed.append(idx)
            if idx not in self.report.rescue_exhausted:
                self.report.rescue_exhausted.append(idx)
            self.state.tick()
            self.log(f"  chapter {idx} failed after all methods.")
        elif res.status == rp.NOT_FOUND:
            self._fail(idx, permanent=True)
            self.log(f"  chapter {idx} permanently unavailable (not found, via rescue).")
        elif res.status == rp.EXTRACTION_FAILED:
            self._fail(idx, extraction=True)
            self.log(f"  chapter {idx} fetched but had no extractable body (via rescue).")
        elif res.status == rp.POOL_FAILED:
            if idx not in self.report.failed:
                self.report.failed.append(idx)
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
        ChapterIndexUnavailable: on the FWN-rescue path, if the TOC cannot be built
            even after the visible-primary fallback (§3.11).
    """
    log = log if log is not None else logger.info

    # 1. Resolve the catalog row and refuse a disabled site BEFORE the adapter is
    #    built or touched (pipeline-layer defense in depth).
    spec = catalog.get_spec(job.novel_slug, job.adapter_key)
    if not spec.enabled:
        raise AdapterDisabledError(
            f"Site {spec.adapter_key!r} for novel {spec.novel_slug!r} is disabled "
            "(not yet available in this release); the pipeline will not run it."
        )

    output_dir = Path(job.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report = RunReport(
        output_dir=output_dir,
        requested_range=(job.start, job.end),
        effective_range=(job.start, job.end),
    )

    # 2a. FWN-browser scope gate (§3.14): the fast-primary + single-lane rescue
    #     conductor. Only when no adapter was injected (an injected adapter keeps the
    #     legacy flow for back-compat / tests). The pipeline OWNS the primary manager
    #     here, building it via the factory so it can replace it on a TOC fallback or
    #     a breaker switch (§3.10/§3.15).
    if _rescue_enabled(job) and adapter is None:
        return _run_with_rescue(
            job, spec, output_dir, report, log, cancel_event, progress_cb,
            sleep_fn=sleep_fn, monotonic_fn=monotonic_fn, host_limiter=host_limiter,
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
            job, drive_spec, adapter, output_dir, report, log, cancel_event,
            progress_cb, pacer,
        )
    except ScrapeCancelled as exc:
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
    output_dir: Path,
    report: RunReport,
    log: LogFn,
    cancel_event: Optional[threading.Event],
    progress_cb: Optional[ProgressFn],
    *,
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
            use_cache=False,                 # rescued content is intentionally not cached
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
        # 3. TOC bootstrap (§3.11) — runs BEFORE the pool/breaker engage. A persisted
        #    index (resume) is loaded instead of refetched.
        index_path = output_dir / INDEX_FILENAME
        metas = _load_index(index_path)
        if metas is None:
            metas = _bootstrap_toc(primary, spec, report, log, run_cancel)
            if not metas:
                report.warnings.append("The chapter index came back empty; nothing to do.")
                _collect_warnings(primary.adapter, report)
                return report
            _save_index(index_path, metas)
            log(f"  discovered {len(metas)} chapters; saved {INDEX_FILENAME}.")
        else:
            log(f"  loaded {len(metas)} chapters from {INDEX_FILENAME} (resume).")

        metas.sort(key=lambda m: m.index)
        avail_lo, avail_hi = metas[0].index, metas[-1].index
        start = max(job.start, avail_lo)
        end = min(job.end, avail_hi)
        if (start, end) != (job.start, job.end):
            log(
                f"Clamped requested range {job.start}-{job.end} to available "
                f"{start}-{end} (TOC covers {avail_lo}-{avail_hi})."
            )
        report.effective_range = (start, end)
        in_range = [m for m in metas if start <= m.index <= end]
        if not in_range:
            report.warnings.append(
                f"Requested range {job.start}-{job.end} is outside the available "
                f"chapters {avail_lo}-{avail_hi}; nothing to scrape."
            )
            log(report.warnings[-1])
            _collect_warnings(primary.adapter, report)
            return report

        # 4. Drive the fast primary pass + rescue lane, then assemble output.
        contents: dict[int, ChapterContent] = {}
        state = _Progress(total=len(in_range), cb=progress_cb)
        on_content = _make_on_content(job, spec, output_dir, report, log, contents)
        conductor = _RescueConductor(
            job=job, spec=spec, runtime_spec=runtime_spec, primary=primary,
            breaker=breaker, pacer=pacer, limiter=limiter, pool_factory=pool_factory,
            report=report, log=log, run_cancel=run_cancel, state=state,
            on_content=on_content, contents=contents,
        )

        fetch_list = _plan_fetch_list(job, spec, output_dir, in_range, report, state)
        try:
            for meta in fetch_list:
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

        # 6. Assemble chunked/single output (folding rescued content in index order).
        #    SEPARATE PDFs were written promptly by ``on_content``. No partial
        #    chunked/single PDF is written on cancel (re-fetched cleanly on resume).
        if not report.cancelled:
            _assemble_output(job, spec, output_dir, in_range, contents, report, log)

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
) -> list[ChapterMeta]:
    """Build the TOC on the primary mode; on a headless Cloudflare block, switch the
    primary visible and retry ONCE; if that still fails, abort cleanly (§3.11). A TOC
    request is NEVER handed to chapter rescue (it has no ``ChapterMeta``)."""
    log("Building chapter index (TOC) …")
    try:
        return primary.build_toc()
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
                return primary.build_toc()
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


def _make_on_content(
    job: ScrapeJob,
    spec: SiteSpec,
    output_dir: Path,
    report: RunReport,
    log: LogFn,
    contents: dict,
) -> Callable[[int, ChapterContent], None]:
    """For SEPARATE, write each chapter's PDF promptly as its content lands (§3.12);
    for CHUNKED/SINGLE the content is folded into the file at the end (it is already
    held in ``contents``), so the callback is a no-op there."""
    if job.output_mode is not OutputMode.SEPARATE:
        return lambda _index, _content: None

    def _write(_index: int, content: ChapterContent) -> None:
        path = output_dir / _separate_pdf_name(content)
        pdf_builder.create_pdf([content], path, title=spec.novel_title)
        report.written.append(path)
        log(f"  wrote {path.name}")

    return _write


def _plan_fetch_list(
    job: ScrapeJob,
    spec: SiteSpec,
    output_dir: Path,
    in_range: list[ChapterMeta],
    report: RunReport,
    state: "_Progress",
) -> list[ChapterMeta]:
    """Apply resume-skips per output mode and return the chapters that still need
    fetching. Skipped chapters are recorded + ticked here (their terminal state is
    'already present'), exactly as the legacy modes resume."""
    if job.output_mode is OutputMode.SEPARATE:
        out: list[ChapterMeta] = []
        for meta in in_range:
            if _existing_separate_pdf(output_dir, meta.index) is not None:
                report.skipped_existing.append(meta.index)
                state.tick()
            else:
                out.append(meta)
        return out

    stem = _stem_for(spec)
    if job.output_mode is OutputMode.SINGLE:
        if (output_dir / f"{stem}_All_Chapters.pdf").exists():
            report.skipped_existing.extend(m.index for m in in_range)
            state.tick(len(in_range))
            return []
        return list(in_range)

    # CHUNKED — skip whole chunks whose PDF already exists (resume).
    out = []
    for group in _chunks(in_range, job.chunk_size):
        a, b = group[0].index, group[-1].index
        if (output_dir / f"{stem}_Chapters_{a}-{b}.pdf").exists():
            report.skipped_existing.extend(m.index for m in group)
            state.tick(len(group))
        else:
            out.extend(group)
    return out


def _assemble_output(
    job: ScrapeJob,
    spec: SiteSpec,
    output_dir: Path,
    in_range: list[ChapterMeta],
    contents: dict,
    report: RunReport,
    log: LogFn,
) -> None:
    """Write CHUNKED/SINGLE PDFs after the final rescue drain, folding rescued
    content into the correct file in index order (§3.12). SEPARATE was written
    promptly by ``on_content`` and needs nothing here. Only chapters whose content
    was collected (primary success or rescued) are included; resume-skipped chunks
    were already excluded from ``in_range`` fetching and are not rewritten."""
    if job.output_mode is OutputMode.SEPARATE:
        return

    stem = _stem_for(spec)
    if job.output_mode is OutputMode.SINGLE:
        if (output_dir / f"{stem}_All_Chapters.pdf").exists():
            return
        ordered = [contents[i] for i in sorted(contents)]
        if ordered:
            path = output_dir / f"{stem}_All_Chapters.pdf"
            pdf_builder.create_pdf(ordered, path, title=spec.novel_title)
            report.written.append(path)
            log(f"  wrote {path.name} ({len(ordered)} chapters)")
        return

    # CHUNKED — one PDF per chunk, each sorted by chapter index.
    for group in _chunks(in_range, job.chunk_size):
        a, b = group[0].index, group[-1].index
        path = output_dir / f"{stem}_Chapters_{a}-{b}.pdf"
        if path.exists():
            continue  # resume-skipped chunk
        present = [contents[m.index] for m in group if m.index in contents]
        if present:
            pdf_builder.create_pdf(present, path, title=spec.novel_title)
            report.written.append(path)
            log(f"  wrote {path.name} ({len(present)} chapters)")


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
    output_dir: Path,
    report: RunReport,
    log: LogFn,
    cancel_event: Optional[threading.Event],
    progress_cb: Optional[ProgressFn],
    pacer: _Pacer,
) -> RunReport:
    # 3. TOC: load a persisted index (resume) or build + persist it.
    index_path = output_dir / INDEX_FILENAME
    metas = _load_index(index_path)
    if metas is None:
        log("Building chapter index (TOC) …")
        metas = adapter.build_chapter_index(spec)  # type: ignore[attr-defined]
        if not metas:
            report.warnings.append("The chapter index came back empty; nothing to do.")
            _collect_warnings(adapter, report)
            return report
        _save_index(index_path, metas)
        log(f"  discovered {len(metas)} chapters; saved {INDEX_FILENAME}.")
    else:
        log(f"  loaded {len(metas)} chapters from {INDEX_FILENAME} (resume).")

    metas.sort(key=lambda m: m.index)
    avail_lo, avail_hi = metas[0].index, metas[-1].index

    # 4. Clamp the requested range to the available TOC.
    start = max(job.start, avail_lo)
    end = min(job.end, avail_hi)
    if (start, end) != (job.start, job.end):
        log(
            f"Clamped requested range {job.start}-{job.end} to available "
            f"{start}-{end} (TOC covers {avail_lo}-{avail_hi})."
        )
    report.effective_range = (start, end)

    in_range = [m for m in metas if start <= m.index <= end]
    if not in_range:
        report.warnings.append(
            f"Requested range {job.start}-{job.end} is outside the available "
            f"chapters {avail_lo}-{avail_hi}; nothing to scrape."
        )
        log(report.warnings[-1])
        _collect_warnings(adapter, report)
        return report

    # 5. Emit per the output mode.
    stem = _stem_for(spec)
    state = _Progress(total=len(in_range), cb=progress_cb)

    if job.output_mode is OutputMode.SEPARATE:
        _run_separate(job, spec, adapter, output_dir, in_range, report, log,
                      cancel_event, state, pacer)
    elif job.output_mode is OutputMode.CHUNKED:
        _run_chunked(job, spec, adapter, output_dir, in_range, stem, report, log,
                     cancel_event, state, pacer)
    else:  # SINGLE
        _run_single(job, spec, adapter, output_dir, in_range, stem, report, log,
                    cancel_event, state, pacer)

    report.auto_slowdowns = pacer.slowdowns
    report.effective_delay = pacer.current
    _collect_warnings(adapter, report)
    log(report.summary())
    return report


class _Progress:
    """Tiny done/total tracker that forwards to the GUI progress callback."""

    def __init__(self, total: int, cb: Optional[ProgressFn]) -> None:
        self.total = total
        self.done = 0
        self._cb = cb

    def tick(self, n: int = 1) -> None:
        self.done += n
        if self._cb is not None:
            self._cb(self.done, self.total)


def _is_permanent_failure(exc: Exception) -> bool:
    """True for a permanently-dead chapter (a true 403/404). These must NOT be
    swept — re-attempting them only wastes time and can never succeed, so they
    short-circuit the relentless-retry treatment (Phase 9C fault tolerance)."""
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
) -> Optional[ChapterContent]:
    """Fetch one chapter. A failure is recorded and returns None (never fatal);
    ``ScrapeCancelled`` is allowed to propagate so the run ends cleanly.

    A non-permanent failure (a block/challenge, not a true 404) registers an
    auto-slowdown step on the pacer; a permanent 403/404 is tracked separately so
    the second-pass sweep skips it. Records each failed index at most once so a
    sweep re-attempt does not double-count."""
    try:
        content = adapter.fetch_chapter(meta, spec)  # type: ignore[attr-defined]
    except ScrapeCancelled:
        raise
    except EmptyExtractionError as exc:
        # The page WAS fetched (a real, non-challenge response) but had no
        # extractable body. This is an extraction failure, NOT a block: record it
        # as a plain failure, but do NOT register an auto-slowdown step and do NOT
        # mark it sweepable (re-fetching the same page yields the same empty body).
        log(f"  chapter {meta.index} FAILED (extraction, not a block): {exc}")
        if meta.index not in report.failed:
            report.failed.append(meta.index)
        if meta.index not in report.extraction_failed:
            report.extraction_failed.append(meta.index)
        # Still observe normal inter-fetch politeness (pacer was not raised).
        if sleep_after and not _cancelled(cancel_event):
            pacer.sleep()
        return None
    except Exception as exc:  # one bad chapter must never kill the run
        log(f"  chapter {meta.index} FAILED: {exc}")
        if meta.index not in report.failed:
            report.failed.append(meta.index)
        if _is_permanent_failure(exc):
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
    if sleep_after and not _cancelled(cancel_event):
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


def _run_separate(job, spec, adapter, output_dir, in_range, report, log,
                  cancel_event, state, pacer) -> None:
    by_index = {m.index: m for m in in_range}

    def _write(content: ChapterContent) -> None:
        path = output_dir / _separate_pdf_name(content)
        pdf_builder.create_pdf([content], path, title=spec.novel_title)
        report.written.append(path)
        log(f"  wrote {path.name}")

    for meta in in_range:
        if _cancelled(cancel_event):
            report.cancelled = True
            return
        existing = _existing_separate_pdf(output_dir, meta.index)
        if existing is not None:
            report.skipped_existing.append(meta.index)
            state.tick()
            continue
        content = _fetch_one(
            adapter, meta, spec, report, log, job, cancel_event, pacer,
            sleep_after=True,
        )
        if content is not None:
            _write(content)
        state.tick()

    # Second-pass sweep (Phase 9C): re-attempt every non-permanent failed chapter
    # once more, at the (possibly auto-slowed) delay — intermittent Cloudflare
    # often clears minutes later. A rescued chapter is written and removed from
    # the failed list; permanent 404s were excluded and stay failed.
    to_sweep = _sweepable(report)
    if to_sweep and not _cancelled(cancel_event):
        log(f"Second-pass sweep: re-attempting {len(to_sweep)} failed chapter(s)…")
        for index in to_sweep:
            if _cancelled(cancel_event):
                report.cancelled = True
                return
            content = _fetch_one(
                adapter, by_index[index], spec, report, log, job, cancel_event,
                pacer, sleep_after=True,
            )
            if content is not None:
                report.failed.remove(index)
                report.rescued.append(index)
                _write(content)


def _fetch_block_with_sweep(
    job, spec, adapter, metas, report, log, cancel_event, state, pacer
) -> Optional[list[ChapterContent]]:
    """Fetch a group of chapters for chunked/single output, then run the
    second-pass sweep over that group's non-permanent failures *before* the PDF is
    built, so a rescued chapter is included in the file. Returns the collected
    contents (index-ordered), or None when the run was cancelled mid-group (so the
    caller writes no partial PDF)."""
    contents: dict[int, ChapterContent] = {}
    failed_metas: list[ChapterMeta] = []
    for meta in metas:
        if _cancelled(cancel_event):
            report.cancelled = True
            return None
        content = _fetch_one(
            adapter, meta, spec, report, log, job, cancel_event, pacer,
            sleep_after=True,
        )
        if content is not None:
            contents[meta.index] = content
        elif (
            meta.index not in report.permanent_failed
            and meta.index not in report.extraction_failed
        ):
            failed_metas.append(meta)
        state.tick()

    if failed_metas and not _cancelled(cancel_event):
        log(f"Second-pass sweep: re-attempting {len(failed_metas)} failed chapter(s)…")
        for meta in failed_metas:
            if _cancelled(cancel_event):
                report.cancelled = True
                return None
            content = _fetch_one(
                adapter, meta, spec, report, log, job, cancel_event, pacer,
                sleep_after=True,
            )
            if content is not None:
                contents[meta.index] = content
                if meta.index in report.failed:
                    report.failed.remove(meta.index)
                report.rescued.append(meta.index)

    return [contents[i] for i in sorted(contents)]


def _run_chunked(job, spec, adapter, output_dir, in_range, stem, report, log,
                 cancel_event, state, pacer) -> None:
    """Chunked output with ONE second-pass sweep over the whole run.

    The first pass fetches every chunk and records its content; non-permanent
    failures from every chunk are collected globally. After the main loop a single
    sweep re-attempts them all once (permanent 403/404 excluded) and slots each
    rescued chapter back into its own chunk. Only then are the chunk PDFs written —
    same filenames/order as before, each sorted by chapter index. Doing the sweep
    once (instead of once per chunk) means a chapter that only clears minutes later
    is still rescued into the correct file."""
    # Each pending entry owns one chunk's output: its PDF path and its
    # index -> content map (filled in the main pass, topped up by the sweep).
    pending: list[dict] = []
    failed_metas: list[ChapterMeta] = []
    entry_for_index: dict[int, dict] = {}

    for group in _chunks(in_range, job.chunk_size):
        if _cancelled(cancel_event):
            report.cancelled = True
            break
        a, b = group[0].index, group[-1].index
        path = output_dir / f"{stem}_Chapters_{a}-{b}.pdf"
        if path.exists():
            report.skipped_existing.extend(m.index for m in group)
            state.tick(len(group))
            continue
        contents: dict[int, ChapterContent] = {}
        entry = {"path": path, "contents": contents}
        cancelled_mid_group = False
        for meta in group:
            if _cancelled(cancel_event):
                report.cancelled = True
                cancelled_mid_group = True
                break
            content = _fetch_one(
                adapter, meta, spec, report, log, job, cancel_event, pacer,
                sleep_after=True,
            )
            if content is not None:
                contents[meta.index] = content
            elif (
                meta.index not in report.permanent_failed
                and meta.index not in report.extraction_failed
            ):
                failed_metas.append(meta)
                entry_for_index[meta.index] = entry
            state.tick()
        if cancelled_mid_group:
            break
        pending.append(entry)

    # One second-pass sweep over ALL non-permanent failures collected above
    # (permanent 403/404 were never added, so they are not re-attempted).
    if failed_metas and not report.cancelled and not _cancelled(cancel_event):
        log(f"Second-pass sweep: re-attempting {len(failed_metas)} failed chapter(s)…")
        for meta in failed_metas:
            if _cancelled(cancel_event):
                report.cancelled = True
                break
            content = _fetch_one(
                adapter, meta, spec, report, log, job, cancel_event, pacer,
                sleep_after=True,
            )
            if content is not None:
                entry_for_index[meta.index]["contents"][meta.index] = content
                if meta.index in report.failed:
                    report.failed.remove(meta.index)
                report.rescued.append(meta.index)

    # Do not write any partial output on cancel — a half-fetched run is re-fetched
    # cleanly on resume into the same output dir.
    if report.cancelled:
        return

    for entry in pending:
        contents = entry["contents"]
        if contents:
            ordered = [contents[i] for i in sorted(contents)]
            pdf_builder.create_pdf(ordered, entry["path"], title=spec.novel_title)
            report.written.append(entry["path"])
            log(f"  wrote {entry['path'].name} ({len(ordered)} chapters)")


def _run_single(job, spec, adapter, output_dir, in_range, stem, report, log,
                cancel_event, state, pacer) -> None:
    path = output_dir / f"{stem}_All_Chapters.pdf"
    if path.exists():
        report.skipped_existing.extend(m.index for m in in_range)
        state.tick(len(in_range))
        log(f"  {path.name} already present; skipping (resume).")
        return
    contents = _fetch_block_with_sweep(
        job, spec, adapter, in_range, report, log, cancel_event, state, pacer
    )
    if report.cancelled:
        return  # no partial single PDF
    if contents:
        pdf_builder.create_pdf(contents, path, title=spec.novel_title)
        report.written.append(path)
        log(f"  wrote {path.name} ({len(contents)} chapters)")
