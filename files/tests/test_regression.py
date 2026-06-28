"""Regression tests ported from the legacy harnesses.

Knowledge ported (NOT the files themselves) from the old
``files/legacy-reference/old-tests/test_heading_extraction.py`` and
``files/legacy-reference/test_chapter_range_loop.py``, which used an
``importlib``-loads-a-hyphenated-file harness. Here the same hard-won cases are rewritten as standard
offline pytest functions against the new adapter API, with small fixtures under
``files/test-files/``.

Cases covered:
  - invisible / zero-width characters in titles (ZWNJ, NBSP, soft hyphen)
  - duplicate heading paragraph (heading repeated as a body paragraph)
  - translator-credit line detection (filtered as noise)
  - degraded title (empty/missing) → "Chapter N." without crashing
  - chapter-count mismatch surfaced as a warning (not a silent skip)
  - duplicate-content skip (identical consecutive bodies → second skipped)
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from webnovel_scraper import catalog
from webnovel_scraper.adapters import freewebnovel as fwn
from webnovel_scraper.adapters.freewebnovel import (
    FreeWebNovelAdapter,
    chapter_url_for,
    filter_duplicate_consecutive_chapters,
)
from webnovel_scraper.models import ChapterContent, ChapterMeta

FIXTURES = Path(__file__).resolve().parents[2] / "files" / "test-files"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeRM:
    """Offline stand-in for RequestManager: serves fixture HTML by URL."""

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


# ── Invisible / zero-width characters in titles ──────────────────────────────
def test_strip_invisible_removes_zwnj_nbsp_soft_hyphen() -> None:
    # ZWNJ (U+200C), NBSP (U+00A0), soft hyphen (U+00AD) injected between words.
    raw = "Chapter‌ 2261:‌ New­ Anti-Fang‌ Alliance"
    assert fwn._strip_invisible(raw) == "Chapter 2261: New Anti-Fang Alliance"


def test_invisible_title_extracted_cleanly_from_live_fixture() -> None:
    html = _fixture("reverend_insanity_chapter_2261.html")
    meta = ChapterMeta(index=2261, url=chapter_url_for(RI_SPEC, 2261))
    adapter = FreeWebNovelAdapter(request_manager=FakeRM(default_html=html))
    content = adapter.fetch_chapter(meta, RI_SPEC)
    assert content.title == "New Anti-Fang Alliance"
    assert content.heading == "Chapter 2261: New Anti-Fang Alliance."
    assert content.paragraphs  # body survived
    # No invisible characters left in the title (ZWNJ U+200C, NBSP U+00A0).
    assert "‌" not in content.title and " " not in content.title


# ── Duplicate heading paragraph ──────────────────────────────────────────────
def test_duplicate_heading_paragraph_removed() -> None:
    html = _fixture("fwn_chapter_clean.html")
    meta = ChapterMeta(index=5, url=chapter_url_for(SS_SPEC, 5))
    adapter = FreeWebNovelAdapter(request_manager=FakeRM(default_html=html))
    content = adapter.fetch_chapter(meta, SS_SPEC)
    # The "<strong>Chapter 5: The Test</strong>" body paragraph must be gone.
    assert all(not fwn.CHAPTER_TITLE_RE.match(p) for p in content.paragraphs)


# ── Translator-credit line ───────────────────────────────────────────────────
def test_translator_credit_line_filtered() -> None:
    html = _fixture("fwn_chapter_clean.html")
    meta = ChapterMeta(index=5, url=chapter_url_for(SS_SPEC, 5))
    adapter = FreeWebNovelAdapter(request_manager=FakeRM(default_html=html))
    content = adapter.fetch_chapter(meta, SS_SPEC)
    assert all("Translator" not in p for p in content.paragraphs)
    assert all("Editor" not in p for p in content.paragraphs)


# ── Degraded title ───────────────────────────────────────────────────────────
def test_degraded_title_falls_back_without_crash() -> None:
    html = _fixture("fwn_chapter_degraded.html")
    meta = ChapterMeta(index=7, url=chapter_url_for(SS_SPEC, 7))
    adapter = FreeWebNovelAdapter(request_manager=FakeRM(default_html=html))
    content = adapter.fetch_chapter(meta, SS_SPEC)
    assert content.title == ""
    assert content.is_degraded
    assert content.heading == "Chapter 7."
    assert content.paragraphs  # body still captured
    assert any("no title text" in w for w in adapter.warnings)


# ── Chapter-count mismatch warning (not a silent skip) ───────────────────────
def test_chapter_count_mismatch_surfaces_warning() -> None:
    toc = _fixture("fwn_toc.html")  # index page max chapter == 50
    # Catalog override claims 60 — disagreement must be surfaced, not silent.
    spec = dataclasses.replace(SS_SPEC, chapter_count=60)
    adapter = FreeWebNovelAdapter(request_manager=FakeRM({spec.url: toc}))
    metas = adapter.build_chapter_index(spec)
    assert len(metas) == 60  # honours the override
    assert any("mismatch" in w.lower() for w in adapter.warnings)


# ── Duplicate-content skip ───────────────────────────────────────────────────
def test_duplicate_content_consecutive_skipped_with_warning() -> None:
    body = ["Identical paragraph one.", "Identical paragraph two."]
    chapters = [
        ChapterContent(index=5, title="A", paragraphs=list(body)),
        ChapterContent(index=6, title="B", paragraphs=list(body)),  # dup body
        ChapterContent(index=7, title="C", paragraphs=["A different body."]),
    ]
    logs: list[str] = []
    kept = filter_duplicate_consecutive_chapters(chapters, log=logs.append)
    assert [c.index for c in kept] == [5, 7]
    assert any("DUPLICATE CONTENT" in m and "chapter 6" in m for m in logs)
