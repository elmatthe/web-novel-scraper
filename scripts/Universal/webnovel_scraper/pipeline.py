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
from .adapters.base import BaseAdapter
from .models import ChapterContent, ChapterMeta, OutputMode, ScrapeJob, SiteSpec
from .registry import REGISTRY, AdapterDisabledError
from .request_manager import RequestManager, ScrapeCancelled

logger = logging.getLogger(__name__)

INDEX_FILENAME = "chapter_index.json"

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
    rescued: list[int] = field(default_factory=list)         # ch. saved by the 2nd-pass sweep
    warnings: list[str] = field(default_factory=list)        # adapter + pipeline warnings
    cancelled: bool = False
    auto_slowdowns: int = 0          # how many times auto-slowdown raised the delay
    effective_delay: float = 0.0     # final inter-fetch delay; never exceeds the ceiling

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
                f"  Rescued by 2nd-pass sweep: {len(self.rescued)}"
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
    novel_slug: str, *, downloads_root: Optional[Path] = None
) -> Path:
    """Return the next free ``webscraped_{slug}-N`` dir under Downloads.

    Auto-increments ``N`` so a fresh run never clobbers a previous one. The path
    is **not** created here — the pipeline creates ``job.output_dir`` when it
    runs. Resolved via ``Path.home()`` so it is platform-neutral (never a
    hardcoded ``C:\\Users\\...``); ``downloads_root`` overrides for tests.
    """
    root = downloads_root if downloads_root is not None else (Path.home() / "Downloads")
    n = 1
    while (root / f"webscraped_{novel_slug}-{n}").exists():
        n += 1
    return root / f"webscraped_{novel_slug}-{n}"


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

    Raises:
        AdapterDisabledError: if the resolved catalog row is disabled — the
            pipeline refuses before the adapter is built or called.
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

    # 2. Build (or accept) the adapter + request manager.
    owns_rm = False
    rm: Optional[RequestManager] = None
    if adapter is None:
        rm = request_manager
        if rm is None:
            rm = RequestManager(
                slug=spec.novel_slug,
                use_cache=job.use_cache,
                log_fn=log,
                max_retries=job.max_retries,
                retry_base_delay=job.retry_base_delay,
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
            job, spec, adapter, output_dir, report, log, cancel_event, progress_cb,
            pacer,
        )
    except ScrapeCancelled as exc:
        log(f"Run cancelled: {exc}")
        report.cancelled = True
        _collect_warnings(adapter, report)
        return report
    finally:
        if owns_rm and rm is not None:
            rm.close()


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
    """Indices eligible for the second-pass sweep: failed but not permanent."""
    return [i for i in report.failed if i not in report.permanent_failed]


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
        elif meta.index not in report.permanent_failed:
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
            elif meta.index not in report.permanent_failed:
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
