"""BH-2 regression tests: cached Cloudflare challenges are cache misses."""

from __future__ import annotations

from pathlib import Path

from webnovel_scraper import request_manager as rm
from webnovel_scraper.request_manager import RequestManager


FIXTURES = Path(__file__).resolve().parents[1] / "test-files"

CHALLENGE = (FIXTURES / "wnd_cloudflare_challenge.html").read_text(encoding="utf-8")
REAL_PAYLOAD = (
    "<!doctype html><html><head><title>Chapter 1</title></head><body>"
    '<script src="/cdn-cgi/challenge-platform/h/g/orchestrate/jsch/v1"></script>'
    "<div id='article'><p>"
    + ("Real chapter prose with enough body text to pass payload detection. " * 8)
    + "</p></div></body></html>"
)


def write_pair(mgr, url: str, html: str) -> Path:
    """An entry with a well-formed provenance record whose bytes may still be unusable — what a
    cache saved before a content check existed looks like (v0.3.0 03.2b)."""
    import hashlib
    import json

    path = mgr.cache_path_for(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = html.encode("utf-8")
    path.write_bytes(data)
    path.with_suffix(".json").write_text(json.dumps({
        "schema_version": 1, "kind": "html-cache-entry", "url": url, "final_url": url,
        "host": url.split("/")[2], "novel_slug": mgr.slug, "fetched_utc": "2026-10-18T00:00:00Z",
        "byte_len": len(data), "sha256": hashlib.sha256(data).hexdigest(), "classification": "valid",
        "strategy": "http", "origin": "network", "app_version": "0.2.1"}), encoding="utf-8")
    return path


def test_cached_cloudflare_challenge_is_treated_as_cache_miss(tmp_path) -> None:
    url = "https://example.test/chapter-1"
    logs: list[str] = []
    mgr = RequestManager("slug", cache_root=tmp_path, log_fn=logs.append)
    write_pair(mgr, url, CHALLENGE)
    calls: list[str] = []

    def fake_uncached(_url: str, strategy: str) -> str:
        calls.append(strategy)
        return REAL_PAYLOAD

    mgr._fetch_uncached_strategy = fake_uncached  # type: ignore[method-assign]

    assert mgr.fetch_html(url) == REAL_PAYLOAD
    assert calls == [rm.FETCH_STRATEGY_HTTP]
    assert mgr.last_fetch_info.from_cache is False
    assert mgr.last_fetch_info.classification == "success"
    assert any("Cloudflare challenge" in line for line in logs)


def test_cached_real_payload_is_still_returned_as_cache_hit(tmp_path) -> None:
    url = "https://example.test/chapter-2"
    mgr = RequestManager("slug", cache_root=tmp_path)
    assert mgr.cache.store(url, REAL_PAYLOAD, strategy="http")

    def explode(_url: str, _strategy: str) -> str:
        raise AssertionError("network path must not run for a real cache hit")

    mgr._fetch_uncached_strategy = explode  # type: ignore[method-assign]

    assert mgr.fetch_html(url) == REAL_PAYLOAD
    assert mgr.last_fetch_info.from_cache is True
    assert mgr.last_fetch_info.classification == "cache"


def test_refetch_after_cached_challenge_overwrites_cache_with_real_payload(tmp_path) -> None:
    url = "https://example.test/chapter-3"
    mgr = RequestManager("slug", cache_root=tmp_path)
    cache_path = write_pair(mgr, url, CHALLENGE)
    calls: list[tuple[str, str]] = []

    def fake_uncached(seen_url: str, strategy: str) -> str:
        calls.append((seen_url, strategy))
        return REAL_PAYLOAD

    mgr._fetch_uncached_strategy = fake_uncached  # type: ignore[method-assign]

    assert mgr.fetch(url, use_browser=False) == REAL_PAYLOAD
    assert calls == [(url, rm.FETCH_STRATEGY_HTTP)]
    assert cache_path.read_text(encoding="utf-8") == REAL_PAYLOAD
    assert mgr.last_fetch_info.from_cache is False
    # ... and the replacement is itself a valid entry on the next read
    assert mgr.fetch(url, use_browser=False) == REAL_PAYLOAD
    assert mgr.last_fetch_info.from_cache is True

