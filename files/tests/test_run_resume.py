"""Run journal, safe run folders and validated Resume (v0.3.0 Checkpoints 03.3-03.4, plan §9.5/§9.7).

Offline and deterministic. Covers the journal format itself (torn tail, damage, tampering), the
interruption matrix of plan §9.7 (crash between publish and record, edited outputs, orphan temps,
torn last line), every Resume refusal, the run-folder lock, allocation of ``<slug>-N`` folders,
partial groups completed by a later resume, the run audit, and both pipeline paths across all three
grouping modes.
"""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from webnovel_scraper import catalog, chapter_preflight, pipeline
from webnovel_scraper import rescue_pool as rp
from webnovel_scraper.chapter_store import ChapterManifestStore
from webnovel_scraper.file_lock import FileLock
from webnovel_scraper.host_rate_limiter import HostRateLimiter
from webnovel_scraper.models import ChapterContent, ChapterMeta, OutputMode, ScrapeJob
from webnovel_scraper.run_journal import (
    JOURNAL_FILENAME,
    LOCK_FILENAME,
    SELECTION_FILENAME,
    JournalError,
    RunHeader,
    RunJournal,
    load_selection,
    replay_journal,
    selection_sha256,
)
from webnovel_scraper.run_storage import RESERVATION_FILENAME, RunStorageError
from webnovel_scraper.source_identity import SourceIdentity

NOVEL, KEY = "shadow-slave", "freewebnovel"
SPEC = catalog.get_spec(NOVEL, KEY)
SOURCE = SourceIdentity.from_site_spec(SPEC)


def url(n: int) -> str:
    return f"{SPEC.url}/chapter-{n}"


class Site:
    """A synthetic site of ``count`` chapters; ``fail`` pages raise; ``stop_at`` cancels."""

    def __init__(self, count, *, fail=(), cancel=None, stop_at=None):
        self.count, self.fail, self.cancel, self.stop_at = count, set(fail), cancel, stop_at
        self.built = 0
        self.fetched: list[int] = []
        self.warnings: list[str] = []

    def build_chapter_index(self, spec, *, start_hint=None, end_hint=None, resume_metas=None,
                            index_progress_cb=None, fast_path=False):
        self.built += 1
        return [ChapterMeta(index=n, url=url(n), slug=n, source_id=str(n)) for n in range(1, self.count + 1)]

    def fetch_chapter(self, meta, spec, **_kw):
        self.fetched.append(meta.index)
        if self.stop_at is not None and meta.index == self.stop_at and self.cancel is not None:
            self.cancel.set()
        if meta.index in self.fail:
            raise RuntimeError(f"HTTP 503 for chapter {meta.index}")
        return ChapterContent(index=meta.index, title=f"Title {meta.index}",
                              paragraphs=[f"Body paragraph of chapter {meta.index}, ordinary prose."])


@pytest.fixture
def store(tmp_path):
    return ChapterManifestStore(tmp_path / "lists-b", tmp_path / "lists-r")


def job(output_dir, start=1, end=6, *, mode=OutputMode.SEPARATE, chunk=2, **kw) -> ScrapeJob:
    return ScrapeJob(novel_slug=NOVEL, adapter_key=KEY, start=start, end=end, delay=0.0,
                     output_mode=mode, use_cache=False, output_dir=Path(output_dir), chunk_size=chunk,
                     **kw)


def run(store, the_job, site, cancel=None, logs=None):
    return pipeline.run_scrape(the_job, adapter=site, log=(logs.append if logs is not None else lambda _m: None),
                               cancel_event=cancel, manifest_store=store, sleep_fn=lambda _s: None)


def new_run(tmp_path, store, site, **kw):
    return run(store, job(tmp_path / "out", allocate_run=True, **kw), site)


def resume(store, folder, site, cancel=None, logs=None, **kw):
    return run(store, job(folder, resume=True, **kw), site, cancel=cancel, logs=logs)


def pdfs(folder: Path) -> list[str]:
    return sorted(p.name for p in (folder / "PDF").glob("*.pdf")) if (folder / "PDF").is_dir() else []


# ── the journal format ────────────────────────────────────────────────────────
def _header(chapters, **kw) -> RunHeader:
    base = dict(novel_slug=NOVEL, run_number=1, source=SOURCE, selection_sha256=selection_sha256(chapters),
                selection_count=len(chapters), first_label=chapters[0].label.text,
                last_label=chapters[-1].label.text, requested=(1, 3), effective=(1, 3), formats=("pdf",),
                grouping_mode="separate", chunk_size=10)
    base.update(kw)
    return RunHeader(**base)


def _chapters(n=3):
    return [chapter_preflight.identity_from_meta(SOURCE, ChapterMeta(index=i, url=url(i), slug=i))
            for i in range(1, n + 1)]


def _publish(journal, key="chapter-00001", path="PDF/Chapter 1_ T.pdf"):
    return journal.publish(key=key, fmt="pdf", path=path, size=3, sha256="0" * 64, pages=[0], missing=[])


def test_a_journal_round_trips_header_records_and_selection(tmp_path):
    chapters = _chapters()
    journal = RunJournal.create(tmp_path, _header(chapters), chapters)
    _publish(journal)
    _publish(journal, path="PDF/Chapter 1_ T2.pdf")              # a later record supersedes
    journal.failure(position=2, page_url=url(3), label="3", reason="HTTP 503")
    journal.end("complete_with_problems", files=1)
    replay = replay_journal(tmp_path / JOURNAL_FILENAME)
    assert replay.header.source == SOURCE and replay.header.selection_count == 3
    assert replay.publishes[("chapter-00001", "pdf")]["path"] == "PDF/Chapter 1_ T2.pdf"
    assert [f["label"] for f in replay.failures] == ["3"] and replay.last_end == "complete_with_problems"
    assert load_selection(tmp_path, replay.header) == chapters
    for line in (tmp_path / JOURNAL_FILENAME).read_text(encoding="utf-8").splitlines():
        assert "Body" not in line                                # never chapter text


def test_a_folder_with_a_run_cannot_take_a_second_one(tmp_path):
    chapters = _chapters()
    RunJournal.create(tmp_path, _header(chapters), chapters)
    with pytest.raises(JournalError, match="already holds a run"):
        RunJournal.create(tmp_path, _header(chapters), chapters)


def test_a_torn_last_line_is_ignored_and_trimmed_before_the_next_record(tmp_path):
    chapters = _chapters()
    journal = RunJournal.create(tmp_path, _header(chapters), chapters)
    _publish(journal)
    path = tmp_path / JOURNAL_FILENAME
    path.write_bytes(path.read_bytes() + b'{"seq": 2, "type": "publ')   # a crash mid-append
    replay = replay_journal(path)
    assert replay.torn_tail and replay.next_seq == 2
    RunJournal.resume(tmp_path, replay).end("cancelled")
    again = replay_journal(path)
    assert not again.torn_tail and again.last_end == "cancelled"


@pytest.mark.parametrize("damage", ["crc", "seq", "garbage"])
def test_a_damaged_line_before_the_end_makes_the_journal_unusable(tmp_path, damage):
    chapters = _chapters()
    journal = RunJournal.create(tmp_path, _header(chapters), chapters)
    _publish(journal)
    journal.end("complete")
    path = tmp_path / JOURNAL_FILENAME
    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[1])
    if damage == "crc":
        record["size"] = 999                                    # the CRC no longer matches
        lines[1] = json.dumps(record)
    elif damage == "seq":
        record["seq"] = 7
        lines[1] = json.dumps(record)
    else:
        lines[1] = "not json at all"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(JournalError, match="Line 2"):
        replay_journal(path)


def test_a_publish_record_cannot_point_outside_the_run_folder(tmp_path):
    chapters = _chapters()
    journal = RunJournal.create(tmp_path, _header(chapters), chapters)
    _publish(journal, path="PDF/../../escape.pdf")
    journal.end("complete")
    with pytest.raises(JournalError, match="unexpected file"):
        replay_journal(tmp_path / JOURNAL_FILENAME)


def test_an_edited_selection_is_refused(tmp_path):
    chapters = _chapters()
    RunJournal.create(tmp_path, _header(chapters), chapters)
    raw = json.loads((tmp_path / SELECTION_FILENAME).read_text(encoding="utf-8"))
    raw["chapters"][0]["label"] = "99"
    (tmp_path / SELECTION_FILENAME).write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(JournalError, match="changed since the run began"):
        load_selection(tmp_path, replay_journal(tmp_path / JOURNAL_FILENAME).header)


# ── run folders ───────────────────────────────────────────────────────────────
def test_new_runs_get_their_own_numbered_folders_per_novel(tmp_path, store):
    first = new_run(tmp_path, store, Site(3), end=3)
    second = new_run(tmp_path, store, Site(3), end=3)
    assert (first.output_dir.name, second.output_dir.name) == ("shadow-slave-1", "shadow-slave-2")
    for folder in (first.output_dir, second.output_dir):
        assert (folder / RESERVATION_FILENAME).is_file() and (folder / JOURNAL_FILENAME).is_file()
        assert len(pdfs(folder)) == 3


def test_a_refused_run_reserves_no_folder(tmp_path, store):
    with pytest.raises(chapter_preflight.ChapterListRefused):
        new_run(tmp_path, store, Site(3), start=10, end=12)
    assert not (tmp_path / "out").exists() or list((tmp_path / "out").iterdir()) == []


def test_old_folders_are_left_alone_and_never_reused(tmp_path, store):
    out = tmp_path / "out"
    (out / "shadow-slave-1").mkdir(parents=True)
    (out / "shadow-slave-1" / "Chapter 1_ Old.pdf").write_bytes(b"pre-0.3.0 output")
    report = new_run(tmp_path, store, Site(2), end=2)
    assert report.output_dir.name == "shadow-slave-2"
    assert (out / "shadow-slave-1" / "Chapter 1_ Old.pdf").read_bytes() == b"pre-0.3.0 output"
    with pytest.raises(RunStorageError, match="reservation marker"):
        resume(store, out / "shadow-slave-1", Site(2))


# ── resume after interruption (both pipeline paths, every grouping) ────────────
@pytest.mark.parametrize("mode", list(OutputMode))
def test_a_stopped_run_resumes_only_what_is_missing(tmp_path, store, mode):
    cancel = threading.Event()
    first_site = Site(6, cancel=cancel, stop_at=4)
    first = run(store, job(tmp_path / "out", allocate_run=True, mode=mode), first_site, cancel=cancel)
    assert first.cancelled
    folder = first.output_dir
    before = set(pdfs(folder))
    if mode is OutputMode.SINGLE:
        assert before == set()                                  # never a partial grouped file on Stop

    second_site = Site(6)
    logs: list[str] = []
    second = resume(store, folder, second_site, logs=logs, mode=mode)
    assert second.resumed and not second.cancelled and second_site.built == 0
    expected = {OutputMode.SEPARATE: 6, OutputMode.CHUNKED: 3, OutputMode.SINGLE: 1}[mode]
    assert len(pdfs(folder)) == expected
    assert before <= set(pdfs(folder))                          # nothing earlier was lost
    # pages already safely in a finished file were not fetched again
    saved_first = {OutputMode.SEPARATE: {1, 2, 3, 4}, OutputMode.CHUNKED: {1, 2, 3, 4},
                   OutputMode.SINGLE: set()}[mode]
    assert set(second_site.fetched) == set(range(1, 7)) - saved_first
    assert replay_journal(folder / JOURNAL_FILENAME).last_end == "complete"


def test_resume_never_changes_the_runs_original_selection(tmp_path, store):
    first = new_run(tmp_path, store, Site(5, fail={5}), end=10**9)
    assert first.effective_range == (1, 5) and first.failed == [5]
    grown = Site(9)                                             # the site published 6-9 since
    second = resume(store, first.output_dir, grown, end=10**9)
    assert grown.fetched == [5] and grown.built == 0
    assert len(pdfs(first.output_dir)) == 5


def test_a_failed_page_inside_a_group_is_added_by_a_later_resume(tmp_path, store):
    first = new_run(tmp_path, store, Site(4, fail={3}), end=4, mode=OutputMode.CHUNKED)
    folder = first.output_dir
    replay = replay_journal(folder / JOURNAL_FILENAME)
    assert replay.publishes[("chunk-00002", "pdf")]["missing"] == [2]       # published as far as it goes
    assert replay.last_end == "complete_with_problems"
    site = Site(4)
    resume(store, folder, site, mode=OutputMode.CHUNKED)
    assert site.fetched == [3, 4]                               # only the incomplete group again
    after = replay_journal(folder / JOURNAL_FILENAME)
    assert after.publishes[("chunk-00002", "pdf")]["missing"] == []
    assert pdfs(folder) == ["Shadow_Slave_Chapters_1-2.pdf", "Shadow_Slave_Chapters_3-4.pdf"]


def test_a_crash_after_writing_a_file_but_before_recording_it_re_renders_it(tmp_path, store, monkeypatch):
    real_publish = RunJournal.publish
    calls = {"n": 0}

    def crash_on_third(self, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt("power cut")             # after os.replace, before the record
        return real_publish(self, **kw)

    monkeypatch.setattr(RunJournal, "publish", crash_on_third)
    with pytest.raises(KeyboardInterrupt):
        new_run(tmp_path, store, Site(4), end=4)
    monkeypatch.setattr(RunJournal, "publish", real_publish)
    folder = tmp_path / "out" / "shadow-slave-1"
    assert "Chapter 3_ Title 3.pdf" in pdfs(folder)            # on disk, but unrecorded
    assert replay_journal(folder / JOURNAL_FILENAME).last_end == "failed"
    site = Site(4)
    resume(store, folder, site, end=4)
    assert site.fetched == [3, 4]                               # the unverified file is rendered again
    assert replay_journal(folder / JOURNAL_FILENAME).last_end == "complete"


def test_an_output_edited_after_publication_is_rendered_again(tmp_path, store):
    first = new_run(tmp_path, store, Site(3), end=3)
    target = first.output_dir / "PDF" / "Chapter 2_ Title 2.pdf"
    target.write_bytes(target.read_bytes()[:-50])               # truncated by something else
    site = Site(3)
    resume(store, first.output_dir, site, end=3)
    assert site.fetched == [2]


def test_orphan_temp_files_are_removed_on_resume(tmp_path, store):
    first = new_run(tmp_path, store, Site(2), end=2)
    orphan = first.output_dir / "PDF" / "Chapter 2_ Title 2.pdf.abc123.part"
    orphan.write_bytes(b"half")
    logs: list[str] = []
    resume(store, first.output_dir, Site(2), logs=logs, end=2)
    assert not orphan.exists() and any("interrupted temporary file" in line for line in logs)


# ── resume refusals ───────────────────────────────────────────────────────────
def _made(tmp_path, store, **kw):
    return new_run(tmp_path, store, Site(3), end=3, **kw).output_dir


def test_resume_refuses_other_formats_or_grouping(tmp_path, store):
    folder = _made(tmp_path, store)
    with pytest.raises(JournalError, match="output formats differ"):
        resume(store, folder, Site(3), formats=("pdf", "txt"))
    with pytest.raises(JournalError, match="different grouping"):
        resume(store, folder, Site(3), mode=OutputMode.SINGLE)
    chunked = _made(tmp_path, store, mode=OutputMode.CHUNKED)
    with pytest.raises(JournalError, match="different grouping"):
        resume(store, chunked, Site(3), mode=OutputMode.CHUNKED, chunk=3)


def test_resume_refuses_another_source(tmp_path, store, monkeypatch):
    folder = _made(tmp_path, store)
    moved = replace(SPEC, url="https://freewebnovel.com/novel/shadow-slave-renamed")
    monkeypatch.setattr(pipeline.catalog, "get_spec", lambda *_a, **_k: moved)
    with pytest.raises(JournalError, match="different source.*index_url"):
        resume(store, folder, Site(3))


def test_resume_refuses_a_run_of_another_novel(tmp_path, store):
    folder = _made(tmp_path, store)
    other = ScrapeJob(novel_slug="reverend-insanity", adapter_key=KEY, start=1, end=3, delay=0.0,
                      output_mode=OutputMode.SEPARATE, use_cache=False, output_dir=folder, resume=True)
    with pytest.raises(RunStorageError, match="different novel"):
        pipeline.run_scrape(other, adapter=Site(3), log=lambda _m: None, manifest_store=store)


def test_resume_refuses_a_damaged_journal_and_changes_nothing(tmp_path, store):
    folder = _made(tmp_path, store)
    path = folder / JOURNAL_FILENAME
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].replace('"pdf"', '"txt"', 1)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    before = {p.name: p.read_bytes() for p in (folder / "PDF").iterdir()}
    site = Site(3)
    with pytest.raises(JournalError, match="damaged"):
        resume(store, folder, site)
    assert site.fetched == [] and {p.name: p.read_bytes() for p in (folder / "PDF").iterdir()} == before


def test_resume_refuses_when_the_sites_list_now_contradicts_the_run(tmp_path, store):
    folder = _made(tmp_path, store)
    from webnovel_scraper.chapter_manifest import ChapterManifest, Provenance
    from webnovel_scraper.source_identity import ChapterLabel

    moved = [SOURCE.chapter_identity(url(n), ChapterLabel(str(n + 1)), slug=n) for n in (1, 2, 3)]
    store.refresh_runtime(SOURCE)
    store.save_runtime(ChapterManifest.create(
        layer="runtime", source=SOURCE, chapters=moved, spans=[(2, 4)],
        provenance=Provenance(generated_utc="2026-10-18T00:00:00Z", validated_utc=None,
                              app_version="0.3.0", method="toc")))
    with pytest.raises(JournalError, match="chapter list changed since this run began"):
        resume(store, folder, Site(3))


def test_two_windows_cannot_work_in_one_run_folder(tmp_path, store):
    folder = _made(tmp_path, store)
    with FileLock(folder / LOCK_FILENAME):
        with pytest.raises(RunStorageError, match="already being used"):
            resume(store, folder, Site(3))


def test_a_new_run_into_a_folder_that_holds_a_run_is_refused(tmp_path, store):
    folder = _made(tmp_path, store)
    with pytest.raises(JournalError, match="already holds a run"):
        run(store, job(folder, end=3), Site(3))


# ── the audit ─────────────────────────────────────────────────────────────────
def test_every_session_leaves_an_audit_of_expected_versus_saved(tmp_path, store):
    first = new_run(tmp_path, store, Site(4, fail={2}), end=4, mode=OutputMode.CHUNKED)
    audit = json.loads((first.output_dir / pipeline.AUDIT_FILENAME).read_text(encoding="utf-8"))
    rows = {row["label"]: row for row in audit["chapters"]}
    assert [row["position"] for row in audit["chapters"]] == [0, 1, 2, 3]
    assert rows["2"]["status"] == "failed" and "503" in rows["2"]["reason"]
    assert rows["1"]["status"] == "saved"                      # in the 1-2 file, which lacks 2
    assert rows["1"]["outputs"]["pdf"]["complete_file"] is False
    assert rows["3"]["status"] == "saved" and rows["3"]["outputs"]["pdf"]["file"].endswith("3-4.pdf")
    assert "Body paragraph" not in json.dumps(audit)            # no chapter text in the audit
    resume(store, first.output_dir, Site(4), mode=OutputMode.CHUNKED)
    audit = json.loads((first.output_dir / pipeline.AUDIT_FILENAME).read_text(encoding="utf-8"))
    assert {row["status"] for row in audit["chapters"]} == {"saved", "resumed"}


# ── the FreeWebNovel rescue path resumes the same way ─────────────────────────
class _Manager:
    def __init__(self, headless):
        self.headless, self.last_fetch_info = headless, None

    def close(self):
        pass


class _Primary(Site):
    def __init__(self, manager, shared):
        self.__dict__ = shared.__dict__                        # share counters with the test

    def fetch_chapter(self, meta, spec, *, fast_path=False):
        return Site.fetch_chapter(self, meta, spec)


def rescue(store, the_job, shared, cancel):
    clock = [0.0]
    limiter = HostRateLimiter(0.0, monotonic=lambda: clock[0], sleep=lambda s: None)
    return pipeline.run_scrape(
        replace(the_job, use_browser=True, headless=False), log=lambda _m: None, cancel_event=cancel,
        host_limiter=limiter, manifest_store=store, sleep_fn=lambda _s: None, monotonic_fn=lambda: clock[0],
        request_manager_factory=lambda *, headless: _Manager(headless),
        primary_adapter_factory=lambda m: _Primary(m, shared),
        rescue_pool_factory=lambda: rp.RescuePool(
            primary_headless=False, host_limiter=limiter, cancel_event=cancel,
            monotonic=lambda: clock[0], sleep=lambda _s: None,
            fetch_fn=lambda meta, **_k: pytest.fail("nothing needs rescue")))


@pytest.mark.parametrize("mode", list(OutputMode))
def test_the_rescue_path_resumes_a_stopped_run(tmp_path, store, mode):
    cancel = threading.Event()
    first_site = Site(6, cancel=cancel, stop_at=3)
    first = rescue(store, job(tmp_path / "out", allocate_run=True, mode=mode), first_site, cancel)
    assert first.cancelled
    second_site = Site(6)
    second = rescue(store, job(first.output_dir, resume=True, mode=mode), second_site, threading.Event())
    assert second.resumed and second_site.built == 0
    assert len(pdfs(first.output_dir)) == {OutputMode.SEPARATE: 6, OutputMode.CHUNKED: 3,
                                           OutputMode.SINGLE: 1}[mode]
    assert replay_journal(first.output_dir / JOURNAL_FILENAME).last_end == "complete"


# ── finding runs to resume ────────────────────────────────────────────────────
def test_only_unfinished_runs_of_this_version_are_offered(tmp_path, store):
    from webnovel_scraper import run_index

    out = tmp_path / "out"
    (out / "shadow-slave-9").mkdir(parents=True)                # a pre-0.3.0 folder: never offered
    (out / "shadow-slave-9" / "Chapter 1_ Old.pdf").write_bytes(b"old")
    finished = new_run(tmp_path, store, Site(2), end=2)
    unfinished = new_run(tmp_path, store, Site(3, fail={3}), end=3)
    offered = run_index.list_runs(out)
    assert [s.path for s in offered] == [unfinished.output_dir]
    summary = offered[0]
    assert (summary.files_done, summary.files_total, summary.last_end) == (2, 3, "complete_with_problems")
    text = summary.describe("Shadow Slave")
    assert text.startswith("shadow-slave-2: Shadow Slave from freewebnovel.com, chapters 1-3 (3), PDF")
    assert "2 of 3 files saved; finished with problems" in text
    everything = run_index.list_runs(out, unfinished_only=False)
    assert {s.path for s in everything} == {finished.output_dir, unfinished.output_dir}
    assert run_index.list_runs(tmp_path / "missing") == []


def test_two_groups_that_would_share_a_name_never_overwrite_each_other(tmp_path, store):
    """Two pages both show chapter 3664 (Supreme Magus): with one chapter per file their grouped
    names would collide, so the second file carries its group key and neither is overwritten."""

    class Twins(Site):
        def build_chapter_index(self, spec, **_kw):
            return [ChapterMeta(index=3664, url=url(s), slug=s, source_id=str(s)) for s in (3678, 3679)]

        def fetch_chapter(self, meta, spec, **_kw):
            return ChapterContent(index=3664, title="Twisted Reflection",
                                  paragraphs=[f"Distinct body from slug {meta.slug}."])

    report = new_run(tmp_path, store, Twins(0), start=3664, end=3664, mode=OutputMode.CHUNKED, chunk=1)
    names = pdfs(report.output_dir)
    assert names == ["Shadow_Slave_Chapters_3664-3664 (chunk-00002).pdf", "Shadow_Slave_Chapters_3664-3664.pdf"]
    assert len(report.written) == 2
