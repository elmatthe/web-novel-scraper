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

from ..models import ChapterContent, ChapterMeta, EmptyExtractionError, SiteSpec
from .base import BaseAdapter

logger = logging.getLogger(__name__)

# ── Title / chapter-number regexes (ported) ──────────────────────────────────
# Accepts colon, dash, or bare-whitespace separators between the number and the
# title, and allows an empty title (the bare-breadcrumb case). The number may be
# fractional ("Chapter 627.2" — FreeWebNovel's side-chapter inserts); a bare
# trailing dot ("Chapter 100. The End") is NOT a fraction and stays title junk.
CHAPTER_TITLE_RE = re.compile(
    r"\bChapter\s+(\d+(?:\.\d+)?)(?:\s*[:\-]\s*|\s+)?(.*)$", re.IGNORECASE
)
# Looser "Chapter N" finder for index-page link text.
CHAPTER_LIST_TEXT_RE = re.compile(r"\bChapter\s+(-?\d+)\b", re.IGNORECASE)
# Anchor-text number for (slug ↔ displayed) pair harvesting: also fractional and
# negative ("Chapter -1 Volume 2 Glossary" marks a non-chapter insert page).
_ANCHOR_NUM_RE = re.compile(r"\bChapter\s+(-?\d+(?:\.\d+)?)\b", re.IGNORECASE)


def _parse_chapter_number(raw: str) -> int | float:
    """"627" → 627 (int); "627.2" → 627.2 (float). Fractional side-chapter
    numbers stay floats so they sort between their integer neighbours."""
    return float(raw) if "." in raw else int(raw)

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

# ── fix-chapter-number-offset: displayed-number addressing (Phase 2) ─────────
# Key under which a walk/probe fetch's HTML is cached on ``ChapterMeta.extra`` so
# ``fetch_chapter`` can reuse it instead of re-fetching the same page a second
# time (offset-resolution reconciliation and content-fetch become ONE network
# hit per chapter, not two).
_PREFETCHED_HTML_KEY = "_prefetched_html"

# Padding (in slugs) added around a binary-search-resolved anchor before/after
# the walk, absorbing a local run of insert pages (Supreme Magus has a run of
# five between slugs 629 and 635) so the true boundary isn't missed by a
# couple of slugs when a caller supplies start_hint/end_hint.
_WALK_PADDING = 8


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
    raw: str, fallback_num: Optional[int | float]
) -> tuple[Optional[int | float], str]:
    """Return ``(chapter_number, title_only)``. ``title_only`` is the trimmed
    title text (site junk removed) or ``""`` for a bare-number heading. Returns
    ``(None, "")`` when the text is not a chapter heading at all. ``chapter_number``
    is a float for a fractional side-chapter ("Chapter 627.2" → 627.2)."""
    raw = _ws(raw)
    m = CHAPTER_TITLE_RE.search(raw)
    if not m:
        if not raw and fallback_num is not None:
            return fallback_num, ""
        return None, ""

    number = _parse_chapter_number(m.group(1))
    title = _ws(m.group(2))

    # Site-junk trimming only (NOT rewording).
    title = re.sub(r"\s*[|\-]\s*Empire\s*Novel.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*-\s*Read\s*Online.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*-\s*Free\s*Web\s*Novel.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*\|\s*Free\s*Web\s*Novel.*$", "", title, flags=re.IGNORECASE)
    title = title.strip(" -|:;.")
    return number, title


def _extract_title(
    soup: BeautifulSoup, fallback_num: Optional[int | float]
) -> tuple[Optional[int | float], str]:
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
    def build_chapter_index(
        self,
        spec: SiteSpec,
        *,
        fast_path: bool = False,
        start_hint: Optional[int | float] = None,
        end_hint: Optional[int | float] = None,
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
        if fast_path:
            html = rm.fetch(spec.url, use_browser=spec.use_browser, fast_path=True)
        else:
            html = rm.fetch(spec.url, use_browser=spec.use_browser)
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
        known: dict[int, tuple[str, Optional[int | float]]] = {}

        if not pairs:
            # Index page gave no free (slug, displayed) evidence at all — one
            # probe fetch decides identity vs. offset. This is the ONLY case
            # that costs an extra fetch beyond the index page itself.
            probe_slug = 1
            if start_hint is not None:
                probe_slug = max(1, min(int(round(start_hint)), count))
            probe_num = self._fetch_and_read(rm, spec, probe_slug, known)
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
                rm, spec, start_hint if start_hint is not None else 1, 1, count, known
            )
            walk_lo = max(1, anchor - _WALK_PADDING)
            if end_hint is None:
                walk_hi = count
            else:
                end_anchor = self._resolve_slug_for_displayed(
                    rm, spec, end_hint, anchor, count, known
                )
                walk_hi = min(count, end_anchor + _WALK_PADDING)

        return self._walk_and_reconcile(rm, spec, walk_lo, walk_hi, known)

    def _scan_index_page(
        self, soup: BeautifulSoup, spec: SiteSpec
    ) -> tuple[Optional[int], list[tuple[int, int | float]]]:
        """One pass over the index page's anchors: the highest SLUG (for
        ``range()`` generation) plus every (slug, displayed) pair where an
        anchor's own href AND visible text both carry a chapter number — free
        evidence for the identity-vs-offset decision, no extra fetch."""
        link_re = _chapter_link_re(spec)
        base = spec.base_url or spec.url
        numbers: list[int] = []
        pairs: list[tuple[int, int | float]] = []

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
    def _fetch_and_read(
        rm,
        spec: SiteSpec,
        slug: int,
        known: dict[int, tuple[str, Optional[int | float]]],
    ) -> Optional[int | float]:
        """Fetch (or reuse a cached) page for ``slug`` and return its displayed
        number, caching ``(html, number)`` in ``known`` so neither the binary
        search, the walk, nor a later ``fetch_chapter`` call ever re-fetches it."""
        if slug not in known:
            html = rm.fetch(chapter_url_for(spec, slug), use_browser=spec.use_browser)
            num, _title = _extract_title(BeautifulSoup(html, "html.parser"), None)
            known[slug] = (html, num)
        return known[slug][1]

    def _resolve_slug_for_displayed(
        self,
        rm,
        spec: SiteSpec,
        target: int | float,
        lo: int,
        hi: int,
        known: dict[int, tuple[str, Optional[int | float]]],
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
            num = self._fetch_and_read(rm, spec, mid, known)
            if num is not None and num >= target:
                best = mid
                hi = mid
            else:
                lo = mid + 1
        return best if best is not None else lo

    def _walk_and_reconcile(
        self,
        rm,
        spec: SiteSpec,
        walk_lo: int,
        walk_hi: int,
        known: dict[int, tuple[str, Optional[int | float]]],
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
            num = self._fetch_and_read(rm, spec, slug, known)
            html = known[slug][0]
            if num is None:
                skipped += 1
                self._warn(
                    f"{spec.novel_slug!r}: skipping slug {slug} (no parseable "
                    "positive chapter number — joke/glossary extra)."
                )
                continue
            metas.append(
                ChapterMeta(
                    index=num,
                    url=chapter_url_for(spec, slug),
                    slug=slug,
                    extra={_PREFETCHED_HTML_KEY: html},
                )
            )
        metas.sort(key=lambda m: m.index)
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
        if prefetched is not None:
            # Reconciliation during build_chapter_index already fetched this
            # exact page (offset novels) — reuse it instead of fetching twice.
            return self._extract_chapter(prefetched, meta)
        rm = self._manager(spec)
        # ``fast_path`` (0.2.0 §3.2): the conductor runs the primary under a bounded
        # camoufox budget that raises a typed signal so a hard chapter is handed to
        # the rescue lane while the primary moves on. Default False keeps the legacy
        # (full-ladder) behaviour for every existing caller and the WND path; only
        # pass the kwarg when set so existing test fakes keep their signature.
        if fast_path:
            html = rm.fetch(meta.url, use_browser=spec.use_browser, fast_path=True)
        else:
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
            # A fully-fetched, non-challenge page with no extractable body is an
            # extraction failure (its own class) — NOT a Cloudflare block. The
            # pipeline records it as a plain failure without auto-slowdown.
            raise EmptyExtractionError(
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
