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
import tempfile
import threading
import weakref
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
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
    ChapterIndex,
    DiscoveryEvidence,
    ChapterMismatchError,
    ChapterIdentityUnknownError,
    ChapterIdentityConflictError,
    EmptyExtractionError,
    SiteSpec,
)
from ..source_identity import ChapterLabel, SourceIdentity
from ..chapter_manifest import ChapterManifest
from ..chapter_verification import PageObservation, SourceBoundChanged, Verification, requires_verification
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

# Every offset-source page is observed, in cancellable batches.
_RECONCILIATION_FETCH_CEILING_PADDING = 64
_DISCOVERY_BATCH = 16
_DISCOVERY_TEMP = Path(__file__).resolve().parents[3] / "files" / "run-temp" / "chapter-discovery"
_EVIDENCE_KEY = "discovery_evidence"


class _PageSpool:
    """One temporary file, kept alive by selected page references until extraction.

    Only one page is materialized at a time. Never a cross-run identity cache.
    TemporaryFile closes and removes itself when the last reference is released.
    """
    def __init__(self, directory=None):
        directory = Path(directory) if directory is not None else _DISCOVERY_TEMP
        directory.mkdir(parents=True, exist_ok=True)
        self.file = tempfile.TemporaryFile(dir=directory)
        weakref.finalize(self, self.file.close)
        self.lock = threading.Lock()

    def put(self, html):
        data = html.encode("utf-8")
        with self.lock:
            self.file.seek(0, 2)
            offset = self.file.tell()
            self.file.write(data)
        return _PageRef(self, offset, len(data))


@dataclass(frozen=True)
class _PageRef:
    spool: _PageSpool
    offset: int
    length: int

    def read(self):
        with self.spool.lock:
            self.spool.file.seek(self.offset)
            return self.spool.file.read(self.length).decode("utf-8")


@dataclass(frozen=True)
class _PageRead:
    number: int | Decimal | None
    html: _PageRef | None
    evidence: DiscoveryEvidence


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
        raise ChapterIdentityConflictError("conflicting chapter identity evidence: " + ", ".join(sorted(labels)),
                                           observed_labels=tuple(sorted(labels)))
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

    def __init__(self, request_manager=None, log=None, *, spool_factory=None) -> None:
        self._rm = request_manager
        self._log = log or logger.info
        # Developer acceptance can own/close isolated spools without global redirection.
        self._spool_factory = spool_factory or _PageSpool
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
        """Generate ordinary addresses or observe every page of an offset source.

        Supreme Magus always requires page observations, even if a rendered index
        slice has equal URL/display numbers. Hints filter selection downstream;
        binary search and padding cannot prove a nonmonotone source complete.
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
        self._log(f"Fresh index/TOC request: {spec.url}")
        if fast_path:
            html = rm.fetch(spec.url, use_browser=spec.use_browser, use_cache=False, fast_path=True)
        else:
            html = rm.fetch(spec.url, use_browser=spec.use_browser, use_cache=False)
        soup = BeautifulSoup(html, "html.parser")
        discovered, pairs = self._scan_index_page(soup, spec)
        requires_pages = spec.novel_slug == "supreme-magus" and urlparse(spec.url).hostname == "freewebnovel.com"
        if requires_pages:
            # Displayed-text and foreign-link fallbacks cannot bound this source.
            own_slugs = []
            for anchor in soup.find_all("a", href=True):
                url = _normalize_url(anchor["href"], spec.base_url or spec.url)
                match = _chapter_link_re(spec).search(url)
                if match and urlparse(url).netloc == urlparse(spec.url).netloc:
                    own_slugs.append(int(match.group(1)))
            if not own_slugs:
                raise RuntimeError("Supreme Magus discovery has no source-scoped URL bound; completeness is unproven.")
            discovered = max(own_slugs)

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

        if not requires_pages and pairs and all(slug == disp for slug, disp in pairs):
            return _identity_list()

        known: dict[int, _PageRead] = {}
        fetch_budget = {"count": 0, "limit": count + _RECONCILIATION_FETCH_CEILING_PADDING}

        if not pairs and not requires_pages:
            # Index page gave no free (slug, displayed) evidence at all — one
            # probe fetch decides identity vs. offset. This is the ONLY case
            # that costs an extra fetch beyond the index page itself.
            probe_slug = 1
            if start_hint is not None:
                probe_slug = max(1, min(int(round(start_hint)), count))
            probe_num = self._fetch_and_read(
                rm, spec, probe_slug, known, fetch_budget=fetch_budget
            )
            if not requires_pages and probe_num == probe_slug:
                return _identity_list()
            self._warn(
                f"{spec.novel_slug!r}: URL slug and displayed chapter number "
                f"disagree (slug {probe_slug} displays "
                f"{probe_num!r}) — addressing by displayed number."
            )
        elif any(slug != displayed for slug, displayed in pairs):
            # ≥1 harvested pair disagreed — offset confirmed for free.
            mismatch = next((p for p in pairs if p[0] != p[1]), pairs[0])
            self._warn(
                f"{spec.novel_slug!r}: URL slug and displayed chapter number "
                f"disagree (slug {mismatch[0]} displays "
                f"{mismatch[1]!r}) — addressing by displayed number."
            )

        # No complete independent TOC: inspect every slug, including backward runs.
        result = self._reconcile_window(
            rm, spec, 1, count, known, fetch_budget=fetch_budget,
            resume_metas=resume_metas, index_progress_cb=index_progress_cb,
            bound_confirmed=not (discovered is None or discovered != count),
        )
        result.tail_confirmed = result.discovery_complete and not (
            discovered is None or (spec.chapter_count is not None and discovered != count)
        )
        if requires_pages and discovered != count:
            # A smaller catalog override could omit a backward sequence or a
            # duplicate label in the unscanned suffix, even for a bounded request.
            result.discovery_complete = False
        return result

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

    @staticmethod
    def _check_cancelled(rm):
        from ..request_manager import ScrapeCancelled
        if getattr(getattr(rm, "cancel_event", None), "is_set", lambda: False)():
            raise ScrapeCancelled("cancelled during chapter-page discovery")

    def _fetch_and_read(self, rm, spec, slug, known, *, fetch_budget=None):
        """Read independent current evidence once; saved tuples are not observations."""
        self._check_cancelled(rm)
        prior = known.get(slug)
        if isinstance(prior, _PageRead):
            return prior.number if prior.evidence.verified else None
        if fetch_budget is not None:
            if fetch_budget["count"] >= fetch_budget["limit"]:
                raise RuntimeError("FreeWebNovel chapter-index reconciliation exceeded its fetch safety ceiling "
                                   f"({fetch_budget['limit']} chapter page fetches).")
            fetch_budget["count"] += 1
        from ..request_manager import FetchError, NotFoundFetchError
        from ..cloudflare_detection import is_cloudflare_challenge
        url = chapter_url_for(spec, slug)
        number, reference = None, None
        self._log(f"Chapter identity revalidation: {url}")
        try:
            # Historical HTML cannot certify current identity. Fetch fresh, then
            # retain the original response only after its chapter body validates.
            html = rm.fetch(url, use_browser=spec.use_browser, use_cache=False)
        except NotFoundFetchError as exc:
            evidence = DiscoveryEvidence("inaccessible", "current-response", detail=f"HTTP {exc.status}: URL missing; chapter absence unproven")
            self._warn(f"{spec.novel_slug!r}: slug {slug} is missing (HTTP {exc.status}); chapter identity unresolved.")
        except FetchError as exc:
            evidence = DiscoveryEvidence("inaccessible", "current-response", detail=str(exc))
        else:
            soup = BeautifulSoup(html, "html.parser")
            body = _extract_paragraphs(soup)
            document_heading = " ".join(node.get_text(" ") for node in soup.select("title, h1"))
            access_document = re.search(r"error\s*1015|rate.?limit|access denied", document_heading, re.I)
            if is_cloudflare_challenge(html) or access_document or (not body and re.search(r"error\s*1015|rate.?limit|access denied", soup.get_text(" "), re.I)):
                evidence = DiscoveryEvidence("inaccessible", "current-response", detail="access/rate-limit document")
            else:
                try:
                    number, _title, _shown = _observed_heading(soup)
                except ChapterMismatchError as exc:
                    evidence = DiscoveryEvidence("conflict", "current-page", detail=str(exc))
                else:
                    if number is not None:
                        label = ChapterLabel.parse(str(number)).text
                        if not body:
                            evidence = DiscoveryEvidence("unreadable", "current-page", (label,), "missing chapter body")
                        else:
                            evidence = DiscoveryEvidence("observed", "current-page", (label,))
                    else:
                        headings = " ".join(_candidate_title_texts(soup))
                        extra = bool(re.search(r"-2\s+Joke Chapter|(?:Chapter\s+)?-1\s+Volume\s+2\s+Glossary", headings, re.I))
                        evidence = DiscoveryEvidence("extra" if extra else "unknown", "current-page", detail="recognized opening extra" if extra else "no positive page identity")
                if prior is not None:
                    old_html, old_number = prior
                    if old_number is not None and number is not None and str(old_number) != str(number):
                        detail = f"prior {'HTML observation' if old_html else 'unverified candidate'} {old_number}; current page {number}"
                        self._warn(f"{url}: {detail}")
                        labels = (str(old_number), str(number)) if old_html else (str(number),)
                        evidence = DiscoveryEvidence("conflict" if old_html else evidence.status,
                                                     evidence.provenance, labels, detail)
                if getattr(getattr(rm, "last_fetch_info", None), "from_cache", False):
                    evidence = DiscoveryEvidence("saved_unknown", "historical-cache", evidence.labels,
                                                 "fetch unexpectedly supplied cached HTML; " + evidence.detail)
                if evidence.verified:
                    retain = getattr(rm, "retain_validated_html", None)
                    if callable(retain):
                        self._extract_for_source(html, _parsed_meta(spec, slug, number, None), spec)
                        retain(url, html)
                    spool = getattr(self, "_discovery_spool", None) or self._spool_factory()
                    reference = spool.put(html)
        if not evidence.verified:
            number = None
            self._warn(f"{url}: {evidence.status}: {evidence.detail}")
        known[slug] = _PageRead(number, reference, evidence)
        return number

    def _resolve_slug_for_displayed(self, rm, spec, target, lo, hi, known, *, fetch_budget=None):
        """Return a navigation candidate, never a monotonicity/completeness proof."""
        target_value = ChapterLabel.parse(str(target)).value
        while lo < hi:
            mid = (lo + hi) // 2
            number = self._fetch_and_read(rm, spec, mid, known, fetch_budget=fetch_budget)
            if number is not None and Decimal(number) >= target_value:
                hi = mid
            else:
                lo = mid + 1
        return lo

    @staticmethod
    def _displayed_offset(slug, num):
        return slug - num if isinstance(num, int) and num >= 1 else None

    def _reconcile_window(self, rm, spec, walk_lo, walk_hi, known, *, fetch_budget=None,
                          resume_metas=None, index_progress_cb=None, bound_confirmed=True):
        """Observe every slug once. Inference never enters returned chapter rows.

        Callbacks contain observed pages and explicit per-URL outcomes; partial
        results never vouch for completeness. Only current-policy historical
        observations can suppress reads; ordinary metadata seeds cannot.
        """
        rows, evidence, observations = {}, {}, {}
        source = SourceIdentity.from_site_spec(spec)
        policy = requires_verification(source)
        bound_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        saved = getattr(resume_metas, "verified_manifest", None)
        if not (policy and isinstance(saved, ChapterManifest) and saved.source == source
                and saved.verification is not None):
            saved = None
        # A source-bound regression cannot quietly drop previously certified pages.
        if saved and saved.verification.bound > walk_hi:
            raise SourceBoundChanged("Source bound decreased; chapter completeness is unproven. A separately reviewed source-bound disposition is required.")
        by_url = {p.url: p for p in saved.verification.pages} if saved else {}
        saved_rows = {c.page_url: c for c in saved.chapters} if saved else {}
        recheck = bool(getattr(resume_metas, "recheck_tail", False))
        # Checkpoints are whole snapshots. Retain unvisited historical observations, but
        # revoke the tail about to be checked before it can be returned by a partial callback.
        pending_pages = {p.url: p for p in saved.verification.pending_tail(source).pages} if saved and recheck else by_url
        for url, previous in by_url.items():
            slug = saved.verification.slug(source, url)
            observations[url] = pending_pages[url]
            chapter = saved_rows.get(url)
            if chapter and observations[url].status == "observed":
                rows[slug] = ChapterMeta(index=chapter.label.to_index(), url=url, slug=slug,
                                        title=chapter.title, source_id=chapter.source_id,
                                        extra={"manifest_label": chapter.label.text})
        seeds = {m.slug: m for m in resume_metas or [] if m.slug is not None}
        self._discovery_spool = self._spool_factory()
        last_emitted = None

        def snapshot(complete=False):
            return ChapterIndex(sorted(rows.values(), key=lambda m: (ChapterLabel.from_meta(m).sort_key, m.slug)),
                                tail_confirmed=False, evidence=dict(evidence), discovery_complete=complete,
                                verification=Verification.create(bound=walk_hi, bound_utc=bound_utc,
                                    bound_confirmed=bound_confirmed, pages=observations.values()) if policy else None)

        def emit(through):
            nonlocal last_emitted
            if through == last_emitted:
                return
            last_emitted = through
            self._log(f"{spec.novel_slug!r}: reconciled through slug {through} of {walk_hi} "
                      f"({len(rows)} observed chapters; no inferred identities).")
            if index_progress_cb is not None:
                index_progress_cb(snapshot(), through, walk_hi)

        through = walk_lo - 1
        try:
            for slug in range(walk_lo, walk_hi + 1):
                self._check_cancelled(rm)
                url = chapter_url_for(spec, slug)
                previous = by_url.get(url)
                reread = saved and recheck and slug > max(0, saved.verification.bound - 64)
                if previous and previous.resolved and not reread:
                    observations[url] = previous
                    evidence[url] = DiscoveryEvidence(previous.status, "previous-observation", previous.labels, previous.detail)
                    through = slug
                    continue
                rows.pop(slug, None)
                number = self._fetch_and_read(rm, spec, slug, known, fetch_budget=fetch_budget)
                read = known[slug]
                evidence[url] = read.evidence
                if number is not None:
                    meta = _parsed_meta(spec, slug, number, None)
                    seed = seeds.get(slug)
                    if seed is not None and _meta_label(seed) != _meta_label(meta):
                        detail = f"saved unknown label {_meta_label(seed)} contradicts observed {_meta_label(meta)}"
                        self._warn(f"{url}: {detail}")
                        evidence[url] = DiscoveryEvidence("observed", "current-page", (_meta_label(meta),), detail)
                    meta.extra[_EVIDENCE_KEY] = evidence[url]
                    meta.extra[_PREFETCHED_HTML_KEY] = read.html
                    rows[slug] = meta
                if policy:
                    fact = evidence[url]
                    observation = PageObservation(url, fact.status, fact.provenance, fact.labels,
                                                  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), fact.detail[:1000])
                    prior_labels = previous.labels if previous and previous.resolved else (previous.prior_labels if previous else None)
                    prior_time = previous.observed_utc if previous and previous.resolved else (previous.prior_observed_utc if previous else None)
                    if prior_labels is not None and observation.resolved and prior_labels != observation.labels:
                        observation = replace(observation, status="conflict", labels=tuple(sorted(set(prior_labels + observation.labels))),
                                              detail=f"Previously observed {prior_labels} at {prior_time}; new page shows {observation.labels}. Explicit refresh required.")
                        rows.pop(slug, None)
                        evidence[url] = DiscoveryEvidence("conflict", "current-page", observation.labels, observation.detail)
                    elif prior_labels is not None and not observation.resolved:
                        observation = replace(observation, prior_labels=prior_labels, prior_observed_utc=prior_time)
                    observations[url] = observation
                through = slug
                if (slug - walk_lo + 1) % _DISCOVERY_BATCH == 0:
                    emit(through)
            emit(through)
            complete = (bool(observations) and all(p.resolved for p in observations.values())) if policy else (
                bool(evidence) and all(e.verified or e.status == "extra" for e in evidence.values()))
            return snapshot(complete)
        except BaseException:
            emit(through)
            raise
        finally:
            del self._discovery_spool

    def _walk_and_reconcile(self, rm, spec, walk_lo, walk_hi, known, *, fetch_budget=None):
        return self._reconcile_window(rm, spec, walk_lo, walk_hi, known, fetch_budget=fetch_budget)

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
                html = prefetched.read() if isinstance(prefetched, _PageRef) else prefetched
                content = self._extract_for_source(html, meta, spec)
                from ..request_manager import FetchInfo
                rm.last_fetch_info = FetchInfo(from_cache=False, classification="prefetched")
                self._log(f"  Same-run discovery HTML reuse: {meta.url}")
                return content
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

        return extract_validated(rm, meta, fetch,
                                 lambda html, chapter: self._extract_for_source(html, chapter, spec), self._log)

    def _extract_for_source(self, html: str, meta: ChapterMeta, spec: SiteSpec) -> ChapterContent:
        content = self._extract_chapter(html, meta)
        if requires_verification(SourceIdentity.from_site_spec(spec)):
            # Match the certified full-publication path: the observed title was
            # already separated from its heading. Cleaning it again can delete
            # original words/numbers (2475), and a missing title must stay empty.
            _number, source_title, _shown = _observed_heading(BeautifulSoup(html, "html.parser"))
            if meta.title is not None and (source_title or "") != meta.title:
                raise ChapterMismatchError(
                    f"Chapter {meta.index}: source title contradicts the verified chapter inventory.",
                    requested_label=_meta_label(meta), observed_labels=(_meta_label(meta),), page_url=meta.url)
            content.title = source_title or ""
        return content

    def _extract_chapter(self, html: str, meta: ChapterMeta) -> ChapterContent:
        soup = BeautifulSoup(html, "html.parser")
        label = _meta_label(meta)
        from ..cloudflare_detection import is_cloudflare_challenge
        from ..request_manager import ChallengeFetchError, RateLimitedFetchError
        headings = " ".join(node.get_text(" ") for node in soup.select("title, h1, span.chapter"))
        if is_cloudflare_challenge(html) or re.search(r"access denied", headings, re.I):
            raise ChallengeFetchError(f"Access document at {meta.url}; chapter identity is unavailable.")
        if re.search(r"error\s*1015|you are being rate limited|rate limit exceeded", headings, re.I):
            raise RateLimitedFetchError(f"Rate-limit document at {meta.url}; chapter absence is unproven.", status=429)
        try:
            number, title, shown = _observed_heading(soup)
        except ChapterIdentityConflictError as exc:
            exc.requested_label, exc.page_url = label, meta.url
            raise
        if number is None:
            raise ChapterIdentityUnknownError(f"the page at {meta.url} has no observed chapter identity.",
                                             requested_label=label, page_url=meta.url)
        observed = ChapterLabel.parse(str(number)).text
        if observed != ChapterLabel.parse(label).text:
            # The offset guard (plan §9.6): a page showing another chapter is never published
            # as this one, however it reached us (cache, network or reconciliation).
            raise ChapterMismatchError(
                f"the page at {meta.url} shows chapter {number}, but the chapter list expects "
                f"chapter {label} there (the site's numbering may have moved; refresh the "
                "chapter list and run again).", requested_label=label,
                observed_labels=(observed,), page_url=meta.url,
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
                f"Could not extract body paragraphs for chapter {meta.index}.",
                requested_label=label, observed_labels=(observed,), page_url=meta.url,
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
