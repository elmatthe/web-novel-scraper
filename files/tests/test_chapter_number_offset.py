"""fix-chapter-number-offset: model + fixtures + addressing-behaviour tests.

FreeWebNovel URL slugs count every page (real chapters plus joke/glossary extras
and fractional ".x" side-chapters) while the page header shows the author's
numbering, so slug and displayed number drift apart (Supreme Magus: offset 2 → 15
across the novel, still growing).

Phase 1 landed the model (``ChapterMeta.slug``, displayed-number ``index``
semantics, chapter_index.json schema v2) and the Supreme Magus fixtures, with the
addressing behaviour itself pinned as ``xfail`` targets. Phase 2 implements that
behaviour in ``freewebnovel.py`` (probe-gated identity check, binary-search start
resolution, per-page reconciliation of the walk) — the former xfail targets below
now pass outright and their marks are removed, per the fix drop's Definition of
Done. The rest of this file are regression pins that must stay green: the
fixtures parse as measured live, identity novels (slug == displayed) keep
byte-identical indexes/filenames with zero extra fetches, and the v2 schema
round-trips slug + fractional indexes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from webnovel_scraper import catalog, pipeline
from webnovel_scraper.adapters import freewebnovel as fwn
from webnovel_scraper.adapters.freewebnovel import FreeWebNovelAdapter
from webnovel_scraper.models import ChapterContent, ChapterMeta, ScrapeJob

FIXTURES = Path(__file__).resolve().parents[2] / "files" / "test-files"

SM_SPEC = catalog.get_spec("supreme-magus", "freewebnovel")
SS_SPEC = catalog.get_spec("shadow-slave", "freewebnovel")


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _soup(name: str) -> BeautifulSoup:
    return BeautifulSoup(_fixture(name), "html.parser")


class FakeRM:
    """Serves fixture HTML by URL; records what was fetched."""

    def __init__(self, html_by_url=None, default_html=None) -> None:
        self.html_by_url = html_by_url or {}
        self.default_html = default_html
        self.fetched: list[str] = []

    def fetch(self, url, *, use_browser=False, use_cache=None, fast_path=False) -> str:
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


# ── Pins: the offset is real and the displayed number is already parsed ──────
def test_extract_title_returns_displayed_number_for_offset_fixture() -> None:
    """Slug 4222's header displays "Chapter 4207: Dangerous Knowledge (Part 2)"
    (measured live 2026-07-19; offset 15). ``_extract_title`` already recovers
    the displayed number — Phase 2 just has to stop discarding it."""
    num, title = fwn._extract_title(_soup("fwn_sm_chapter_slug4222_displayed4207.html"), None)
    assert num == 4207
    assert title == "Dangerous Knowledge (Part 2)"


def test_offset_fixture_body_extracts_and_flows_through_extract_chapter() -> None:
    meta = ChapterMeta(index=4222, url=fwn.chapter_url_for(SM_SPEC, 4222), slug=4222)
    adapter = FreeWebNovelAdapter(request_manager=FakeRM())
    content = adapter._extract_chapter(_fixture("fwn_sm_chapter_slug4222_displayed4207.html"), meta)
    assert content.index == meta.index
    assert content.paragraphs  # the sanitized fixture is extraction-viable


def test_unnumbered_extras_have_no_parseable_positive_number() -> None:
    """The joke chapter (slug 1) and volume glossary (slug 2) have no positive
    "Chapter N" header — the basis of the Phase-2 skip rule."""
    for name in (
        "fwn_sm_chapter_slug1_joke_extra.html",
        "fwn_sm_chapter_slug2_glossary_extra.html",
    ):
        num, _title = fwn._extract_title(_soup(name), None)
        assert num is None, name


def test_index_slice_discover_max_is_slug_space() -> None:
    """The index page's anchors put the newest chapter at slug 4405 while its
    link text displays "Chapter 4390" — the count that feeds ``range()`` is
    unambiguously slug-space (Phase 2 keeps it that way for URL generation)."""
    adapter = FreeWebNovelAdapter(request_manager=FakeRM())
    assert adapter._discover_max_chapter(_fixture("fwn_sm_index_slice.html"), SM_SPEC) == 4405


# ── Pins: model + identity-novel regression safety ───────────────────────────
def test_build_chapter_index_populates_slug_alongside_index() -> None:
    toc = _fixture("fwn_toc.html")
    adapter = FreeWebNovelAdapter(request_manager=FakeRM({SS_SPEC.url: toc}))
    metas = adapter.build_chapter_index(SS_SPEC)
    assert [m.slug for m in metas] == list(range(1, 51))
    assert [m.index for m in metas] == [m.slug for m in metas]


def test_identity_novel_build_chapter_index_makes_zero_extra_fetches() -> None:
    """Hard regression requirement (decision #3 — identity is auto-detected,
    never assumed): on a novel where slug == displayed, confirming that fact
    costs NOTHING beyond the index-page fetch ``build_chapter_index`` already
    makes to discover the chapter count. The (slug, displayed) evidence is
    harvested for FREE from that same index page's anchors — no dedicated probe
    fetch, no per-chapter reconciliation walk. Proof: ``FakeRM`` here registers
    ONLY the index URL — no chapter URLs at all — so this test would fail
    loudly (``KeyError``) if identity detection ever tried to fetch a chapter
    page. Before this fix, ``build_chapter_index`` made exactly one fetch (the
    index page) for an identity novel; ``rm.fetched`` proves that count is
    unchanged, i.e. truly zero EXTRA fetches, not just "no crash"."""
    toc = _fixture("fwn_toc.html")
    rm = FakeRM({SS_SPEC.url: toc})  # deliberately: zero chapter URLs registered
    adapter = FreeWebNovelAdapter(request_manager=rm)

    metas = adapter.build_chapter_index(SS_SPEC)

    assert rm.fetched == [SS_SPEC.url]  # exactly one fetch, same as pre-fix
    assert [m.index for m in metas] == list(range(1, 51))
    assert [m.index for m in metas] == [m.slug for m in metas]


def test_identity_novel_indexes_and_filenames_unchanged() -> None:
    """Hard regression requirement: on a novel where slug == displayed, the
    index and the SEPARATE filename are byte-identical to pre-fix output."""
    toc = _fixture("fwn_toc.html")
    adapter = FreeWebNovelAdapter(request_manager=FakeRM({SS_SPEC.url: toc}))
    metas = adapter.build_chapter_index(SS_SPEC)
    assert [m.index for m in metas] == list(range(1, 51))
    assert metas[24].url == "https://freewebnovel.com/novel/shadow-slave/chapter-25"

    content = ChapterContent(index=5, title="The Test", paragraphs=["body"])
    assert content.heading == "Chapter 5: The Test."
    assert pipeline._separate_pdf_name(content) == "Chapter 5_ The Test.pdf"


def test_fractional_index_renders_naturally_in_heading_and_filename() -> None:
    """Representation choice (locked): fractional displayed numbers are floats.
    627.2 sorts between 627 and 628 and renders as "Chapter 627.2"."""
    content = ChapterContent(
        index=627.2, title="Birthday and Anniversary Part 1", paragraphs=["body"]
    )
    assert content.heading == "Chapter 627.2: Birthday and Anniversary Part 1."
    assert (
        pipeline._separate_pdf_name(content)
        == "Chapter 627.2_ Birthday and Anniversary Part 1.pdf"
    )
    assert 627 < content.index < 628
    assert sorted([628, 627.2, 627]) == [627, 627.2, 628]


# ── Pins: chapter_index.json schema v2 ───────────────────────────────────────
def test_index_roundtrip_preserves_slug_and_fractional_index(tmp_path) -> None:
    metas = [
        ChapterMeta(index=627, url=fwn.chapter_url_for(SM_SPEC, 629), slug=629),
        ChapterMeta(index=627.2, url=fwn.chapter_url_for(SM_SPEC, 630), slug=630),
        ChapterMeta(index=628, url=fwn.chapter_url_for(SM_SPEC, 635), slug=635),
    ]
    path = tmp_path / pipeline.INDEX_FILENAME
    pipeline._save_index(path, metas, SM_SPEC)
    loaded = pipeline._load_index(path, SM_SPEC)
    assert loaded == metas
    assert [type(m.index) for m in loaded] == [int, float, int]
    assert [m.slug for m in loaded] == [629, 630, 635]


def test_index_number_normalization() -> None:
    assert pipeline._index_number(627) == 627
    assert type(pipeline._index_number(627.0)) is int  # never "Chapter 627.0"
    assert pipeline._index_number(627.2) == 627.2
    with pytest.raises(TypeError):
        pipeline._index_number("627")
    with pytest.raises(TypeError):
        pipeline._index_number(True)


# ── Phase 2: displayed-number addressing (formerly xfail targets) ───────────
def test_fractional_page_parses_as_fractional_number() -> None:
    """Slug 630's header displays "Chapter 627.2 Birthday and Anniversary Part 1"
    (measured live). Today's regex yields (627, "2 Birthday and Anniversary
    Part 1") — the fraction must become part of the number, not the title."""
    num, title = fwn._extract_title(_soup("fwn_sm_chapter_slug630_fractional_627_2.html"), None)
    assert num == 627.2
    assert title == "Birthday and Anniversary Part 1"


def test_fractional_pdf_does_not_collide_with_integer_resume_lookup(tmp_path) -> None:
    (tmp_path / "Chapter 627.2_ Birthday and Anniversary Part 1.pdf").write_bytes(b"pdf")
    assert pipeline._existing_separate_pdf(tmp_path, 627) is None


def test_offset_novel_index_is_displayed_space_with_extras_skipped() -> None:
    """Synthetic offset novel, 8 slugs: 1 = unnumbered joke, 2 = glossary
    ("Chapter -1"), 3-5 = Chapters 1-3, 6 = fractional 3.2, 7-8 = Chapters 4-5.
    Target: metas are displayed-space [(1,3),(2,4),(3,5),(3.2,6),(4,7),(5,8)]
    as (index, slug), with the two extras skipped."""

    def chapter_page(header: str) -> str:
        return (
            "<html><head><title>{h} - Free Web Novel</title></head><body>"
            "<h1><span class='chapter'>{h}</span></h1>"
            "<div id='article'><p>A body paragraph long enough to pass the "
            "noise filter for this synthetic chapter page.</p></div>"
            "</body></html>"
        ).format(h=header)

    index_page = "<html><body>" + "".join(
        f"<a href='{SM_SPEC.url}/chapter-{n}'>x</a>" for n in range(1, 9)
    ) + "</body></html>"

    headers = {
        1: "-2 Joke Chapter. Do not read",
        2: "Chapter -1 Volume 1 Glossary",
        3: "Chapter 1 Prologue",
        4: "Chapter 2 First Steps",
        5: "Chapter 3 Awakening",
        6: "Chapter 3.2 Side Story",
        7: "Chapter 4 Departure",
        8: "Chapter 5 Arrival",
    }
    rm = FakeRM(
        {SM_SPEC.url: index_page}
        | {f"{SM_SPEC.url}/chapter-{n}": chapter_page(h) for n, h in headers.items()}
    )
    adapter = FreeWebNovelAdapter(request_manager=rm)
    metas = adapter.build_chapter_index(SM_SPEC)

    assert [(m.index, m.slug) for m in metas] == [
        (1, 3),
        (2, 4),
        (3, 5),
        (3.2, 6),
        (4, 7),
        (5, 8),
    ]


# ── Phase 2: binary-search start resolution + bounded walk ──────────────────
def _synth_displayed_label(slug: int) -> str | None:
    """Synthetic 500-slug offset novel: slugs 1-3 are unnumbered extras; a
    constant offset of 3 from slug 4 onward (slug 4 -> "Chapter 1"), ONE
    fractional insert at slug 120 ("Chapter 116.5"), after which the offset
    grows to 4 — the same shape (constant runs + one insert bump) measured live
    on Supreme Magus, just compressed to a size a test can populate cheaply."""
    if slug <= 3:
        return None
    if slug < 120:
        return str(slug - 3)
    if slug == 120:
        return "116.5"
    return str(slug - 4)


class SlugSynthesizingRM:
    """Synthesizes each chapter page's header purely from the slug parsed out of
    the requested URL, instead of hand-authoring hundreds of fixtures for a
    large synthetic novel. Records every URL fetched so a test can assert the
    walk stayed BOUNDED (did not scan the whole novel)."""

    _SLUG_RE = re.compile(r"/chapter-(\d+)$")

    def __init__(self, index_html: str, index_url: str) -> None:
        self.index_html = index_html
        self.index_url = index_url
        self.fetched: list[str] = []

    def fetch(self, url, *, use_browser=False, use_cache=None, fast_path=False) -> str:
        self.fetched.append(url)
        if url == self.index_url:
            return self.index_html
        m = self._SLUG_RE.search(url)
        assert m, f"unexpected URL in synthetic test: {url}"
        slug = int(m.group(1))
        label = _synth_displayed_label(slug)
        header = f"Chapter {label} Synthetic Title" if label else "-9 Unnumbered extra"
        return (
            "<html><head><title>x</title></head><body>"
            f"<h1><span class='chapter'>{header}</span></h1>"
            "<div id='article'><p>A body paragraph long enough to pass the "
            "noise filter for this synthetic chapter page.</p></div>"
            "</body></html>"
        )

    def start(self):
        return self

    def close(self):
        pass


def test_binary_search_and_bounded_walk_resolve_displayed_number_to_slug() -> None:
    """Range semantics on a large offset novel: a requested displayed number
    (116) resolves to its correct slug (119) — and the fractional insert right
    beside it (slug 120 -> "116.5") is picked up by the padded walk — WITHOUT
    walking the whole 500-slug novel. Index-page anchors carry no text (as in
    the earlier extras test), so identity can't be confirmed for free and the
    binary-search/walk path is exercised exactly as it would be for a real
    request landing outside the index page's rendered slice."""
    index_url = SM_SPEC.url
    index_html = "<html><body>" + "".join(
        f"<a href='{index_url}/chapter-{n}'>x</a>" for n in (498, 499, 500)
    ) + "</body></html>"
    rm = SlugSynthesizingRM(index_html, index_url)
    adapter = FreeWebNovelAdapter(request_manager=rm)

    metas = adapter.build_chapter_index(SM_SPEC, start_hint=116, end_hint=116)

    by_index = {m.index: m.slug for m in metas}
    assert by_index[116] == 119
    assert by_index[116.5] == 120

    # Boundedness: a full walk of this novel would fetch ~500 chapter pages;
    # binary search + the padded window should need a small fraction of that.
    assert len(rm.fetched) < 60, (
        f"expected a bounded walk, fetched {len(rm.fetched)} URLs"
    )


# ── Phase 3: pipeline wiring — job.start/end reach the adapter as hints ──────
def test_pipeline_run_near_high_chapter_stays_bounded_on_offset_novel(tmp_path) -> None:
    """End-to-end through ``pipeline.run_scrape`` (the legacy/injected-adapter
    path — used unconditionally whenever an adapter is injected, per
    ``run_scrape``'s own scope-gate check): a user request for a single high
    displayed chapter (116) on the same 500-slug synthetic offset novel resolves
    to the correct slug (119), writes the correctly-named PDF, and — the point
    of Phase 3's pipeline wiring — the underlying fetch count stays BOUNDED
    (job.start/job.end actually reached ``build_chapter_index`` as
    start_hint/end_hint; before this wiring, the adapter always walked
    unbounded from slug 1, which for this 500-slug novel would need ~120+
    fetches just to reach chapter 116)."""
    index_url = SM_SPEC.url
    index_html = "<html><body>" + "".join(
        f"<a href='{index_url}/chapter-{n}'>x</a>" for n in (498, 499, 500)
    ) + "</body></html>"
    rm = SlugSynthesizingRM(index_html, index_url)
    adapter = FreeWebNovelAdapter(request_manager=rm)

    job = ScrapeJob(
        novel_slug="supreme-magus",
        adapter_key="freewebnovel",
        start=116,
        end=116,
        delay=0.0,
        output_mode=pipeline.OutputMode.SEPARATE,
        use_cache=False,
        output_dir=tmp_path,
    )

    report = pipeline.run_scrape(job, adapter=adapter, log=lambda _m: None)

    assert report.effective_range == (116, 116)
    assert len(report.written) == 1
    assert report.written[0].name == "Chapter 116_ Synthetic Title.pdf"
    assert not report.failed

    # Boundedness proof: index-build (bounded walk) + one content fetch (or
    # zero, if fetch_chapter reused the walk's cached HTML) — nowhere near an
    # unbounded from-slug-1 walk (~120 fetches) or the full 500-slug novel.
    assert len(rm.fetched) < 60, (
        f"expected job.start/end to bound the walk, fetched {len(rm.fetched)} URLs"
    )


def test_pipeline_end_hint_treats_unbounded_end_as_no_hint() -> None:
    """``_end_hint_for`` passes the real requested end through, but folds an
    effectively-unbounded end (e.g. app.py's ``ALL_CHAPTERS = 10**9`` sentinel
    for "scrape to the end") down to ``None`` — avoiding a wasted binary search
    that would only ever converge on "the last slug" anyway."""
    job = ScrapeJob(
        novel_slug="supreme-magus", adapter_key="freewebnovel", start=5, end=200,
        delay=0.0, output_mode=pipeline.OutputMode.SEPARATE, use_cache=False,
        output_dir=Path("."),
    )
    assert pipeline._end_hint_for(job) == 200

    unbounded_job = ScrapeJob(
        novel_slug="supreme-magus", adapter_key="freewebnovel", start=5, end=10**9,
        delay=0.0, output_mode=pipeline.OutputMode.SEPARATE, use_cache=False,
        output_dir=Path("."),
    )
    assert pipeline._end_hint_for(unbounded_job) is None


# ── Phase 2: _discover_max_chapter fallback-consistency fix ─────────────────
def test_discover_max_chapter_loosened_href_fallback() -> None:
    """When no anchor href matches THIS novel's own index-URL path (the primary,
    strict regex), a looser '/chapter-N' href match still yields a slug-space
    count — never falling through to the displayed-space text fallback when a
    URL-based number is available at all."""
    html = (
        "<html><body>"
        "<a href='/novel/some-other-novel/chapter-3'>Chapter 3</a>"
        "<a href='https://freewebnovel.com/novel/supreme-magus-novel/chapter-77'>Chapter 77</a>"
        "</body></html>"
    )
    adapter = FreeWebNovelAdapter(request_manager=FakeRM())
    # The primary regex is scoped to SM_SPEC's own index path; only the second
    # anchor's href actually matches "/novel/supreme-magus-novel/chapter-77", so
    # the loosened fallback (not the text fallback) is what finds it.
    assert adapter._discover_max_chapter(html, SM_SPEC) == 77


def test_discover_max_chapter_text_only_fallback_warns_displayed_space() -> None:
    """With NO href-derived number available at all, the text fallback still
    returns a count (better than raising), but is flagged as displayed-space —
    it must never be silently trusted as the slug count on an offset novel."""
    html = "<html><body><a>Chapter 42: Some Title</a></body></html>"
    adapter = FreeWebNovelAdapter(request_manager=FakeRM())
    assert adapter._discover_max_chapter(html, SM_SPEC) == 42
    assert any("displayed link text, not URL slugs" in w for w in adapter.warnings)
