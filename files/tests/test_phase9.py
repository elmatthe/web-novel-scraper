"""Phase 9 tests: live-scrape hardening. All offline (no network, mocked sleeps).

Covers:
  9A — the GUI inter-fetch delay control validates + binds to ScrapeJob.delay.
  9B — adaptive auto-slowdown raises the effective delay after repeated
       challenges and respects the ceiling (sleeps are mocked, never waited).
  9C — relentless per-chapter retry only gives up after the configured attempts
       *plus* a second-pass sweep; a permanent 404 short-circuits (no sweep).
  9D — the WebNovel post-redirect g_data page is extracted and is NOT mis-read as
       a Cloudflare challenge (it carries the ambient beacon); a real interstitial
       IS flagged and makes the fetch ladder escalate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from webnovel_scraper import catalog, pipeline
from webnovel_scraper import request_manager as rm
from webnovel_scraper import cf_bypass
from webnovel_scraper.adapters import webnovel_dynamic as wnd
from webnovel_scraper.adapters.webnovel_dynamic import WebNovelDynamicAdapter
from webnovel_scraper.models import ChapterContent, ChapterMeta, OutputMode, ScrapeJob
from webnovel_scraper.request_manager import RequestManager

FIXTURES = Path(__file__).resolve().parents[2] / "files" / "test-files"
NQ_SPEC = catalog.get_spec("the-noble-queen", "webnovel_dynamic")
ENABLED_NOVEL = "shadow-slave"
ENABLED_KEY = "freewebnovel"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _noop(*_a, **_k) -> None:
    return None


# ── Fakes ────────────────────────────────────────────────────────────────────
class FakeRM:
    """Serves fixture HTML by URL (or a default) and records what was fetched."""

    def __init__(self, html_by_url=None, default_html=None) -> None:
        self.html_by_url = html_by_url or {}
        self.default_html = default_html
        self.fetched: list[str] = []

    def fetch(self, url, *, use_browser=False, use_cache=None) -> str:
        self.fetched.append(url)
        if url in self.html_by_url:
            return self.html_by_url[url]
        if self.default_html is not None:
            return self.default_html
        raise KeyError(url)

    def start(self):
        return self

    def close(self):
        pass


class ScriptedAdapter:
    """A pipeline-facing fake adapter whose per-chapter failures are scripted.

    ``fail_until[index]`` = number of leading attempts that fail (transient block);
    ``permanent`` = indices that always raise an HTTP-404-style error (never swept).
    """

    def __init__(
        self, count: int, *, fail_until=None, permanent=None,
        fail_message="Cloudflare challenge still present",
    ) -> None:
        self.count = count
        self.fail_until = dict(fail_until or {})
        self.permanent = set(permanent or [])
        self.fail_message = fail_message
        self.calls: dict[int, int] = {}
        self.fetched: list[int] = []
        self.warnings: list[str] = []

    def build_chapter_index(self, spec):
        return [
            ChapterMeta(index=n, url=f"https://example/{n}", source_id=str(n))
            for n in range(1, self.count + 1)
        ]

    def fetch_chapter(self, meta, spec):
        self.fetched.append(meta.index)
        self.calls[meta.index] = self.calls.get(meta.index, 0) + 1
        if meta.index in self.permanent:
            raise RuntimeError(f"HTTP 404 for https://example/{meta.index}")
        if self.calls[meta.index] <= self.fail_until.get(meta.index, 0):
            raise RuntimeError(self.fail_message)
        return ChapterContent(
            index=meta.index,
            title=f"Title {meta.index}",
            paragraphs=[
                f"First body paragraph for chapter {meta.index}, long enough.",
                f"Second body paragraph for chapter {meta.index}, also prose.",
            ],
        )


def _job(tmp_path, mode=OutputMode.SEPARATE, *, start=1, end=5, chunk_size=10):
    return ScrapeJob(
        novel_slug=ENABLED_NOVEL,
        adapter_key=ENABLED_KEY,
        start=start,
        end=end,
        delay=0.0,
        output_mode=mode,
        use_cache=False,
        output_dir=tmp_path,
        chunk_size=chunk_size,
    )


def _pdfs(tmp_path):
    return sorted(p.name for p in tmp_path.glob("*.pdf"))


# ── 9A — GUI inter-fetch delay control ───────────────────────────────────────
def test_default_delay_is_nonnegative_float() -> None:
    import app

    value = float(app.DEFAULT_DELAY)
    assert value >= 0.0
    # Sensible anti-detection default (~2s), not zero.
    assert value >= 1.0


def test_gui_delay_validates_and_binds_to_job(monkeypatch) -> None:
    import app

    tkmod = pytest.importorskip("tkinter")
    try:
        win = app.ScraperApp()
    except tkmod.TclError as exc:  # no display (headless CI)
        pytest.skip(f"no Tk display available: {exc}")
    try:
        errors: list[tuple[str, str]] = []
        monkeypatch.setattr(
            app.messagebox, "showerror", lambda *a, **k: errors.append(a)
        )

        # A valid fractional delay is collected and is the value that becomes
        # ScrapeJob.delay in _on_start (start, end, delay, timeout, mode, chunk).
        win._delay_var.set("2.5")
        params = win._collect_params()
        assert params is not None
        assert params[2] == 2.5
        job = ScrapeJob(
            novel_slug="x", adapter_key="y", start=params[0], end=params[1],
            delay=params[2], output_mode=params[4], use_cache=True,
            output_dir=Path("."),
        )
        assert job.delay == 2.5

        # A negative delay is rejected (validation), not silently accepted.
        win._delay_var.set("-1")
        assert win._collect_params() is None
        assert errors  # an error dialog was raised
    finally:
        win.destroy()


# ── 9B — adaptive auto-slowdown ──────────────────────────────────────────────
def test_pacer_escalates_and_respects_ceiling() -> None:
    slept: list[float] = []
    pacer = pipeline._Pacer(
        2.0, multiplier=2.0, ceiling=10.0, floor=2.0, log=_noop,
        sleep_fn=slept.append,
    )
    assert pacer.current == 2.0

    pacer.register_block()           # 2.0 -> 4.0
    assert pacer.current == 4.0 and pacer.slowdowns == 1
    pacer.register_block()           # 4.0 -> 8.0
    assert pacer.current == 8.0
    pacer.register_block()           # 16.0 capped to ceiling 10.0
    assert pacer.current == 10.0 and pacer.slowdowns == 3
    pacer.register_block()           # already at ceiling -> no change, no slowdown
    assert pacer.current == 10.0 and pacer.slowdowns == 3

    pacer.sleep()
    assert slept == [10.0]


def test_pacer_zero_base_seeds_floor_on_first_block() -> None:
    pacer = pipeline._Pacer(0.0, floor=2.0, ceiling=10.0, log=_noop, sleep_fn=_noop)
    assert pacer.current == 0.0
    pacer.register_block()
    assert pacer.current == 2.0 and pacer.slowdowns == 1


def test_run_auto_slows_after_repeated_challenges(tmp_path) -> None:
    # Chapters 2 and 3 always fail with a block (non-permanent) -> the run slows.
    adapter = ScriptedAdapter(count=5, fail_until={2: 99, 3: 99})
    report = pipeline.run_scrape(
        _job(tmp_path), adapter=adapter, log=_noop, sleep_fn=_noop,
    )
    assert report.auto_slowdowns >= 2
    assert report.effective_delay >= pipeline.AUTO_SLOWDOWN_FLOOR
    assert report.effective_delay <= pipeline.AUTO_SLOWDOWN_CEILING
    assert sorted(report.failed) == [2, 3]


# ── 9C — relentless retry + second-pass sweep ────────────────────────────────
def test_second_pass_sweep_rescues_transient_failure(tmp_path) -> None:
    # Chapter 3 fails on its first attempt and succeeds on the sweep re-attempt.
    adapter = ScriptedAdapter(count=5, fail_until={3: 1})
    report = pipeline.run_scrape(
        _job(tmp_path), adapter=adapter, log=_noop, sleep_fn=_noop,
    )
    assert report.rescued == [3]
    assert report.failed == []
    assert len(report.written) == 5
    assert len(_pdfs(tmp_path)) == 5
    # The sweep re-attempted ONLY the failed chapter, after the full main pass.
    assert adapter.fetched == [1, 2, 3, 4, 5, 3]


def test_sweep_reattempts_only_the_failed_set(tmp_path) -> None:
    adapter = ScriptedAdapter(count=5, fail_until={2: 1, 4: 1})
    pipeline.run_scrape(_job(tmp_path), adapter=adapter, log=_noop, sleep_fn=_noop)
    # Main pass fetches 1..5 in order; the sweep re-attempts only 2 and 4.
    assert adapter.fetched == [1, 2, 3, 4, 5, 2, 4]


def test_permanent_404_short_circuits_and_is_not_swept(tmp_path) -> None:
    adapter = ScriptedAdapter(count=5, permanent=[3])
    report = pipeline.run_scrape(
        _job(tmp_path), adapter=adapter, log=_noop, sleep_fn=_noop,
    )
    assert report.failed == [3]
    assert report.permanent_failed == [3]
    assert report.rescued == []
    # Chapter 3 is fetched once and never re-attempted (the sweep skips 404s).
    assert adapter.fetched == [1, 2, 3, 4, 5]
    assert len(report.written) == 4


def test_sweep_rescues_in_single_mode_before_pdf_written(tmp_path) -> None:
    adapter = ScriptedAdapter(count=4, fail_until={2: 1})
    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.SINGLE), adapter=adapter, log=_noop, sleep_fn=_noop,
    )
    assert report.rescued == [2]
    assert report.failed == []
    # One combined PDF containing all four chapters (the rescued one included).
    assert _pdfs(tmp_path) == ["Shadow_Slave_All_Chapters.pdf"]


def test_more_generous_default_retries() -> None:
    # Phase 9C made the give-up threshold explicit and generous.
    assert ScrapeJob(
        novel_slug="x", adapter_key="y", start=1, end=1, delay=0.0,
        output_mode=OutputMode.SINGLE, use_cache=False, output_dir=Path("."),
    ).max_retries >= 6
    assert rm.MAX_RETRIES >= 6


# ── 9D — WebNovel g_data post-redirect + robust challenge detection ──────────
def test_post_redirect_g_data_chapter_extracted() -> None:
    html = _fixture("wnd_g_data_post_redirect_chapter.html")
    # No __NEXT_DATA__ on the redirected page; the body comes from g_data.chapInfo.
    assert wnd.parse_next_data(html) is None
    raw_title, paragraphs = wnd.extract_chapter(html, fallback_index=1)
    assert raw_title == "Glory to the Victor"
    assert len(paragraphs) == 3
    assert paragraphs[0].startswith("One strike after another")


def test_wnd_adapter_extracts_post_redirect_g_data() -> None:
    html = _fixture("wnd_g_data_post_redirect_chapter.html")
    meta = ChapterMeta(
        index=1,
        url="https://www.webnovel.com/book/28684090500376805/76998299197949227",
        title=None,
    )
    adapter = WebNovelDynamicAdapter(request_manager=FakeRM(default_html=html))
    content = adapter.fetch_chapter(meta, NQ_SPEC)
    assert content.title == "Glory to the Victor"
    assert content.heading == "Chapter 1: Glory to the Victor."
    assert len(content.paragraphs) == 3


def test_cleared_post_redirect_page_not_flagged_as_challenge() -> None:
    """The cleared page carries the ambient /cdn-cgi/challenge-platform/ beacon but
    has real g_data content. The old loose check mis-flagged it as a challenge,
    which is exactly why camoufox 'still saw a Cloudflare challenge'."""
    html = _fixture("wnd_g_data_post_redirect_chapter.html")
    assert "challenge-platform" in html.lower()      # the ambient beacon IS present
    assert rm.is_cloudflare_challenge(html) is False  # but it is not a challenge
    assert cf_bypass.is_cloudflare_challenge(html) is False


def test_real_interstitial_is_flagged() -> None:
    html = _fixture("wnd_cloudflare_challenge.html")
    assert rm.is_cloudflare_challenge(html) is True
    assert cf_bypass.is_cloudflare_challenge(html) is True


def test_challenge_html_makes_strategy_retryable(tmp_path, monkeypatch) -> None:
    """A strategy that returns a challenge page (not an exception) must surface a
    retryable failure so the ladder escalates rather than caching a stub."""
    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)
    challenge = _fixture("wnd_cloudflare_challenge.html")
    monkeypatch.setattr(mgr, "_get_text", lambda session, url: challenge)
    with pytest.raises(rm._RetryableFetch):
        mgr._fetch_uncached_strategy("https://example/ch", rm.FETCH_STRATEGY_HTTP)


def test_default_ladder_escalates_to_camoufox_for_non_browser_fetch() -> None:
    """Browser-mode OFF still auto-escalates to a browser engine on a block: the
    default ladder includes the camoufox rungs (Phase 9E behaviour)."""
    assert rm.FETCH_STRATEGY_CAMOUFOX in rm.DEFAULT_ESCALATION_LADDER
    assert rm.FETCH_STRATEGY_CAMOUFOX_FRESH in rm.DEFAULT_ESCALATION_LADDER


# ── Phase 9 review fixes ─────────────────────────────────────────────────────
def test_single_ambient_beacon_without_payload_is_challenge_or_escalates(
    tmp_path, monkeypatch
) -> None:
    """An ambient /cdn-cgi/challenge-platform/ beacon with NO strong marker and NO
    real structural payload is an active challenge — both detectors must flag it,
    and a strategy that returns that body must escalate (retryable) rather than
    cache/return it. Removing the old length-only clearance is what makes a large
    bare-beacon body still flag here."""
    beacon = (
        "<!DOCTYPE html><html><head>"
        '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>'
        "</head><body><div>Checking your browser before you continue.</div>"
        # Padding well past the old 40 KB length-clearance threshold to prove a
        # large body is no longer treated as a real payload by size alone.
        + ("<span>filler</span>" * 3000)
        + "</body></html>"
    )
    assert len(beacon) > 40_000
    assert rm.is_cloudflare_challenge(beacon) is True
    assert cf_bypass.is_cloudflare_challenge(beacon) is True

    mgr = RequestManager("s", use_cache=False, cache_root=tmp_path)
    monkeypatch.setattr(mgr, "_get_text", lambda session, url: beacon)
    with pytest.raises(rm._RetryableFetch):
        mgr._fetch_uncached_strategy("https://example/ch", rm.FETCH_STRATEGY_HTTP)
    # And nothing was cached for that URL (the challenge body was not returned).
    assert not mgr.cache_path_for("https://example/ch").exists()


def test_auto_slowdown_sleeps_after_block(tmp_path) -> None:
    """After a non-permanent block the raised inter-fetch delay must actually be
    slept before the next fetch/sweep retry — not skipped by the early return."""
    slept: list[float] = []
    # Chapter 2 always blocks (non-permanent); base delay is 0 so the only sleeps
    # recorded come from the auto-slowdown applied after the block.
    adapter = ScriptedAdapter(count=3, fail_until={2: 99})
    pipeline.run_scrape(
        _job(tmp_path, start=1, end=3), adapter=adapter, log=_noop,
        sleep_fn=slept.append,
    )
    # The first real sleep is the post-block floor delay, applied immediately
    # after the block and before the run proceeds.
    assert slept, "no inter-fetch sleep happened after the block"
    assert slept[0] == pipeline.AUTO_SLOWDOWN_FLOOR


def test_chunked_sweep_runs_once_over_all_non_permanent_failures(tmp_path) -> None:
    """Chunked mode runs the second-pass sweep ONCE over all collected
    non-permanent failures (not once per chunk), after the full main pass, with
    permanent failures excluded and rescued chapters slotted into the right
    chunk PDF."""
    logs: list[str] = []
    # chunk_size 2 over 1..5 -> [1-2], [3-4], [5-5]. Failures span two chunks
    # (2 and 3); chapter 5 is a permanent 404 (must be excluded from the sweep).
    adapter = ScriptedAdapter(count=5, fail_until={2: 1, 3: 1}, permanent=[5])
    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.CHUNKED, start=1, end=5, chunk_size=2),
        adapter=adapter, log=logs.append, sleep_fn=_noop,
    )
    # Exactly one sweep for the whole run.
    sweeps = [m for m in logs if m.startswith("Second-pass sweep")]
    assert len(sweeps) == 1
    # Main pass fetches every chunk (1..5) before any sweep re-attempt; the sweep
    # then re-attempts only the non-permanent failures (2 and 3).
    assert adapter.fetched[:5] == [1, 2, 3, 4, 5]
    assert sorted(adapter.fetched[5:]) == [2, 3]
    # Permanent 404 excluded from the sweep and never rescued.
    assert report.permanent_failed == [5]
    assert 5 not in report.rescued
    assert sorted(report.rescued) == [2, 3]
    assert report.failed == [5]
    # Rescued chapters land in their own chunk PDFs (5-5 stays unwritten: empty).
    assert _pdfs(tmp_path) == [
        "Shadow_Slave_Chapters_1-2.pdf",
        "Shadow_Slave_Chapters_3-4.pdf",
    ]
    assert len(report.written) == 2


def test_http_401_is_not_permanent_and_is_swept(tmp_path) -> None:
    """HTTP 401 is transient, not permanent: it must auto-slowdown, be swept, and
    be eligible for rescue (only 403/404 are permanent)."""
    adapter = ScriptedAdapter(
        count=5, fail_until={3: 1},
        fail_message="HTTP 401 Unauthorized for https://example/3",
    )
    report = pipeline.run_scrape(
        _job(tmp_path), adapter=adapter, log=_noop, sleep_fn=_noop,
    )
    assert report.permanent_failed == []        # 401 is NOT permanent
    assert report.rescued == [3]                 # swept and rescued
    assert report.failed == []
    assert report.auto_slowdowns >= 1            # the block triggered auto-slowdown
    assert adapter.fetched == [1, 2, 3, 4, 5, 3]


def test_pacer_initial_delay_clamped_to_ceiling(tmp_path) -> None:
    """The ceiling is an absolute cap: a base delay above it is clamped from the
    start, and the effective delay never exceeds it — including via a run report."""
    pacer = pipeline._Pacer(50.0, ceiling=30.0, floor=2.0, log=_noop, sleep_fn=_noop)
    assert pacer.current == 30.0
    pacer.register_block()                       # already at ceiling -> no increase
    assert pacer.current <= 30.0

    job = ScrapeJob(
        novel_slug=ENABLED_NOVEL, adapter_key=ENABLED_KEY, start=1, end=2,
        delay=50.0, output_mode=OutputMode.SEPARATE, use_cache=False,
        output_dir=tmp_path,
    )
    report = pipeline.run_scrape(
        job, adapter=ScriptedAdapter(count=2), log=_noop, sleep_fn=_noop,
    )
    assert report.effective_delay <= pipeline.AUTO_SLOWDOWN_CEILING
    assert report.effective_delay == pipeline.AUTO_SLOWDOWN_CEILING
