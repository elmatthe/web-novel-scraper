"""0.1.3 regression: the live FreeWebNovel camoufox detection/timing bug.

A live chapter-102 test proved (on screen) that headful camoufox DOES clear
FreeWebNovel's Cloudflare challenge — the real chapter rendered in the window —
yet the scraper logged "Cloudflare challenge still present after camoufox fetch"
on every attempt and discarded the chapter.

Root cause: the shared ``has_real_payload`` structural check only knew WebNovel's
containers plus two *incidental* FWN wrapper classes (``.m-read`` / the brittle
``class="txt"`` exact-substring). It did NOT know FreeWebNovel's actual primary
content container, ``<div id="article">``. So a fully-cleared FWN chapter page —
which still carries Cloudflare's ambient ``/cdn-cgi/challenge-platform/`` beacon —
was scored as "no real payload", the ambient beacon then tripped
``is_cloudflare_challenge`` → True, and the successfully-fetched chapter was thrown
away. This is the exact class of false-flag fixed for WebNovel in Phase 9D, now
fixed for the FWN browser path.

All offline/deterministic — no real browser launch, no network, no real sleeps.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from webnovel_scraper import cf_bypass
from webnovel_scraper import request_manager as rm
from webnovel_scraper.adapters.freewebnovel import FreeWebNovelAdapter
from webnovel_scraper.cloudflare_detection import (
    STRONG_CF_MARKERS,
    has_real_payload,
    is_cloudflare_challenge,
)
from webnovel_scraper.models import ChapterMeta
from webnovel_scraper.request_manager import RequestManager

FIXTURES = Path(__file__).resolve().parents[2] / "files" / "test-files"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")

# Cloudflare's ambient beacon — injected on EVERY protected response, cleared or
# not. On its own it proves nothing; it only flags a page that has no real payload.
AMBIENT_BEACON = (
    '<script src="/cdn-cgi/challenge-platform/h/g/orchestrate/jsch/v1"></script>'
)

# A fully-cleared FreeWebNovel chapter page: the real ``<div id="article">`` body
# is present AND the ambient beacon is still injected — the exact shape of the
# page camoufox cleared on screen during the live chapter-102 test.
CLEARED_FWN = (
    "<!DOCTYPE html><html><head><title>Chapter 102: Stone Saint</title>"
    f"{AMBIENT_BEACON}</head><body>"
    "<div class='m-read'><div id='article' class='txt' "
    "style='font-size:18px;line-height:1.6;'>"
    "<p>Chapter 102: Stone Saint</p>"
    "<p>The stone saint stood at the heart of the ruined hall, and the world "
    "held its breath as the ancient seal finally cracked open.</p>"
    "<p>A second long paragraph of real chapter prose continues here, well past "
    "the empty-shell text guard, proving this is populated content.</p>"
    "</div></div></body></html>"
)

# A post-clearance transitional page: the interstitial is gone and the body
# container exists but is still EMPTY (chapter DOM not yet rendered). Reading here
# would capture a chapter the browser is about to show — the premature read.
TRANSITIONAL_FWN = (
    "<!DOCTYPE html><html><head><title>FreeWebNovel</title>"
    f"{AMBIENT_BEACON}</head><body>"
    "<div class='m-read'><div id='article' class='txt'></div></div>"
    "</body></html>"
)

# A genuine interstitial: a strong marker + an empty body + the ambient beacon.
GENUINE_CHALLENGE = (
    "<!DOCTYPE html><html><head><title>Just a moment...</title>"
    f"{AMBIENT_BEACON}</head><body>"
    "<div class='cf-browser-verification'>cf_chl_opt</div>"
    "</body></html>"
)


# ── 1. The exact live bug: cleared FWN page + ambient beacon is NOT a challenge ──
def test_cleared_fwn_chapter_with_ambient_beacon_is_not_a_challenge() -> None:
    assert "challenge-platform" in CLEARED_FWN.lower()  # ambient beacon IS present
    assert has_real_payload(CLEARED_FWN) is True         # #article body recognized
    assert is_cloudflare_challenge(CLEARED_FWN) is False
    # Both re-export sites must agree (they share one detector — never drift).
    assert rm.is_cloudflare_challenge(CLEARED_FWN) is False
    assert cf_bypass.is_cloudflare_challenge(CLEARED_FWN) is False


def test_cleared_fwn_chapter_fetch_succeeds_without_escalation(
    tmp_path, monkeypatch
) -> None:
    """The browser-primary fetch returns the cleared chapter on the FIRST camoufox
    attempt — no escalation, no retry — now that the page is no longer misread."""

    class _CM:
        def __exit__(self, *exc) -> None:
            return None

    monkeypatch.setattr(
        cf_bypass, "create_camoufox_browser", lambda *, headless, **_k: (_CM(), object())
    )
    # camoufox returns the cleared page (ambient beacon + real #article body).
    monkeypatch.setattr(cf_bypass, "fetch_camoufox", lambda page, url, **_k: CLEARED_FWN)

    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path, sleep_fn=lambda _s: None)
    strategies: list[str] = []
    orig = mgr._fetch_uncached_strategy
    monkeypatch.setattr(
        mgr,
        "_fetch_uncached_strategy",
        lambda url, strategy: strategies.append(strategy) or orig(url, strategy),
    )

    html = mgr.fetch(
        "https://freewebnovel.com/novel/x/chapter-102", use_browser=True
    )
    assert html == CLEARED_FWN
    # Cleared on attempt 1 via camoufox — the chapter WRITES, no escalation.
    assert strategies == [rm.FETCH_STRATEGY_CAMOUFOX]


# ── 2. The camoufox fetch WAITS for content before capturing ─────────────────
class _FakePage:
    """A mock camoufox page whose ``content()`` returns a scripted sequence — so a
    test can simulate the real chapter DOM appearing only after several polls."""

    def __init__(self, pages: list[str]) -> None:
        self._pages = list(pages)
        self.content_calls = 0
        self.waits = 0

    def goto(self, url, **_k):  # noqa: ANN001
        class _Resp:
            status = 200

        return _Resp()

    def wait_for_timeout(self, _ms) -> None:  # noqa: ANN001
        self.waits += 1

    def content(self) -> str:
        idx = min(self.content_calls, len(self._pages) - 1)
        self.content_calls += 1
        return self._pages[idx]


def test_fetch_camoufox_waits_for_content_then_captures_it() -> None:
    """The real ``fetch_camoufox`` must POLL through the post-clearance transitional
    window (ambient beacon, empty body) and capture only once the real chapter DOM
    is present — never the early empty page."""
    page = _FakePage(
        [TRANSITIONAL_FWN, TRANSITIONAL_FWN, TRANSITIONAL_FWN, CLEARED_FWN]
    )
    html = cf_bypass.fetch_camoufox(
        page, "https://freewebnovel.com/novel/x/chapter-102", cf_timeout=30
    )
    # Captured the populated chapter, not a transitional empty-body read.
    assert html == CLEARED_FWN
    assert has_real_payload(html) is True
    # It actually waited (polled) for the body to appear rather than reading early.
    assert page.waits >= 3


def test_fetch_camoufox_returns_promptly_on_non_chapter_origin() -> None:
    """A page that is neither a challenge nor chapter content (e.g. a session
    warm-up origin GET) returns promptly instead of polling the full timeout."""
    plain_origin = "<!DOCTYPE html><html><body><h1>FreeWebNovel</h1></body></html>"
    page = _FakePage([plain_origin])
    html = cf_bypass.fetch_camoufox(
        page, "https://freewebnovel.com/", cf_timeout=30
    )
    assert html == plain_origin
    assert page.waits == 0  # no challenge, no content to wait for → no polling


# ── 3. A genuine challenge is still detected and still escalates ──────────────
def test_genuine_challenge_still_detected_and_escalates(tmp_path, monkeypatch) -> None:
    assert has_real_payload(GENUINE_CHALLENGE) is False
    assert is_cloudflare_challenge(GENUINE_CHALLENGE) is True
    assert rm.is_cloudflare_challenge(GENUINE_CHALLENGE) is True
    assert cf_bypass.is_cloudflare_challenge(GENUINE_CHALLENGE) is True

    # A strategy that returns the challenge body must surface a typed challenge
    # failure (0.2.0 §3.3) so the ladder escalates / the conductor routes it to
    # rescue rather than caching/returning the interstitial.
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)
    monkeypatch.setattr(mgr, "_get_text", lambda session, url: GENUINE_CHALLENGE)
    with pytest.raises(rm.ChallengeFetchError):
        mgr._fetch_uncached_strategy("https://example/ch", rm.FETCH_STRATEGY_HTTP)


def test_empty_article_shell_with_ambient_beacon_is_still_a_challenge() -> None:
    """An empty ``<div id="article"></div>`` shell carrying only the ambient beacon
    must NOT clear — presence of the container is not enough, it must hold real
    text. This is the guard that keeps a transitional/empty page from false-clearing."""
    assert has_real_payload(TRANSITIONAL_FWN) is False
    assert is_cloudflare_challenge(TRANSITIONAL_FWN) is True


# ── 4. The live ch-102 regression: "just a moment" PROSE on a cleared page ───
# Captured from the real headful-camoufox fetch of Shadow Slave chapter 102
# (files/diag_ch102.py). The page fully cleared — real <div id="article"> body
# rendered — but the chapter prose contains the sentence "…ending its life just a
# moment before breaking apart…". "just a moment" is Cloudflare's interstitial
# page TITLE and was a STRONG marker that flagged immediately, so the old detector
# discarded this cleared chapter even though the WebNovel/#article payload fix
# (aedff7f) was already in place — that fix only gated the AMBIENT-marker branch,
# never the strong-marker path. The fixture is the sanitized real page (CF ambient
# beacon + the populated #article body, ad-script noise removed).
FWN_102_CLEARED_FIXTURE = "fwn_chapter_102_cleared.html"


def test_cleared_fwn_102_with_just_a_moment_in_prose_is_not_a_challenge() -> None:
    html = _fixture(FWN_102_CLEARED_FIXTURE)
    lower = html.lower()
    # The exact collision: a STRONG marker phrase occurs in the real body prose…
    assert "just a moment" in STRONG_CF_MARKERS
    assert "just a moment" in lower
    # …and Cloudflare's ambient beacon is still on the cleared page…
    assert "challenge-platform" in lower
    # …yet the real chapter body is present, so the page is CLEARED, not a challenge.
    assert has_real_payload(html) is True
    assert is_cloudflare_challenge(html) is False
    # Both re-export sites share the one detector and must agree.
    assert rm.is_cloudflare_challenge(html) is False
    assert cf_bypass.is_cloudflare_challenge(html) is False


def test_cleared_fwn_102_adapter_extracts_non_empty_body() -> None:
    """The FWN adapter pulls a full body out of the same cleared page — proving the
    only failure was detection, not extraction."""
    html = _fixture(FWN_102_CLEARED_FIXTURE)
    adapter = FreeWebNovelAdapter(log=lambda _m: None)
    meta = ChapterMeta(index=102, url="https://freewebnovel.com/novel/shadow-slave/chapter-102")
    content = adapter._extract_chapter(html, meta)
    assert content.paragraphs  # non-empty body
    assert len(content.paragraphs) >= 10
    assert content.title == "Stone Saint"


def test_cleared_fwn_102_fetch_succeeds_on_first_camoufox_attempt(
    tmp_path, monkeypatch
) -> None:
    """End-to-end through the browser-primary ladder: the cleared ch-102 page is
    accepted on the FIRST camoufox attempt — no escalation to stealth-Chromium."""
    html_fixture = _fixture(FWN_102_CLEARED_FIXTURE)

    class _CM:
        def __exit__(self, *exc) -> None:
            return None

    monkeypatch.setattr(
        cf_bypass, "create_camoufox_browser", lambda *, headless, **_k: (_CM(), object())
    )
    monkeypatch.setattr(cf_bypass, "fetch_camoufox", lambda page, url, **_k: html_fixture)

    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path, sleep_fn=lambda _s: None)
    strategies: list[str] = []
    orig = mgr._fetch_uncached_strategy
    monkeypatch.setattr(
        mgr,
        "_fetch_uncached_strategy",
        lambda url, strategy: strategies.append(strategy) or orig(url, strategy),
    )
    out = mgr.fetch("https://freewebnovel.com/novel/shadow-slave/chapter-102", use_browser=True)
    assert out == html_fixture
    assert strategies == [rm.FETCH_STRATEGY_CAMOUFOX]
