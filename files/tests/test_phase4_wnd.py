"""Phase 4 tests: the WebNovel-dynamic adapter. All offline (fixtures + a fake
RequestManager); no network.

Covers TOC discovery from ``__NEXT_DATA__`` (count/order/titles/URLs, no gaps),
the ``__NEXT_DATA__``-absent error path (no silent empty index), chapter body
extraction via the JSON primary path and the DOM-selector fallback, ``_is_junky``
filtering, chapter-index persistence (build once, reload without network), and
registry/disabled-adapter consistency for The Noble Queen.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from webnovel_scraper import catalog
from webnovel_scraper.adapters import webnovel_dynamic as wnd
from webnovel_scraper.adapters.webnovel_dynamic import WebNovelDynamicAdapter
from webnovel_scraper.models import ChapterMeta
from webnovel_scraper.registry import (
    AdapterDisabledError,
    get_adapter,
    get_adapter_for_spec,
)

FIXTURES = Path(__file__).resolve().parents[2] / "files" / "test-files"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeRM:
    """Offline stand-in for RequestManager: serves fixture HTML by URL and
    records what was fetched (so a test can assert the network was not touched)."""

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


NQ_SPEC = catalog.get_spec("the-noble-queen", "webnovel_dynamic")


# ── build_chapter_index: __NEXT_DATA__ TOC parse ─────────────────────────────
def test_build_chapter_index_parses_next_data_toc(tmp_path) -> None:
    toc = _fixture("wnd_next_data_toc.html")
    adapter = WebNovelDynamicAdapter(
        request_manager=FakeRM({NQ_SPEC.url: toc})
    )
    metas = adapter.build_chapter_index(NQ_SPEC)

    # 5 chapters across 2 volumes, contiguous, in order.
    assert len(metas) == 5
    assert [m.index for m in metas] == [1, 2, 3, 4, 5]
    assert [m.title for m in metas] == [
        "The Beginning",
        "Chapter 2: The Road",
        "The Gate",
        "The Keeper",
        "The Crown",
    ]
    # URLs are built from base_url / story / book_id / chapterId.
    assert (
        metas[0].url
        == "https://dynamic.webnovel.com/story/28684090500376805/1001"
    )
    assert metas[4].source_id == "1005"
    # chapterCnt == 5 matches, so no mismatch warning; no gaps either.
    assert adapter.warnings == []


def test_build_chapter_index_no_next_data_raises(tmp_path) -> None:
    page = _fixture("wnd_no_next_data.html")
    adapter = WebNovelDynamicAdapter(
        request_manager=FakeRM({NQ_SPEC.url: page})
    )
    with pytest.raises(RuntimeError, match="__NEXT_DATA__"):
        adapter.build_chapter_index(NQ_SPEC)


def test_build_chapter_index_parses_live_g_data_book_shape() -> None:
    """Live WebNovel now exposes the TOC through g_data.book, not __NEXT_DATA__."""
    page = (
        '<html><body><script nonce="">g_data.book= {'
        '"bookInfo":{"bookId":"28684090500376805","chapterNum":2},'
        '"volumeItems":[{"chapterItems":['
        '{"chapterIndex":1,"chapterId":"76998299197949227",'
        '"chapterName":"Glory\\ to\\ the\\ Victor"},'
        '{"chapterIndex":2,"chapterId":"76998336242047085",'
        '"chapterName":"Train\\ of\\ Thought"}'
        "]}]};</script></body></html>"
    )
    adapter = WebNovelDynamicAdapter(
        request_manager=FakeRM({NQ_SPEC.url: page})
    )
    metas = adapter.build_chapter_index(NQ_SPEC)
    assert [m.index for m in metas] == [1, 2]
    assert [m.title for m in metas] == ["Glory to the Victor", "Train of Thought"]
    assert metas[0].url.endswith("/76998299197949227")


# ── fetch_chapter: __NEXT_DATA__ primary path ────────────────────────────────
def test_fetch_chapter_via_next_data(tmp_path) -> None:
    html = _fixture("wnd_next_data_chapter.html")
    meta = ChapterMeta(
        index=3,
        url="https://dynamic.webnovel.com/story/28684090500376805/1003",
        title="The Gate",
    )
    adapter = WebNovelDynamicAdapter(
        request_manager=FakeRM(default_html=html)
    )
    content = adapter.fetch_chapter(meta, NQ_SPEC)

    assert content.index == 3
    assert content.title == "The Gate"
    assert content.heading == "Chapter 3: The Gate."
    assert len(content.paragraphs) == 3
    assert content.paragraphs[0].startswith("Sunny stepped through the gate")


def test_fetch_chapter_parses_live_g_data_chapinfo_shape() -> None:
    """Live WebNovel chapter bodies are in g_data.chapInfo with HTML fragments."""
    html = (
        '<html><body><script nonce="">g_data.chapInfo= {'
        '"chapterId":"76998299197949227",'
        '"chapterName":"Glory\\ to\\ the\\ Victor",'
        '"chapterIndex":1,'
        '"contents":['
        '{"content":"<p>One\\ strike\\ after\\ another.</p>"},'
        '{"content":"<p>Next\\ chapter</p>"},'
        '{"content":"<p>Queen\\ Bee\\ adjusted\\ her\\ stance.</p>"}'
        "]};</script></body></html>"
    )
    meta = ChapterMeta(
        index=1,
        url="https://dynamic.webnovel.com/story/28684090500376805/76998299197949227",
        title=None,
    )
    adapter = WebNovelDynamicAdapter(
        request_manager=FakeRM(default_html=html)
    )
    content = adapter.fetch_chapter(meta, NQ_SPEC)
    assert content.title == "Glory to the Victor"
    assert content.heading == "Chapter 1: Glory to the Victor."
    assert content.paragraphs == [
        "One strike after another.",
        "Queen Bee adjusted her stance.",
    ]


# ── fetch_chapter: DOM fallback path ─────────────────────────────────────────
def test_fetch_chapter_dom_fallback(tmp_path) -> None:
    html = _fixture("wnd_dom_fallback_chapter.html")  # no __NEXT_DATA__ at all
    meta = ChapterMeta(
        index=4,
        url="https://dynamic.webnovel.com/story/28684090500376805/1004",
        title=None,  # force the adapter to take the DOM-extracted title
    )
    adapter = WebNovelDynamicAdapter(
        request_manager=FakeRM(default_html=html)
    )
    content = adapter.fetch_chapter(meta, NQ_SPEC)

    assert content.title == "The Keeper"  # from the DOM <h1> title selector
    assert content.heading == "Chapter 4: The Keeper."
    # The two real paragraphs survive; the "Next chapter" / login-comment junk
    # is filtered out.
    assert len(content.paragraphs) == 2
    assert all("Next chapter" not in p for p in content.paragraphs)
    assert all("login" not in p.lower() for p in content.paragraphs)


# ── _is_junky filtering ──────────────────────────────────────────────────────
def test_is_junky_filters_chrome_and_passes_prose() -> None:
    assert wnd._is_junky("Next chapter")
    assert wnd._is_junky("Please login to leave a comment")
    assert wnd._is_junky("Tap to open reader setting")
    assert not wnd._is_junky(
        "Sunny stepped through the gate into the gloom and kept walking."
    )


def test_fetch_chapter_filters_junky_next_data(tmp_path) -> None:
    html = _fixture("wnd_junky_content.html")
    meta = ChapterMeta(
        index=6,
        url="https://dynamic.webnovel.com/story/28684090500376805/1006",
        title="The Junk Test",
    )
    adapter = WebNovelDynamicAdapter(
        request_manager=FakeRM(default_html=html)
    )
    content = adapter.fetch_chapter(meta, NQ_SPEC)

    # Only the two genuine prose paragraphs survive the junk filter.
    assert len(content.paragraphs) == 2
    assert content.paragraphs[0].startswith("A real opening paragraph")
    assert all("login" not in p.lower() for p in content.paragraphs)
    assert all("reader setting" not in p.lower() for p in content.paragraphs)


# ── Stateless TOC: no stale slug-scoped cache (Phase 8 bug-hunt #1) ──────────
def _toc_html(count: int) -> str:
    """A minimal ``__NEXT_DATA__`` TOC page with ``count`` contiguous chapters."""
    items = ", ".join(
        '{"chapterIndex": %d, "chapterId": "%d", "chapterName": "Chapter %d"}'
        % (n, 1000 + n, n)
        for n in range(1, count + 1)
    )
    return (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        '{"props": {"pageProps": {"data": {'
        '"bookInfo": {"bookId": "28684090500376805", "chapterCnt": %d}, '
        '"volumeItems": [{"volumeId": 1, "chapterItems": [%s]}]'
        "}}}}"
        "</script></body></html>"
    ) % (count, items)


def test_build_chapter_index_no_stale_cache_reflects_grown_toc() -> None:
    """The adapter must always re-parse the live TOC. A second run after the
    novel has gained chapters reflects the *current* count — it is never served
    a stale chapter list from an adapter-owned cache (the adapter keeps none;
    the pipeline owns TOC persistence, output-dir-scoped)."""
    # First run sees 5 chapters.
    rm1 = FakeRM({NQ_SPEC.url: _toc_html(5)})
    first = WebNovelDynamicAdapter(request_manager=rm1).build_chapter_index(NQ_SPEC)
    assert [m.index for m in first] == [1, 2, 3, 4, 5]
    assert rm1.fetched == [NQ_SPEC.url]  # the TOC was actually fetched

    # The novel later gains chapters. A *fresh* adapter for the SAME slug must
    # reflect the new count, not a stale 5 short-circuited from a prior run.
    rm2 = FakeRM({NQ_SPEC.url: _toc_html(8)})
    second = WebNovelDynamicAdapter(request_manager=rm2).build_chapter_index(NQ_SPEC)
    assert [m.index for m in second] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert rm2.fetched == [NQ_SPEC.url]  # re-parsed live, not short-circuited


def test_build_chapter_index_keeps_no_own_index_cache(tmp_path) -> None:
    """The adapter used to write ``files/cache/{slug}/chapter_index.json`` next to
    the request manager's cache dir — that slug-scoped cache was the stale-TOC
    bug. Give the fake manager a cache dir and prove nothing is written there."""
    rm = FakeRM({NQ_SPEC.url: _toc_html(3)})
    rm.cache_dir = tmp_path
    WebNovelDynamicAdapter(request_manager=rm).build_chapter_index(NQ_SPEC)
    assert not (tmp_path / "chapter_index.json").exists()
    assert list(tmp_path.iterdir()) == []  # nothing written at all


# ── Registry / disabled-adapter consistency for The Noble Queen ──────────────
def test_registry_resolves_webnovel_dynamic() -> None:
    assert isinstance(get_adapter("webnovel_dynamic"), WebNovelDynamicAdapter)
    assert isinstance(get_adapter_for_spec(NQ_SPEC), WebNovelDynamicAdapter)


def test_disabled_novel_bin_for_noble_queen_refused() -> None:
    nb_spec = catalog.get_spec("the-noble-queen", "novel_bin")
    with pytest.raises(AdapterDisabledError):
        get_adapter_for_spec(nb_spec)
