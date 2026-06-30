"""Shared Cloudflare challenge detector for the fetch layer (Phase 9 fix).

One implementation, imported by both :mod:`request_manager` and :mod:`cf_bypass`
so the two can never drift. It distinguishes an *active* interstitial from a
fully-cleared, content-bearing page that merely still carries Cloudflare's
ambient beacon script (the long-standing false-flag that made camoufox "still
see a Cloudflare challenge" on a real WebNovel chapter page).

Detection rules:
  * Strong interstitial markers flag immediately — they only ever appear on the
    live challenge page.
  * Ambient markers (``/cdn-cgi/challenge-platform/``, ``cf-mitigated``,
    ``managed-challenge``) flag ONLY when the page carries no real structural
    payload. They are injected on every protected response, cleared or not, so on
    their own they prove nothing.
  * There is NO length-based clearance. A large body is never treated as a real
    payload by size alone — "real payload" means *structural* evidence only.

"Real payload" is decided structurally (not by a bare substring): a parseable
``__NEXT_DATA__`` JSON blob, a ``g_data.chapInfo`` object with a non-empty
``contents`` array, or a real chapter-body container element in the DOM. Cheap
substring checks are used only as prefilters to avoid parsing on the common
challenge page; the structural check is what actually clears an ambient marker.
"""

from __future__ import annotations

import json
from typing import Optional

from bs4 import BeautifulSoup

# Strong interstitial markers — appear ONLY on the live challenge page, so any
# single one is conclusive evidence of an active challenge.
STRONG_CF_MARKERS = (
    "just a moment",
    "cf-browser-verification",
    "cf_chl_opt",
    "_cf_chl_",
)

# Ambient markers Cloudflare injects on EVERY protected response — including a
# fully-cleared, content-bearing page (the /cdn-cgi/challenge-platform/ beacon
# script, the cf-mitigated header echo, a managed-challenge stub). On their own
# they do NOT mean the page is still a challenge; they only count when no real
# structural payload is present.
AMBIENT_CF_MARKERS = ("challenge-platform", "cf-mitigated", "managed-challenge")

# Chapter-body containers/selectors the adapters key off. Lowercased substrings
# used purely as a cheap prefilter; presence is confirmed structurally below.
#
# FreeWebNovel's chapter body lives in ``<div id="article">`` (the FWN adapter's
# primary container) — the incidental ``.m-read`` / ``class="txt"`` wrapper
# classes were NOT a reliable signal on a live camoufox-rendered page (Firefox
# serialization / multi-class / inline-style variance breaks the brittle
# ``class="txt"`` exact-substring), which is why a fully-cleared FWN chapter that
# still carried the ambient beacon was misread as a challenge. The stable id-based
# ``#article`` signal (both quote styles) fixes that.
_BODY_CONTAINER_MARKERS = (
    "chaptercontent_content",
    "cha-words",
    "cha-content",
    "m-read",
    'class="txt"',
    'id="article"',
    "id='article'",
    "read-content",
    "chapter-content",
)

# CSS selectors matching those same containers, used for the structural confirm.
_BODY_CONTAINER_SELECTOR = (
    "[class*='ChapterContent_content'], "
    "[class*='ChapterContent_container'], "
    ".cha-words, .cha-content, .m-read, .txt, "
    "#article, #chapter-content, .chapter-content, "
    ".read-content, .novel-content, .chapter-body, .entry-content"
)

# A matched container must hold at least this many non-space characters of text to
# count as a real (populated) body. Presence alone is not enough — an empty
# ``<div id="article"></div>`` shell on a challenge template must NOT clear an
# ambient beacon. A real chapter body holds thousands of characters, so this is a
# generous empty-shell guard, not a content-length heuristic.
_MIN_BODY_TEXT_CHARS = 20


def _has_next_data_payload(html: str, h: str) -> bool:
    """True when a parseable, non-empty ``__NEXT_DATA__`` JSON blob is present."""
    if "__next_data__" not in h:
        return False
    try:
        soup = BeautifulSoup(html, "html.parser")
        nd = soup.find("script", id="__NEXT_DATA__")
    except Exception:
        return False
    if not nd or not nd.string:
        return False
    try:
        return bool(json.loads(nd.string))
    except Exception:
        return False


def _has_g_data_chapter_payload(html: str, h: str) -> bool:
    """True when ``g_data.chapInfo`` parses to an object with non-empty contents."""
    if "g_data.chapinfo" not in h:
        return False
    # Lazy import: reuse the adapter's tolerant JS-assignment parser so "valid"
    # here means exactly what the adapter accepts. Lazy to avoid any import cycle.
    try:
        from .adapters.webnovel_dynamic import parse_g_data_chapter
    except Exception:
        return False
    try:
        ci = parse_g_data_chapter(html)
    except Exception:
        return False
    if not ci:
        return False
    return bool(ci.get("contents"))


def _has_body_container(html: str, h: str) -> bool:
    """True when a real, populated chapter-body container exists in the DOM.

    Presence alone is not enough: at least one matched container must carry
    non-trivial text, so an empty ``<div id="article"></div>`` shell on a
    challenge template is never mistaken for cleared chapter content.
    """
    if not any(m in h for m in _BODY_CONTAINER_MARKERS):
        return False
    try:
        soup = BeautifulSoup(html, "html.parser")
        nodes = soup.select(_BODY_CONTAINER_SELECTOR)
    except Exception:
        return False
    return any(
        len(node.get_text(" ", strip=True)) >= _MIN_BODY_TEXT_CHARS
        for node in nodes
    )


def has_real_payload(html: str) -> bool:
    """True when the page carries real structural content (not a bare beacon).

    Structural evidence only — never page length. Any one of: a valid
    ``__NEXT_DATA__`` blob, a ``g_data.chapInfo`` with non-empty contents, or a
    real chapter-body container element.
    """
    if not html:
        return False
    h = html.lower()
    return (
        _has_next_data_payload(html, h)
        or _has_g_data_chapter_payload(html, h)
        or _has_body_container(html, h)
    )


def is_cloudflare_challenge(html: str) -> bool:
    """True only when the HTML is an *active* Cloudflare challenge/interstitial.

    Strong interstitial markers flag immediately. Ambient beacon markers flag
    only when the page carries no real structural payload, so a successfully
    fetched chapter page (which still carries the beacon) is never misread as a
    challenge.
    """
    if not html:
        return False
    h = html.lower()
    if any(m in h for m in STRONG_CF_MARKERS):
        return True
    if any(m in h for m in AMBIENT_CF_MARKERS):
        return not has_real_payload(html)
    return False
