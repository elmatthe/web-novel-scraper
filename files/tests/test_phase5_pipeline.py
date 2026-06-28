"""Phase 5 tests: the pipeline orchestration. All offline.

The pipeline is driven with a **fake adapter** (no network, no real site) that
returns fixture-shaped ``ChapterMeta`` / ``ChapterContent``, so these tests
exercise the real run policy — TOC persistence, range clamping, the three output
modes, resume (skip existing PDFs), the pipeline-layer disabled-adapter refusal,
and cancellation — while still building real PDFs through the real PDF builder.
"""

from __future__ import annotations

import threading

import pytest

from webnovel_scraper import catalog, pipeline
from webnovel_scraper.models import (
    ChapterContent,
    ChapterMeta,
    OutputMode,
    ScrapeJob,
)
from webnovel_scraper.registry import AdapterDisabledError

ENABLED_NOVEL = "shadow-slave"
ENABLED_KEY = "freewebnovel"


# ── Fakes ────────────────────────────────────────────────────────────────────
class FakeAdapter:
    """Offline stand-in for a real adapter. Records what it built/fetched so a
    test can prove the network (here, the adapter) was or was not touched."""

    def __init__(self, count: int = 5, on_fetch=None) -> None:
        self.count = count
        self.on_fetch = on_fetch
        self.built = 0
        self.fetched: list[int] = []
        self.warnings: list[str] = []

    def build_chapter_index(self, spec) -> list[ChapterMeta]:
        self.built += 1
        return [
            ChapterMeta(index=n, url=f"https://example/{n}", source_id=str(n))
            for n in range(1, self.count + 1)
        ]

    def fetch_chapter(self, meta, spec) -> ChapterContent:
        self.fetched.append(meta.index)
        if self.on_fetch is not None:
            self.on_fetch(meta.index)
        return ChapterContent(
            index=meta.index,
            title=f"Title {meta.index}",
            paragraphs=[
                f"First body paragraph for chapter {meta.index}, long enough to render.",
                f"Second body paragraph for chapter {meta.index}, also clearly prose.",
            ],
        )


class ExplodingAdapter:
    """Asserts loudly if any of its methods are ever called — used to prove the
    pipeline refuses a disabled site before touching the adapter."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def build_chapter_index(self, spec):  # pragma: no cover - must not run
        raise AssertionError("build_chapter_index called for a disabled site")

    def fetch_chapter(self, meta, spec):  # pragma: no cover - must not run
        raise AssertionError("fetch_chapter called for a disabled site")


def _job(tmp_path, mode: OutputMode, *, start=1, end=5, chunk_size=10) -> ScrapeJob:
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


def _pdfs(tmp_path) -> list[str]:
    return sorted(p.name for p in tmp_path.glob("*.pdf"))


# ── Output modes ─────────────────────────────────────────────────────────────
def test_separate_mode_writes_one_pdf_per_chapter(tmp_path) -> None:
    adapter = FakeAdapter(count=5)
    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.SEPARATE), adapter=adapter, log=lambda m: None
    )

    assert adapter.fetched == [1, 2, 3, 4, 5]
    assert len(report.written) == 5
    assert len(_pdfs(tmp_path)) == 5
    assert all(name.startswith("Chapter ") for name in _pdfs(tmp_path))
    assert report.failed == []
    # The TOC was persisted for resume.
    assert (tmp_path / pipeline.INDEX_FILENAME).is_file()


def test_chunked_mode_groups_chapters(tmp_path) -> None:
    adapter = FakeAdapter(count=5)
    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.CHUNKED, chunk_size=2),
        adapter=adapter,
        log=lambda m: None,
    )

    # 5 chapters / 2 per file -> [1-2], [3-4], [5-5].
    assert _pdfs(tmp_path) == [
        "Shadow_Slave_Chapters_1-2.pdf",
        "Shadow_Slave_Chapters_3-4.pdf",
        "Shadow_Slave_Chapters_5-5.pdf",
    ]
    assert len(report.written) == 3
    assert adapter.fetched == [1, 2, 3, 4, 5]


def test_single_mode_writes_one_pdf(tmp_path) -> None:
    adapter = FakeAdapter(count=5)
    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.SINGLE), adapter=adapter, log=lambda m: None
    )

    assert _pdfs(tmp_path) == ["Shadow_Slave_All_Chapters.pdf"]
    assert len(report.written) == 1
    assert adapter.fetched == [1, 2, 3, 4, 5]


# ── Resume ───────────────────────────────────────────────────────────────────
def test_resume_skips_existing_separate_pdfs(tmp_path) -> None:
    first = FakeAdapter(count=5)
    pipeline.run_scrape(
        _job(tmp_path, OutputMode.SEPARATE), adapter=first, log=lambda m: None
    )
    assert len(_pdfs(tmp_path)) == 5

    # Second run into the SAME dir: nothing should be fetched, the index should
    # be loaded from disk (build not called again), and every chapter is skipped.
    second = FakeAdapter(count=5)
    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.SEPARATE), adapter=second, log=lambda m: None
    )

    assert second.built == 0  # TOC loaded from chapter_index.json, not rebuilt
    assert second.fetched == []  # no chapter re-fetched
    assert sorted(report.skipped_existing) == [1, 2, 3, 4, 5]
    assert report.written == []
    assert len(_pdfs(tmp_path)) == 5  # unchanged


def test_resume_skips_existing_single_pdf(tmp_path) -> None:
    pipeline.run_scrape(
        _job(tmp_path, OutputMode.SINGLE), adapter=FakeAdapter(count=3),
        log=lambda m: None,
    )
    second = FakeAdapter(count=3)
    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.SINGLE), adapter=second, log=lambda m: None
    )
    assert second.fetched == []
    assert report.written == []
    assert sorted(report.skipped_existing) == [1, 2, 3]


# ── Range clamping ───────────────────────────────────────────────────────────
def test_range_clamped_to_available_toc(tmp_path) -> None:
    adapter = FakeAdapter(count=5)
    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.SEPARATE, start=3, end=99),
        adapter=adapter,
        log=lambda m: None,
    )

    assert report.effective_range == (3, 5)
    assert adapter.fetched == [3, 4, 5]
    assert len(_pdfs(tmp_path)) == 3


def test_exhausted_chapter_failure_recorded_and_run_continues(tmp_path) -> None:
    def fail_chapter_2(index: int) -> None:
        if index == 2:
            raise RuntimeError("retry ladder exhausted")

    adapter = FakeAdapter(count=3, on_fetch=fail_chapter_2)
    log_lines: list[str] = []
    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.SEPARATE, end=3),
        adapter=adapter,
        log=log_lines.append,
        sleep_fn=lambda _s: None,  # the block triggers an auto-slowdown sleep
    )

    # Main pass fetches 1,2,3; the second-pass sweep re-attempts only the failed
    # chapter 2, which fails again and stays failed (it is not a permanent 404).
    assert adapter.fetched == [1, 2, 3, 2]
    assert report.failed == [2]
    assert report.rescued == []
    assert len(report.written) == 2
    assert len(_pdfs(tmp_path)) == 2
    assert "failed chapters: 2" in report.summary()
    assert any("chapter 2 FAILED" in line for line in log_lines)
    assert any("Second-pass sweep" in line for line in log_lines)


# ── Disabled-adapter refusal (defense in depth) ──────────────────────────────
def test_disabled_adapter_refused_and_never_called(tmp_path) -> None:
    spy = ExplodingAdapter()
    job = ScrapeJob(
        novel_slug="shadow-slave",
        adapter_key="empire_novel",  # a disabled catalog row
        start=1,
        end=5,
        delay=0.0,
        output_mode=OutputMode.SINGLE,
        use_cache=False,
        output_dir=tmp_path,
    )

    with pytest.raises(AdapterDisabledError):
        pipeline.run_scrape(job, adapter=spy, log=lambda m: None)

    # The stub's methods were never reached (ExplodingAdapter would have raised
    # AssertionError instead of AdapterDisabledError), and no output was created.
    assert _pdfs(tmp_path) == []


def test_disabled_check_precedes_adapter_for_every_stub(tmp_path) -> None:
    for key in ("empire_novel", "novel_bin", "telegraph"):
        spec = [
            s
            for s in catalog.all_specs()
            if s.adapter_key == key and not s.enabled
        ][0]
        job = ScrapeJob(
            novel_slug=spec.novel_slug,
            adapter_key=key,
            start=1,
            end=3,
            delay=0.0,
            output_mode=OutputMode.SEPARATE,
            use_cache=False,
            output_dir=tmp_path,
        )
        with pytest.raises(AdapterDisabledError):
            pipeline.run_scrape(job, adapter=ExplodingAdapter(), log=lambda m: None)


# ── Cancellation ─────────────────────────────────────────────────────────────
def test_cancel_event_stops_iteration(tmp_path) -> None:
    cancel = threading.Event()

    # Cancel right after the first chapter is fetched; the loop must stop before
    # fetching the rest.
    def stop_after_first(index: int) -> None:
        if index == 1:
            cancel.set()

    adapter = FakeAdapter(count=5, on_fetch=stop_after_first)
    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.SEPARATE),
        adapter=adapter,
        cancel_event=cancel,
        log=lambda m: None,
    )

    assert report.cancelled is True
    assert adapter.fetched == [1]  # chapter 2+ never fetched
    # Only chapter 1's PDF made it to disk.
    assert len(_pdfs(tmp_path)) == 1


def test_preset_cancel_writes_nothing(tmp_path) -> None:
    cancel = threading.Event()
    cancel.set()  # cancelled before the first chapter
    adapter = FakeAdapter(count=5)
    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.SINGLE),
        adapter=adapter,
        cancel_event=cancel,
        log=lambda m: None,
    )

    assert report.cancelled is True
    assert adapter.fetched == []
    assert _pdfs(tmp_path) == []


# ── Output-dir resolution ────────────────────────────────────────────────────
def test_resolve_output_dir_default_is_slug_and_auto_increments(tmp_path) -> None:
    # Default name is now the novel slug (was "webscraped_{slug}").
    d1 = pipeline.resolve_output_dir("shadow-slave", downloads_root=tmp_path)
    assert d1 == tmp_path / "shadow-slave-1"
    d1.mkdir()
    d2 = pipeline.resolve_output_dir("shadow-slave", downloads_root=tmp_path)
    assert d2 == tmp_path / "shadow-slave-2"


def test_resolve_output_dir_custom_parent_and_name(tmp_path) -> None:
    parent = tmp_path / "MyNovels"
    parent.mkdir()
    d1 = pipeline.resolve_output_dir(
        "shadow-slave", parent_dir=parent, base_name="Shadow Slave Books"
    )
    # Uses the chosen parent + sanitised custom name, with the -N increment.
    assert d1.parent == parent
    assert d1.name == "Shadow Slave Books-1"
    d1.mkdir()
    d2 = pipeline.resolve_output_dir(
        "shadow-slave", parent_dir=parent, base_name="Shadow Slave Books"
    )
    assert d2.name == "Shadow Slave Books-2"


def test_resolve_output_dir_blank_name_falls_back_to_slug(tmp_path) -> None:
    d = pipeline.resolve_output_dir(
        "shadow-slave", parent_dir=tmp_path, base_name="   "
    )
    assert d == tmp_path / "shadow-slave-1"


# ── Nesting regression (0.1.1 doubled-folder bug) ────────────────────────────
def test_resolve_output_dir_default_is_single_level_not_nested(tmp_path) -> None:
    """A default run lands directly under the Downloads-equivalent base — exactly
    one directory deep, never inside a prior run's folder."""
    d = pipeline.resolve_output_dir("shadow-slave", downloads_root=tmp_path)
    assert d == tmp_path / "shadow-slave-1"
    assert d.parent == tmp_path  # top level of the base, not nested


def test_resolve_output_dir_increment_is_sibling_not_nested(tmp_path) -> None:
    """The second default run is a SIBLING of the first under the same parent —
    never created inside the first run's folder (the doubled-folder bug)."""
    d1 = pipeline.resolve_output_dir("shadow-slave", downloads_root=tmp_path)
    d1.mkdir()
    d2 = pipeline.resolve_output_dir("shadow-slave", downloads_root=tmp_path)
    assert d2 == tmp_path / "shadow-slave-2"
    assert d2.parent == d1.parent == tmp_path
    assert d1 not in d2.parents  # d2 is NOT nested inside d1


def test_resolve_output_dir_ignores_old_webscraped_folders(tmp_path) -> None:
    """The pre-0.1.1 ``webscraped_{slug}-N`` folders must not interfere with the
    new ``{slug}-N`` scan: a fresh default run still yields ``{slug}-1`` and never
    nests inside (or matches) a leftover ``webscraped_`` folder."""
    (tmp_path / "webscraped_shadow-slave-1").mkdir()
    (tmp_path / "webscraped_shadow-slave-2").mkdir()
    d = pipeline.resolve_output_dir("shadow-slave", downloads_root=tmp_path)
    assert d == tmp_path / "shadow-slave-1"
    assert d.parent == tmp_path
    # Critically NOT nested inside the leftover folder.
    assert d != tmp_path / "webscraped_shadow-slave-1" / "shadow-slave-1"
