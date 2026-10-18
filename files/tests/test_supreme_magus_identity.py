"""Supreme Magus on Free Web Novel .com (owner acceptance): chapter identity, typo'd headings, offset dips.

The real pages are not stored; every page here is a short synthetic page with the real shape.

* Slug 1964 shows "Chpater 1953: Nineteen (Part 1)" and slug 1967 (a stale saved copy) shows the very same
  page. The misspelt word hid the number from the heading reader, which also switched the wrong-chapter
  guard off, so the page was published under the requested number with "Untitled".
* Slugs 1653-1932 display two numbers more than the chapter list inferred: the site skips 1642-1643, and two
  fractional pages (1923.1, 1923.2) bring the slug-to-number gap back to where it was. The reconciler saw equal
  gaps at both ends and assumed the whole span was constant.
"""

from __future__ import annotations

import re
from dataclasses import replace

import pytest

from webnovel_scraper import catalog, pipeline
from webnovel_scraper.adapters import freewebnovel as fwn
from webnovel_scraper.adapters.freewebnovel import FreeWebNovelAdapter
from webnovel_scraper.chapter_store import ChapterManifestStore
from webnovel_scraper.content import heading_for, parse_heading
from webnovel_scraper.models import ChapterMeta, ChapterMismatchError, OutputMode, ScrapeJob

SPEC = catalog.get_spec("supreme-magus", "freewebnovel")
BODY = ("Your story is too convenient to be true.", "Like what?")


def page(heading: str, story_first_line: str | None = None, body=BODY, title_tag: str | None = None) -> str:
    lines = ([story_first_line] if story_first_line is not None else []) + list(body)
    paragraphs = "".join(f"<p>{line}</p>" for line in lines)
    return (f"<html><head><title>{title_tag or f'Supreme Magus - {heading} | Free Web Novel'}</title></head><body>"
            f"<div class='top'><h1>Supreme Magus</h1><span class='chapter'>{heading}</span></div>"
            f"<div id='article'>{paragraphs}</div></body></html>")


def extract(html: str, number, slug: int = 1964):
    meta = ChapterMeta(index=number, url=f"{SPEC.url}/chapter-{slug}", slug=slug)
    return FreeWebNovelAdapter(request_manager=object())._extract_chapter(html, meta)


# ── headings: the misspelt word, titles, Untitled ────────────────────────────
def test_a_misspelt_chapter_word_is_read_as_the_chapter_it_names():
    content = extract(page("Chpater 1953: Nineteen (Part 1)", "Chpater 1953: Nineteen (Part 1)"), 1953)
    assert (content.index, content.heading) == (1953, "Chapter 1953: Nineteen (Part 1)")
    assert content.paragraphs == list(BODY)          # the repeated heading line is gone, the story is whole


def test_the_story_may_repeat_a_heading_with_the_correct_word_while_the_page_misspells_it():
    content = extract(page("Chpater 1953: Nineteen (Part 1)", "Chapter 1953: Nineteen (Part 1)"), 1953)
    assert content.heading == "Chapter 1953: Nineteen (Part 1)"
    assert content.paragraphs == list(BODY)


def test_only_known_misspellings_of_chapter_are_recognised():
    assert parse_heading("Chpater 1953: Nineteen") == ("1953", "Nineteen")
    assert parse_heading("Chaper 7 Title") == ("7", "Title")
    for prose in ("Chapel 12 was dark", "Champion 3 rose", "Charter 5: signed"):
        assert parse_heading(prose) == (None, "")


def test_a_page_of_another_chapter_with_a_misspelt_heading_is_rejected_not_relabelled():
    """The 1956 corruption: the page asked for as 1956 is chapter 1953's page."""
    html = page("Chpater 1953: Nineteen (Part 1)", "Chpater 1953: Nineteen (Part 1)")
    with pytest.raises(ChapterMismatchError, match="shows chapter 1953"):
        extract(html, 1956, slug=1967)


def test_an_untitled_heading_is_only_a_title_fallback_for_a_confirmed_chapter():
    content = extract(page("Chapter 1953"), 1953)
    assert content.heading == "Chapter 1953: Untitled"
    assert content.paragraphs == list(BODY)
    with pytest.raises(ChapterMismatchError):
        extract(page("Chapter 1953"), 1956)


def test_the_site_name_in_the_page_title_is_not_a_chapter_title():
    """Slugs 1930/1931 head their page "Chapter 1921" only; the document title gave the title "Free Web Novel"."""
    html = page("Chapter 1921", None, title_tag="Supreme Magus - Chapter 1921 - Free Web Novel")
    content = extract(html, 1921, slug=1930)
    assert content.heading == "Chapter 1921: Untitled"


def test_a_heading_title_is_preferred_to_untitled_when_another_candidate_has_it():
    html = page("Chpater 1953", None, title_tag="Supreme Magus - Chpater 1953: Nineteen (Part 1) | Free Web Novel")
    assert extract(html, 1953).heading == "Chapter 1953: Nineteen (Part 1)"


def test_a_fractional_chapter_keeps_its_fraction():
    content = extract(page("Chapter 1923.1: Peace Offers (Part 2)", "Chapter 1923.1: Peace Offers (Part 2)"), 1923.1, slug=1933)
    assert content.heading == "Chapter 1923.1: Peace Offers (Part 2)"
    with pytest.raises(ChapterMismatchError):
        extract(page("Chapter 1923.1: Peace Offers (Part 2)"), 1923, slug=1933)


def test_the_listed_pair_of_pages_stays_distinct():
    a = extract(page("Chpater 1953: Nineteen (Part 1)", "Chpater 1953: Nineteen (Part 1)"), 1953)
    b = extract(page("Chapter 1954: Nineteen (Part 2)", "Chapter 1954: Nineteen (Part 2)", body=("Another body.",)), 1954, slug=1965)
    assert a.heading != b.heading and a.paragraphs != b.paragraphs


def test_lord_of_mysteries_composite_headings_are_unaffected():
    content = extract(page("Chapter 1129.1 - 1130 Initiative", "Chapter 1130 Initiative"), 1130, slug=1130)
    assert content.heading == "Chapter 1129.1-1130: Initiative"


# ── a wrong page from the cache, the prefetch or the site never becomes the chapter ──────────────
class SequencedRM:
    """Serves pages in order; the first is flagged as coming from the saved-page cache."""

    class Info:
        def __init__(self, from_cache):
            self.from_cache = from_cache

    def __init__(self, *pages):
        self.pages, self.fetches, self.set_aside = list(pages), 0, []
        self.last_fetch_info = self.Info(True)

    def fetch(self, url, **_kw):
        html = self.pages[min(self.fetches, len(self.pages) - 1)]
        self.fetches += 1
        self.last_fetch_info = self.Info(self.fetches == 1)
        return html

    def invalidate_cache(self, url, reason):
        self.set_aside.append(url)


WRONG = page("Chpater 1953: Nineteen (Part 1)", "Chpater 1953: Nineteen (Part 1)")
RIGHT = page("Chapter 1956: Climbing the Ranks (Part 2)", "Chapter 1956: Climbing the Ranks (Part 2)", body=("Real 1956 text.",))
META_1956 = ChapterMeta(index=1956, url=f"{SPEC.url}/chapter-1967", slug=1967)


def test_a_stale_cached_page_of_another_chapter_is_set_aside_and_the_site_page_used():
    rm = SequencedRM(WRONG, RIGHT)
    content = FreeWebNovelAdapter(request_manager=rm).fetch_chapter(META_1956, SPEC)
    assert (content.heading, content.paragraphs) == ("Chapter 1956: Climbing the Ranks (Part 2)", ["Real 1956 text."])
    assert rm.set_aside == [META_1956.url] and rm.fetches == 2


def test_a_wrong_page_from_the_site_too_is_a_failure_not_a_chapter():
    rm = SequencedRM(WRONG, WRONG)
    with pytest.raises(ChapterMismatchError):
        FreeWebNovelAdapter(request_manager=rm).fetch_chapter(META_1956, SPEC)
    assert rm.fetches == 2


def test_a_wrong_prefetched_page_is_not_trusted_and_the_page_is_fetched_again():
    rm = SequencedRM(RIGHT)
    meta = replace(META_1956, extra={fwn._PREFETCHED_HTML_KEY: WRONG})
    content = FreeWebNovelAdapter(request_manager=rm).fetch_chapter(meta, SPEC)
    assert content.paragraphs == ["Real 1956 text."]
    assert rm.set_aside == [META_1956.url] and rm.fetches == 1


def test_a_wrong_prefetched_page_and_a_wrong_refetch_fail_the_chapter():
    rm = SequencedRM(WRONG)
    meta = replace(META_1956, extra={fwn._PREFETCHED_HTML_KEY: WRONG})
    with pytest.raises(ChapterMismatchError):
        FreeWebNovelAdapter(request_manager=rm).fetch_chapter(meta, SPEC)


def test_a_run_and_its_resume_never_publish_one_chapters_text_under_another(tmp_path):
    class Site:
        """Chapters 1-5 are fine; the site (or its cache) serves chapter 3's page for chapter 4."""
        enabled = True
        fixed = False

        def __init__(self):
            self.adapter, self.fetched = FreeWebNovelAdapter(request_manager=object()), []

        def build_chapter_index(self, spec, **_kw):
            return [ChapterMeta(index=n, url=f"{SPEC.url}/chapter-{n + 10}", slug=n + 10) for n in range(1, 6)]

        def fetch_chapter(self, meta, spec, **_kw):
            self.fetched.append(meta.index)
            shown = 3 if meta.index == 4 and not self.fixed else meta.index
            html = page(f"Chpater {shown}: Title {shown}", f"Chpater {shown}: Title {shown}", body=(f"Body of {shown}.", "Boom!"))
            return self.adapter._extract_chapter(html, meta)

    store = ChapterManifestStore(tmp_path / "b", tmp_path / "r")
    job = ScrapeJob(novel_slug=SPEC.novel_slug, adapter_key="freewebnovel", start=1, end=5, delay=0.0,
                    output_mode=OutputMode.SEPARATE, chunk_size=5, use_cache=False,
                    output_dir=tmp_path / "out", allocate_run=True, formats=("txt",))
    site = Site()
    first = pipeline.run_scrape(job, adapter=site, log=lambda _m: None, sleep_fn=lambda _s: None, manifest_store=store)
    assert first.failed == [4]
    texts = {p.name: p.read_text(encoding="utf-8") for p in (first.output_dir / "TXT").glob("*.txt")}
    assert sorted(texts) == ["Chapter 1_ Title 1.txt", "Chapter 2_ Title 2.txt", "Chapter 3_ Title 3.txt",
                             "Chapter 5_ Title 5.txt"]
    assert sum("Body of 3." in t for t in texts.values()) == 1          # chapter 3's text appears once

    site.fixed = True
    again = pipeline.run_scrape(replace(job, output_dir=first.output_dir, allocate_run=False, resume=True),
                                adapter=site, log=lambda _m: None, sleep_fn=lambda _s: None, manifest_store=store)
    assert again.failed == [] and site.fetched[-1:] == [4]
    names = sorted(p.name for p in (first.output_dir / "TXT").glob("*.txt"))
    assert names == [f"Chapter {n}_ Title {n}.txt" for n in range(1, 6)]
    assert (first.output_dir / "TXT" / "Chapter 4_ Title 4.txt").read_text(encoding="utf-8").count("Body of 4.") == 1


# ── the slug-to-number map: a dip between two equal gaps (the 282 failures) ───────────────────────
INDEX_HTML = "<html><body>" + "".join(f"<a href='{SPEC.url}/chapter-{n}'>x</a>" for n in (398, 399, 400)) + "</body></html>"


def dip_label(slug: int) -> str | None:
    """Slugs 1-2 unnumbered extras; gap 2; from slug 200 the author skips two numbers (gap 0) for 200
    slugs; two fractional pages follow and the gap is 2 again. The same shape as Supreme Magus 1653-1935."""
    if slug <= 2:
        return None
    if slug < 200:
        return str(slug - 2)
    if slug < 400:
        return str(slug)
    if slug in (400, 401):
        return f"399.{slug - 399}"
    return str(slug - 2)


class MapRM:
    _SLUG = re.compile(r"/chapter-(\d+)$")

    def __init__(self, label_fn, count):
        self.label_fn, self.count, self.fetched = label_fn, count, []
        self.index = ("<html><body>" + "".join(f"<a href='{SPEC.url}/chapter-{n}'>x</a>" for n in (count - 2, count - 1, count))
                      + "</body></html>")

    def fetch(self, url, **_kw):
        self.fetched.append(url)
        if url == SPEC.url:
            return self.index
        slug = int(self._SLUG.search(url).group(1))
        label = self.label_fn(slug)
        heading = f"Chapter {label} Title {slug}" if label else "-9 Unnumbered extra"
        return (f"<html><head><title>x</title></head><body><span class='chapter'>{heading}</span>"
                "<div id='article'><p>Body.</p></div></body></html>")


def test_a_span_between_two_equal_gaps_is_not_assumed_constant():
    rm = MapRM(dip_label, 600)
    metas = FreeWebNovelAdapter(request_manager=rm).build_chapter_index(SPEC, start_hint=1, end_hint=5000)
    got = {m.slug: m.index for m in metas}
    for slug in range(3, 601):
        assert got[slug] == float(dip_label(slug)), f"slug {slug}"
    assert len(metas) == 598
    chapter_fetches = [u for u in rm.fetched if "/chapter-" in u]
    assert len(chapter_fetches) < 250, len(chapter_fetches)       # still far from a page-by-page walk


def test_the_stopped_run_mapping_would_have_been_wrong():
    """Pin of the failure: the number the old reconciler inferred (slug - 2) is not what slug 250 displays."""
    assert int(dip_label(250)) != 250 - 2


def test_a_plain_offset_novel_still_resolves_with_few_fetches():
    rm = MapRM(lambda s: None if s <= 2 else str(s - 2), 600)
    metas = FreeWebNovelAdapter(request_manager=rm).build_chapter_index(SPEC, start_hint=1, end_hint=5000)
    assert {m.slug: m.index for m in metas} == {s: s - 2 for s in range(3, 601)}
    assert len([u for u in rm.fetched if "/chapter-" in u]) < 100


def test_heading_for_never_turns_an_unknown_number_into_a_number():
    assert heading_for(1953, "") == "Chapter 1953: Untitled"
