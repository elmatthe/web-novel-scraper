"""v0.3.0 Checkpoint 02.2: the Run section's progress model (``ui/run_progress.py``).

Pure and offline: no Tk, no threads, no real clock (a fake monotonic clock is passed in or
timestamps are given explicitly), so nothing here is timing-sensitive.
"""

from __future__ import annotations

import math

import pytest

from webnovel_scraper.ui import run_activity as ra
from webnovel_scraper.ui import run_progress as rp
from webnovel_scraper.ui.run_progress import FinalCounts, RunTracker


class _Clock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def now(self) -> float:
        return self.t


def _tracker() -> tuple[RunTracker, _Clock]:
    clock = _Clock()
    return RunTracker(clock=clock.now), clock


def _scraping(total: int = 40, already_present: int = 0, at: float = 0.0):
    tracker, clock = _tracker()
    tracker.start()
    tracker.phase("scraping", total, already_present, at=at)
    return tracker, clock


# ------------------------------------------------------------------------------ formatting

@pytest.mark.parametrize(("seconds", "text"), [
    (None, "ETA: —"), (-1, "ETA: —"), (math.nan, "ETA: —"), (0, "ETA: 0 s"), (44.6, "ETA: 45 s"),
    (60, "ETA: 1 min"), (95, "ETA: 1 min 35 s"), (600, "ETA: 10 min"), (3 * 3600 + 5 * 60,
                                                                       "ETA: 3 h 5 min"),
])
def test_format_eta(seconds, text) -> None:
    assert rp.format_eta(seconds) == text


@pytest.mark.parametrize(("done", "total", "expected"), [
    (0, 40, 0), (1, 3, 33), (2, 3, 66), (99, 100, 99), (100, 100, 100), (140, 100, 100),
    (0, 0, None), (5, 0, None), (None, 10, None), (5, None, None),
])
def test_percent_never_reaches_100_before_every_chapter_is_done(done, total, expected) -> None:
    assert rp.percent(done, total) == expected


def test_progress_text_is_a_dash_until_a_total_is_known() -> None:
    assert rp.format_progress(None, None) == "—"
    assert rp.format_progress(0, 0) == "—"
    assert rp.format_progress(12, 40) == "12 / 40 · 30%"
    assert rp.format_progress(45, 40) == "40 / 40 · 100%"  # never shown above the total


# ----------------------------------------------------------------------------- the phases

def test_idle_view() -> None:
    view = RunTracker().view()
    assert (view.state, view.status, view.counts, view.eta) == ("idle", "Idle", "—", "ETA: —")
    assert not view.indeterminate and view.fraction is None


def test_preparing_has_no_fake_percentage() -> None:
    tracker, _ = _tracker()
    tracker.start()
    view = tracker.view()
    assert view.state == rp.PREPARING and view.status == "Preparing chapter list…"
    assert view.counts == "—" and view.eta == "ETA: —" and view.detail == ""
    assert view.indeterminate is True and view.fraction is None


def test_a_known_total_makes_progress_determinate() -> None:
    tracker, _ = _scraping(total=40)
    view = tracker.view()
    assert view.state == rp.SCRAPING and view.status == "Scraping chapters"
    assert view.counts == "0 / 40 · 0%" and view.indeterminate is False and view.fraction == 0.0
    tracker.progress(12, 40, at=1.0)
    view = tracker.view()
    assert view.counts == "12 / 40 · 30%" and view.fraction == pytest.approx(0.3)


def test_progress_alone_implies_the_chapters_are_known() -> None:
    tracker, _ = _tracker()
    tracker.start()
    tracker.progress(1, 5, at=1.0)
    assert tracker.view().state == rp.SCRAPING and tracker.view().counts == "1 / 5 · 20%"


@pytest.mark.parametrize("total", [0, -3])
def test_a_progress_update_without_a_usable_total_is_ignored(total) -> None:
    tracker, _ = _tracker()
    tracker.start()
    tracker.progress(2, total)
    view = tracker.view()
    assert view.state == rp.PREPARING and view.counts == "—" and view.indeterminate


def test_building_output_phase_shows_no_eta_and_keeps_the_count() -> None:
    tracker, _ = _scraping(total=40)
    tracker.progress(40, 40, at=1.0)
    tracker.phase("building", 40, 0)
    view = tracker.view()
    assert view.state == rp.BUILDING and view.status == "Building output…"
    assert view.counts == "40 / 40 · 100%" and view.eta == "ETA: —"


def test_a_phase_never_goes_back() -> None:
    tracker, _ = _scraping()
    tracker.phase("building", 40, 0)
    tracker.phase("scraping", 40, 3)  # a delayed routine update from before the build began
    assert tracker.view().state == rp.BUILDING


def test_counts_never_go_backwards() -> None:
    tracker, _ = _scraping()
    tracker.progress(10, 40, at=1.0)
    tracker.progress(7, 40, at=2.0)
    assert tracker.view().done == 10


def test_start_forgets_the_previous_run() -> None:
    tracker, _ = _scraping(total=40, already_present=5)
    tracker.progress(40, 40, at=1.0)
    tracker.finish("completed", counts=FinalCounts(pdfs=35, already_present=5))
    tracker.start()
    view = tracker.view()
    assert view.state == rp.PREPARING and view.counts == "—" and view.detail == ""
    assert tracker.outcome is None and view.indeterminate


# ---------------------------------------------------------------------------- stop and end

def test_stopping_text_depends_on_the_phase() -> None:
    tracker, _ = _tracker()
    tracker.start()
    tracker.stop_requested()
    assert tracker.view().status == "Stopping…" and tracker.view().state == rp.STOPPING
    tracker, _ = _scraping()
    tracker.stop_requested()
    assert tracker.view().status == "Stopping after the current chapter…"
    assert tracker.view().eta == "ETA: —"


def test_stop_when_nothing_runs_is_ignored() -> None:
    tracker = RunTracker()
    tracker.stop_requested()
    assert tracker.view().state == rp.IDLE


def test_cancelled_during_preparation() -> None:
    tracker, _ = _tracker()
    tracker.start()
    tracker.stop_requested()
    tracker.finish("stopped", counts=FinalCounts())
    view = tracker.view()
    assert view.state == rp.CANCELLED and view.status == "Stopped" and view.severity == "info"
    assert view.counts == "—" and not view.indeterminate and view.eta == ""
    assert view.detail == "0 PDFs"


def test_cancelled_during_scraping_keeps_the_partial_count() -> None:
    tracker, _ = _scraping(total=40)
    tracker.progress(12, 40, at=1.0)
    tracker.stop_requested()
    tracker.finish("stopped", processed=(12, 40), counts=FinalCounts(pdfs=12))
    view = tracker.view()
    assert view.state == rp.CANCELLED and view.counts == "12 / 40 · 30%"
    assert view.detail == "12 PDFs" and view.eta == ""


def test_failure_during_output_building_is_not_success_at_100_percent() -> None:
    tracker, _ = _scraping(total=40)
    tracker.progress(40, 40, at=1.0)
    tracker.phase("building", 40, 0)
    tracker.finish("failed", processed=(40, 40))
    view = tracker.view()
    assert view.state == rp.FAILED and view.status == "Run failed" and view.severity == "error"
    assert view.counts == "40 / 40 · 100%" and view.detail == "" and view.eta == ""


def test_refused() -> None:
    tracker, _ = _tracker()
    tracker.start()
    tracker.finish("refused")
    view = tracker.view()
    assert (view.state, view.status, view.severity) == (rp.REFUSED, "Run refused", "error")
    assert view.counts == "—" and not view.indeterminate


@pytest.mark.parametrize(("outcome", "state", "status", "severity"), [
    ("completed", rp.COMPLETE, "Complete", "info"),
    ("completed_with_problems", rp.COMPLETE, "Finished with problems", "warning"),
    ("nothing_written", rp.COMPLETE, "Finished — no PDFs written", "warning"),
    ("unconfirmed", rp.COMPLETE, "Ended — results not confirmed", "warning"),
    ("stopped", rp.CANCELLED, "Stopped", "info"),
    ("failed", rp.FAILED, "Run failed", "error"),
    ("refused", rp.REFUSED, "Run refused", "error"),
])
def test_every_outcome_has_a_distinct_final_state(outcome, state, status, severity) -> None:
    tracker, _ = _scraping()
    tracker.finish(outcome)
    view = tracker.view()
    assert (view.state, view.status, view.severity) == (state, status, severity)
    assert tracker.terminal and not tracker.active


def test_the_outcome_table_covers_exactly_the_activity_outcomes() -> None:
    assert set(rp.OUTCOME_STATES) == set(ra.OUTCOMES) == set(rp.OUTCOME_LABELS)


def test_an_unknown_outcome_is_never_shown_as_success() -> None:
    tracker, _ = _scraping()
    tracker.finish("something-new")
    assert tracker.outcome == "unconfirmed" and tracker.view().severity == "warning"


def test_late_events_cannot_overwrite_the_final_outcome() -> None:
    tracker, _ = _scraping(total=40)
    tracker.progress(20, 40, at=1.0)
    tracker.finish("completed_with_problems", processed=(40, 40),
                   counts=FinalCounts(pdfs=38, failed=2))
    before = tracker.view()
    tracker.progress(21, 40, at=2.0)           # a routine update that arrived late
    tracker.phase("scraping", 40, 4)
    tracker.phase("building", 40, 4)
    tracker.stop_requested()
    tracker.finish("completed")                # and a second finish
    assert tracker.view() == before
    assert before.counts == "40 / 40 · 100%" and before.status == "Finished with problems"


def test_the_workers_last_count_wins_over_a_coalesced_update() -> None:
    tracker, _ = _scraping(total=40)
    tracker.progress(5, 40, at=1.0)            # the only update the window ever drew
    tracker.finish("completed", processed=(40, 40), counts=FinalCounts(pdfs=40))
    assert tracker.view().counts == "40 / 40 · 100%"


# ------------------------------------------------------- chapters, PDFs and other counters

def test_hundred_percent_processed_with_failures_is_not_hundred_percent_successful() -> None:
    tracker, _ = _scraping(total=40)
    tracker.progress(40, 40, at=1.0)
    tracker.finish("completed_with_problems", processed=(40, 40),
                   counts=FinalCounts(pdfs=38, failed=2, recovered=1))
    view = tracker.view()
    assert view.counts == "40 / 40 · 100%"
    assert view.status == "Finished with problems" and view.severity == "warning"
    assert view.detail == "38 PDFs · 2 failed · 1 recovered"


def test_pdfs_are_counted_separately_from_chapters() -> None:
    tracker, _ = _scraping(total=120)
    tracker.progress(120, 120, at=1.0)
    tracker.finish("completed", processed=(120, 120), counts=FinalCounts(pdfs=3))
    view = tracker.view()
    assert view.counts == "120 / 120 · 100%" and view.detail == "3 PDFs"
    tracker.start()
    tracker.phase("scraping", 1, 0)
    tracker.progress(1, 1)
    tracker.finish("completed", counts=FinalCounts(pdfs=1))
    assert tracker.view().detail == "1 PDF"


def test_already_present_chapters_are_counted_but_are_not_failures() -> None:
    tracker, _ = _scraping(total=40, already_present=0)
    tracker.phase("scraping", 40, 12, at=0.0)
    tracker.progress(12, 40, at=0.0)
    view = tracker.view()
    assert view.detail == "12 already present" and view.counts == "12 / 40 · 30%"
    tracker.progress(40, 40, at=100.0)
    tracker.finish("completed", processed=(40, 40),
                   counts=FinalCounts(pdfs=28, already_present=12))
    assert tracker.view().detail == "28 PDFs · 12 already present"
    assert tracker.view().severity == "info"


def test_final_detail_lists_only_what_happened() -> None:
    tracker, _ = _scraping()
    tracker.finish("completed", counts=FinalCounts(pdfs=2, not_listed=3))
    assert tracker.view().detail == "2 PDFs · 3 not on site list"


# ------------------------------------------------------------------------------------ ETA

def _steady(per: float, count: int, *, total: int = 40, already_present: int = 0):
    tracker, _ = _scraping(total=total, already_present=already_present)
    for n in range(1, count + 1):
        tracker.progress(already_present + n, total, at=per * n)
    return tracker


def test_no_estimate_before_enough_chapters_have_been_measured() -> None:
    tracker, _ = _scraping(total=40)
    assert tracker.eta_seconds(now=100.0) is None
    for n in (1, 2):
        tracker.progress(n, 40, at=10.0 * n)
        assert tracker.eta_seconds(now=10.0 * n) is None, n
    tracker.progress(3, 40, at=30.0)
    assert tracker.eta_seconds(now=30.0) == pytest.approx(37 * 10.0)


def test_no_estimate_from_a_burst_of_very_fast_chapters() -> None:
    tracker, _ = _scraping(total=40)
    for n in range(1, 7):
        tracker.progress(n, 40, at=0.5 * n)          # six chapters in three seconds
    assert tracker.eta_seconds(now=3.0) is None      # under ETA_MIN_SECONDS of evidence
    tracker.progress(7, 40, at=5.5)
    assert tracker.eta_seconds(now=5.5) == pytest.approx(33 * (5.5 / 7))


def test_the_eta_text_is_a_dash_until_it_is_known_then_a_time() -> None:
    tracker, clock = _scraping(total=40)
    assert tracker.view().eta == "ETA: —"
    for n in (1, 2, 3):
        tracker.progress(n, 40, at=10.0 * n)
    clock.t = 30.0
    assert tracker.view().eta == "ETA: 6 min 10 s"


def test_a_slowing_run_is_not_promised_its_old_speed() -> None:
    tracker, _ = _scraping(total=100)
    t = 0.0
    for n in range(1, 6):                              # five quick chapters (2 s each)
        t += 2.0
        tracker.progress(n, 100, at=t)
    for n in range(6, 16):                             # then ten slow ones (20 s each)
        t += 20.0
        tracker.progress(n, 100, at=t)
    overall = t / 15
    assert overall == pytest.approx(14.0)
    assert tracker._eta.seconds_per_chapter() == pytest.approx(20.0)   # the recent pace
    assert tracker.eta_seconds(now=t) == pytest.approx(85 * 20.0)


def test_a_speeding_up_run_keeps_the_conservative_overall_pace() -> None:
    tracker, _ = _scraping(total=100)
    t = 0.0
    for n in range(1, 6):
        t += 20.0
        tracker.progress(n, 100, at=t)
    for n in range(6, 16):
        t += 2.0
        tracker.progress(n, 100, at=t)
    assert tracker._eta.seconds_per_chapter() == pytest.approx(t / 15)
    assert tracker.eta_seconds(now=t) == pytest.approx(85 * (t / 15))


def test_irregular_chapter_times_give_a_finite_positive_estimate() -> None:
    tracker, _ = _scraping(total=30)
    t = 0.0
    for n, took in enumerate((1.0, 40.0, 3.0, 3.0, 90.0, 2.0, 2.0, 15.0), start=1):
        t += took
        tracker.progress(n, 30, at=t)
    eta = tracker.eta_seconds(now=t)
    assert eta is not None and math.isfinite(eta) and eta > 0


def test_a_long_silent_stretch_makes_the_estimate_unknown_again() -> None:
    tracker = _steady(10.0, 5)                          # last chapter finished at t=50
    assert tracker.eta_seconds(now=50.0) == pytest.approx(35 * 10.0)
    assert tracker.eta_seconds(now=50.0 + 40.0) is not None
    assert tracker.eta_seconds(now=50.0 + 41.0) is None  # a retry/recovery is dragging on
    tracker.progress(6, 40, at=100.0)                    # ...and it recovers with the next chapter
    assert tracker.eta_seconds(now=100.0) is not None


def test_chapters_that_resume_skipped_do_not_speed_up_the_estimate() -> None:
    # 50 of 100 chapters were already present: they finish in one instant, then real ones take 10 s.
    tracker, _ = _scraping(total=100, already_present=50, at=0.0)
    tracker.progress(50, 100, at=0.0)
    assert tracker.eta_seconds(now=0.0) is None
    for n in (1, 2, 3):
        tracker.progress(50 + n, 100, at=10.0 * n)
    assert tracker.eta_seconds(now=30.0) == pytest.approx(47 * 10.0)


def test_skips_that_arrive_between_fetched_chapters_stay_out_of_the_rate() -> None:
    tracker, _ = _scraping(total=20, at=0.0)
    done = 0
    t = 0.0
    skipped = 0
    for step in range(8):
        if step % 2:
            skipped += 1                                 # the pipeline reports the skip first...
            tracker.phase("scraping", 20, skipped, at=t)
            done += 1
            tracker.progress(done, 20, at=t)             # ...then the tick
        else:
            t += 10.0
            done += 1
            tracker.progress(done, 20, at=t)
    assert tracker._eta.seconds_per_chapter() == pytest.approx(10.0)


def test_the_estimate_ignores_chapter_list_preparation() -> None:
    tracker, clock = _tracker()
    clock.t = 0.0
    tracker.start()
    clock.t = 600.0                                      # ten minutes building the chapter list
    tracker.phase("scraping", 40, 0)                     # the pipeline says scraping began now
    for n in (1, 2, 3):
        tracker.progress(n, 40, at=600.0 + 10.0 * n)
    assert tracker._eta.seconds_per_chapter() == pytest.approx(10.0)


def test_no_estimate_when_progress_has_no_start_event_until_enough_chapters() -> None:
    tracker, _ = _tracker()
    tracker.start()
    for n in range(1, 5):                                # the first tick is only the baseline
        tracker.progress(n, 40, at=10.0 * n)
    assert tracker.eta_seconds(now=40.0) == pytest.approx(36 * 10.0)


def test_no_estimate_when_nothing_is_left_or_time_is_degenerate() -> None:
    tracker = _steady(10.0, 40, total=40)
    assert tracker.eta_seconds(now=400.0) is None        # nothing remaining
    tracker, _ = _scraping(total=40)
    for n in (1, 2, 3, 4):
        tracker.progress(n, 40, at=0.0)                  # all in one instant: no division by zero
    assert tracker.eta_seconds(now=0.0) is None
    tracker, _ = _scraping(total=40)
    tracker.progress(1, 40, at=50.0)
    tracker.progress(2, 40, at=40.0)                     # an out-of-order stamp never runs backwards
    tracker.progress(3, 40, at=60.0)
    tracker.progress(4, 40, at=70.0)
    eta = tracker.eta_seconds(now=70.0)
    assert eta is not None and eta > 0


def test_no_estimate_while_stopping_building_or_after_the_end() -> None:
    tracker = _steady(10.0, 5)
    assert tracker.view(now=50.0).eta != "ETA: —"
    tracker.stop_requested()
    assert tracker.view(now=50.0).eta == "ETA: —"
    tracker.finish("stopped", counts=FinalCounts(pdfs=5))
    assert tracker.view(now=50.0).eta == "" and tracker.eta_seconds(now=50.0) is None
    tracker = _steady(10.0, 5)
    tracker.phase("building", 40, 0)
    assert tracker.eta_seconds(now=50.0) is None


def test_every_run_starts_with_a_fresh_estimate() -> None:
    tracker = _steady(10.0, 5)
    assert tracker.eta_seconds(now=50.0) is not None
    tracker.finish("completed")
    tracker.start()
    tracker.phase("scraping", 40, 0, at=1000.0)
    assert tracker.eta_seconds(now=1000.0) is None
    tracker.progress(1, 40, at=1010.0)
    assert tracker.eta_seconds(now=1010.0) is None


def test_retries_and_rescues_do_not_move_the_count() -> None:
    # The pipeline ticks a chapter once, at its end state; the same count repeated (a retry
    # reporting again) changes nothing and the ETA only sees real completions.
    tracker, _ = _scraping(total=40)
    tracker.progress(1, 40, at=10.0)
    for _ in range(5):
        tracker.progress(1, 40, at=20.0)
    assert tracker.view().counts == "1 / 40 · 2%"
    assert tracker._eta.seconds_per_chapter() is None


# ------------------------------------------------------------------------------ the events

def test_the_phase_event_is_never_coalesced_except_for_routine_skips() -> None:
    begin = ra.phase_event("scraping", 40, 0, at=1.0)
    assert begin.coalesce_key is None and begin.important
    skip = ra.phase_event("scraping", 40, 3, at=2.0)
    assert skip.coalesce_key == ra.PHASE_KEY and not skip.important
    building = ra.phase_event("building", 40, 3, at=3.0)
    assert building.coalesce_key is None and building.important
    assert building.payload == ra.PhaseUpdate("building", 40, 3, 3.0)


def test_the_finish_event_carries_the_final_count_and_results() -> None:
    event = ra.finished_event("completed", (40, 40), FinalCounts(pdfs=2))
    assert event.important and event.kind == "job"
    assert event.payload == ra.RunFinished("completed", (40, 40), FinalCounts(pdfs=2))


def test_a_late_higher_count_after_a_stop_cannot_change_the_final_count() -> None:
    tracker, _ = _scraping(total=40)
    tracker.progress(20, 40, at=1.0)
    tracker.finish("stopped", processed=(20, 40), counts=FinalCounts(pdfs=20))
    tracker.progress(30, 40, at=2.0)            # a routine update the window drew too late
    assert tracker.view().counts == "20 / 40 · 50%" and tracker.view().status == "Stopped"
