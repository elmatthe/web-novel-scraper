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
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import requests

from .cloudflare_detection import is_cloudflare_challenge

logger = logging.getLogger(__name__)

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
FETCH_TIMEOUT = 30         # per-request timeout, seconds
PERMANENT_STATUSES = (403, 404)   # never retried — treated as a permanent skip
RETRY_STATUS_MIN = 500     # >= this is a server error and IS retried
BROWSER_NAV_TIMEOUT_MS = 60_000
CF_CLEAR_TIMEOUT = 45.0    # seconds to wait for a Cloudflare challenge to clear

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

# Repo-root-relative cache root: files/cache/ (request_manager is at
# scripts/Universal/webnovel_scraper/, so parents[3] is the repo root).
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CACHE_ROOT = _REPO_ROOT / "files" / "cache"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Realistic Chrome headers (ported verbatim from scrape_noble_queen-v3.py).
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
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.webnovel.com/",
}


class FetchError(RuntimeError):
    """A fetch failed permanently (all retries exhausted, or a permanent status).

    The pipeline records this as a failed chapter and skips it; it is never fatal.
    """


class _PermanentStatus(Exception):
    """Internal: a 403/404 that must not be retried."""


class _RetryableFetch(Exception):
    """Internal: a transient failure eligible for the retry ladder."""


class ScrapeCancelled(Exception):
    """Raised when a fetch is requested after ``cancel_event`` has been set.

    The GUI Stop button sets the manager's ``cancel_event``; the pipeline lets
    this propagate to end the run cleanly between (or at the start of) fetches.
    """


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


def _looks_like_permanent_status(exc: Exception) -> bool:
    """Best-effort classifier for browser/navigation status errors."""
    msg = str(exc)
    for status in PERMANENT_STATUSES:
        if re.search(rf"\bHTTP\s+{status}\b", msg, re.IGNORECASE):
            return True
    return False


class RequestManager:
    """Fetches HTML over HTTP or a stealth browser, with a shared on-disk cache.

    Args:
        slug: drives the cache subdirectory (``files/cache/{slug}/``).
        use_cache: read/write the on-disk HTML cache.
        headless: run the Playwright browser headless (browser path only).
        cache_root: override the cache root (defaults to ``files/cache/``);
            useful for tests so they never touch the repo cache.
        log_fn: optional logging callback ``(str) -> None`` (defaults to logger).
    """

    def __init__(
        self,
        slug: str,
        use_cache: bool = True,
        headless: bool = True,
        *,
        cache_root: Optional[Path] = None,
        log_fn: Optional[Callable[[str], None]] = None,
        max_retries: int = MAX_RETRIES,
        retry_base_delay: float = RETRY_SLEEP,
        retry_jitter_ratio: float = RETRY_JITTER_RATIO,
        sleep_fn: Callable[[float], None] = time.sleep,
        random_fn: Callable[[], float] = random.random,
    ) -> None:
        self.slug = slug
        self.use_cache = use_cache
        self.headless = headless
        self.cache_dir = (cache_root or DEFAULT_CACHE_ROOT) / slug
        self._log: Callable[[str], None] = log_fn or logger.info
        self.max_retries = max(0, int(max_retries))
        self.retry_base_delay = float(retry_base_delay)
        self.retry_jitter_ratio = float(retry_jitter_ratio)
        self._sleep = sleep_fn
        self._random = random_fn

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
    ) -> str:
        """Unified fetch facade used by adapters and the pipeline.

        Fetches through a retry ladder. ``use_browser=False`` starts at plain
        HTTP and escalates through cloudscraper and Playwright. ``use_browser=True``
        starts at Playwright and escalates to a fresh browser context/UA.
        ``use_cache`` defaults to the instance's ``self.use_cache`` when None.
        Raises :class:`ScrapeCancelled` if
        ``cancel_event`` is set at the start of the call so a Stop request ends
        the run before any network work happens.
        """
        if self.cancel_event.is_set():
            raise ScrapeCancelled(f"Fetch cancelled before requesting {url}")
        effective_cache = self.use_cache if use_cache is None else use_cache
        max_attempt_retries = self.max_retries if max_retries is None else max(0, int(max_retries))
        base_delay = self.retry_base_delay if retry_base_delay is None else float(retry_base_delay)
        ladder = BROWSER_ESCALATION_LADDER if use_browser else DEFAULT_ESCALATION_LADDER
        return self._fetch_with_retry_ladder(
            url,
            use_cache=effective_cache,
            ladder=ladder,
            max_retries=max_attempt_retries,
            retry_base_delay=base_delay,
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

    @staticmethod
    def _get_text(session: Any, url: str) -> str:
        r = session.get(url, timeout=FETCH_TIMEOUT, allow_redirects=True)
        if r.status_code in PERMANENT_STATUSES:
            raise _PermanentStatus(f"HTTP {r.status_code} for {url}")
        if r.status_code >= RETRY_STATUS_MIN:
            raise RuntimeError(f"HTTP {r.status_code}")
        r.raise_for_status()
        return r.text

    def _fetch_uncached_strategy(self, url: str, strategy: str) -> str:
        """Run exactly one fetch attempt using one concrete strategy."""
        if strategy == FETCH_STRATEGY_HTTP:
            html = self._get_text(self.session, url)
        elif strategy == FETCH_STRATEGY_CLOUDSCRAPER:
            html = self._get_text(self._cloudscraper(), url)
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
            raise _RetryableFetch(
                f"undecodable response from {strategy} (an unsupported "
                "content-encoding); escalating instead of caching garbage"
            )
        if is_cloudflare_challenge(html):
            raise _RetryableFetch(f"Cloudflare challenge returned by {strategy}")
        return html

    def _fetch_with_retry_ladder(
        self,
        url: str,
        *,
        use_cache: bool,
        ladder: Sequence[str],
        max_retries: int,
        retry_base_delay: float,
    ) -> str:
        cache_path = self.cache_path_for(url)
        if use_cache:
            cached = self._read_cache(cache_path)
            if cached is not None:
                return cached

        max_attempts = max(1, int(max_retries) + 1)
        last_exc: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            if self.cancel_event.is_set():
                raise ScrapeCancelled(f"Fetch cancelled before requesting {url}")

            strategy = _strategy_for_attempt(ladder, attempt)
            t0 = time.time()
            try:
                self._log(f"  fetch attempt {attempt}/{max_attempts} via {strategy}")
                html = self._fetch_uncached_strategy(url, strategy)
                elapsed = time.time() - t0
                self._log(f"  fetched {len(html) / 1024.0:.1f} KB in {elapsed:.1f} s")
                if use_cache:
                    self._write_cache(cache_path, html)
                return html
            except _PermanentStatus as exc:
                self._log(f"  {exc} - not retrying.")
                raise FetchError(str(exc)) from exc
            except Exception as exc:
                if _looks_like_permanent_status(exc):
                    self._log(f"  {exc} - not retrying.")
                    raise FetchError(str(exc)) from exc
                last_exc = exc
                self._log(
                    f"  attempt {attempt}/{max_attempts} via {strategy} failed: {exc}"
                )
                if attempt >= max_attempts:
                    break
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
                self._sleep(delay)

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
        html = fetch_with_stealth(
            page,
            url,
            nav_timeout=BROWSER_NAV_TIMEOUT_MS,
            cf_timeout=CF_CLEAR_TIMEOUT,
            log_fn=self._log,
        )
        if is_cloudflare_challenge(html):
            raise _RetryableFetch("Cloudflare challenge still present after browser fetch")
        return html

    # ── Camoufox path ────────────────────────────────────────────────────────
    def _ensure_camoufox_page(self) -> Any:
        """Start a Camoufox browser once; reuse its page thereafter."""
        if self._cf_page is not None:
            return self._cf_page

        from .cf_bypass import create_camoufox_browser

        self._cf_cm, self._cf_page = create_camoufox_browser(headless=self.headless)
        return self._cf_page

    def _reset_camoufox(self) -> None:
        """Tear down the Camoufox browser so the next attempt gets a fresh one."""
        cm = self._cf_cm
        self._cf_page = None
        self._cf_cm = None
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            except Exception as exc:
                self._log(f"  (camoufox reset: {exc})")

    def _fetch_camoufox_once(self, url: str, *, fresh_context: bool) -> str:
        # One browser engine per thread: fully stop any live Chromium stealth engine
        # (including its sync-Playwright driver) before starting Camoufox, which
        # spins up its own sync-Playwright internally.
        if self._pw is not None or self._page is not None:
            self._teardown_chromium()
        if fresh_context:
            self._reset_camoufox()

        from .cf_bypass import fetch_camoufox

        page = self._ensure_camoufox_page()
        html = fetch_camoufox(
            page,
            url,
            nav_timeout=BROWSER_NAV_TIMEOUT_MS,
            cf_timeout=CF_CLEAR_TIMEOUT,
            log_fn=self._log,
        )
        if is_cloudflare_challenge(html):
            raise _RetryableFetch("Cloudflare challenge still present after camoufox fetch")
        return html

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
