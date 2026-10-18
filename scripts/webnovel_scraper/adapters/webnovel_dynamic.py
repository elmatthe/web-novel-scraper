"""WebNovel-dynamic adapter (ENABLED).

Ported from the legacy ``scrape_noble_queen-v3.py``. The dynamic WebNovel host
(``dynamic.webnovel.com``) is a Next.js app that embeds the entire chapter list
*and* each chapter body in a ``<script id="__NEXT_DATA__">`` JSON blob — far more
stable than CSS scraping. This adapter prefers that JSON and only falls back to
DOM selectors when the blob is missing or shaped unexpectedly. Plain HTTP works
here (no Cloudflare challenge), so ``use_browser`` stays False.

What is intentionally NOT carried over (per plan §4.8 / §5): the do-not-touch
protected-name lexicon (``load_do_not_touch`` / ``apply_with_guard`` /
``_NQ_MASTER_INDEX``), the Unicode/decorative replacement table
(``V2_DECORATIVE_REPLACEMENTS``), and the per-paragraph prose editors
(``_clean_text_chars`` / ``clean_text`` / ``normalize_paragraph``). Only
whitespace/empty-paragraph trimming and nav/comment junk filtering are kept —
that is extraction, not editing. The novel-specific ``_is_junky`` brand guard
(``"noble queen" + "shadow slave"``) is dropped for the same reason the
FreeWebNovel adapter dropped brand-name noise: it can appear in legitimate prose.

Networking is never done directly here — the adapter calls
``RequestManager.fetch(url, use_browser=spec.use_browser)``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

from ..models import ChapterContent, ChapterIndex, ChapterMeta, EmptyExtractionError, SiteSpec
from ..content import clean_paragraphs, clean_title, is_site_chrome, label_text, normalize_text
from .base import BaseAdapter, extract_validated

logger = logging.getLogger(__name__)

# Default dynamic host, used only if a spec carries no base_url.
_DEFAULT_HOST = "https://dynamic.webnovel.com"

# v0.3.0 05: the legacy JUNK_KEYWORDS substring filter ("comment", "report", "swipe", ...) is gone.
# Measured on the 890 cached Noble Queen chapters it deleted 381 real story paragraphs ("He
# reported...", "her husband's comment made sense"), and no reader-interface line repeats in the
# chapter container at all. Only whole-paragraph interface text is dropped now
# (``content.is_site_chrome``).

# Fallback DOM selectors for the chapter body, used only when __NEXT_DATA__ does
# not yield content (ported verbatim from the legacy CONTENT_SELECTORS).
CONTENT_SELECTORS = (
    "[class*='ChapterContent_content']",
    "[class*='ChapterContent_container']",
    "div.cha-words",
    "div.cha-content",
    "[class*='ChapterContent']",
    "article",
    "main",
)

# DOM title selectors for the fallback path.
_TITLE_SELECTORS = (
    "[class*='ChapterContent_title']",
    "h1",
    ".cha-tit",
    ".chapter-title",
)


def _ws(text: str) -> str:
    """Collapse whitespace and strip — the only text cleanup we apply."""
    return re.sub(r"\s+", " ", text or "").strip()


def parse_next_data(html: str) -> Optional[dict]:
    """Return the parsed ``__NEXT_DATA__`` JSON, or None if absent/unparseable.

    Ported verbatim from the legacy scraper.
    """
    soup = BeautifulSoup(html, "html.parser")
    nd = soup.find("script", id="__NEXT_DATA__")
    if not nd or not nd.string:
        return None
    try:
        return json.loads(nd.string)
    except Exception:
        return None


def _clean_js_object_literal(raw: str) -> str:
    """Make WebNovel's JSON-like assignment payload acceptable to json.loads.

    The current live site escapes ordinary spaces/punctuation as ``\\ `` and
    ``\\&`` inside strings. JavaScript accepts those escape sequences; JSON does
    not. Preserve valid JSON escapes and strip only the invalid backslash.
    """
    return re.sub(r'\\(?!["\\/bfnrtu])', "", raw)


def _extract_js_assignment(html: str, name: str) -> Optional[dict]:
    """Parse ``name = {...}`` from a script tag, returning a dict when possible."""
    marker = html.find(name)
    if marker < 0:
        return None
    eq = html.find("=", marker)
    if eq < 0:
        return None
    start = html.find("{", eq)
    if start < 0:
        return None

    depth = 0
    in_string = False
    quote = ""
    escaped = False
    for pos in range(start, len(html)):
        ch = html[pos]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                in_string = False
            continue
        if ch in ("'", '"'):
            in_string = True
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                raw = html[start : pos + 1]
                try:
                    return json.loads(_clean_js_object_literal(raw))
                except Exception:
                    return None
    return None


def parse_g_data_book(html: str) -> Optional[dict]:
    """Return current live WebNovel ``g_data.book`` payload, if present."""
    return _extract_js_assignment(html, "g_data.book")


def parse_g_data_chapter(html: str) -> Optional[dict]:
    """Return current live WebNovel ``g_data.chapInfo`` payload, if present."""
    return _extract_js_assignment(html, "g_data.chapInfo")


def _is_junky(text: str) -> bool:
    """True only for a whole paragraph of reader-interface text (never a substring match)."""
    return is_site_chrome(text)


def _content_to_text(raw: str) -> str:
    """Convert a WebNovel content field to plain paragraph text (inline markup joined as read)."""
    raw = str(raw or "")
    if "<" in raw and ">" in raw:
        soup = BeautifulSoup(raw, "html.parser")
        for br in soup.find_all("br"):
            br.replace_with(" ")
        return normalize_text(soup.get_text())
    return normalize_text(raw)


def extract_chapter(
    html: str, fallback_index: int
) -> tuple[Optional[str], list[str]]:
    """Return ``(raw_chapter_name, paragraphs)`` for one chapter.

    Primary path: ``__NEXT_DATA__.props.pageProps.data.chapterInfo.contents[]``.
    DOM fallback: BeautifulSoup over ``CONTENT_SELECTORS`` when the JSON path
    yields no usable content. Junk paragraphs are dropped either way; only
    whitespace/empty trimming is applied to the survivors.
    """
    data = parse_next_data(html)
    if data:
        try:
            ci = data["props"]["pageProps"]["data"]["chapterInfo"]
            contents = ci.get("contents") or []
            paragraphs = [
                _content_to_text(str(c.get("content") or ""))
                for c in contents
                if c and _content_to_text(str(c.get("content") or ""))
            ]
            paragraphs = [p for p in paragraphs if not _is_junky(p)]
            if paragraphs:
                chapter_name = ci.get("chapterName") or None
                return chapter_name, paragraphs
        except (KeyError, TypeError):
            pass

    ci = parse_g_data_chapter(html)
    if ci:
        contents = ci.get("contents") or []
        paragraphs = [
            _content_to_text(str(c.get("content") or ""))
            for c in contents
            if c and _content_to_text(str(c.get("content") or ""))
        ]
        paragraphs = [p for p in paragraphs if not _is_junky(p)]
        if paragraphs:
            chapter_name = ci.get("chapterName") or None
            return chapter_name, paragraphs

    # DOM fallback.
    soup = BeautifulSoup(html, "html.parser")
    container = None
    for sel in CONTENT_SELECTORS:
        container = soup.select_one(sel)
        if container:
            break
    if container is None:
        return None, []

    paras = [_content_to_text(str(p)) for p in container.find_all("p")]
    paras = [p for p in paras if p and not _is_junky(p)]

    title_text: Optional[str] = None
    for sel in _TITLE_SELECTORS:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            title_text = el.get_text(" ", strip=True)
            break
    return title_text, paras


class WebNovelDynamicAdapter(BaseAdapter):
    """WebNovel-dynamic scraping adapter (The Noble Queen and any dynamic-host
    novel via one code path)."""

    key = "webnovel_dynamic"
    enabled = True

    def __init__(self, request_manager=None, log=None) -> None:
        self._rm = request_manager
        self._log = log or logger.info
        self.warnings: list[str] = []

    # ── helpers ──────────────────────────────────────────────────────────────
    def _manager(self, spec: SiteSpec):
        if self._rm is None:
            from ..request_manager import RequestManager

            self._rm = RequestManager(slug=spec.novel_slug)
        return self._rm

    def _warn(self, msg: str) -> None:
        self.warnings.append(msg)
        self._log(f"WARNING: {msg}")

    # ── contract ─────────────────────────────────────────────────────────────
    def build_chapter_index(self, spec: SiteSpec) -> list[ChapterMeta]:
        """Discover the complete chapter list from the ``__NEXT_DATA__`` TOC.

        The novel's main page embeds ``props.pageProps.data.volumeItems[]``, each
        with ``chapterItems[]``; flattening those gives the full, ordered list
        directly (no pagination, no map-lookup gap risk).

        This adapter is a **stateless** TOC builder: it always re-parses the live
        ``__NEXT_DATA__`` and keeps no chapter-index cache of its own. TOC
        persistence and resume are owned solely by the pipeline (output-dir-scoped
        ``chapter_index.json``), so a fresh run into a new output dir always sees
        the *current* chapter count and can never be silently served a stale TOC
        from a prior run (the Phase 8 bug-hunt fix for the old slug-scoped cache).

        Raises a clear error if ``__NEXT_DATA__`` is missing/malformed or the TOC
        parses to an empty list — never silently returns an empty index.
        """
        rm = self._manager(spec)
        # Always fresh and never cached (v0.3.0 03.2b): this page is where new chapters appear.
        html = rm.fetch(spec.url, use_browser=spec.use_browser, use_cache=False)

        data = parse_next_data(html)
        if data:
            try:
                d = data["props"]["pageProps"]["data"]
                book = d.get("bookInfo") or {}
                volumes = d.get("volumeItems") or []
            except (KeyError, TypeError) as exc:
                raise RuntimeError(
                    f"Unexpected TOC __NEXT_DATA__ shape for {spec.novel_slug!r}: {exc}"
                ) from exc
        else:
            d = parse_g_data_book(html)
            if not d:
                raise RuntimeError(
                    f"No supported TOC payload (__NEXT_DATA__ or g_data.book) on "
                    f"the TOC page for {spec.novel_slug!r} ({spec.url})."
                )
            book = d.get("bookInfo") or {}
            volumes = d.get("volumeItems") or []

        book_id = str(book.get("bookId") or spec.book_id or "")
        expected = int(book.get("chapterCnt") or book.get("chapterNum") or 0)
        base = (spec.base_url or _DEFAULT_HOST).rstrip("/")

        metas: list[ChapterMeta] = []
        for vol in volumes:
            for it in (vol or {}).get("chapterItems") or []:
                try:
                    idx = int(it["chapterIndex"])
                except (KeyError, TypeError, ValueError):
                    continue
                cid = str(it.get("chapterId") or "")
                if not cid:
                    continue
                cname = str(it.get("chapterName") or f"Chapter {idx}")
                metas.append(
                    ChapterMeta(
                        index=idx,
                        url=f"{base}/story/{book_id}/{cid}",
                        title=cname,
                        source_id=cid,
                    )
                )

        metas.sort(key=lambda m: m.index)

        if not metas:
            raise RuntimeError(
                f"TOC parsed for {spec.novel_slug!r} but produced an empty "
                "chapter list."
            )

        # Completeness checks — surfaced as warnings, never a silent skip.
        gaps = [
            metas[i].index
            for i in range(1, len(metas))
            if metas[i].index != metas[i - 1].index + 1
        ]
        if gaps:
            self._warn(
                f"chapter index gaps detected for {spec.novel_slug!r} at "
                f"{gaps[:10]}{'...' if len(gaps) > 10 else ''}"
            )
        if expected and len(metas) != expected:
            self._warn(
                f"scraped chapter count ({len(metas)}) does not match "
                f"bookInfo.chapterCnt ({expected}) for {spec.novel_slug!r}"
            )

        self._log(
            f"Found {len(metas)} chapters for {spec.novel_slug!r} "
            f"(range {metas[0].index}..{metas[-1].index})."
        )
        # Fewer chapters than the site's own count means the list may be cut short, so its last
        # chapter must not be taken as the novel's end (v0.3.0 End clamping, ADR 011).
        return ChapterIndex(metas, tail_confirmed=not (expected and len(metas) < expected))

    def fetch_chapter(self, meta: ChapterMeta, spec: SiteSpec) -> ChapterContent:
        rm = self._manager(spec)
        return extract_validated(
            rm, meta, lambda: rm.fetch(meta.url, use_browser=spec.use_browser),
            self._extract_chapter, self._log,
        )

    def _extract_chapter(self, html: str, meta: ChapterMeta) -> ChapterContent:
        raw_title, paragraphs = extract_chapter(html, meta.index)

        if not paragraphs:
            # Fully-fetched page, no extractable body: an extraction failure (its
            # own class), NOT a Cloudflare block — see EmptyExtractionError.
            raise EmptyExtractionError(
                f"Could not extract body paragraphs for chapter {meta.index} "
                f"({meta.url})."
            )

        # Prefer the index's chapter_name (meta.title) as the canonical title
        # source; fall back to whatever the page yielded. Then reduce to the
        # bare title — ChapterContent.heading applies the shared "Chapter N:"
        # wrap.
        label = label_text(meta.index)
        title = clean_title(meta.title or "", label) or clean_title(raw_title or "", label)
        paragraphs = clean_paragraphs(paragraphs, label=label, title=title)
        if not paragraphs:
            raise EmptyExtractionError(
                f"Could not extract body paragraphs for chapter {meta.index} ({meta.url})."
            )

        if not title:
            self._warn(
                f"chapter {label} — no title text found (content written, "
                f"heading will be 'Chapter {label}: Untitled')"
            )

        return ChapterContent(
            index=meta.index, title=title, paragraphs=paragraphs, label=label
        )
