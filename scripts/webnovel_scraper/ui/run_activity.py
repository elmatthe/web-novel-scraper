"""What a scrape run tells the Activity panel: start lines, pipeline messages, the outcome.

The window owns the run's structured events (start, stop, outcome); the pipeline still speaks
through its existing ``log(str)`` and ``progress_cb(done, total)`` callbacks, which this module
turns into :class:`~webnovel_scraper.ui.activity.ActivityEvent` objects without changing the
pipeline. Pure functions, no Tk: the worker thread may call any of them.

The pipeline-message bridge
---------------------------
Every pipeline line goes to Detailed unchanged (and to the session log). A short table,
:data:`PIPELINE_MESSAGE_RULES`, also lifts the lines a user must see into Summary and marks
warnings. Each rule is a literal marker copied from an existing pipeline/adapter message, not
a pattern guessed for text that might appear; ``test_ui_run_activity.py`` fails if a marker
no longer occurs in the package source. Anything unmatched stays a Detailed-only info line.
This is a compatibility bridge until the pipeline emits structured events itself.

The outcome
-----------
The final Summary lines come from the :class:`~webnovel_scraper.pipeline.RunReport` the
pipeline returns (written, skipped, failed, cancelled …), never from the progress percentage:
a run that processed 100% of its chapters with failures is reported as finished with problems.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from pathlib import Path

from webnovel_scraper.ui.activity import ERROR, INFO, WARNING, ActivityEvent

__all__ = [
    "ORIGIN_RUN", "ORIGIN_PIPELINE", "PROGRESS_KEY", "SEPARATOR", "RUN_ENDED_LINE",
    "PIPELINE_MESSAGE_RULES", "ProgressUpdate", "RunFinished", "OUTCOMES",
    "classify_pipeline_message", "pipeline_event", "progress_event", "finished_event",
    "run_started_events", "outcome_events",
]

ORIGIN_RUN = "run"
ORIGIN_PIPELINE = "pipeline"
PROGRESS_KEY = "run-progress"
SEPARATOR = "─" * 36
RUN_ENDED_LINE = "— run ended —"
_LIST_PREVIEW = 20

# (how, marker, severity, summary). First match wins. Matched against the message with its
# leading indentation removed; "prefix" = startswith, "contains" = substring. Case-sensitive,
# so "FAILED" (a chapter that could not be saved) is not the retry ladder's "failed".
PIPELINE_MESSAGE_RULES: tuple[tuple[str, str, str, bool], ...] = (
    # The pipeline's own end-of-run report block; Summary gets the structured outcome instead.
    ("prefix", "Run complete", INFO, False),
    # Warnings the pipeline and adapters already label.
    ("prefix", "WARNING:", WARNING, True),
    ("prefix", "DUPLICATE CONTENT:", WARNING, True),
    # A chapter that could not be saved.
    ("contains", "FAILED", WARNING, True),
    ("contains", "failed after all methods", WARNING, True),
    ("contains", "permanently unavailable", WARNING, True),
    ("contains", "had no extractable body", WARNING, True),
    ("contains", "still rate-limited after", WARNING, True),
    # Recovery the user should know about.
    ("contains", "switching to a visible", WARNING, True),
    ("contains", "not installed/launchable", WARNING, True),
    ("prefix", "auto-slowdown:", WARNING, True),
    ("prefix", "Second-pass sweep:", INFO, True),
    ("contains", " rescued (", INFO, True),
    ("prefix", "Run cancelled:", INFO, True),
    # Preparation milestones.
    ("prefix", "Building chapter index", INFO, True),
    ("prefix", "discovered ", INFO, True),
    ("prefix", "loaded ", INFO, True),
    ("prefix", "Clamped requested range", INFO, True),
    ("prefix", "NOTE:", INFO, True),
)

OUTCOMES = ("completed", "completed_with_problems", "nothing_written", "stopped", "failed",
            "refused", "unconfirmed")


@dataclass(frozen=True)
class ProgressUpdate:
    """``progress_cb(done, total)``: chapters processed out of the selected range."""

    done: int
    total: int


@dataclass(frozen=True)
class RunFinished:
    """The worker has returned; one of :data:`OUTCOMES`."""

    outcome: str


# ----------------------------------------------------------------------- pipeline bridge

def classify_pipeline_message(text: str) -> tuple[str, bool]:
    """(severity, summary) for one pipeline log line, from :data:`PIPELINE_MESSAGE_RULES`."""
    body = text.lstrip()
    for how, marker, severity, summary in PIPELINE_MESSAGE_RULES:
        if body.startswith(marker) if how == "prefix" else marker in body:
            return severity, summary
    return INFO, False


def pipeline_event(text: str) -> ActivityEvent:
    severity, summary = classify_pipeline_message(text)
    return ActivityEvent(ORIGIN_PIPELINE, text, severity, summary)


def progress_event(done: int, total: int) -> ActivityEvent:
    """Routine progress: a pending update is replaced by a newer one, never queued twice."""
    return ActivityEvent(ORIGIN_RUN, kind="job", payload=ProgressUpdate(int(done), int(total)),
                         coalesce_key=PROGRESS_KEY)


def finished_event(outcome: str) -> ActivityEvent:
    return ActivityEvent(ORIGIN_RUN, kind="job", payload=RunFinished(outcome))


# ----------------------------------------------------------------------------- run start

def run_started_events(*, novel: str, site: str, start: int, end: int | None, grouping: str,
                       output_dir: Path, settings: str) -> list[ActivityEvent]:
    """The lines a Start press reports: Summary gets the run, the range and the output;
    Detailed also gets the effective fetch settings. ``end=None`` means through the last
    available chapter."""
    chapters = (f"Chapters: {start} to the last available chapter." if end is None
                else f"Chapters: {start} to {end}.")
    return [
        ActivityEvent(ORIGIN_RUN, SEPARATOR, kind="separator"),
        ActivityEvent(ORIGIN_RUN, f"Run started: {novel} from {site}.", summary=True),
        ActivityEvent(ORIGIN_RUN, chapters, summary=True),
        ActivityEvent(ORIGIN_RUN, f"Output: PDF, {grouping}.", summary=True),
        ActivityEvent(ORIGIN_RUN, f"Saving to: {output_dir}", summary=True),
        ActivityEvent(ORIGIN_RUN, f"Settings: {settings}"),
    ]


# ------------------------------------------------------------------------------- outcome

def _chapter_list(numbers) -> str:
    ordered = sorted(set(numbers))
    shown = ", ".join(str(n) for n in ordered[:_LIST_PREVIEW])
    more = len(ordered) - _LIST_PREVIEW
    return shown + (f" … and {more} more" if more > 0 else "")


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _processed_line(processed: tuple[int, int] | None, *, before: str = "") -> ActivityEvent | None:
    if processed is None:
        return None
    done, total = processed
    return ActivityEvent(ORIGIN_RUN, f"Chapters processed{before}: {done} of {total}.", summary=True)


def outcome_events(report: object | None, *, processed: tuple[int, int] | None = None,
                   error: BaseException | None = None, refused: BaseException | None = None,
                   ) -> tuple[str, list[ActivityEvent]]:
    """The run outcome and its Activity lines.

    *report* is the pipeline's ``RunReport`` (read by attribute, so this module never imports
    the pipeline); *processed* is the last ``(done, total)`` progress, shown as a count only;
    *error* / *refused* are what the worker caught instead of a report.
    """
    def run(message: str, severity: str = INFO, *, summary: bool = True) -> ActivityEvent:
        return ActivityEvent(ORIGIN_RUN, message, severity, summary)

    events: list[ActivityEvent] = []
    if refused is not None:
        outcome = "refused"
        events.append(run(f"Run refused: {refused}", ERROR))
    elif error is not None:
        outcome = "failed"
        events.append(run(f"The run stopped because of an unexpected error: "
                          f"{type(error).__name__}: {error}", ERROR))
        events.append(_processed_line(processed, before=" before the error"))
        trace = "".join(traceback.format_exception(type(error), error, error.__traceback__)).rstrip()
        events.append(run(trace, ERROR, summary=False))
    elif report is None or not hasattr(report, "written"):
        outcome = "unconfirmed"
        events.append(run("Run ended, but the scraper returned no run report, so its results "
                          "cannot be confirmed.", WARNING))
        events.append(_processed_line(processed))
    else:
        written = len(getattr(report, "written", ()) or ())
        skipped = len(getattr(report, "skipped_existing", ()) or ())
        failed = list(getattr(report, "failed", ()) or ())
        missing = list(getattr(report, "permanent_failed", ()) or ())
        empty = list(getattr(report, "extraction_failed", ()) or ())
        problems = len(set(failed) | set(missing) | set(empty))
        if getattr(report, "cancelled", False):
            outcome = "stopped"
            events.append(run("Stopped before the end of the selected range, as requested."))
        elif problems:
            outcome = "completed_with_problems"
            events.append(run(f"Finished with problems: {_plural(problems, 'chapter')} could "
                              "not be saved.", WARNING))
        elif written == 0 and skipped == 0:
            outcome = "nothing_written"
            events.append(run("Finished, but no PDFs were written.", WARNING))
        else:
            outcome = "completed"
            events.append(run(f"Completed: {_plural(written, 'PDF')} written and no chapter "
                              "failures recorded."))
        events.append(_processed_line(processed))
        events.append(run(f"PDFs written: {written}. Already present (skipped): {skipped}."))
        if failed:
            events.append(run(f"Failed chapters: {_chapter_list(failed)}.", WARNING))
        if missing:
            events.append(run(f"Not found on the site: {_chapter_list(missing)}.", WARNING))
        if empty:
            events.append(run(f"Fetched but no chapter text found: {_chapter_list(empty)}.",
                              WARNING))
        rescued = list(getattr(report, "rescued", ()) or ())
        if rescued:
            events.append(run(f"Recovered by the rescue browser or the second pass: "
                              f"{_plural(len(rescued), 'chapter')}."))
        output_dir = getattr(report, "output_dir", None)
        if output_dir:
            events.append(run(f"Saved in: {output_dir}"))
    events.append(run(RUN_ENDED_LINE, summary=False))
    return outcome, [event for event in events if event is not None]
