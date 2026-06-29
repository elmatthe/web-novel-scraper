#!/usr/bin/env python3

"""
cf_bypass.py — Cloudflare bypass utilities for web scrapers.

Provides a multi-strategy approach to bypass Cloudflare's bot detection:

  Strategy 1 (default): playwright-stealth
    - Patches 12+ JS properties (navigator.webdriver, chrome runtime, etc.)
    - Lightweight, works with existing Playwright code
    - pip install playwright-stealth

  Strategy 2: camoufox
    - Anti-detect Firefox with fingerprint injection at the C++ level
    - Rotates realistic device fingerprints via BrowserForge
    - pip install camoufox[geoip] && python -m camoufox fetch

  Strategy 3: nodriver
    - Chrome DevTools Protocol-based, no WebDriver footprint at all
    - Built-in verify_cf() for automatic Turnstile solving
    - pip install nodriver

All strategies include additional manual stealth patches and human-like
behavior (random delays, mouse jitter, realistic viewport) to reduce
detection surface beyond what any single library covers.

Live escalation order used by ``request_manager`` (NOT the "Strategy 1/2/3"
numbering above, which is historical): camoufox leads the browser rungs because
it clears the common managed challenge cheaply, and the Chromium
playwright-stealth rungs are the LAST-RESORT rescue after camoufox is exhausted:

    http -> cloudscraper -> camoufox -> camoufox_fresh
         -> playwright_stealth -> playwright_stealth_fresh

A live FreeWebNovel challenge was observed to defeat camoufox on every attempt,
which is why the Chromium-stealth rescue rungs are wired back in after it. Only
ONE browser engine may be live per thread (camoufox + Chromium each run their own
sync-Playwright), so ``request_manager`` tears the other engine down before
starting one.

Usage:
    from cf_bypass import create_stealth_browser, wait_for_cloudflare

    # Inside a sync_playwright() context:
    browser, context = create_stealth_browser(playwright_instance)
    page = context.new_page()
    page.goto("https://protected-site.com")
    wait_for_cloudflare(page)
    html = page.content()
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# The Cloudflare challenge detector is shared with request_manager via the
# ``cloudflare_detection`` module so the two can never drift. ``is_cloudflare_
# challenge`` is re-exported below for callers that import it from cf_bypass.
from .cloudflare_detection import is_cloudflare_challenge  # noqa: E402

# Point Chromium / playwright-stealth at the contained in-repo browser cache the
# launcher installs into (files/bin/ms-playwright), even when started outside the
# launcher. ``setdefault`` semantics — see ``browser_env``.
from .browser_env import ensure_browsers_path  # noqa: E402

ensure_browsers_path()

EXTRA_STEALTH_JS = """
// Patch navigator.webdriver to undefined (not false)
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

// Patch chrome runtime
if (!window.chrome) { window.chrome = {}; }
if (!window.chrome.runtime) {
    window.chrome.runtime = {
        connect: function() {},
        sendMessage: function() {},
        id: undefined
    };
}
window.chrome.loadTimes = function() {
    return {
        commitLoadTime: Date.now() / 1000,
        connectionInfo: "h2",
        finishDocumentLoadTime: Date.now() / 1000,
        finishLoadTime: Date.now() / 1000,
        firstPaintAfterLoadTime: 0,
        firstPaintTime: Date.now() / 1000,
        navigationType: "Other",
        npnNegotiatedProtocol: "h2",
        requestTime: Date.now() / 1000 - 0.3,
        startLoadTime: Date.now() / 1000 - 0.3,
        wasAlternateProtocolAvailable: false,
        wasFetchedViaSpdy: true,
        wasNpnNegotiated: true
    };
};
window.chrome.csi = function() {
    return {
        onloadT: Date.now(),
        startE: Date.now() - 300,
        pageT: 300
    };
};

// Patch permissions API
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) =>
    parameters.name === 'notifications'
        ? Promise.resolve({state: Notification.permission})
        : originalQuery(parameters);

// Patch plugins/mimeTypes to look like a real browser
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const plugins = [
            {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format'},
            {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: ''},
            {name: 'Native Client', filename: 'internal-nacl-plugin', description: ''}
        ];
        plugins.refresh = () => {};
        return plugins;
    }
});

Object.defineProperty(navigator, 'mimeTypes', {
    get: () => {
        const mimeTypes = [
            {type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format',
             enabledPlugin: {name: 'Chrome PDF Plugin'}},
            {type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format',
             enabledPlugin: {name: 'Chrome PDF Viewer'}}
        ];
        mimeTypes.refresh = () => {};
        return mimeTypes;
    }
});

// Hide automation indicators in iframe contexts
try {
    for (let i = 0; i < window.frames.length; i++) {
        try {
            Object.defineProperty(window.frames[i].navigator, 'webdriver', {get: () => undefined});
        } catch(e) {}
    }
} catch(e) {}
"""

REALISTIC_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1680, "height": 1050},
    {"width": 2560, "height": 1440},
]

USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
]


def _random_viewport() -> dict[str, int]:
    return random.choice(REALISTIC_VIEWPORTS)


def _random_user_agent() -> str:
    return random.choice(USER_AGENTS)


def _add_human_delay(low: float = 0.3, high: float = 1.5) -> None:
    time.sleep(random.uniform(low, high))


def _safe_page_content(page: Any, max_attempts: int = 12) -> str:
    """Read page HTML, retrying when Playwright reports navigation in progress."""
    last: Optional[Exception] = None
    for _ in range(max_attempts):
        try:
            return page.content()
        except Exception as e:
            last = e
            msg = str(e).lower()
            if "navigating" in msg or "changing the content" in msg:
                try:
                    page.wait_for_timeout(400)
                except Exception:
                    time.sleep(0.4)
                continue
            raise
    if last:
        raise last
    raise RuntimeError("Could not read page HTML (navigation did not settle).")


def simulate_human_behavior(page: Any) -> None:
    """Move mouse randomly and scroll a bit to look human."""
    try:
        vp = page.viewport_size or {"width": 1280, "height": 720}
        x = random.randint(100, max(200, vp["width"] - 200))
        y = random.randint(100, max(200, vp["height"] - 200))
        page.mouse.move(x, y)
        _add_human_delay(0.1, 0.4)

        page.mouse.move(
            x + random.randint(-80, 80),
            y + random.randint(-80, 80),
            steps=random.randint(5, 15),
        )
        _add_human_delay(0.2, 0.6)

        page.evaluate("window.scrollBy(0, %d)" % random.randint(50, 300))
        _add_human_delay(0.3, 0.8)
    except Exception:
        pass


def _try_click_turnstile(page: Any) -> None:
    """Attempt to find and click a Cloudflare Turnstile checkbox."""
    try:
        turnstile_selectors = [
            'iframe[src*="challenges.cloudflare.com"]',
            'iframe[src*="turnstile"]',
            "#turnstile-wrapper iframe",
            ".cf-turnstile iframe",
        ]
        for sel in turnstile_selectors:
            frame_el = page.query_selector(sel)
            if frame_el:
                frame = frame_el.content_frame()
                if frame:
                    _add_human_delay(0.5, 1.5)
                    checkbox = frame.query_selector(
                        'input[type="checkbox"], .ctp-checkbox-label, #challenge-stage'
                    )
                    if checkbox:
                        bbox = checkbox.bounding_box()
                        if bbox:
                            page.mouse.click(
                                bbox["x"] + bbox["width"] / 2 + random.uniform(-3, 3),
                                bbox["y"] + bbox["height"] / 2 + random.uniform(-3, 3),
                            )
                            _add_human_delay(1.0, 3.0)
                            return
                    frame.click("body", position={"x": 20, "y": 20})
                    _add_human_delay(1.0, 3.0)
                    return
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Strategy 1: playwright-stealth (recommended first attempt)
# ---------------------------------------------------------------------------

def create_stealth_browser(
    playwright_instance: Any,
    *,
    headless: bool = True,
    proxy: Optional[dict[str, str]] = None,
    user_agent: Optional[str] = None,
) -> tuple[Any, Any]:
    """
    Launch a Chromium browser with playwright-stealth applied.

    Returns (browser, context). All pages created from the context
    inherit stealth patches automatically.

    Requires: pip install playwright-stealth
    """
    ua = user_agent or _random_user_agent()
    vp = _random_viewport()
    launch_kwargs: dict[str, Any] = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    }
    if proxy:
        launch_kwargs["proxy"] = proxy

    stealth = None
    try:
        from playwright_stealth import Stealth
        stealth = Stealth()
        logger.info("playwright-stealth loaded (Stealth class)")
    except ImportError:
        logger.warning(
            "playwright-stealth not installed. "
            "Install with: pip install playwright-stealth"
        )

    browser = playwright_instance.chromium.launch(**launch_kwargs)

    context = browser.new_context(
        user_agent=ua,
        viewport=vp,
        locale="en-US",
        timezone_id="America/New_York",
        color_scheme="light",
        has_touch=False,
        is_mobile=False,
        java_script_enabled=True,
        extra_http_headers={
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        },
    )

    if stealth is not None:
        try:
            stealth.apply_stealth_sync(context)
        except Exception:
            try:
                for page in context.pages:
                    from playwright_stealth import stealth_sync
                    stealth_sync(page)
            except Exception:
                pass

    context.add_init_script(EXTRA_STEALTH_JS)

    logger.info(
        "Stealth browser ready (headless=%s, viewport=%dx%d)",
        headless, vp["width"], vp["height"],
    )
    return browser, context


# ---------------------------------------------------------------------------
# Strategy 2: camoufox (Firefox-based, C++ fingerprint injection)
# ---------------------------------------------------------------------------

def create_camoufox_browser(
    *,
    headless: bool = True,
    proxy: Optional[dict[str, str]] = None,
    humanize: bool = True,
    geoip: bool = True,
) -> tuple[Any, Any]:
    """
    Launch a Camoufox browser (anti-detect Firefox).

    Returns (camoufox_context_manager, page).
    Caller must keep the context manager alive while using the page.

    ``humanize`` adds human-like cursor movement and ``geoip`` aligns the spoofed
    locale/timezone/WebRTC to the exit IP — both materially improve the Cloudflare
    pass rate, which is the whole reason camoufox is preferred over Chromium here.

    Requires: pip install camoufox[geoip] && python -m camoufox fetch
    """
    from camoufox.sync_api import Camoufox

    kwargs: dict[str, Any] = {"headless": headless, "humanize": humanize, "geoip": geoip}
    if proxy:
        kwargs["proxy"] = proxy

    cm = Camoufox(**kwargs)
    browser = cm.__enter__()
    page = browser.new_page()
    logger.info("Camoufox browser ready (headless=%s)", headless)
    return cm, page


# ---------------------------------------------------------------------------
# Cloudflare wait / challenge handling
# ---------------------------------------------------------------------------

def wait_for_cloudflare(
    page: Any,
    timeout: float = 45,
    log_fn=None,
) -> bool:
    """
    If the current page is a Cloudflare challenge, wait for it to clear.

    Tries clicking Turnstile checkboxes automatically and simulates
    human behavior. Returns True if the challenge cleared (or there was
    no challenge), False if it timed out.
    """
    _log = log_fn or logger.info
    html = _safe_page_content(page)

    if not is_cloudflare_challenge(html):
        return True

    _log("Cloudflare challenge detected — attempting automatic bypass...")
    deadline = time.time() + timeout
    attempt = 0

    while time.time() < deadline:
        attempt += 1
        simulate_human_behavior(page)
        _try_click_turnstile(page)

        page.wait_for_timeout(random.randint(2000, 4000))
        html = _safe_page_content(page)

        if not is_cloudflare_challenge(html):
            elapsed = time.time() - (deadline - timeout)
            _log(f"Cloudflare challenge cleared after {elapsed:.1f}s")
            return True

        if attempt % 3 == 0:
            remaining = deadline - time.time()
            _log(
                f"Still waiting for Cloudflare (attempt {attempt}, {remaining:.0f}s remaining)..."
            )

    _log(f"Cloudflare challenge did NOT clear within {int(timeout)}s.")
    return False


def fetch_with_stealth(
    page: Any,
    url: str,
    *,
    wait_until: str = "domcontentloaded",
    nav_timeout: int = 60_000,
    cf_timeout: float = 45,
    log_fn=None,
) -> str:
    """
    Navigate to a URL, handle Cloudflare, and return the page HTML.

    Combines page.goto + wait_for_cloudflare + human simulation
    into a single call for convenience.
    """
    _log = log_fn or logger.info
    _add_human_delay(0.3, 1.0)

    response = page.goto(url, wait_until=wait_until, timeout=nav_timeout)
    status = getattr(response, "status", None)
    if status in (403, 404):
        raise RuntimeError(f"HTTP {status} for {url}")
    page.wait_for_timeout(random.randint(800, 2000))

    if not wait_for_cloudflare(page, timeout=cf_timeout, log_fn=log_fn):
        raise RuntimeError(
            f"Cloudflare challenge did not clear for {url}. "
            "Try running with headless=False, using a different strategy, "
            "or adding a proxy."
        )

    simulate_human_behavior(page)
    return _safe_page_content(page)


def fetch_camoufox(
    page: Any,
    url: str,
    *,
    wait_until: str = "domcontentloaded",
    nav_timeout: int = 60_000,
    cf_timeout: float = 45,
    log_fn=None,
) -> str:
    """Navigate a Camoufox page and return HTML once any Cloudflare challenge clears.

    Leaner than :func:`fetch_with_stealth`: it relies on Camoufox's own anti-detect
    fingerprint + humanization to clear Cloudflare's managed challenge and only
    polls page content until it stops looking like a challenge (or ``cf_timeout``
    elapses). No turnstile-clicking or extra mouse simulation — those both slow the
    fetch ~10x and are unnecessary for Camoufox, which clears the challenge cold.
    """
    _log = log_fn or logger.info
    response = page.goto(url, wait_until=wait_until, timeout=nav_timeout)
    status = getattr(response, "status", None)
    if status in (403, 404):
        raise RuntimeError(f"HTTP {status} for {url}")

    deadline = time.time() + cf_timeout
    html = _safe_page_content(page)
    while is_cloudflare_challenge(html) and time.time() < deadline:
        page.wait_for_timeout(1500)
        html = _safe_page_content(page)
    return html


# ---------------------------------------------------------------------------
# Auto-select best available strategy
# ---------------------------------------------------------------------------

def detect_available_strategies() -> list[str]:
    """Return list of available bypass strategies, ordered by preference."""
    available = []

    try:
        from playwright_stealth import Stealth  # noqa: F401
        available.append("stealth")
    except ImportError:
        pass

    try:
        from camoufox.sync_api import Camoufox  # noqa: F401
        available.append("camoufox")
    except ImportError:
        pass

    if not available:
        available.append("patched")

    return available


def print_setup_help() -> None:
    """Print installation instructions for bypass strategies."""
    print(
        "\n"
        "=== Cloudflare Bypass Setup ===\n"
        "\n"
        "Install at least one strategy (recommended: install both):\n"
        "\n"
        "  Strategy 1 — playwright-stealth (recommended first):\n"
        "    pip install playwright-stealth\n"
        "\n"
        "  Strategy 2 — camoufox (strongest, Firefox-based):\n"
        "    pip install camoufox[geoip]\n"
        "    python -m camoufox fetch\n"
        "\n"
        "Both strategies require Playwright:\n"
        "    pip install playwright\n"
        "    playwright install chromium    # for strategy 1\n"
        "\n"
        "Tips:\n"
        "  - If headless mode is still blocked, try headless=False\n"
        "  - Adding a residential proxy dramatically improves success rates\n"
        "  - Increase delay between requests to 3-5 seconds\n"
    )
