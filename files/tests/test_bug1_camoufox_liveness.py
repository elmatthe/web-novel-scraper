"""0.2.0 live-test BUG-1 — camoufox page/context liveness guard + within-attempt
recreate-and-retry.

All offline/deterministic — no real browser launch, no network, no real sleeps.
The camoufox engine is mocked at the ``cf_bypass`` seam (``create_camoufox_browser``
+ ``fetch_camoufox``) so the tests exercise the REAL RequestManager
liveness/recreate/warm-up logic without ever launching Firefox.

Maps to the Phase C TESTS checklist:
  * dead cached page detected by the liveness check → ``_reset_camoufox`` → the
    navigation succeeds on the recreated page (single recreate).
  * page dies BETWEEN the liveness check and the goto (mid-attempt close) →
    caught, reset, retry-within-attempt succeeds.
  * two consecutive dead pages → the second attempt also fails → raises normally,
    no infinite recreate loop (exactly one recreate per rung).
  * warm-up hits a 'page closed' → host NOT marked warmed, dead page NOT left in
    cache, ``_reset_camoufox`` called.
  * healthy page (``is_closed()`` → False, the FWN ch-102 happy path) → the
    liveness check NEVER triggers a recreate.
  * the liveness helper + the closed-error detector behave on edge inputs.
  * a successful camoufox attempt with one mid-attempt recreate returns the
    correct fetched content.
  * ScrapeCancelled still re-raises through the warm-up path (Phase B regression).

NOTE on the liveness helper's missing-method default: a page object that does NOT
expose ``is_closed`` is treated as ALIVE (not dead). This is deliberate and
load-bearing — every real camoufox/Playwright page exposes ``is_closed()`` (→
``False`` when healthy), and existing offline tests cache a bare ``object()`` as
the page; treating a missing method as "alive" is the only choice that lets the
guard NEVER spuriously recreate a healthy page while still recreating one that is
*positively* closed. (The Phase C plan sketched "returns False on a no-method
object"; that would recreate every cached fake/healthy page lacking the
introspection hook and break the existing object()-reuse suite, so the safe
default is used and documented here instead.)
"""

from __future__ import annotations

import pytest

from webnovel_scraper import cf_bypass
from webnovel_scraper import request_manager as rm
from webnovel_scraper.request_manager import RequestManager

_CLEAN = (
    "<html><body><div class='txt'>"
    "<p>A real chapter body paragraph, long enough to render.</p>"
    "<p>A second paragraph of prose for the chapter body.</p>"
    "</div></body></html>"
)
_WARM = "<html><body><h1>FreeWebNovel</h1></body></html>"

# The canonical Playwright/Camoufox teardown message the detector keys on.
_CLOSED_MSG = "Target page, context or browser has been closed"


class _PageClosed(RuntimeError):
    """Stand-in for the Playwright 'target closed' error (detected by message)."""


def _closed() -> _PageClosed:
    return _PageClosed(_CLOSED_MSG)


class _Page:
    """A camoufox page whose ``is_closed()`` is scriptable."""

    def __init__(self, *, closed: bool = False) -> None:
        self._closed = closed

    def is_closed(self) -> bool:
        return self._closed


class _SpyCM:
    """A camoufox context manager that records when it is torn down."""

    def __init__(self) -> None:
        self.exited = 0

    def __exit__(self, *exc) -> None:  # noqa: ANN002
        self.exited += 1


class _Harness:
    """Wires the cf_bypass seam: ``create_camoufox_browser`` mints a fresh alive
    page each call and ``fetch_camoufox`` delegates to a scriptable behaviour so a
    test can make warm-up or the chapter goto die (and recover)."""

    def __init__(self, behavior) -> None:
        self.behavior = behavior
        self.created: list[_Page] = []
        self.cms: list[_SpyCM] = []
        self.fetch_urls: list[str] = []

    def install(self, monkeypatch) -> None:
        outer = self

        def create(*, headless, **_k):  # noqa: ANN001
            cm = _SpyCM()
            page = _Page()
            outer.cms.append(cm)
            outer.created.append(page)
            return cm, page

        def fetch(page, url, **_k):  # noqa: ANN001
            idx = len(outer.fetch_urls)
            outer.fetch_urls.append(url)
            return outer.behavior(page, url, idx)

        monkeypatch.setattr(cf_bypass, "create_camoufox_browser", create)
        monkeypatch.setattr(cf_bypass, "fetch_camoufox", fetch)


def _is_chapter(url: str) -> bool:
    return url.rstrip("/").endswith("/ch")


# ── unit: the liveness helper + the closed-error detector ────────────────────
def test_page_is_alive_edge_inputs() -> None:
    assert rm._page_is_alive(None) is False  # nothing to reuse

    class _Open:
        def is_closed(self):
            return False

    class _Closed:
        def is_closed(self):
            return True

    class _Raises:
        def is_closed(self):
            raise RuntimeError(_CLOSED_MSG)

    assert rm._page_is_alive(_Open()) is True
    assert rm._page_is_alive(_Closed()) is False
    # A raising introspection call is itself proof the target is gone → dead.
    assert rm._page_is_alive(_Raises()) is False
    # A page object without ``is_closed`` is treated as ALIVE and never raises
    # AttributeError — see the module docstring's note on the safe default.
    assert rm._page_is_alive(object()) is True


def test_is_page_closed_error_detection() -> None:
    assert rm._is_page_closed_error(Exception(_CLOSED_MSG)) is True
    assert rm._is_page_closed_error(RuntimeError("Browser has been closed")) is True
    assert rm._is_page_closed_error(RuntimeError("the Page has been closed")) is True
    assert rm._is_page_closed_error(ValueError("HTTP 500 server error")) is False
    assert rm._is_page_closed_error(rm.ScrapeCancelled("stop")) is False


# ── 1. dead cached page → recreate within the attempt → success ──────────────
def test_dead_cached_page_is_recreated_then_succeeds(tmp_path, monkeypatch) -> None:
    h = _Harness(lambda page, url, idx: _CLEAN)
    h.install(monkeypatch)
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)

    # Seed a positively-closed cached page + a spy browser the reset must tear down.
    dead_cm = _SpyCM()
    mgr._cf_cm = dead_cm
    mgr._cf_page = _Page(closed=True)

    html = mgr._fetch_camoufox_once("https://fwn/ch", fresh_context=False)

    assert html == _CLEAN
    assert dead_cm.exited == 1            # the dead browser was torn down
    assert len(h.created) == 1           # exactly one fresh browser built
    assert mgr._cf_page is h.created[0]  # the fresh, alive page is now cached


# ── 2. page dies BETWEEN the liveness check and the chapter goto ─────────────
def test_page_dies_mid_attempt_then_retry_succeeds(tmp_path, monkeypatch) -> None:
    state = {"chapter_failed": False}

    def behavior(page, url, idx):
        if _is_chapter(url) and not state["chapter_failed"]:
            state["chapter_failed"] = True
            raise _closed()           # dies on the FIRST chapter goto
        return _CLEAN if _is_chapter(url) else _WARM

    h = _Harness(behavior)
    h.install(monkeypatch)
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)

    # Cached page is ALIVE at the liveness check, then dies during the goto.
    seeded_cm = _SpyCM()
    mgr._cf_cm = seeded_cm
    mgr._cf_page = _Page(closed=False)

    html = mgr._fetch_camoufox_once("https://fwn/ch", fresh_context=False)

    assert html == _CLEAN
    assert seeded_cm.exited == 1   # the mid-attempt-closed browser was reset
    assert len(h.created) == 1     # exactly ONE recreate within the rung


# ── 3. two consecutive dead pages → raise, no infinite recreate loop ─────────
def test_two_consecutive_dead_pages_raise_without_looping(tmp_path, monkeypatch) -> None:
    def behavior(page, url, idx):
        if _is_chapter(url):
            raise _closed()           # chapter goto ALWAYS dies
        return _WARM

    h = _Harness(behavior)
    h.install(monkeypatch)
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)

    seeded_cm = _SpyCM()
    mgr._cf_cm = seeded_cm
    mgr._cf_page = _Page(closed=False)

    with pytest.raises(_PageClosed):
        mgr._fetch_camoufox_once("https://fwn/ch", fresh_context=False)

    # One recreate only: seeded browser reset once, exactly one fresh build, and
    # the chapter goto was attempted exactly twice (no unbounded loop).
    assert seeded_cm.exited == 1
    assert len(h.created) == 1
    assert sum(1 for u in h.fetch_urls if _is_chapter(u)) == 2


# ── 4. warm-up hits a closed page → not warmed, not cached, reset called ─────
def test_warmup_closed_page_resets_and_does_not_mark_warmed(tmp_path, monkeypatch) -> None:
    def behavior(page, url, idx):
        raise _closed()               # warm-up origin GET dies

    h = _Harness(behavior)
    h.install(monkeypatch)
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)

    seeded_cm = _SpyCM()
    page = _Page(closed=False)
    mgr._cf_cm = seeded_cm
    mgr._cf_page = page

    with pytest.raises(_PageClosed):
        mgr._warm_camoufox_session(page, "https://fwn/ch")

    assert "fwn" not in mgr._cf_warmed_hosts   # NOT marked warmed
    assert mgr._cf_page is None                # dead page NOT left cached
    assert mgr._cf_cm is None
    assert seeded_cm.exited == 1               # _reset_camoufox ran


def test_warmup_non_closed_failure_is_swallowed_and_marks_warmed(tmp_path, monkeypatch) -> None:
    """Regression: a NON-closed warm-up failure stays best-effort (swallowed +
    host marked warmed), unchanged from Phase B — only the closed case recreates."""
    def behavior(page, url, idx):
        raise RuntimeError("transient warm-up hiccup")

    h = _Harness(behavior)
    h.install(monkeypatch)
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)
    page = _Page(closed=False)
    mgr._cf_page = page

    mgr._warm_camoufox_session(page, "https://fwn/ch")  # does NOT raise

    assert "fwn" in mgr._cf_warmed_hosts
    assert mgr._cf_page is page   # not reset on a non-closed failure


# ── 5. healthy page → liveness NEVER triggers a recreate ─────────────────────
def test_healthy_page_is_reused_never_recreated(tmp_path, monkeypatch) -> None:
    h = _Harness(lambda page, url, idx: _CLEAN if _is_chapter(url) else _WARM)
    h.install(monkeypatch)
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)

    h1 = mgr._fetch_camoufox_once("https://fwn/ch", fresh_context=False)
    h2 = mgr._fetch_camoufox_once("https://fwn/ch", fresh_context=False)
    h3 = mgr._fetch_camoufox_once("https://fwn/ch", fresh_context=False)

    assert h1 == h2 == h3 == _CLEAN
    assert len(h.created) == 1            # built once, reused — never recreated
    assert h.cms[0].exited == 0          # the healthy browser was never reset


# ── 6. successful attempt with one mid-attempt recreate returns right content ─
def test_recreate_attempt_returns_correct_body_via_dispatch(tmp_path, monkeypatch) -> None:
    marker = "<html><body><div class='txt'><p>WND chapter prose body.</p></div></body></html>"
    state = {"failed": False}

    def behavior(page, url, idx):
        if _is_chapter(url) and not state["failed"]:
            state["failed"] = True
            raise _closed()
        return marker if _is_chapter(url) else _WARM

    h = _Harness(behavior)
    h.install(monkeypatch)
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)

    # Drive through the strategy dispatch (the real ladder rung entry point).
    html = mgr._fetch_uncached_strategy("https://wnd/ch", rm.FETCH_STRATEGY_CAMOUFOX)
    assert html == marker


# ── 7. ScrapeCancelled still re-raises through warm-up (Phase B regression) ───
def test_scrapecancelled_still_reraises_through_warmup(tmp_path, monkeypatch) -> None:
    def behavior(page, url, idx):
        raise rm.ScrapeCancelled("stop")

    h = _Harness(behavior)
    h.install(monkeypatch)
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)
    page = _Page(closed=False)
    mgr._cf_page = page

    with pytest.raises(rm.ScrapeCancelled):
        mgr._warm_camoufox_session(page, "https://fwn/ch")
    # Cancel is NOT a 'closed' error → host not warmed, but no recreate either.
    assert "fwn" not in mgr._cf_warmed_hosts
