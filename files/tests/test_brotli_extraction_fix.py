"""Regression tests for the live FreeWebNovel body-extraction failure.

Two distinct defects were behind the "chapter 3+ FAILED: Could not extract body
paragraphs" symptom on the live Shadow Slave scrape:

  1a. BROTLI DECODE. The HTTP headers advertised ``Accept-Encoding: gzip,
      deflate, br`` but ``requests`` cannot decode brotli without the optional
      ``brotli`` package, so a brotli-encoded chapter page came back as
      U+FFFD-replacement-char garbage that yielded zero paragraphs. The fix drops
      ``br`` from the header and adds a fetch-layer garble guard so an undecodable
      response escalates the retry ladder instead of being cached.

  1b. MISCLASSIFICATION. An extraction-empty outcome (a fully-fetched,
      non-challenge page with no body) was routed through the Cloudflare/block
      path, triggering the pipeline's ``_Pacer`` auto-slowdown. It is now its own
      ``EmptyExtractionError`` failure class: recorded as a plain failure, never a
      block, and never swept.

All offline (fixtures + fakes); no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from webnovel_scraper import catalog, pipeline
from webnovel_scraper import request_manager as rm
from webnovel_scraper.adapters.freewebnovel import FreeWebNovelAdapter
from webnovel_scraper.models import (
    ChapterContent,
    ChapterMeta,
    EmptyExtractionError,
    OutputMode,
    ScrapeJob,
)
from webnovel_scraper.request_manager import RequestManager

FIXTURES = Path(__file__).resolve().parents[2] / "files" / "test-files"
SS_SPEC = catalog.get_spec("shadow-slave", "freewebnovel")


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ── 1a: the current FWN markup extracts (proves it was decode, not selectors) ──
def test_current_fwn_chapter_extracts_nonempty_body() -> None:
    """The real, correctly-decoded current FreeWebNovel chapter-3 page (saved as a
    fixture) extracts a full body — the adapter's selectors were never the
    problem; the live failure was a brotli mis-decode upstream."""
    html = _fixture("fwn_chapter_current_ok.html")
    adapter = FreeWebNovelAdapter(request_manager=None)
    content = adapter._extract_chapter(
        html, ChapterMeta(index=3, url="https://freewebnovel.com/novel/shadow-slave/chapter-3")
    )
    assert content.paragraphs, "current FWN markup must yield body paragraphs"
    assert len(content.paragraphs) > 20
    assert content.heading == "Chapter 3: The Strings of Fate"


# ── 1a: the garble guard recognises a brotli-mis-decoded page ─────────────────
def test_brotli_garbage_fixture_is_detected_as_garbled() -> None:
    garbage = _fixture("fwn_chapter_brotli_garbage.html")
    clean = _fixture("fwn_chapter_current_ok.html")
    assert rm._looks_garbled(garbage) is True
    assert rm._looks_garbled(clean) is False


def test_accept_encoding_no_longer_requests_brotli() -> None:
    """Guard against the root cause regressing: never advertise brotli, which
    ``requests`` cannot decode without the optional package."""
    assert "br" not in rm.BROWSER_HEADERS["Accept-Encoding"]


def test_garbled_http_response_escalates_and_is_not_cached(tmp_path, monkeypatch) -> None:
    """A garbled (undecodable) HTTP response is treated as a retryable fetch
    failure — the ladder escalates and nothing is written to the cache."""
    garbage = _fixture("fwn_chapter_brotli_garbage.html")

    class _Resp:
        status_code = 200
        text = garbage

        def raise_for_status(self) -> None:
            pass

    class _Sess:
        headers: dict = {}

        def get(self, url, **kwargs):
            return _Resp()

        def close(self) -> None:
            pass

    mgr = RequestManager(
        "ss", cache_root=tmp_path, max_retries=0, retry_jitter_ratio=0.0,
        sleep_fn=lambda _s: None,
    )
    monkeypatch.setattr(mgr, "_session", _Sess())

    # The single HTTP attempt raises TransientFetchError internally (an undecodable
    # body, 0.2.0 §3.3) and, with no retries left, the ladder gives up with a
    # FetchError. Critically, the garbage was never cached.
    with pytest.raises(rm.FetchError):
        mgr.fetch("https://freewebnovel.com/novel/shadow-slave/chapter-3")
    assert not mgr.cache_path_for(
        "https://freewebnovel.com/novel/shadow-slave/chapter-3"
    ).exists()


def test_garbled_cache_entry_is_ignored_and_refetched(tmp_path) -> None:
    """A poisoned cache file (written before the brotli fix) is treated as a miss
    so the next run re-fetches clean instead of serving garbage."""
    url = "https://freewebnovel.com/novel/shadow-slave/chapter-3"
    mgr = RequestManager("ss", cache_root=tmp_path)
    garbage = _fixture("fwn_chapter_brotli_garbage.html")
    write_pair(mgr, url, garbage)                 # provenance intact, bytes unusable
    assert mgr._read_cache(url) is None           # garbled -> miss
    # The same garbage in the pre-0.3.0 layout is never copied or served either.
    legacy = mgr.legacy_cache_dir / mgr.cache_path_for(url).name
    mgr.cache_path_for(url).unlink()
    mgr.cache_path_for(url).with_suffix(".json").unlink()
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(garbage, encoding="utf-8")
    assert mgr._read_cache(url) is None
    assert not mgr.cache_path_for(url).exists()
    # A clean entry still reads back normally.
    assert mgr.cache.store(url, "<html><body><p>real chapter</p></body></html>", strategy="http")
    assert mgr._read_cache(url) is not None


def write_pair(mgr, url: str, html: str):
    """An entry with a well-formed provenance record whose bytes are still unusable."""
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


# ── 1b: an empty-extraction failure is NOT a block and is NOT swept ───────────
class _EmptyExtractionAdapter:
    """Fake adapter: every fetch raises EmptyExtractionError for one chapter."""

    def __init__(self, count: int, empty_index: int) -> None:
        self.count = count
        self.empty_index = empty_index
        self.fetched: list[int] = []
        self.warnings: list[str] = []

    def build_chapter_index(self, spec):
        return [
            ChapterMeta(index=n, url=f"https://freewebnovel.com/novel/shadow-slave/chapter-{n}")
            for n in range(1, self.count + 1)
        ]

    def fetch_chapter(self, meta, spec):
        self.fetched.append(meta.index)
        if meta.index == self.empty_index:
            raise EmptyExtractionError(
                f"Could not extract body paragraphs for chapter {meta.index}."
            )
        return ChapterContent(
            index=meta.index,
            title=f"Title {meta.index}",
            paragraphs=[
                f"A body paragraph for chapter {meta.index}, long enough to render."
            ],
        )


def _job(tmp_path, mode=OutputMode.SEPARATE, *, start=1, end=3) -> ScrapeJob:
    return ScrapeJob(
        novel_slug="shadow-slave",
        adapter_key="freewebnovel",
        start=start,
        end=end,
        delay=0.0,
        output_mode=mode,
        use_cache=False,
        output_dir=tmp_path,
    )


def test_empty_extraction_classified_as_extraction_failure_not_block(tmp_path) -> None:
    adapter = _EmptyExtractionAdapter(count=3, empty_index=2)
    log_lines: list[str] = []
    report = pipeline.run_scrape(
        _job(tmp_path),
        adapter=adapter,
        log=log_lines.append,
        sleep_fn=lambda _s: None,
    )

    # Recorded as a plain failure AND tracked as an extraction failure.
    assert report.failed == [2]
    assert report.extraction_failed == [2]
    # NOT treated as a Cloudflare block: the pacer never advanced (delay 0.0 would
    # have jumped to the 2.0s floor and counted a slowdown if misclassified).
    assert report.auto_slowdowns == 0
    assert report.effective_delay == 0.0
    # NOT swept: chapter 2 is fetched exactly once (no second-pass re-attempt).
    assert adapter.fetched == [1, 2, 3]
    assert report.rescued == []
    assert any("chapter 2 FAILED [EMPTY_BODY]" in line for line in log_lines)
    from webnovel_scraper.run_journal import JOURNAL_FILENAME, replay_journal
    [failure] = replay_journal(report.output_dir / JOURNAL_FILENAME).failures
    assert (failure["label"], failure["stage"], failure["reason_code"]) == ("2", "fetch", "EMPTY_BODY")


def test_empty_extraction_not_swept_in_single_mode(tmp_path) -> None:
    adapter = _EmptyExtractionAdapter(count=3, empty_index=2)
    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.SINGLE),
        adapter=adapter,
        log=lambda m: None,
        sleep_fn=lambda _s: None,
    )
    # Chapter 2 fetched once, never re-swept; the other two chapters still write.
    assert adapter.fetched == [1, 2, 3]
    assert report.extraction_failed == [2]
    assert report.auto_slowdowns == 0
    assert len(report.written) == 1  # single PDF with chapters 1 and 3
