"""Phase 1 (0.2.0) — typed fetch failures + body-first classification + fast /
HTTP-probe budget split + the shared host limiter + ScrapeJob run-config +
per-manager timeouts + last_fetch_info.

All offline + deterministic: faked sessions/pages, an injected fake monotonic
clock + sleep, no real network and no real browser launch. These lock in the
mechanisms the rescue pool (Phase 2) and the pipeline conductor + breaker
(Phase 3) build on — without spinning up more than one (conceptual) lane.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from webnovel_scraper import cf_bypass
from webnovel_scraper import request_manager as rm
from webnovel_scraper.adapters.freewebnovel import FreeWebNovelAdapter
from webnovel_scraper.cloudflare_detection import has_real_payload
from webnovel_scraper.host_rate_limiter import HostRateLimiter, normalize_host
from webnovel_scraper.models import (
    ChapterMeta,
    OutputMode,
    ScrapeJob,
    SiteSpec,
    runtime_site_spec,
)
from webnovel_scraper.request_manager import RequestManager, ScrapeCancelled


# ── fixtures / fakes ──────────────────────────────────────────────────────────
GENUINE_CHALLENGE = (
    "<!DOCTYPE html><html><head><title>Just a moment...</title>"
    '<script src="/cdn-cgi/challenge-platform/h/g/orchestrate/jsch/v1"></script>'
    "</head><body><div class='cf-browser-verification'>cf_chl_opt</div></body></html>"
)
CLEARED = (
    "<!DOCTYPE html><html><head><title>Chapter 1</title></head><body>"
    "<div id='article'><p>" + ("Real chapter prose continues here. " * 8) + "</p>"
    "</div></body></html>"
)


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "", headers: dict | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.headers: dict = {}

    def get(self, url: str, **kwargs) -> _FakeResponse:
        return self._response

    def close(self) -> None:
        pass


class _Page403:
    """A fake browser page whose navigation returns HTTP 403 and whose content is
    a fixed body — to prove a 403 is NOT short-circuited before the CF wait."""

    def __init__(self, body: str) -> None:
        self._body = body
        self.gotos: list[str] = []

    def goto(self, url, **_k):  # noqa: ANN001
        self.gotos.append(url)

        class _Resp:
            status = 403

        return _Resp()

    def wait_for_timeout(self, _ms) -> None:  # noqa: ANN001
        pass

    def content(self) -> str:
        return self._body


def _fake_clock():
    """Return (now_fn, sleep_fn) over a shared mutable monotonic clock."""
    clock = [0.0]
    sleeps: list[float] = []

    def now() -> float:
        return clock[0]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    return clock, sleeps, now, sleep


# ── single-lane constants (invariant #1) ─────────────────────────────────────
def test_single_lane_constants_present_and_capped() -> None:
    assert rm.RESCUE_WORKERS == 1
    assert rm.RESCUE_MAX_WORKERS == 1          # HARD cap in 0.2.0 (§9 deferred)
    assert rm.FAST_BROWSER_ATTEMPTS == 2
    assert rm.FAST_HTTP_PROBE_ATTEMPTS == 2
    assert rm.HOST_MIN_INTERVAL == 3.0
    assert rm.DEFAULT_DELAY == 3.0
    assert rm.RESCUE_MAX_ELAPSED_PER_CHAPTER == 180.0


# ── fast path raises a typed signal after the bounded browser budget ──────────
def test_fast_path_raises_challenge_after_browser_budget(tmp_path, monkeypatch) -> None:
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path, sleep_fn=lambda _s: None)
    strategies: list[str] = []

    def fake_strategy(url, strategy):
        strategies.append(strategy)
        raise rm.ChallengeFetchError("blocked")

    monkeypatch.setattr(mgr, "_fetch_uncached_strategy", fake_strategy)
    with pytest.raises(rm.ChallengeFetchError):
        mgr.fetch("https://fwn/ch", use_browser=True, fast_path=True)

    # Exactly the bounded camoufox budget — no stealth rung (that is rescue's job).
    assert strategies == [rm.FETCH_STRATEGY_CAMOUFOX, rm.FETCH_STRATEGY_CAMOUFOX]
    assert strategies.count(rm.FETCH_STRATEGY_CAMOUFOX) == rm.FAST_BROWSER_ATTEMPTS
    assert rm.FETCH_STRATEGY_PLAYWRIGHT_STEALTH not in strategies


def test_fast_path_http_probes_do_not_consume_browser_budget(tmp_path, monkeypatch) -> None:
    """With HTTP-first ON the two cheap probes are EXTRA — the camoufox budget is
    still spent in full afterwards (§3.2a), so HTTP-first can't starve the browser."""
    mgr = RequestManager(
        "s", use_cache=False, cache_root=tmp_path, try_http_first=True,
        sleep_fn=lambda _s: None,
    )
    strategies: list[str] = []

    def fake_strategy(url, strategy):
        strategies.append(strategy)
        raise rm.ChallengeFetchError("blocked")

    monkeypatch.setattr(mgr, "_fetch_uncached_strategy", fake_strategy)
    with pytest.raises(rm.ChallengeFetchError):
        mgr.fetch("https://fwn/ch", use_browser=True, fast_path=True)

    assert strategies == [
        rm.FETCH_STRATEGY_HTTP,
        rm.FETCH_STRATEGY_CLOUDSCRAPER,
        rm.FETCH_STRATEGY_CAMOUFOX,
        rm.FETCH_STRATEGY_CAMOUFOX,
    ]
    # The browser budget is untouched by the probes.
    assert strategies.count(rm.FETCH_STRATEGY_CAMOUFOX) == rm.FAST_BROWSER_ATTEMPTS


# ── true 404 → NotFound, never retried / never hard ───────────────────────────
def test_true_404_is_notfound_and_never_retried(tmp_path, monkeypatch) -> None:
    sleeps: list[float] = []
    mgr = RequestManager(
        "s", use_cache=False, cache_root=tmp_path, max_retries=4,
        retry_jitter_ratio=0.0, sleep_fn=sleeps.append,
    )
    strategies: list[str] = []

    def fake_strategy(url, strategy):
        strategies.append(strategy)
        raise rm.NotFoundFetchError("HTTP 404 for x", status=404)

    monkeypatch.setattr(mgr, "_fetch_uncached_strategy", fake_strategy)
    with pytest.raises(rm.NotFoundFetchError):
        mgr.fetch("https://x/missing")

    assert strategies == [rm.FETCH_STRATEGY_HTTP]   # terminal on attempt 1
    assert sleeps == []                             # no backoff
    assert mgr.last_fetch_info.classification == "not_found"


def test_get_text_404_classifies_as_notfound(tmp_path, monkeypatch) -> None:
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)
    monkeypatch.setattr(mgr, "_session", _FakeSession(_FakeResponse(404, "")))
    with pytest.raises(rm.NotFoundFetchError):
        mgr._fetch_uncached_strategy("https://x/missing", rm.FETCH_STRATEGY_HTTP)


# ── CF-style 503 body → Challenge, not a generic 5xx transient ────────────────
def test_cf_503_body_classifies_as_challenge_not_transient(tmp_path, monkeypatch) -> None:
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)
    monkeypatch.setattr(mgr, "_session", _FakeSession(_FakeResponse(503, GENUINE_CHALLENGE)))
    with pytest.raises(rm.ChallengeFetchError) as excinfo:
        mgr._fetch_uncached_strategy("https://x/ch", rm.FETCH_STRATEGY_HTTP)
    # A CF body served with a 5xx must NOT be misread as a plain transient 5xx.
    assert not isinstance(excinfo.value, rm.TransientFetchError)


def test_plain_503_without_cf_body_is_transient(tmp_path, monkeypatch) -> None:
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)
    monkeypatch.setattr(
        mgr, "_session", _FakeSession(_FakeResponse(503, "<html><body>down</body></html>"))
    )
    with pytest.raises(rm.TransientFetchError):
        mgr._fetch_uncached_strategy("https://x/ch", rm.FETCH_STRATEGY_HTTP)


# ── browser-403 with a CF body → Challenge, and it does NOT exit before the wait ─
def test_browser_403_does_not_short_circuit_and_captures_cleared_content() -> None:
    page = _Page403(CLEARED)
    html = cf_bypass.fetch_camoufox(page, "https://fwn/ch", cf_timeout=5)
    # The 403 did NOT raise: we navigated, polled, and captured real content.
    assert page.gotos == ["https://fwn/ch"]
    assert html == CLEARED
    assert has_real_payload(html) is True


def test_browser_403_with_challenge_body_raises_challenge(tmp_path, monkeypatch) -> None:
    class _CM:
        def __exit__(self, *exc) -> None:
            return None

    page = _Page403(GENUINE_CHALLENGE)
    monkeypatch.setattr(
        cf_bypass, "create_camoufox_browser", lambda *, headless, **_k: (_CM(), page)
    )
    # cloudflare_timeout=0 → poll budget exhausted immediately with the challenge
    # still on the page → ChallengeFetchError (NOT NotFound, the 403 was not permanent).
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path, cloudflare_timeout=0.0)
    with pytest.raises(rm.ChallengeFetchError):
        mgr._fetch_camoufox_once("https://fwn/ch", fresh_context=False)
    assert page.gotos  # we DID navigate (did not bail before the goto/CF wait)


# ── fake-clock pacing: cannot breach the effective interval ───────────────────
def test_host_limiter_paces_at_effective_interval() -> None:
    clock, sleeps, now, sleep = _fake_clock()
    lim = HostRateLimiter(10.0, monotonic=now, sleep=sleep)
    times: list[float] = []
    for _ in range(3):
        lim.acquire("https://h/p")
        times.append(clock[0])
    # A 10s effective interval yields exactly 10s spacing — never closer.
    assert times == [0.0, 10.0, 20.0]


def test_host_limiter_positive_jitter_added_after_interval() -> None:
    clock, sleeps, now, sleep = _fake_clock()
    lim = HostRateLimiter(
        10.0, jitter_ratio=0.1, monotonic=now, sleep=sleep, random_fn=lambda: 1.0
    )
    lim.acquire("https://h/p")   # reserves 0 + 10 + (1.0 * 0.1 * 10) = 11.0
    lim.acquire("https://h/p")   # must wait to 11.0 (>= the bare interval)
    assert clock[0] == 11.0


def test_warmup_and_chapter_nav_are_both_gated(tmp_path, monkeypatch) -> None:
    clock, sleeps, now, sleep = _fake_clock()
    lim = HostRateLimiter(3.0, monotonic=now, sleep=sleep)
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path, host_limiter=lim)

    acquired: list[tuple[str, float]] = []
    real_acquire = lim.acquire

    def spy(url, **kwargs):
        real_acquire(url, **kwargs)
        acquired.append((url, clock[0]))

    monkeypatch.setattr(lim, "acquire", spy)
    # Never touch the wire: return a real-payload body so the first HTTP attempt wins.
    monkeypatch.setattr(mgr, "_get_text", lambda session, url: CLEARED)

    mgr.fetch("https://h/novel/ch-1", use_browser=False)

    # Two top-level navigations gated: the origin warm-up GET AND the chapter GET.
    assert len(acquired) == 2
    assert acquired[0][0].endswith("/")                 # warm-up to the origin
    assert acquired[1][0] == "https://h/novel/ch-1"     # the chapter
    # The chapter nav waited a full interval after the warm-up reserved its slot.
    assert acquired[1][1] - acquired[0][1] >= 3.0


def test_host_limiter_acquire_honors_cancel_event() -> None:
    ev = threading.Event()
    ev.set()
    lim = HostRateLimiter(3.0)
    with pytest.raises(ScrapeCancelled):
        lim.acquire("https://h/p", cancel_event=ev)


def test_normalize_host_keys() -> None:
    assert normalize_host("https://Example.com/a") == "example.com"
    assert normalize_host("https://example.com:8080/a") == "example.com:8080"
    assert normalize_host("https://example.com/a") == normalize_host("http://example.com/b")


# ── last_fetch_info reports cache vs network ──────────────────────────────────
def test_last_fetch_info_reports_cache_vs_network(tmp_path, monkeypatch) -> None:
    url = "https://h/ch"
    mgr = RequestManager("s", cache_root=tmp_path)   # use_cache defaults True

    monkeypatch.setattr(mgr, "_fetch_uncached_strategy", lambda u, s: "<html>ok</html>")
    mgr.fetch(url)
    assert mgr.last_fetch_info.from_cache is False
    assert mgr.last_fetch_info.classification == "success"

    # Second fetch is served from the cache written above — no network strategy runs.
    def explode(u, s):
        raise AssertionError("network must not be hit on a cache hit")

    monkeypatch.setattr(mgr, "_fetch_uncached_strategy", explode)
    mgr.fetch(url)
    assert mgr.last_fetch_info.from_cache is True
    assert mgr.last_fetch_info.classification == "cache"


# ── ScrapeJob.use_browser drives fetching; the catalog SiteSpec is untouched ──
def _spec(use_browser: bool) -> SiteSpec:
    return SiteSpec(
        novel_slug="x", novel_title="X", adapter_key="freewebnovel",
        display_name="FWN", enabled=True, url="https://fwn/novel/x",
        use_browser=use_browser,
    )


def _job(use_browser: bool) -> ScrapeJob:
    return ScrapeJob(
        novel_slug="x", adapter_key="freewebnovel", start=1, end=1, delay=3.0,
        output_mode=OutputMode.SEPARATE, use_cache=False, output_dir=Path("."),
        use_browser=use_browser,
    )


@pytest.mark.parametrize("use_browser", [True, False])
def test_job_use_browser_reaches_adapter_without_mutating_catalog_spec(use_browser) -> None:
    spec = _spec(use_browser=False)       # the catalog row is always browser-OFF here
    job = _job(use_browser=use_browser)
    runtime = runtime_site_spec(spec, job)

    assert runtime.use_browser is use_browser
    assert spec.use_browser is False      # the shared catalog row is NEVER mutated

    seen: dict[str, bool] = {}

    class _RM:
        def fetch(self, url, *, use_browser=False, **kwargs):
            seen["use_browser"] = use_browser
            return CLEARED

    adapter = FreeWebNovelAdapter(request_manager=_RM(), log=lambda _m: None)
    adapter.fetch_chapter(ChapterMeta(index=1, url="https://fwn/novel/x/chapter-1"), runtime)
    assert seen["use_browser"] is use_browser


# ── 429 sets a host cooldown for both lanes and is NOT a challenge ────────────
def test_429_sets_host_cooldown_and_is_not_a_challenge(tmp_path) -> None:
    clock, sleeps, now, sleep = _fake_clock()
    lim = HostRateLimiter(3.0, monotonic=now, sleep=sleep)

    primary = RequestManager(
        "s", use_cache=False, cache_root=tmp_path, host_limiter=lim,
        max_retries=3, retry_jitter_ratio=0.0, sleep_fn=lambda _s: None,
    )
    primary._session = _FakeSession(_FakeResponse(429, "", headers={"Retry-After": "50"}))

    with pytest.raises(rm.RateLimitedFetchError):
        primary.fetch("https://h/ch")

    # Terminal as a rate-limit, NOT a challenge — the breaker (Phase 3) ignores it.
    assert primary.last_fetch_info.classification == "rate_limited"

    # The shared limiter both lanes use now parks this host until the Retry-After.
    blocked = lim.blocked_until("https://h/ch")
    assert blocked >= 50.0

    # The rescue lane shares the SAME limiter, so its next nav observes the cooldown.
    before = clock[0]
    lim.acquire("https://h/ch")
    assert clock[0] >= 50.0
    assert clock[0] >= before


# ── ScrapeJob single-lane validation (invariant #1) ───────────────────────────
def test_scrapejob_defaults_single_lane_and_rejects_multi_worker() -> None:
    job = _job(use_browser=True)
    assert job.rescue_workers == 1
    assert job.request_timeout == 30.0
    with pytest.raises(ValueError):
        ScrapeJob(
            novel_slug="x", adapter_key="freewebnovel", start=1, end=1, delay=3.0,
            output_mode=OutputMode.SEPARATE, use_cache=False, output_dir=Path("."),
            rescue_workers=2,
        )


# ── explicit per-manager timeouts replace the module global ───────────────────
def test_explicit_per_manager_timeouts(tmp_path) -> None:
    mgr = RequestManager(
        "s", cache_root=tmp_path, http_timeout=12.5,
        browser_nav_timeout=20.0, cloudflare_timeout=33.0,
    )
    assert mgr._http_timeout == 12.5
    assert mgr._browser_nav_timeout_ms == 20_000     # seconds → ms
    assert mgr._cloudflare_timeout == 33.0


def test_get_text_uses_instance_http_timeout(tmp_path) -> None:
    seen: dict[str, float] = {}

    class _S:
        headers: dict = {}

        def get(self, url, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            return _FakeResponse(200, CLEARED)

    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path, http_timeout=7.0)
    mgr._get_text(_S(), "https://h/ch")
    assert seen["timeout"] == 7.0
