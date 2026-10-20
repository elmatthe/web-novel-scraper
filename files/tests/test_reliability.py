"""Reliability hardening (v0.3.0 Checkpoint 06). Offline, deterministic, bounded.

Stop while output is being built, cache-only operation for an open-ended run, released memory, and a
bounded long-run stress with intermittent failures whose counters and journal must add up exactly.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from webnovel_scraper import catalog, pdf_builder, pipeline
from webnovel_scraper.chapter_manifest import ChapterManifest, Provenance
from webnovel_scraper.chapter_store import ChapterManifestStore
from webnovel_scraper.models import ChapterContent, ChapterMeta, OutputMode, ScrapeJob
from webnovel_scraper.request_manager import ScrapeCancelled
from webnovel_scraper.run_journal import JOURNAL_FILENAME, replay_journal
from webnovel_scraper.source_identity import ChapterLabel, SourceIdentity

SPEC = catalog.get_spec("shadow-slave", "freewebnovel")
SOURCE = SourceIdentity.from_site_spec(SPEC)


class Site:
    def __init__(self, count, *, fail_once=(), fail_always=(), toc_error=None):
        self.count, self.fail_once, self.fail_always = count, set(fail_once), set(fail_always)
        self.toc_error = toc_error
        self.built = 0
        self.fetched: list[int] = []
        self.warnings: list[str] = []

    def build_chapter_index(self, spec, **_kw):
        self.built += 1
        if self.toc_error is not None:
            raise self.toc_error
        return [ChapterMeta(index=n, url=f"{spec.url}/chapter-{n}", slug=n) for n in range(1, self.count + 1)]

    def fetch_chapter(self, meta, spec, **_kw):
        self.fetched.append(meta.index)
        if meta.index in self.fail_always:
            raise RuntimeError("HTTP 503 (always)")
        if meta.index in self.fail_once:
            self.fail_once.discard(meta.index)
            raise RuntimeError("HTTP 503 (once)")
        return ChapterContent(meta.index, f"Title {meta.index}", [f"Line of chapter {meta.index}.", "Boom!"])


@pytest.fixture
def store(tmp_path):
    return ChapterManifestStore(tmp_path / "b", tmp_path / "r")


def job(out, start=1, end=6, mode=OutputMode.CHUNKED, **kw):
    return ScrapeJob(novel_slug="shadow-slave", adapter_key="freewebnovel", start=start, end=end, delay=0.0,
                     output_mode=mode, chunk_size=2, use_cache=False, output_dir=out, allocate_run=True, **kw)


def run(store, the_job, site, *, cancel=None, logs=None):
    return pipeline.run_scrape(the_job, adapter=site, log=(logs.append if logs is not None else lambda _m: None),
                               cancel_event=cancel, sleep_fn=lambda _s: None, manifest_store=store)


# ── Stop while the output is being built ──────────────────────────────────────
def test_a_pdf_layout_stops_at_the_next_chapter_and_publishes_nothing(tmp_path):
    cancel = threading.Event()
    cancel.set()
    chapters = [ChapterContent(n, "T", ["x " * 50]) for n in range(1, 4)]
    with pytest.raises(ScrapeCancelled):
        pdf_builder.create_pdf(chapters, tmp_path / "out.pdf", title="T", cancel_event=cancel)
    assert list(tmp_path.iterdir()) == []                         # no file, no .part


def test_stop_between_txt_files_builds_no_further_file(tmp_path, store, monkeypatch):
    """TXT has no layout to interrupt, so the check between files is what stops the build."""
    cancel = threading.Event()
    real = pipeline.render_output

    def render_then_stop(fmt, contents, path, title, cancel_event=None):
        real(fmt, contents, path, title, cancel_event=cancel_event)
        cancel.set()

    monkeypatch.setattr(pipeline, "render_output", render_then_stop)
    report = run(store, job(tmp_path / "out", formats=("txt",)), Site(6), cancel=cancel)
    assert report.cancelled
    assert [p.name for p in (report.output_dir / "TXT").glob("*.txt")] == ["Shadow_Slave_Chapters_1-2.txt"]


def test_stop_during_the_grouped_build_keeps_finished_files_and_resume_builds_the_rest(tmp_path, store, monkeypatch):
    cancel = threading.Event()
    real = pipeline.render_output
    built = []

    def render_then_stop(fmt, contents, path, title, cancel_event=None):
        real(fmt, contents, path, title, cancel_event=cancel_event)
        built.append(path.name)
        cancel.set()                                              # Stop pressed after the first file

    monkeypatch.setattr(pipeline, "render_output", render_then_stop)
    logs: list[str] = []
    report = run(store, job(tmp_path / "out"), Site(6), cancel=cancel, logs=logs)
    assert report.cancelled and built == ["Shadow_Slave_Chapters_1-2.pdf"]
    folder = report.output_dir
    assert sorted(p.name for p in (folder / "PDF").glob("*.pdf")) == ["Shadow_Slave_Chapters_1-2.pdf"]
    replay = replay_journal(folder / JOURNAL_FILENAME)
    assert replay.last_end == "cancelled" and list(replay.publishes) == [("chunk-00001", "pdf")]
    assert any("stopped while the output files were being built" in line for line in logs)

    monkeypatch.setattr(pipeline, "render_output", real)
    site = Site(6)
    resumed = run(store, replace_job(job(folder), resume=True), site)
    assert sorted(site.fetched) == [3, 4, 5, 6] and not resumed.cancelled
    assert len(list((folder / "PDF").glob("*.pdf"))) == 3


def replace_job(the_job, **kw):
    from dataclasses import replace
    return replace(the_job, allocate_run=False, **kw)


def test_a_resume_that_fetches_fewer_chapters_never_shrinks_a_saved_file(tmp_path, store):
    """Session 1 saves chapters 1-10 without 3; the resume can fetch only 1-2. The 9-chapter file
    must stay as it was (not be replaced by a 2-chapter one), and only chapter 3 is a failure."""
    import json
    from dataclasses import replace

    first_job = replace(job(tmp_path / "out", 1, 10, formats=("txt",)), chunk_size=10)
    first = run(store, first_job, Site(10, fail_always={3}))
    folder = first.output_dir
    path = folder / "TXT" / "Shadow_Slave_Chapters_1-10.txt"
    before = path.read_bytes()
    assert before.count(b"Chapter ") == 9

    logs: list[str] = []
    again = run(store, replace_job(replace(job(folder, 1, 10, formats=("txt",)), chunk_size=10), resume=True),
                Site(10, fail_always=set(range(3, 11))), logs=logs)
    assert path.read_bytes() == before
    assert any("kept Shadow_Slave_Chapters_1-10.txt as it was" in line for line in logs)
    assert sorted(again.failed) == [3]
    replay = replay_journal(folder / JOURNAL_FILENAME)
    assert len(replay.publishes[("chunk-00001", "txt")]["pages"]) == 9
    assert replay.last_end == "complete_with_problems"
    # CP4 preserves every real fetch failure even when an earlier intact partial
    # publication still holds the page. Current outcome and history are distinct.
    assert [int(f["label"]) for f in replay.failures if f["seq"] > first_end_seq(replay)] == list(range(3, 11))
    assert again.accounting["requested_success"] == 9
    assert again.accounting["requested_failure"] == 1
    rows = json.loads((folder / pipeline.AUDIT_FILENAME).read_text(encoding="utf-8"))["chapters"]
    assert {r["label"]: r["status"] for r in rows} == {
        **{str(n): "saved" for n in range(1, 11)}, "3": "failed"}


def first_end_seq(replay):
    """Sequence number of the journal's first session end (records after it are the resume's)."""
    return replay.ends[0]["seq"]


def test_the_chapter_in_progress_when_stop_is_pressed_is_still_saved(tmp_path, store):
    cancel = threading.Event()

    class StopOnThree(Site):
        def fetch_chapter(self, meta, spec, **kw):
            content = super().fetch_chapter(meta, spec, **kw)
            if meta.index == 3:
                cancel.set()
            return content

    report = run(store, job(tmp_path / "out", mode=OutputMode.SEPARATE), StopOnThree(6), cancel=cancel)
    assert report.cancelled
    assert len(list((report.output_dir / "PDF").glob("*.pdf"))) == 3   # 1, 2 and the one in progress


# ── cache-only operation ──────────────────────────────────────────────────────
def _saved(store, numbers, spans):
    chapters = [SOURCE.chapter_identity(f"{SPEC.url}/chapter-{n}", ChapterLabel(str(n)), slug=n) for n in numbers]
    store.save_runtime(ChapterManifest.create(
        layer="runtime", source=SOURCE, chapters=chapters, spans=spans,
        provenance=Provenance(generated_utc="2026-10-18T00:00:00Z", validated_utc=None, app_version="0.3.0",
                              method="toc")))


def test_an_open_ended_run_without_the_network_uses_the_saved_list_and_says_so(tmp_path, store):
    _saved(store, range(1, 11), [(1, 10)])
    logs: list[str] = []
    site = Site(10, toc_error=RuntimeError("could not reach freewebnovel.com"))
    report = run(store, job(tmp_path / "out", 3, 10**9, mode=OutputMode.SEPARATE), site, logs=logs)
    assert site.built == 1 and report.effective_range == (3, 10) and len(report.written) == 8
    assert any(line.startswith("WARNING: newer chapters could not be looked for") for line in logs)
    assert any("the saved chapter list was used" in note for note in report.range_notes)


def test_without_the_network_a_saved_list_with_a_hole_still_refuses(tmp_path, store):
    _saved(store, [1, 2, 3, 8, 9, 10], [(1, 3), (8, 10)])          # 4-7 never examined
    with pytest.raises(RuntimeError, match="could not reach"):
        run(store, job(tmp_path / "out", 1, 10**9), Site(10, toc_error=RuntimeError("could not reach the site")))


def test_a_finite_range_never_hides_a_failed_discovery(tmp_path, store):
    _saved(store, range(1, 11), [(1, 10)])
    with pytest.raises(RuntimeError, match="could not reach"):
        run(store, job(tmp_path / "out", 5, 20), Site(10, toc_error=RuntimeError("could not reach the site")))


def test_while_another_window_updates_the_list_an_open_run_reads_the_saved_one(tmp_path, store):
    _saved(store, range(1, 7), [(1, 6)])
    other = ChapterManifestStore(store._baseline_root, store._runtime_root)
    logs: list[str] = []
    with other.writer_lock(SOURCE):
        site = Site(6)
        report = run(store, job(tmp_path / "out", 1, 10**9, mode=OutputMode.SEPARATE), site, logs=logs)
    assert site.built == 0 and len(report.written) == 6
    assert any("another run is updating the saved chapter list" in line for line in logs)


# ── saved-page cache hits ─────────────────────────────────────────────────────
def test_chapters_from_the_saved_page_cache_wait_no_delay_and_are_counted(tmp_path, store):
    """Standard (non-browser) path: a cached page made no request, so a repeat run of a cached novel
    must not sleep the inter-fetch delay after it (1,432 cached chapters at 3 s would be 72 min)."""
    from types import SimpleNamespace

    class CachedSite(Site):
        def __init__(self, count, cached):
            super().__init__(count)
            self.cached = set(cached)
            self._rm = SimpleNamespace(last_fetch_info=None)

        def fetch_chapter(self, meta, spec, **kw):
            content = super().fetch_chapter(meta, spec, **kw)
            if meta.index in self.cached:   # a network fetch here reports nothing, so a stale "from
                self._rm.last_fetch_info = SimpleNamespace(from_cache=True)   # cache" must not linger
            return content

    sleeps: list[float] = []
    logs: list[str] = []
    the_job = ScrapeJob(novel_slug="shadow-slave", adapter_key="freewebnovel", start=1, end=4, delay=2.0,
                        output_mode=OutputMode.SEPARATE, use_cache=True, output_dir=tmp_path / "out",
                        allocate_run=True, formats=("txt",))
    heard: list[tuple] = []
    report = pipeline.run_scrape(the_job, adapter=CachedSite(4, cached={1, 2}), log=logs.append,
                                 sleep_fn=sleeps.append, manifest_store=store,
                                 progress_cb=lambda d, t: heard.append(("progress", d)),
                                 phase_cb=lambda p, t, a, c: heard.append(("phase", c)))
    assert report.from_cache == 2 and len(report.written) == 4
    # The observer hears each cached chapter before the tick that counts it (the window keeps
    # them out of the ETA, since they cost no time).
    assert heard == [("phase", 0), ("phase", 1), ("progress", 1), ("phase", 2), ("progress", 2),
                     ("progress", 3), ("progress", 4)]
    assert sleeps == [2.0, 2.0]                                   # after chapters 3 and 4 only
    assert any("From the saved-page cache: 2 chapter page(s)" in line for line in logs)


# ── memory ────────────────────────────────────────────────────────────────────
def test_published_chapter_text_is_released(tmp_path, store, monkeypatch):
    seen = []
    real_finish = pipeline._finish_run

    def spy(the_run, report, log):
        seen.append(dict(the_run.outputs.contents))
        real_finish(the_run, report, log)

    monkeypatch.setattr(pipeline, "_finish_run", spy)
    run(store, job(tmp_path / "out", mode=OutputMode.SEPARATE), Site(6))
    assert seen == [{}]                                           # nothing held after publishing


# ── bounded long-run stress with faults ───────────────────────────────────────
def test_a_long_run_with_intermittent_failures_adds_up_exactly(tmp_path, store):
    count = 300
    flaky = set(range(7, count, 23))                              # fail once, recovered by the sweep
    dead = {50, 150, 250}                                         # fail every time
    site = Site(count, fail_once=flaky, fail_always=dead)
    ticks: list[tuple[int, int]] = []
    report = pipeline.run_scrape(
        job(tmp_path / "out", 1, count, mode=OutputMode.SEPARATE, formats=("txt",)), adapter=site,
        log=lambda _m: None, sleep_fn=lambda _s: None, manifest_store=store,
        progress_cb=lambda d, t: ticks.append((d, t)))
    assert ticks[-1] == (count, count) and [d for d, _ in ticks] == list(range(1, count + 1))
    assert sorted(report.failed) == sorted(dead)
    assert sorted(report.rescued) == sorted(flaky)
    assert len(report.written) == count - len(dead)
    replay = replay_journal(report.output_dir / JOURNAL_FILENAME)
    assert len(replay.publishes) == count - len(dead)
    assert sorted(int(f["label"]) for f in replay.failures) == sorted(dead | flaky)
    assert report.accounting["requested_success"] == count - len(dead)
    assert report.accounting["requested_failure"] == len(dead)
    assert report.accounting["attempted"] == count and report.accounting["not_attempted"] == 0
    for failure in replay.failures:
        if int(failure["label"]) in flaky:
            position = failure["position"]
            assert replay.contents[position]["seq"] > failure["seq"]
            assert replay.publishes[(f"chapter-{position + 1:05d}", "txt")]["seq"] > failure["seq"]
    assert replay.last_end == "complete_with_problems"
    # every saved file holds exactly its own chapter
    for path in (report.output_dir / "TXT").glob("*.txt"):
        number = int(path.name.split()[1].rstrip("_"))
        assert path.read_text(encoding="utf-8").startswith(f"Chapter {number}: Title {number}\n")
    # and resuming fetches only the dead chapters again
    again = Site(count, fail_always=dead)
    pipeline.run_scrape(replace_job(job(report.output_dir, 1, count, mode=OutputMode.SEPARATE,
                                        formats=("txt",)), resume=True),
                        adapter=again, log=lambda _m: None, sleep_fn=lambda _s: None, manifest_store=store)
    assert sorted(set(again.fetched)) == sorted(dead)
