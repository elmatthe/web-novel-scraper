"""The SiteAdapter abstract base class + normalized return-type contract.

Every site adapter subclasses `BaseAdapter`. Networking lives in the request
manager (passed in / injected later), parsing lives in the adapter, and the PDF
builder knows nothing about sites. Stub adapters set ``enabled = False`` and
raise from both abstract methods; the registry refuses to hand out a disabled
adapter, and the raise is the backstop.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Callable

from ..models import ChapterContent, ChapterMeta, EmptyExtractionError, SiteSpec

# Characters illegal in Windows filenames plus the POSIX path separators, so a
# chapter title can never create a subdirectory or break a path on any OS.
_ILLEGAL_FILENAME_CHARS = re.compile(r'[:*?"<>|/\\]+')
_WHITESPACE = re.compile(r"\s+")


def extract_validated(
    rm: object,
    meta: ChapterMeta,
    fetch: Callable[[], str],
    extract: Callable[[str, ChapterMeta], ChapterContent],
    log: Callable[[str], None],
) -> ChapterContent:
    """Fetch and extract one chapter so the HTML cache only ever keeps usable pages (03.2b).

    A page that yields no body or the wrong chapter is set aside in the cache (when the request
    manager has one). If it had come *from* the cache it is fetched once more from the site; a
    second failure is the chapter's failure, so a bad page can never cause a refetch loop.
    """
    invalidate = getattr(rm, "invalidate_cache", None)
    html = fetch()
    info = getattr(rm, "last_fetch_info", None)
    from_cache = bool(getattr(info, "from_cache", False))
    try:
        return extract(html, meta)
    except EmptyExtractionError as exc:
        if callable(invalidate):
            invalidate(meta.url, str(exc))
        if not from_cache:
            raise
        log(f"  chapter {meta.index}: the cached page could not be used ({exc}); fetching it again.")
    html = fetch()
    try:
        return extract(html, meta)
    except EmptyExtractionError as exc:
        if callable(invalidate):
            invalidate(meta.url, str(exc))
        raise


class BaseAdapter(ABC):
    """Abstract base for all site adapters.

    Subclasses set ``key`` (must match the catalog ``adapter_key``) and
    ``enabled`` (whether a real implementation exists yet).
    """

    key: str = ""
    enabled: bool = True

    @abstractmethod
    def build_chapter_index(self, spec: SiteSpec) -> list[ChapterMeta]:
        """TOC-first discovery: return ordered, de-duplicated chapter metas."""
        raise NotImplementedError

    @abstractmethod
    def fetch_chapter(self, meta: ChapterMeta, spec: SiteSpec) -> ChapterContent:
        """Fetch + parse one chapter into normalized content."""
        raise NotImplementedError

    # ── shared, non-abstract helpers ────────────────────────────────────────
    def is_enabled(self, spec: SiteSpec) -> bool:
        """Whether this novel/site row is enabled (catalog-driven)."""
        return spec.enabled

    @staticmethod
    def safe_filename(title: str, max_len: int = 120) -> str:
        """Port of ``_safe_filename`` from the legacy FreeWebNovel scraper.

        Replaces illegal filename characters (and path separators, for
        cross-platform safety) with ``_``, collapses whitespace, truncates, and
        never returns empty.
        """
        cleaned = _ILLEGAL_FILENAME_CHARS.sub("_", title).strip()
        cleaned = _WHITESPACE.sub(" ", cleaned)
        return cleaned[:max_len].strip() or "untitled"
