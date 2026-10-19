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
from decimal import Decimal
from typing import Callable, Optional
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

from ..content import (
    CHAPTER_WORD,
    clean_paragraphs,
    clean_title,
    is_site_chrome,
    label_text,
    normalize_text,
    parse_heading,
    strip_promotions,
)
from ..models import (
    ChapterContent,
    ChapterMeta,
    ChapterMismatchError,
    EmptyExtractionError,
    SiteSpec,
)
from ..source_identity import ChapterLabel
from .base import BaseAdapter, extract_validated

logger = logging.getLogger(__name__)

# ── Title / chapter-number regexes (ported) ──────────────────────────────────
# Accepts colon, dash, or bare-whitespace separators between the number and the
# title, and allows an empty title (the bare-breadcrumb case). The number may be
# fractional ("Chapter 627.2" — FreeWebNovel's side-chapter inserts); a bare
# trailing dot ("Chapter 100. The End") is NOT a fraction and stays title junk.
CHAPTER_TITLE_RE = re.compile(
    rf"\b{CHAPTER_WORD}\s+(\d+(?:\.\d+)?)(?:\s*[:\-]\s*|\s+)?(.*)$", re.IGNORECASE
)
# Looser "Chapter N" finder for index-page link text.
CHAPTER_LIST_TEXT_RE = re.compile(r"\bChapter\s+(-?\d+)\b", re.IGNORECASE)
# Anchor-text number for (slug ↔ displayed) pair harvesting: also fractional and
# negative ("Chapter -1 Volume 2 Glossary" marks a non-chapter insert page).
_ANCHOR_NUM_RE = re.compile(rf"\b{CHAPTER_WORD}\s+(-?\d+(?:\.\d+)?)\b", re.IGNORECASE)
# A "title" that is only the website's name (what the document title leaves after the chapter number).
_SITE_NAME_ONLY_RE = re.compile(r"free\s*web\s*novel(?:\s*\.\s*(?:com|net))?", re.IGNORECASE)


# How many opening lines of a page's body are checked against its heading.
_HEADING_LINES = 2

# Composite heading some pages show: "Chapter 1129.1 - 1130 Initiative" (the story itself begins
# "Chapter 1130 Initiative"). The first number is the page before it, the second the real chapter.
_COMPOSITE_RE = re.compile(
    rf"\b{CHAPTER_WORD}\s+(\d+(?:\.\d+)?)\s*[-–—]\s*(\d+)(?![\d.])\s*(.*)$", re.IGNORECASE
)


def _parse_chapter_number(raw: str) -> int | Decimal:
    """Keep fractional text at first parse, including trailing zeros. Numeric legacy
    indices are created only alongside the exact ``manifest_label`` metadata."""
    return Decimal(raw) if "." in raw else int(raw)


def _parsed_meta(spec: SiteSpec, slug: int, number: int | float | Decimal, html: Optional[str]) -> ChapterMeta:
    label = ChapterLabel.parse(str(number))
    extra = {"manifest_label": label.text}
    if html is not None:
        extra[_PREFETCHED_HTML_KEY] = html
    return ChapterMeta(index=label.to_index(), url=chapter_url_for(spec, slug), slug=slug, extra=extra)

# FreeWebNovel injects zero-width / format / non-breaking-space characters
# between words (notably U+200C Zero-Width Non-Joiner in Reverend Insanity
# titles). None of these are matched by ``\s``, so the title regex silently
# fails on every heading candidate — the 2261–2327 "Chapter N:." empty-title
# bug. Strip them (→ single space) before any whitespace-sensitive matching.
_INVISIBLE_CHARS = "​‌‍‬ ­﻿"
_INVISIBLE_RE = re.compile(f"[{_INVISIBLE_CHARS}]+")

# Body containers, in priority order: the FIRST one present that holds paragraphs is the chapter
# body (v0.3.0 05). ``#article`` is the .com reader, ``#chapter-content`` the .net reader. The old
# "largest container wins" rule picked wrappers that also hold the reader widgets (text-to-speech,
# report form), and its noise/short-line filters then deleted real prose; measured on 1,398 cached
# pages, they removed 2,274 story paragraphs ("Boom!", "He sighed.", "***", curly-quoted lines).
# Generic wrappers (``main``, ``.chapter``) are deliberately not containers.
_BODY_CONTAINER_SELECTORS = (
    "#article",
    "#chapter-content",
    ".chapter-content",
    ".read-content",
    ".entry-content",
    ".novel-content",
    ".chapter-body",
    "article",
)
# This website's own promotional sentences, removed only when confidently identified
# (``content.strip_promotions``).
SITE_NAMES = ("freewebnovel",)

# A discovered chapter count below this for a "known long novel" is suspicious
# and is surfaced as a warning (never silently trusted).
_COMPLETENESS_MIN = 10

# ── fix-chapter-number-offset: displayed-number addressing (Phase 2) ─────────
# Key under which a walk/probe fetch's HTML is cached on ``ChapterMeta.extra`` so
# ``fetch_chapter`` can reuse it instead of re-fetching the same page a second
# time (offset-resolution reconciliation and content-fetch become ONE network
# hit per chapter, not two).
_PREFETCHED_HTML_KEY = "_prefetched_html"
_MISSING_PAGE_MARKER = ""

# Padding (in slugs) added around a binary-search-resolved anchor before/after
# the walk, absorbing a local run of insert pages (Supreme Magus has a run of
# five between slugs 629 and 635) so the true boundary isn't missed by a
# couple of slugs when a caller supplies start_hint/end_hint.
_WALK_PADDING = 8
_RECONCILIATION_FETCH_CEILING_PADDING = 64
_RECONCILIATION_SEARCH_LOG_EVERY = 8
# Longest run of slugs whose number is inferred from two probed pages with the same slug-to-number
# gap (see ``_reconcile_window``): a change of gap shorter than this between two such pages is not seen
# here, and the per-chapter identity check refuses the page instead.
_VERIFY_STRIDE = 16


def _meta_label(meta: ChapterMeta) -> str:
    """The exact label of a chapter (the manifest's text when present: ``627.10`` survives)."""
    return ChapterLabel.from_meta(meta).text


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

    # Only opening headings are identity evidence; a mention in ordinary prose is not.
    for line in _extract_paragraphs(soup)[:_HEADING_LINES]:
        if parse_heading(line)[0] is not None:
            add(line)

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
    body_ids = {id(container) for selector in _BODY_CONTAINER_SELECTORS for container in soup.select(selector)}
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
        # A body container or the next story paragraph is never a split title.
        if id(nxt) in body_ids or any(id(parent) in body_ids for parent in tag.parents):
            continue
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
    raw: str, fallback_num: Optional[int | Decimal]
) -> tuple[Optional[int | Decimal], str]:
    """Return ``(chapter_number, title_only)``. ``title_only`` is the trimmed
    title text (site junk removed) or ``""`` for a bare-number heading. Returns
    ``(None, "")`` when the text is not a chapter heading at all. ``chapter_number``
    retains exact fractional digits. ``fallback_num`` is ignored for compatibility:
    an expected number is never observed evidence."""
    raw = _ws(raw)
    m = CHAPTER_TITLE_RE.search(raw)
    if not m:
        return None, ""

    number = _parse_chapter_number(m.group(1))
    title = _ws(m.group(2))

    # Site-junk trimming only (NOT rewording).
    title = re.sub(r"\s*[|\-]\s*Empire\s*Novel.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*-\s*Read\s*Online.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*-\s*Free\s*Web\s*Novel.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*\|\s*Free\s*Web\s*Novel.*$", "", title, flags=re.IGNORECASE)
    title = title.strip(" -|:;.")
    if _SITE_NAME_ONLY_RE.fullmatch(title):
        title = ""      # "Supreme Magus - Chapter 1921 - Free Web Novel": the site, not a chapter title
    return number, title


def _composite_heading(soup: BeautifulSoup) -> Optional[tuple[str, int, str]]:
    """``(shown, number, title)`` for a composite heading "Chapter 1129.1 - 1130 Initiative",
    else ``None``. It counts only when everything agrees: the second number is a whole number
    above the first by at most one, and the story's own opening line (a different element of
    the page) says "Chapter <second number> <same title>". Any other pair of numbers in a
    heading is not a composite, so a page showing a different chapter is still rejected."""
    paragraphs = _extract_paragraphs(soup)[:_HEADING_LINES]
    for candidate in _candidate_title_texts(soup):
        match = _COMPOSITE_RE.search(candidate)
        if match is None:
            continue
        first, second = Decimal(match.group(1)), int(match.group(2))
        if not 0 < second - first <= 1:
            continue
        title = clean_title(match.group(3), second)
        for line in paragraphs:
            story_number, story_title = parse_heading(line)
            if story_number == str(second) and clean_title(story_title, second).casefold() == title.casefold():
                return label_text(match.group(1)), second, title
    return None


def _extract_title(
    soup: BeautifulSoup, fallback_num: Optional[int | Decimal]
) -> tuple[Optional[int | float], str]:
    """Legacy numeric title helper. Extraction/probing use ``_observed_heading``
    instead so exact fractional evidence never passes through this float bridge.
    ``fallback_num`` is ignored: unknown identity stays unknown."""
    title_less: Optional[tuple[Optional[int | float], str]] = None
    for candidate in _candidate_title_texts(soup):
        num, title = _normalize_heading_line(candidate, fallback_num)
        if num is None:
            continue
        if title:
            return float(num) if isinstance(num, Decimal) else num, title
        if title_less is None:
            title_less = (float(num) if isinstance(num, Decimal) else num, "")
    if title_less is not None:
        return title_less
    return None, ""


def _observed_heading(soup: BeautifulSoup) -> tuple[Optional[int | Decimal], str, Optional[str]]:
    """Resolve page evidence without an expected number. Only a confirmed composite may
    reconcile two different labels; every other numbered candidate must agree."""
    composite = _composite_heading(soup)
    evidence = []
    for candidate in _candidate_title_texts(soup):
        number, title = _normalize_heading_line(candidate, None)
        if number is None:
            continue
        if composite is not None:
            pair = _COMPOSITE_RE.search(candidate)
            if pair and (label_text(pair.group(1)), int(pair.group(2))) == composite[:2]:
                number, title = composite[1], composite[2]
        evidence.append((number, title))
    if not evidence:
        return None, "", None
    labels = {ChapterLabel.parse(str(number)).text for number, _ in evidence}
    if len(labels) != 1:
        raise ChapterMismatchError("conflicting chapter identity evidence: " + ", ".join(sorted(labels)))
    number = evidence[0][0]
    title = next((title for _, title in evidence if title), "")
    shown = f"{composite[0]}-{composite[1]}" if composite else None
    return number, title, shown


# ── Body extraction (v0.3.0 05: structure, then the shared canonical cleanup) ─
def _strip_embedded_watermarks(soup: BeautifulSoup) -> None:
    for tag in soup.find_all("subtxt"):
        tag.decompose()


def node_text(node) -> str:
    """A paragraph's text with inline markup joined as the reader sees it ("<i>What</i>?" ->
    "What?", never "What ?"), ``<br>`` as a space, then normalised (``content.normalize_text``)."""
    for br in node.find_all("br"):
        br.replace_with(" ")
    return normalize_text(node.get_text())


def _extract_paragraphs(soup: BeautifulSoup, heading_line: str | None = None) -> list[str]:
    """The raw story paragraphs of the first body container present (see
    ``_BODY_CONTAINER_SELECTORS``): watermark tags, reader-interface lines and this site's own
    promotions removed; nothing else. ``heading_line`` is accepted for compatibility and unused
    (the canonical cleanup handles repeated headings). ``[]`` when no container has paragraphs."""
    _strip_embedded_watermarks(soup)
    for selector in _BODY_CONTAINER_SELECTORS:
        node = soup.select_one(selector)
        if node is None:
            continue
        paras = [strip_promotions(node_text(p), SITE_NAMES) for p in node.find_all("p")]
        paras = [p for p in paras if p and not is_site_chrome(p)]
        if paras:
            return paras
    return []


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
    def build_chapter_index(
        self,
        spec: SiteSpec,
        *,
        fast_path: bool = False,
        start_hint: Optional[int | float] = None,
        end_hint: Optional[int | float] = None,
        resume_metas: Optional[list[ChapterMeta]] = None,
        index_progress_cb: Optional[
            Callable[[list[ChapterMeta], int, int], None]
        ] = None,
    ) -> list[ChapterMeta]:
        """Discover the complete chapter list (TOC-first), addressed by the
        site's DISPLAYED chapter number (fix-chapter-number-offset).

        **Approach B — generated URLs (the legacy bug fix).** The legacy
        scraper looked chapter URLs up in the map parsed from the landing page,
        but FreeWebNovel's index renders only a *slice* of the full list, so any
        chapter in the unrendered gap was silently dropped (the 2261–2327 bug).
        The index page is used ONLY to learn the highest SLUG (the "newest
        chapters" widget reliably shows the latest chapter), and chapter URLs
        are *generated* from a template over ``1..count`` slugs — gap-free by
        construction.

        ``spec.chapter_count`` overrides the discovered slug count when set
        (and a disagreement is surfaced as a warning, never silently honoured).

        **Identity-vs-offset (probe-gated, per-novel auto-detected):** most
        FreeWebNovel novels number slugs and displayed chapters identically. The
        SAME index-page fetch already made above usually reveals this for FREE —
        its rendered anchors carry both the URL slug and "Chapter N: Title" link
        text, so comparing them costs no extra network call. Only when the index
        page's anchor text is uninformative does one extra probe fetch decide
        identity vs. offset (rare — real FreeWebNovel TOC pages are informative).
        An IDENTITY novel returns the exact same generated list as before (zero
        extra fetches, byte-identical output). An OFFSET novel (confirmed:
        Supreme Magus) walks forward reconciling each page's ACTUAL displayed
        number — fractional ".x" side-chapters keep their fractional label,
        unnumbered/negative extras (joke chapters, glossaries) are skipped and
        logged. ``start_hint``/``end_hint`` (displayed-space, both optional) let
        a caller bound that walk to the requested range via a binary search over
        the monotone displayed(slug) relationship, padded to absorb a local
        cluster of inserts; omitted, the walk covers the whole discovered slug
        range (this repo's pipeline does not thread them yet — see Phase 3 note
        in the fix drop).
        """
        rm = self._manager(spec)
        # ``fast_path`` (0.2.0 §3.11): the conductor builds the TOC under the bounded
        # fast policy so a headless Cloudflare block surfaces a ChallengeFetchError
        # quickly (for the visible-retry fallback) instead of walking the long ladder.
        # Only pass the kwarg when set, so the legacy call signature (and every test
        # fake that mimics it) is unchanged for the default path.
        # ``use_cache=False`` (v0.3.0 03.2b): the index page is where the newest chapter is
        # learned, so it is always fetched fresh and never cached; a stale copy would hide
        # chapters the site has published since.
        if fast_path:
            html = rm.fetch(spec.url, use_browser=spec.use_browser, use_cache=False, fast_path=True)
        else:
            html = rm.fetch(spec.url, use_browser=spec.use_browser, use_cache=False)
        soup = BeautifulSoup(html, "html.parser")
        discovered, pairs = self._scan_index_page(soup, spec)

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

        def _identity_list() -> list[ChapterMeta]:
            # ``slug`` records the URL slug number each meta is fetched at.
            # ``index`` equals ``slug`` here — the identity/no-op case.
            return [
                ChapterMeta(index=n, url=chapter_url_for(spec, n), slug=n)
                for n in range(1, count + 1)
            ]

        if pairs and all(slug == disp for slug, disp in pairs):
            return _identity_list()

        # Cache of slug -> (html, displayed_number) so a page fetched to decide
        # identity/resolve the anchor is never fetched again by the walk below.
        known: dict[int, tuple[Optional[str], Optional[int | Decimal]]] = {}
        for meta in resume_metas or []:
            if meta.slug is not None:
                known[meta.slug] = (None, _parse_chapter_number(_meta_label(meta)))
        fetch_budget = {
            "count": 0,
            "limit": count + _RECONCILIATION_FETCH_CEILING_PADDING,
        }

        if not pairs:
            # Index page gave no free (slug, displayed) evidence at all — one
            # probe fetch decides identity vs. offset. This is the ONLY case
            # that costs an extra fetch beyond the index page itself.
            probe_slug = 1
            if start_hint is not None:
                probe_slug = max(1, min(int(round(start_hint)), count))
            probe_num = self._fetch_and_read(
                rm, spec, probe_slug, known, fetch_budget=fetch_budget
            )
            if probe_num == probe_slug:
                return _identity_list()
            self._warn(
                f"{spec.novel_slug!r}: URL slug and displayed chapter number "
                f"disagree (slug {probe_slug} displays "
                f"{probe_num!r}) — addressing by displayed number."
            )
        else:
            # ≥1 harvested pair disagreed — offset confirmed for free.
            mismatch = next((p for p in pairs if p[0] != p[1]), pairs[0])
            self._warn(
                f"{spec.novel_slug!r}: URL slug and displayed chapter number "
                f"disagree (slug {mismatch[0]} displays "
                f"{mismatch[1]!r}) — addressing by displayed number."
            )

        # ── OFFSET path: resolve the walk window, then reconcile per page ────
        if start_hint is None and end_hint is None:
            walk_lo, walk_hi = 1, count
        else:
            anchor = self._resolve_slug_for_displayed(
                rm,
                spec,
                start_hint if start_hint is not None else 1,
                1,
                count,
                known,
                fetch_budget=fetch_budget,
            )
            walk_lo = max(1, anchor - _WALK_PADDING)
            if end_hint is None:
                walk_hi = count
            else:
                end_anchor = self._resolve_slug_for_displayed(
                    rm,
                    spec,
                    end_hint,
                    anchor,
                    count,
                    known,
                    fetch_budget=fetch_budget,
                )
                walk_hi = min(count, end_anchor + _WALK_PADDING)

        return self._reconcile_window(
            rm,
            spec,
            walk_lo,
            walk_hi,
            known,
            fetch_budget=fetch_budget,
            resume_metas=resume_metas,
            index_progress_cb=index_progress_cb,
        )

    def _scan_index_page(
        self, soup: BeautifulSoup, spec: SiteSpec
    ) -> tuple[Optional[int], list[tuple[int, int | Decimal]]]:
        """One pass over the index page's anchors: the highest SLUG (for
        ``range()`` generation) plus every (slug, displayed) pair where an
        anchor's own href AND visible text both carry a chapter number — free
        evidence for the identity-vs-offset decision, no extra fetch."""
        link_re = _chapter_link_re(spec)
        base = spec.base_url or spec.url
        numbers: list[int] = []
        pairs: list[tuple[int, int | Decimal]] = []

        for a in soup.find_all("a", href=True):
            href = _normalize_url(a["href"], base)
            m = link_re.search(href)
            if not m:
                continue
            slug = int(m.group(1))
            numbers.append(slug)
            tm = _ANCHOR_NUM_RE.search(_ws(a.get_text(" ", strip=True)))
            if tm:
                pairs.append((slug, _parse_chapter_number(tm.group(1))))

        if not numbers:
            # Loosened href fallback: any "/chapter-N" path segment, regardless
            # of the exact index-URL path — still slug-space (from the URL
            # itself), just less strictly scoped to this novel's own links.
            loose_re = re.compile(r"/chapter-(\d+)(?:[/?#]|$)", re.IGNORECASE)
            for a in soup.find_all("a", href=True):
                m = loose_re.search(a["href"])
                if m:
                    numbers.append(int(m.group(1)))

        if not numbers:
            # Last resort: anchor TEXT is DISPLAYED-space, not slug-space, so on
            # an offset novel this count would UNDERSHOOT the true slug count —
            # never trusted silently.
            for a in soup.find_all("a"):
                m = CHAPTER_LIST_TEXT_RE.search(_ws(a.get_text(" ", strip=True)))
                if m:
                    n = int(m.group(1))
                    if n >= 1:
                        numbers.append(n)
            if numbers:
                self._warn(
                    f"{spec.novel_slug!r}: chapter count derived from displayed "
                    "link text, not URL slugs — may undershoot the true slug "
                    "count on an offset novel"
                )

        discovered = max(numbers) if numbers else None
        return discovered, pairs

    def _discover_max_chapter(
        self, html: str, spec: SiteSpec
    ) -> Optional[int]:
        """Back-compat wrapper (still used directly by existing tests)."""
        discovered, _pairs = self._scan_index_page(
            BeautifulSoup(html, "html.parser"), spec
        )
        return discovered

    def _fetch_and_read(
        self,
        rm,
        spec: SiteSpec,
        slug: int,
        known: dict[int, tuple[Optional[str], Optional[int | Decimal]]],
        *,
        fetch_budget: Optional[dict[str, int]] = None,
    ) -> Optional[int | Decimal]:
        """Fetch (or reuse a cached) page for ``slug`` and return its displayed
        number, caching ``(html, number)`` in ``known`` so neither the binary
        search, the walk, nor a later ``fetch_chapter`` call ever re-fetches it."""
        if slug not in known:
            if fetch_budget is not None:
                fetch_budget["count"] += 1
                if fetch_budget["count"] > fetch_budget["limit"]:
                    raise RuntimeError(
                        "FreeWebNovel chapter-index reconciliation exceeded its "
                        f"fetch safety ceiling ({fetch_budget['limit']} chapter "
                        "page fetches). Aborting instead of continuing an "
                        "unexpectedly long TOC walk."
                    )
            from ..request_manager import NotFoundFetchError

            try:
                html = rm.fetch(
                    chapter_url_for(spec, slug), use_browser=spec.use_browser
                )
            except NotFoundFetchError:
                known[slug] = (_MISSING_PAGE_MARKER, None)
                self._warn(
                    f"{spec.novel_slug!r}: slug {slug} is missing (HTTP 404/410); "
                    "treating it as a chapter-number discontinuity."
                )
            else:
                page = BeautifulSoup(html, "html.parser")
                try:
                    num, _title, _shown = _observed_heading(page)
                except ChapterMismatchError as exc:
                    self._warn(f"unresolved chapter identity at {chapter_url_for(spec, slug)}: {exc}")
                    num = None
                known[slug] = (html, num)
        return known[slug][1]

    def _resolve_slug_for_displayed(
        self,
        rm,
        spec: SiteSpec,
        target: int | float,
        lo: int,
        hi: int,
        known: dict[int, tuple[Optional[str], Optional[int | Decimal]]],
        *,
        fetch_budget: Optional[dict[str, int]] = None,
    ) -> int:
        """Binary search over the monotone (non-decreasing) displayed(slug)
        relationship for the LOWEST slug whose displayed number is >= target —
        the first page addressing that chapter. An unparseable page (extras
        read as ``None``) is treated as "below target" so the search steps past
        it rather than stalling. This only needs to be approximately right: the
        caller pads the result and the per-page walk is the authoritative
        reconciliation."""
        best = hi
        while lo < hi:
            mid = (lo + hi) // 2
            num = self._fetch_and_read(
                rm, spec, mid, known, fetch_budget=fetch_budget
            )
            if num is not None and num >= target:
                best = mid
                hi = mid
            else:
                lo = mid + 1
        return best if best is not None else lo

    @staticmethod
    def _displayed_offset(slug: int, num: Optional[int | Decimal]) -> Optional[int]:
        """Return ``slug - displayed`` only for ordinary positive integer
        chapter pages. Fractional side chapters and unnumbered extras are not
        inferable constant-span endpoints, so the compressed reconciler walks
        around them explicitly."""
        if isinstance(num, int) and num >= 1:
            return slug - num
        return None

    def _reconcile_window(
        self,
        rm,
        spec: SiteSpec,
        walk_lo: int,
        walk_hi: int,
        known: dict[int, tuple[Optional[str], Optional[int | Decimal]]],
        *,
        fetch_budget: Optional[dict[str, int]] = None,
        resume_metas: Optional[list[ChapterMeta]] = None,
        index_progress_cb: Optional[
            Callable[[list[ChapterMeta], int, int], None]
        ] = None,
    ) -> list[ChapterMeta]:
        """Resolve a slug window to displayed-space chapter metadata.

        Large offset novels are mostly constant-offset spans interrupted by a
        small number of inserts/extras. Fetching every page during TOC build
        makes a full-range run look hung for hours. This walk uses the documented
        slug-to-number gap, fetches the pages that are not ordinary numbered chapters,
        and infers only short spans between probed pages with the same gap."""
        if walk_lo > walk_hi:
            return []
        metas_by_slug: dict[int, ChapterMeta] = {
            meta.slug: meta
            for meta in (resume_metas or [])
            if meta.slug is not None and walk_lo <= meta.slug <= walk_hi
        }
        last_search_log_count = 0

        def add_meta(meta: ChapterMeta) -> None:
            if meta.slug is None:
                raise RuntimeError("FreeWebNovel reconciliation produced a row without a slug.")
            metas_by_slug[meta.slug] = meta

        def sorted_metas() -> list[ChapterMeta]:
            return sorted(
                metas_by_slug.values(),
                key=lambda meta: (ChapterLabel.parse(_meta_label(meta)).sort_key, meta.slug or 0),
            )

        def add_inferred(lo: int, hi: int, offset: int) -> None:
            for slug in range(lo, hi + 1):
                index = slug - offset
                if index < 1:
                    continue
                add_meta(
                    ChapterMeta(
                        index=index,
                        url=chapter_url_for(spec, slug),
                        slug=slug,
                    )
                )

        def add_exact(slug: int) -> Optional[int]:
            num = self._fetch_and_read(
                rm, spec, slug, known, fetch_budget=fetch_budget
            )
            html = known[slug][0]
            if num is None:
                if html == _MISSING_PAGE_MARKER:
                    return None
                self._warn(
                    f"{spec.novel_slug!r}: skipping slug {slug} (no parseable "
                    "positive chapter number — joke/glossary extra)."
                )
                return None
            add_meta(_parsed_meta(spec, slug, num, html))
            return self._displayed_offset(slug, num)

        def emit_progress(through_slug: int) -> None:
            metas = sorted_metas()
            fetches = fetch_budget["count"] if fetch_budget is not None else len(known)
            self._log(
                f"{spec.novel_slug!r}: reconciled through slug {through_slug} "
                f"of {walk_hi} ({len(metas)} chapters resolved; {fetches} "
                "chapter pages fetched)."
            )
            if index_progress_cb is not None:
                index_progress_cb(metas, through_slug, walk_hi)

        def maybe_log_search(search_lo: int, search_hi: int) -> None:
            nonlocal last_search_log_count
            fetches = fetch_budget["count"] if fetch_budget is not None else len(known)
            if fetches - last_search_log_count < _RECONCILIATION_SEARCH_LOG_EVERY:
                return
            last_search_log_count = fetches
            self._log(
                f"{spec.novel_slug!r}: locating next chapter-number offset "
                f"change in slugs {search_lo}-{search_hi} "
                f"({fetches} chapter pages fetched so far)."
            )

        # Establish the first ordinary integer page. Opening joke/glossary or
        # fractional pages are authoritative and therefore fetched explicitly.
        cursor = walk_lo
        current_offset: Optional[int] = None
        while cursor <= walk_hi and current_offset is None:
            current_offset = add_exact(cursor)
            cursor += 1
        if current_offset is None:
            emit_progress(walk_hi)
            return sorted_metas()

        # Establish the final ordinary offset; a trailing insert is walked later.
        right_integer_slug = walk_hi
        final_offset: Optional[int] = None
        while right_integer_slug >= cursor and final_offset is None:
            num = self._fetch_and_read(
                rm, spec, right_integer_slug, known, fetch_budget=fetch_budget
            )
            final_offset = self._displayed_offset(right_integer_slug, num)
            if final_offset is None:
                right_integer_slug -= 1
        if final_offset is None:
            for slug in range(cursor, walk_hi + 1):
                add_exact(slug)
            emit_progress(walk_hi)
            return sorted_metas()

        # The gap between slug and displayed number is NOT monotone: the site can skip numbers for a
        # stretch and then come back to the earlier gap (Supreme Magus slugs 1653-1932 show two numbers
        # more than the pages before and after them). Two equal gaps therefore prove nothing about the
        # pages between; a span is inferred only when it is short enough to sit between two probed
        # pages that agree. Longer spans are halved (fetching the middle page) until they are, so a
        # change in the gap is found to the exact slug. Pages with no ordinary number (fractional
        # inserts, extras, missing pages) are fetched one by one, never inferred.
        add_exact(right_integer_slug)
        emit_progress(cursor - 1)
        pending = [(cursor - 1, right_integer_slug, current_offset, final_offset)]
        while pending:
            lo, hi, off_lo, off_hi = pending.pop()
            if hi - lo <= 1:
                continue
            if off_lo is None:
                pending.append((lo + 1, hi, add_exact(lo + 1), off_hi))
            elif off_hi is None:
                pending.append((lo, hi - 1, off_lo, add_exact(hi - 1)))
            elif off_lo == off_hi and hi - lo <= _VERIFY_STRIDE:
                add_inferred(lo + 1, hi - 1, off_lo)
                emit_progress(hi)
            else:
                # Equal gaps: step along at the probe spacing. Different gaps: halve, to find where.
                mid = lo + _VERIFY_STRIDE if off_lo == off_hi else (lo + hi) // 2
                off_mid = add_exact(mid)
                maybe_log_search(lo, hi)
                pending.append((mid, hi, off_mid, off_hi))
                pending.append((lo, mid, off_lo, off_mid))
        for slug in range(right_integer_slug + 1, walk_hi + 1):
            add_exact(slug)
        emit_progress(walk_hi)

        metas = sorted_metas()
        self._log(
            f"{spec.novel_slug!r}: chapter-index reconciliation read "
            f"{fetch_budget['count'] if fetch_budget is not None else len(known)} chapter pages and "
            f"inferred the rest only between pages at most {_VERIFY_STRIDE} slugs apart that agree."
        )
        return metas

    def _walk_and_reconcile(
        self,
        rm,
        spec: SiteSpec,
        walk_lo: int,
        walk_hi: int,
        known: dict[int, tuple[Optional[str], Optional[int | Decimal]]],
        *,
        fetch_budget: Optional[dict[str, int]] = None,
    ) -> list[ChapterMeta]:
        """Walk slugs ``walk_lo..walk_hi`` in order, reading each page's ACTUAL
        displayed chapter number. Fractional numbers are kept as their own
        label; a page with no parseable positive number (joke chapter, negative
        "Volume Glossary" extras) is skipped and logged, not emitted as a
        degraded "Chapter N." — that silent mislabel is what this fix removes.
        Fetched HTML is cached on the returned meta's ``extra`` so
        ``fetch_chapter`` never re-fetches the same page."""
        metas: list[ChapterMeta] = []
        skipped = 0
        for slug in range(walk_lo, walk_hi + 1):
            num = self._fetch_and_read(
                rm, spec, slug, known, fetch_budget=fetch_budget
            )
            html = known[slug][0]
            if num is None:
                skipped += 1
                if html == _MISSING_PAGE_MARKER:
                    continue
                self._warn(
                    f"{spec.novel_slug!r}: skipping slug {slug} (no parseable "
                    "positive chapter number — joke/glossary extra)."
                )
                continue
            metas.append(_parsed_meta(spec, slug, num, html))
        metas.sort(key=lambda m: (ChapterLabel.parse(_meta_label(m)).sort_key, m.slug or 0))
        if skipped:
            self._log(
                f"{spec.novel_slug!r}: resolved {len(metas)} chapter(s) by "
                f"displayed number over slugs {walk_lo}-{walk_hi} "
                f"({skipped} extra page(s) skipped)."
            )
        return metas

    def fetch_chapter(
        self, meta: ChapterMeta, spec: SiteSpec, *, fast_path: bool = False
    ) -> ChapterContent:
        prefetched = meta.extra.get(_PREFETCHED_HTML_KEY) if meta.extra else None
        rm = self._manager(spec)
        if prefetched is not None:
            # Reconciliation during build_chapter_index already fetched this
            # exact page (offset novels) — reuse it instead of fetching twice. It gets the same
            # checks as any page: one that is not this chapter is set aside and the page is fetched
            # again, exactly as for a saved page (``extract_validated``).
            try:
                return self._extract_chapter(prefetched, meta)
            except EmptyExtractionError as exc:
                invalidate = getattr(rm, "invalidate_cache", None)
                if callable(invalidate):
                    invalidate(meta.url, str(exc))
                meta.extra.pop(_PREFETCHED_HTML_KEY, None)
                self._log(f"  chapter {meta.index}: the page read while listing chapters could not be used ({exc}); fetching it again.")

        def fetch() -> str:
            # ``fast_path`` (0.2.0 §3.2): the conductor runs the primary under a bounded
            # camoufox budget that raises a typed signal so a hard chapter is handed to
            # the rescue lane while the primary moves on. Default False keeps the legacy
            # (full-ladder) behaviour for every existing caller and the WND path; only
            # pass the kwarg when set so existing test fakes keep their signature.
            if fast_path:
                return rm.fetch(meta.url, use_browser=spec.use_browser, fast_path=True)
            return rm.fetch(meta.url, use_browser=spec.use_browser)

        return extract_validated(rm, meta, fetch, self._extract_chapter, self._log)

    def _extract_chapter(self, html: str, meta: ChapterMeta) -> ChapterContent:
        soup = BeautifulSoup(html, "html.parser")
        number, title, shown = _observed_heading(soup)
        if number is None:
            raise ChapterMismatchError(f"the page at {meta.url} has no observed chapter identity.")
        label = _meta_label(meta)
        observed = ChapterLabel.parse(str(number)).text
        if observed != ChapterLabel.parse(label).text:
            # The offset guard (plan §9.6): a page showing another chapter is never published
            # as this one, however it reached us (cache, network or reconciliation).
            raise ChapterMismatchError(
                f"the page at {meta.url} shows chapter {number}, but the chapter list expects "
                f"chapter {label} there (the site's numbering may have moved; refresh the "
                "chapter list and run again)."
            )

        title = clean_title(title, label) or clean_title(meta.title or "", label)
        paragraphs = clean_paragraphs(_extract_paragraphs(soup), label=label, title=title)
        if shown is not None:
            label = shown
        if not paragraphs:
            # A fully-fetched, non-challenge page with no extractable body is an
            # extraction failure (its own class) — NOT a Cloudflare block. The
            # pipeline records it as a plain failure without auto-slowdown.
            raise EmptyExtractionError(
                f"Could not extract body paragraphs for chapter {meta.index}."
            )

        if not title:
            # Degraded: the content is kept under an explicit, deterministic heading.
            self._warn(
                f"chapter {label} — no title text found in any heading "
                f"candidate (content written, heading will be 'Chapter {label}: Untitled')"
            )

        return ChapterContent(
            index=meta.index, title=title or "", paragraphs=paragraphs, label=label
        )
