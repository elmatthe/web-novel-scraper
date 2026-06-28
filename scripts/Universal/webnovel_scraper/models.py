"""Normalized, source-agnostic data models for webnovel_scraper.

These are pure in-memory data holders with no I/O and no side effects at
import time. Adapters produce `ChapterMeta`/`ChapterContent`; the pipeline and
PDF builder consume only these. `SiteSpec` is one catalog row (a novel on a
specific site); `ScrapeJob` is one GUI-initiated run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


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
    use_browser: bool = False          # default HTTP path; browser only on CF challenge
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
    # Relentless per-chapter retry (Phase 9C): retries AFTER the first attempt, so
    # 6 means up to 7 escalating attempts per chapter before it is recorded failed.
    # On top of this, the pipeline runs a second-pass sweep over the failed list
    # once the main range completes. Generous on purpose for long unattended runs.
    max_retries: int = 6
    retry_base_delay: float = 5.0

    def __repr__(self) -> str:
        return (
            f"ScrapeJob({self.novel_slug!r} via {self.adapter_key!r}, "
            f"ch {self.start}-{self.end}, {self.output_mode.value})"
        )
