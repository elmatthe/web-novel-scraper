"""Shared Cloudflare challenge detector for the fetch layer (Phase 9 fix).

One implementation, imported by both :mod:`request_manager` and :mod:`cf_bypass`
so the two can never drift. It distinguishes an *active* interstitial from a
fully-cleared, content-bearing page that merely still carries Cloudflare's
ambient beacon script (the long-standing false-flag that made camoufox "still
see a Cloudflare challenge" on a real WebNovel chapter page).

Detection rules:
  * Real structural payload wins. A page that carries a populated chapter body
    (or a parseable ``__NEXT_DATA__`` / ``g_data.chapInfo`` blob) is a
    successfully fetched page, not a challenge — so it clears regardless of which
    Cloudflare marker strings are also present. This is checked FIRST.
  * Strong interstitial markers (``cf-browser-verification``, ``cf_chl_opt`` …)
    flag a page that has no real payload. NOTE: one historical "strong" marker,
    the phrase ``just a moment`` (Cloudflare's interstitial page title), is an
    ordinary English phrase that legitimately occurs in chapter prose — e.g.
    "…ending its life just a moment before…". A live FreeWebNovel chapter-102
    fetch was discarded for exactly this reason: the cleared page rendered the
    real chapter, but the prose contained "just a moment" and the old
    flag-immediately rule short-circuited to "challenge". Gating every marker on
    the absence of real payload (below) is what fixes that without weakening
    detection of a genuine interstitial (which never carries a populated body).
  * Ambient markers (``/cdn-cgi/challenge-platform/``, ``cf-mitigated``,
    ``managed-challenge``) are injected on every protected response, cleared or
    not, so on their own they prove nothing — same payload gate applies.
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

# Strong interstitial markers. These now only flag a page that carries NO real
# structural payload (see ``is_cloudflare_challenge``), so even the loose
# ``just a moment`` phrase — which is the genuine interstitial's page title but
# also ordinary chapter prose ("…just a moment before…") — can no longer misflag a
# cleared chapter whose real body rendered. On a genuine interstitial (no populated
# body) the payload gate is open and these still flag immediately.
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

    Real structural payload is checked FIRST: a page that carries a populated
    chapter-body container (or a parseable ``__NEXT_DATA__`` / ``g_data.chapInfo``
    blob) is a successfully fetched page, so it is never a challenge — regardless
    of which Cloudflare marker strings (strong OR ambient) are also present. This
    is what lets a cleared FreeWebNovel chapter through even though (a) it still
    carries the ambient ``/cdn-cgi/challenge-platform/`` beacon and (b) its real
    prose may contain the phrase "just a moment". Only when there is NO real
    payload do the strong/ambient markers flag — exactly the genuine-interstitial
    case (a CF challenge page has no populated chapter body), so this cannot misread
    a real challenge as cleared.
    """
    if not html:
        return False
    # Payload gate: real content present → cleared, no matter what markers say.
    if has_real_payload(html):
        return False
    h = html.lower()
    if any(m in h for m in STRONG_CF_MARKERS):
        return True
    if any(m in h for m in AMBIENT_CF_MARKERS):
        return True
    return False
