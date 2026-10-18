"""Activity: structured events, a thread-safe channel, the display model and the session log.

Adapted from PDF-Splitter ``scripts/pdf_splitter/activity.py`` (the author's local working
copy supplied 2026-10-17, newer than GitHub ``edcbd4f``), whose concepts come from the
Audiobook Creation Tool v0.6.6 (``shared/job_control.py`` event stream and Summary/Detailed
projection, ``shared/job_ui.py`` main-thread pump, ``shared/logging_setup.py`` session log).
Like the original, nothing here knows about scraping; :mod:`webnovel_scraper.ui.run_activity`
turns a scrape into these events.

The rules
---------
* **Producers emit structured events only.** :class:`ActivityEvent` carries origin, severity,
  a summary flag, a message and an optional payload. A worker thread calls
  :meth:`Activity.emit` (a lock-protected, bounded queue); it never touches a widget and never
  calls Tk, not even ``after``. The UI thread owns :class:`ActivityPump`, which drains the
  channel on a Tk timer, a bounded number of events per tick.
* **Routing.** ``summary=True`` events go to the Summary *and* Detailed views; the rest only to
  Detailed. Separators go to both. ``kind="job"`` events carry a payload for the window
  (progress, run finished) instead of text.
* **Order.** Events are shown in the order they were emitted. A UI-thread :meth:`Activity.report`
  first routes everything still queued, and every line queued before a job event is drawn
  before that job event is handled.
* **Nothing important is dropped.** Progress events with the same ``coalesce_key`` replace each
  other while pending (only the latest matters). Routine detail may be left out of the
  *display* when the queue is full; that is counted and announced in Detailed. Warnings,
  errors, Summary lines, separators and job events never are.
* **The session log is complete.** Changed from PDF-Splitter, which writes the log as the UI
  thread draws: here every message is written when it is *emitted* (the log is locked, so any
  thread may write), so a line the display skipped, or one still queued when the window
  closed, is still on disk, in emission order.
* **Display history is bounded** (a line cap per view). *Clear Log* empties the displayed views
  only; the session log file is untouched.
* **The session log is visible when it fails.** The first failure becomes one warning in
  Summary and writing stops; there is no retry storm and no recursive reporting.
* **Close is harmless.** After :meth:`Activity.close` events are refused, the pump stops, and the
  log is closed.

This module imports no Tk.
"""

from __future__ import annotations

import platform
import re
import sys
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

__all__ = [
    "INFO", "WARNING", "ERROR", "ActivityEvent", "ActivityLine", "RenderUpdate", "format_event",
    "ActivityChannel", "ActivityModel", "SessionLog", "Activity", "ActivityPump",
    "DEFAULT_LINE_CAP", "DEFAULT_DRAIN_LIMIT", "DEFAULT_MAX_PENDING", "DEFAULT_POLL_MS",
    "MAX_LOGGED_CHARS",
]

INFO, WARNING, ERROR = "info", "warning", "error"
DEFAULT_LINE_CAP = 5000
DEFAULT_DRAIN_LIMIT = 64
DEFAULT_MAX_PENDING = 500
DEFAULT_POLL_MS = 80
# One logged message is cut here. A long traceback fits; a page of HTML in an exception does not.
MAX_LOGGED_CHARS = 8000

_SEVERITY_WORDS = {WARNING: "Warning", ERROR: "Error"}


@dataclass(frozen=True)
class ActivityEvent:
    """One thing worth telling the user or the log. Immutable and safe to share across threads.

    ``kind`` is ``"message"`` (displayed and logged), ``"separator"`` (a run divider, displayed
    in both views) or ``"job"`` (``payload`` is for the window; not displayed or logged).
    ``coalesce_key`` lets a newer pending event replace an older one.
    """

    origin: str
    message: str = ""
    severity: str = INFO
    summary: bool = False
    payload: object | None = None
    kind: str = "message"
    coalesce_key: str | None = None

    @property
    def important(self) -> bool:
        """Events that must never be dropped or coalesced away silently."""
        return (self.severity != INFO or self.summary or self.kind != "message"
                ) and self.coalesce_key is None


@dataclass(frozen=True)
class ActivityLine:
    text: str
    severity: str = INFO


@dataclass(frozen=True)
class RenderUpdate:
    """What the views must do: append these lines, and/or empty themselves first."""

    summary: tuple[ActivityLine, ...] = ()
    detailed: tuple[ActivityLine, ...] = ()
    cleared: bool = False

    @property
    def empty(self) -> bool:
        return not (self.summary or self.detailed or self.cleared)


def format_event(event: ActivityEvent) -> ActivityLine:
    """The displayed line. Warnings and errors are labelled in words, not only by colour,
    unless the message already carries the label (``"WARNING: …"`` from the pipeline). A
    message's leading indentation stays in front of the label, so raw pipeline text is
    unchanged."""
    message = event.message
    body = message.lstrip()
    indent = message[: len(message) - len(body)]
    word = _SEVERITY_WORDS.get(event.severity)
    if word and not body.lower().startswith(word.lower() + ":"):
        body = f"{word}: {body}"
    return ActivityLine(indent + body, event.severity)


# --------------------------------------------------------------------------- channel

class ActivityChannel:
    """A bounded, lock-protected hand-off from worker threads to the UI thread."""

    def __init__(self, max_pending: int = DEFAULT_MAX_PENDING) -> None:
        if max_pending < 1:
            raise ValueError("max_pending must be at least 1")
        self._max = max_pending
        self._lock = threading.Lock()
        self._entries: deque[list] = deque()
        self._by_key: dict[str, list] = {}
        self._closed = False
        self.dropped = 0
        self.coalesced = 0

    def emit(self, event: ActivityEvent) -> bool:
        """Queue *event* (any thread). Returns False if it was refused or dropped."""
        with self._lock:
            if self._closed:
                return False
            key = event.coalesce_key
            if key is not None and key in self._by_key:
                self._by_key[key][0] = event  # newest wins, in the original position
                self.coalesced += 1
                return True
            if not event.important and key is None and len(self._entries) >= self._max:
                self.dropped += 1  # routine detail only; counted and announced, never silent
                return False
            entry = [event]
            self._entries.append(entry)
            if key is not None:
                self._by_key[key] = entry
            return True

    def drain(self, limit: int | None) -> list[ActivityEvent]:
        """Remove and return up to *limit* events (all of them for ``None``), oldest first."""
        out: list[ActivityEvent] = []
        with self._lock:
            while self._entries and (limit is None or len(out) < limit):
                entry = self._entries.popleft()
                event = entry[0]
                if event.coalesce_key is not None and self._by_key.get(event.coalesce_key) is entry:
                    del self._by_key[event.coalesce_key]
                out.append(event)
        return out

    def pending(self) -> int:
        with self._lock:
            return len(self._entries)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._entries.clear()
            self._by_key.clear()


# ------------------------------------------------------------------------ display model

class ActivityModel:
    """The in-memory Summary/Detailed history, capped per view. Pure data."""

    def __init__(self, line_cap: int = DEFAULT_LINE_CAP) -> None:
        self.line_cap = line_cap
        self.summary: deque[ActivityLine] = deque(maxlen=line_cap)
        self.detailed: deque[ActivityLine] = deque(maxlen=line_cap)

    def add(self, event: ActivityEvent) -> tuple[ActivityLine | None, ActivityLine]:
        """Record *event*; returns (summary line or None, detailed line)."""
        line = format_event(event)
        self.detailed.append(line)
        if event.summary or event.kind == "separator":
            # Summary reads as a list of milestones: the pipeline's step indentation is dropped.
            s_line = ActivityLine(line.text.lstrip(), line.severity)
            self.summary.append(s_line)
            return s_line, line
        return None, line

    def clear(self) -> None:
        self.summary.clear()
        self.detailed.clear()


# -------------------------------------------------------------------------- session log

# Defence in depth: the scraper never logs cookies or credentials, but an exception message
# could quote a header. These values are replaced before anything reaches the disk.
_SECRET_HEADER = re.compile(r"(?im)\b(set-cookie|cookie|authorization|proxy-authorization)(\s*[:=]\s*)[^\r\n]+")
_SECRET_COOKIE = re.compile(r"(?i)\b(cf_clearance|__cf_bm|cf_chl_[a-z0-9_]*|__cflb)=[^;\s,'\"]+")


def _loggable(message: str) -> str:
    """The text written to disk: secrets redacted, length capped, continuation lines indented."""
    text = _SECRET_HEADER.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", message)
    text = _SECRET_COOKIE.sub(lambda m: f"{m.group(1)}=[redacted]", text)
    if len(text) > MAX_LOGGED_CHARS:
        text = text[:MAX_LOGGED_CHARS] + f" … [truncated {len(text) - MAX_LOGGED_CHARS} characters]"
    return text.replace("\r\n", "\n").replace("\n", "\n    ")


class SessionLog:
    """One technical log file per application session under ``files/runtime-data/logs/``.

    UTF-8, one timestamped line per message, flushed line by line, safe to call from any
    thread. It takes only message text (no payloads, never chapter text) and reports a failure
    once through :attr:`failed` / :attr:`last_error` instead of raising.
    """

    def __init__(self, directory: str | Path, *, app_name: str = "Web Novel Scraper",
                 clock: Callable[[], datetime] = datetime.now) -> None:
        self.directory = Path(directory)
        self.app_name = app_name
        self._clock = clock
        self._lock = threading.Lock()
        self._handle = None
        self.path: Path | None = None
        self.failed = False
        self.last_error = ""

    @property
    def is_open(self) -> bool:
        return self._handle is not None

    def open(self) -> bool:
        with self._lock:
            if self._handle is not None:
                return True
            if self.failed:
                return False
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                stamp = self._clock().strftime("%Y%m%d-%H%M%S")
                counter = 1
                while True:
                    suffix = "" if counter == 1 else f"-{counter}"
                    path = self.directory / f"session-{stamp}{suffix}.log"
                    try:  # exclusive create: two windows started in the same second never share a file
                        self._handle = path.open("x", encoding="utf-8", newline="\n", buffering=1)
                        break
                    except FileExistsError:
                        counter += 1
                self.path = path
                self._handle.write(
                    f"{self._stamp()} [info] {self.app_name} session started "
                    f"(Python {platform.python_version()}, {sys.platform})\n")
                return True
            except OSError as exc:
                self._fail(exc)
                return False

    def write(self, event: ActivityEvent) -> bool:
        with self._lock:
            if self.failed or self._handle is None:
                return False
            try:
                self._handle.write(f"{self._stamp()} [{event.severity}] {event.origin}: "
                                   f"{_loggable(event.message)}\n")
                return True
            except (OSError, ValueError) as exc:
                self._fail(exc)
                return False

    def _stamp(self) -> str:
        return f"{self._clock():%Y-%m-%d %H:%M:%S}"

    def _fail(self, exc: Exception) -> None:
        self.failed = True
        self.last_error = f"{type(exc).__name__}: {exc}"
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                handle.close()
            except (OSError, ValueError):
                pass

    def close(self) -> None:
        with self._lock:
            handle, self._handle = self._handle, None
            if handle is None:
                return
            try:
                handle.write(f"{self._stamp()} [info] {self.app_name} session closed\n")
                handle.close()
            except (OSError, ValueError):
                pass


# --------------------------------------------------------------------------- the facade

class Activity:
    """Routes events to the display model, the session log and the job sink.

    ``report`` is for the UI thread (immediate); ``emit`` is for any thread (queued). Both end
    in the same routing, so a producer's thread never affects how an event is shown.
    """

    def __init__(self, *, session_log: SessionLog | None = None,
                 on_render: Callable[[RenderUpdate], None] | None = None,
                 job_sink: Callable[[ActivityEvent], None] | None = None,
                 line_cap: int = DEFAULT_LINE_CAP, drain_limit: int = DEFAULT_DRAIN_LIMIT,
                 max_pending: int = DEFAULT_MAX_PENDING) -> None:
        self.channel = ActivityChannel(max_pending)
        self.model = ActivityModel(line_cap)
        self.session_log = session_log
        self.on_render = on_render or (lambda update: None)
        self.job_sink = job_sink or (lambda event: None)
        self.drain_limit = drain_limit
        self._owner = threading.get_ident()
        self._closed = False
        self._routing = False
        self._log_failure_reported = False
        self._drops_reported = 0
        self._summary_out: list[ActivityLine] = []
        self._detailed_out: list[ActivityLine] = []

    @property
    def closed(self) -> bool:
        return self._closed

    # -- producers --------------------------------------------------------------------
    def emit(self, event: ActivityEvent) -> bool:
        """Thread-safe. Logs the event now and queues it for the UI thread's next pump."""
        if self._closed:
            return False
        self._to_disk(event)
        return self.channel.emit(event)

    def report(self, event: ActivityEvent) -> bool:
        """UI thread only: route *event* now, after everything already queued."""
        return self.report_many([event])

    def report_many(self, events: list[ActivityEvent]) -> bool:
        """UI thread only: route several events with a single display update."""
        if self._closed:
            return False
        if threading.get_ident() != self._owner:
            raise RuntimeError("Activity.report must be called from the UI thread; use emit() elsewhere")
        events = list(events)
        for event in events:
            self._to_disk(event)
        if not self._routing:  # inside routing, the earlier events are already being shown
            self._route(self.channel.drain(None))
        self._route(events)
        return True

    def pump(self, limit: int | None = None) -> int:
        """UI thread: route at most *limit* queued events (bounded work per tick)."""
        if self._closed:
            return 0
        events = self.channel.drain(self.drain_limit if limit is None else limit)
        self._route(events)
        return len(events)

    def pump_all(self) -> int:
        """UI thread: route everything queued right now."""
        if self._closed:
            return 0
        events = self.channel.drain(None)
        self._route(events)
        return len(events)

    # -- routing -------------------------------------------------------------------------
    def _route(self, events: list[ActivityEvent]) -> None:
        was_routing, self._routing = self._routing, True
        try:
            for event in events:
                if self._closed:
                    break
                if event.kind == "job":
                    self._render_out()  # every line before a job event is on screen first
                    self.job_sink(event)
                    continue
                s_line, d_line = self.model.add(event)
                if s_line is not None:
                    self._summary_out.append(s_line)
                self._detailed_out.append(d_line)
            self._note_drops()
            self._note_log_failure()
        finally:
            self._routing = was_routing
        self._render_out()

    def _render_out(self) -> None:
        if self._closed or not (self._summary_out or self._detailed_out):
            return
        update = RenderUpdate(tuple(self._summary_out), tuple(self._detailed_out))
        self._summary_out.clear()
        self._detailed_out.clear()
        self.on_render(update)

    def _show(self, event: ActivityEvent) -> None:
        """Display an Activity-owned notice without logging it (it may be about the log)."""
        s_line, d_line = self.model.add(event)
        if s_line is not None:
            self._summary_out.append(s_line)
        self._detailed_out.append(d_line)

    def _to_disk(self, event: ActivityEvent) -> None:
        log = self.session_log
        if log is None or log.failed or event.kind == "job":
            return
        log.write(event)

    def _note_drops(self) -> None:
        dropped = self.channel.dropped
        if dropped <= self._drops_reported:
            return
        count, self._drops_reported = dropped - self._drops_reported, dropped
        where = ("the session log has every line" if self.session_log is not None
                 and not self.session_log.failed else "they could not be saved")
        self._show(ActivityEvent(
            "activity", f"{count} routine detail line(s) were left out of this view to keep the "
                        f"window responsive; {where}.", WARNING))

    def _note_log_failure(self) -> None:
        log = self.session_log
        if log is None or not log.failed or self._log_failure_reported:
            return
        self._log_failure_reported = True
        verb = "created" if log.path is None else "written"
        self._show(ActivityEvent(
            "activity", f"The session log could not be {verb} ({log.last_error}); technical "
                        "details are not being saved for this session.", WARNING, summary=True))

    def note_log_open_failure(self) -> None:
        """Make an unavailable session log visible once (call after ``SessionLog.open``)."""
        if self._closed:
            return
        self._note_log_failure()
        self._render_out()

    # -- display ----------------------------------------------------------------------------
    def clear_display(self) -> None:
        """Clear Log: empty the displayed Summary/Detailed history. The log file is untouched;
        later events display normally."""
        if self._closed:
            return
        self.model.clear()
        self._summary_out.clear()
        self._detailed_out.clear()
        self.on_render(RenderUpdate(cleared=True))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.channel.close()
        if self.session_log is not None:
            self.session_log.close()


# ---------------------------------------------------------------------------- the pump

class ActivityPump:
    """Drains the channel from a scheduler while something can still produce events.

    *schedule*/*cancel* have the shape of ``widget.after`` / ``widget.after_cancel`` but are plain
    callables, so this module needs no widget. At most one callback is outstanding. A backlog
    is drained in bounded slices one millisecond apart, so the window keeps handling input. It
    stops when nothing is active and nothing is pending, unless ``always_on`` (the scraper's
    window: a pipeline helper thread may still log after the worker has returned).
    """

    def __init__(self, activity: Activity, schedule: Callable[[int, Callable[[], None]], object],
                 cancel: Callable[[object], None], is_active: Callable[[], bool], *,
                 interval_ms: int = DEFAULT_POLL_MS, always_on: bool = False) -> None:
        self._activity = activity
        self._schedule = schedule
        self._cancel = cancel
        self._is_active = is_active
        self._interval = max(1, int(interval_ms))
        self._always_on = always_on
        self._handle: object | None = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running or self._activity.closed:
            return
        self._running = True
        self._handle = self._schedule(self._interval, self.tick)

    def tick(self) -> None:
        self._handle = None
        if not self._running:
            return
        try:
            self._activity.pump()
        finally:
            more = self._activity.channel.pending() > 0
            if (self._running and not self._activity.closed
                    and (self._always_on or more or self._is_active())):
                self._handle = self._schedule(1 if more else self._interval, self.tick)
            else:
                self._running = False

    def stop(self) -> None:
        self._running = False
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                self._cancel(handle)
            except Exception:  # noqa: BLE001 - a dead Tk interpreter has nothing left to cancel
                pass
