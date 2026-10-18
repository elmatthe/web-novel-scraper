"""v0.3.0 Checkpoint 02.1: what a scrape run tells the Activity panel. Offline, no Tk.

``webnovel_scraper.ui.run_activity`` turns the pipeline's existing ``log(str)`` lines
into Summary/Detailed events through a short table of literal markers (no regex
classifier), and builds the run outcome from the pipeline's ``RunReport`` — never from
the progress percentage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from webnovel_scraper.pipeline import RunReport
from webnovel_scraper.ui import run_activity as ra
from webnovel_scraper.ui.activity import ERROR, INFO, WARNING, ActivityModel

PACKAGE = Path(__file__).resolve().parents[2] / "scripts" / "webnovel_scraper"


def _package_source() -> str:
    """Every scraping module's source (the GUI's own ``ui/`` package excluded)."""
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(PACKAGE.rglob("*.py")) if "ui" not in path.relative_to(PACKAGE).parts
    )


def _summary(events) -> list[str]:
    model = ActivityModel()
    for event in events:
        model.add(event)
    return [line.text for line in model.summary]


def _detailed(events) -> list[str]:
    model = ActivityModel()
    for event in events:
        model.add(event)
    return [line.text for line in model.detailed]


# ── The pipeline-message bridge ──────────────────────────────────────────────
def test_the_bridge_is_a_short_table_of_literal_markers() -> None:
    rules = ra.PIPELINE_MESSAGE_RULES
    assert 0 < len(rules) <= 25
    for how, marker, severity, summary in rules:
        assert how in ("prefix", "contains")
        assert severity in (INFO, WARNING, ERROR) and isinstance(summary, bool)
        assert marker
        if how == "prefix":  # prefixes match the line after its indentation is removed
            assert marker == marker.lstrip()


def test_every_marker_still_occurs_in_the_pipeline_source() -> None:
    """Guards against wording drift: if a pipeline/adapter message is reworded, its rule
    stops matching and this test names the stale marker."""
    source = _package_source()
    missing = [marker for _how, marker, _sev, _sum in ra.PIPELINE_MESSAGE_RULES
               if marker not in source]
    assert missing == []


@pytest.mark.parametrize("line, severity, summary", [
    # Real pipeline/adapter lines (copied from the source), as the GUI receives them.
    ("Building chapter index (TOC) …", INFO, True),
    ("  discovered 120 chapters; saved chapter_index.json.", INFO, True),
    ("  loaded 120 chapters from chapter_index.json (resume).", INFO, True),
    ("Clamped requested range 1-1000000000 to available 1-120 (TOC covers 1-120).", INFO, True),
    ("NOTE: chapter_index.json uses the old pre-identity format; rebuilding the TOC under "
     "the current schema.", INFO, True),
    ("WARNING: chapter_index.json belongs to a different novel/site; rebuilding.", WARNING, True),
    ("DUPLICATE CONTENT: chapter 7 has identical body to the previous chapter; skipping.",
     WARNING, True),
    ("  chapter 5 FAILED: HTTP 403", WARNING, True),
    ("  chapter 9 failed after all methods.", WARNING, True),
    ("  chapter 9 permanently unavailable (not found).", WARNING, True),
    ("  chapter 9 fetched but had no extractable body.", WARNING, True),
    ("  chapter 9 still rate-limited after 3 retries; recording a transient failure for resume.",
     WARNING, True),
    ("  primary mode broadly blocked — switching to a visible browser for the rest of the run.",
     WARNING, True),
    ("  TOC blocked by Cloudflare while headless — switching to a visible browser and "
     "retrying the index once.", WARNING, True),
    ("  camoufox browser engine is not installed/launchable — skipping to chromium "
     "immediately (no backoff).", WARNING, True),
    ("auto-slowdown: inter-fetch delay raised to 6.0s after repeated challenges (ceiling 30s).",
     WARNING, True),
    ("Second-pass sweep: re-attempting 2 failed chapter(s)…", INFO, True),
    ("  chapter 12 rescued (visible).", INFO, True),
    ("Run cancelled: stop requested", INFO, True),
    # Routine technical detail: Detailed only.
    ("  fetch attempt 1/3 via camoufox", INFO, False),
    ("  fetched 12.4 KB in 1.2 s", INFO, False),
    ("  attempt 1/3 via http failed: timed out", INFO, False),  # a retry, not a lost chapter
    ("Cache entry 00012.html is a Cloudflare challenge; re-fetching.", INFO, False),
    ("  warmed camoufox session on freewebnovel.com (origin GET to acquire cf_clearance "
     "in the browser context)", INFO, False),
    ("  wrote Chapter 0001.pdf", INFO, False),
    ("Run complete.\n  Output: x\n  Failed:  2\n    failed chapters: 3, 4\n    warning: gap",
     INFO, False),  # the pipeline's report block; Summary gets the structured outcome
])
def test_pipeline_lines_are_routed_by_the_bridge(line, severity, summary) -> None:
    assert ra.classify_pipeline_message(line) == (severity, summary)
    event = ra.pipeline_event(line)
    assert (event.origin, event.message, event.severity, event.summary) == (
        "pipeline", line, severity, summary)


def test_raw_pipeline_text_is_kept_in_detailed() -> None:
    lines = ["  fetch attempt 1/3 via camoufox", "  chapter 5 FAILED: HTTP 403"]
    detailed = _detailed(ra.pipeline_event(line) for line in lines)
    assert detailed == ["  fetch attempt 1/3 via camoufox", "  Warning: chapter 5 FAILED: HTTP 403"]
    assert _summary(ra.pipeline_event(line) for line in lines) == [
        "Warning: chapter 5 FAILED: HTTP 403"]


def test_progress_and_finish_are_job_events() -> None:
    progress = ra.progress_event(3, 10)
    assert progress.kind == "job" and progress.coalesce_key == ra.PROGRESS_KEY
    assert progress.payload == ra.ProgressUpdate(3, 10)
    finish = ra.finished_event("completed")
    assert finish.kind == "job" and finish.coalesce_key is None and finish.important
    assert finish.payload == ra.RunFinished("completed")


# ── Run start ────────────────────────────────────────────────────────────────
def test_run_start_names_the_novel_site_range_and_output(tmp_path) -> None:
    events = ra.run_started_events(
        novel="Shadow Slave", site="Free Web Novel", start=5, end=None,
        grouping="one PDF per chapter", output_dir=tmp_path / "shadow-slave-1",
        settings="delay 3 s, timeout 30 s.")
    assert events[0].kind == "separator"
    assert _summary(events)[1:] == [
        "Run started: Shadow Slave from Free Web Novel.",
        "Chapters: 5 to the last available chapter.",
        "Output: PDF, one PDF per chapter.",
        f"Saving to: {tmp_path / 'shadow-slave-1'}",
    ]
    assert _detailed(events)[-1] == "Settings: delay 3 s, timeout 30 s."
    bounded = ra.run_started_events(novel="N", site="S", start=1, end=40, grouping="g",
                                    output_dir=tmp_path, settings="")
    assert "Chapters: 1 to 40." in _summary(bounded)


# ── Outcome ──────────────────────────────────────────────────────────────────
def _report(tmp_path, **fields) -> RunReport:
    return RunReport(output_dir=tmp_path, requested_range=(1, 3), effective_range=(1, 3), **fields)


def test_a_clean_run_is_completed(tmp_path) -> None:
    report = _report(tmp_path, written=[tmp_path / f"c{i}.pdf" for i in range(3)])
    outcome, events = ra.outcome_events(report, processed=(3, 3))
    assert outcome == "completed"
    summary = _summary(events)
    assert summary[0] == "Completed: 3 PDFs written and no chapter failures recorded."
    assert "Chapters processed: 3 of 3." in summary
    assert "PDFs written: 3. Already present (skipped): 0." in summary
    assert f"Saved in: {tmp_path}" in summary
    assert _detailed(events)[-1] == ra.RUN_ENDED_LINE
    assert ra.RUN_ENDED_LINE not in summary
    assert all(event.severity == INFO for event in events)


def test_full_progress_with_failures_is_not_called_a_success(tmp_path) -> None:
    """100% of chapters processed is not 100% saved: the outcome comes from the report."""
    report = _report(tmp_path, written=[tmp_path / "c1.pdf"], failed=[2],
                     permanent_failed=[3], extraction_failed=[3])
    outcome, events = ra.outcome_events(report, processed=(3, 3))
    assert outcome == "completed_with_problems"
    summary = _summary(events)
    assert summary[0] == "Warning: Finished with problems: 2 chapters could not be saved."
    assert not any(line.startswith("Completed") for line in summary)
    assert "Warning: Failed chapters: 2." in summary
    assert "Warning: Not found on the site: 3." in summary
    assert "Warning: Fetched but no chapter text found: 3." in summary


def test_long_failure_lists_are_shortened(tmp_path) -> None:
    report = _report(tmp_path, failed=list(range(1, 31)))
    _, events = ra.outcome_events(report)
    line = next(s for s in _summary(events) if s.startswith("Warning: Failed chapters:"))
    assert line.endswith("19, 20 … and 10 more.")


def test_nothing_written_is_a_warning(tmp_path) -> None:
    outcome, events = ra.outcome_events(_report(tmp_path), processed=(0, 0))
    assert outcome == "nothing_written"
    assert _summary(events)[0] == "Warning: Finished, but no PDFs were written."


def test_a_resumed_run_with_only_skips_is_completed(tmp_path) -> None:
    outcome, events = ra.outcome_events(_report(tmp_path, skipped_existing=[1, 2, 3]))
    assert outcome == "completed"
    assert "PDFs written: 0. Already present (skipped): 3." in _summary(events)


def test_a_stopped_run_says_so_with_its_counts(tmp_path) -> None:
    report = _report(tmp_path, written=[tmp_path / "c1.pdf"], cancelled=True, rescued=[1])
    outcome, events = ra.outcome_events(report, processed=(1, 3))
    assert outcome == "stopped"
    summary = _summary(events)
    assert summary[0] == "Stopped before the end of the selected range, as requested."
    assert "Chapters processed: 1 of 3." in summary
    assert "Recovered by the rescue browser or the second pass: 1 chapter." in summary


def test_an_unexpected_error_is_an_error_with_a_traceback_in_detailed() -> None:
    try:
        raise RuntimeError("index unavailable")
    except RuntimeError as exc:
        error = exc
    outcome, events = ra.outcome_events(None, processed=(4, 10), error=error)
    assert outcome == "failed"
    summary, detailed = _summary(events), _detailed(events)
    assert summary[0] == ("Error: The run stopped because of an unexpected error: "
                          "RuntimeError: index unavailable")
    assert "Chapters processed before the error: 4 of 10." in summary
    assert events[0].severity == ERROR
    trace = next(line for line in detailed if "Traceback (most recent call last)" in line)
    assert "RuntimeError: index unavailable" in trace
    assert all("Traceback" not in line for line in summary)


def test_a_refused_site_is_an_error() -> None:
    outcome, events = ra.outcome_events(None, refused=ValueError("site 'x' is disabled"))
    assert outcome == "refused"
    assert _summary(events) == ["Error: Run refused: site 'x' is disabled"]


def test_a_missing_report_is_never_presented_as_success() -> None:
    outcome, events = ra.outcome_events(None, processed=(10, 10))
    assert outcome == "unconfirmed"
    assert _summary(events)[0].startswith("Warning: Run ended, but the scraper returned no run report")


def test_every_outcome_ends_with_the_run_ended_line(tmp_path) -> None:
    cases = [dict(report=_report(tmp_path, written=[tmp_path])), dict(report=None),
             dict(report=None, error=OSError("x")), dict(report=None, refused=OSError("y"))]
    for case in cases:
        outcome, events = ra.outcome_events(case.pop("report"), **case)
        assert outcome in ra.OUTCOMES
        assert events[-1].message == ra.RUN_ENDED_LINE and not events[-1].summary
