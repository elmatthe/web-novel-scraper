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


def test_existing_double_dot_degraded_pdf_still_matches_resume(tmp_path) -> None:
    old_name = tmp_path / "Chapter 7..pdf"
    old_name.write_bytes(b"old pdf")

    assert pipeline._existing_separate_pdf(tmp_path, 7) == old_name


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
