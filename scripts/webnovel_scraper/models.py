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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .chapter_manifest import ChapterManifest
    from .chapter_verification import Verification


class EmptyExtractionError(Exception):
    """An adapter fully fetched a page but found no body paragraphs in it.

    This is its OWN failure class, distinct from a Cloudflare block / network
    error: the page came back (a real, non-challenge response) but the body
    container had nothing extractable — usually a markup change on the site. The
    pipeline records it as a plain chapter failure and must NOT treat it as a
    block/challenge (so it never triggers auto-slowdown or the second-pass sweep).
    """

    reason_code = "EMPTY_BODY"

    def __init__(self, message: str, *, requested_label: str | None = None,
                 observed_labels: tuple[str, ...] = (), page_url: str | None = None):
        super().__init__(message)
        self.requested_label = requested_label
        self.observed_labels = tuple(observed_labels)
        self.page_url = page_url


class ChapterMismatchError(EmptyExtractionError):
    """The page was fetched and has a body, but it shows a different chapter than the chapter
    list expects at that address (the site's numbering moved, or the wrong page was served).

    An extraction-class failure: never a block, never swept, and never published as if it were
    the expected chapter. The usual cure is an explicit chapter-list refresh."""

    reason_code = "IDENTITY_MISMATCH"


class ChapterIdentityUnknownError(ChapterMismatchError):
    """No positive numbered page identity could be authenticated."""

    reason_code = "IDENTITY_UNKNOWN"


class ChapterIdentityConflictError(ChapterMismatchError):
    """Independent numbered headings on one page disagree."""

    reason_code = "IDENTITY_CONFLICT"


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

    ``index`` is the chapter number the SITE DISPLAYS in its page header — the
    number a user types into the range fields — used for ordering, filenames,
    resume, and the range clamp. It is an ``int`` for ordinary chapters and a
    ``float`` for fractional side-chapters (FreeWebNovel's "Chapter 627.2"
    inserts), which sort naturally between their integer neighbours.

    ``slug`` is the site's URL slug number (the ``N`` in ``…/chapter-N``) the
    page is actually fetched at. On some novels the slug sequence counts insert
    pages (glossaries, side-chapters) the displayed numbering does not, so slug
    and displayed number drift apart; carrying both keeps fetching in slug-space
    and everything user-facing in displayed-space. ``None`` where the adapter
    does not use slug-numbered URLs (e.g. WebNovel-dynamic).
    """

    index: int | float         # displayed chapter number (fractional for .x inserts)
    url: str                   # absolute URL to fetch the chapter from
    title: str | None = None   # title text only (site-junk trimmed), or None
    source_id: str | None = None       # site's own chapter id, if parsed
    slug: int | None = None            # URL slug number the chapter is fetched at
    extra: dict = field(default_factory=dict)  # adapter-specific fetch hints

    @property
    def is_degraded(self) -> bool:
        return not (self.title and self.title.strip())


@dataclass(frozen=True)
class DiscoveryEvidence:
    """Discovery facts; persistence additionally requires the versioned page-observation policy.

    Only ``observed`` with current-page provenance certifies an identity. Candidates,
    saved_unknown, conflict, unreadable, inaccessible, extra and unknown describe
    different outcomes. ``source_absent`` requires independent source evidence;
    a missing URL never proves a displayed chapter absent.
    """

    status: str
    provenance: str
    labels: tuple[str, ...] = ()
    detail: str = ""

    @property
    def verified(self) -> bool:
        return self.status == "observed" and self.provenance == "current-page"


class ChapterIndex(list):
    """A discovered chapter list (``ChapterMeta`` rows) that also says whether the adapter could
    confirm it runs to the site's real last chapter. ``tail_confirmed=False`` means the list may be
    short (for example the site's own chapter count disagrees with it), so the preflight never uses
    its last chapter to clamp a requested End. A plain list counts as confirmed.

    ``examined`` is set by an adapter that reads only part of a paginated list: the chapter-number
    spans of the parts it read. A number missing between two of them was never looked at, so it is
    not a gap in the site's list. ``None`` means the whole list was read."""

    def __init__(self, items=(), *, tail_confirmed: bool = True,
                 examined: tuple[tuple[int, int], ...] | None = None,
                 evidence: dict[str, DiscoveryEvidence] | None = None,
                 discovery_complete: bool = False, verification: Verification | None = None,
                 verified_manifest: ChapterManifest | None = None, recheck_tail: bool = False) -> None:
        super().__init__(items)
        self.tail_confirmed = tail_confirmed
        self.examined = examined
        self.evidence = evidence or {}
        self.discovery_complete = discovery_complete
        self.verification = verification
        self.verified_manifest = verified_manifest
        self.recheck_tail = recheck_tail


@dataclass
class ChapterContent:
    """A fully extracted chapter: resolved title + cleaned body paragraphs (the one canonical
    content both TXT and PDF render, v0.3.0 Checkpoint 05)."""

    index: int | float         # displayed chapter number (fractional for .x inserts;
    #                            renders naturally in ``heading``: "Chapter 627.2: …")
    title: str                 # resolved title (may be "" for a degraded chapter)
    paragraphs: list[str]      # cleaned body paragraphs (no nav/comment noise)
    label: str | None = None   # exact label text when it differs from ``index`` ("627.10")

    @property
    def is_degraded(self) -> bool:
        return not (self.title and self.title.strip())

    @property
    def label_text(self) -> str:
        from .content import label_text

        return label_text(self.label if self.label else self.index)

    @property
    def heading(self) -> str:
        """The one heading line, ``Chapter N: Title`` (``Chapter N: Untitled`` when the title is
        unknown). No period is added."""
        from .content import heading_for

        return heading_for(self.label_text, self.title or "")

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
    # ── 0.3.0 03.2a ──────────────────────────────────────────────────────────
    # One-shot, explicit: back up the saved runtime chapter list (never the tracked
    # baseline) and rediscover it for this run. Never set silently; the GUI clears it
    # after every run.
    refresh_chapter_list: bool = False
    # GUI explicitly chooses saved/update. "auto" retains discovery behavior for
    # existing developer callers that have not selected a list policy.
    chapter_list_mode: str = "auto"
    # ── 0.3.0 03.2b ──────────────────────────────────────────────────────────
    # One-shot, explicit: fetch every selected chapter page from the site again instead of
    # serving it from the HTML cache; the fresh valid pages replace the cached ones.
    refresh_html_cache: bool = False
    # ── 0.3.0 03.3 ───────────────────────────────────────────────────────────
    # Output formats, in render order ("pdf", "txt"); at least one. Both share one set of groups.
    formats: tuple = ("pdf",)
    # True: ``output_dir`` is the output ROOT and the pipeline reserves a fresh
    # ``<novel-slug>-N`` run folder inside it once the chapter list is settled (a refused run
    # reserves nothing). False: ``output_dir`` is the run folder itself.
    allocate_run: bool = False
    # True: ``output_dir`` is an earlier run folder to continue. Its journal, not this job's
    # range, decides which pages are fetched; formats and grouping must match the journal.
    resume: bool = False

    def __post_init__(self) -> None:
        formats = tuple(self.formats)
        if self.chapter_list_mode not in {"auto", "saved", "update"}:
            raise ValueError("chapter_list_mode must be auto, saved or update.")
        if not formats or any(f not in ("pdf", "txt") for f in formats) or len(set(formats)) != len(formats):
            raise ValueError(f"formats must be one or both of 'pdf' and 'txt' (got {self.formats!r}).")
        self.formats = formats
        if self.allocate_run and self.resume:
            raise ValueError("a job either starts a new run or resumes one, not both.")
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
