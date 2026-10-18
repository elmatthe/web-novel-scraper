"""One deterministic, conservative normalisation of chapter text (v0.3.0 Checkpoint 05).

Every adapter extracts a chapter's raw title and paragraphs from its own page structure; this module
is the single place that turns them into canonical content for both TXT and PDF. It edits only what
can be identified with confidence and never rewords the story:

* **Characters.** Zero-width and format characters (ZWSP/ZWNJ/ZWJ, word joiner, BOM, soft hyphen,
  bidi controls) and C0/C1 control characters are removed; no-break and other Unicode spaces become
  ordinary spaces; whitespace runs collapse. Every other character (letters of any script, curly
  quotes, dashes, ellipses, CJK) is kept exactly. HTML entities a site double-escaped in a title
  (``&quot;``) are decoded.
* **Title.** Site suffixes ("- Free Web Novel", "| FreeWebNovel", "- Read Online") and a leading
  repeat of the chapter's own number ("1 Foreigners" -> "Foreigners") are removed, as are the
  separators around them ("Chapter 714 – Title" -> "Title"). Nothing else in a title changes.
* **Heading.** Exactly one line, ``Chapter N: Title`` (no forced period); ``Chapter N: Untitled``
  when no title can be recovered (the adapter warns).
* **Body.** Empty and invisible-only paragraphs go. A translator/editor credit line goes only when it
  is one of the first or last three paragraphs (measured: every one of 306 sampled credit lines was
  in the first three). A repeat of the heading goes only among the first two paragraphs, and only
  when it starts with the chapter number (or "Chapter N") and the rest is the title. There is no
  length filter, no keyword or substring filter and no de-duplication of repeated lines: short lines
  ("Boom!", "***", "He sighed."), dialogue in any quote style and lines a story repeats on purpose
  are prose (measured on the cached pages: the old filters deleted thousands of them).
"""

from __future__ import annotations

import html
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

UNTITLED = "Untitled"

# Removed outright: zero-width/format characters FreeWebNovel injects between words (notably
# U+200C), the BOM, soft hyphens and bidi controls.
_INVISIBLE = re.compile(
    "[​‌‍⁠﻿­‎‏‪-‮⁦-⁩᠎]"
)
# C0/C1 controls except tab/newline (which become spaces in a paragraph anyway).
_CONTROLS = re.compile("[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]")
# Unicode spaces that are not ordinary spaces (NBSP, en/em/thin spaces, ideographic space...).
_SPACES = re.compile("[   -   　]")
_WS = re.compile(r"\s+")

_SEP = r"[:\-‐-―|.]"     # colon, hyphen, the dash family, pipe, period
_HEADING = re.compile(rf"^\s*chapter\s+(\d+(?:\.\d+)?)\b(?:\s*{_SEP}+\s*|\s+)?(.*)$", re.IGNORECASE)
_SITE_SUFFIXES = (
    re.compile(r"\s*[|\-–—]\s*Empire\s*Novel.*$", re.IGNORECASE),
    re.compile(r"\s*[|\-–—]\s*Read\s*Online.*$", re.IGNORECASE),
    re.compile(r"\s*[|\-–—]\s*Free\s*Web\s*Novel.*$", re.IGNORECASE),
    re.compile(r"\s*[|\-–—]\s*FreeWebNovel.*$", re.IGNORECASE),
)
_CREDIT = re.compile(
    r"^(?:translator|translated by|translation|tl|editor|edited by|editing|ed|proofreader|"
    r"proofread by|proofreading|pr)\s*[:：\-–—]\s*\S",
    re.IGNORECASE,
)
_CREDIT_MAX_LEN = 160
_EDGE = 3            # credit lines are looked for only among the first/last three paragraphs
_HEADING_ZONE = 2    # a repeated heading is looked for only among the first two paragraphs


def normalize_text(text: object) -> str:
    """Characters and whitespace only (see the module docstring); never words."""
    value = "" if text is None else str(text)
    value = unicodedata.normalize("NFC", value)
    value = _INVISIBLE.sub("", value)
    value = _CONTROLS.sub(" ", value)
    value = _SPACES.sub(" ", value)
    return _WS.sub(" ", value).strip()


def label_text(value: object) -> str:
    """Canonical text of a chapter number: ``627.2`` stays ``627.2``, ``627.0``/``627`` -> ``627``."""
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            whole, _, frac = text.partition(".")
            return str(int(whole)) + (f".{frac}" if frac else "")
        return text
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    return str(int(dec)) if dec == dec.to_integral_value() else str(dec)


def parse_heading(text: object) -> tuple[Optional[str], str]:
    """``("12", "Title")`` from "Chapter 12 - Title" / "Chapter 12: Title" / "Chapter 12 Title";
    ``(None, "")`` when the text is not a chapter heading."""
    match = _HEADING.match(normalize_text(text))
    if not match:
        return None, ""
    return label_text(match.group(1)), match.group(2).strip()


def clean_title(raw: object, label: object) -> str:
    """The title of chapter *label* from its raw site text (heading, list entry or metadata)."""
    title = normalize_text(html.unescape(html.unescape(normalize_text(raw))))
    number, rest = parse_heading(title)
    if number is not None and number == label_text(label):
        title = rest
    for suffix in _SITE_SUFFIXES:
        title = suffix.sub("", title)
    num = re.escape(label_text(label))
    # A leading repeat of the number: "1 Foreigners", "1431 - Bonus Chapter", "12: Title".
    title = re.sub(rf"^{num}(?:\s*{_SEP}+\s*|\s+)(?=\S)", "", title)
    if title == label_text(label):
        title = ""
    title = re.sub(rf"^(?:\s*{_SEP})+\s*", "", title)
    title = re.sub(r"\s*[:\-‐-―|]+\s*$", "", title)
    return title.strip()


def heading_for(label: object, title: str) -> str:
    """The one canonical heading line."""
    return f"Chapter {label_text(label)}: {title.strip() or UNTITLED}"


def _comparable(text: str) -> str:
    text = normalize_text(text).casefold()
    text = re.sub("[‐-―]", "-", text)
    text = re.sub("[‘’“”\"']", "", text)
    text = re.sub(r"\s*([:\-|.])\s*", r"\1", text)
    return text.strip(" :-|.")


def is_repeated_heading(paragraph: str, label: object, title: str) -> bool:
    """Whether *paragraph* is the page repeating the chapter heading (number first, then title)."""
    text = _comparable(paragraph)
    number = re.escape(label_text(label))
    match = re.match(rf"^chapter\s*{number}(?![\d.])", text) or re.match(rf"^{number}(?![\d.])", text)
    if not match:
        return False
    rest = text[match.end():].strip(" :-|.")
    if not rest:
        return True
    wanted = _comparable(title)
    if not wanted:
        return False
    return rest == wanted or (len(rest) >= 8 and wanted.endswith(rest))


def is_credit_line(paragraph: str) -> bool:
    text = normalize_text(paragraph)
    return len(text) <= _CREDIT_MAX_LEN and bool(_CREDIT.match(text))


# Reader-interface text that is never a whole story paragraph. Matched against the WHOLE paragraph
# (case-insensitive, ignoring surrounding punctuation), never as a substring: the old substring
# filters ("comment", "report", "login", "swipe"...) deleted real prose such as "He reported...".
# Measured over 709,458 cached paragraphs: the only whole-paragraph hit was one "Next Chapter".
_CHROME_EXACT = frozenset({
    "next chapter", "previous chapter", "prev chapter", "chapter list", "table of contents",
    "report chapter", "add to library", "load more comments", "reader mode", "text to speech",
})
_CHROME_PATTERNS = (
    re.compile(r"^please (?:log ?in|sign ?in|sign up|register)\b.{0,80}$", re.IGNORECASE),
    re.compile(r"^.{0,40}\breader settings?\b.{0,40}$", re.IGNORECASE),
)
# Reverend Insanity pages carry the old translation site's inline comment buttons as paragraphs.
_CHROME_CASE_SENSITIVE = frozenset({"COMMENT"})


def is_site_chrome(paragraph: str) -> bool:
    """Whether a whole paragraph is reader-interface text (see ``_CHROME_EXACT``)."""
    text = normalize_text(paragraph)
    if text in _CHROME_CASE_SENSITIVE:
        return True
    key = text.casefold().strip(" .!:>-<«»→←")
    return key in _CHROME_EXACT or any(p.match(text) for p in _CHROME_PATTERNS)


_AUTHOR_NOTE = re.compile(r"^\(?\s*(?:A/?N|Author[’']?s note|Author note)\s*[:\-]", re.IGNORECASE)


def _site_token(name: str) -> str:
    """A pattern for a site name written with optional spaces/dots between its letters."""
    return r"[\s.]*".join(re.escape(c) for c in name)


def strip_promotions(paragraph: str, site_names: Iterable[str]) -> str:
    """Remove a website's own promotional sentence (``Continue reading on freewebnovel.com``,
    ``[Updated from F r e e w e b n o v e l. c o m]``, ``Please go to https://...freewebnovel.com/...
    to read the latest chapters for free``) when it is the whole paragraph or the paragraph's last
    sentence. Anything mentioning the site mid-sentence (an author's anti-piracy note, say) is kept.
    Returns the paragraph, possibly empty."""
    text = normalize_text(paragraph)
    if _AUTHOR_NOTE.match(text):
        return text            # the author's own words, even when they name the site
    for name in site_names:
        token = rf"\[?\s*(?:read)*\s*{_site_token(name)}(?:[\s.,]*(?:c\s*o\s*m|live|net))?\s*\]?"
        url = rf"https?://\S*{_site_token(name)}\S*"
        lead = rf"[A-Z][^.!?”\"]{{0,70}}?\b(?:at|on|via|with|through|from|by|to|is)\s+(?:our\s+)?"
        promo = (
            rf"(?:[\[(]\s*|\*{{1,3}}\s*)?"
            rf"(?:{lead}{token}(?:\s+only)?"                       # "... on freewebnovel(.com)"
            rf"|{lead}{url}(?:[^.!?”\"]{{0,40}}?)"             # "Please go to https://... to read ..."
            rf"|{token})\s*[.!\])]*\s*$"
        )
        whole = re.match(rf"^{promo}", text, re.IGNORECASE)
        if whole:
            return ""
        tail = re.search(rf"(?<=[.!?”\"’)])\s*{promo}", text, re.IGNORECASE)
        if tail and tail.start() > 0:
            text = text[:tail.start()].rstrip()
    return text


def clean_paragraphs(paragraphs: Iterable[object], *, label: object, title: str) -> list[str]:
    """The canonical body: see the module docstring for exactly what is removed."""
    body = [normalize_text(p) for p in paragraphs]
    body = [p for p in body if p]
    count = len(body)
    kept = [p for i, p in enumerate(body)
            if not ((i < _EDGE or i >= count - _EDGE) and is_credit_line(p))]
    out: list[str] = []
    for i, p in enumerate(kept):
        if i < _HEADING_ZONE and not out and is_repeated_heading(p, label, title):
            continue
        out.append(p)
    return out
