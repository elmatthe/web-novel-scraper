"""What the Run section shows: the run's phase, chapter counts, percentage and ETA.

Pure state, no Tk and no threads: the window feeds a :class:`RunTracker` the events its pump
delivers (on the Tk thread) and draws the :class:`RunView` it returns. The ETA formatting is
adapted from PDF-Splitter's ``pdf_splitter/jobs.py`` (``format_eta``; the author's local
working copy supplied 2026-10-17, itself from the Audiobook Creation Tool). The state model
is this app's own: a scrape has a phase, a chapter count and a final outcome, not a queue of
files.

What the numbers mean
---------------------
* **Chapters processed** is the pipeline's ``progress_cb(done, total)``: ``total`` is the
  selected source pages and ``done`` those that reached an end state (saved, failed or already
  present), each once; a retry, a rescue or a second pass never adds to it. It says how far the
  run has got, never how much of it worked, so 100% processed is not 100% successful.
* **Already present** is the share of ``done`` that resume skipped, reported by the pipeline's
  optional ``phase_cb``. It is also what keeps the ETA honest: skipped chapters cost no time.
* **PDFs written**, **failed**, **recovered** and anything else about results come only from
  the final ``RunReport`` (:class:`FinalCounts`). One PDF can hold many chapters.
* **Phase** is what the pipeline said (``phase_cb``) and nothing is guessed: no chapter total
  is shown, and the bar stays indeterminate, until the pipeline has one.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

__all__ = [
    "IDLE", "PREPARING", "SCRAPING", "BUILDING", "STOPPING", "COMPLETE", "CANCELLED",
    "FAILED", "REFUSED", "ACTIVE_STATES", "TERMINAL_STATES", "OUTCOME_STATES",
    "OUTCOME_LABELS", "FinalCounts", "RunView", "EtaEstimator", "RunTracker",
    "format_eta", "format_progress", "percent",
]

IDLE = "idle"
PREPARING = "preparing"
SCRAPING = "scraping"          # the same strings as the pipeline's PHASE_SCRAPING / PHASE_BUILDING
BUILDING = "building"
STOPPING = "stopping"
COMPLETE = "complete"
CANCELLED = "cancelled"
FAILED = "failed"
REFUSED = "refused"
ACTIVE_STATES = frozenset({PREPARING, SCRAPING, BUILDING, STOPPING})
TERMINAL_STATES = frozenset({COMPLETE, CANCELLED, FAILED, REFUSED})

# run_activity.OUTCOMES -> (final state, status text, severity of that text)
OUTCOME_STATES = {
    "completed": COMPLETE,
    "completed_with_problems": COMPLETE,
    "nothing_written": COMPLETE,
    "unconfirmed": COMPLETE,
    "stopped": CANCELLED,
    "failed": FAILED,
    "refused": REFUSED,
}
OUTCOME_LABELS = {
    "completed": ("Complete", "info"),
    "completed_with_problems": ("Finished with problems", "warning"),
    "nothing_written": ("Finished — no PDFs written", "warning"),
    "unconfirmed": ("Ended — results not confirmed", "warning"),
    "stopped": ("Stopped", "info"),
    "failed": ("Run failed", "error"),
    "refused": ("Run refused", "error"),
}

# ETA: how much real progress is needed before a remaining time is shown, how many recent
# chapters are weighed against the whole run, and when a silent stretch (a long recovery)
# turns the estimate back into "unknown".
ETA_MIN_CHAPTERS = 3
ETA_MIN_SECONDS = 5.0
ETA_RECENT_CHAPTERS = 10
ETA_STALL_FACTOR = 4.0
ETA_STALL_FLOOR = 30.0

_SEPARATOR = " · "


# ------------------------------------------------------------------------------ formatting

def format_eta(seconds: float | None) -> str:
    """PDF-Splitter's ETA text; unknown (or nonsensical) is ``ETA: —``."""
    if seconds is None or seconds != seconds or seconds < 0:
        return "ETA: —"
    seconds = int(round(seconds))
    if seconds < 60:
        return f"ETA: {seconds} s"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"ETA: {minutes} min" + (f" {rest} s" if rest and minutes < 10 else "")
    hours, minutes = divmod(minutes, 60)
    return f"ETA: {hours} h {minutes} min"


def percent(done: int | None, total: int | None) -> int | None:
    """Whole percent processed, rounded down so 100% means every selected chapter."""
    if done is None or not total or total < 0:
        return None
    return max(0, min(100, done * 100 // total))


def format_progress(done: int | None, total: int | None) -> str:
    """``12 / 40 · 30%``; ``—`` while the total is not known."""
    pct = percent(done, total)
    if pct is None or done is None:
        return "—"
    return f"{min(done, total)} / {total}{_SEPARATOR}{pct}%"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


# ------------------------------------------------------------------------------- the model

@dataclass(frozen=True)
class FinalCounts:
    """Results, read from the pipeline's ``RunReport`` once the run has ended."""

    pdfs: int = 0              # PDF files written (one may hold many chapters)
    already_present: int = 0   # chapters resume skipped
    failed: int = 0            # chapters that could not be saved (any reason)
    recovered: int = 0         # chapters saved by the rescue lane or the second pass
    not_listed: int = 0        # numbers inside the selected range the site's list lacks


@dataclass(frozen=True)
class RunView:
    """Everything the Run section draws, derived in one place."""

    state: str
    status: str
    severity: str              # "info" | "warning" | "error": how to colour ``status``
    counts: str
    detail: str
    eta: str
    done: int | None
    total: int | None
    indeterminate: bool        # no chapter total yet: show an honest, unfilled/moving bar

    @property
    def fraction(self) -> float | None:
        if self.done is None or not self.total:
            return None
        return max(0.0, min(1.0, self.done / self.total))


class EtaEstimator:
    """Remaining time from measured chapter completions on a monotonic clock.

    Time starts when the pipeline says scraping began, so chapter-list preparation is never
    counted, and chapters that resume skipped are left out of the rate. The per-chapter time
    is the slower of the whole run's average and the recent chapters' average, so a run that
    is slowing down is not promised a speed it no longer has. Nothing is estimated before
    :data:`ETA_MIN_CHAPTERS` chapters and :data:`ETA_MIN_SECONDS` have been measured, and a
    silent stretch much longer than a chapter normally takes (a retry or recovery) makes the
    estimate unknown again rather than stale. No timer: it is read when the window redraws.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._marks: deque[tuple[float, int]] = deque(maxlen=ETA_RECENT_CHAPTERS + 1)
        self._anchor: tuple[float, int] | None = None
        self._fetched = 0
        self._done = 0
        self._total = 0

    def begin(self, at: float) -> None:
        """Scraping started at ``at``."""
        if self._anchor is None:
            self._anchor = (at, 0)
            self._marks.append(self._anchor)

    def update(self, done: int, total: int, already_present: int, at: float) -> None:
        self._done, self._total = done, total
        fetched = max(0, done - max(0, already_present))
        if fetched <= self._fetched:
            return
        if self._marks and at < self._marks[-1][0]:
            at = self._marks[-1][0]             # never run backwards
        self._fetched = fetched
        mark = (at, fetched)
        self._marks.append(mark)
        if self._anchor is None:                # no start event: measure from the first chapter
            self._anchor = mark

    def seconds_per_chapter(self) -> float | None:
        if self._anchor is None or not self._marks:
            return None
        t_last, f_last = self._marks[-1]
        t_first, f_first = self._anchor
        measured = f_last - f_first
        if measured < ETA_MIN_CHAPTERS or t_last - t_first < ETA_MIN_SECONDS:
            return None
        overall = (t_last - t_first) / measured
        t_recent, f_recent = self._marks[0]
        recent = (t_last - t_recent) / (f_last - f_recent) if f_last > f_recent else overall
        return max(overall, recent)

    def seconds_remaining(self, now: float) -> float | None:
        per = self.seconds_per_chapter()
        remaining = self._total - self._done
        if per is None or remaining <= 0 or not self._marks:
            return None
        if now - self._marks[-1][0] > max(ETA_STALL_FACTOR * per, ETA_STALL_FLOOR):
            return None
        return remaining * per


class RunTracker:
    """The Run section's state for one run at a time. Not thread-safe: Tk thread only.

    Events arrive in the order the worker produced them (the Activity channel keeps the
    order of everything important and coalesces only routine progress), but the tracker does
    not rely on it: counts never go backwards, a phase never goes back, and once the run has
    ended nothing but :meth:`start` changes it, so a delayed progress update can never
    overwrite the final outcome.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.reset()

    # ----------------------------------------------------------------------- lifecycle
    def reset(self) -> None:
        self._state = IDLE
        self._phase = IDLE
        self._stopping = False
        self._done: int | None = None
        self._total: int | None = None
        self._already_present = 0
        self._outcome: str | None = None
        self._counts: FinalCounts | None = None
        self._eta = EtaEstimator()

    def start(self) -> None:
        """A new run: forget everything about the last one."""
        self.reset()
        self._state = self._phase = PREPARING

    @property
    def active(self) -> bool:
        return self._state in ACTIVE_STATES

    @property
    def terminal(self) -> bool:
        return self._state in TERMINAL_STATES

    @property
    def state(self) -> str:
        return STOPPING if self._stopping and self.active else self._state

    @property
    def outcome(self) -> str | None:
        return self._outcome

    # --------------------------------------------------------------------------- events
    def phase(self, phase: str, total: int, already_present: int, at: float | None = None) -> None:
        if not self.active or phase not in (SCRAPING, BUILDING):
            return
        at = self._clock() if at is None else at
        if phase == SCRAPING and self._phase == BUILDING:
            return                              # a phase never goes back
        if total > 0:
            self._total = total
            self._done = self._done or 0
        self._already_present = max(self._already_present, max(0, already_present))
        if phase == SCRAPING and self._phase != SCRAPING:
            self._eta.begin(at)
        self._phase = self._state = phase
        if phase == BUILDING:
            self._eta.reset()

    def progress(self, done: int, total: int, at: float | None = None) -> None:
        if not self.active:
            return
        at = self._clock() if at is None else at
        if total <= 0:
            return                              # no usable total: nothing honest to show
        if self._phase == PREPARING:
            # Progress implies the chapters are known. With no start event the ETA measures
            # from the first finished chapter instead.
            self._phase = self._state = SCRAPING
        self._total = total
        self._done = max(self._done or 0, min(done, total))
        if self._phase == SCRAPING:
            self._eta.update(self._done, total, self._already_present, at)

    def stop_requested(self) -> None:
        if self.active:
            self._stopping = True

    def finish(self, outcome: str, *, processed: tuple[int, int] | None = None,
               counts: FinalCounts | None = None) -> None:
        """The run ended with one of ``run_activity.OUTCOMES``. The first finish wins."""
        if not self.active:
            return
        self._state = OUTCOME_STATES.get(outcome, COMPLETE)
        self._phase = self._state
        self._outcome = outcome if outcome in OUTCOME_STATES else "unconfirmed"
        self._stopping = False
        self._counts = counts
        if processed is not None and processed[1] > 0:
            # The worker's last callback value: the authoritative end of the count, even if
            # the routine update carrying it was coalesced away.
            self._total = processed[1]
            self._done = max(self._done or 0, min(processed[0], processed[1]))

    # --------------------------------------------------------------------------- views
    def eta_seconds(self, now: float | None = None) -> float | None:
        if self._state != SCRAPING or self._stopping:
            return None
        return self._eta.seconds_remaining(self._clock() if now is None else now)

    def view(self, now: float | None = None) -> RunView:
        state = self.state
        if self.terminal:
            status, severity = OUTCOME_LABELS[self._outcome or "unconfirmed"]
            eta, detail = "", self._final_detail()
        else:
            severity = "info"
            status = self._active_status(state)
            eta = format_eta(self.eta_seconds(now)) if self.active or state == IDLE else ""
            detail = self._live_detail()
        return RunView(
            state=state, status=status, severity=severity,
            counts=format_progress(self._done, self._total), detail=detail, eta=eta,
            done=self._done, total=self._total,
            indeterminate=self.active and self._total is None,
        )

    def _active_status(self, state: str) -> str:
        if state == IDLE:
            return "Idle"
        if state == STOPPING:
            return ("Stopping after the current chapter…" if self._phase == SCRAPING
                    else "Stopping…")
        return {PREPARING: "Preparing chapter list…", SCRAPING: "Scraping chapters",
                BUILDING: "Building output…"}[state]

    def _live_detail(self) -> str:
        if self._already_present:
            return f"{self._already_present} already present"
        return ""

    def _final_detail(self) -> str:
        counts = self._counts
        if counts is None:
            return ""
        parts = [_plural(counts.pdfs, "PDF")]
        if counts.failed:
            parts.append(f"{counts.failed} failed")
        if counts.recovered:
            parts.append(f"{counts.recovered} recovered")
        if counts.already_present:
            parts.append(f"{counts.already_present} already present")
        if counts.not_listed:
            parts.append(f"{counts.not_listed} not on site list")
        return _SEPARATOR.join(parts)
