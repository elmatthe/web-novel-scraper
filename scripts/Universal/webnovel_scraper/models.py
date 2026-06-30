"""Normalized, source-agnostic data models for webnovel_scraper.

These are pure in-memory data holders with no I/O and no side effects at
import time. Adapters produce `ChapterMeta`/`ChapterContent`; the pipeline and
PDF builder consume only these. `SiteSpec` is one catalog row (a novel on a
specific site); `ScrapeJob` is one GUI-initiated run.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path


class EmptyExtractionError(Exception):
    """An adapter fully fetched a page but found no body paragraphs in it.

    This is its OWN failure class, distinct from a Cloudflare block / network
    error: the page came back (a real, non-challenge response) but the body
    container had nothing extractable — usually a markup change on the site. The
    pipeline records it as a plain chapter failure and must NOT treat it as a
    block/challenge (so it never triggers auto-slowdown or the second-pass sweep).
    """


class OutputMode(str, Enum):
    """How chapters are grouped into PDF files."""

    SEPARATE = "separate"  # one PDF per chapter
    CHUNKED = "chunked"    # N chapters per PDF
    SINGLE = "single"      # one PDF for the whole range


@dataclass
class SiteSpec:
    """One catalog row: a single novel as available on a single site.

    Carries the novel association (so the catalog can be a flat data list) plus
    any site-specific config the adapter needs to build URLs and fetch.
    """

    novel_slug: str            # e.g. "shadow-slave" — catalog key for the novel
    novel_title: str           # human display title, e.g. "Shadow Slave"
    adapter_key: str           # registry key, e.g. "freewebnovel"
    display_name: str          # site label for the GUI, e.g. "Free Web Novel"
    enabled: bool              # False -> disabled/stub site, greyed in the GUI
    url: str                   # the novel index / catalog URL on this site
    book_id: str | None = None        # site's own book id (e.g. webnovel_dynamic)
    url_template: str | None = None    # optional chapter-URL template, "{n}" slot
    use_browser: bool = False          # True -> browser-primary (headful camoufox
    #                                    from request #1, the FreeWebNovel default in
    #                                    0.1.3). False -> HTTP path (e.g. the
    #                                    Cloudflare-free WebNovel-dynamic site).
    base_url: str = ""                 # site origin, e.g. "https://freewebnovel.com"
    chapter_count: int | None = None   # authoritative chapter-count override (else
    #                                    derived from the index page's highest number)

    def __repr__(self) -> str:  # concise, debug-friendly
        state = "enabled" if self.enabled else "disabled"
        return (
            f"SiteSpec({self.novel_slug!r} via {self.adapter_key!r}, {state})"
        )


@dataclass
class ChapterMeta:
    """A TOC entry discovered before the chapter body is fetched.

    `title` may be None/empty, which marks a degraded chapter (no title text
    was discoverable); the body is still fetched and written.
    """

    index: int                 # 1-based chapter index used for ordering/filenames
    url: str                   # absolute URL to fetch the chapter from
    title: str | None = None   # title text only (site-junk trimmed), or None
    source_id: str | None = None       # site's own chapter id, if parsed
    extra: dict = field(default_factory=dict)  # adapter-specific fetch hints

    @property
    def is_degraded(self) -> bool:
        return not (self.title and self.title.strip())


@dataclass
class ChapterContent:
    """A fully extracted chapter: resolved title + cleaned body paragraphs."""

    index: int                 # 1-based chapter index
    title: str                 # resolved title (may be "" for a degraded chapter)
    paragraphs: list[str]      # cleaned body paragraphs (no nav/comment noise)

    @property
    def is_degraded(self) -> bool:
        return not (self.title and self.title.strip())

    @property
    def heading(self) -> str:
        """Normalized PDF header line, 'Chapter N: Title.' (or 'Chapter N.')."""
        if self.is_degraded:
            return f"Chapter {self.index}."
        return f"Chapter {self.index}: {self.title.strip()}."

    @property
    def raw_text(self) -> str:
        """'heading\\n\\nbody...' — the text fed to the PDF builder."""
        return "\n\n".join([self.heading, *self.paragraphs])


@dataclass
class ScrapeJob:
    """One scrape run, as configured in the GUI."""

    novel_slug: str
    adapter_key: str
    start: int                 # first chapter (inclusive)
    end: int                   # last chapter (inclusive)
    delay: float               # per-fetch delay, seconds
    output_mode: OutputMode
    use_cache: bool
    output_dir: Path
    chunk_size: int = 10       # chapters per PDF when output_mode is CHUNKED
    # Per-chapter retry budget (retries AFTER the first attempt) for the NON-browser
    # (HTTP / opt-in) path. The browser-primary path (FreeWebNovel default) ignores
    # this and caps itself to its short bounded ladder (a couple of same-page
    # retries + ≤1 fresh-page recovery) so it never replays the six-engine storm.
    # On top of either, the pipeline runs ONE second-pass sweep over the failed
    # list once the main range completes.
    max_retries: int = 6
    retry_base_delay: float = 5.0
    # Opt-in: try two cheap HTTP rungs before camoufox on the browser-primary path.
    # Default False — plain HTTP trips FreeWebNovel's Cloudflare, which is exactly
    # why the browser is primary. Threaded into the RequestManager as try_http_first.
    http_first: bool = False
    # ── 0.2.0 run-config (§3.14) ─────────────────────────────────────────────
    # The run's behaviour now travels on the (immutable) job rather than being
    # inferred from a mutated SiteSpec or a module-level global. The pipeline makes
    # a per-run SiteSpec copy from ``use_browser`` (see ``runtime_site_spec``); the
    # FWN rescue scope gate is ``adapter_key == "freewebnovel" and use_browser``.
    use_browser: bool = False          # browser-primary path for this run
    headless: bool = False             # primary browser headless (False = visible)
    request_timeout: float = 30.0      # ordinary HTTP/nav timeout, seconds (§3.15);
    #                                    the 180s rescue deadline is a SEPARATE
    #                                    internal ceiling, not this field.
    rescue_workers: int = 1            # 0.2.0 is single-lane — validated == 1 below
    #                                    (the 1–5 toggle is DEFERRED to 0.2.1, §9).

    def __post_init__(self) -> None:
        # 0.2.0 invariant #1: strictly single-lane rescue. Reject any other count
        # here so a stray value can never spin up a second worker (the multi-worker
        # design is 0.2.1, plan §9).
        if self.rescue_workers != 1:
            raise ValueError(
                "0.2.0 is single-lane: rescue_workers must be 1 "
                f"(got {self.rescue_workers!r}); 1–5 workers are deferred to 0.2.1."
            )

    def __repr__(self) -> str:
        return (
            f"ScrapeJob({self.novel_slug!r} via {self.adapter_key!r}, "
            f"ch {self.start}-{self.end}, {self.output_mode.value})"
        )


def runtime_site_spec(spec: SiteSpec, job: "ScrapeJob") -> SiteSpec:
    """Return a per-run :class:`SiteSpec` copy whose ``use_browser`` reflects the job.

    The catalog's ``SiteSpec`` rows are shared, long-lived data — mutating one to
    carry a run's browser choice (the pre-0.2.0 behaviour) leaks that choice into
    later runs and across threads. Instead the pipeline builds a throwaway copy via
    ``dataclasses.replace`` and passes *that* to TOC discovery and every chapter
    fetch, so ``ScrapeJob.use_browser`` actually drives fetching while the catalog
    row is left untouched (§3.14).
    """
    return replace(spec, use_browser=job.use_browser)
