"""FreeWebNovel ``.net`` adapter (v0.3.0 Checkpoint 04). ENABLED for the novels validated live.

``freewebnovel.net`` is a different website from ``freewebnovel.com``, not a mirror with the same
addresses (live evidence 2026-10-18, recorded in the plan §9.15):

* The novel's index is ``/<novel>.html``; its chapter list is paginated as ``/<novel>.html?page=N``,
  40 chapters a page, with a page selector (``#indexselect``) whose options name every page and its
  position range ("C.41 - C.80") and a "Latest Chapters" widget (``.m-newest1``).
* Chapter addresses are descriptive and cannot be generated from a number
  (``/lord-of-the-mysteries/chapter-1-crimson.html``), so the list pages are the only source of
  addresses. A few are misspelt by the site and are used exactly as listed (Lord of the Mysteries
  1303 is ``/lord-of-the-mysteries/chaspter-1303-coming-to-life.html``); such an entry is taken only
  when its address is a single ``word-N-….html`` page of the same novel whose N equals the entry's
  chapter number. The site's own ``/ajax-chapter-list`` endpoint is never used (``robots.txt`` disallows
  ``/ajax-``).
* A chapter page has the same reader structure as ``.com`` closely enough for the shared FreeWebNovel
  extraction: heading ``h1.chapter``, body ``#chapter-content`` (the reader's text-to-speech widget
  and report form sit outside it). So this adapter reuses :class:`FreeWebNovelAdapter` for chapters
  (including the displayed-number check) and only replaces chapter-list discovery.
* Plain HTTP worked for every sampled page with no challenge, so catalog rows use the HTTP path
  (``use_browser=False``); the request manager's escalation ladder still reaches a browser if a
  challenge ever appears. List pages are fetched fresh (never from the HTML cache).

Discovery reads only the list pages a request needs: the first page (page count, ranges and the
latest-chapters widget) plus the pages whose ranges overlap the requested window, one page of margin
each side, widening while the window's ends are not yet found. An open-ended request always reads
the last page. The returned list claims its tail only when the last page was read and agrees with the
selector's range and the latest-chapters widget; otherwise ``tail_confirmed`` is False and the
preflight will not clamp an End to it.
"""

from __future__ import annotations

import html as html_lib
import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ..content import clean_title, label_text, normalize_text, parse_heading
from ..models import ChapterIndex, ChapterMeta, SiteSpec
from .freewebnovel import FreeWebNovelAdapter

logger = logging.getLogger(__name__)

PAGE_SIZE = 40                  # chapters per list page (observed; the selector is authoritative)
_PAGE_MARGIN = 1                # extra list pages read on each side of the requested window
_RANGE_RE = re.compile(r"C\.\s*(\d+)\s*-\s*C\.\s*(\d+)", re.IGNORECASE)
# A chapter page under the novel's folder whose prefix is not spelt "chapter" ("chaspter-1303-…").
_IRREGULAR_RE = re.compile(r"[a-z]+-(\d+)(?:-[a-z0-9-]*)?\.html")
_LABEL_KEY = "manifest_label"


@dataclass
class ListPage:
    number: int
    entries: list = field(default_factory=list)          # (label text, title, absolute url)
    ranges: dict = field(default_factory=dict)            # page number -> (first, last) position
    urls: dict = field(default_factory=dict)              # page number -> absolute list-page url
    latest: list = field(default_factory=list)            # labels in the latest-chapters widget
    unnumbered: list = field(default_factory=list)        # entry texts without a chapter number
    offered: set = field(default_factory=set)             # every page number the selector offers
    irregular: list = field(default_factory=list)         # (label, path) entries at misspelt addresses


def _novel_path(spec: SiteSpec) -> str:
    """``/lord-of-the-mysteries`` from ``https://freewebnovel.net/lord-of-the-mysteries.html``."""
    path = urlsplit(spec.url).path
    return path[:-5] if path.endswith(".html") else path.rstrip("/")


def _entry(anchor, base: str, novel_path: str):
    """``(label, raw text, url, irregular)`` for a chapter link of this novel, else None. A link
    whose address is not ``<novel>/chapter-….html`` counts only when it is one ``word-N-….html`` page
    in the novel's folder and N is the whole chapter number its text shows; nothing is corrected."""
    href = anchor.get("href") or ""
    absolute = urljoin(base, href)
    parts = urlsplit(absolute)
    if parts.scheme != "https" or (parts.hostname or "") != urlsplit(base).hostname:
        return None
    raw = anchor.get("title") or anchor.get_text(" ", strip=True)
    raw = normalize_text(html_lib.unescape(raw))
    label, _rest = parse_heading(raw)
    url = f"https://{parts.hostname}{parts.path}"
    if parts.path.startswith(novel_path + "/chapter-") and parts.path.endswith(".html"):
        return label, raw, url, False
    folder, _, name = parts.path.rpartition("/")
    match = _IRREGULAR_RE.fullmatch(name)
    if folder != novel_path or match is None or label is None or label != match.group(1):
        return None
    return label, raw, url, True


def parse_list_page(html: str, spec: SiteSpec, number: int) -> ListPage:
    """One list page: its chapter entries (in page order), the page selector and the latest widget."""
    soup = BeautifulSoup(html, "html.parser")
    base = f"https://{urlsplit(spec.url).hostname}/"
    novel_path = _novel_path(spec)
    page = ListPage(number=number)
    container = soup.select_one("#idData")
    anchors = container.select("a[href]") if container is not None else []
    for anchor in anchors:
        parsed = _entry(anchor, base, novel_path)
        if parsed is None:
            continue
        label, raw, url, irregular = parsed
        if label is None:
            page.unnumbered.append(raw)
            continue
        if irregular:
            page.irregular.append((label, urlsplit(url).path))
        page.entries.append((label, clean_title(raw, label), url))
    select = soup.select_one("#indexselect")
    if select is not None:
        for option in select.find_all("option"):
            try:
                value = int(str(option.get("value", "")).strip())
            except ValueError:
                continue
            page.offered.add(value)
            match = _RANGE_RE.search(option.get_text(" ", strip=True))
            if match:
                page.ranges[value] = (int(match.group(1)), int(match.group(2)))
            data_url = option.get("data-url")
            if data_url:
                page.urls[value] = urljoin(base, data_url)
    for anchor in soup.select(".m-newest1 a[href]"):
        parsed = _entry(anchor, base, novel_path)
        if parsed is not None and parsed[0] is not None:
            page.latest.append(parsed[0])
    return page


def _number(label: str) -> float:
    return float(label)


def _read_spans(pages: dict) -> tuple[tuple[int, int], ...]:
    """The chapter-number span of each unbroken run of list pages that was read (the pages between
    two runs were not read, so nothing is known about the chapters they list)."""
    spans: list[tuple[int, int]] = []
    run: list[int] = []
    for number in sorted(pages) + [None]:
        if number is not None and (not run or number == run[-1] + 1):
            run.append(number)
            continue
        values = [_number(e[0]) for n in run for e in pages[n].entries]
        if values:
            spans.append((int(min(values)), int(max(values))))
        run = [number]
    return tuple(spans)


class FreeWebNovelNetAdapter(FreeWebNovelAdapter):
    """``freewebnovel.net``: paginated chapter list, descriptive chapter addresses."""

    key = "freewebnovel_net"
    enabled = True

    def _list_page(self, rm, spec: SiteSpec, number: int, url: str) -> ListPage:
        html = rm.fetch(url, use_browser=spec.use_browser, use_cache=False)
        return parse_list_page(html, spec, number)

    def build_chapter_index(
        self,
        spec: SiteSpec,
        *,
        fast_path: bool = False,
        start_hint: Optional[int | float] = None,
        end_hint: Optional[int | float] = None,
        resume_metas: Optional[list[ChapterMeta]] = None,
        index_progress_cb: Optional[Callable[[list[ChapterMeta], int, int], None]] = None,
    ) -> ChapterIndex:
        rm = self._manager(spec)
        first = self._list_page(rm, spec, 1, spec.url)
        if not first.entries:
            raise RuntimeError(
                f"The chapter list page of {spec.novel_slug!r} ({spec.url}) lists no chapters; the "
                "site's layout may have changed."
            )
        count = max([1, *first.ranges, *first.urls, *first.offered])
        # A page whose selector text gives no "C.a - C.b" range (a layout change) is placed by the
        # first page's size; the window then widens to the real numbers, and the list's end is not
        # vouched for.
        size = max(1, len(first.entries) + len(first.unnumbered))
        ranges = {n: first.ranges.get(n, ((n - 1) * size + 1, n * size)) for n in range(1, count + 1)}
        guessed = len(first.ranges) < count if count > 1 else False
        if guessed:
            self._warn(
                f"{spec.novel_slug!r}: the chapter-list page selector does not say which chapters each "
                "page holds; pages are estimated and the list's end is not treated as the novel's end."
            )
        urls = dict(first.urls)
        urls.setdefault(1, spec.url)
        pages: dict[int, ListPage] = {1: first}

        def page_for(position: float) -> int:
            for number, (a, b) in sorted(ranges.items()):
                if a <= position <= b:
                    return number
            return 1 if position < min(a for a, _ in ranges.values()) else count

        low = 1 if start_hint is None else page_for(max(1, int(start_hint)))
        high = count if end_hint is None else page_for(int(end_hint))
        wanted = set(range(max(1, low - _PAGE_MARGIN), min(count, high + _PAGE_MARGIN) + 1))
        if end_hint is None:
            wanted.add(count)

        def fetch(numbers) -> None:
            for number in sorted(numbers):
                if number in pages:
                    continue
                url = urls.get(number) or f"{spec.url}?page={number}"
                pages[number] = self._list_page(rm, spec, number, url)

        fetch(wanted)
        # Widen while the requested window's ends are not inside what was read (a list whose
        # numbering drifts from its positions); bounded by the page count. Only the window's own
        # pages count: page 1 is always read, but says nothing about the chapters near Start.
        window_low = min(wanted)
        for _ in range(count):
            labels = [_number(e[0]) for n, p in pages.items() if n >= window_low for e in p.entries]
            grow = set()
            if start_hint is not None and labels and min(labels) > start_hint and window_low > 1:
                window_low -= 1
                grow.add(window_low)
            last_read = max(pages)
            if end_hint is not None and labels and max(labels) < end_hint and last_read < count:
                grow.add(last_read + 1)
            grow -= set(pages)
            if not grow:
                break
            fetch(grow)

        metas: list[ChapterMeta] = []
        seen: set[str] = set()
        unnumbered = 0
        for number in sorted(pages):
            unnumbered += len(pages[number].unnumbered)
            for label, path in pages[number].irregular:
                self._log(f"{spec.novel_slug!r}: chapter {label} is listed at an irregular address "
                          f"({path}); it is used exactly as the site lists it.")
            for label, title, url in pages[number].entries:
                if url in seen:
                    continue
                seen.add(url)
                index = int(label) if "." not in label else float(label)
                metas.append(ChapterMeta(index=index, url=url, title=title or None,
                                         extra={_LABEL_KEY: label_text(label)}))
        if unnumbered:
            self._warn(
                f"{spec.novel_slug!r}: {unnumbered} chapter-list entr{'y' if unnumbered == 1 else 'ies'} "
                "without a chapter number were skipped."
            )
        tail = False if guessed else self._tail_confirmed(spec, pages, ranges, count)
        return ChapterIndex(metas, tail_confirmed=tail, examined=_read_spans(pages))

    def _tail_confirmed(self, spec: SiteSpec, pages: dict, ranges: dict, count: int) -> bool:
        """The list reaches the novel's real last chapter only if its last page was read, holds as
        many entries as the selector says, and agrees with the latest-chapters widget."""
        last = pages.get(count)
        if last is None or not last.entries:
            return False
        a, b = ranges.get(count, (None, None))
        if a is not None and len(last.entries) + len(last.unnumbered) != b - a + 1:
            self._warn(
                f"{spec.novel_slug!r}: the last chapter-list page holds {len(last.entries)} entries "
                f"but its range says {b - a + 1}; the list's end is not treated as the novel's end."
            )
            return False
        top = max(_number(e[0]) for e in last.entries)
        latest = [_number(label) for label in pages[1].latest]
        if latest and max(latest) > top:
            self._warn(
                f"{spec.novel_slug!r}: the latest-chapters widget shows chapter "
                f"{label_text(max(latest))} but the list ends at {label_text(top)}; the list's end is "
                "not treated as the novel's end."
            )
            return False
        return True
