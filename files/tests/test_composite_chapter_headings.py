"""Composite chapter headings (owner acceptance, Lord of Mysteries 2 on Free Web Novel .com).

For chapters 1130-1138 the page at ``chapter-N`` shows ``Chapter <N-1 or N-1.1> - N Title`` in its
heading while the story itself opens ``Chapter N Title``. The first number used to be read as the
chapter and the page rejected as another chapter. The pages here are short synthetic pages with the
real shape; no site text is stored."""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from webnovel_scraper import catalog, pipeline
from webnovel_scraper.adapters import freewebnovel as fwn
from webnovel_scraper.adapters.freewebnovel import FreeWebNovelAdapter
from webnovel_scraper.chapter_store import ChapterManifestStore
from webnovel_scraper.models import ChapterMeta, ChapterMismatchError, OutputMode, ScrapeJob
from webnovel_scraper.run_journal import JOURNAL_FILENAME, replay_journal

_audit_spec = importlib.util.spec_from_file_location(
    "audit_run", Path(__file__).resolve().parents[1] / "audit_run.py")
audit_run = importlib.util.module_from_spec(_audit_spec)
_audit_spec.loader.exec_module(audit_run)

SPEC = catalog.get_spec("lord-of-mysteries-2-circle-of-inevitability", "freewebnovel")


def page(heading: str, story_first_line: str | None, body=("He walked on.", "She smiled.")) -> str:
    lines = ([story_first_line] if story_first_line else []) + list(body)
    paragraphs = "".join(f"<p>{line}</p>" for line in lines)
    return (f"<html><head><title>Novel - {heading} | Free Web Novel</title></head><body>"
            f"<h1>Novel</h1><span class='chapter'>{heading}</span>"
            f"<div id='article'>{paragraphs}</div></body></html>")


def extract(html: str, number: int):
    meta = ChapterMeta(index=number, url=f"{SPEC.url}/chapter-{number}", slug=number)
    return FreeWebNovelAdapter(request_manager=object())._extract_chapter(html, meta)


# ── the two real shapes ──────────────────────────────────────────────────────
def test_a_fractional_carry_over_heading_keeps_the_chapter_and_shows_the_pair():
    content = extract(page("Chapter 1129.1 - 1130 Initiative", "Chapter 1130 Initiative"), 1130)
    assert content.index == 1130                       # identity: selection, sorting, resume
    assert content.heading == "Chapter 1129.1-1130: Initiative"
    assert content.paragraphs == ["He walked on.", "She smiled."]   # the story's own heading line is dropped


def test_a_whole_number_carry_over_heading_keeps_the_chapter_and_shows_the_pair():
    content = extract(page("Chapter 1130 - 1131 Individual Preparations",
                           "Chapter 1131 Individual Preparations"), 1131)
    assert content.index == 1131
    assert content.heading == "Chapter 1130-1131: Individual Preparations"
    assert content.paragraphs == ["He walked on.", "She smiled."]


def test_the_story_line_may_use_a_colon_or_dash_and_differ_in_case():
    content = extract(page("Chapter 1130 - 1131 Individual Preparations",
                           "Chapter 1131: individual preparations"), 1131)
    assert content.heading == "Chapter 1130-1131: Individual Preparations"


def test_an_ordinary_page_is_unchanged():
    content = extract(page("Chapter 1139 Curse", "Chapter 1139 Curse"), 1139)
    assert (content.index, content.heading) == (1139, "Chapter 1139: Curse")


def test_a_title_that_starts_with_a_number_is_still_just_a_title():
    content = extract(page("Chapter 5 - 3 Kings", None), 5)
    assert content.heading == "Chapter 5: 3 Kings"


# ── genuine mismatches are still rejected ────────────────────────────────────
@pytest.mark.parametrize("heading,story,expected,why", [
    ("Chapter 1129.1 - 1130 Initiative", "Chapter 1130 Initiative", 1131, "page is chapter 1130, 1131 expected"),
    ("Chapter 1129.1 - 1130 Initiative", "Chapter 1129 Initiative", 1130, "story line disagrees with the heading"),
    ("Chapter 1129.1 - 1130 Initiative", "Chapter 1130 Something Else", 1130, "story title differs"),
    ("Chapter 1129.1 - 1130 Initiative", None, 1130, "nothing independent confirms the number"),
    ("Chapter 1129.1 - 1130 Initiative", "He walked on", 1130, "story line is not a chapter heading"),
    ("Chapter 1100 - 1130 Initiative", "Chapter 1130 Initiative", 1130, "first number is not just before the second"),
    ("Chapter 1131 - 1130 Initiative", "Chapter 1130 Initiative", 1130, "numbers run backwards"),
    ("Chapter 1130 - 1130 Initiative", "Chapter 1130 Initiative", 1131, "equal numbers, other chapter expected"),
    ("Chapter 1129.1 - 1130.5 Initiative", "Chapter 1130.5 Initiative", 1130, "second number is not a whole chapter"),
    ("Chapter 1129 Initiative", "Chapter 1129 Initiative", 1130, "plain page of another chapter"),
    # A confirmed pair is chapter B and nothing else: asking for its first number must fail (Codex C1).
    ("Chapter 1130 - 1131 Individual Preparations", "Chapter 1131 Individual Preparations", 1130,
     "confirmed 1131 page requested as 1130 (whole first number)"),
    ("Chapter 1129.1 - 1130 Initiative", "Chapter 1130 Initiative", 1129.1,
     "confirmed 1130 page requested as 1129.1 (fractional first number)"),
    ("Chapter 1129.1 - 1130 Initiative", "Chapter 1130 Initiative", 1129,
     "confirmed 1130 page requested as 1129"),
])
def test_genuine_mismatches_are_still_rejected(heading, story, expected, why):
    with pytest.raises(ChapterMismatchError):
        extract(page(heading, story), expected)


# ── discovery by displayed number uses the same reading ──────────────────────
def test_the_probe_reads_the_real_chapter_number_of_a_composite_page():
    soup = BeautifulSoup(page("Chapter 1129.1 - 1130 Initiative", "Chapter 1130 Initiative"), "html.parser")
    assert fwn._composite_heading(soup) == ("1129.1", 1130, "Initiative")
    plain = BeautifulSoup(page("Chapter 1139 Curse", "Chapter 1139 Curse"), "html.parser")
    assert fwn._composite_heading(plain) is None


class OneRM:
    def __init__(self, html):
        self.html = html

    def fetch(self, url, **_kw):
        return self.html


def test_the_displayed_number_probe_does_not_see_a_false_offset():
    html = page("Chapter 1130 - 1131 Individual Preparations", "Chapter 1131 Individual Preparations")
    known: dict = {}
    number = FreeWebNovelAdapter(request_manager=OneRM(html))._fetch_and_read(OneRM(html), SPEC, 1131, known)
    assert number == 1131


# ── the acceptance tool ──────────────────────────────────────────────────────
def test_the_audit_matches_a_composite_heading_to_its_chapter():
    text = audit_run.CHAPTER_SEPARATOR.join(
        ["Chapter 1129.1-1130: Initiative\n\nx", "Chapter 1130-1131: Prep\n\ny"])
    assert audit_run._headings_in_txt(text) == ["1130", "1131"]


# ── a whole run, then Resume ─────────────────────────────────────────────────
class Site:
    """Chapters 3 and 4 fail in the first session (as the old reading made them); then they load."""

    def __init__(self, broken=()):
        self.broken = set(broken)
        self.fetched: list[int] = []
        self.adapter = FreeWebNovelAdapter(request_manager=object())

    def build_chapter_index(self, spec, **_kw):
        return [ChapterMeta(index=n, url=f"{spec.url}/chapter-{n}", slug=n) for n in range(1, 7)]

    def fetch_chapter(self, meta, spec, **_kw):
        self.fetched.append(meta.index)
        n = meta.index
        if n in (3, 4):
            if n in self.broken:
                raise ChapterMismatchError(f"the page shows chapter {n - 1}, but chapter {n} is expected")
            html = page(f"Chapter {n - 1}.1 - {n} Title {n}" if n == 3 else f"Chapter {n - 1} - {n} Title {n}",
                        f"Chapter {n} Title {n}", body=(f"Body of {n}.", "Boom!"))
        else:
            html = page(f"Chapter {n} Title {n}", f"Chapter {n} Title {n}", body=(f"Body of {n}.", "Boom!"))
        return self.adapter._extract_chapter(html, meta)


def test_a_run_with_composite_chapters_audits_and_a_resume_completes_an_older_failed_run(tmp_path):
    store = ChapterManifestStore(tmp_path / "b", tmp_path / "r")
    job = ScrapeJob(novel_slug=SPEC.novel_slug, adapter_key="freewebnovel", start=1, end=6, delay=0.0,
                    output_mode=OutputMode.SEPARATE, chunk_size=6, use_cache=False,
                    output_dir=tmp_path / "out", allocate_run=True, formats=("txt", "pdf"))

    def go(the_job, site):
        return pipeline.run_scrape(the_job, adapter=site, log=lambda _m: None, sleep_fn=lambda _s: None,
                                   manifest_store=store)

    first = go(job, Site(broken={3, 4}))
    folder = first.output_dir
    assert sorted(first.failed) == [3, 4]
    assert audit_run.main([str(folder)]) == 1                    # the failed chapters are reported

    site = Site()
    again = go(replace(job, output_dir=folder, allocate_run=False, resume=True), site)
    assert sorted(site.fetched) == [3, 4]                        # only what was missing
    assert again.failed == []
    assert audit_run.main([str(folder), "--pdf-text"]) == 0
    names = sorted(p.name for p in (folder / "TXT").glob("*.txt"))
    assert names == ["Chapter 1_ Title 1.txt", "Chapter 2.1-3_ Title 3.txt", "Chapter 2_ Title 2.txt",
                     "Chapter 3-4_ Title 4.txt", "Chapter 5_ Title 5.txt", "Chapter 6_ Title 6.txt"]
    replay = replay_journal(folder / JOURNAL_FILENAME)
    assert replay.last_end == "complete"


# ── a confirmed pair under its first number: cache path and whole run (Codex C1) ──────────────
PAIR_1131 = page("Chapter 1130 - 1131 Individual Preparations", "Chapter 1131 Individual Preparations")


class CachedRM:
    """Serves one page as if from the saved-page cache; records what the cache is told to set aside."""

    class Info:
        from_cache = True

    last_fetch_info = Info()

    def __init__(self, html):
        self.html, self.set_aside, self.fetches = html, [], 0

    def fetch(self, url, **_kw):
        self.fetches += 1
        return self.html

    def invalidate_cache(self, url, reason):
        self.set_aside.append(url)


def test_a_cached_confirmed_page_under_the_wrong_address_is_set_aside_and_never_accepted():
    rm = CachedRM(PAIR_1131)
    meta = ChapterMeta(index=1130, url=f"{SPEC.url}/chapter-1130", slug=1130)
    with pytest.raises(ChapterMismatchError):
        FreeWebNovelAdapter(request_manager=rm).fetch_chapter(meta, SPEC)
    assert rm.set_aside == [meta.url, meta.url]        # set aside, fetched once more, set aside again
    assert rm.fetches == 2


def test_a_run_never_publishes_a_confirmed_page_as_the_chapter_before_it(tmp_path):
    class Stale(Site):
        def fetch_chapter(self, meta, spec, **_kw):
            if meta.index == 3:            # the list says 3, the site serves the pair that is chapter 4
                return self.adapter._extract_chapter(
                    page("Chapter 3 - 4 Title 4", "Chapter 4 Title 4"), meta)
            return super().fetch_chapter(meta, spec)

    store = ChapterManifestStore(tmp_path / "b", tmp_path / "r")
    job = ScrapeJob(novel_slug=SPEC.novel_slug, adapter_key="freewebnovel", start=1, end=6, delay=0.0,
                    output_mode=OutputMode.SEPARATE, chunk_size=6, use_cache=False,
                    output_dir=tmp_path / "out", allocate_run=True, formats=("txt",))
    report = pipeline.run_scrape(job, adapter=Stale(), log=lambda _m: None, sleep_fn=lambda _s: None,
                                 manifest_store=store)
    assert report.failed == [3]
    names = sorted(p.name for p in (report.output_dir / "TXT").glob("*.txt"))
    assert names == ["Chapter 1_ Title 1.txt", "Chapter 2_ Title 2.txt", "Chapter 3-4_ Title 4.txt",
                     "Chapter 5_ Title 5.txt", "Chapter 6_ Title 6.txt"]   # nothing for 3; 4 once, as 4
