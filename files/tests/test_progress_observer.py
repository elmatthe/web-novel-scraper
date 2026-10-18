"""v0.3.0 Checkpoint 02.2: the pipeline's optional ``phase_cb`` observer, end to end.

The observer is instrumentation only, so these tests drive the real pipeline offline (fake
adapters and the fake browser seams the existing suites use) and check two things: that the
observer reports what the run is really doing, and that it changes nothing the run does. The
callbacks are then fed through the same events the window uses into a ``RunTracker``, so the
phase, the count and the final state are checked against a real run, not a hand-made one.
"""

from __future__ import annotations

import threading

import pytest

from webnovel_scraper import catalog, pipeline
from webnovel_scraper import rescue_pool as rp
from webnovel_scraper.host_rate_limiter import HostRateLimiter
from webnovel_scraper.models import ChapterContent, ChapterMeta, OutputMode, ScrapeJob
from webnovel_scraper.request_manager import ChallengeFetchError, FetchInfo, ScrapeCancelled
from webnovel_scraper.ui import run_activity as ra
from webnovel_scraper.ui import run_progress as rpg

NOVEL, KEY = "shadow-slave", "freewebnovel"


# ── fakes ─────────────────────────────────────────────────────────────────────
class _Adapter:
    """A legacy-path adapter. ``fail_once`` chapters fail on their first fetch only."""

    def __init__(self, count: int = 5, *, fail_once=(), on_fetch=None, toc_raises=None) -> None:
        self.count, self.fail_once, self.on_fetch = count, set(fail_once), on_fetch
        self.toc_raises = toc_raises
        self.fetched: list[int] = []
        self.warnings: list[str] = []

    def build_chapter_index(self, spec):
        if self.toc_raises is not None:
            raise self.toc_raises
        return [ChapterMeta(index=n, url=f"https://freewebnovel.com/novel/shadow-slave/chapter-{n}", source_id=str(n))
                for n in range(1, self.count + 1)]

    def fetch_chapter(self, meta, spec):
        self.fetched.append(meta.index)
        if self.on_fetch is not None:
            self.on_fetch(meta.index)
        if meta.index in self.fail_once:
            self.fail_once.discard(meta.index)
            raise RuntimeError("temporary glitch")
        return ChapterContent(index=meta.index, title=f"Title {meta.index}",
                              paragraphs=[f"Body paragraph for chapter {meta.index}, long enough."])


class _DuplicateNumbers(_Adapter):
    """Two distinct source pages that both display chapter 3664."""

    def build_chapter_index(self, spec):
        return [ChapterMeta(index=3664, url=f"https://freewebnovel.com/novel/shadow-slave/chapter-{slug}", source_id=str(slug),
                            slug=slug) for slug in (3678, 3679)]

    def fetch_chapter(self, meta, spec):
        return ChapterContent(index=meta.index, title="Twisted Reflection (Part 1)",
                              paragraphs=[f"Distinct body from source slug {meta.slug}."])


def _job(tmp_path, mode=OutputMode.SEPARATE, *, start=1, end=5, chunk_size=2) -> ScrapeJob:
    return ScrapeJob(novel_slug=NOVEL, adapter_key=KEY, start=start, end=end, delay=0.0,
                     output_mode=mode, use_cache=False, output_dir=tmp_path, chunk_size=chunk_size)


class _Run:
    """What a front end hears from one run: an ordered list of callbacks, and a tracker fed
    through the window's own event payloads."""

    def __init__(self, observe: bool = True) -> None:
        self.calls: list[tuple] = []
        self.observe = observe
        self.clock = 0.0
        self.tracker = rpg.RunTracker(clock=lambda: self.clock)
        self.tracker.start()
        self.report = None
        self.error: BaseException | None = None

    def progress(self, done, total) -> None:
        self.calls.append(("progress", done, total))
        self.clock += 1.0
        event = ra.progress_event(done, total, at=self.clock)
        self.tracker.progress(event.payload.done, event.payload.total, event.payload.at)

    def phase(self, phase, total, already_present) -> None:
        self.calls.append(("phase", phase, total, already_present))
        self.clock += 1.0
        event = ra.phase_event(phase, total, already_present, at=self.clock)
        p = event.payload
        self.tracker.phase(p.phase, p.total, p.already_present, p.at)

    def run(self, job, **kwargs):
        extra = {"phase_cb": self.phase} if self.observe else {}
        kwargs.setdefault("sleep_fn", lambda _seconds: None)    # never really wait
        try:
            self.report = pipeline.run_scrape(job, log=lambda _m: None, progress_cb=self.progress,
                                              **extra, **kwargs)
        except Exception as exc:  # noqa: BLE001 - the worker's catch-all, as in the window
            self.error = exc
        return self

    def finish(self) -> rpg.RunView:
        done = [c for c in self.calls if c[0] == "progress"]
        processed = (done[-1][1], done[-1][2]) if done else None
        outcome, _events = ra.outcome_events(self.report, processed=processed, error=self.error)
        self.tracker.finish(outcome, processed=processed, counts=ra.final_counts(self.report))
        return self.tracker.view()

    @property
    def phases(self):
        return [c for c in self.calls if c[0] == "phase"]

    @property
    def progress_calls(self):
        return [c for c in self.calls if c[0] == "progress"]


# ── what the observer reports ─────────────────────────────────────────────────
def test_scraping_is_announced_with_the_selected_total_before_the_first_chapter(tmp_path) -> None:
    run = _Run().run(_job(tmp_path), adapter=_Adapter(5))
    assert run.calls[0] == ("phase", "scraping", 5, 0)
    assert run.progress_calls[-1] == ("progress", 5, 5)
    assert run.phases == [("phase", "scraping", 5, 0)]            # SEPARATE has no build step
    assert run.finish().status == "Complete" and run.tracker.view().counts == "5 / 5 · 100%"


@pytest.mark.parametrize("mode", [OutputMode.CHUNKED, OutputMode.SINGLE])
def test_building_is_announced_after_the_last_chapter_and_before_the_pdf(tmp_path, mode,
                                                                          monkeypatch) -> None:
    run = _Run()
    original = pipeline.pdf_builder.create_pdf
    seen_when_building: list[list] = []

    def spy(*args, **kwargs):
        seen_when_building.append(list(run.calls))
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline.pdf_builder, "create_pdf", spy)
    run.run(_job(tmp_path, mode), adapter=_Adapter(5))
    assert run.phases == [("phase", "scraping", 5, 0), ("phase", "building", 5, 0)]
    first_build = run.calls.index(("phase", "building", 5, 0))
    assert all(c[0] == "progress" for c in run.calls[1:first_build])
    assert run.progress_calls[-1] == ("progress", 5, 5) and first_build > len(run.calls) - 3
    assert seen_when_building and ("phase", "building", 5, 0) in seen_when_building[0]
    view = run.finish()
    assert view.state == rpg.COMPLETE and view.counts == "5 / 5 · 100%"
    assert view.detail == ("3 PDFs" if mode is OutputMode.CHUNKED else "1 PDF")


def test_chunked_and_single_pdfs_are_not_chapters(tmp_path) -> None:
    run = _Run().run(_job(tmp_path, OutputMode.SINGLE), adapter=_Adapter(5))
    assert run.progress_calls[-1] == ("progress", 5, 5) and len(run.report.written) == 1
    assert run.finish().counts == "5 / 5 · 100%"


def test_the_observer_changes_nothing_the_run_does(tmp_path) -> None:
    def execute(observe: bool, folder):
        folder.mkdir()
        run = _Run(observe).run(_job(folder, OutputMode.CHUNKED), adapter=_Adapter(5, fail_once=[3]))
        return run, sorted(p.name for p in folder.glob("*.pdf"))

    watched, files_a = execute(True, tmp_path / "a")
    plain, files_b = execute(False, tmp_path / "b")
    assert files_a == files_b
    assert watched.progress_calls == plain.progress_calls
    for field in ("skipped_existing", "failed", "permanent_failed", "extraction_failed",
                  "rescued", "cancelled", "toc_gaps", "requested_range", "effective_range"):
        assert getattr(watched.report, field) == getattr(plain.report, field), field
    assert [p.name for p in watched.report.written] == [p.name for p in plain.report.written]
    assert not plain.phases and watched.phases


def test_a_failing_observer_cannot_affect_the_run(tmp_path) -> None:
    def explode(*_args):
        raise RuntimeError("the front end broke")

    report = pipeline.run_scrape(_job(tmp_path, OutputMode.CHUNKED), adapter=_Adapter(5),
                                 log=lambda _m: None, phase_cb=explode)
    assert len(report.written) == 3 and not report.failed and not report.cancelled


def test_existing_callers_without_the_observer_still_work(tmp_path) -> None:
    ticks: list[tuple[int, int]] = []
    report = pipeline.run_scrape(_job(tmp_path, end=3), adapter=_Adapter(3), log=lambda _m: None,
                                 progress_cb=lambda d, t: ticks.append((d, t)))
    assert ticks == [(1, 3), (2, 3), (3, 3)] and len(report.written) == 3


# ── counting ──────────────────────────────────────────────────────────────────
def test_a_failed_then_recovered_chapter_is_counted_once(tmp_path) -> None:
    run = _Run().run(_job(tmp_path), adapter=_Adapter(5, fail_once=[2]))
    done = [d for _kind, d, _t in run.progress_calls]
    assert done == [1, 2, 3, 4, 5]                   # the second pass adds no extra tick
    assert run.report.rescued == [2] and run.report.failed == []
    view = run.finish()
    assert view.counts == "5 / 5 · 100%" and view.status == "Complete"
    assert view.detail == "5 PDFs · 1 recovered"


def test_a_chapter_that_stays_failed_still_counts_as_processed(tmp_path) -> None:
    class _Dead(_Adapter):
        def fetch_chapter(self, meta, spec):
            if meta.index == 4:
                raise RuntimeError("HTTP 404 not found")
            return super().fetch_chapter(meta, spec)

    run = _Run().run(_job(tmp_path), adapter=_Dead(5))
    assert run.progress_calls[-1] == ("progress", 5, 5)
    view = run.finish()
    assert view.counts == "5 / 5 · 100%"             # processed, but...
    assert view.status == "Finished with problems" and view.severity == "warning"
    assert view.detail == "4 PDFs · 1 failed"


def test_chapters_already_present_are_counted_and_reported_as_skipped(tmp_path) -> None:
    pipeline.run_scrape(_job(tmp_path), adapter=_Adapter(5), log=lambda _m: None)
    for pdf in sorted(tmp_path.glob("*.pdf"))[:2]:
        pdf.unlink()                                   # two chapters will be fetched again
    adapter = _Adapter(5)
    run = _Run().run(_job(tmp_path), adapter=adapter)
    assert adapter.fetched == [1, 2]
    assert run.progress_calls[-1] == ("progress", 5, 5)
    assert run.phases[-1] == ("phase", "scraping", 5, 3)
    assert len(run.report.skipped_existing) == 3
    skips = [c[3] for c in run.phases]
    assert skips == sorted(skips) and skips[0] == 0
    view = run.finish()
    assert view.counts == "5 / 5 · 100%" and view.detail == "2 PDFs · 3 already present"


def test_a_whole_run_already_present_reports_no_new_pdfs(tmp_path) -> None:
    pipeline.run_scrape(_job(tmp_path, OutputMode.SINGLE, end=4), adapter=_Adapter(4),
                        log=lambda _m: None)
    run = _Run().run(_job(tmp_path, OutputMode.SINGLE, end=4), adapter=_Adapter(4))
    assert run.phases[0] == ("phase", "scraping", 4, 0) and run.phases[-1][3] == 4
    assert [c for c in run.phases if c[1] == "building"] == []
    view = run.finish()
    assert view.counts == "4 / 4 · 100%" and view.state == rpg.COMPLETE
    assert view.detail == "0 PDFs · 4 already present"


def test_distinct_source_pages_with_one_displayed_number_are_separate_chapters(tmp_path) -> None:
    run = _Run().run(_job(tmp_path, start=3664, end=3664), adapter=_DuplicateNumbers())
    assert run.phases[0] == ("phase", "scraping", 2, 0)
    assert run.progress_calls[-1] == ("progress", 2, 2)
    assert len(run.report.written) == 2
    assert run.finish().counts == "2 / 2 · 100%"


# ── stopping and failing ──────────────────────────────────────────────────────
def test_cancelled_while_the_chapter_list_is_prepared(tmp_path) -> None:
    run = _Run().run(_job(tmp_path), adapter=_Adapter(5, toc_raises=ScrapeCancelled("stop")))
    assert run.calls == [] and run.report.cancelled          # no total was ever known
    assert run.tracker.view().indeterminate                   # still honestly "preparing"
    run.tracker.stop_requested()
    assert run.tracker.view().status == "Stopping…"
    view = run.finish()
    assert (view.state, view.status, view.counts) == (rpg.CANCELLED, "Stopped", "—")
    assert not view.indeterminate and view.eta == ""


def test_cancelled_while_scraping_keeps_the_partial_count(tmp_path) -> None:
    cancel = threading.Event()
    adapter = _Adapter(6, on_fetch=lambda index: cancel.set() if index == 3 else None)
    run = _Run().run(_job(tmp_path, end=6), adapter=adapter, cancel_event=cancel)
    assert run.report.cancelled and run.progress_calls[-1] == ("progress", 3, 6)
    run.tracker.stop_requested()
    assert run.tracker.view().status == "Stopping after the current chapter…"
    view = run.finish()
    assert view.state == rpg.CANCELLED and view.counts == "3 / 6 · 50%"
    assert view.detail == "3 PDFs" and view.eta == ""
    assert [c for c in run.phases if c[1] == "building"] == []


def test_a_failure_while_the_output_is_built_ends_as_failed_not_complete(tmp_path,
                                                                         monkeypatch) -> None:
    def broken(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(pipeline.pdf_builder, "create_pdf", broken)
    run = _Run().run(_job(tmp_path, OutputMode.CHUNKED), adapter=_Adapter(5))
    assert isinstance(run.error, OSError) and run.report is None
    assert run.phases[-1] == ("phase", "building", 5, 0)
    assert run.tracker.view().status == "Building output…"
    view = run.finish()
    assert view.state == rpg.FAILED and view.status == "Run failed"
    assert view.counts == "5 / 5 · 100%"               # every chapter was fetched; the run failed
    assert view.severity == "error"


# ── the browser/rescue conductor ──────────────────────────────────────────────
class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(0.0, seconds)


class _Manager:
    def __init__(self, headless, limiter, cancel) -> None:
        self.headless, self.host_limiter, self.cancel_event = headless, limiter, cancel
        self.last_fetch_info = None

    def close(self) -> None:
        pass


class _PrimaryAdapter:
    def __init__(self, manager, hard) -> None:
        self.manager, self.hard, self.warnings = manager, hard, []

    def build_chapter_index(self, spec, *, fast_path=False):
        return [ChapterMeta(index=i, url=f"https://freewebnovel.com/novel/shadow-slave/chapter-{i}")
                for i in range(1, 7)]

    def fetch_chapter(self, meta, spec, *, fast_path=False):
        if meta.index in self.hard:
            self.manager.last_fetch_info = FetchInfo(from_cache=False, classification="challenge")
            raise ChallengeFetchError("blocked")
        self.manager.last_fetch_info = FetchInfo(from_cache=False, classification="success",
                                                 strategy="camoufox")
        return _content(meta.index)


def _content(i: int) -> ChapterContent:
    return ChapterContent(index=i, title=f"Title {i}",
                          paragraphs=[f"First body paragraph for chapter {i}, long enough."])


def _conductor(tmp_path, mode, *, hard=(), rescue_ok=True, run=None, chunk_size=2,
               stopped=False):
    clock, cancel = _Clock(), threading.Event()
    if stopped:
        cancel.set()
    job = ScrapeJob(novel_slug=NOVEL, adapter_key=KEY, start=1, end=6, delay=3.0,
                    output_mode=mode, use_cache=False, output_dir=tmp_path,
                    chunk_size=chunk_size, use_browser=True, headless=True)
    limiter = HostRateLimiter(max(pipeline.HOST_MIN_INTERVAL, job.delay),
                              monotonic=clock.now, sleep=clock.sleep)

    def rescue_fetch(meta, *, manager, adapter, mode, fresh, budget):
        if not rescue_ok:
            raise ChallengeFetchError("rescue could not clear it")
        return _content(meta.index)

    def pool_factory():
        return rp.RescuePool(
            primary_headless=True, host_limiter=limiter, cancel_event=cancel,
            monotonic=clock.now, sleep=clock.sleep, fetch_fn=rescue_fetch,
            manager_factory=lambda *, headless: type("M", (), {"headless": headless,
                                                                "close": lambda self: None})(),
            adapter_factory=lambda m: object(), log=lambda _m: None)

    run = run or _Run()
    return run.run(
        job, cancel_event=cancel, sleep_fn=clock.sleep, monotonic_fn=clock.now,
        host_limiter=limiter,
        request_manager_factory=lambda *, headless: _Manager(headless, limiter, cancel),
        primary_adapter_factory=lambda m: _PrimaryAdapter(m, set(hard)),
        rescue_pool_factory=pool_factory)


def test_the_conductor_reports_scraping_then_building_and_ticks_each_chapter_once(tmp_path) -> None:
    run = _conductor(tmp_path, OutputMode.CHUNKED, hard=[3, 4])
    assert run.report.rescued == [3, 4] and run.report.failed == []
    ticks = [d for _k, d, _t in run.progress_calls]
    assert ticks == sorted(ticks) and ticks[-1] == 6 and len(ticks) == 6    # primary + rescue: once
    assert run.phases == [("phase", "scraping", 6, 0), ("phase", "building", 6, 0)]
    assert run.calls.index(("phase", "building", 6, 0)) > run.calls.index(("progress", 6, 6))
    view = run.finish()
    assert view.counts == "6 / 6 · 100%" and view.status == "Complete"
    assert view.detail == "3 PDFs · 2 recovered"


def test_the_conductor_reports_resume_skips_before_the_first_fetch(tmp_path) -> None:
    stem = pipeline._stem_for(catalog.get_spec(NOVEL, KEY))
    (tmp_path / f"{stem}_Chapters_1-2.pdf").write_bytes(b"%PDF-1.4 already here")
    run = _conductor(tmp_path, OutputMode.CHUNKED)
    assert run.phases[0] == ("phase", "scraping", 6, 0)
    assert run.phases[1] == ("phase", "scraping", 6, 2)
    assert run.progress_calls[0] == ("progress", 2, 6)         # the skipped chunk, in one step
    assert run.calls.index(run.phases[1]) < run.calls.index(run.progress_calls[0])
    assert run.progress_calls[-1] == ("progress", 6, 6)
    view = run.finish()
    assert view.detail == "2 PDFs · 2 already present"


def test_a_conductor_run_with_an_unrecoverable_chapter_is_finished_with_problems(tmp_path) -> None:
    run = _conductor(tmp_path, OutputMode.SEPARATE, hard=[5], rescue_ok=False)
    assert run.progress_calls[-1] == ("progress", 6, 6)
    assert run.report.failed == [5]
    view = run.finish()
    assert view.counts == "6 / 6 · 100%" and view.status == "Finished with problems"
    assert view.detail == "5 PDFs · 1 failed"
    assert [c for c in run.phases if c[1] == "building"] == []


def test_the_conductor_stopped_before_any_chapter_ends_as_stopped(tmp_path) -> None:
    run = _conductor(tmp_path, OutputMode.SEPARATE, stopped=True)
    assert run.report.cancelled and not run.report.written and not run.report.failed
    assert not [c for c in run.phases if c[1] == "building"]
    run.tracker.stop_requested()
    view = run.finish()
    assert view.state == rpg.CANCELLED and view.status == "Stopped" and view.eta == ""
    assert view.detail == "0 PDFs"
