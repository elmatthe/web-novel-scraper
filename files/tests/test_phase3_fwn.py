"""Phase 3 tests: the FreeWebNovel adapter. All offline (fixtures + a fake
RequestManager); no network.

Covers TOC completeness (the silent-skip regression), chapter title + body
extraction, invisible-char title cleaning, degraded-title fallback, noise
filtering, and consecutive-paragraph dedup.
"""

from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup

from webnovel_scraper import catalog
from webnovel_scraper.adapters import freewebnovel as fwn
from webnovel_scraper.adapters.freewebnovel import (
    FreeWebNovelAdapter,
    chapter_url_for,
)
from webnovel_scraper.models import ChapterMeta

FIXTURES = Path(__file__).resolve().parents[2] / "files" / "test-files"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeRM:
    """Serves fixture HTML by URL; records what was fetched."""

    def __init__(self, html_by_url=None, default_html=None) -> None:
        self.html_by_url = html_by_url or {}
        self.default_html = default_html
        self.fetched: list[str] = []

    def fetch(self, url, *, use_browser=False, use_cache=None) -> str:
        self.fetched.append(url)
        if url in self.html_by_url:
            return self.html_by_url[url]
        if self.default_html is not None:
            return self.default_html
        raise KeyError(url)

    def start(self):
        return self

    def close(self):
        pass


SS_SPEC = catalog.get_spec("shadow-slave", "freewebnovel")
RI_SPEC = catalog.get_spec("reverend-insanity", "freewebnovel")


# ── URL generation (generalized over the index URL, no per-novel hardcoding) ──
def test_chapter_url_generation_derives_from_index_url() -> None:
    assert (
        chapter_url_for(SS_SPEC, 5)
        == "https://freewebnovel.com/novel/shadow-slave/chapter-5"
    )
    sm_spec = catalog.get_spec("supreme-magus", "freewebnovel")
    assert (
        chapter_url_for(sm_spec, 12)
        == "https://freewebnovel.com/novel/supreme-magus-novel/chapter-12"
    )


# ── build_chapter_index: completeness + no gaps (silent-skip regression) ──────
def test_build_chapter_index_complete_no_gaps() -> None:
    toc = _fixture("fwn_toc.html")  # newest widget shows max chapter 50, with
    #                                 chapters 4..47 NOT rendered (a gap).
    adapter = FreeWebNovelAdapter(request_manager=FakeRM({SS_SPEC.url: toc}))
    metas = adapter.build_chapter_index(SS_SPEC)

    # Complete 1..50 despite the landing page only rendering a slice — this is
    # the fix for the legacy silent-skip bug.
    assert len(metas) == 50
    assert [m.index for m in metas] == list(range(1, 51))
    # The gap chapters (e.g. 25) that the landing page never rendered are
    # present and point at generated URLs.
    gap = next(m for m in metas if m.index == 25)
    assert gap.url == "https://freewebnovel.com/novel/shadow-slave/chapter-25"


def test_build_chapter_index_count_override() -> None:
    import dataclasses

    toc = _fixture("fwn_toc.html")
    spec = dataclasses.replace(SS_SPEC, chapter_count=120)
    adapter = FreeWebNovelAdapter(request_manager=FakeRM({spec.url: toc}))
    metas = adapter.build_chapter_index(spec)
    assert [m.index for m in metas] == list(range(1, 121))


# ── fetch_chapter: title + body extraction ───────────────────────────────────
def test_fetch_chapter_extracts_title_and_paragraphs() -> None:
    html = _fixture("fwn_chapter_clean.html")
    meta = ChapterMeta(index=5, url=chapter_url_for(SS_SPEC, 5))
    adapter = FreeWebNovelAdapter(request_manager=FakeRM(default_html=html))
    content = adapter.fetch_chapter(meta, SS_SPEC)

    assert content.index == 5
    assert content.title == "The Test"
    assert content.heading == "Chapter 5: The Test."
    # The three real body paragraphs survive; junk is gone.
    assert len(content.paragraphs) == 3
    assert content.paragraphs[0].startswith("Sunny opened his eyes")


def test_fetch_chapter_filters_noise_snippets() -> None:
    html = _fixture("fwn_chapter_clean.html")
    meta = ChapterMeta(index=5, url=chapter_url_for(SS_SPEC, 5))
    adapter = FreeWebNovelAdapter(request_manager=FakeRM(default_html=html))
    content = adapter.fetch_chapter(meta, SS_SPEC)
    # "Report chapter" is a NOISE_SNIPPET and must not appear.
    assert all("report chapter" not in p.lower() for p in content.paragraphs)


def test_fetch_chapter_invisible_char_title_cleaned() -> None:
    html = _fixture("reverend_insanity_chapter_2261.html")
    meta = ChapterMeta(index=2261, url=chapter_url_for(RI_SPEC, 2261))
    adapter = FreeWebNovelAdapter(request_manager=FakeRM(default_html=html))
    content = adapter.fetch_chapter(meta, RI_SPEC)
    assert content.heading == "Chapter 2261: New Anti-Fang Alliance."


def test_fetch_chapter_degraded_title_fallback() -> None:
    html = _fixture("fwn_chapter_degraded.html")
    meta = ChapterMeta(index=7, url=chapter_url_for(SS_SPEC, 7))
    adapter = FreeWebNovelAdapter(request_manager=FakeRM(default_html=html))
    content = adapter.fetch_chapter(meta, SS_SPEC)
    assert content.is_degraded
    assert content.heading == "Chapter 7."
    assert content.paragraphs


# ── Consecutive-paragraph dedup inside body extraction ───────────────────────
def test_extract_paragraphs_dedupes_consecutive_duplicates() -> None:
    html = (
        "<div id='article'>"
        "<p>A repeated sentence that is long enough to pass the filter.</p>"
        "<p>A repeated sentence that is long enough to pass the filter.</p>"
        "<p>A different following sentence, also long enough to survive.</p>"
        "</div>"
    )
    soup = BeautifulSoup(html, "html.parser")
    paras = fwn._extract_paragraphs(soup, heading_line="Chapter 1: X.")
    assert len(paras) == 2  # the consecutive duplicate collapsed to one
