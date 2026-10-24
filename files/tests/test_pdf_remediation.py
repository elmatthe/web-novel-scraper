"""Offline regressions for the final Supreme Magus rendering defects."""
import struct
import threading
from pathlib import Path

import pytest
from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics

from webnovel_scraper import pdf_builder
from webnovel_scraper.models import ChapterContent


def test_bullet_preserves_exact_unicode_in_heading_and_body(tmp_path, monkeypatch):
    import reportlab

    # Use the locally bundled font so this regression is independent of OS fonts.
    font_dir = Path(reportlab.__file__).parent / "fonts"
    monkeypatch.setattr(pdf_builder, "_font_dirs", lambda: [font_dir])
    monkeypatch.setattr(pdf_builder, "_FALLBACK_FONT_FILES", (("WNS-TestVera", "Vera.ttf", None),))
    monkeypatch.setattr(pdf_builder, "_fallback_cache", None)
    chapter = ChapterContent(1099, "1368 \u2022", ["No, this is not a prophecy\u2022", "Next paragraph."])
    path = pdf_builder.create_pdf([chapter], tmp_path / "bullet.pdf", "T")
    texts = [page.extract_text() for page in PdfReader(path).pages]
    assert all(text.strip() for text in texts)
    assert "".join("".join(texts).split()) == "".join(
        "".join((chapter.heading, *chapter.paragraphs)).split())
    assert "\x7f" not in "".join(texts)


def test_full_page_before_cancel_checkpoint_does_not_create_blank(tmp_path, monkeypatch):
    original = pdf_builder._styles

    def styles():
        body, heading = original()
        body.leading = 678  # heading + spacing + one body line fills the Letter frame
        return body, heading

    monkeypatch.setattr(pdf_builder, "_styles", styles)
    chapters = [ChapterContent(i, "Boundary", ["body"]) for i in (1, 2)]
    path = pdf_builder.create_pdf(chapters, tmp_path / "boundary.pdf", "T",
                                  cancel_event=threading.Event())
    texts = [page.extract_text() for page in PdfReader(path).pages]
    assert len(texts) == 2
    assert texts == [f"Chapter {i}: Boundary\nbody\n" for i in (1, 2)]


def test_fraction_boundaries_and_long_paragraphs_preserve_all_text(tmp_path):
    chapters = [ChapterContent(1, "Long", ["word " * 4000]),
                ChapterContent(627, "Fraction", ["Last paragraph."], label="627.2")]
    path = pdf_builder.create_pdf(chapters, tmp_path / "long.pdf", "T",
                                  cancel_event=threading.Event())
    texts = [page.extract_text() for page in PdfReader(path).pages]
    assert all(text.strip() for text in texts)
    assert any(text.startswith("Chapter 627.2: Fraction") for text in texts)
    assert "".join("".join(texts).split()) == "".join(
        "".join((ch.heading, *ch.paragraphs)) for ch in chapters).replace(" ", "")


def test_local_collection_embeds_real_lepcha_and_tai_viet_glyphs(tmp_path):
    path = next((d / "SansSerifCollection.ttf" for d in pdf_builder._font_dirs()
                 if (d / "SansSerifCollection.ttf").is_file()), None)
    if path is None:
        pytest.skip("local SIL OFL Sans Serif Collection is not installed")
    line = "\u1c0d \u1c45 \u1c47 \uaaa7 \uaac0"
    chapters = [ChapterContent(1, "Coverage", [line])]
    assert not pdf_builder.unsupported_characters(chapters)
    pdf = pdf_builder.create_pdf(chapters, tmp_path / "glyphs.pdf", "T")
    reader = PdfReader(pdf)
    assert line in reader.pages[0].extract_text()
    for char in line.replace(" ", ""):
        name = next(name for name, cmap in pdf_builder._fallback_fonts() if ord(char) in cmap)
        face = pdfmetrics.getFont(name).face
        gid = face.charToGlyph[ord(char)]
        assert gid != 0
        assert face.glyphPos[gid + 1] > face.glyphPos[gid]
        assert struct.unpack_from(">H", face.get_table("OS/2"), 8)[0] & 0x0302 == 0
    embedded = [font.get_object() for font in reader.pages[0]["/Resources"]["/Font"].values()
                if "SansSerifCollection" in str(font.get_object().get("/BaseFont"))]
    assert embedded and embedded[0]["/FontDescriptor"]["/FontFile2"].get_data()


def test_markup_fails_closed_without_coverage(monkeypatch):
    monkeypatch.setattr(pdf_builder, "_fallback_fonts", lambda: [])
    with pytest.raises(pdf_builder.UnsupportedGlyphError, match="U\\+1C0D"):
        pdf_builder.markup("\u1c0d")


@pytest.mark.parametrize("rights", [0x0002, 0x0100, 0x0200])
def test_fallback_refuses_fonts_that_prohibit_subset_embedding(tmp_path, monkeypatch, rights):
    from types import SimpleNamespace
    (tmp_path / "restricted.ttf").touch()
    face = SimpleNamespace(table={"OS/2": True}, charToGlyph={0x1c0d: 1},
        get_table=lambda _: b"\0" * 8 + struct.pack(">H", rights))
    monkeypatch.setattr(pdf_builder, "_font_dirs", lambda: [tmp_path])
    monkeypatch.setattr(pdf_builder, "_FALLBACK_FONT_FILES", (("Restricted", "restricted.ttf", None),))
    monkeypatch.setattr(pdf_builder, "_UnicodeTTFont", lambda *args, **kwargs: SimpleNamespace(face=face))
    monkeypatch.setattr(pdf_builder, "_fallback_cache", None)
    monkeypatch.setattr(pdfmetrics, "registerFont", lambda _: pytest.fail("restricted font registered"))
    assert pdf_builder._fallback_fonts() == []


def test_cmap_notdef_entry_does_not_count_as_coverage(tmp_path, monkeypatch):
    from types import SimpleNamespace
    (tmp_path / "font.ttf").touch()
    face = SimpleNamespace(table={}, charToGlyph={0x1c0d: 0, 0x1c45: 2})
    monkeypatch.setattr(pdf_builder, "_font_dirs", lambda: [tmp_path])
    monkeypatch.setattr(pdf_builder, "_FALLBACK_FONT_FILES", (("Synthetic", "font.ttf", None),))
    monkeypatch.setattr(pdf_builder, "_UnicodeTTFont", lambda *args, **kwargs: SimpleNamespace(face=face))
    monkeypatch.setattr(pdf_builder, "_fallback_cache", None)
    monkeypatch.setattr(pdfmetrics, "registerFont", lambda _: None)
    assert pdf_builder._fallback_fonts() == [("Synthetic", {0x1c45})]
