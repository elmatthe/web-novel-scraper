"""Regression tests for low-risk 0.2.1 Phase D cleanup findings."""

from __future__ import annotations

import threading
import time

from webnovel_scraper import pipeline
from webnovel_scraper import rescue_pool as rp
from webnovel_scraper.models import ChapterContent
from webnovel_scraper.rescue_pool import RescuePool


def test_degraded_separate_filename_has_single_dot_pdf_suffix() -> None:
    content = ChapterContent(
        index=7,
        title="",
        paragraphs=["Body text long enough for a degraded chapter."],
    )

    assert pipeline._separate_pdf_name(content) == "Chapter 7.pdf"


def test_a_stray_pdf_with_a_matching_name_is_never_taken_as_already_saved(tmp_path) -> None:
    """v0.3.0 03.3 (plan §9.5): only a journal record whose checksum matches counts. The
    pre-0.3.0 filename scan (which matched "Chapter 7..pdf" for chapter 7) is gone."""
    assert not hasattr(pipeline, "_existing_separate_pdf")
    from webnovel_scraper.models import ChapterMeta, OutputMode
    from webnovel_scraper.run_journal import RunHeader, RunJournal, selection_sha256
    from webnovel_scraper.run_output import RunOutputs
    from webnovel_scraper.source_identity import SourceIdentity
    from webnovel_scraper import catalog, chapter_preflight

    spec = catalog.get_spec("shadow-slave", "freewebnovel")
    source = SourceIdentity.from_site_spec(spec)
    meta = ChapterMeta(index=7, url=f"{spec.url}/chapter-7", slug=7)
    chapters = [chapter_preflight.identity_from_meta(source, meta)]
    header = RunHeader(novel_slug="shadow-slave", run_number=0, source=source,
                       selection_sha256=selection_sha256(chapters), selection_count=1,
                       first_label="7", last_label="7", requested=(7, 7), effective=(7, 7),
                       formats=("pdf",), grouping_mode="separate", chunk_size=10)
    journal = RunJournal.create(tmp_path, header, chapters)
    (tmp_path / "PDF").mkdir()
    (tmp_path / "PDF" / "Chapter 7.pdf").write_bytes(b"old pdf")
    outputs = RunOutputs(run_dir=tmp_path, journal=journal, metas=[meta], labels=["7"],
                         mode=OutputMode.SEPARATE, chunk_size=10, formats=("pdf",), title="SS",
                         stem="SS", render=lambda *a: None, report=None, log=lambda _m: None)
    assert outputs.plan_fetch() == [meta]       # still needed: the file proves nothing


def test_rescue_pool_start_is_idempotent_under_concurrent_callers(monkeypatch) -> None:
    real_thread = threading.Thread
    created: list[object] = []
    init_entered = threading.Event()
    release_init = threading.Event()

    class SpyThread:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs
            self.started = False
            created.append(self)
            init_entered.set()
            release_init.wait(1.0)

        def start(self) -> None:
            self.started = True

        def join(self, _timeout=None) -> None:
            return None

        def is_alive(self) -> bool:
            return False

    monkeypatch.setattr(rp.threading, "Thread", SpyThread)
    pool = RescuePool(
        primary_headless=True,
        fetch_fn=lambda *args, **kwargs: None,
        log=lambda _m: None,
    )
    ready = threading.Barrier(3)

    def call_start() -> None:
        ready.wait()
        pool.start()

    callers = [
        real_thread(target=call_start, name="start-caller-1"),
        real_thread(target=call_start, name="start-caller-2"),
    ]
    for caller in callers:
        caller.start()
    ready.wait()

    assert init_entered.wait(1.0)
    time.sleep(0.05)
    assert len(created) == 1

    release_init.set()
    for caller in callers:
        caller.join(1.0)

    assert len(created) == 1
    assert created[0].started is True
