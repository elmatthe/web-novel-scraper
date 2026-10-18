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

import os
import re
import tempfile
from io import BytesIO
from pathlib import Path

import pdfplumber
from pypdf import PdfReader, PdfWriter
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Flowable, PageBreak, Paragraph, SimpleDocTemplate

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


def _atomic_write(path: Path, write_fn) -> None:
    """Write to a unique temp file in the SAME directory, then ``os.replace()`` it
    onto ``path`` so a cancel / crash / kill mid-write can never leave a corrupt
    final file (the publish is atomic on both POSIX and Windows). ``write_fn(file)``
    does the actual writing into the temp's binary file object. Any failure removes
    the temp and re-raises, so no ``.part`` artifact is left behind. 0.2.0 BUG-2
    partial-cleanup fix (applies to FWN and WND output alike)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".part"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f_out:
            write_fn(f_out)
        os.replace(tmp, path)
    except BaseException:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def remove_single_heading_pages(pdf_path: Path) -> Path:
    """Drop pages that contain only a single chapter heading line.

    Ported from ``sm_pdf_editor-v8.2.py``; uses ``pdfplumber`` to detect
    heading-only pages and ``pypdf`` to rewrite the file. The rewrite is now
    ATOMIC (temp-then-``os.replace``, via :func:`_atomic_write`) and reads the
    source from an in-memory copy so no file handle lingers on the destination —
    a cancel/crash mid-rewrite can never truncate the existing file. Returns the
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

    # Read the source from an in-memory copy so no handle lingers on pdf_path,
    # then publish atomically (temp-then-os.replace) so the existing file is never
    # truncated mid-rewrite.
    reader = PdfReader(BytesIO(pdf_path.read_bytes()))
    writer = PdfWriter()
    for idx in pages_to_keep:
        writer.add_page(reader.pages[idx])
    _atomic_write(pdf_path, writer.write)
    return pdf_path


# ── Pipeline-facing API ──────────────────────────────────────────────────────
def chapters_to_text(chapters: list[ChapterContent]) -> str:
    """Join chapters into PDF feed text: heading + body per chapter, ``\\f`` between.

    Each chapter contributes ``heading\\n\\nbody...``; chapters are separated by a
    form-feed so each starts on a new page.
    """
    return "\f\n\n".join(ch.raw_text for ch in chapters)


# ── Structured rendering (v0.3.0 Checkpoint 05) ─────────────────────────────
# The pipeline renders from canonical ``ChapterContent``: each chapter's heading is ITS heading,
# styled as a heading, and every paragraph is body text. Nothing is guessed from text patterns, so a
# story line that happens to read "Chapter 5: ..." stays body text, and fractional chapters
# ("Chapter 627.2: ...") get a real heading. The print style is the established one above.
#
# Characters the standard Times-Roman font cannot encode (it covers WinAnsi: Latin letters, curly
# quotes, dashes, ellipses, accents, €) would otherwise come out as wrong glyphs. Each run of such
# characters is drawn from the first installed font that has them; everything else keeps the
# established font, so ordinary text looks exactly as before. With no font for a character it is
# left as it is (and reported), never replaced or dropped.
_FALLBACK_FONT_FILES = (
    # (registered name, file under %WINDIR%\Fonts, TrueType collection index)
    ("WNS-TimesNewRoman", "times.ttf", None),
    ("WNS-SegoeUI", "segoeui.ttf", None),
    ("WNS-SegoeUISymbol", "seguisym.ttf", None),
    ("WNS-SegoeUIEmoji", "seguiemj.ttf", None),
    ("WNS-YaHei", "msyh.ttc", 0),
    ("WNS-MSGothic", "msgothic.ttc", 0),
    ("WNS-MalgunGothic", "malgun.ttf", None),
    ("WNS-Nirmala", "Nirmala.ttf", None),
    ("WNS-ArialUnicode", "arialuni.ttf", None),
)
_fallback_cache: list | None = None
missing_glyphs: set[str] = set()   # characters no available font could draw (diagnostics)


def _font_dirs() -> list[Path]:
    dirs = []
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if windir:
        dirs.append(Path(windir) / "Fonts")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    return dirs


def _fallback_fonts() -> list:
    """``[(font name, set of code points)]`` of the fallback fonts installed here (cached)."""
    global _fallback_cache
    if _fallback_cache is not None:
        return _fallback_cache
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    found = []
    for name, filename, index in _FALLBACK_FONT_FILES:
        for directory in _font_dirs():
            path = directory / filename
            if not path.is_file():
                continue
            try:
                font = TTFont(name, str(path), subfontIndex=index or 0) if index is not None else TTFont(name, str(path))
                pdfmetrics.registerFont(font)
                found.append((name, set(font.face.charToGlyph)))
            except Exception:  # noqa: BLE001 - an unusable font file is simply skipped
                pass
            break
    _fallback_cache = found
    return found


def _encodable(char: str) -> bool:
    try:
        char.encode("cp1252")
    except UnicodeEncodeError:
        return False
    return True


def markup(text: str) -> str:
    """ReportLab paragraph markup for plain *text*: escaped, with characters outside the standard
    font drawn from a fallback font that has them."""
    if all(_encodable(c) for c in text):
        return _escape_html(text)
    out: list[str] = []
    run_font, run = None, []

    def flush() -> None:
        if run:
            chunk = _escape_html("".join(run))
            out.append(chunk if run_font is None else f'<font name="{run_font}">{chunk}</font>')
            run.clear()

    fonts = _fallback_fonts()
    for char in text:
        font = None
        if not _encodable(char):
            code = ord(char)
            font = next((name for name, cmap in fonts if code in cmap), None)
            if font is None:
                missing_glyphs.add(char)
        if font != run_font:
            flush()
            run_font = font
        run.append(char)
    flush()
    return "".join(out)


def _styles() -> tuple[ParagraphStyle, ParagraphStyle]:
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "CustomBody", parent=styles["BodyText"], fontName="Times-Roman", fontSize=11,
        leading=15, alignment=TA_JUSTIFY, spaceBefore=6, spaceAfter=6,
    )
    heading = ParagraphStyle(
        "ChapterHeading", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=14,
        leading=18, spaceBefore=18, spaceAfter=12, textColor="#134252", alignment=TA_LEFT,
        keepWithNext=1,   # a heading never ends a page alone (no heading-only pages)
    )
    return body, heading


class _CancelPoint(Flowable):
    """A zero-size flowable that stops a long layout when Stop is pressed (v0.3.0 06). ReportLab
    lays the story out in one call; checking here, once per chapter, keeps Stop responsive while a
    whole-novel file (measured: about 85 s for 1,432 chapters) is being built."""

    def __init__(self, cancel_event) -> None:
        super().__init__()
        self._cancel = cancel_event

    def wrap(self, available_width, available_height):
        if self._cancel is not None and self._cancel.is_set():
            from .request_manager import ScrapeCancelled

            raise ScrapeCancelled("stopped while the PDF was being built")
        return 0, 0

    def draw(self) -> None:
        pass


def build_story(chapters: list[ChapterContent], cancel_event=None) -> list:
    """The ReportLab flowables: per chapter a page break (after the first), its heading, its
    paragraphs, in the given order."""
    body_style, heading_style = _styles()
    story: list = []
    for i, chapter in enumerate(chapters):
        if cancel_event is not None:
            story.append(_CancelPoint(cancel_event))
        if i > 0:
            story.append(PageBreak())
        story.append(Paragraph(markup(chapter.heading), heading_style))
        for paragraph in chapter.paragraphs:
            story.append(Paragraph(markup(paragraph), body_style))
    return story


def create_pdf(
    chapters: list[ChapterContent], output_path: Path, title: str, *, cancel_event=None
) -> Path:
    """Render chapters into one PDF and publish it atomically. Returns the path.

    The whole build is staged in a temp file in the SAME directory and published
    onto ``output_path`` with a single atomic ``os.replace`` only after the build has fully
    succeeded and been flushed to disk — so a cancel / crash / kill at any point leaves NO
    partial or corrupt final PDF (and no leftover ``.part`` artifact). 0.2.0 BUG-2
    partial-cleanup fix; applies to every grouping. Since v0.3.0 the layout is built from the
    chapters' structure (``build_story``), not re-parsed from joined text."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        dir=str(output_path.parent), prefix=output_path.name + ".", suffix=".part"
    )
    os.close(fd)  # ReportLab opens the path itself
    tmp = Path(tmp_name)
    try:
        doc_kwargs = dict(
            pagesize=letter, rightMargin=0.5 * inch, leftMargin=0.5 * inch,
            topMargin=0.5 * inch, bottomMargin=0.5 * inch,
        )
        if title:
            doc_kwargs["title"] = title
        SimpleDocTemplate(str(tmp), **doc_kwargs).build(build_story(chapters, cancel_event))
        with open(tmp, "r+b") as handle:   # on disk before it is published (v0.3.0 03.3)
            os.fsync(handle.fileno())
        os.replace(tmp, output_path)       # atomic publish
    except BaseException:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise
    return output_path
