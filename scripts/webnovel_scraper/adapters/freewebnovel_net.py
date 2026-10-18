"""FreeWebNovel ``.net`` adapter (v0.3.0 Checkpoint 04). ENABLED for the novels validated live.

``freewebnovel.net`` is a different website from ``freewebnovel.com``, not a mirror with the same
addresses (live evidence 2026-10-18, recorded in the plan §9.15):

* The novel's index is ``/<novel>.html``; its chapter list is paginated as ``/<novel>.html?page=N``,
  40 chapters a page, with a page selector (``#indexselect``) whose options name every page and its
  position range ("C.41 - C.80") and a "Latest Chapters" widget (``.m-newest1``).
* Chapter addresses are descriptive and cannot be generated from a number
  (``/lord-of-the-mysteries/chapter-1-crimson.html``), so the list pages are the only source of
  addresses. The site's own ``/ajax-chapter-list`` endpoint is never used (``robots.txt`` disallows
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
_LABEL_KEY = "manifest_label"


@dataclass
class ListPage:
    number: int
    entries: list = field(default_factory=list)          # (label text, title, absolute url)
    ranges: dict = field(default_factory=dict)            # page number -> (first, last) position
    urls: dict = field(default_factory=dict)              # page number -> absolute list-page url
    latest: list = field(default_factory=list)            # labels in the latest-chapters widget
    unnumbered: list = field(default_factory=list)        # entry texts without a chapter number


def _novel_path(spec: SiteSpec) -> str:
    """``/lord-of-the-mysteries`` from ``https://freewebnovel.net/lord-of-the-mysteries.html``."""
    path = urlsplit(spec.url).path
    return path[:-5] if path.endswith(".html") else path.rstrip("/")


def _entry(anchor, base: str, novel_path: str):
    href = anchor.get("href") or ""
    absolute = urljoin(base, href)
    parts = urlsplit(absolute)
    if parts.scheme != "https" or (parts.hostname or "") != urlsplit(base).hostname:
        return None
    if not parts.path.startswith(novel_path + "/chapter-") or not parts.path.endswith(".html"):
        return None
    raw = anchor.get("title") or anchor.get_text(" ", strip=True)
    raw = normalize_text(html_lib.unescape(raw))
    label, _rest = parse_heading(raw)
    return label, raw, f"https://{parts.hostname}{parts.path}"


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
        label, raw, url = parsed
        if label is None:
            page.unnumbered.append(raw)
            continue
        page.entries.append((label, clean_title(raw, label), url))
    select = soup.select_one("#indexselect")
    if select is not None:
        for option in select.find_all("option"):
            try:
                value = int(str(option.get("value", "")).strip())
            except ValueError:
                continue
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
        ranges = dict(first.ranges) or {1: (1, len(first.entries))}
        count = max(ranges)
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
        # numbering drifts from its positions); bounded by the page count.
        for _ in range(count):
            labels = [_number(e[0]) for p in pages.values() for e in p.entries]
            grow = set()
            first_read = min(n for n in pages if n > 1) if len(pages) > 1 else 1
            if start_hint is not None and labels and min(labels) > start_hint and first_read > 1:
                grow.add(first_read - 1)
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
        return ChapterIndex(metas, tail_confirmed=self._tail_confirmed(spec, pages, ranges, count))

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
