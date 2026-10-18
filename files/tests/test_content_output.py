"""Canonical chapter text, TXT and PDF output (v0.3.0 Checkpoint 05). Offline and deterministic.

The example lines are short synthetic strings shaped like the cases measured on real pages (credit
lines, repeated headings, site promotions, short story lines); no story text is copied.
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path

import pdfplumber
import pytest

from webnovel_scraper import catalog, content as C, pdf_builder, pipeline, txt_builder
from webnovel_scraper.adapters import freewebnovel as fwn
from webnovel_scraper.chapter_store import ChapterManifestStore
from webnovel_scraper.models import ChapterContent, ChapterMeta, OutputMode, ScrapeJob

SPEC = catalog.get_spec("shadow-slave", "freewebnovel")


# ── characters ────────────────────────────────────────────────────────────────
def test_invisible_and_control_characters_go_and_meaningful_unicode_stays():
    raw = "Chap‌ter one​ — “quoted” ’apos’ café 诡秘之主 Ω\u0007 …﻿"
    assert C.normalize_text(raw) == "Chapter one — “quoted” ’apos’ café 诡秘之主 Ω …"
    assert C.normalize_text("  many   spaces\tand\nlines  ") == "many spaces and lines"


@pytest.mark.parametrize("value,text", [(5, "5"), (5.0, "5"), (627.2, "627.2"), ("627.10", "627.10"),
                                        ("007", "7")])
def test_labels_keep_fractions_exactly(value, text):
    assert C.label_text(value) == text


# ── titles and the one heading ────────────────────────────────────────────────
@pytest.mark.parametrize("raw,label,title", [
    ("1 Foreigners", 1, "Foreigners"),                                   # .net LOTM2: number repeated
    ("Chapter 1 - 1 Foreigners", 1, "Foreigners"),
    ("Chapter 714 – The Moongazer Serpent Awakens", 714, "The Moongazer Serpent Awakens"),  # en dash
    ("Chapter 1431  -  1431 Bonus Chapter: That Corner (1)", 1431, "Bonus Chapter: That Corner (1)"),
    ("Chapter 1432 - END - 1432 Bonus Chapter: That Corner (2)", 1432,
     "END - 1432 Bonus Chapter: That Corner (2)"),                         # not confidently a repeat: kept
    ("2 &quot;Prank&quot;", 2, '"Prank"'),                                # double-escaped at the source
    ("Crimson | FreeWebNovel", 1, "Crimson"),
    ("Chapter 627.2 Birthday and Anniversary Part 1", "627.2", "Birthday and Anniversary Part 1"),
    ("And yet...", 9, "And yet..."),                                      # its own punctuation stays
    ("Chapter 7", 7, ""),
])
def test_titles_lose_only_site_junk_and_a_repeated_number(raw, label, title):
    assert C.clean_title(raw, label) == title


def test_exactly_one_canonical_heading_and_an_explicit_untitled_fallback():
    assert ChapterContent(1, "Crimson", ["x"]).heading == "Chapter 1: Crimson"
    assert ChapterContent(627.2, "Side Story", ["x"]).heading == "Chapter 627.2: Side Story"
    assert ChapterContent(627.1, "Ten", ["x"], label="627.10").heading == "Chapter 627.10: Ten"
    assert ChapterContent(8, "", ["x"]).heading == "Chapter 8: Untitled"


# ── the body ──────────────────────────────────────────────────────────────────
def test_short_story_lines_dialogue_and_repeats_are_all_kept():
    body = ["Painful!", "Boom!", "***", "...", "He sighed.", "“Stop!”", "’Crap...’", "Myriad Self!",
            "Myriad Self!", "1", "They read:"]
    assert C.clean_paragraphs(body, label=1, title="Crimson") == body


def test_credit_lines_go_only_at_the_edges():
    body = ["Translator: AtlasStudios Editor: AtlasStudios", "Story begins.", "Middle line.",
            "Editor: hand me the report, she said, and the room went quiet for a while.",
            "More story.", "Story ends.", "Edited by: Someone"]
    out = C.clean_paragraphs(body, label=1, title="Crimson")
    assert out[0] == "Story begins." and out[-1] == "Story ends."
    assert "Editor: hand me the report" in out[2]                         # mid-chapter: kept


@pytest.mark.parametrize("first,label,title", [
    ("1 Foreigners", 1, "Foreigners"),
    ("600 Correct Reaction", 600, "Correct Reaction"),
    ("Chapter 3: The Gate", 3, "The Gate"),
    ("Chapter 3 - The Gate.", 3, "The Gate"),
    ("1432 Bonus Chapter: That Corner (2)", 1432, "END - 1432 Bonus Chapter: That Corner (2)"),
    ("Chapter 12", 12, ""),
])
def test_a_repeated_heading_at_the_top_is_removed(first, label, title):
    assert C.clean_paragraphs([first, "Real opening line."], label=label, title=title) == [
        "Real opening line."]


@pytest.mark.parametrize("first", ["3 days later, he returned.", "The Gate", "Crimson light filled the room."])
def test_prose_that_merely_resembles_a_heading_is_kept(first):
    assert C.clean_paragraphs([first, "Next."], label=3, title="The Gate")[0] == first


def test_a_heading_like_line_later_in_the_chapter_is_kept():
    body = ["Opening.", "Chapter 3: The Gate", "Closing."]
    assert C.clean_paragraphs(body, label=3, title="The Gate") == body


@pytest.mark.parametrize("line", ["Next Chapter", "previous chapter", "Report chapter", "COMMENT",
                                  "Please login to leave a comment on this chapter.",
                                  "Tap to open reader setting and adjust the font."])
def test_reader_interface_lines_are_chrome(line):
    assert C.is_site_chrome(line)


@pytest.mark.parametrize("line", ["He reported to the captain at once.", "Her comment made sense.",
                                  "A swipe of the claw.", "Report!", "Comment", "No discussion.",
                                  "Next chapter is long, the translator wrote."])
def test_prose_with_interface_words_is_not_chrome(line):
    assert not C.is_site_chrome(line)


@pytest.mark.parametrize("raw,kept", [
    ("Explore more at freewebnovel", ""),
    ("[Updated from F r e e w e b n o v e l. c o m]", ""),
    ("*** This chapter is updated by FreeWebnovel.Com", ""),
    ("Please go to https://ReadReadFreeWebNovel.com/Some-Novel/ to read the latest chapters for free", ""),
    ("He left the room. Continue your journey with freewebnovel", "He left the room."),
    ('"Thanks." Lith said. The source of this content is f r e e w e b n o v e l. c o m', '"Thanks." Lith said.'),
    ("(AN: If you aren’t reading this on FreeWebNovel, you’re reading pirated content.)",
     "(AN: If you aren’t reading this on FreeWebNovel, you’re reading pirated content.)"),
    ("It is a free web novel, she said.", "It is a free web novel, she said."),
])
def test_site_promotions_go_but_author_notes_and_prose_stay(raw, kept):
    assert C.strip_promotions(raw, ["freewebnovel"]) == kept


# ── the two FreeWebNovel page structures (synthetic, shaped like the live pages) ─
NET_PAGE = """<html><head><title>Novel: Chapter 1 - 1 Foreigners</title></head><body>
<div class="m-read" id="chapter"><div class="top"><h2 class="tit"><a href="/novel.html">Novel</a></h2>
<h1 class="chapter"><span class="chapter-title"><span class="chapter-text">Chapter 1 - 1 Foreigners</span></span></h1></div>
<div class="tts"><p>Uses your browser's voices (e.g. Microsoft voices on Windows).</p></div>
<div class="txt fwn-reader-txt"><div class="chapter-c" id="chapter-content">
<p>Translator: Someone</p><p>Editor: Someone</p><p>1 Foreigners</p>
<p>Painful!</p><p>A line with <i>inline</i> markup<b>!</b></p><p>“Stop!”</p><p>***</p>
<p>Explore more at freewebnovel</p><p>The last line.</p></div></div>
<div class="report"><p>Tell us what is wrong and we can fix it as soon as possible.</p></div>
</div></body></html>"""

COM_PAGE = """<html><head><title>Chapter 600 Correct Reaction - Novel | Free Web Novel</title></head><body>
<div class="m-read"><h1 class="tit">Novel</h1><span class="chapter">Chapter 600 Correct Reaction</span>
<div id="article"><h4>Chapter 600: Correct Reaction</h4><p>600 Correct Reaction</p>
<p>Translator: Atlas Studios Editor: Atlas Studios</p>
<p>Boom!</p><p>He sighed.</p><p>He went on. Stay connected via freewebnovel</p></div>
<div class="comments"><p>Load more comments</p></div></div></body></html>"""


def _extract(html, label, url="https://freewebnovel.com/novel/x/chapter-1"):
    adapter = fwn.FreeWebNovelAdapter(log=lambda _m: None)
    return adapter, adapter._extract_chapter(html, ChapterMeta(index=label, url=url))


def test_the_net_reader_reads_only_its_body_container():
    _, chapter = _extract(NET_PAGE, 1)
    assert chapter.heading == "Chapter 1: Foreigners"
    assert chapter.paragraphs == ["Painful!", "A line with inline markup!", "“Stop!”", "***",
                                  "The last line."]


def test_the_com_reader_drops_its_repeated_heading_credits_and_promotion():
    _, chapter = _extract(COM_PAGE, 600)
    assert chapter.heading == "Chapter 600: Correct Reaction"
    assert chapter.paragraphs == ["Boom!", "He sighed.", "He went on."]


def test_a_page_without_any_known_body_container_is_an_extraction_failure():
    from webnovel_scraper.models import EmptyExtractionError

    with pytest.raises(EmptyExtractionError):
        _extract("<html><body><h1>Chapter 1: X</h1><p>stray text</p></body></html>", 1)


def test_an_untitled_page_gets_the_explicit_fallback_and_a_warning():
    adapter, chapter = _extract("<html><body><div id='article'><p>Only text.</p></div></body></html>", 9)
    assert chapter.heading == "Chapter 9: Untitled"
    assert any("Untitled" in w for w in adapter.warnings)


# ── TXT and PDF from the same chapters ────────────────────────────────────────
CHAPTERS = [
    ChapterContent(1, "Crimson", ["Painful!", "How painful!", "A longer line of ordinary story prose " * 3]),
    ChapterContent(627.2, "Side Story", ["Chapter 5: The Gate.", "“Quoted” — dashes… and café."]),
    ChapterContent(3, "", ["Untitled body."]),
]


def test_txt_is_utf8_with_one_heading_per_chapter_in_order(tmp_path):
    path = txt_builder.create_txt(CHAPTERS, tmp_path / "out.txt")
    data = path.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")                           # no BOM
    text = data.decode("utf-8")
    assert text.startswith("Chapter 1: Crimson\n\nPainful!\n\nHow painful!")
    assert [m.group(0) for m in re.finditer(r"^Chapter [\d.]+: .*$", text, re.M)] == [
        "Chapter 1: Crimson", "Chapter 627.2: Side Story", "Chapter 5: The Gate.", "Chapter 3: Untitled"]
    assert "\n\n\nChapter 627.2: Side Story\n\n" in text
    assert not list(tmp_path.glob("*.part"))


def _pdf_words(path: Path) -> list[str]:
    with pdfplumber.open(str(path)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    return text.split()


def _txt_words(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").split()


def test_pdf_and_txt_carry_the_same_text_in_the_same_order(tmp_path):
    pdf = pdf_builder.create_pdf(CHAPTERS, tmp_path / "out.pdf", title="Test")
    txt = txt_builder.create_txt(CHAPTERS, tmp_path / "out.txt")
    assert _pdf_words(pdf) == _txt_words(txt)


def test_each_chapter_starts_a_page_and_headings_are_styled_as_headings(tmp_path):
    pdf = pdf_builder.create_pdf(CHAPTERS, tmp_path / "out.pdf", title="Test")
    with pdfplumber.open(str(pdf)) as doc:
        assert len(doc.pages) == 3
        for page, heading in zip(doc.pages, ("Chapter 1: Crimson", "Chapter 627.2: Side Story",
                                             "Chapter 3: Untitled")):
            first_line = (page.extract_text() or "").splitlines()[0]
            assert first_line == heading
            bold = {c["fontname"] for c in page.chars if c["top"] < page.chars[0]["top"] + 5}
            assert any("Helvetica-Bold" in name for name in bold)
        # A body line that reads like a heading stays body text (Times), never restyled.
        body_line = [c for c in doc.pages[1].chars if c["text"] == "G"]
        assert body_line and all("Times" in c["fontname"] for c in body_line)


def test_a_huge_first_paragraph_never_leaves_its_heading_alone_on_a_page(tmp_path):
    chapters = [ChapterContent(1, "Long", ["word " * 4000])]
    pdf = pdf_builder.create_pdf(chapters, tmp_path / "out.pdf", title="Test")
    with pdfplumber.open(str(pdf)) as doc:
        first = (doc.pages[0].extract_text() or "").splitlines()
        assert first[0] == "Chapter 1: Long" and len(first) > 5


@pytest.mark.skipif(not pdf_builder._fallback_fonts(), reason="no Windows fallback fonts installed")
def test_characters_outside_the_print_font_are_drawn_from_an_installed_font(tmp_path):
    line = "Greek Ω, arrows → ←, a star ★ and 诡秘之主."
    pdf = pdf_builder.create_pdf([ChapterContent(1, "Unicode", [line])], tmp_path / "u.pdf", title="T")
    with pdfplumber.open(str(pdf)) as doc:
        text = doc.pages[0].extract_text()
    assert "Ω" in text and "→" in text and "★" in text and "诡秘之主" in text


# ── the pipeline: every format and grouping ───────────────────────────────────
class Site:
    warnings: list = []

    def __init__(self, count, fail=()):
        self.count, self.fail = count, set(fail)

    def build_chapter_index(self, spec, **_kw):
        return [ChapterMeta(index=n, url=f"{spec.url}/chapter-{n}", slug=n) for n in range(1, self.count + 1)]

    def fetch_chapter(self, meta, spec, **_kw):
        if meta.index in self.fail:
            raise RuntimeError("HTTP 503")
        return ChapterContent(meta.index, f"Title {meta.index}", [f"Line one of {meta.index}.", "Boom!"])


def _run(tmp_path, mode, formats, fail=()):
    job = ScrapeJob(novel_slug="shadow-slave", adapter_key="freewebnovel", start=1, end=4, delay=0.0,
                    output_mode=mode, chunk_size=3, use_cache=False, output_dir=tmp_path / "out",
                    allocate_run=True, formats=formats)
    return pipeline.run_scrape(job, adapter=Site(4, fail), log=lambda _m: None, sleep_fn=lambda _s: None,
                               manifest_store=ChapterManifestStore(tmp_path / "b", tmp_path / "r"))


@pytest.mark.parametrize("mode", list(OutputMode))
@pytest.mark.parametrize("formats", [("pdf",), ("txt",), ("pdf", "txt")])
def test_every_format_and_grouping_combination(tmp_path, mode, formats):
    report = _run(tmp_path, mode, formats)
    folder = report.output_dir
    expected = {OutputMode.SEPARATE: 4, OutputMode.CHUNKED: 2, OutputMode.SINGLE: 1}[mode]
    pdfs = sorted(p.stem for p in (folder / "PDF").glob("*.pdf")) if "pdf" in formats else []
    txts = sorted(p.stem for p in (folder / "TXT").glob("*.txt")) if "txt" in formats else []
    if "pdf" in formats:
        assert len(pdfs) == expected
    else:
        assert not (folder / "PDF").exists()
    if "txt" in formats:
        assert len(txts) == expected
        for txt in (folder / "TXT").glob("*.txt"):
            if "pdf" in formats:                                         # same text, same order
                assert _pdf_words(folder / "PDF" / (txt.stem + ".pdf")) == _txt_words(txt)
    if formats == ("pdf", "txt"):
        assert pdfs == txts                                              # same groups, same names
    assert len(report.written) == expected * len(formats)


def test_a_partial_failure_is_missing_from_both_formats_and_reported(tmp_path):
    report = _run(tmp_path, OutputMode.CHUNKED, ("pdf", "txt"), fail={2})
    txt = (report.output_dir / "TXT" / "Shadow_Slave_Chapters_1-3.txt").read_text(encoding="utf-8")
    assert "Chapter 1: Title 1" in txt and "Chapter 3: Title 3" in txt and "Chapter 2:" not in txt
    assert _pdf_words(report.output_dir / "PDF" / "Shadow_Slave_Chapters_1-3.pdf") == txt.split()
    assert report.failed == [2]
