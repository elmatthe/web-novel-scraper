"""Checkpoint 1: synthetic .com page structures, never copied chapter prose."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from webnovel_scraper import catalog, pdf_builder, pipeline, txt_builder
from webnovel_scraper.adapters import freewebnovel as fwn
from webnovel_scraper.chapter_preflight import identity_from_meta, meta_from_identity
from webnovel_scraper.models import ChapterMeta, ChapterMismatchError
from webnovel_scraper.rescue_pool import HEADLESS_CAMOUFOX, RescuePool
from webnovel_scraper.source_identity import ChapterIdentity, SourceIdentity

SPEC = catalog.get_spec("supreme-magus", "freewebnovel")
SOURCE = SourceIdentity.from_site_spec(SPEC)
META = ChapterMeta(42, f"{SPEC.url}/chapter-42", slug=42)


def page(heading="", opening=None, *, document="Diagnostic novel", body="Diagnostic prose remains intact."):
    return (f"<title>{document}</title><span class='chapter'>{heading}</span>"
            "<div id='article'>" + (f"<p>{opening}</p>" if opening else "")
            + f"<p>{body}</p></div>")


def extract(html, meta=META):
    return fwn.FreeWebNovelAdapter(request_manager=object())._extract_chapter(html, meta)


BAD_PAGES = [
    page(),
    page(document="A title without a number"),
    page("Chzzpter 42: Unrecognized"),
    page("Chapter 42: Expected", "Chapter 41: Actual"),
    page("Chapter 41: Actual", body="A character mentioned Chapter 42 elsewhere."),
    page(body="A character mentioned Chapter 42 elsewhere."),
    page("Chapter 42: Expected", document="Novel - Chapter 41: Actual"),
]


@pytest.mark.parametrize("html", BAD_PAGES)
def test_missing_or_conflicting_observations_never_become_the_expected_chapter(html):
    with pytest.raises(ChapterMismatchError):
        extract(html)


def test_title_helper_cannot_substitute_an_expected_number_for_missing_evidence():
    assert fwn._extract_title(BeautifulSoup(page(), "html.parser"), 42) == (None, "")


@pytest.mark.parametrize("label", ["627.1", "627.10", "627.00"])
def test_first_number_parse_keeps_all_fractional_digits(label):
    assert str(fwn._parse_chapter_number(label)) == label


@pytest.mark.parametrize("heading,opening,title", [
    ("Chapter 42: Diagnostic", "Chapter 42: Diagnostic", "Diagnostic"),
    ("Chpater 42: Diagnostic", "Chapter 42: Diagnostic", "Diagnostic"),
    ("Chapter 42", None, "Untitled"),
    ("", "Chpater 42: Recovered", "Recovered"),
    ("Chapter 41.1 - 42: Diagnostic", "Chapter 42: Diagnostic", "Diagnostic"),
])
def test_verified_pages_keep_one_heading_and_canonical_prose(heading, opening, title):
    content = extract(page(heading, opening))
    assert content.heading.endswith(f": {title}")
    assert content.paragraphs == ["Diagnostic prose remains intact."]
    assert txt_builder.chapters_to_txt([content]).count("Chapter ") == 1


def test_numbered_document_title_can_independently_identify_a_page():
    assert extract(page(document="Novel - Chapter 42: Diagnostic | Free Web Novel")).heading == "Chapter 42: Diagnostic"


class Pages:
    def __init__(self, *pages, cached=False):
        self.pages, self.cached = pages, cached
        self.fetches, self.rejected = 0, []

    def fetch(self, url, **kw):
        html = self.pages[min(self.fetches, len(self.pages) - 1)]
        self.fetches += 1
        self.last_fetch_info = SimpleNamespace(from_cache=self.cached and self.fetches == 1)
        return html

    def invalidate_cache(self, url, reason):
        self.rejected.append((url, reason))


@pytest.mark.parametrize("html", BAD_PAGES)
@pytest.mark.parametrize("path", ["network", "cache", "prefetch", "rescue"])
def test_all_fetch_paths_validate_and_bound_retries(html, path):
    rm = Pages(html, cached=path == "cache")
    meta = replace(META, extra={fwn._PREFETCHED_HTML_KEY: html} if path == "prefetch" else {})
    adapter = fwn.FreeWebNovelAdapter(request_manager=rm)
    stored = []
    if path == "rescue":
        rm._fetch_uncached_strategy = lambda url, strategy: rm.fetch(url)
        rm.cache = SimpleNamespace(store=lambda *a, **kw: stored.append(a))
    with pytest.raises(ChapterMismatchError):
        if path == "rescue":
            RescuePool._default_fetch(SimpleNamespace(_use_cache=True), meta, manager=rm,
                                      adapter=adapter, mode=HEADLESS_CAMOUFOX, fresh=False, budget=1)
        else:
            adapter.fetch_chapter(meta, SPEC, fast_path=True)
    assert rm.fetches == (2 if path == "cache" else 1)
    assert len(rm.rejected) == (0 if path == "rescue" else 2 if path in ("cache", "prefetch") else 1)
    assert stored == []


@pytest.mark.parametrize("path", ["cache", "prefetch"])
def test_unknown_saved_page_can_recover_only_with_a_verified_fresh_page(path):
    right = page("Chapter 42: Recovered", "Chapter 42: Recovered")
    rm = Pages(*([page(), right] if path == "cache" else [right]), cached=path == "cache")
    meta = replace(META, extra={fwn._PREFETCHED_HTML_KEY: page()} if path == "prefetch" else {})
    result = fwn.FreeWebNovelAdapter(request_manager=rm).fetch_chapter(meta, SPEC)
    assert result.heading == "Chapter 42: Recovered"
    assert len(rm.rejected) == 1


def discovered_fractional_pages():
    # Consecutive fractional pages force the existing walker to read both; no inference policy change.
    html = [page(f"Chapter {label}: Diagnostic", f"Chapter {label}: Diagnostic")
            for label in ("627.10", "627.1")]
    rm = Pages(*html)
    adapter = fwn.FreeWebNovelAdapter(request_manager=rm)
    return adapter._walk_and_reconcile(rm, SPEC, 630, 631, {})


def test_actual_com_parser_preserves_exact_fractional_labels_through_metadata_and_output(tmp_path):
    metas = discovered_fractional_pages()
    assert [m.extra.get("manifest_label") for m in metas] == ["627.1", "627.10"]
    identities = [identity_from_meta(SOURCE, m) for m in metas]
    assert [c.label.text for c in identities] == ["627.1", "627.10"]
    assert [ChapterIdentity.from_meta(SOURCE, m).label.text for m in metas] == ["627.1", "627.10"]
    contents = []
    for identity, meta in zip(identities, metas):
        back = ChapterIdentity.from_json(SOURCE, identity.to_json())
        bridged = meta_from_identity(back, meta)
        contents.append(fwn.FreeWebNovelAdapter(request_manager=object()).fetch_chapter(bridged, SPEC))
    assert [c.heading for c in contents] == ["Chapter 627.1: Diagnostic", "Chapter 627.10: Diagnostic"]
    names = [pipeline._separate_pdf_name(c) for c in contents]
    assert len(set(names)) == 2
    for content, name in zip(contents, names):
        output = txt_builder.create_txt([content], tmp_path / (name + ".txt"))
        assert output.read_text(encoding="utf-8").count("Chapter ") == 1
        pdf = pdf_builder.create_pdf([content], tmp_path / name, "Diagnostic novel")
        from pypdf import PdfReader
        assert content.heading in PdfReader(pdf).pages[0].extract_text()


def test_com_build_index_carries_exact_labels_without_changing_numeric_indices():
    class Site:
        def fetch(self, url, **kw):
            if url == SPEC.url:
                return f"<a href='{SPEC.url}/chapter-2'>Chapter 627.10</a>"
            label = "627.1" if url.endswith("chapter-1") else "627.10"
            return page(f"Chapter {label}: Diagnostic", f"Chapter {label}: Diagnostic")

    metas = fwn.FreeWebNovelAdapter(request_manager=Site()).build_chapter_index(SPEC)
    assert [m.index for m in metas] == [627.1, 627.1]
    assert [m.extra["manifest_label"] for m in metas] == ["627.1", "627.10"]
    assert len({m.url for m in metas}) == 2


def test_seeded_exact_label_survives_existing_probe_reuse():
    meta = ChapterMeta(627.1, f"{SPEC.url}/chapter-1", slug=1, extra={"manifest_label": "627.10"})

    class Site:
        def fetch(self, url, **kw):
            assert url == SPEC.url  # Existing seed reuse; no discovery policy change here.
            return f"<a href='{SPEC.url}/chapter-1'>Chapter 627.10</a>"

    result = fwn.FreeWebNovelAdapter(request_manager=Site()).build_chapter_index(SPEC, resume_metas=[meta])
    assert result[0].extra["manifest_label"] == "627.10"


@pytest.mark.parametrize("observed,expected", [("627.1", "627.10"), ("627.10", "627.1")])
def test_numerically_equal_fractional_labels_are_not_identity_certificates(observed, expected):
    meta = replace(META, index=float(expected), extra={"manifest_label": expected})
    with pytest.raises(ChapterMismatchError):
        extract(page(f"Chapter {observed}: Diagnostic"), meta)


@pytest.mark.parametrize("label", ["627.2", "627.3", "627.4", "627.5", "627.6", "952.2", "952.3", "1923.1", "1923.2", "627.10", "627.00"])
def test_supported_fractions_survive_com_probe_and_reconciled_metadata(label):
    html = page(f"Chapter {label}: Diagnostic", f"Chapter {label}: Diagnostic")
    rm = Pages(html)
    adapter = fwn.FreeWebNovelAdapter(request_manager=rm)
    metas = adapter._reconcile_window(rm, SPEC, 630, 630, {})
    assert metas[0].extra.get("manifest_label") == label
    content = adapter.fetch_chapter(metas[0], SPEC)
    assert content.heading == f"Chapter {label}: Diagnostic"
    assert content.paragraphs == ["Diagnostic prose remains intact."]


def test_conflicting_exact_fractional_opening_is_rejected_even_when_values_are_equal():
    with pytest.raises(ChapterMismatchError, match="conflicting"):
        extract(page("Chapter 627.1: Diagnostic", "Chapter 627.10: Diagnostic"),
                replace(META, index=627.1, extra={"manifest_label": "627.1"}))


def test_historical_1953_1956_bodies_are_distinct_and_stale_page_is_rejected():
    old = page("Chpater 1953: Nineteen (Part 1)", "Chpater 1953: Nineteen (Part 1)", body="Diagnostic body A.")
    new = page("Chapter 1956: Climbing the Ranks (Part 2)", "Chapter 1956: Climbing the Ranks (Part 2)", body="Diagnostic body B.")
    a = extract(old, ChapterMeta(1953, f"{SPEC.url}/chapter-1964", slug=1964))
    meta = ChapterMeta(1956, f"{SPEC.url}/chapter-1967", slug=1967)
    with pytest.raises(ChapterMismatchError):
        extract(old, meta)
    b = extract(new, meta)
    assert (a.heading, b.heading) == ("Chapter 1953: Nineteen (Part 1)", "Chapter 1956: Climbing the Ranks (Part 2)")
    assert a.paragraphs == ["Diagnostic body A."]
    assert b.paragraphs == ["Diagnostic body B."]
