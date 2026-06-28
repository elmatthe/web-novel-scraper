"""The single PDF layout for webnovel_scraper.

Ported (behavior-verbatim) from ``sm_pdf_editor-v8.2.py``'s ReportLab layout:
Letter page, 0.5" margins, Times-Roman 11/15pt justified body, Helvetica-Bold
14/18pt #134252 headings, form-feed (``\\f``) = page break. The editor's
spellcheck / lexicon / profanity / OCR-repair / GUI code is intentionally NOT
ported.

PDF post-processing uses ``pypdf`` (the legacy file used the deprecated PyPDF2).

Public API:
  - ``create_pdf(chapters, output_path, title)`` — the pipeline entry point.
  - ``chapters_to_text(chapters)`` — join ``ChapterContent`` into feed text.
  - ``create_pdf_from_text(text, output_path, title=None)`` — low-level layout.
  - ``remove_single_heading_pages(pdf_path)`` — drop heading-only pages.
"""

from __future__ import annotations

import re
from pathlib import Path

import pdfplumber
from pypdf import PdfReader, PdfWriter
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate

from .models import ChapterContent

# ── Heading regexes (ported verbatim from sm_pdf_editor-v8.2.py) ──────────────
# Strict chapter heading; allows an empty title ("Chapter 1818:."). IGNORECASE.
CHAPTER_HEADING_EXACT_RE = re.compile(
    r"^Chapter\s+\d+:\s*.*?\.\s*$",
    re.IGNORECASE,
)
# Merged chapter+body: "Chapter N: Title" followed by dialogue (quote).
CHAPTER_MERGED_QUOTE_RE = re.compile(
    r"^(Chapter\s+\d+:\s*[^\"\'“‘\n]+)\s+([\"'“‘].*)",
    re.IGNORECASE,
)
# Fallback: "Chapter N: Title" followed by a new sentence (space + capital).
CHAPTER_MERGED_CAPITAL_RE = re.compile(
    r"^(Chapter\s+\d+:\s*.+?)\s+([A-Z][^.]+\.\s+.*)",
    re.IGNORECASE,
)
# Heading-only page detection (post-processing).
SINGLE_HEADING_PAGE_RE = re.compile(
    r"^Chapter\s+\d+:\s*.*?\.?\s*$",
    re.IGNORECASE | re.DOTALL,
)

MAX_HEADING_LENGTH = 500  # allow long chapter titles


def _is_chapter_heading(text: str) -> bool:
    """Whether a paragraph is a chapter heading (title may be empty)."""
    stripped = text.strip()
    if len(stripped) > MAX_HEADING_LENGTH:
        return False
    if CHAPTER_HEADING_EXACT_RE.match(stripped):
        return True
    return False


def _escape_html(text: str) -> str:
    """Escape text for safe use in a reportlab Paragraph (HTML-like markup)."""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


def create_pdf_from_text(
    text: str, output_path: Path, title: str | None = None
) -> None:
    """Build a PDF from feed text.

    Splits on ``\\f`` into pages, then on blank lines into paragraphs. Splits
    merged chapter+body paragraphs so headings render correctly. Layout is
    identical to the legacy ``create_pdf_from_text``.
    """
    styles = getSampleStyleSheet()

    body_style = ParagraphStyle(
        "CustomBody",
        parent=styles["BodyText"],
        fontName="Times-Roman",
        fontSize=11,
        leading=15,
        alignment=TA_JUSTIFY,
        spaceBefore=6,
        spaceAfter=6,
    )

    chapter_style = ParagraphStyle(
        "ChapterHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        spaceBefore=18,
        spaceAfter=12,
        textColor="#134252",
        alignment=TA_LEFT,
    )

    doc_kwargs = dict(
        pagesize=letter,
        rightMargin=0.5 * inch,
        leftMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )
    if title:
        doc_kwargs["title"] = title
    doc = SimpleDocTemplate(str(output_path), **doc_kwargs)

    story = []
    pages = [p for p in text.split("\f") if p.strip()]

    for i, page_text in enumerate(pages):
        if i > 0:
            story.append(PageBreak())

        paragraphs = [p for p in page_text.split("\n\n") if p.strip()]

        for para in paragraphs:
            stripped = para.strip()
            clean_text = " ".join(stripped.split())

            if _is_chapter_heading(clean_text):
                safe_text = _escape_html(clean_text)
                story.append(Paragraph(safe_text, chapter_style))
            else:
                # Merged chapter+body: "Chapter N: Title" followed by quote/body.
                m_merged = CHAPTER_MERGED_QUOTE_RE.match(clean_text)
                if not m_merged:
                    m_merged = CHAPTER_MERGED_CAPITAL_RE.match(clean_text)
                if m_merged:
                    heading_part = m_merged.group(1).rstrip()
                    body_part = m_merged.group(2)
                    if not heading_part.endswith("."):
                        heading_part = heading_part + "."
                    if len(heading_part) <= MAX_HEADING_LENGTH:
                        story.append(
                            Paragraph(_escape_html(heading_part), chapter_style)
                        )
                    if body_part.strip():
                        story.append(
                            Paragraph(_escape_html(body_part.strip()), body_style)
                        )
                elif clean_text:
                    body_html = _escape_html(clean_text)
                    story.append(Paragraph(body_html, body_style))

    doc.build(story)


def remove_single_heading_pages(pdf_path: Path) -> Path:
    """Drop pages that contain only a single chapter heading line.

    Ported verbatim from ``sm_pdf_editor-v8.2.py``; uses ``pdfplumber`` to detect
    heading-only pages and ``pypdf`` to rewrite the file in place. Returns the
    (unchanged) path for chaining.
    """
    pdf_path = Path(pdf_path)
    with pdfplumber.open(str(pdf_path)) as pdf:
        total_pages = len(pdf.pages)
        pages_to_keep = []
        for page_idx, page in enumerate(pdf.pages):
            raw_text = page.extract_text() or ""
            text = raw_text.strip()
            if not text:
                pages_to_keep.append(page_idx)
                continue
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            if len(lines) != 1:
                pages_to_keep.append(page_idx)
                continue
            only_line = lines[0]
            if SINGLE_HEADING_PAGE_RE.match(only_line):
                continue
            pages_to_keep.append(page_idx)

    if len(pages_to_keep) == total_pages:
        return pdf_path

    reader = PdfReader(str(pdf_path))
    writer = PdfWriter()
    for idx in pages_to_keep:
        writer.add_page(reader.pages[idx])
    with open(pdf_path, "wb") as f_out:
        writer.write(f_out)
    return pdf_path


# ── Pipeline-facing API ──────────────────────────────────────────────────────
def chapters_to_text(chapters: list[ChapterContent]) -> str:
    """Join chapters into PDF feed text: heading + body per chapter, ``\\f`` between.

    Each chapter contributes ``heading\\n\\nbody...``; chapters are separated by a
    form-feed so each starts on a new page.
    """
    return "\f\n\n".join(ch.raw_text for ch in chapters)


def create_pdf(
    chapters: list[ChapterContent], output_path: Path, title: str
) -> Path:
    """Render chapters into one PDF and strip heading-only pages. Returns the path."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    text = chapters_to_text(chapters)
    create_pdf_from_text(text, output_path, title=title)
    remove_single_heading_pages(output_path)
    return output_path
