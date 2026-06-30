"""Unified fetch layer for webnovel_scraper.

Consolidates the HTTP path (ported from ``scrape_noble_queen-v3.py``) and the
Cloudflare browser path (ported from ``freewebnovel-webscraper.py`` +
``cf_bypass.py``) behind a single ``RequestManager``. Fetch only — no parsing,
no PDF logic.

Two fetch entry points share one on-disk cache:
  - ``fetch_html(url)``         — requests.Session, with a cloudscraper fallback
                                  when a Cloudflare challenge page is returned.
  - ``fetch_html_browser(url)`` — Playwright + cf_bypass stealth, browser reused
                                  across calls; call ``close()`` to tear it down.
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence
from urllib.parse import urlsplit, urlunsplit

import requests

from .browser_env import ensure_browsers_path
from .cloudflare_detection import has_real_payload, is_cloudflare_challenge
from .host_rate_limiter import HostRateLimiter

logger = logging.getLogger(__name__)

# Make sure the Chromium / playwright-stealth rungs look in the contained in-repo
# browser cache (files/bin/ms-playwright) the launcher installs into, even when the
# program is started outside the launcher. ``setdefault`` semantics — see
# ``browser_env``.
ensure_browsers_path()

# ── Retry / fetch constants ──────────────────────────────────────────────────
# MAX_RETRIES is retries after the first attempt. With the default of 6, a fetch
# can make up to 7 attempts total — enough to walk every rung of the 6-rung
# escalation ladder at least once (http, cloudscraper, camoufox, camoufox_fresh,
# playwright_stealth, playwright_stealth_fresh), with one extra on the final
# stealth rung. This is the "relentless per-chapter retry" Phase 9 wants for long
# unattended runs: a chapter is only recorded failed after this whole ladder AND
# the pipeline's second-pass sweep (which re-walks the same full ladder) both give
# up. Permanent 403/404 still short-circuit immediately so a genuinely dead
# chapter never hangs the run.
MAX_RETRIES = 6
RETRY_SLEEP = 5.0          # backoff base, seconds
RETRY_BACKOFF_MULTIPLIER = 3.0
MAX_RETRY_SLEEP = 120.0
RETRY_JITTER_RATIO = 0.15
# The legacy inter-attempt backoff is slept in slices no longer than this so a
# Stop request (``cancel_event``) aborts the wait within one slice instead of
# elapsing the full (up to ~138s) backoff — the same cancel-aware discipline as
# ``HostRateLimiter._wait`` (one injected timing source, no monolithic real-clock
# sleep). 0.2.0 live-test BUG-2 fix.
BACKOFF_WAIT_SLICE = 0.25

# Substrings of the Playwright/Camoufox error raised when a page/context/browser
# has been torn down out from under us (the live BUG-1 — an unfocused headful
# camoufox window whose page was closed mid-run). Matched by MESSAGE rather than
# by a specific Playwright error class so the detector does not depend on
# playwright being importable here and survives Playwright renaming its classes.
_PAGE_CLOSED_MARKERS = (
    "has been closed",
    "target closed",
    "target page, context or browser has been closed",
    "browser has been closed",
    "page has been closed",
    "context has been closed",
)


def _is_page_closed_error(exc: BaseException) -> bool:
    """True if ``exc`` looks like a 'target/page/context/browser closed' teardown.

    Detected purely by message substring (never raises, never imports playwright).
    Used to turn a camoufox page that died mid-navigation into a single
    recreate-and-retry-within-the-attempt instead of failing the whole rung
    (0.2.0 live-test BUG-1). A non-closed failure returns False so it escalates
    normally.
    """
    try:
        msg = str(exc).lower()
    except Exception:
        return False
    return any(marker in msg for marker in _PAGE_CLOSED_MARKERS)


def _page_is_alive(page: Any) -> bool:
    """Whether a cached browser ``page`` can still be reused, without throwing.

    Conservative in the SAFE direction: a page is treated as alive UNLESS it can
    be positively proven closed. ``page.is_closed()`` returning truthy, or raising
    (a raising introspection call is itself strong evidence the underlying target
    is gone), means dead. A page object that does not expose ``is_closed`` at all
    (e.g. a test fake, or any object we cannot introspect) is treated as ALIVE so
    the liveness guard NEVER spuriously recreates a healthy page — on a real,
    healthy camoufox/Playwright page ``is_closed()`` exists and returns ``False``,
    so the FWN happy path is reused unchanged. 0.2.0 live-test BUG-1.
    """
    if page is None:
        return False
    is_closed = getattr(page, "is_closed", None)
    if not callable(is_closed):
        return True
    try:
        return not is_closed()
    except Exception:
        return False


# Default per-request HTTP timeout, seconds. 0.2.0 (§3.15): this is now only the
# DEFAULT for the explicit ``RequestManager(http_timeout=…)`` constructor arg — it
# is no longer mutated at runtime. The pre-0.2.0 race (the GUI assigning to this
# module global while another thread read it) is gone: each manager carries its
# own timeouts. Kept as a constant so existing references stay valid.
FETCH_TIMEOUT = 30
# 404/410 are the only genuinely PERMANENT statuses (the chapter does not exist).
# 403 is deliberately NOT here any more: on FreeWebNovel a 403 is a Cloudflare
# block, classified body-first as a ChallengeFetchError so it can be rescued, not
# treated as a permanent skip (§3.3). ``PERMANENT_STATUSES`` is kept as an alias of
# ``NOT_FOUND_STATUSES`` for back-compat callers.
NOT_FOUND_STATUSES = (404, 410)
PERMANENT_STATUSES = NOT_FOUND_STATUSES
RETRY_STATUS_MIN = 500     # >= this is a server error (transient unless a CF body)
RATE_LIMIT_STATUS = 429
BROWSER_NAV_TIMEOUT_MS = 60_000
CF_CLEAR_TIMEOUT = 45.0    # seconds to wait for a Cloudflare challenge to clear

# ── 0.2.0 fast-primary + single-lane rescue constants (plan §3.1) ─────────────
# Defined here next to the existing ladder constants; the run-config ones are
# threaded via ScrapeJob (§3.14). 0.2.0 is strictly single-lane: RESCUE_MAX_WORKERS
# is a HARD cap of 1 (the user-selectable 1–5 toggle is DEFERRED to 0.2.1, §9).
DEFAULT_DELAY = 3.0                    # inter-fetch, fast path (GUI default → 3.0)
HOST_MIN_INTERVAL = 3.0                # global per-host floor (§3.4)
FAST_BROWSER_ATTEMPTS = 2              # persistent-camoufox attempts on the fast path
FAST_HTTP_PROBE_ATTEMPTS = 2           # ONLY when HTTP-first is on; separate budget
FAST_PATH_ATTEMPT_TIMEOUT = 15.0       # seconds; short, no large exponential backoff
RESCUE_WORKERS = 1                     # 0.2.0 is single-lane
RESCUE_MAX_WORKERS = 1                 # HARD cap in 0.2.0
RESCUE_MAX_PENDING = 16                # bounded rescue backlog (§3.8)
RESCUE_MAX_ELAPSED_PER_CHAPTER = 180.0  # monotonic PROCESSING deadline per chapter

FETCH_STRATEGY_HTTP = "http"
FETCH_STRATEGY_CLOUDSCRAPER = "cloudscraper"
FETCH_STRATEGY_CAMOUFOX = "camoufox"
FETCH_STRATEGY_CAMOUFOX_FRESH = "camoufox_fresh"
FETCH_STRATEGY_PLAYWRIGHT_STEALTH = "playwright_stealth"
FETCH_STRATEGY_PLAYWRIGHT_STEALTH_FRESH = "playwright_stealth_fresh"
# Backwards-compatible aliases (the Chromium stealth rungs were historically named
# ``browser`` / ``browser_fresh``). Kept so any external caller/log parser keeps
# resolving; new code uses the playwright_stealth names.
FETCH_STRATEGY_BROWSER = FETCH_STRATEGY_PLAYWRIGHT_STEALTH
FETCH_STRATEGY_BROWSER_FRESH = FETCH_STRATEGY_PLAYWRIGHT_STEALTH_FRESH

# Escalation ladder (live-tuned after the first genuine FreeWebNovel Cloudflare
# challenge, which camoufox alone FAILED to clear on every attempt):
#   http -> cloudscraper -> camoufox -> camoufox_fresh
#        -> playwright_stealth -> playwright_stealth_fresh
# Camoufox (anti-detect Firefox) leads the browser rungs — it clears the common
# managed challenge cheaply — but is no longer assumed sufficient: the Chromium
# playwright-stealth rungs are the LAST-RESORT rescue after camoufox is exhausted,
# since a real FWN challenge has been observed to defeat camoufox where Chromium
# stealth may still clear.
#
# Only ONE browser engine can be live per thread: camoufox runs its own internal
# sync-Playwright and our Chromium stealth path starts another sync-Playwright;
# two of them on the same thread raise "Sync API inside the asyncio loop". The
# fetch methods therefore tear the *other* engine down before starting one
# (see ``_fetch_camoufox_once`` / ``_fetch_browser_once``), so the ladder can walk
# from camoufox into the stealth rungs within a single chapter's attempts. A
# missing camoufox/stealth install simply raises and the ladder escalates on.
DEFAULT_ESCALATION_LADDER = (
    FETCH_STRATEGY_HTTP,
    FETCH_STRATEGY_CLOUDSCRAPER,
    FETCH_STRATEGY_CAMOUFOX,
    FETCH_STRATEGY_CAMOUFOX_FRESH,
    FETCH_STRATEGY_PLAYWRIGHT_STEALTH,
    FETCH_STRATEGY_PLAYWRIGHT_STEALTH_FRESH,
)
BROWSER_ESCALATION_LADDER = (
    FETCH_STRATEGY_CAMOUFOX,
    FETCH_STRATEGY_CAMOUFOX_FRESH,
    FETCH_STRATEGY_PLAYWRIGHT_STEALTH,
    FETCH_STRATEGY_PLAYWRIGHT_STEALTH_FRESH,
)

# ── Bounded TWO-engine headful FWN browser ladder (FreeWebNovel default) ──────
# 0.1.3, matched to the legacy scraper that downloaded whole novels without
# tripping Cloudflare. A FreeWebNovel chapter is fetched through a persistent,
# VISIBLE (headless=False) browser from request #1 — no HTTP-first, no six-engine
# relaunch storm. The bounded per-chapter ladder is:
#   camoufox -> camoufox -> camoufox_fresh -> playwright_stealth
# i.e. a couple of retries on the SAME warmed camoufox page, then ONE fresh-camoufox
# recovery, then ONE escalation to **headful stealth-Chromium** — the exact engine
# the legacy scraper used in its default VISIBLE config (camoufox was its
# headless-only path), so it is the engine historically PROVEN to clear FWN's
# Cloudflare. Both engines run headful (``headless=False``). The retry budget is
# CAPPED to the ladder length in ``fetch`` so a blocked chapter walks it exactly
# once (≤1 camoufox recreation + ≤1 stealth-Chromium escalation), regardless of the
# job's generous ``max_retries``. No ``playwright_stealth_fresh`` / cloudscraper /
# http rungs are on this default browser-primary path.
HEADFUL_PRIMARY_LADDER = (
    FETCH_STRATEGY_CAMOUFOX,
    FETCH_STRATEGY_CAMOUFOX,
    FETCH_STRATEGY_CAMOUFOX_FRESH,
    FETCH_STRATEGY_PLAYWRIGHT_STEALTH,
)
# When the user opts into "try fast HTTP first" ON the browser-primary path, two
# cheap HTTP rungs are tried before the same bounded camoufox -> stealth path.
HTTP_FIRST_PRIMARY_LADDER = (
    FETCH_STRATEGY_HTTP,
    FETCH_STRATEGY_CLOUDSCRAPER,
    FETCH_STRATEGY_CAMOUFOX,
    FETCH_STRATEGY_CAMOUFOX_FRESH,
    FETCH_STRATEGY_PLAYWRIGHT_STEALTH,
)
# After camoufox has proven unable to clear the challenge once in a run, later
# chapters (and end-of-run sweep retries) go STRAIGHT to the persistent headful
# stealth-Chromium engine — reusing the one browser, never relaunching it. This is
# required for true reuse: camoufox and stealth-Chromium each run their own
# sync-Playwright loop and cannot coexist on one thread, so replaying the camoufox
# rungs between two stealth chapters would force a Chromium teardown/relaunch each
# time. Latching off camoufox after the first fallback keeps the stealth browser
# alive across every later fallback chapter. Two same-page attempts, no relaunch.
STEALTH_LATCHED_LADDER = (
    FETCH_STRATEGY_PLAYWRIGHT_STEALTH,
    FETCH_STRATEGY_PLAYWRIGHT_STEALTH,
)
# The stealth-Chromium strategies (used to set the run latch when either is reached
# on the browser-primary path).
_STEALTH_STRATEGIES = (
    FETCH_STRATEGY_PLAYWRIGHT_STEALTH,
    FETCH_STRATEGY_PLAYWRIGHT_STEALTH_FRESH,
)

# ── 0.2.0 FAST-PRIMARY ladders (§3.2 / §3.2a) ─────────────────────────────────
# The fast path is what the conductor (Phase 3) runs as the primary: it makes a
# *bounded* number of persistent-camoufox attempts and then raises a typed signal
# (ChallengeFetchError) so the hard chapter is handed to the rescue lane while the
# primary moves on. Crucially it carries NO stealth-Chromium rung — escalation to
# stronger engines is the rescue worker's job, so the primary stays fast.
#
#   HTTP-first OFF (default):  camoufox(reused) ×FAST_BROWSER_ATTEMPTS
#   HTTP-first ON:             http ×1, cloudscraper ×1   (FAST_HTTP_PROBE_ATTEMPTS)
#                              THEN camoufox(reused) ×FAST_BROWSER_ATTEMPTS
#
# The optional HTTP probes are EXTRA, never a substitute for the browser budget —
# so HTTP-first can't burn both attempts on HTTP and never reach the browser, the
# disaster §3.2a warns about on FWN.
FAST_BROWSER_LADDER = (FETCH_STRATEGY_CAMOUFOX,) * FAST_BROWSER_ATTEMPTS
FAST_HTTP_FIRST_LADDER = (
    (FETCH_STRATEGY_HTTP, FETCH_STRATEGY_CLOUDSCRAPER)[:FAST_HTTP_PROBE_ATTEMPTS]
    + FAST_BROWSER_LADDER
)

# Repo-root-relative cache root: files/cache/ (request_manager is at
# scripts/Universal/webnovel_scraper/, so parents[3] is the repo root).
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CACHE_ROOT = _REPO_ROOT / "files" / "cache"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Realistic Chrome headers. These are the *static* defaults; the request-specific
# ``Referer`` and ``Sec-Fetch-Site`` are set per request by ``_http_get`` (see the
# warm-up / same-origin chaining there) because the right value depends on whether
# the request is the first landing on a host or a follow-on navigation within it.
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    # Do NOT advertise Brotli ("br"). ``requests``/``urllib3`` only decode it when
    # the optional ``brotli`` package is installed; without it a brotli-encoded
    # response comes back as raw compressed bytes that ``r.text`` mis-decodes into
    # U+FFFD-replacement-char garbage (the Shadow Slave "chapter 3+ extract zero
    # paragraphs" bug). gzip and deflate are always decodable, so request only
    # those — the page content is identical, just gzip-compressed instead.
    "Accept-Encoding": "gzip, deflate",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    # Default for the very first (warm-up / address-bar) navigation; switched to
    # "same-origin" per request once the host has been warmed.
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    # NOTE: no static "Referer". The old verbatim port hardcoded
    # ``Referer: https://www.webnovel.com/`` on EVERY request, including
    # FreeWebNovel ones — a cross-site referer that does not match the host being
    # fetched is a bot-tell. The correct, host-derived Referer is set per request
    # in ``_http_get``.
}


class FetchError(RuntimeError):
    """A fetch failed permanently (all retries exhausted, or a permanent status).

    The pipeline records this as a failed chapter and skips it; it is never fatal.
    The 0.2.0 typed subclasses below let the conductor route a failure correctly
    (rescue vs terminal) while every existing ``except FetchError`` caller keeps
    working unchanged (§3.3).
    """


class NotFoundFetchError(FetchError):
    """404/410 — the chapter does not exist. Terminal: NEVER rescued."""

    def __init__(self, message: str, *, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class ChallengeFetchError(FetchError):
    """A Cloudflare interstitial / block. Eligible for the rescue lane."""

    def __init__(self, message: str, *, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class TransientFetchError(FetchError):
    """A timeout / 5xx / reset (no CF body). Rescued with backoff; NOT a block."""

    def __init__(self, message: str, *, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class RateLimitedFetchError(FetchError):
    """A 429. Transient + raises global host pacing; honors a valid Retry-After.

    Distinct from a Cloudflare block: it does NOT count toward the breaker and is
    NOT submitted to browser rescue (§3.9) — the shared limiter parks the host.
    """

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class _PermanentStatus(Exception):
    """Deprecated internal marker (pre-0.2.0). Retained for any external import;
    the body-first classifier now raises the typed :class:`FetchError` subclasses
    above instead of this."""


class _RetryableFetch(Exception):
    """Deprecated internal marker (pre-0.2.0). Retained for any external import;
    a retryable challenge/transient is now a typed :class:`ChallengeFetchError` /
    :class:`TransientFetchError`, which the ladder still retries (they are
    ``FetchError`` subclasses caught by the generic retry branch)."""


class ScrapeCancelled(Exception):
    """Raised when a fetch is requested after ``cancel_event`` has been set.

    The GUI Stop button sets the manager's ``cancel_event``; the pipeline lets
    this propagate to end the run cleanly between (or at the start of) fetches.
    """


@dataclass
class FetchInfo:
    """Cache-vs-network metadata for the most recent ``fetch`` (§3.8/§3.9).

    The pipeline-owned circuit breaker (Phase 3) consumes this to count ONLY
    uncached primary *network* challenges — cache hits and resume-skips are not in
    the breaker's denominator. ``classification`` is one of: ``"cache"``,
    ``"success"``, ``"challenge"``, ``"transient"``, ``"not_found"``,
    ``"rate_limited"``.
    """

    from_cache: bool
    classification: str
    status: Optional[int] = None
    strategy: Optional[str] = None


# The Cloudflare challenge detector now lives in ``cloudflare_detection`` and is
# imported above so request_manager and cf_bypass share one implementation that
# cannot drift. ``is_cloudflare_challenge`` is re-exported here unchanged so
# existing callers/tests that use ``request_manager.is_cloudflare_challenge`` keep
# working.


# A body dominated by U+FFFD replacement characters was served in a
# content-encoding the HTTP client could not decode (e.g. brotli without the
# brotli package). It is not real HTML — never cache it or hand it to a parser.
_REPLACEMENT_CHAR = "�"
_GARBLED_ABS_FLOOR = 32        # ignore the odd stray replacement char on tiny pages
_GARBLED_RATIO = 0.02         # >2% replacement chars => mis-decoded binary, not HTML


def _looks_garbled(html: str) -> bool:
    """True when ``html`` is mis-decoded binary (an undecodable content-encoding).

    Real HTML effectively never contains U+FFFD replacement characters; a
    brotli/compressed body that ``requests`` could not decode is ~40% of them.
    Used as a guard so an undecodable response is treated as a retryable fetch
    failure (escalate the ladder) instead of being cached and silently failing
    body extraction downstream.
    """
    if not html:
        return False
    n = len(html)
    if n < 200:
        return False
    bad = html.count(_REPLACEMENT_CHAR)
    return bad >= _GARBLED_ABS_FLOOR and (bad / n) > _GARBLED_RATIO


def cache_key_for(url: str) -> str:
    """Deterministic cache filename for a URL (same URL -> same name)."""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return f"{digest}.html"


def compute_backoff_delay(
    retry_number: int,
    *,
    base_delay: float = RETRY_SLEEP,
    multiplier: float = RETRY_BACKOFF_MULTIPLIER,
    max_delay: float = MAX_RETRY_SLEEP,
    jitter_ratio: float = RETRY_JITTER_RATIO,
    random_fn: Callable[[], float] = random.random,
) -> float:
    """Return the sleep before retry ``retry_number`` (1-based).

    With default jitter disabled this yields 5, 15, 45, 120 for retry numbers
    1..4. Jitter is multiplicative and bounded so tests can set it to 0.
    """
    retry_number = max(1, int(retry_number))
    raw = min(float(max_delay), float(base_delay) * (float(multiplier) ** (retry_number - 1)))
    if jitter_ratio <= 0:
        return raw
    jitter = (random_fn() * 2.0 - 1.0) * float(jitter_ratio)
    return max(0.0, raw * (1.0 + jitter))


def _strategy_for_attempt(ladder: Sequence[str], attempt: int) -> str:
    """Choose the escalation strategy for a 1-based attempt number."""
    if not ladder:
        return FETCH_STRATEGY_HTTP
    return ladder[min(max(1, attempt) - 1, len(ladder) - 1)]


def _looks_like_not_found(exc: Exception) -> bool:
    """True when a browser/navigation error message reports a 404/410.

    The browser engines (cf_bypass) surface a missing chapter as
    ``RuntimeError("HTTP 404 …")``; this maps that to a terminal NotFound. NOTE:
    403 is deliberately excluded — a browser-layer 403 is handled body-first (poll
    for clearance, then classify as a challenge), never as a permanent skip (§3.3).
    """
    msg = str(exc)
    for status in NOT_FOUND_STATUSES:
        if re.search(rf"\bHTTP\s+{status}\b", msg, re.IGNORECASE):
            return True
    return False


# Back-compat alias (the helper was renamed when 403 stopped being "permanent").
_looks_like_permanent_status = _looks_like_not_found


# Markers in an exception message that mean a browser ENGINE is not installed /
# cannot launch — a structural failure that retrying (with or without a long
# backoff) can never fix. Caught so the ladder advances to the next rung
# IMMEDIATELY rather than sleeping 100+ seconds before re-attempting a launch that
# is doomed (the live "retrying in 102.7s with playwright_stealth_fresh" freeze).
_BROWSER_LAUNCH_FAILURE_MARKERS = (
    "executable doesn't exist",
    "executable doesnt exist",
    "playwright install",
    "please run the following command",
    "looks like playwright",
    "browsertype.launch",
    "host system is missing dependencies",
    "no module named 'camoufox'",
    'no module named "camoufox"',
    "no module named 'playwright'",
    'no module named "playwright"',
    "no module named 'playwright_stealth'",
    'no module named "playwright_stealth"',
)


def _looks_like_browser_launch_failure(exc: Exception) -> bool:
    """True when ``exc`` means a browser engine is missing or cannot launch.

    A missing engine (no Chromium download, camoufox not fetched, the stealth/
    playwright package absent) is NOT a transient network blip: retrying the same
    rung can never succeed. Classifying it lets the ladder skip the rung
    immediately, with no backoff sleep, so a fresh install that only ran setup
    never hangs the run.
    """
    # A missing Python package (cloudscraper, camoufox, playwright, the stealth
    # helper) can never be fixed by retrying — treat any import error as a
    # launch/engine failure so the ladder advances without backoff.
    if isinstance(exc, (ImportError, FileNotFoundError)):
        return True
    msg = str(exc).lower()
    return any(marker in msg for marker in _BROWSER_LAUNCH_FAILURE_MARKERS)


def _origin_of(url: str) -> Optional[str]:
    """Return ``scheme://netloc/`` for ``url``, or None if it has no host."""
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return urlunsplit((parts.scheme, parts.netloc, "/", "", ""))


def _parse_retry_after(response: Any) -> Optional[float]:
    """Best-effort ``Retry-After`` (delta-seconds) from a response, else None.

    Only the integer delta-seconds form is honored (the common case for a 429);
    an HTTP-date form or anything unparseable returns None, so the limiter falls
    back to its default cooldown rather than trusting a value it can't read.
    """
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    try:
        raw = headers.get("Retry-After")
    except Exception:
        return None
    if raw is None:
        return None
    try:
        seconds = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def _classification_for(exc: Optional[BaseException]) -> str:
    """Map a terminal/last fetch exception to a ``FetchInfo.classification`` tag.

    Only ``"challenge"`` feeds the pipeline breaker (§3.9); a generic/unknown last
    failure is treated as ``"transient"`` so it never trips the headless breaker.
    """
    if isinstance(exc, ChallengeFetchError):
        return "challenge"
    if isinstance(exc, RateLimitedFetchError):
        return "rate_limited"
    if isinstance(exc, NotFoundFetchError):
        return "not_found"
    return "transient"


class RequestManager:
    """Fetches HTML over HTTP or a stealth browser, with a shared on-disk cache.

    Args:
        slug: drives the cache subdirectory (``files/cache/{slug}/``).
        use_cache: read/write the on-disk HTML cache.
        headless: run the browser headless. Defaults to **False** (visible) in
            0.1.3 — the FreeWebNovel primary path is a persistent VISIBLE camoufox
            browser (the legacy-matched config Cloudflare clears for); an advanced
            user can force headless from the GUI.
        try_http_first: on the browser-primary path, try two cheap HTTP rungs
            before falling back to camoufox. Default False (opt-in) — plain HTTP
            trips FreeWebNovel's Cloudflare, which is why the browser is primary.
        cache_root: override the cache root (defaults to ``files/cache/``);
            useful for tests so they never touch the repo cache.
        log_fn: optional logging callback ``(str) -> None`` (defaults to logger).
    """

    def __init__(
        self,
        slug: str,
        use_cache: bool = True,
        headless: bool = False,
        *,
        try_http_first: bool = False,
        cache_root: Optional[Path] = None,
        log_fn: Optional[Callable[[str], None]] = None,
        max_retries: int = MAX_RETRIES,
        retry_base_delay: float = RETRY_SLEEP,
        retry_jitter_ratio: float = RETRY_JITTER_RATIO,
        sleep_fn: Callable[[float], None] = time.sleep,
        random_fn: Callable[[], float] = random.random,
        http_timeout: float = FETCH_TIMEOUT,
        browser_nav_timeout: float = BROWSER_NAV_TIMEOUT_MS / 1000.0,
        cloudflare_timeout: float = CF_CLEAR_TIMEOUT,
        host_limiter: Optional[HostRateLimiter] = None,
    ) -> None:
        self.slug = slug
        self.use_cache = use_cache
        self.headless = headless
        self.try_http_first = try_http_first
        self.cache_dir = (cache_root or DEFAULT_CACHE_ROOT) / slug
        self._log: Callable[[str], None] = log_fn or logger.info
        self.max_retries = max(0, int(max_retries))
        self.retry_base_delay = float(retry_base_delay)
        self.retry_jitter_ratio = float(retry_jitter_ratio)
        self._sleep = sleep_fn
        self._random = random_fn

        # Explicit per-manager timeouts (§3.15) — the source of truth, replacing the
        # mutated module-level FETCH_TIMEOUT global. ``http_timeout`` is the GUI's
        # "Timeout" field (ordinary HTTP/nav timeout); the 180s rescue deadline is a
        # SEPARATE internal ceiling threaded per-attempt by the rescue worker.
        self._http_timeout = float(http_timeout)
        self._browser_nav_timeout_ms = int(float(browser_nav_timeout) * 1000)
        self._cloudflare_timeout = float(cloudflare_timeout)

        # Shared, fair, per-host navigation limiter (§3.4). Constructed once per run
        # and injected into BOTH the primary pipeline manager and the rescue
        # worker's manager (never per-worker). ``None`` => no pacing (legacy / tests).
        self.host_limiter = host_limiter

        # Cache-vs-network metadata for the most recent fetch (§3.8). The breaker
        # reads this to count only uncached primary network challenges.
        self.last_fetch_info: Optional[FetchInfo] = None

        # The GUI Stop button sets this; ``fetch`` checks it and raises
        # ScrapeCancelled so the pipeline can end a run cleanly.
        self.cancel_event = threading.Event()

        self._session: Optional[requests.Session] = None
        self._scraper: Any = None  # cloudscraper session, lazily created

        # Browser path state (lazily started, reused across calls).
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

        # Camoufox (anti-detect Firefox) path state. ``_cf_cm`` is the Camoufox
        # context manager (kept alive for the browser's lifetime); ``_cf_page`` is
        # the reused page. Lazily started on first camoufox attempt.
        self._cf_cm: Any = None
        self._cf_page: Any = None
        # Hosts whose camoufox browser SESSION has been warmed (an origin GET that
        # lets Cloudflare seat a cf_clearance cookie in the browser context). One
        # warm-up per host per browser; cleared when the browser is recreated.
        self._cf_warmed_hosts: set = set()
        # Run latch: set True once camoufox has been exhausted on the browser-primary
        # path (a chapter reached the stealth-Chromium fallback rung). Subsequent
        # browser-primary fetches then go straight to the persistent stealth-Chromium
        # engine (STEALTH_LATCHED_LADDER) instead of replaying camoufox — so the one
        # stealth browser is reused, never relaunched per chapter.
        self._camoufox_exhausted = False

    # ── lifecycle + unified facade ───────────────────────────────────────────
    def start(self) -> "RequestManager":
        """Placeholder for future session warm-up; returns self for chaining.

        No-op today — the HTTP session and browser are both started lazily on
        first use. Kept so the pipeline can call ``start()`` symmetrically with
        ``close()`` and so a warm-up can be added later without an API change.
        """
        return self

    def fetch(
        self,
        url: str,
        *,
        use_browser: bool = False,
        use_cache: Optional[bool] = None,
        max_retries: Optional[int] = None,
        retry_base_delay: Optional[float] = None,
        fast_path: bool = False,
    ) -> str:
        """Unified fetch facade used by adapters and the pipeline.

        ``use_browser=True`` (the FreeWebNovel default in 0.1.3) is the
        **headful-camoufox primary** path: the chapter is fetched through one
        persistent VISIBLE camoufox browser, retried a couple of times on the SAME
        warmed page with at most ONE fresh-page recovery — a bounded ladder, never
        the six-engine storm. With ``try_http_first`` it tries two cheap HTTP rungs
        first. ``use_browser=False`` is the legacy/opt-in path: plain HTTP that
        escalates through cloudscraper, camoufox, and the Chromium playwright-stealth
        rescue rungs (still the right behaviour for non-Cloudflare sites like
        WebNovel-dynamic, whose attempt-1 HTTP succeeds anyway).

        ``use_cache`` defaults to the instance's ``self.use_cache`` when None.
        Raises :class:`ScrapeCancelled` if ``cancel_event`` is set at the start of
        the call so a Stop request ends the run before any network work happens.
        """
        if self.cancel_event.is_set():
            raise ScrapeCancelled(f"Fetch cancelled before requesting {url}")
        effective_cache = self.use_cache if use_cache is None else use_cache
        caller_retries = self.max_retries if max_retries is None else max(0, int(max_retries))
        base_delay = self.retry_base_delay if retry_base_delay is None else float(retry_base_delay)
        if use_browser and fast_path:
            # 0.2.0 fast-primary (§3.2/§3.2a): a BOUNDED camoufox budget then a typed
            # signal so the conductor hands the hard chapter to rescue. No stealth
            # rung (that is rescue's job) and no large backoff — the host limiter
            # paces navigations, so a second sleep here would only double-count.
            ladder = FAST_HTTP_FIRST_LADDER if self.try_http_first else FAST_BROWSER_LADDER
            max_attempt_retries = len(ladder) - 1
            base_delay = 0.0
            browser_primary = True
        elif use_browser:
            # Browser-primary (FWN default): bounded two-engine headful ladder
            # camoufox -> camoufox_fresh -> stealth-Chromium. Cap the retry budget to
            # the ladder length so a blocked chapter walks it exactly once — a couple
            # of same-page camoufox retries, ONE fresh-camoufox recovery, ONE
            # stealth-Chromium escalation — never the six-engine storm. Once camoufox
            # is exhausted for the run, later chapters go straight to the persistent
            # stealth-Chromium engine (reused, not relaunched). ``min`` still honours
            # a smaller caller-supplied budget (used by some tests).
            if self._camoufox_exhausted:
                ladder = STEALTH_LATCHED_LADDER
            elif self.try_http_first:
                ladder = HTTP_FIRST_PRIMARY_LADDER
            else:
                ladder = HEADFUL_PRIMARY_LADDER
            max_attempt_retries = min(caller_retries, len(ladder) - 1)
            browser_primary = True
        else:
            ladder = DEFAULT_ESCALATION_LADDER
            max_attempt_retries = caller_retries
            browser_primary = False
        return self._fetch_with_retry_ladder(
            url,
            use_cache=effective_cache,
            ladder=ladder,
            max_retries=max_attempt_retries,
            retry_base_delay=base_delay,
            browser_primary=browser_primary,
        )

    # ── cache helpers ────────────────────────────────────────────────────────
    def cache_path_for(self, url: str) -> Path:
        return self.cache_dir / cache_key_for(url)

    def _read_cache(self, cache_path: Path) -> Optional[str]:
        if not (self.use_cache and cache_path.is_file()):
            return None
        try:
            html = cache_path.read_text(encoding="utf-8")
        except Exception as exc:  # corrupt/locked cache -> re-fetch
            self._log(f"Cache read failed for {cache_path.name}: {exc}; re-fetching.")
            return None
        # Self-heal a poisoned cache: an entry written before the brotli fix may be
        # mis-decoded garbage. Ignore it (and re-fetch cleanly) rather than serving
        # garbage that fails body extraction downstream.
        if _looks_garbled(html):
            self._log(
                f"Cache entry {cache_path.name} is garbled (undecodable); re-fetching."
            )
            return None
        return html

    def _write_cache(self, cache_path: Path, html: str) -> None:
        if not self.use_cache:
            return
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(html, encoding="utf-8")
        except Exception as exc:
            self._log(f"  (cache write failed: {exc})")

    # ── HTTP path ────────────────────────────────────────────────────────────
    @property
    def session(self) -> requests.Session:
        if self._session is None:
            s = requests.Session()
            s.headers.update(BROWSER_HEADERS)
            self._session = s
        return self._session

    def _cloudscraper(self) -> Any:
        if self._scraper is None:
            import cloudscraper  # lazy: only when a CF challenge is hit

            self._scraper = cloudscraper.create_scraper()
            self._scraper.headers.update(BROWSER_HEADERS)
        return self._scraper

    def _acquire_nav(self, url: str) -> None:
        """Gate one TOP-LEVEL navigation through the shared host limiter (§3.4).

        Called immediately before each ``session.get`` / ``cloudscraper.get`` /
        origin warm-up ``goto`` / chapter ``goto`` / retry nav — NOT for cache reads
        or browser sub-resources. A no-op when no limiter is injected (legacy/tests).
        Honors ``cancel_event`` while it waits.
        """
        if self.host_limiter is not None:
            self.host_limiter.acquire(url, cancel_event=self.cancel_event)

    def _get_text(self, session: Any, url: str) -> str:
        """One HTTP GET, classified BODY-FIRST (§3.3).

        The pre-0.2.0 version raised on 403/5xx *before* the body could be seen, so a
        Cloudflare interstitial served with a 403/503 was misclassified. Now the body
        is read first and the classification order is: real payload → success; CF
        challenge body → ChallengeFetchError; 404/410 → NotFoundFetchError; 429 →
        RateLimitedFetchError (Retry-After honored); 5xx/other (no CF) →
        TransientFetchError; a bare 403 with neither payload nor a CF marker →
        ChallengeFetchError (conservative) + an "unmatched 403 body" log so the live
        pass can refine the markers.
        """
        r = session.get(url, timeout=self._http_timeout, allow_redirects=True)
        status = getattr(r, "status_code", None)
        body = r.text or ""

        # (2) Real chapter payload present → success regardless of status code (a
        # cleared page can still carry a 403/503 + ambient beacon).
        if has_real_payload(body):
            return body
        # (3) Cloudflare challenge body → challenge (a CF-style 503 lands HERE, not
        # as a generic 5xx).
        if is_cloudflare_challenge(body):
            raise ChallengeFetchError(
                f"Cloudflare challenge body from {url} (HTTP {status})", status=status
            )
        # (4) 404 / 410 → terminal not-found.
        if status in NOT_FOUND_STATUSES:
            raise NotFoundFetchError(f"HTTP {status} for {url}", status=status)
        # (5) 429 → rate limited (transient + global host pacing).
        if status == RATE_LIMIT_STATUS:
            raise RateLimitedFetchError(
                f"HTTP 429 (rate limited) for {url}",
                status=status,
                retry_after=_parse_retry_after(r),
            )
        # (6) 5xx (no CF body) → transient.
        if status is not None and status >= RETRY_STATUS_MIN:
            raise TransientFetchError(f"HTTP {status} for {url}", status=status)
        # (7) Bare 403 with neither payload nor a known CF marker → conservatively a
        # challenge, logged so the live pass can refine the markers.
        if status == 403:
            self._log(
                f"  unmatched 403 body for {url} (no real payload, no CF marker) — "
                "treating as a challenge conservatively; capture this body to refine "
                "the Cloudflare markers."
            )
            raise ChallengeFetchError(f"HTTP 403 (unmatched body) for {url}", status=status)
        # Any other 4xx → let requests raise its standard HTTPError (transient-ish);
        # a 2xx falls through and returns the body.
        r.raise_for_status()
        return body

    @staticmethod
    def _warmed_hosts_for(session: Any) -> set:
        """The set of hosts already warmed on ``session`` (one per session)."""
        warmed = getattr(session, "_wns_warmed_hosts", None)
        if warmed is None:
            warmed = set()
            try:
                session._wns_warmed_hosts = warmed
            except Exception:
                # A session that refuses attributes (unlikely) simply re-warms;
                # harmless, just an extra homepage GET.
                pass
        return warmed

    @staticmethod
    def _apply_request_headers(
        session: Any, *, referer: Optional[str], sec_fetch_site: str
    ) -> None:
        """Set the per-request Referer / Sec-Fetch-Site on ``session.headers``.

        Works on a real ``requests``/``cloudscraper`` session (CaseInsensitiveDict)
        and on the plain-dict fakes the tests use.
        """
        headers = session.headers
        headers["Sec-Fetch-Site"] = sec_fetch_site
        if referer:
            headers["Referer"] = referer
        else:
            try:
                headers.pop("Referer", None)
            except Exception:
                pass

    def _http_get(self, session: Any, url: str) -> str:
        """HTTP GET that mimics a real browser's navigation sequence.

        The legacy FreeWebNovel scraper downloaded a whole novel without tripping
        Cloudflare; the rewrite lost two browser-like behaviours that this restores
        on the primary (attempt-1) HTTP path:

        1. **A once-per-host warm-up GET to the site origin.** Before the first
           chapter on a host is requested, we GET the homepage so Cloudflare issues
           a ``cf_clearance`` cookie into the persistent session — exactly what a
           human does by opening the site before reading. (A full run already
           fetches the index page first, but a *resume* run loads the cached TOC and
           would otherwise hit a chapter URL cold, with no cookies.) Cookies persist
           on ``self.session`` across every chapter, so the clearance is reused.
        2. **A host-derived Referer + correct Sec-Fetch-Site.** The warm-up looks
           like an address-bar navigation (``Sec-Fetch-Site: none``, no Referer);
           every subsequent same-host request looks like an in-site click
           (``Sec-Fetch-Site: same-origin``, ``Referer`` = the site origin).

        Goes through ``_get_text`` so status handling — and the tests that patch
        ``_get_text`` — stay unchanged.
        """
        origin = _origin_of(url)
        host = urlsplit(url).netloc
        warmed = self._warmed_hosts_for(session)

        if origin and host and host not in warmed:
            # Best-effort warm-up: a failure here must never fail the real fetch
            # (the real GET below still runs and reports the true outcome).
            self._apply_request_headers(session, referer=None, sec_fetch_site="none")
            try:
                self._acquire_nav(origin)  # warm-up GET is a top-level nav (§3.4)
                self._get_text(session, origin)
                self._log(
                    f"  warmed session on {host} (homepage GET to acquire cf cookies)"
                )
            except Exception as exc:
                self._log(f"  (warm-up GET for {host} skipped: {exc})")
            warmed.add(host)

        if origin and host in warmed:
            self._apply_request_headers(
                session, referer=origin, sec_fetch_site="same-origin"
            )
        else:
            self._apply_request_headers(session, referer=None, sec_fetch_site="none")
        self._acquire_nav(url)  # the real chapter GET is a top-level nav (§3.4)
        return self._get_text(session, url)

    def _fetch_uncached_strategy(self, url: str, strategy: str) -> str:
        """Run exactly one fetch attempt using one concrete strategy."""
        if strategy == FETCH_STRATEGY_HTTP:
            html = self._http_get(self.session, url)
        elif strategy == FETCH_STRATEGY_CLOUDSCRAPER:
            html = self._http_get(self._cloudscraper(), url)
        elif strategy == FETCH_STRATEGY_PLAYWRIGHT_STEALTH:
            html = self._fetch_browser_once(url, fresh_context=False)
        elif strategy == FETCH_STRATEGY_PLAYWRIGHT_STEALTH_FRESH:
            html = self._fetch_browser_once(url, fresh_context=True)
        elif strategy == FETCH_STRATEGY_CAMOUFOX:
            html = self._fetch_camoufox_once(url, fresh_context=False)
        elif strategy == FETCH_STRATEGY_CAMOUFOX_FRESH:
            html = self._fetch_camoufox_once(url, fresh_context=True)
        else:
            raise ValueError(f"Unknown fetch strategy: {strategy}")

        if _looks_garbled(html):
            raise TransientFetchError(
                f"undecodable response from {strategy} (an unsupported "
                "content-encoding); escalating instead of caching garbage"
            )
        if is_cloudflare_challenge(html):
            raise ChallengeFetchError(f"Cloudflare challenge returned by {strategy}")
        return html

    def _cancellable_backoff(self, seconds: float) -> None:
        """Sleep ``seconds`` via the injected ``self._sleep``, but in slices no
        longer than ``BACKOFF_WAIT_SLICE`` that re-check ``cancel_event`` each
        slice — so a Stop aborts the legacy inter-attempt backoff within one slice
        instead of elapsing the full (up to ~138s) wait. One injected timing source
        (a fake clock drives it deterministically; no monolithic real-clock sleep),
        mirroring ``HostRateLimiter._wait``. Returns early on cancel; the ladder's
        loop-top check then raises :class:`ScrapeCancelled`. 0.2.0 BUG-2 fix."""
        remaining = float(seconds)
        while remaining > 0.0:
            if self.cancel_event.is_set():
                return
            chunk = remaining if remaining < BACKOFF_WAIT_SLICE else BACKOFF_WAIT_SLICE
            self._sleep(chunk)
            remaining -= chunk

    def _fetch_with_retry_ladder(
        self,
        url: str,
        *,
        use_cache: bool,
        ladder: Sequence[str],
        max_retries: int,
        retry_base_delay: float,
        browser_primary: bool = False,
    ) -> str:
        cache_path = self.cache_path_for(url)
        if use_cache:
            cached = self._read_cache(cache_path)
            if cached is not None:
                self.last_fetch_info = FetchInfo(from_cache=True, classification="cache")
                return cached

        max_attempts = max(1, int(max_retries) + 1)
        last_exc: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            if self.cancel_event.is_set():
                raise ScrapeCancelled(f"Fetch cancelled before requesting {url}")

            strategy = _strategy_for_attempt(ladder, attempt)
            if browser_primary and strategy in _STEALTH_STRATEGIES:
                # camoufox could not clear this chapter — latch to the legacy
                # stealth-Chromium engine for the rest of the run so later chapters
                # reuse the one persistent stealth browser instead of relaunching it
                # behind a camoufox retry (the two engines cannot coexist on a
                # thread). Set as soon as the stealth rung is reached.
                self._camoufox_exhausted = True
            t0 = time.time()
            try:
                self._log(f"  fetch attempt {attempt}/{max_attempts} via {strategy}")
                html = self._fetch_uncached_strategy(url, strategy)
                elapsed = time.time() - t0
                self._log(f"  fetched {len(html) / 1024.0:.1f} KB in {elapsed:.1f} s")
                if use_cache:
                    self._write_cache(cache_path, html)
                self.last_fetch_info = FetchInfo(
                    from_cache=False, classification="success", strategy=strategy
                )
                return html
            except ScrapeCancelled:
                raise
            except NotFoundFetchError as exc:
                # 404/410 — terminal, NEVER rescued.
                self._log(f"  {exc} - not retrying (not found).")
                self.last_fetch_info = FetchInfo(
                    from_cache=False, classification="not_found",
                    status=getattr(exc, "status", None), strategy=strategy,
                )
                raise
            except RateLimitedFetchError as exc:
                # 429 — park the host for BOTH lanes via the shared limiter, then
                # surface as terminal. A browser identity from the same IP is the
                # wrong answer to a site-wide rate limit, so we do NOT walk the rest
                # of the ladder; the pipeline (Phase 3) retries on the primary within
                # a bounded rate-limit budget after the cooldown.
                if self.host_limiter is not None:
                    self.host_limiter.note_rate_limited(
                        url, getattr(exc, "retry_after", None)
                    )
                self._log(f"  {exc} - host cooldown applied; not escalating to browser.")
                self.last_fetch_info = FetchInfo(
                    from_cache=False, classification="rate_limited",
                    status=getattr(exc, "status", None), strategy=strategy,
                )
                raise
            except Exception as exc:
                # A browser-layer 404/410 surfaces as RuntimeError("HTTP 404 …") →
                # terminal not-found. 403 is deliberately NOT here — it is classified
                # body-first as a challenge (polled for clearance first), never a skip.
                if _looks_like_not_found(exc):
                    self._log(f"  {exc} - not retrying (not found).")
                    self.last_fetch_info = FetchInfo(
                        from_cache=False, classification="not_found", strategy=strategy
                    )
                    raise NotFoundFetchError(str(exc)) from exc
                last_exc = exc
                self._log(
                    f"  attempt {attempt}/{max_attempts} via {strategy} failed: {exc}"
                )
                if attempt >= max_attempts:
                    break
                # A browser engine that isn't installed / can't launch is a
                # STRUCTURAL failure: retrying it (let alone after a 100-second
                # backoff) can never succeed. Advance to the next rung IMMEDIATELY,
                # with no sleep, so a fresh install that only ran setup never hangs.
                if _looks_like_browser_launch_failure(exc):
                    next_strategy = _strategy_for_attempt(ladder, attempt + 1)
                    self._log(
                        f"  {strategy} browser engine is not installed/launchable — "
                        f"skipping to {next_strategy} immediately (no backoff). "
                        "Re-run Setup_and_Run-Web-Novel-Scraper to install the "
                        "browser engines."
                    )
                    continue
                delay = compute_backoff_delay(
                    attempt,
                    base_delay=retry_base_delay,
                    jitter_ratio=self.retry_jitter_ratio,
                    random_fn=self._random,
                )
                next_strategy = _strategy_for_attempt(ladder, attempt + 1)
                self._log(
                    f"  retrying in {delay:.1f}s with {next_strategy}."
                )
                # Cancel-aware sliced wait (BUG-2): a Stop during a long backoff
                # takes effect within one slice; the loop-top check above then
                # raises ScrapeCancelled on the next iteration.
                self._cancellable_backoff(delay)

        # Exhausted. Surface the LAST failure's TYPE so the conductor routes it
        # correctly (ChallengeFetchError → rescue; TransientFetchError → rescue with
        # backoff). A non-typed last error falls back to a generic FetchError.
        self.last_fetch_info = FetchInfo(
            from_cache=False,
            classification=_classification_for(last_exc),
            status=getattr(last_exc, "status", None),
        )
        if isinstance(last_exc, FetchError):
            raise last_exc
        raise FetchError(f"Giving up on {url}: {last_exc}")

    def fetch_html(self, url: str, use_cache: bool = True) -> str:
        """Fetch a URL over HTTP (with cloudscraper CF fallback). Cached.

        Raises ``FetchError`` on a permanent failure (403/404 or retries
        exhausted) so the caller can record-and-skip.
        """
        return self._fetch_with_retry_ladder(
            url,
            use_cache=use_cache,
            ladder=(FETCH_STRATEGY_HTTP, FETCH_STRATEGY_CLOUDSCRAPER),
            max_retries=self.max_retries,
            retry_base_delay=self.retry_base_delay,
        )

    # ── Browser path ─────────────────────────────────────────────────────────
    def _ensure_browser_page(self) -> Any:
        """Start Playwright + a stealth browser once; reuse the page thereafter."""
        if self._page is not None:
            return self._page

        from playwright.sync_api import sync_playwright

        from .cf_bypass import create_stealth_browser

        # Start Playwright only once per manager. The ``browser_fresh`` strategy
        # tears down the browser/context/page via ``_reset_browser`` but keeps the
        # running Playwright driver; starting a second ``sync_playwright()`` on the
        # same thread while the first is alive raises "Sync API inside the asyncio
        # loop" and poisons every later browser attempt. Reuse the live driver and
        # only launch a fresh browser + context (which still picks a new UA).
        if self._pw is None:
            self._pw = sync_playwright().start()
        self._browser, self._context = create_stealth_browser(
            self._pw, headless=self.headless
        )
        self._page = self._context.new_page()
        return self._page

    def _reset_browser(self) -> None:
        """Drop the current browser context so the next attempt gets a fresh UA."""
        for attr, closer in (
            ("_page", lambda p: p.close()),
            ("_context", lambda c: c.close()),
            ("_browser", lambda b: b.close()),
        ):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    closer(obj)
                except Exception as exc:
                    self._log(f"  (browser reset: {attr} -> {exc})")
                setattr(self, attr, None)

    def _teardown_chromium(self) -> None:
        """Fully stop the Chromium stealth engine, INCLUDING its sync-Playwright
        driver. Needed before starting Camoufox: Camoufox runs its own internal
        sync-Playwright and two sync-Playwright loops on one thread raise "Sync API
        inside the asyncio loop". (``_reset_browser`` keeps the driver alive for a
        fresh-context reset; this also stops the driver.)"""
        self._reset_browser()
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception as exc:
                self._log(f"  (chromium teardown: _pw -> {exc})")
            self._pw = None

    def _fetch_browser_once(self, url: str, *, fresh_context: bool) -> str:
        # One browser engine per thread: tear down any live Camoufox engine before
        # starting Chromium stealth (their sync-Playwright loops cannot coexist).
        if self._cf_cm is not None or self._cf_page is not None:
            self._reset_camoufox()
        if fresh_context:
            self._reset_browser()

        from .cf_bypass import fetch_with_stealth

        page = self._ensure_browser_page()
        self._acquire_nav(url)  # chapter goto is a top-level nav (§3.4)
        html = fetch_with_stealth(
            page,
            url,
            nav_timeout=self._browser_nav_timeout_ms,
            cf_timeout=self._cloudflare_timeout,
            log_fn=self._log,
        )
        if is_cloudflare_challenge(html):
            raise ChallengeFetchError(
                "Cloudflare challenge still present after browser fetch"
            )
        return html

    # ── Camoufox path ────────────────────────────────────────────────────────
    def _ensure_camoufox_page(self) -> Any:
        """Start a Camoufox browser once; reuse its page thereafter.

        Before reusing the cached page, verify it is still alive. A headful
        camoufox page/context can be torn down out from under us mid-run (the
        live BUG-1 — an unfocused/occluded window whose page was closed), and
        reusing a dead page fails the whole rung. If the cached page is
        positively closed, drop the whole browser and build a fresh one within
        the same attempt. A healthy page (``is_closed()`` → ``False``, the normal
        FWN case) is reused unchanged, so this guard NEVER triggers on a healthy
        page — the FWN scope gate / single-lane / ch-102 detector are untouched.
        """
        if self._cf_page is not None and _page_is_alive(self._cf_page):
            return self._cf_page
        if self._cf_page is not None:
            # Cached page is positively dead — tear the browser down so a fresh
            # one (with a fresh, re-warmable context) is built below.
            self._log("  (camoufox page was closed; recreating browser)")
            self._reset_camoufox()

        from .cf_bypass import create_camoufox_browser

        self._cf_cm, self._cf_page = create_camoufox_browser(headless=self.headless)
        return self._cf_page

    def _reset_camoufox(self) -> None:
        """Tear down the Camoufox browser so the next attempt gets a fresh one."""
        cm = self._cf_cm
        self._cf_page = None
        self._cf_cm = None
        # A fresh browser has an empty cookie jar, so its session must be re-warmed.
        self._cf_warmed_hosts = set()
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            except Exception as exc:
                self._log(f"  (camoufox reset: {exc})")

    def _warm_camoufox_session(self, page: Any, url: str) -> None:
        """Warm the VISIBLE camoufox browser SESSION on a host, once per browser.

        Before the first chapter on a host is fetched, navigate the same persistent
        page to the site origin so Cloudflare seats a ``cf_clearance`` cookie in the
        browser context — exactly what a human does by opening the site before
        reading. The 0.1.2 warm-up only warmed the *HTTP* session; the FWN primary
        path now runs through the browser, so this warms the *browser* session it
        actually uses. The cookie persists on the camoufox context across every
        later chapter navigation on the same page.

        Best-effort: a warm-up failure is swallowed (the real chapter fetch that
        follows still runs and reports the true outcome), and each host is warmed at
        most once per browser (recreated browsers re-warm).
        """
        origin = _origin_of(url)
        host = urlsplit(url).netloc
        if not origin or not host or host in self._cf_warmed_hosts:
            return
        from .cf_bypass import fetch_camoufox

        try:
            self._acquire_nav(origin)  # origin warm-up goto is a top-level nav (§3.4)
            fetch_camoufox(
                page,
                origin,
                nav_timeout=self._browser_nav_timeout_ms,
                cf_timeout=self._cloudflare_timeout,
                log_fn=self._log,
            )
            self._log(
                f"  warmed camoufox session on {host} "
                "(origin GET to acquire cf_clearance in the browser context)"
            )
        except ScrapeCancelled:
            # A Stop during the warm-up navigation must END the run, never be
            # swallowed as a best-effort "warm-up skipped" failure by the broad
            # except below (ScrapeCancelled is an Exception). Re-raise first so it
            # propagates like the main ladder's existing re-raise (BUG-2 secondary
            # gap). Matters on the FWN path where the shared limiter can raise it.
            raise
        except Exception as exc:
            if _is_page_closed_error(exc):
                # The page died during warm-up (BUG-1 companion). Do NOT mark the
                # host warmed and do NOT leave the dead page cached — tearing it
                # down here and re-raising lets ``_fetch_camoufox_once``'s
                # recreate-and-retry build a fresh page and re-warm it, instead
                # of the old behaviour (swallow + mark warmed + hand the caller a
                # corpse that then fails the chapter goto).
                self._log(
                    f"  (camoufox warm-up hit a closed page on {host}; recreating)"
                )
                self._reset_camoufox()
                raise
            self._log(f"  (camoufox warm-up for {host} skipped: {exc})")
        # Mark warmed even on a (non-closed) failure so a flaky origin never
        # re-warms every chapter.
        self._cf_warmed_hosts.add(host)

    def _fetch_camoufox_once(self, url: str, *, fresh_context: bool) -> str:
        # One browser engine per thread: fully stop any live Chromium stealth engine
        # (including its sync-Playwright driver) before starting Camoufox, which
        # spins up its own sync-Playwright internally.
        if self._pw is not None or self._page is not None:
            self._teardown_chromium()
        if fresh_context:
            self._reset_camoufox()

        from .cf_bypass import fetch_camoufox

        # The cached camoufox page can die between the liveness check in
        # ``_ensure_camoufox_page`` and the actual warm-up/goto (BUG-1). Allow at
        # most ONE recreate-and-retry WITHIN this rung: if warm-up or the chapter
        # nav raises a 'target closed' teardown on the first try, rebuild the
        # browser and try once more. A second closed failure (or any non-closed
        # failure) propagates so the ladder advances normally — no recreate loop.
        for attempt in range(2):
            page = self._ensure_camoufox_page()
            try:
                # Warm the browser session on this host once before fetching the
                # chapter, so the chapter request carries the cf_clearance cookie
                # from request #1.
                self._warm_camoufox_session(page, url)
                self._acquire_nav(url)  # chapter goto is a top-level nav (§3.4)
                html = fetch_camoufox(
                    page,
                    url,
                    nav_timeout=self._browser_nav_timeout_ms,
                    cf_timeout=self._cloudflare_timeout,
                    log_fn=self._log,
                )
            except ScrapeCancelled:
                raise
            except Exception as exc:
                if attempt == 0 and _is_page_closed_error(exc):
                    # Page/context was torn down mid-attempt — drop the dead
                    # browser (idempotent if warm-up already reset it) and retry
                    # ONCE on a fresh page within the same rung.
                    self._log(
                        f"  (camoufox page closed mid-fetch; recreating once: {exc})"
                    )
                    self._reset_camoufox()
                    continue
                raise
            if is_cloudflare_challenge(html):
                raise ChallengeFetchError(
                    "Cloudflare challenge still present after camoufox fetch"
                )
            return html
        # Unreachable: the loop body always returns or raises (attempt 1 re-raises
        # any closed error rather than continuing).
        raise ChallengeFetchError(
            "Cloudflare challenge still present after camoufox fetch"
        )

    def fetch_html_browser(self, url: str, use_cache: bool = True) -> str:
        """Fetch a URL through the Playwright stealth browser. Cached.

        Raises ``FetchError`` if the Cloudflare challenge never clears.
        """
        return self._fetch_with_retry_ladder(
            url,
            use_cache=use_cache,
            ladder=BROWSER_ESCALATION_LADDER,
            max_retries=self.max_retries,
            retry_base_delay=self.retry_base_delay,
        )

    # ── lifecycle ────────────────────────────────────────────────────────────
    def close(self) -> None:
        """Tear down the browser + Playwright cleanly. Safe to call repeatedly."""
        # Camoufox first (its context manager owns its own browser/driver).
        if self._cf_cm is not None:
            self._reset_camoufox()
        for attr, closer in (
            ("_page", lambda p: p.close()),
            ("_context", lambda c: c.close()),
            ("_browser", lambda b: b.close()),
            ("_pw", lambda pw: pw.stop()),
        ):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    closer(obj)
                except Exception as exc:
                    self._log(f"  (browser teardown: {attr} -> {exc})")
                setattr(self, attr, None)
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None

    def __enter__(self) -> "RequestManager":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
