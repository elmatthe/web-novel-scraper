"""FreeWebNovel adapter (ENABLED).

Ported from the legacy ``freewebnovel-webscraper.py``, keeping only extraction
logic and dropping all prose editing. What is intentionally NOT carried over
(per the plan §3.2 / §5): ``_clean_ri_rni_paragraphs``, the Reverend-Insanity /
Renegade-Immortal title-form + em-dash + translator-credit *prose-editing*
regexes, ``EDITOR_MAP`` coupling, and the per-novel master-index lexicons. The
generic, extraction-level noise filtering (nav/comment stripping, a translator
credit-line noise filter, duplicate-heading dedup, whitespace cleanup) is kept
because that is extraction, not editing.

Networking is never done directly here — the adapter calls
``RequestManager.fetch(url, use_browser=spec.use_browser)``.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

from ..models import ChapterContent, ChapterMeta, SiteSpec
from .base import BaseAdapter

logger = logging.getLogger(__name__)

# ── Title / chapter-number regexes (ported) ──────────────────────────────────
# Accepts colon, dash, or bare-whitespace separators between the number and the
# title, and allows an empty title (the bare-breadcrumb case).
CHAPTER_TITLE_RE = re.compile(
    r"\bChapter\s+(\d+)(?:\s*[:\-]\s*|\s+)?(.*)$", re.IGNORECASE
)
# Looser "Chapter N" finder for index-page link text.
CHAPTER_LIST_TEXT_RE = re.compile(r"\bChapter\s+(-?\d+)\b", re.IGNORECASE)

# FreeWebNovel injects zero-width / format / non-breaking-space characters
# between words (notably U+200C Zero-Width Non-Joiner in Reverend Insanity
# titles). None of these are matched by ``\s``, so the title regex silently
# fails on every heading candidate — the 2261–2327 "Chapter N:." empty-title
# bug. Strip them (→ single space) before any whitespace-sensitive matching.
_INVISIBLE_CHARS = "​‌‍‬ ­﻿"
_INVISIBLE_RE = re.compile(f"[{_INVISIBLE_CHARS}]+")

# Translator/editor credit lines are navigation/credit noise, not body prose.
# This is an extraction-level noise filter (kept per plan §5), NOT the dropped
# RI/RNI prose-editing credit regex.
_CREDIT_LINE_RE = re.compile(
    r"^(translator|tl|editor|ed|proofreader|pr)\s*[:\-]\s*\S+",
    re.IGNORECASE,
)

# Navigation / chrome / comment snippets to drop during body extraction. The
# novel/brand-name entries from the legacy list (e.g. "shadow slave",
# "freewebnovel") are deliberately omitted — they can legitimately appear in
# body prose, and matching them as noise risks dropping real paragraphs.
NOISE_SNIPPETS = (
    "contact",
    "dmca",
    "privacy policy",
    "reader mode",
    "report chapter",
    "table of contents",
    "bookmark",
    "login",
    "register",
    "comment",
    "discussion",
    "next chapter",
    "previous chapter",
    "want to read more chapters",
    "text to speech",
    "add to library",
    "load more comments",
    "use arrow keys",
)

# Body-container selectors, best-score wins.
_BODY_CONTAINER_SELECTORS = (
    "#article",
    "article",
    "#chapter-content",
    ".chapter-content",
    ".entry-content",
    ".read-content",
    ".novel-content",
    ".chapter-body",
    ".chapter",
    "main",
)

# A discovered chapter count below this for a "known long novel" is suspicious
# and is surfaced as a warning (never silently trusted).
_COMPLETENESS_MIN = 10


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _strip_invisible(text: str) -> str:
    if not text:
        return ""
    return _ws(_INVISIBLE_RE.sub(" ", text))


def _normalize_url(url: str, base_url: str) -> str:
    absolute = urljoin(base_url, url)
    absolute, _ = urldefrag(absolute)
    return absolute


def _chapter_link_re(spec: SiteSpec) -> re.Pattern[str]:
    """Regex matching a chapter-URL path for this novel, derived from the
    index URL's path (generalized — no per-novel hardcoding)."""
    path = urlparse(spec.url).path.rstrip("/")
    return re.compile(
        re.escape(path) + r"/chapter-(\d+)(?:[/?#]|$)", re.IGNORECASE
    )


def chapter_url_for(spec: SiteSpec, n: int) -> str:
    """The fetch URL for chapter ``n``. Uses ``spec.url_template`` if set, else
    derives ``{index_url}/chapter-{n}`` from the catalog index URL."""
    if spec.url_template:
        return spec.url_template.format(n=n)
    return f"{spec.url.rstrip('/')}/chapter-{n}"


# ── Title extraction (ported, prose-editing removed) ─────────────────────────
def _candidate_title_texts(soup: BeautifulSoup) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        # Strip invisibles BEFORE adding so every consumer (regex + final
        # heading text) gets the cleaned form.
        cleaned = _strip_invisible(raw)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            candidates.append(cleaned)

    for tag in soup.select("span.chapter"):
        add(tag.get_text(" ", strip=True))

    meta_ch = soup.find("meta", attrs={"property": "og:novel:chapter_name"})
    if meta_ch and meta_ch.get("content"):
        add(meta_ch["content"])

    # Walk headings; get_text on the parent collapses split <strong>/<span>
    # children into one space-separated heading string.
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"], limit=30):
        add(tag.get_text(" ", strip=True))

    for sel in _BODY_CONTAINER_SELECTORS:
        for container in soup.select(sel):
            for child in container.find_all(["p", "div"], limit=10):
                txt = child.get_text(" ", strip=True)
                if not txt or len(txt) > 300:
                    continue
                if "chapter" in txt.lower():
                    add(txt)

    title_tag = soup.find("title")
    if title_tag:
        add(title_tag.get_text(" ", strip=True))

    for prop in ("og:title", "twitter:title"):
        meta = soup.find("meta", attrs={"property": prop}) or soup.find(
            "meta", attrs={"name": prop}
        )
        if meta and meta.get("content"):
            add(meta.get("content", ""))

    # Sibling-merge fallback: an inline "Chapter N" / "Chapter N:" with no title
    # — synthesize a merged candidate from the following sibling text.
    for tag in soup.find_all(
        ["strong", "span", "b", "p", "h1", "h2", "h3", "h4", "h5", "h6"]
    ):
        text = _strip_invisible(tag.get_text(" ", strip=True))
        if not text:
            continue
        m = CHAPTER_TITLE_RE.search(text)
        if not m:
            continue
        if (m.group(2) or "").strip():
            continue  # already titled — not a split case
        sib_text = ""
        nxt = tag.find_next_sibling()
        if nxt is not None:
            sib_text = _strip_invisible(nxt.get_text(" ", strip=True))
        if not sib_text:
            cur = tag.next_sibling
            while cur is not None and not sib_text:
                if hasattr(cur, "get_text"):
                    sib_text = _strip_invisible(cur.get_text(" ", strip=True))
                else:
                    sib_text = _strip_invisible(str(cur))
                cur = getattr(cur, "next_sibling", None)
        if not sib_text or len(sib_text) > 200:
            continue
        if CHAPTER_TITLE_RE.search(sib_text):
            continue  # sibling is itself a heading
        add(f"Chapter {m.group(1)}: {sib_text}")

    return candidates


def _normalize_heading_line(
    raw: str, fallback_num: Optional[int]
) -> tuple[Optional[int], str]:
    """Return ``(chapter_number, title_only)``. ``title_only`` is the trimmed
    title text (site junk removed) or ``""`` for a bare-number heading. Returns
    ``(None, "")`` when the text is not a chapter heading at all."""
    raw = _ws(raw)
    m = CHAPTER_TITLE_RE.search(raw)
    if not m:
        if not raw and fallback_num is not None:
            return fallback_num, ""
        return None, ""

    number = int(m.group(1))
    title = _ws(m.group(2))

    # Site-junk trimming only (NOT rewording).
    title = re.sub(r"\s*[|\-]\s*Empire\s*Novel.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*-\s*Read\s*Online.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*-\s*Free\s*Web\s*Novel.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*\|\s*Free\s*Web\s*Novel.*$", "", title, flags=re.IGNORECASE)
    title = title.strip(" -|:;.")
    return number, title


def _extract_title(
    soup: BeautifulSoup, fallback_num: Optional[int]
) -> tuple[Optional[int], str]:
    """Two-pass: prefer a candidate with BOTH a number and a non-empty title;
    fall back to a title-less (bare-breadcrumb) candidate only if nothing
    better exists. Robust to render variation between scrapes."""
    title_less: Optional[tuple[Optional[int], str]] = None
    for candidate in _candidate_title_texts(soup):
        num, title = _normalize_heading_line(candidate, fallback_num)
        if num is None:
            continue
        if title:
            return num, title
        if title_less is None:
            title_less = (num, "")
    if title_less is not None:
        return title_less
    return fallback_num, ""


# ── Body extraction (ported) ─────────────────────────────────────────────────
def _is_noise_paragraph(text: str, heading_line: str) -> bool:
    t = _ws(text)
    if not t:
        return True
    lower = t.lower()
    if lower == heading_line.lower().strip():
        return True
    if CHAPTER_TITLE_RE.match(t):          # duplicate-heading paragraph
        return True
    if _CREDIT_LINE_RE.match(t):           # translator/editor credit line
        return True
    if re.fullmatch(r"[\W_]+", t):
        return True
    if len(t) < 4:
        return True
    if len(t) < 12 and not (t.startswith('"') or t.startswith("'")):
        return True
    return any(snippet in lower for snippet in NOISE_SNIPPETS)


def _strip_embedded_watermarks(soup: BeautifulSoup) -> None:
    for tag in soup.find_all("subtxt"):
        tag.decompose()


def _extract_paragraphs(soup: BeautifulSoup, heading_line: str) -> list[str]:
    _strip_embedded_watermarks(soup)

    best_paras: list[str] = []
    best_score = -1

    for selector in _BODY_CONTAINER_SELECTORS:
        for node in soup.select(selector):
            paras = []
            for p in node.find_all("p"):
                txt = _ws(p.get_text(" ", strip=True))
                if _is_noise_paragraph(txt, heading_line):
                    continue
                paras.append(txt)
            score = sum(len(p) for p in paras)
            if score > best_score and paras:
                best_score = score
                best_paras = paras

    if not best_paras:
        paras = []
        for p in soup.find_all("p"):
            txt = _ws(p.get_text(" ", strip=True))
            if _is_noise_paragraph(txt, heading_line):
                continue
            paras.append(txt)
        best_paras = paras

    # Consecutive-duplicate paragraph dedup.
    deduped: list[str] = []
    last = None
    for para in best_paras:
        if para == last:
            continue
        deduped.append(para)
        last = para
    return deduped


def filter_duplicate_consecutive_chapters(
    chapters: list[ChapterContent], log=None
) -> list[ChapterContent]:
    """Drop a chapter whose body paragraphs are identical to the previous kept
    chapter's (a site URL-vs-content offset can serve the same content twice).
    The second copy is skipped with a warning rather than silently emitted."""
    out: list[ChapterContent] = []
    prev_paras: Optional[list[str]] = None
    for ch in chapters:
        if prev_paras is not None and ch.paragraphs == prev_paras:
            if log is not None:
                log(
                    f"DUPLICATE CONTENT: chapter {ch.index} has identical body "
                    "to the previous chapter; skipping."
                )
            continue
        out.append(ch)
        prev_paras = ch.paragraphs
    return out


class FreeWebNovelAdapter(BaseAdapter):
    """FreeWebNovel scraping adapter (all four FWN novels via one code path)."""

    key = "freewebnovel"
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
        """Discover the complete chapter list (TOC-first).

        **Approach B — generated URLs (the legacy bug fix).** The legacy
        scraper looked chapter URLs up in the map parsed from the landing page,
        but FreeWebNovel's index renders only a *slice* of the full list, so any
        chapter in the unrendered gap was silently dropped (the 2261–2327 bug).

        Instead, the index page is used ONLY to learn the highest chapter number
        (the "newest chapters" widget reliably shows the latest chapter), and
        the chapter URLs are then *generated* from a template over ``1..count``.
        This is gap-free by construction — ``range(1, count + 1)`` cannot skip a
        middle chapter regardless of what the landing page rendered.

        ``spec.chapter_count`` overrides the discovered count when set (and a
        disagreement with the index's highest number is surfaced as a warning,
        never silently honoured).
        """
        rm = self._manager(spec)
        html = rm.fetch(spec.url, use_browser=spec.use_browser)
        discovered = self._discover_max_chapter(html, spec)

        if spec.chapter_count is not None:
            count = spec.chapter_count
            if discovered is not None and discovered != count:
                self._warn(
                    f"chapter-count mismatch for {spec.novel_slug!r}: catalog "
                    f"override is {count} but the index page's highest chapter "
                    f"is {discovered}"
                )
        elif discovered is not None:
            count = discovered
        else:
            raise RuntimeError(
                f"Could not determine the chapter count for {spec.novel_slug!r} "
                "from the index page."
            )

        if count < _COMPLETENESS_MIN:
            self._warn(
                f"only {count} chapters discovered for {spec.novel_slug!r} — "
                "suspiciously low for a full novel; verify the index page"
            )

        return [
            ChapterMeta(index=n, url=chapter_url_for(spec, n))
            for n in range(1, count + 1)
        ]

    def _discover_max_chapter(
        self, html: str, spec: SiteSpec
    ) -> Optional[int]:
        soup = BeautifulSoup(html, "html.parser")
        link_re = _chapter_link_re(spec)
        base = spec.base_url or spec.url
        numbers: list[int] = []

        for a in soup.find_all("a", href=True):
            href = _normalize_url(a["href"], base)
            m = link_re.search(href)
            if m:
                numbers.append(int(m.group(1)))

        if not numbers:
            # Fallback: scan link text for "Chapter N".
            for a in soup.find_all("a"):
                m = CHAPTER_LIST_TEXT_RE.search(_ws(a.get_text(" ", strip=True)))
                if m:
                    n = int(m.group(1))
                    if n >= 1:
                        numbers.append(n)

        return max(numbers) if numbers else None

    def fetch_chapter(self, meta: ChapterMeta, spec: SiteSpec) -> ChapterContent:
        rm = self._manager(spec)
        html = rm.fetch(meta.url, use_browser=spec.use_browser)
        return self._extract_chapter(html, meta)

    def _extract_chapter(self, html: str, meta: ChapterMeta) -> ChapterContent:
        soup = BeautifulSoup(html, "html.parser")
        _number, title = _extract_title(soup, meta.index)

        # Heading text used only for noise filtering (exact-match dedup).
        heading_for_filter = (
            f"Chapter {meta.index}: {title}." if title else f"Chapter {meta.index}."
        )
        paragraphs = _extract_paragraphs(soup, heading_for_filter)
        if not paragraphs:
            raise RuntimeError(
                f"Could not extract body paragraphs for chapter {meta.index}."
            )

        if not title:
            # Degraded: content is kept; ChapterContent renders "Chapter N."
            self._warn(
                f"chapter {meta.index} — no title text found in any heading "
                "candidate (content written, heading will be 'Chapter N.')"
            )

        return ChapterContent(
            index=meta.index, title=title or "", paragraphs=paragraphs
        )
