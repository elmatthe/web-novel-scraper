"""What a scrape run tells the Activity panel: start lines, pipeline messages, the outcome.

The window owns the run's structured events (start, stop, outcome); the pipeline still speaks
through its existing ``log(str)`` and ``progress_cb(done, total)`` callbacks plus, since
Checkpoint 02.2, the optional observability-only ``phase_cb(phase, total, already_present)``.
This module turns them into :class:`~webnovel_scraper.ui.activity.ActivityEvent` objects
without changing the pipeline. Pure functions, no Tk: the worker thread may call any of them.
The Run section's state lives in :mod:`webnovel_scraper.ui.run_progress`.

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

import time
import traceback
from dataclasses import dataclass
from pathlib import Path

from webnovel_scraper.ui.activity import ERROR, INFO, WARNING, ActivityEvent
from webnovel_scraper.ui.run_progress import FinalCounts

__all__ = [
    "ORIGIN_RUN", "ORIGIN_PIPELINE", "PROGRESS_KEY", "PHASE_KEY", "SEPARATOR", "RUN_ENDED_LINE",
    "UNBOUNDED_END", "PIPELINE_MESSAGE_RULES", "ProgressUpdate", "PhaseUpdate", "RunFinished",
    "OUTCOMES", "classify_pipeline_message", "pipeline_event", "progress_event", "phase_event",
    "finished_event", "final_counts", "run_started_events", "outcome_events",
]

ORIGIN_RUN = "run"
ORIGIN_PIPELINE = "pipeline"
PROGRESS_KEY = "run-progress"
PHASE_KEY = "run-phase"
# An end chapter at or above this means "through the last available chapter" (the window's
# ALL_CHAPTERS sentinel is 10**9; this is the pipeline's own _UNBOUNDED_END_THRESHOLD).
UNBOUNDED_END = 10**6
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
    ("prefix", "NOTE:", INFO, True),
)

OUTCOMES = ("completed", "completed_with_problems", "nothing_written", "stopped", "failed",
            "refused", "unconfirmed")


@dataclass(frozen=True)
class ProgressUpdate:
    """``progress_cb(done, total)``: chapters processed out of the selected range. ``at`` is
    when the worker made the call (``time.monotonic``), so the ETA measures the work, not
    how long the update waited to be drawn. ``from_cache`` is how many of the ``done`` the
    saved-page cache served, as the pipeline last said (``phase_cb``) before this call."""

    done: int
    total: int
    at: float | None = None
    from_cache: int = 0


@dataclass(frozen=True)
class PhaseUpdate:
    """``phase_cb(phase, total, already_present)``: where the pipeline is (see
    ``pipeline.PhaseFn``). ``phase`` is ``"scraping"`` or ``"building"``."""

    phase: str
    total: int
    already_present: int
    at: float | None = None


@dataclass(frozen=True)
class RunFinished:
    """The worker has returned; one of :data:`OUTCOMES`. ``processed`` is the last
    ``(done, total)`` the pipeline reported and ``counts`` the result totals from its
    ``RunReport`` (``None`` when there was no report)."""

    outcome: str
    processed: tuple[int, int] | None = None
    counts: FinalCounts | None = None


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


def progress_event(done: int, total: int, at: float | None = None, *,
                   from_cache: int = 0) -> ActivityEvent:
    """Routine progress: a pending update is replaced by a newer one, never queued twice."""
    stamp = time.monotonic() if at is None else at
    return ActivityEvent(ORIGIN_RUN, kind="job",
                         payload=ProgressUpdate(int(done), int(total), stamp, int(from_cache)),
                         coalesce_key=PROGRESS_KEY)


def phase_event(phase: str, total: int, already_present: int,
                at: float | None = None) -> ActivityEvent:
    """A phase change is never coalesced away. The updates that only raise the
    already-present count while scraping are routine (one per skipped chapter, possibly
    thousands) and replace each other like progress does."""
    stamp = time.monotonic() if at is None else at
    routine = phase == "scraping" and already_present > 0
    return ActivityEvent(ORIGIN_RUN, kind="job",
                         payload=PhaseUpdate(phase, int(total), int(already_present), stamp),
                         coalesce_key=PHASE_KEY if routine else None)


def finished_event(outcome: str, processed: tuple[int, int] | None = None,
                   counts: FinalCounts | None = None) -> ActivityEvent:
    return ActivityEvent(ORIGIN_RUN, kind="job", payload=RunFinished(outcome, processed, counts))


def final_counts(report: object | None) -> FinalCounts | None:
    """Result totals from the pipeline's ``RunReport`` (``None`` without a usable report).
    Chapter numbers are the report's own; a chapter listed under several failure reasons is
    counted once, and PDFs are counted separately from chapters."""
    if report is None or not hasattr(report, "written"):
        return None
    problems = (set(getattr(report, "failed", ()) or ()) | set(getattr(report, "permanent_failed", ()) or ())
                | set(getattr(report, "extraction_failed", ()) or ()))
    files = [Path(p) for p in (getattr(report, "written", ()) or ())]
    return FinalCounts(
        pdfs=sum(1 for p in files if p.suffix.lower() != ".txt"),
        txts=sum(1 for p in files if p.suffix.lower() == ".txt"),
        already_present=len(getattr(report, "skipped_existing", ()) or ()),
        failed=len(problems),
        recovered=len(set(getattr(report, "rescued", ()) or ()) - problems),
        not_listed=len(_gaps_in_range(report)),
    )


# ----------------------------------------------------------------------------- run start

def run_started_events(*, novel: str, site: str, start: int, end: int | None, grouping: str,
                       output_dir: Path, settings: str, resuming: str = "",
                       formats: tuple = ("pdf",)) -> list[ActivityEvent]:
    """The lines a Start (or Resume) press reports: Summary gets the run, the range and the
    output; Detailed also gets the effective fetch settings. ``end=None`` means through the last
    available chapter. ``resuming`` (non-empty for a resume) says how far the run had got."""
    chapters = (f"Chapters: {start} to the last available chapter." if end is None
                else f"Chapters: {start} to {end}.")
    started = (f"Run resumed: {novel} from {site} ({resuming})." if resuming
               else f"Run started: {novel} from {site}.")
    where = (f"Continuing in: {output_dir}" if resuming
             else f"Saving to: {output_dir} (a new folder for this run)")
    kinds = " and ".join(f.upper() for f in formats) or "PDF"
    return [
        ActivityEvent(ORIGIN_RUN, SEPARATOR, kind="separator"),
        ActivityEvent(ORIGIN_RUN, started, summary=True),
        ActivityEvent(ORIGIN_RUN, chapters, summary=True),
        ActivityEvent(ORIGIN_RUN, f"Output: {kinds}, {grouping}.", summary=True),
        ActivityEvent(ORIGIN_RUN, where, summary=True),
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


def _line(message: str, severity: str = INFO, *, summary: bool = True) -> ActivityEvent:
    return ActivityEvent(ORIGIN_RUN, message, severity, summary)


def _span(value) -> tuple[int, int] | None:
    try:
        low, high = value
        return int(low), int(high)
    except (TypeError, ValueError):
        return None


def _gaps_in_range(report: object) -> list[int]:
    """Chapter numbers the site's list lacks *inside the range that was scraped*. The
    pipeline records gaps across the whole list; the ones outside the selection do not
    matter to this run."""
    effective = _span(getattr(report, "effective_range", None))
    if effective is None or effective[0] > effective[1]:
        return []
    gaps = getattr(report, "toc_gaps", ()) or ()
    return sorted({n for n in gaps if effective[0] <= n <= effective[1]})


def _coverage_events(report: object) -> list[ActivityEvent]:
    """Where the site could not give what was asked for. An end left open ("through the last
    available chapter", the 10**9 sentinel) is never reported as cut short, however far the
    list actually goes; only an end the user typed is compared with the list."""
    events: list[ActivityEvent] = []
    requested = _span(getattr(report, "requested_range", None))
    effective = _span(getattr(report, "effective_range", None))
    if requested and effective and effective[0] <= effective[1]:
        if requested[1] < UNBOUNDED_END and effective[1] < requested[1]:
            events.append(_line(
                f"The site's chapter list ends at chapter {effective[1]}, so chapters "
                f"{effective[1] + 1} to {requested[1]} are not available.", WARNING))
        if effective[0] > requested[0]:
            events.append(_line(
                f"The site's chapter list starts at chapter {effective[0]}, so chapters "
                f"{requested[0]} to {effective[0] - 1} are not available.", WARNING))
    gaps = _gaps_in_range(report)
    if gaps:
        events.append(_line(f"Not on the site's chapter list, so not saved: {_chapter_list(gaps)}.",
                            WARNING))
    return events


def _files_wording(report: object) -> tuple[str, str, str]:
    """("PDFs", "3 PDFs", "3") for a PDF-only run (the established wording), otherwise
    ("files", "3 PDFs and 3 TXT files", "3 PDF, 3 TXT")."""
    formats = tuple(getattr(report, "formats", ("pdf",)) or ("pdf",))
    files = [Path(p) for p in (getattr(report, "written", ()) or ())]
    if formats == ("pdf",):
        return "PDFs", _plural(len(files), "PDF"), str(len(files))
    per = {f: sum(1 for p in files if p.suffix.lower() == "." + f) for f in formats}
    text = " and ".join(_plural(n, "PDF" if f == "pdf" else "TXT file") for f, n in per.items())
    detail = ", ".join(f"{n} {f.upper()}" for f, n in per.items())
    return "files", text, detail


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
    run = _line
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
        files_word, files_text, written_detail = _files_wording(report)
        failed = list(getattr(report, "failed", ()) or ())
        missing = list(getattr(report, "permanent_failed", ()) or ())
        empty = list(getattr(report, "extraction_failed", ()) or ())
        problems = len(set(failed) | set(missing) | set(empty))
        unaccounted = (processed[1] - processed[0]) if processed else 0
        gaps = _gaps_in_range(report)
        if getattr(report, "cancelled", False):
            outcome = "stopped"
            events.append(run("Stopped before the end of the selected range, as requested."))
        elif problems:
            outcome = "completed_with_problems"
            events.append(run(f"Finished with problems: {_plural(problems, 'chapter')} could "
                              "not be saved.", WARNING))
        elif gaps and (written or skipped):
            # Everything listed was saved, but numbers inside the range are missing from the
            # site's own list: the output is not a complete copy of the range (owner rule).
            outcome = "completed_with_problems"
            verb = "is" if len(gaps) == 1 else "are"
            events.append(run(f"Finished with gaps: {_plural(len(gaps), 'chapter number')} inside "
                              f"the range {verb} missing from the site's chapter list, so the output "
                              "is not a complete copy of the range.", WARNING))
        elif unaccounted > 0:
            # A run that ended normally accounts for every selected chapter; if the
            # pipeline's own count disagrees, say so rather than call it complete.
            outcome = "completed_with_problems"
            events.append(run(f"Finished, but {_plural(unaccounted, 'selected chapter')} "
                              "could not be accounted for.", WARNING))
        elif written == 0 and skipped == 0:
            outcome = "nothing_written"
            events.append(run(f"Finished, but no {files_word} were written.", WARNING))
        elif written == 0:
            outcome = "completed"
            events.append(run("Completed: every selected chapter was already saved in the run "
                              f"folder, so no new {files_word} were written."))
        else:
            outcome = "completed"
            events.append(run(f"Completed: {files_text} written and no chapter "
                              "failures recorded."))
        events.append(_processed_line(processed))
        events.append(run(f"{files_word[0].upper() + files_word[1:]} written: {written_detail}. "
                          f"Already present (skipped): {skipped}."))
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
        events.extend(_coverage_events(report))
        warnings = [str(w) for w in (getattr(report, "warnings", ()) or ())]
        if warnings and outcome == "nothing_written":
            # Nothing was saved: the pipeline's own reasons are the answer to "why".
            for warning in warnings[:3]:
                events.append(run(warning, WARNING))
            if len(warnings) > 3:
                events.append(run(f"… and {len(warnings) - 3} more; see Detailed.", WARNING))
        elif warnings:
            events.append(run(f"{_plural(len(warnings), 'warning')} recorded; Detailed lists "
                              "them.", WARNING))
        output_dir = getattr(report, "output_dir", None)
        if output_dir:
            events.append(run(f"Saved in: {output_dir}"))
    events.append(run(RUN_ENDED_LINE, summary=False))
    return outcome, [event for event in events if event is not None]
