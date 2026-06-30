# webnovel-scraper - Handoff

## Current Focus — 0.2.0 live-test bug-fix drop (Phase C DONE; release-prep NEXT)
**2026-06-30, HOME-PC. Working from `md-instructions/0.2.0_live-test-bug-report-and-fix-plan.md`
(3 bugs surfaced by the user's first 0.2.0 live test, all on the legacy WND path; NOT 0.2.0
regressions). ALL THREE BUGS NOW FIXED (BUG-2 in Phase B, BUG-1 + BUG-3 in Phase C). NOT
committed; 0.2.0 stays Unreleased/undated. `verify` GREEN at 255 passed (245 after Phase B;
+10 new BUG-1 tests = 255). Ran ONCE foreground under a 300s watchdog — pytest 7.36s, no test
needed the watchdog.**

### PERSISTED Phase A root-cause findings (accepted; survive compaction)
- **BUG-1 (HIGH, intermittent) — camoufox page closed before nav (WND ladder, shared code).**
  `request_manager.py` caches one camoufox page and NEVER checks it is alive
  (`grep is_closed/is_connected` = none). `_ensure_camoufox_page` returns a dead
  `self._cf_page`; the non-fresh `camoufox` rung doesn't reset; `_warm_camoufox_session`
  swallows the "page closed" warm-up failure and marks the host warmed, leaving a dead page;
  the chapter `goto` then fails the WHOLE attempt. No within-attempt recreate — recovery only
  comes from the NEXT rung (`camoufox_fresh` → `_reset_camoufox`). When the recreated page is
  ALSO torn down (BUG-3) both rungs fail (live run #1). **Fix = Phase C**: guarded liveness
  check + recreate-on-closed within the attempt (purely additive; never triggers on a healthy
  FWN page → scope gate / single-lane / ch-102 detector untouched). Reproduced offline.
- **BUG-2 (MEDIUM) — slow Stop + no atomic write. FIXED THIS PHASE (see below).** Exact cause
  was the monolithic `self._sleep(delay)` inter-attempt backoff in
  `_fetch_with_retry_ladder` (up to ~138s, un-sliced, cancel only checked at loop-top). The
  legacy WND manager was also built WITHOUT a `sleep_fn`, so it used real `time.sleep`.
- **BUG-3 (INVESTIGATE, partly environmental) — headful camoufox stalls/torn-down while
  unfocused on Windows.** Firefox background/occlusion throttling of timer-driven CF-challenge
  JS + `wait_for_timeout`; "click in → unstuck" matches. Likely shares BUG-1's root cause.
  **Phase C**: prefer concrete load/selector/network signals over `wait_for_timeout` where it
  helps; if still partly an OS reality, DOCUMENT it (one-line README/handoff note) — NO
  fake-focus/automation-evasion hacks.
- **Phase-4 lifecycle CONFIRMED present in live `app.py`** (non-daemon worker `daemon=False` +
  `_begin_close`/`_poll_close` poll-until-exit before `destroy`); module docstring still says
  "daemon thread" (stale wording, not stale code). **No `FETCH_TIMEOUT` global writes remain**
  (only the module constant; `app.py` has zero refs; config travels on `ScrapeJob`).

### Phase B — what changed (per file)
- `request_manager.py` — (1) new module const `BACKOFF_WAIT_SLICE = 0.25`; (2) new
  `RequestManager._cancellable_backoff(seconds)` slicing helper (≤0.25s slices, re-checks
  `cancel_event` each slice, drives off the injected `self._sleep`, returns early on cancel —
  mirrors `HostRateLimiter._wait`); (3) `_fetch_with_retry_ladder` now calls
  `self._cancellable_backoff(delay)` instead of `self._sleep(delay)` (the loop-top check still
  raises `ScrapeCancelled`); (4) `_warm_camoufox_session` now `except ScrapeCancelled: raise`
  BEFORE its broad `except Exception`, so a Stop during warm-up nav propagates (matters on the
  FWN path's shared limiter). Main-ladder `ScrapeCancelled` re-raise UNCHANGED.
- `pipeline.py` — legacy-path `RequestManager(...)` build now threads `sleep_fn=sleep_fn` (the
  run's injected timing source) so the legacy ladder's sliced backoff is fake-clock-drivable in
  tests instead of falling through to real `time.sleep`. Rescue path / sweep / scope gate
  untouched.
- `pdf_builder.py` — (1) new `_atomic_write(path, write_fn)` (temp-in-same-dir →
  `os.replace`, cleans temp on any failure); (2) `remove_single_heading_pages` now reads the
  source from an in-memory `BytesIO` copy (no lingering handle) and rewrites atomically;
  (3) `create_pdf` stages the whole build (ReportLab + heading-strip) in a `.part` temp and
  publishes with a single atomic `os.replace` only on full success — a cancel/crash/kill at any
  point leaves NO corrupt final PDF and NO `.part` artifact. Covers SEPARATE/CHUNKED/SINGLE and
  the FWN path. Added `import os, tempfile, from io import BytesIO`.
- Tests — **NEW `files/tests/test_bug2_cancel_atomic.py` (12 tests)**: sliced backoff aborts
  within one slice on cancel (fake clock); real-clock regression guard that HANGS if the
  backoff reverts to a monolithic sleep; full-wait-preserved-when-not-cancelled (slices sum to
  the delay); `_warm_camoufox_session` re-raises `ScrapeCancelled` (and still swallows a
  non-cancel warm-up failure); main-ladder `ScrapeCancelled` re-raise unchanged; atomic
  `create_pdf` success/valid-PDF/no-`.part`; mid-write failure (os.replace raises) leaves no
  corrupt final + no temp; failure doesn't clobber an existing good final; all 3 output modes
  write finals with no `.part`; FWN healthy run writes all 5, `failed==[]`, no `.part`, scope
  gate intact (pool never built).
- Tests UPDATED (4 existing, faithful to original intent — the backoff is now sliced, so a
  recording `sleep_fn` sees ≤0.25s slices; assertions switched from an exact per-attempt list
  to TOTAL + slice-cap): `test_phase2.py` (×3 incl. the parametrized retry test),
  `test_cf_avoidance.py` (launch-failure-still-backs-off), `test_headful_camoufox.py`
  (stealth-launch-failure-non-blocking). No test deleted/ignored to hit a number.

### Phase B divergences / notes
- The sliced backoff lives in the SHARED `_fetch_with_retry_ladder`, so the FWN browser-primary
  path's inter-attempt backoff is now cancel-aware too — a benign superset of the WND-only ask,
  no behavior change beyond interruptibility. Total backoff duration is unchanged (verified by
  the sum-preserved test).
- The inter-fetch `_Pacer.sleep` (3.0s default) is still a single `self._sleep` — left as-is
  (the plan scoped the fix to the long ladder backoff; 3s is tolerable for Stop latency). Noted
  as a possible future polish, not done now.
- CHANGELOG NOT touched: BUG-2/3 are pre-acceptance fixes to the still-Unreleased 0.2.0; fold
  into the 0.2.0 entry at acceptance/tag time (keeps 0.2.0 Unreleased/undated; docs gate green).

### Phase C — what changed (BUG-1 liveness/recreate + BUG-3 doc note)
All in the SHARED camoufox code in `request_manager.py` (cf_bypass.py UNTOUCHED) — purely
additive/guarded robustness that NEVER triggers on a healthy page.
- `request_manager.py` —
  - **NEW module helper `_page_is_alive(page)`** — returns whether a cached browser page can be
    reused, WITHOUT throwing. Conservative in the SAFE direction: a page is ALIVE unless
    *positively* proven closed (`page.is_closed()` truthy, or `is_closed()` *raises* → dead). A
    page object that does NOT expose `is_closed` (a test fake, or anything we can't introspect)
    is treated as **alive** — the only choice that lets the guard never spuriously recreate a
    healthy page (real Playwright/camoufox pages always have `is_closed()` → `False` when
    healthy; the existing offline suite caches a bare `object()` as the page). NOTE: the Phase C
    plan sketched "returns False on a no-method object"; that would recreate every cached
    healthy/fake page lacking the hook and break the object()-reuse tests, so the safe default
    is used and documented (in code + in the new test's module docstring).
  - **NEW module helper `_is_page_closed_error(exc)`** — True iff `str(exc)` looks like a
    Playwright/Camoufox "target/page/context/browser has been closed" teardown (matched by
    message substring via `_PAGE_CLOSED_MARKERS`, so it needs no playwright import and survives
    class renames). Never raises. `ScrapeCancelled`/other failures → False (escalate normally).
  - **`_ensure_camoufox_page`** now calls `_page_is_alive(self._cf_page)` before reusing the
    cached page; a positively-dead page triggers `_reset_camoufox()` + a fresh build WITHIN the
    attempt. Healthy page → reused unchanged (guard never fires).
  - **`_fetch_camoufox_once`** wrapped in a `for attempt in range(2)` loop: if warm-up or the
    chapter goto raises a closed-page error on the FIRST try, `_reset_camoufox()` + retry ONCE
    on a fresh page within the SAME rung. A second closed error (or any non-closed failure)
    re-raises so the ladder advances — exactly one recreate per rung, no loop. `ScrapeCancelled`
    re-raises immediately (never treated as a closed-page retry).
  - **`_warm_camoufox_session`** — its broad `except Exception` now special-cases a closed-page
    error: do NOT mark the host warmed, do NOT leave the dead page cached — `_reset_camoufox()`
    + re-raise so `_fetch_camoufox_once`'s recreate-and-retry rebuilds + re-warms a fresh page
    (the old behaviour swallowed it, marked warmed, and handed the caller a corpse → the chapter
    goto then failed the whole rung = the live BUG-1). A NON-closed warm-up failure is still
    swallowed + marks warmed (Phase B behaviour unchanged). `ScrapeCancelled` re-raise (Phase B)
    unchanged and FIRST in the except chain.
- **BUG-3 — assessed, NOT code-fixed (environmental); documented.** The CF interstitial is
  JS-rendered and clears on Cloudflare's own timers; there is NO reliable concrete
  selector/load signal to wait on for "challenge cleared" (we already positively wait on
  `has_real_payload` in `cf_bypass.fetch_camoufox`). The stall is Firefox throttling its
  background/occluded-window timers on Windows — an OS reality, not something an external
  `wait_for_*` can un-throttle. Per the plan's explicit allowance, the poll is left as-is and
  the **liveness recreate (BUG-1 fix) is the primary code mitigation** for the closed-window
  case. Documented honestly:
  - `README.md` — one sentence under "Cloudflare handling": keep the browser window visible;
    minimised/occluded Firefox may have challenge timers throttled; click into it if a chapter
    stalls.
  - (full note here in handoff, above.)
- Tests — **NEW `files/tests/test_bug1_camoufox_liveness.py` (10 tests)**: liveness-helper edge
  inputs (None→dead, open→alive, closed→dead, raising→dead, no-`is_closed`→alive); closed-error
  detector positives/negatives (incl. `ScrapeCancelled`→False); dead cached page → reset +
  recreate → success; page dies mid-attempt → reset + retry-within-rung succeeds; two
  consecutive dead pages → raises, exactly one recreate (chapter goto attempted exactly twice,
  no loop); warm-up closed → host NOT warmed + dead page NOT cached + reset ran; warm-up
  NON-closed failure → swallowed + marked warmed (Phase B unchanged); healthy page reused across
  3 chapters → never recreated, browser never reset; recreate attempt returns the correct body
  via `_fetch_uncached_strategy`; `ScrapeCancelled` still re-raises through warm-up. No existing
  test changed (the object()-page reuse tests stay green because missing-`is_closed` = alive).

### Phase C divergences / notes
- Liveness "missing `is_closed` = ALIVE" default deviates from the plan's literal "returns False
  on a no-method object" — see the rationale above; the deviation is what KEEPS the never-fire
  guarantee and the existing green suite. Documented in code + test docstring.
- BUG-3 is intentionally a doc-note, not a code change to `cf_bypass.py` (no reliable concrete
  signal for the JS interstitial; the BUG-1 recreate is the real mitigation). cf_bypass.py was
  NOT touched.
- FWN scope gate (`job.adapter_key == "freewebnovel" and job.use_browser`), single-lane
  invariant (`RESCUE_MAX_WORKERS = 1`), and the ch-102 payload-gated detector are **UNTOUCHED**.
  The guard is purely additive and never fires on a healthy FWN page.
- CHANGELOG still NOT touched (BUG-1/2/3 are pre-acceptance fixes to still-Unreleased 0.2.0;
  fold into the 0.2.0 entry at acceptance/tag time).

**SAFE TO COMPACT before release-prep: YES.** All Phase C changes are on disk, `verify` green at
255, nothing in-flight. All three live-test bugs (BUG-1/2/3) are now addressed. Next step is the
user's acceptance of 0.2.0 (fold BUG-1/2/3 into the 0.2.0 CHANGELOG entry at tag time per §8) or
a fresh live re-test — no further automated phase is pending.

## Current Focus (newest)
**0.2.0 IMPLEMENTED — all five phases complete and verified; PENDING the user's manual
live test (§7). NOT committed; 0.2.0 stays Unreleased/undated (2026-06-30, HOME-PC).**
Phase 5 was docs + version only (no behaviour change): `CHANGELOG.md` gained
`## [0.2.0] — Unreleased` (undated; describes a SINGLE background rescue lane, not "N
workers"; cites the actual breaker thresholds ≥5 consecutive OR ≥9-of-20, the 180s
deadline, `HOST_MIN_INTERVAL=3.0`, `RATE_LIMIT_RETRY_BUDGET=2`); `Briefing.md` got the
0.2.0 architecture (`rescue_pool.py` single lane, `host_rate_limiter.py`, fast/HTTP-probe
split, the pipeline conductor + scope gate + headless-only breaker + TOC bootstrap,
`ScrapeJob` run-config), "Current Version → 0.2.0 (Unreleased)", a 0.2.0 "What Has Been
Built" entry, an updated Known Issues (headless-clearance is the open live question) and
Next Steps (§7 Pass A + Pass B, cache-OFF + fresh folder); `README.md` got one minimal
note (Headless may be auto-overridden to visible for the rest of a broadly-blocked run;
hard chapters may open a visible rescue browser); and a new light offline
`files/tests/test_release_metadata.py` (4 tests) asserts the top CHANGELOG version is
0.2.0, Briefing agrees, and 0.2.0 stays Unreleased/undated.

`verify` GREEN at **233 passed** (Phase-4 run was 228 passed + 1 intermittent no-Tk skip
= 229 collected; +4 release-metadata = 233 collected; this run all GUI tests passed so the
no-Tk skip did not trigger — it remains an expected, by-design graceful skip on a machine
with no Tk display). No tests deleted/ignored to hit a number. Ran ONCE foreground under a
300s watchdog — completed in ~7.2s, no test needed the watchdog to terminate.

**verify.py needed NO change.** Its docs gate requires `^##\s*\[?v?\d+\.\d+\.\d+\]?`,
which `## [0.2.0] — Unreleased` already satisfies (the date is optional), so the undated
Unreleased heading passes without weakening the pattern. Not a divergence.

**SAFE TO COMPACT before the manual live test: YES.** Phase 5 is the last automated phase;
everything is on disk, `verify` is green at 233, nothing is in-flight. A future
context-cleared session can resume straight into helping interpret/triage the live pass
results (or starting 0.2.1 only after the user accepts 0.2.0 per §8). See the consolidated
cross-phase report in the Session Sync Log entry below.

## Current Focus (Phase 4)
**0.2.0 — Phase 4 complete (GUI wiring: 3.0s delay default, accurate Headless hint,
rescue/breaker activity surfaced in the existing log pane, the GUI-pre-built
RequestManager retired in favour of pipeline-owned managers, and the non-daemon
window-close poll-until-exit lifecycle); NOT committed, 0.2.0 stays Unreleased
(2026-06-30, HOME-PC).** `verify` GREEN at **228 passed, 1 skipped** (Phase-3 baseline
222 + 7 new Phase-4 tests = 229 collected; the 1 skip is the graceful no-Tk-display
fallback, see below). No tests deleted. Ran ONCE foreground under a 300s watchdog —
completed in 8.54s, no test needed the watchdog to terminate. Docs/version are Phase 5.

**What changed, per file:**
- `app.py` (the thin GUI shell) —
  - **§4.1 delay default → `DEFAULT_DELAY = "3.0"`** (anti-detection hint unchanged).
  - **§4.2 Headless hint copy** rewritten to be accurate about the breaker override +
    the visible rescue browser: *"Headless primary browser (advanced). If Cloudflare
    broadly blocks headless mode, the app may automatically open and keep a visible
    browser for the rest of the run; hard chapters may also open a visible rescue
    browser."* (the only permitted copy change).
  - **§3.15 — the GUI no longer pre-builds a `RequestManager`.** Removed the
    `from webnovel_scraper.request_manager import RequestManager` import and the
    `RequestManager(...)` construction in `_on_start`; the pipeline now OWNS/replaces
    the active primary manager via its factory seam (rescue path) or builds its own
    from the job (legacy path). `_run_worker(self, job)` lost the `rm` parameter and
    its `rm.close()` teardown (the pipeline closes its managers). This closes the
    Phase-3 transitional divergence (the GUI-passed `rm` was ignored on the rescue
    path).
  - **Stopped mutating `SiteSpec.use_browser`.** `_on_start` now computes a local
    `use_browser` and passes it via `ScrapeJob.use_browser`; the catalog row is never
    mutated. The Start log line reads the local `use_browser`.
  - **§4.4 window-close lifecycle.** The scrape worker is now **non-daemon**
    (`daemon=False`) so the pipeline's teardown `finally` (browser + rescue-pool close)
    always runs. New `_closing` flag + `CLOSE_POLL_MS = 50`. `_on_close` is idempotent
    and, while running, confirms then calls `_begin_close` (set `cancel_event`, mark
    closing, lock buttons) → `_poll_close`, which re-schedules itself via `self.after`
    on the Tk event loop until `self._worker` is no longer alive, then `destroy()` —
    instead of destroying immediately. `_thread_log`/`_thread_progress` and the worker's
    final `self.after(0, self._on_run_finished)` are now wrapped so a late callback on a
    destroyed window is a silent no-op (a non-daemon worker can still emit one line after
    close begins).
- `pipeline.py` (`run_scrape`, legacy branch only) — because the GUI stopped
  pre-building/handing down a manager, the adapter-less legacy path now (a) derives a
  per-run spec via `runtime_site_spec(spec, job)` so **`job.use_browser` drives the
  adapter** (the catalog FWN row is `use_browser=True`, so an unchecked-browser FWN run
  would otherwise wrongly launch a browser), and (b) threads `headless=job.headless` +
  `http_timeout=job.request_timeout` into the `RequestManager` it builds (preserving the
  Timeout field the GUI used to supply). **The rescue path, the sweep logic, and the
  injected-adapter branch are otherwise unchanged** (an injected adapter still receives
  the catalog spec; default jobs build a manager with the same defaults as before, so
  existing legacy/WND tests are unaffected).
- Tests — **NEW** `files/tests/test_phase4_gui.py` (7): delay default == "3.0";
  source-scan guard (no `RequestManager(`, no `import RequestManager`, no `FETCH_TIMEOUT`,
  no `spec.use_browser =`, `daemon=False` present); Headless hint mentions the visible
  override + rescue browser; **Start launches a non-daemon worker that passes the
  ScrapeJob and NO `request_manager`** (job carries delay 3.0 / timeout / `use_browser` /
  `rescue_workers==1`); **close polls until the worker exits then destroys** (fake worker
  + injected `after` + idempotent second close — no real-clock wait); thread callbacks are
  safe after destroy; **legacy path threads job config + derives `use_browser` from the
  job** (recording adapter + recording manager prove `use_browser=False` reaches the
  adapter despite the catalog `True`, and the self-built manager honours
  `http_timeout`/`headless`/`use_cache`/`http_first`).

**Code-vs-plan divergences (how I adapted):**
- **Legacy-path pipeline edit was required to drop the `SiteSpec.use_browser` mutation
  safely.** The plan's §4 frames Phase 4 as GUI-only, but removing the GUI mutation
  exposed that the legacy path read `spec.use_browser` straight off the catalog row
  (FWN = `True`). I threaded `runtime_site_spec` + the job's timeout/headless into the
  legacy real-adapter branch so behaviour is preserved (and now job-driven). This is the
  minimal change needed to honour the §3.14 "config travels on the job, not a mutated
  catalog row" invariant; the sweep logic itself is untouched. Documented, not scope creep.
- **One Tk-instantiating GUI test intermittently SKIPS under the full suite.** The
  display-needing tests use the same `pytest.importorskip("tkinter")` + `TclError →
  pytest.skip` guard as the pre-existing `test_phase8_gui.py`. On Windows, repeated
  `tk.Tk()` creation across the full session occasionally trips Tcl's "can't find a usable
  init.tcl" init; when that happens the test skips gracefully rather than failing (the
  standalone file run shows all 7 passing). This matches the established GUI-test pattern
  (no headful/display test is a hard requirement in `verify`, per §5/Phase 4). No live
  browser is ever launched in the suite.
- **Phase-3 transitional divergence #1 (GUI-passed `rm` ignored on the rescue path) is now
  CLOSED** — the GUI passes no manager at all. Divergences #2 (final-drain thread-coordination
  polling) and #3 (rescue-pool initial mode latched at run start) are pipeline-internal and
  untouched by Phase 4.

**SAFE TO COMPACT before Phase 5: YES.** No in-flight state — all changes are on disk,
`verify` is green at 228 passed / 1 skipped, no test needed the watchdog to terminate
(full gate 8.54s). Phase 5 is docs + version only (§5): `CHANGELOG.md` `## [0.2.0] —
Unreleased` (singular rescue lane, not "N workers"), `Briefing.md` architecture +
"Current Version → 0.2.0 (Unreleased)" + suite count, `handoff.md` Current Focus +
not-committed Sync Log entry, a minimal `README.md` note (Headless may be overridden to
visible; hard chapters may open a visible rescue browser), and the light release-metadata
test. No commit, no push.

## Current Focus (Phase 3)
**0.2.0 — Phase 3 complete (pipeline conductor: TOC bootstrap fallback,
fast-primary loop, single-lane rescue as the SOLE FWN-browser retrier, pipeline-owned
headless-only circuit breaker + headless→visible recreate/latch, 429 host-cooldown
policy, continuous + final rescue drain folded into all three output modes, RunReport
invariants + metrics); NOT committed, 0.2.0 stays Unreleased (2026-06-30, HOME-PC).**
`verify` GREEN at **222 passed** (Phase-2 baseline 203 + 19 new Phase-3 tests). No tests
deleted. Still strictly single-lane (the conductor builds the one `RescuePool` lazily on
the first hard chapter). Ran ONCE foreground under a 300s watchdog — completed in 7.15s,
no test needed the watchdog to terminate. GUI is Phase 4; docs/version are Phase 5.

**What changed, per file:**
- `adapters/freewebnovel.py` — added an optional `fast_path: bool = False` kwarg to
  `build_chapter_index` and `fetch_chapter`, threaded to `rm.fetch(..., fast_path=True)`
  **only when set** (so the legacy call signature — and every existing `FakeRM` test fake
  that mimics it — is unchanged for the default path). This lets the conductor run the
  primary/TOC under the Phase-1 bounded fast policy that raises a typed
  `ChallengeFetchError` quickly.
- `pipeline.py` — the conductor. New imports (`collections`, `HostRateLimiter`,
  `runtime_site_spec`, typed `FetchError` subclasses, `rescue_pool as rp`,
  `HOST_MIN_INTERVAL`). Breaker thresholds (`BREAKER_CONSECUTIVE_CHALLENGES=5`,
  `BREAKER_WINDOW=20`, `BREAKER_WINDOW_CHALLENGES=9`), `RATE_LIMIT_RETRY_BUDGET=2`, and
  `class ChapterIndexUnavailable`. `RunReport` gained the §3.16 metrics
  (`rescue_exhausted` ⊆ `failed`, `rescue_queue_peak`, `rescue_jobs_submitted`,
  `rescue_jobs_completed`, `rescue_worker_failures`, `circuit_breaker_tripped`,
  `primary_switched_visible`, `rescue_strategy`) + two summary lines. `_rescue_enabled`
  = the FWN-browser scope gate (`adapter_key=="freewebnovel" and use_browser`).
  `_CircuitBreaker` (headless-only, armed only when the run started headless; counts only
  uncached primary NETWORK fetches; consecutive≥5 OR ≥9-of-20 challenges trips).
  `_PrimaryEngine` (the pipeline-owned manager+adapter pair; headless fixed at
  construction; `switch_to_visible` recreates both and closes the old EXACTLY ONCE;
  `managers_closed` for the test). `_RescueConductor` (per-chapter routing: success /
  hard→rescue / not-found / extraction / 429; breaker trip → switch+latch+synchronous
  visible retry; continuous `drain()` + blocking `final_drain()`; immutable
  `RescueResult` folded into the report + content store on the pipeline thread; lazy pool
  so an easy run never builds a worker). `_run_with_rescue` (TOC bootstrap via
  `_bootstrap_toc`, range clamp, `_plan_fetch_list` resume-skips per mode, the fast loop,
  final drain, `_assemble_output` writing CHUNKED/SINGLE folded in index order while
  SEPARATE is written promptly by `_make_on_content`; default real factories share one
  `HostRateLimiter` + `cancel_event`; closes the final manager + pool in `finally`).
  `run_scrape` rewired: new optional seams (`monotonic_fn`, `host_limiter`,
  `request_manager_factory`, `primary_adapter_factory`, `rescue_pool_factory`) + the
  scope-gate branch. **Legacy `_drive` / `_run_separate` / `_run_chunked` / `_run_single`
  / `_Pacer` are UNCHANGED** — the WND/HTTP/injected-adapter sweep path is byte-for-byte
  the same (an injected `adapter` always keeps the legacy flow).
- Tests — **NEW** `files/tests/test_phase3_rescue_conductor.py` (19): scope gate (3 cases)
  + WND-regression-never-builds-a-pool; easy-run-never-instantiates-rescue; TOC
  headless-block→visible-retry→success (manager recreated, each closed once) + visible-
  still-fails→clean abort + visible-primary-block-aborts-without-a-retry; SEPARATE/
  CHUNKED/SINGLE fold rescued in index order; permanent NotFound recorded + run completes;
  rescue_exhausted is failed-not-rescued; breaker unit thresholds (consecutive, reset on
  non-challenge, ≥9-of-20 window without 5-in-a-row, not-armed); breaker trips→switch→
  synchronous-visible-retry→latch (managers closed once each, ch5 retried visible, 6-8
  latched visible, 1-4 rescued); breaker NOT armed on a visible-primary run (one manager,
  all rescued); 429 cooldown observed on the shared limiter + no breaker + resolves on the
  primary (never escalated to rescue) + persistent-429→transient-for-resume; `_Pacer`
  block raises the SHARED limiter interval (rescue paces with it); RunReport invariants
  under a mixed scenario (rescued∩failed=∅; permanent/extraction/exhausted ⊆ failed);
  Stop cancels loop+pool incl. a job mid-fake-CF-wait, every accepted job terminalizes
  (`jobs_completed==jobs_submitted`, `outstanding==0`), cancelled≠rescue_exhausted.

**Code-vs-plan divergences (how I adapted):**
- **`request_manager` passed by the current GUI is IGNORED on the rescue path
  (transitional).** Until Phase 4 rewires the GUI to pass config via `ScrapeJob` + the
  factory seam (§3.15), a FWN-browser run from the live GUI passes a pre-built `rm` that
  the conductor does not use (it owns managers via the default factory built from job
  config: slug/use_cache/headless/http_first/request_timeout + the shared limiter +
  cancel_event). The GUI still closes its unused `rm` in its own `finally` — a harmless
  no-op (the manager is lazily started, so an un-started `close()` does nothing). Phase 4
  removes the GUI-owned manager. Documented, not weakened.
- **Final drain uses thread-coordination polling, not a logical fake-clock wait.**
  `final_drain` does `pool.join(0.1)` in a loop until the worker thread exits, draining
  between — the SAME rationale as the pool's internal queue polling (the worker makes
  real progress, so it can never hang a fake-clock test; the cancel test proves the
  end-to-end join completes without the watchdog). The CRITICAL TIMING requirement is
  upheld: the only LOGICAL waits anywhere are the limiter's and the rescue worker's
  `_cancelable_sleep`, both off the single injected `sleep`/`monotonic` seam from
  Phases 1–2. The conductor introduces NO new real-clock logical wait — the 429 retry
  re-calls `primary.fetch`, whose `acquire` waits the host cooldown on the shared
  injected-clock limiter.
- **Rescue pool's initial mode is latched to `job.headless` at run start.** If the breaker
  (or the TOC fallback) later switches the PRIMARY to visible, the rescue worker still
  begins its already-queued jobs from the headless ladder rung — but it only ever
  escalates (Phase 2), so it reaches headful/chromium regardless. The "rescue never starts
  weaker than the primary" invariant holds at construction (the primary was headless
  then); after a breaker trip the conductor retries hard chapters SYNCHRONOUSLY on the
  visible primary and only submits to rescue on continued failure. Noted, not weakened.
- **Previously-logged divergences that did NOT bite this phase.** Phase-0 #2 (403/5xx raise
  points) is moot — Phase-1 body-first classification already routes a 403/CF-503 to
  `ChallengeFetchError`, so the breaker counts challenges correctly with no further change.
  Phase-0 #5 (daemon-thread GUI close) and #6 (GUI-owned manager / mutated SiteSpec) are
  deliberately untouched here — they are the Phase-4 GUI lifecycle change; the conductor
  already consumes a per-run `runtime_site_spec` copy and never mutates the catalog row.

**SAFE TO COMPACT before Phase 4: YES.** No in-flight state — all changes are on disk,
`verify` is green at 222, no test needed the watchdog to terminate (full gate 7.15s).
Phase 4 wires the GUI (§4): delay default → 3.0, accurate Headless hint about the breaker
override + visible rescue browser, surface rescue/breaker activity in the existing log
pane only, and the window-close lifecycle (non-daemon worker; on close set cancel_event,
poll until the worker exits, then destroy) — confirming against the current daemon-thread
GUI and reporting if it conflicts. It should also rewire `_on_start` to pass config via
`ScrapeJob` and let the conductor own the manager (retiring the transitional ignored-`rm`
above) and stop mutating `spec.use_browser`.

## Current Focus (Phase 2)
**0.2.0 — Phase 2 complete (single-lane RescuePool: one dedicated worker thread,
ladder-as-data + monotonic escalation + initial-mode-follows-primary, per-chapter
180s deadline, bounded/dedupe/cancel-aware backpressure, immutable RescueResult,
one-terminal-result + worker-crash handling); NOT committed, 0.2.0 stays Unreleased
(2026-06-30, HOME-PC).** `verify` GREEN at **203 passed** (Phase-1 baseline 187 + 16
new Phase-2 tests). No tests deleted. Still strictly single-lane (`RESCUE_MAX_WORKERS`
HARD cap 1; the pool's constructor rejects `workers != 1`). The pool is built but NOT
yet wired into `run_scrape` — the conductor/TOC bootstrap/breaker/sweep-replacement is
Phase 3; GUI is Phase 4.

**What changed, per file:**
- `rescue_pool.py` — **NEW.** `RescuePool` owns ONE dedicated `threading.Thread`
  worker (no `ThreadPoolExecutor`), a `queue.Queue(maxsize=RESCUE_MAX_PENDING)` job
  backlog + an unbounded results queue. The worker creates its **own**
  `RequestManager` AND its **own** `FreeWebNovelAdapter` (via injectable
  `manager_factory`/`adapter_factory` seams; real defaults build the genuine ones,
  sharing the run's `HostRateLimiter` + `cancel_event`) and uses/closes them all on
  the one worker thread (browser teardown in `finally`). Ladder-as-data
  (`RescueStep`/`RESCUE_LADDER`): `headless_camoufox` ×2 (reuse) + ×1 (fresh) →
  `headful_camoufox` ×2 (first fresh) → `headful_chromium` ×2 (first fresh). Modes
  only escalate (`_MODE_RANK`); a worker latched higher skips lower steps on later
  chapters; `_steps_for_current_latch()` enforces the floor. Initial mode follows the
  primary: headless-primary → start `HEADLESS_CAMOUFOX` (full ladder); visible-primary
  → start `HEADFUL_CAMOUFOX` (skip both headless steps). The headless→headful boundary
  recreates the manager (headless is fixed at construction); camoufox→chromium within
  headful is the manager's own engine-switch. Per-chapter deadline (`started_at` = the
  DEQUEUE time, queue wait not charged): before each attempt it computes `remaining`,
  refuses if `< RESCUE_MIN_ATTEMPT_BUDGET`, and passes `min(attempt_timeout, remaining)`
  as the budget; default fetch applies the budget to nav AND CF wait via
  `manager._fetch_uncached_strategy` then `adapter._extract_chapter`. Immutable
  `RescueResult(meta, content, status, strategy, attempts, error)`; statuses
  `rescued`/`rescue_exhausted`/`cancelled`/`not_found`/`extraction_failed`/`pool_failed`.
  `submit()` is cancel-aware backpressure (dedupe by index AND URL; blocks when full;
  never drops/bypasses the cap). One-terminal-result invariant: every accepted job
  emits exactly one result, including queued-then-cancelled (`cancelled`). Worker-crash
  / factory-init failure → `_PoolFailure`: terminalize active + drain pending as
  `pool_failed`, stop accepting, expose `worker_failed`/`pool_error`. EVERY logical
  wait goes through `_cancelable_sleep` (one injected `sleep`/`monotonic`, sliced +
  cancel-checked); thread coordination uses short real `queue` timeouts (worker makes
  real progress, so no fake-clock hang). `finish()` = graceful drain; `cancel()`/
  `close()` = prompt stop + join. Std-lib only.
- Tests — **NEW** `files/tests/test_phase2_rescue_pool.py` (16): single-lane constants
  + pool rejects `workers=2`; ladder shape + first-headful-fresh; backlog of several
  chapters; clears-on-a-later-step → rescued (strategy `headful_chromium`); never-clears
  → exhausted after the full 7-attempt ladder; monotonic escalation (latched headful
  never returns to headless; first headful fresh); initial-mode-follows-visible-primary
  (no headless step ever run); no double-submit (index AND URL); same-thread ownership
  (manager-create/adapter-create/fetch/teardown all == worker ident, ≠ main; rescue
  adapter distinct from the primary's); deadline bounds total processing == 180 + an
  attempt refused with too little time; every accepted job exactly one result;
  queued-then-cancelled each emit `cancelled`; worker-init failure → pool-level failure
  (all jobs `pool_failed`, no silent loss, stops accepting); not-found/extraction
  terminal-not-retried (one attempt each); cancel interrupts a fake CF wait mid-attempt
  (~5s of a 100s wait); timing-regression guard (a 600 fake-second wait completes via
  the injected clock in ~no real time — would hang on a real-clock regression).

**Code-vs-plan divergences (how I adapted):**
- **Per-attempt budget vs nav+CF.** The plan says pass `min(strategy_timeout, remaining)`
  into nav AND the CF wait. The default fetch does exactly that, so a single REAL
  attempt's wall-time can approach 2× the budget — the worker's deadline check stops the
  *ladder* but the real per-chapter ceiling is ~180s + one attempt's nav/CF overshoot
  (and an in-flight `page.goto` may run to its nav timeout; cancellation is prompt
  *between* polls, §3.12). The deterministic suite proves the LOGICAL bound (each attempt
  consumes ≤ its budget → total ≤ 180). Documented in `_default_fetch`, not weakened.
- **Single-engine rescue fetch reaches manager internals.** The default fetch drives one
  concrete engine via `manager._fetch_uncached_strategy(url, strategy)` + parses via
  `adapter._extract_chapter(html, meta)` (not the adapter's `fetch_chapter`/`rm.fetch`
  ladder) so the WORKER owns escalation precisely (the plan's "escalate by recreating,
  never flipping a live field"). Same-package use of those helpers; rescued content is
  intentionally not cached.
- **No `queue.join()`/`task_done()` anywhere** (the plan warns about a `queue.join()`
  left waiting on an unmatched `task_done()`): the worker uses `get(timeout=_GET_POLL)`
  and breaks on stop/cancel/empty, and accounting is by counters — so there is no
  join-deadlock surface at all.
- **`finish()` (graceful) vs `cancel()` (prompt)** are distinct: graceful drains the
  backlog to completion (the pipeline's final blocking drain); cancel sets `cancel_event`
  and terminalizes the rest as `cancelled`. `cancel_event` is the shared run event so a
  graceful `finish()` deliberately does NOT set it (it would stop the primary too).

**SAFE TO COMPACT before Phase 3: YES.** No in-flight state — all changes are on disk,
`verify` is green at 203, no test needed the watchdog to terminate (Phase-2 file ran in
3.15s; full gate 5.74s). Phase 3 wires `rescue_pool.RescuePool` into `run_scrape`: TOC
bootstrap fallback (§3.11) before the loop, fast-path loop enqueues hard chapters +
continuous/final result drain, rescue as the sole retrier (old sweep off on the FWN
browser path, kept for WND), the pipeline-owned headless-only breaker (§3.9/§3.10) + the
`request_manager_factory` recreate seam (§3.15), `_Pacer`→limiter interval, `RunReport`
metrics (§3.16).

## Current Focus (Phase 1)
**0.2.0 — Phase 1 complete (typed failures + body-first classification + fast/HTTP-probe
split + host limiter + ScrapeJob run-config + per-manager timeouts + last_fetch_info);
NOT committed, 0.2.0 stays Unreleased (2026-06-30, HOME-PC).** `verify` GREEN at **187
passed** (Phase-0 baseline 166 + 21 new Phase-1 tests). No tests deleted. Still strictly
single-lane; rescue pool / conductor / breaker / GUI are Phases 2–5.

**What changed, per file:**
- `models.py` — `ScrapeJob` gained `use_browser`, `headless`, `request_timeout=30.0`,
  `rescue_workers=1`; `__post_init__` rejects `rescue_workers != 1` (invariant #1). Added
  `runtime_site_spec(spec, job)` = `dataclasses.replace(spec, use_browser=job.use_browser)`
  so a per-run SiteSpec copy drives fetching without mutating the shared catalog row
  (§3.14). `EmptyExtractionError` left exactly where it was (NOT under FetchError).
- `host_rate_limiter.py` — **NEW.** `HostRateLimiter(interval, *, jitter_ratio, monotonic,
  sleep, random_fn, default_cooldown)`: per-host key (`normalize_host` = lowercase host +
  explicit port), FIFO ticket deque (front waiter always wins → no starvation), lock NOT
  held during the wait, positive-only jitter added after the interval, `raise_interval`
  (never lowers — for `_Pacer` in Phase 3), `note_rate_limited`/`blocked_until` host
  cooldown for 429, cancel-aware wait raising `ScrapeCancelled`. Std-lib only.
- `request_manager.py` — typed `FetchError` subclasses `NotFoundFetchError` (404/410,
  terminal), `ChallengeFetchError`, `TransientFetchError`, `RateLimitedFetchError(retry_after)`
  (§3.3); `_get_text` is now an **instance method** doing body-first classification (real
  payload → success even on 403/503; CF body → Challenge incl. a CF-style 503; 404/410 →
  NotFound; 429 → RateLimited+Retry-After; 5xx → Transient; bare 403 → Challenge + an
  "unmatched 403 body" log). `PERMANENT_STATUSES` redefined to `(404, 410)` (alias of new
  `NOT_FOUND_STATUSES`) — **403 is no longer permanent.** Fast ladders `FAST_BROWSER_LADDER`
  / `FAST_HTTP_FIRST_LADDER` + `fetch(..., fast_path=True)` (camoufox×2, no stealth rung;
  HTTP probes are EXTRA, never consume the browser budget — §3.2a). Constructor gains
  explicit `http_timeout` / `browser_nav_timeout` / `cloudflare_timeout` (retiring the
  mutated module global) and `host_limiter`; `_acquire_nav` gates every top-level nav
  (HTTP warm-up + chapter, camoufox warm-up + chapter, stealth chapter); the ladder raises
  the **last typed error** on exhaustion and records `last_fetch_info` (cache/success/
  challenge/transient/not_found/rate_limited) for the Phase-3 breaker. 429 notifies the
  shared limiter cooldown and is terminal (not escalated to browser).
- `cf_bypass.py` — `fetch_with_stealth` / `fetch_camoufox` no longer short-circuit on 403
  (only 404/410 raise); a 403 proceeds to the CF wait/poll and the body is classified by
  the caller (§3.3, browser-403-clears-first). `fetch_with_stealth` returns the current page
  on a non-clear instead of raising, so the manager classifies body-first.
- `app.py` — removed the `request_manager.FETCH_TIMEOUT` module-global mutation (and its now
  unused `rm_module` import); the GUI passes `http_timeout=timeout` to the manager and sets
  `use_browser`/`headless`/`request_timeout` on the `ScrapeJob`. (Full GUI wiring is Phase 4.)
- Tests — **NEW** `files/tests/test_phase1_rescue_core.py` (21). Adjusted 4 existing
  assertions to track the deliberate behavior change: the old internal `_RetryableFetch`
  signal is now the typed `ChallengeFetchError` (`test_phase9.py` ×2,
  `test_camoufox_cleared_detection.py` ×1) and `PERMANENT_STATUSES` is now `(404, 410)`
  (`test_phase2.py`); one prose comment in `test_brotli_extraction_fix.py`. No test deleted.

**Code-vs-plan divergences (how I adapted):**
- **Host-limiter wait can't be `cancel_event.wait` under a fake clock** (it would block real
  time while the injected monotonic never advances → hang). Adapted: the wait always times
  via the injected `sleep` (one timing source, deterministic), sliced into ≤0.25s chunks
  that re-check `cancel_event` — so it still aborts promptly on Stop (honors the §3.4
  "not a bare sleep" intent) without coupling to real time. Noted, not weakened.
- **Limiter ↔ ScrapeCancelled import cycle** avoided by importing `ScrapeCancelled` lazily
  inside `acquire` only on the cancel path (request_manager imports HostRateLimiter at top).
- **`_RetryableFetch` / `_PermanentStatus` kept defined but no longer raised** (back-compat
  for any external import); the typed `FetchError` subclasses now carry the signal. The
  ladder still retries Challenge/Transient (they're `FetchError`s caught by the generic
  retry branch) and treats NotFound/RateLimited as terminal.
- **Limiter is built but not yet injected into the live pipeline** — per the Phase-1 scope
  ("build the limiter + acquire points; don't rewire run_scrape"). Wiring it (and the
  fast_path conductor) is Phase 3. `RequestManager(host_limiter=None)` default keeps every
  existing path byte-for-byte unchanged.

**SAFE TO COMPACT before Phase 2: YES.** No in-flight state — all changes are committed to
disk, `verify` is green, and everything Phase 2 needs (typed errors, fast ladders + the
`fast_path` seam, `HostRateLimiter`, `ScrapeJob` config, `last_fetch_info`, `runtime_site_spec`)
is in source + captured above. Phase 2 builds `rescue_pool.py` (one dedicated thread, queue,
ladder-as-data + monotonic escalation, per-chapter deadline, RescueResult) on these.

## Current Focus (Phase 0)
**0.2.0 concurrent-hard-chapter-rescue — Phase 0 (baseline) complete; NOT committed,
0.2.0 stays Unreleased (2026-06-30, HOME-PC).** Implementing the
`md-instructions/0.2.0_concurrent-hard-chapter-rescue.md` drop in phases (0→5),
verifying after each, then stopping for the user's manual live test. Strictly
SINGLE-LANE rescue (`RESCUE_MAX_WORKERS = 1`); multi-worker is deferred to 0.2.1.
No commit/push at any point.

**Phase 0 — baseline established.**
- **Read-only audit divergence (git):** `git` is **not installed** on this machine and
  this working copy is **not a git repository** (no `.git`). The plan's Phase 0 git
  commands (`git status/branch/log`) could not run. The non-negotiable invariant
  ("preserve ALL existing local changes; no reset/clean/checkout/pull/stash") is
  **trivially satisfied** — there is no git state to mutate. Reported, not weakened.
- **Detector is payload-gated (confirmed):** `cloudflare_detection.is_cloudflare_challenge`
  checks `has_real_payload(html)` FIRST (~line 193) and returns `False` when a real body is
  present, before any strong/ambient marker — the strong-marker path does NOT short-circuit
  ahead of payload.
- **ch-102 fixture present:** `files/test-files/fwn_chapter_102_cleared.html` (sanitized
  real capture — populated `#article` body + "just a moment" in prose + ambient
  `/cdn-cgi/challenge-platform/` beacon).
- **Three ch-102 regression tests present** in `files/tests/test_camoufox_cleared_detection.py`:
  `test_cleared_fwn_102_with_just_a_moment_in_prose_is_not_a_challenge`,
  `test_cleared_fwn_102_adapter_extracts_non_empty_body`,
  `test_cleared_fwn_102_fetch_succeeds_on_first_camoufox_attempt`.
- **Baseline: `verify` GREEN — 166 passed.** Exactly the expected known baseline (the clean
  case); the payload-gate fix is already in place. No tests deleted/added in Phase 0.

**Key code-vs-plan divergences logged for later phases** (code is authoritative for
mechanics; will adapt + note in the final report):
1. `request_manager.FETCH_TIMEOUT` is a **module global** read in the static `_get_text`;
   the GUI mutates it (`app.py` `_on_start`). Plan §3.15 wants explicit per-manager timeouts.
2. **403/404 are raised before the body can be inspected** — HTTP `_get_text` raises
   `_PermanentStatus` on 403/404; browser `cf_bypass.fetch_with_stealth`/`fetch_camoufox`
   raise `RuntimeError("HTTP {status}")` on 403/404 BEFORE the CF wait/poll. Plan §3.3 wants
   body-first classification (403→Challenge after CF wait, 404/410→NotFound) + browser-403
   clears-first.
3. CF detection currently surfaces as the internal `_RetryableFetch`; terminal failure is a
   plain `FetchError`. No typed subclasses, no `HostRateLimiter`, no `last_fetch_info` yet.
4. FWN browser ladder is camoufox→headful stealth-Chromium with one-engine-per-thread
   teardown + a `_camoufox_exhausted` run latch (`headless` fixed at construction; switching
   engines tears the other down) — matches the plan's §3.6 premise.
5. GUI scrape worker is a **daemon** thread; `_on_close` confirms then `destroy()`s
   immediately. Plan §4.4 prefers a non-daemon worker + poll-until-exit on close.
6. GUI **mutates `spec.use_browser`** and constructs/owns the `RequestManager`. Plan
   §3.14/§3.15 want a per-run `SiteSpec` copy + pipeline-owned manager via a factory seam.

## Current Focus (prior)
**0.1.3 FWN ladder refinement — added the bounded headful stealth-Chromium fallback
after camoufox (2026-06-29) — complete on `feature/v0.1.3-headful-camoufox`; `verify`
green at 157 tests.** The prior 0.1.3 pass made headful camoufox the SOLE FWN browser
engine and dropped stealth-Chromium. But the legacy diff showed the old scraper's
proven-working VISIBLE engine was stealth-Chromium (camoufox was its headless-only
path), so the historically-proven engine was missing from the FWN chain. This pass
adds it back as a BOUNDED fallback, not the old storm:
- New FWN per-chapter ladder: `HEADFUL_PRIMARY_LADDER = (camoufox, camoufox,
  camoufox_fresh, playwright_stealth)`. **Per-chapter cap = 4 attempts** (5 with
  HTTP-first via `HTTP_FIRST_PRIMARY_LADDER`). camoufox primary (same-page retries +
  one fresh recovery), then ONE escalation to **headful stealth-Chromium**
  (`cf_bypass.create_stealth_browser`/`fetch_with_stealth`, run VISIBLE since
  `self.headless=False`). No `playwright_stealth_fresh`/cloudscraper/http on the
  default browser path.
- **Persistent + reused stealth engine via a run latch.** `_camoufox_exhausted` is
  set the moment a chapter reaches the stealth rung (set in `_fetch_with_retry_ladder`
  when `browser_primary` + a stealth strategy). Once latched, later chapters + sweep
  retries use `STEALTH_LATCHED_LADDER = (playwright_stealth, playwright_stealth)` —
  straight to the ONE persistent stealth-Chromium browser, reused not relaunched.
  This latch is REQUIRED for true reuse: camoufox and stealth-Chromium each run their
  own sync-Playwright and can't coexist on a thread, so replaying camoufox between two
  stealth chapters would force a Chromium teardown/relaunch. Latching off camoufox
  after the first fallback keeps the one stealth browser alive.
- Non-blocking launch preserved: a missing/unlaunchable stealth Chromium is an
  immediate strategy failure (no backoff), chapter recorded failed, run continues.
  Stealth uses the contained `PLAYWRIGHT_BROWSERS_PATH → files/bin/ms-playwright`
  (the launcher already installs chromium via `python -m playwright install chromium`
  — confirmed).
- `test_headful_camoufox.py` grew 12→20 (both engines mocked at cf_bypass +
  sync_playwright seams — no real launch): escalates-to-stealth-once, stealth-headful,
  stealth-created-once-and-reused across fallback chapters, run-latches-after-first-
  fallback, stealth-launch-failure-non-blocking (unit + pipeline), sweep-uses-fallback.
  `test_phase2.py` ladder tests updated (4-/5-rung). `verify` green: 157 passed.
  **Honest:** offline only; the live ch-102 test will show WHICH engine clears it
  (camoufox or the stealth-Chromium fallback) — watch the log.

## Current Focus (prior)
**0.1.3 headful-camoufox-primary for FreeWebNovel (2026-06-29) — complete on branch
`feature/v0.1.3-headful-camoufox`; `verify` green at 153 tests; COMMITTED locally
(commit a2bd1b4). Push was blocked by the session's permission policy — the user
must run `git push -u origin feature/v0.1.3-headful-camoufox` (no merge, no tag, no
release).** Pulled the merged 0.1.2 work into `main` first (verify green, 138
tests), branched, then re-architected the FWN fetch path to match the legacy
scraper.

**Legacy diff (the key deliverable).** This real clone DOES contain
`files/legacy-reference/freewebnovel-webscraper.py` (the 0.1.2 pass ran on a non-git
copy that lacked it). Diffed against the current request layer:
- CONFIRMED: legacy GUI defaulted `playwright_var=True` + `headless_var=False`
  (lines 1594–1595) → VISIBLE from request #1.
- CONFIRMED: `HtmlFetcher.start()` made ONE browser/page; `fetch()` reused
  `self._page` for every chapter (lines 351–497); `reset_browser()` only on a
  CF/timeout retry (lines 1311–1352) with a backoff schedule — no per-chapter engine
  ladder.
- CONFIRMED: with use_playwright=True (default) it never did HTTP-first; no 6-rung
  ladder.
- CONFIRMED: no special header/UA/cookie/warm-up trick — just headful + persistent +
  one-fetch-per-chapter.
- CORRECTION: legacy gated camoufox behind `and self.playwright_headless` (line 379),
  so its DEFAULT VISIBLE engine was headful **stealth-Chromium** (`create_stealth_
  browser`), not camoufox (camoufox was the headless-only path). The fix is
  engine-independent; 0.1.3 uses headful **camoufox** as primary (stronger
  anti-detect; the engine this codebase already warms/reuses).

**What changed.** Defaults flipped (`request_manager.py` `headless=False`;
`catalog.py` 4 FWN rows `use_browser=True`; `app.py` `DEFAULT_HEADLESS=False`,
`DEFAULT_BROWSER_MODE=True`, `DEFAULT_HTTP_FIRST=False`). One persistent VISIBLE
camoufox browser reused across chapters + a once-per-host browser-session warm-up
(`_warm_camoufox_session` navigates the page to the origin so cf_clearance lands in
the browser context — the 0.1.2 warm-up only warmed the HTTP session). Bounded
retries: browser-primary walks `HEADFUL_PRIMARY_LADDER = (camoufox, camoufox,
camoufox_fresh)` with the budget capped to the ladder length (≤1 fresh recovery), no
stealth rungs; the end-of-run sweep is one pass and thus auto-bounded. HTTP-first is
opt-in (`ScrapeJob.http_first` / `RequestManager.try_http_first`, GUI checkbox
default off). WebNovel-dynamic keeps `use_browser=False` plain-HTTP. New
`test_headful_camoufox.py` (12); `test_phase2.py` + `test_phase8_gui.py` updated.
**Honest:** offline tests prove wiring/flow only; live headful-camoufox clearance of
FWN's CURRENT Cloudflare is UNPROVEN — needs a live pass on chapter 102 (visible
window opens, stays open/reused, 102 succeeds).

## Current Focus (prior)
**0.1.2 Cloudflare avoidance + fresh-install fixes (2026-06-29) â€” complete on
branch `feature/v0.1.2-cf-avoidance`; `verify` green at 138 tests; NOT pushed yet
(see push status below).** Work-PC run (Shadow Slave 100â€“110, fresh GitHub zip)
found three problems, all addressed:
1. **Slow + repeated CF challenges (Task 1).** Shifted from *fighting* the
   challenge to *avoiding* it at the HTTP layer. `request_manager._http_get` now
   does a **once-per-host warm-up GET** to the site origin (acquires `cf_clearance`
   into the persistent session before chapter fetches â€” the gap on resume runs
   where the TOC is cached), sets a **host-derived `Referer`** (was a hardcoded
   cross-site `webnovel.com` referer = bot-tell), and chains **`Sec-Fetch-Site`**
   (`none` warm-up â†’ `same-origin` chapters). Persistent session + cookie reuse
   already existed; brotli + garbled self-heal preserved.
   **â€¼ï¸ The legacy `freewebnovel-webscraper.py` is gitignored (`files/legacy-reference/`)
   and was ABSENT from the zip, so it could NOT be diffed â€” this is best-practice,
   not a confirmed port. The user pre-approved proceeding this way.**
2. **Stealth rungs crash on fresh install (Task 2).** The `.bat` fetched camoufox
   but never Chromium; the `.command` installed Chromium but never camoufox â€” each
   missing one engine. Both now install **both**: `.bat` adds
   `python -m playwright install chromium` contained in `files\bin\ms-playwright`
   via `PLAYWRIGHT_BROWSERS_PATH` (sentinel `.venv\playwright.fetched`); `.command`
   adds camoufox fetch (sentinel `.venv/camoufox.fetched`). New
   `webnovel_scraper/browser_env.py` defaults `PLAYWRIGHT_BROWSERS_PATH` to the
   contained path at import (setdefault) so runtime finds Chromium even outside the
   launcher; imported by `request_manager` + `cf_bypass`.
3. **Freeze on launch failure (Task 3).** `_looks_like_browser_launch_failure`
   classifies an engine-missing / "Executable doesn't exist / playwright install"
   error as an **immediate** strategy failure that advances the ladder with **no**
   backoff sleep (kills the live 100-second "retrying in 102.7sâ€¦" hang). Clear
   one-line log points to re-running setup; exhausted chapter recorded failed, run
   continues (test-proven).
- **Task 4 (ladder):** shape unchanged
  (`http â†’ cloudscraper â†’ camoufox â†’ camoufox_fresh â†’ playwright_stealth â†’
  playwright_stealth_fresh`); no strategy removed. Task 1 avoids the challenge;
  browser rungs are the now-launchable, non-blocking safety net.
- New `files/tests/test_cf_avoidance.py` (10). **Honest:** live confirmation that
  avoidance stops the challenges is the user's next full run from HOME-PC; if it
  still challenges, next levers are headful mode / residential proxy.

## Current Focus (prior)
**0.1.1 Cloudflare ladder: playwright-stealth rescue rungs + stronger end-of-run
sweep (2026-06-28) — complete; `verify` green at 128 tests.** A full Shadow Slave
stress-scrape (1–3065) produced the first genuine FWN Cloudflare challenge and
camoufox **failed every attempt** (chapters 102, 174, …) — reversing the standing
"camoufox is sufficient" assumption. Wired the dormant Chromium playwright-stealth
strategy back into the live ladder as the last-resort rungs:
`http → cloudscraper → camoufox → camoufox_fresh → playwright_stealth →
playwright_stealth_fresh` (constants renamed `FETCH_STRATEGY_PLAYWRIGHT_STEALTH
[_FRESH]`, old `…_BROWSER…` kept as aliases; `BROWSER_ESCALATION_LADDER` gained the
same two rungs; `MAX_RETRIES` left at 6 = 7 attempts, enough for all six rungs).
Added one-engine-per-thread teardown (`_teardown_chromium` stops the Chromium driver
before camoufox; `_reset_camoufox` runs before Chromium stealth) so the two
sync-Playwright engines never collide. The Phase-9C end-of-run sweep now re-walks
this full ladder for every CF-skipped (non-permanent, non-extraction) chapter — the
stealth rescue camoufox couldn't give on the main pass — writing rescued chapters in
all three modes and leaving the rest in `RunReport.failed` + the summary. New
`test_stealth_rescue.py` (8) + 2 `test_phase2.py` ladder tests updated. **Honest
status:** offline tests prove only the wiring/flow; whether Chromium stealth clears a
live FWN challenge is UNPROVEN until the user's next live run. The brotli + nesting
fixes are confirmed working live and untouched. NOT committed; plan not deleted.

## Current Focus (prior)
**0.1.1 output-folder nesting fix (2026-06-28) — complete; `verify` green at 120
tests.** A live run of the new output-folder feature wrote a *doubled* path
(`…\Downloads\webscraped_shadow-slave-1\shadow-slave-1`). `resolve_output_dir`
itself was correct; the GUI's read-only field both displayed and held the parent
and `Browse…` wrote the picked folder back into it, so a prior/browsed output dir
became the next run's `parent_dir`, nesting `{slug}-N` one level deeper each run.
Fixed in `app.py`: the chosen parent now lives in a dedicated `self._output_parent`
Path (default `~/Downloads`, set **only** by Browse); the read-only field shows a
live preview of the resolved **target** (`<parent>/{name}-N`) that is never fed
back as a parent; one `_resolve_output_dir` helper feeds both the preview and the
run. `chapter_index.json` stays in the output dir (resume source of truth; moving
it would break same-folder resume — flagged cosmetic, left in place). +3
`resolve_output_dir` tests + 1 GUI preview test. The brotli body-extraction fix is
**confirmed working live** and untouched. NOT committed; plan not deleted.

## Current Focus (prior)
**0.1.1 post-live-pass fixes (2026-06-28) — complete; `verify` green at 116
tests.** A live Shadow Slave FreeWebNovel scrape surfaced two distinct defects,
both fixed offline this session, plus a requested feature:
1. **Brotli body-extraction failure (Critical).** Chapters 3+ failed "Could not
   extract body paragraphs" because the HTTP layer advertised `Accept-Encoding:
   …, br` and `requests` can't decode Brotli without the `brotli` package → the
   page came back as U+FFFD garbage → zero paragraphs. Fix: dropped `br` from the
   header; added a `_looks_garbled` fetch guard (undecodable → retryable, never
   cached); cache reads self-heal a poisoned entry. The adapter selectors were
   never wrong (the decoded current markup extracts 50 paragraphs).
2. **Extraction-failure misclassification.** An empty-extraction outcome was
   calling `pacer.register_block()` (auto-slowdown). Now its own
   `models.EmptyExtractionError`: recorded in `RunReport.failed` +
   `extraction_failed`, never a block, never swept.
3. **User-choosable output folder (feature).** Default folder renamed
   `webscraped_{slug}-N` → `{slug}-N`; `resolve_output_dir` gained `parent_dir` +
   `base_name`; GUI got an **Output folder** Browse… picker + **Folder name**
   field. Logic stays in `resolve_output_dir`.

NOT committed (user force-pushes manually). Implementation-plan drop not deleted.

## Current Focus (Phase 9)
**Phase 9 (live-scrape hardening) implemented — 0.1.0 remains pending the user's
manual live test + tag/force-push.** This session implemented the five-part Phase
9 from the plan §8: **9A** GUI inter-fetch delay control ("Delay between fetches
(seconds)", default 2.0, bound to `ScrapeJob.delay`); **9B** adaptive
auto-slowdown via a pipeline `_Pacer` (raises the effective delay on
blocks/challenges, floor 2.0 / ceiling 30.0, logged, reported); **9C** relentless
per-chapter retry (`MAX_RETRIES` 4→6) plus a **second-pass sweep** over the
non-permanent failed list (rescued chapters tracked; permanent 403/404
short-circuits via `RunReport.permanent_failed`); **9D** the WebNovel camoufox
rescue — fixed the over-eager `is_cloudflare_challenge` that mis-flagged *cleared*
post-redirect pages carrying the ambient `/cdn-cgi/challenge-platform/` beacon
(now content-aware in both `request_manager` and `cf_bypass`), confirmed `g_data`
is a co-equal parse path reachable from a browser rung; **9E** docs. New
`test_phase9.py` (16 cases). Suite is now **101 offline tests**, `verify` green.
The WebNovel rescue is **fixed/proven offline for the over-eager-detection root
cause**; live confirmation that camoufox clears a genuinely-challenged chapter is
for the user's manual pass. NOT committed; implementation-plan drop intentionally
**not deleted**.

---

## Open Issues / Bugs

| # | Severity   | File | Description | Status | Found by |
|---|------------|------|-------------|--------|----------|
| 1 | Minor      | scripts/Universal/webnovel_scraper/adapters/webnovel_dynamic.py | `build_chapter_index` persists/loads its own `chapter_index.json` unconditionally (ignores `use_cache`); redundant with the pipeline TOC persistence; can serve a stale TOC across runs over time. | **Fixed** 2026-06-27 â€” adapter persistence removed; now a stateless TOC builder, pipeline owns TOC persistence | Claude Code |
| 2 | Minor      | scripts/Universal/app.py | Headless checkbox defaults off (visible browser) vs README "Leave Headless on." | **Fixed** 2026-06-27 â€” `DEFAULT_HEADLESS=True`, checkbox wired to it | Claude Code |
| 3 | Suggestion | scripts/Universal/webnovel_scraper/pipeline.py | Degraded-chapter SEPARATE filename is `Chapter N..pdf` (double dot). | Flagged â€” awaiting user | Claude Code |
| 4 | Suggestion | scripts/Universal/app.py | `spec.use_browser` mutates the shared catalog `SiteSpec` (reset each run, harmless). | Flagged â€” awaiting user | Claude Code |
| 5 | Suggestion | scripts/Universal/webnovel_scraper/pdf_builder.py | `PdfReader` in `remove_single_heading_pages` not closed (handle leak). | Flagged â€” awaiting user | Claude Code |
| 6 | Suggestion | scripts/Universal/webnovel_scraper/pipeline.py | A PDF that fails mid-write would be treated as complete by resume (silent skip on re-run). | Flagged â€” awaiting user | Claude Code |
| 7 | Critical | scripts/Universal/webnovel_scraper/request_manager.py; scripts/Universal/webnovel_scraper/pipeline.py | FreeWebNovel first-time uncached chapter fetches can hit Cloudflare mid-scrape; prior path risked chapter failures without enough retry/escalation. | **Mitigated/closed for fatal behavior** 2026-06-27 — retry ladder + backoff + strategy escalation added; exhausted chapters are recorded/skipped and the run continues. Live bypass success still needs manual FWN validation. | Codex |
| 8 | Critical | scripts/Universal/webnovel_scraper/request_manager.py | Live FWN chapters 3+ failed body extraction — `Accept-Encoding: …, br` returned brotli that `requests` could not decode → U+FFFD garbage → zero paragraphs. | **Fixed** 2026-06-28 — dropped `br`; `_looks_garbled` guard (undecodable → retryable, not cached); cache reads self-heal poisoned entries. | Claude Code |
| 9 | Critical | scripts/Universal/webnovel_scraper/pipeline.py | Empty-extraction outcome misclassified as a Cloudflare block, driving `_Pacer` auto-slowdown to 30 s (every ch. 3+ in the live incident, throttling the run to a near halt + needless camoufox escalation). | **Fixed** 2026-06-28 — own `EmptyExtractionError` class; recorded in `failed`/`extraction_failed`, no block, excluded from sweep. | Claude Code |
| 10 | Critical | scripts/Universal/app.py | 0.1.1 output-folder feature nested a `{slug}-N` folder inside the prior run's folder (`…/webscraped_shadow-slave-1/shadow-slave-1`): the GUI's read-only field both showed and held the parent, and Browse wrote the picked folder back into it, so a prior output dir became the next run's `parent_dir`. | **Fixed** 2026-06-28 — parent kept in a dedicated `_output_parent` Path (set only by Browse); read-only field shows the resolved target preview, never fed back as a parent; one `_resolve_output_dir` helper. | Claude Code |
| 11 | Critical | scripts/Universal/webnovel_scraper/request_manager.py; scripts/Universal/webnovel_scraper/cf_bypass.py | Live (1–3065) FWN Cloudflare challenge: camoufox cleared NONE of it — the whole `http→cloudscraper→camoufox→camoufox_fresh` ladder failed on chapters 102, 174, … So the camoufox-only ladder is live-proven insufficient; those chapters were skipped (correct resilience) but left gaps. | **Wiring fixed, bypass UNPROVEN** 2026-06-28 — Chromium playwright-stealth re-wired as last-resort rungs after camoufox; one-engine-per-thread teardown added; end-of-run sweep re-walks the full ladder (incl. stealth) for every CF-skipped chapter. Whether stealth actually clears live FWN CF needs a fresh live pass. | Claude Code |

**Open Critical that still needs live validation: #11** — camoufox is live-proven
insufficient against a real FWN challenge; the Chromium playwright-stealth rescue
rungs are now wired in (and the end-of-run sweep re-walks the full ladder), but
whether stealth *actually* clears a live FWN challenge is unconfirmed and needs the
user's next live pass over the known-bad chapters (102, 174, …). If stealth also
fails, escalate to headful mode / a residential proxy / `nodriver`. Item **#7 is
mitigated/closed for scraper resilience** (fatal halt/mass-failure behavior fixed;
**but** #11 now supersedes its "live bypass success still needs validation" caveat
with the concrete finding that camoufox alone is not enough). Items **#8, #9, #10
are fixed** (this 0.1.1 cycle); **#1, #2 fixed** earlier; **#3–#6 remain deferred**
(Suggestion-level). Full detail for the older Phase 8 findings is in
`files/test-logs/v0.1.0_pre-release.md`.

Notes:
- `scripts/Universal/app.py` is now the real single-window GUI (Phase 6).
- `scripts/Universal/webnovel_scraper/pipeline.py` is implemented (Phase 5).
- `Setup_and_Run-Web-Novel-Scraper.bat` is finalized (Phase 7): launches the GUI via the
  venv's `pythonw.exe` (no console behind the window); idempotent deps via a
  `.venv\requirements.lock` + `fc /b` skip. The GUI itself does no console suppression â€”
  that's the launcher's job.
- Root `README.md` is rewritten (Phase 7) as a minimal user-facing guide. The old legacy
  editor scripts it used to describe live under `files/legacy-reference/`.
- For Phase 8: the launcher's clean-double-click-on-a-no-Python-machine item from the Â§7
  portability checklist still needs to be exercised on a real no-Python machine (or its
  manual walkthrough documented) in the final release log.
- GUI design notes for the next agent: the browser/headless checkboxes are only wired for
  browser-capable adapters (`freewebnovel`); `webnovel_dynamic` is HTTP-only so they are
  inert. Timeout is applied by setting `request_manager.FETCH_TIMEOUT`, and browser mode by
  setting `spec.use_browser` on the resolved catalog row â€” both deliberately avoid editing
  the tested Phase 2/5 modules. End-chapter blank = `ALL_CHAPTERS` (10**9), clamped by the
  pipeline to the real TOC.

---

## Work Log (newest first)

- 2026-06-29 - Claude Code (HOME-PC): **0.1.3 FWN ladder refinement — bounded headful
  stealth-Chromium fallback after camoufox** (same `feature/v0.1.3-headful-camoufox`
  branch, no branch switch). Reason: the legacy diff showed the old scraper's proven
  VISIBLE engine was stealth-Chromium, which my prior 0.1.3 pass had removed from the
  FWN chain. Added it back as a bounded fallback: `HEADFUL_PRIMARY_LADDER` now
  `(camoufox, camoufox, camoufox_fresh, playwright_stealth)` (cap 4/chapter; 5 with
  HTTP-first), stealth runs headful, persistent + reused via the `_camoufox_exhausted`
  run latch (`STEALTH_LATCHED_LADDER` for latched chapters/sweep so the one stealth
  browser is never relaunched). Latch set in `_fetch_with_retry_ladder` when
  `browser_primary` reaches a stealth rung; `fetch` selects the latched ladder.
  Non-blocking launch + contained `files/bin/ms-playwright` Chromium preserved
  (launcher installs chromium — confirmed). `test_headful_camoufox.py` 12→20 (added a
  `_FakeBrowsers` two-engine mock incl. `sync_playwright`, so no real browser ever
  launches); `test_phase2.py` ladder tests updated to the 4-/5-rung ladders. `verify`
  green: **157 passed**. Committed + (attempted) push on the same branch; NO merge/
  tag/release. **Honest:** offline only; live ch-102 will show which engine clears.
- 2026-06-29 - Claude Code (HOME-PC): **0.1.3 headful-camoufox-primary for
  FreeWebNovel** on new branch `feature/v0.1.3-headful-camoufox`. Pulled merged
  0.1.2 into `main` (verify green, 138 tests), branched. **Independently diffed the
  legacy `freewebnovel-webscraper.py` (PRESENT in this real git clone, unlike the
  0.1.2 non-git copy):** confirmed legacy defaulted to a VISIBLE browser from
  request #1, ONE persistent browser/page reused per chapter, no HTTP-first, no
  escalation ladder; corrected one claim — its default-visible engine was headful
  stealth-Chromium (camoufox was gated behind `and self.playwright_headless`, i.e.
  headless-only). Built the architecture fix engine-independently with headful
  camoufox: flipped `RequestManager.headless` default to False, set the 4 FWN catalog
  rows `use_browser=True`, `app.DEFAULT_HEADLESS=False` + browser-mode default ON;
  added `HEADFUL_PRIMARY_LADDER`/`HTTP_FIRST_PRIMARY_LADDER` and capped the
  browser-primary retry budget to the ladder length (≤1 fresh recovery, no stealth
  storm); added `_warm_camoufox_session` (one-time browser-session origin warm-up,
  cleared on browser recreation); added the opt-in HTTP-first toggle
  (`ScrapeJob.http_first` / `RequestManager.try_http_first` / GUI checkbox).
  WebNovel-dynamic untouched (plain HTTP). New `test_headful_camoufox.py` (12);
  updated `test_phase2.py` + `test_phase8_gui.py` (old-ladder / headless-default
  assumptions). `verify` green: **153 passed**. Committed locally (a2bd1b4); the
  branch push was blocked by the session permission policy, so the user must push it
  (`git push -u origin feature/v0.1.3-headful-camoufox`); did NOT merge, tag, or
  release. **Honest:** wiring/flow proven offline; live headful-camoufox
  clearance of FWN's current Cloudflare unproven — minimal live test is chapter 102
  with Headless off / HTTP-first off (visible window opens, stays open/reused, 102
  succeeds). The prior Codex/0.1.2 investigation confirmed the current-code side
  (HTTP-first + headless + 6-rung ladder) of this root cause.
- 2026-06-29 - Claude Code (CSPW-PC, work PC): **0.1.2 Cloudflare avoidance +
  fresh-install fixes** on new branch `feature/v0.1.2-cf-avoidance`. **Task 1:**
  could NOT diff the legacy scraper (`files/legacy-reference/` is gitignored and
  was absent from the fresh zip; searched the whole Desktop tree â€” only the current
  adapter exists). User pre-approved proceeding with the brief's named "most
  probable win." Implemented in `request_manager.py`: `_http_get` (warm-up GET to
  host origin once per session, host-derived `Referer`, `Sec-Fetch-Site`
  none→same-origin chaining), `_warmed_hosts_for`, `_apply_request_headers`;
  removed the hardcoded `Referer: webnovel.com` from `BROWSER_HEADERS`. Persistent
  session + cookie reuse were already present (one `requests.Session` per manager,
  reused all run). Kept `_get_text(session, url)` 2-arg signature so the
  `test_phase9` monkeypatches still bind. **Task 2:** new `browser_env.py`
  (setdefault `PLAYWRIGHT_BROWSERS_PATH` → `files/bin/ms-playwright`), imported by
  `request_manager` + `cf_bypass`; `.bat` adds gated `python -m playwright install
  chromium`; `.command` adds the gated camoufox fetch it was missing. **Task 3:**
  `_looks_like_browser_launch_failure` + a launch-failure branch in
  `_fetch_with_retry_ladder` that `continue`s to the next rung with no backoff
  sleep. **Task 4:** ladder shape unchanged, documented. New
  `files/tests/test_cf_avoidance.py` (10). `verify` green: **138 passed**. Branch
  created + committed; push status recorded in the Session Sync Log. Implementation
  plan (this drop) is the in-prompt task, not a file to delete.
- 2026-06-28 - Claude Code: **0.1.1 release — committed, tagged, pushed.** Release
  housekeeping session (no feature/code changes). Ran `verify` (green: **128
  passed**), audited the working tree against the three 0.1.1 Session Sync Log
  entries (all files present, no discrepancy; pre-0.1.1 fixtures already tracked in
  the v0.1.0 orphan commit `0bb4fe5`), confirmed the docs already reflect all 0.1.1
  work (brotli fix, output-folder nesting fix, playwright-stealth rescue rungs,
  strengthened end-of-run sweep) and that README still matches current behavior.
  Staged everything with `git add -A` and committed a **new** commit on top of
  `0bb4fe5` on `main` (did NOT amend/rebase the orphan commit), tagged `v0.1.1`
  (annotated), pushed `main` + the tag to `origin`, and created the GitHub release
  `v0.1.1`. Open Critical **#11** (live FWN Cloudflare bypass — whether Chromium
  playwright-stealth actually clears a real FWN challenge) remains UNPROVEN and is
  carried into 0.1.1 as a known issue; backlog item 2 (rename the "Use Playwright
  browser mode" GUI label) still open.
- 2026-06-28 - Claude Code: **0.1.1 Cloudflare ladder — playwright-stealth rescue
  rungs + stronger end-of-run sweep.** Live finding from the 1–3065 Shadow Slave
  stress-scrape: the first genuine FWN Cloudflare challenge, and camoufox FAILED it
  on every attempt (chapters 102, 174, …) — `http→cloudscraper→camoufox→
  camoufox_fresh (×4)` all returned "challenge still present." GOAL 1: wired the
  dormant Chromium playwright-stealth strategy back into the live ladder as the
  last-resort rungs → `http → cloudscraper → camoufox → camoufox_fresh →
  playwright_stealth → playwright_stealth_fresh`. Renamed `FETCH_STRATEGY_BROWSER
  [_FRESH]` → `FETCH_STRATEGY_PLAYWRIGHT_STEALTH[_FRESH]` (old names aliased),
  extended `DEFAULT_ESCALATION_LADDER` + `BROWSER_ESCALATION_LADDER`, updated the
  dispatch + ladder comments + cf_bypass docstring. `MAX_RETRIES` left at 6 (=7
  attempts) — already enough to walk all six rungs once (no change needed). Added
  one-engine-per-thread teardown: `_teardown_chromium()` (stops the Chromium
  sync-Playwright driver) runs before camoufox starts, and `_reset_camoufox()` runs
  before Chromium stealth starts — they can't share a thread ("Sync API inside the
  asyncio loop"). Confirmed `is_cloudflare_challenge` already clears a stealth-cleared
  page content-aware (FWN `class="txt"` body is a structural marker) — no detection
  change. GOAL 2: the existing Phase-9C sweep already covers all non-permanent,
  non-extraction failures in all three modes and re-calls `rm.fetch` (which re-walks
  the FULL ladder, since failed chapters are never cached) — so extending the ladder
  in GOAL 1 automatically gives the sweep the stealth rescue the main pass lacked; no
  pipeline change needed, verified by tests. New `files/tests/test_stealth_rescue.py`
  (8: ladder order, camoufox_fresh→stealth advance, both teardown directions,
  end-to-end sweep-reaches-stealth-and-rescues across SEPARATE/CHUNKED/SINGLE,
  fails-every-rung-stays-failed+in-summary); updated 2 `test_phase2.py` ladder tests.
  Marked the Briefing "drop playwright-stealth" backlog item CANCELLED. `verify`
  green: **128 passed**. **Honest:** offline tests prove wiring/flow only; live FWN
  stealth clearance unproven until the next live run. NOT committed; plan not deleted.
- 2026-06-28 - Claude Code: **0.1.1 output-folder nesting fix.** A live run wrote
  `…\Downloads\webscraped_shadow-slave-1\shadow-slave-1` (doubled folder).
  Diagnosed: `pipeline.resolve_output_dir` was correct (default →
  `~/Downloads/{slug}-N`, `-N` scan keys on `{slug}-N` and ignores the old
  `webscraped_` prefix); the bug was in `app.py` — the read-only "Output folder"
  field both displayed and held the *parent*, and `_on_browse` wrote the picked
  folder into that same var, which `_on_start` then passed as `parent_dir`, so a
  prior/browsed output dir nested a fresh `{slug}-N` inside it. Fix: added
  `self._output_parent` (Path, default `~/Downloads`, set only by Browse); the
  read-only field now shows a live **target** preview via `_refresh_output_preview`
  (never fed back as a parent); both the preview and the run resolve through one
  `_resolve_output_dir` helper using the stored parent Path. **Secondary item
  decision:** left `chapter_index.json` in the output dir — it is the documented
  output-dir-scoped resume source of truth and the "re-run into the same folder to
  resume" contract + tests depend on it; relocating to `files/cache/{slug}/` would
  risk breaking resume, so it stays (flagged cosmetic). Tests: +3
  `resolve_output_dir` cases (`test_phase5_pipeline.py`: default single-level,
  increment-as-sibling, old-`webscraped_`-ignored) + 1 GUI case
  (`test_phase8_gui.py`: target preview one level under Downloads, parent passed is
  the Downloads base not the target). Also fixed two handoff doc-sync nits flagged
  by Codex (issue #9 severity Major→Critical; added the missing Deleted line for
  `scrape_noble_queen_issue_summary.md` to the prior 2026-06-28 sync entry).
  `verify` green: **120 passed**. The brotli body-extraction fix is confirmed
  working live and was not touched. NOT committed; plan not deleted.
- 2026-06-28 - Claude Code: **0.1.1 post-live-pass fixes** (two defects + a
  feature). Investigated the live FWN "chapters 3+ FAILED: Could not extract body
  paragraphs" report using the on-disk cache at `files/cache/shadow-slave/`:
  computed the SHA-256 cache keys, found chapters 1–2 were clean HTML (~76 KB) and
  3+ were undecoded binary garbage (~15 KB, 43% U+FFFD). Re-fetched chapter 3
  live: `Accept-Encoding: gzip, deflate, br` → `Content-Encoding: br`, 6516
  replacement chars, no `<html>`; `gzip, deflate` → clean 75 KB HTML.
  **Root causes:** (1a) brotli mis-decode (no `brotli` pkg in `requests`); (1b)
  `pipeline._fetch_one` calling `pacer.register_block()` on the resulting
  empty-extraction. **Fixes:** dropped `br` from `BROWSER_HEADERS`; added
  `request_manager._looks_garbled` (>2% U+FFFD → `_RetryableFetch`, used on fetch
  AND cache-read so a poisoned cache self-heals); new `models.EmptyExtractionError`
  raised by both adapters and classified in `pipeline._fetch_one` as an
  extraction failure (recorded in `failed` + new `RunReport.extraction_failed`,
  no block, excluded from `_sweepable` and the chunked/single sweep collectors).
  **Feature (Task 2):** renamed default output dir `webscraped_{slug}-N` →
  `{slug}-N`; `resolve_output_dir` gained `parent_dir`/`base_name`; `app.py` got
  an Output-folder Browse… picker + Folder-name field (logic stays in
  `resolve_output_dir`). Added fixtures `fwn_chapter_current_ok.html` +
  `fwn_chapter_brotli_garbage.html`; new `test_brotli_extraction_fix.py` (7);
  updated `test_phase5_pipeline.py` + `test_phase8_gui.py`. `verify` green: **116
  passed**. NOT committed; plan not deleted. **Honest risk:** the brotli fix is
  generic to the FWN HTTP path so it applies to all FWN novels (not just Shadow
  Slave); the only residual unknown is whether FWN ever serves an encoding other
  than gzip/deflate/br (the garble guard would catch that case and escalate).
- 2026-06-27 - Claude Code: implemented **Phase 9 (live-scrape hardening)**, all
  five parts. **9A:** relabelled the GUI delay field to "Delay between fetches
  (seconds)", default 1.2→2.0, anti-detection hint; binds to `ScrapeJob.delay`
  (already plumbed). **9B:** new `pipeline._Pacer` (multiplier 1.5, floor 2.0s,
  ceiling 30.0s) raises the effective inter-fetch delay on block-classified
  chapter failures; `run_scrape` gained an injectable `sleep_fn`; `RunReport` gained
  `auto_slowdowns` + `effective_delay`. **9C:** `MAX_RETRIES`/`ScrapeJob.max_retries`
  4→6; second-pass sweep over non-permanent failures (SEPARATE post-loop;
  CHUNKED/SINGLE pre-write via `_fetch_block_with_sweep`); `RunReport.rescued` +
  `permanent_failed`; `_is_permanent_failure` classifies 403/404 to short-circuit
  the sweep. **9D:** rewrote `is_cloudflare_challenge` in `request_manager.py` (and
  aligned `cf_bypass.py`) to be content-aware — strong interstitial markers flag
  immediately, ambient `/cdn-cgi/challenge-platform/` + `cf-mitigated` beacon
  markers flag only when no real payload present; this is the fix for "challenge
  still present after camoufox fetch" on cleared post-redirect WebNovel pages. Added
  `wnd_g_data_post_redirect_chapter.html` + `wnd_cloudflare_challenge.html` fixtures.
  **9E:** README + Briefing note browser-off still auto-escalates. Added
  `test_phase9.py` (16 cases); updated `test_phase5_pipeline.py` exhausted-chapter
  test for the sweep. `verify` green: **101 passed**. Did NOT commit; plan not
  deleted (user force-pushes 0.1.0 and removes the plan after the live pass).
- 2026-06-27 - Codex: mitigated the live-discovered FreeWebNovel Cloudflare
  Critical before any long stress run. `RequestManager.fetch()` now owns a
  configurable retry ladder (`max_retries=4`, `retry_base_delay=5.0`) with
  exponential backoff + jitter, permanent 403/404 short-circuiting, retryable
  5xx/network/CF handling, and strategy escalation: HTTP -> cloudscraper ->
  Playwright stealth -> fresh Playwright context/new UA. `ScrapeJob`, the
  pipeline-created manager, and the GUI-owned manager now thread those defaults
  without adding GUI controls. Browser navigation treats 403/404 as permanent.
  Pipeline behavior was regression-tested so an exhausted chapter is logged,
  added to `RunReport.failed`, listed in the summary, and the next chapter still
  runs. Added/updated offline tests in `test_phase2.py` and
  `test_phase5_pipeline.py`; `verify` green: **82 passed**. Status: fatal
  scraper behavior fixed; live Cloudflare bypass reliability remains to be
  validated by the user's manual FWN pass.
- 2026-06-27 - Phase 8 follow-up: fixed flagged items #1 and #2 (user-approved);
  #3-6 left deferred. **#1 (webnovel_dynamic stale TOC):** removed the adapter's
  redundant slug-scoped `chapter_index.json` persistence entirely (dropped
  `INDEX_FILENAME`, the `cache_dir` param, `_index_dir`, `_load_index` /
  `_save_index` / `_meta_to_dict` / `_dict_to_meta`, and `force_refresh`) â€” the
  adapter is now a **stateless** TOC builder that always re-parses the live
  `__NEXT_DATA__`. The pipeline's output-dir-scoped `chapter_index.json` is the
  single source of truth for resume, so a fresh run into a NEW output dir always
  sees the current chapter count (no stale reuse, independent of the cache
  toggle). Chose removal over threading `use_cache` because the default
  `use_cache=True` would otherwise still reuse a stale slug-scoped index. **#2
  (headless default):** added module constant `DEFAULT_HEADLESS = True` (matches
  the README) and wired the checkbox to it. **Tests:** replaced the obsolete WND
  persistence test with two regressions in `test_phase4_wnd.py`
  (`...no_stale_cache_reflects_grown_toc` â€” a grown TOC re-parses to the new
  count, proving no stale reuse; `...keeps_no_own_index_cache` â€” no adapter-owned
  index file is written), updated the three `default_html` WND constructions to
  drop the removed `cache_dir` kwarg, and added `test_phase8_gui.py`
  (`DEFAULT_HEADLESS` constant + a display-guarded end-to-end check that the
  checkbox initialises on). `verify` green: **74 passed** (was 71; -1 obsolete
  test, +4 new). No Briefing/CHANGELOG change (minor fixes, per the task); not
  committed. - Claude Code
- 2026-06-27 - Phase 8 complete (bug hunt + release pass): reviewed every module
  systematically (deprecated libs, error handling, hardcoded paths, fresh-machine
  breakage, edge cases, GUI thread-safety, resource leaks, imports, debug
  artifacts, platform-neutrality). **No Critical defects** â†’ no code changed,
  suite unchanged at 71 tests, `verify` green. Verified imports of all 15
  package modules under the venv and probed edge cases (all-heading-only PDF,
  degraded filename + resume match, WND cache signature). Logged six
  Minor/Suggestion items for the user (see Open Issues) and left them unfixed.
  Wrote `files/test-logs/v0.1.0_pre-release.md` (final release log; live scrapes
  structured + marked skipped for the user's manual pass). Updated Briefing +
  CHANGELOG (Phase 8) + this handoff. Did NOT commit and did NOT delete the
  implementation-plan drop (user deletes it after their live test). - Claude Code
- 2026-06-27 - Phase 7 complete: finalized `Setup_and_Run-Web-Novel-Scraper.bat`
  against plan Â§7 + the portability checklist (already self-locating via `%~dp0`,
  user-scope-only Python install with python.org fallback, self-healing in-repo
  `.venv`, contained Playwright Chromium, SmartScreen note, pause-on-failure).
  Two fixes: (1) launch the GUI via the venv's `pythonw.exe` (was `python`) so no
  console shows behind the tkinter window, falling back to `python` if pythonw is
  absent; (2) idempotent deps â€” write `.venv\requirements.lock` after a good
  install and `fc /b`-compare on later runs to skip pip unless requirements
  changed/venv rebuilt. Left `.command` as-is (full macOS launcher, untested-on-Mac
  note). Rewrote root `README.md` into a minimal user-facing guide (what it does,
  supported novels/sites table w/ 0.1.0 status, double-click quick start,
  SmartScreen note, Downloads output path, browser-mode note, Windows-only). NOT
  committed (left in working tree per user). `verify` green: 71 passed. - Claude Code
- 2026-06-27 - Phase 6 complete: implemented `app.py` (`ScraperApp(tk.Tk)`), the
  single-window GUI, replacing the Phase-0 stub. Catalog-driven novel/site selection
  (disabled rows greyed + non-selectable via a `Menubutton`/`Menu`); start/end (blank =
  all), delay (1.2 s default), timeout, output-mode radio (Separate / Chunked + per-PDF
  count / Single), browser + headless + cache toggles (browser/headless inert for the
  HTTP-only `webnovel_dynamic`, active for `freewebnovel`), determinate progress bar,
  scrolled log, Start/Stop. Scrape runs on a daemon thread; all UI updates via
  `self.after(0, ...)`; Stop sets a `threading.Event` the pipeline honours; the worker
  closes the GUI-owned `RequestManager` in `finally`. Timeout wired via
  `request_manager.FETCH_TIMEOUT`, browser via `spec.use_browser` â€” no tested module
  touched. No headful test added (per plan Â§8); smoke-checked by instantiating the window,
  driving the selection/validation/running-state handlers, and tearing it down. `verify`
  green: 71 passed (suite unchanged). - Claude Code
- 2026-06-27 - Phase 5 complete: implemented `pipeline.py` (`run_scrape`) â€” TOC-first
  orchestration with output-dir-scoped `chapter_index.json` persistence, range clamp,
  resume (skip existing PDFs, no re-fetch), single-failed-chapter tolerance, per-fetch
  delay + `cancel_event`, and the three output modes (separate / chunked / single) through
  `pdf_builder.create_pdf`. Pipeline-layer disabled-adapter refusal (`AdapterDisabledError`
  before the adapter is built/called) + `resolve_output_dir` + `RunReport`. New
  `test_phase5_pipeline.py` (11 offline cases via a fake adapter). Also did the pre-task
  `.gitignore` cleanup (removed the duplicate inline-comment runtime-dir lines). `verify`
  green: 71 passed. - Claude Code
- 2026-06-26 - Post-restructure validation cleanup only (no Phase 5 work): fixed effective
  `.gitignore` rules for generated `files/` folders, updated stale Briefing fixture paths,
  corrected Claude settings to the migrated `scripts/` / `files/tests/` / `files/test-files/`
  layout, removed root `.pytest_cache/`, and re-ran `verify` green. - Codex
- 2026-06-25 - Structural refactor to AI-WORKSPACE cross-platform layout (no feature
  work, no git). Renamed `Scripts/` to `scripts/`; moved the `webnovel_scraper/` package and
  `app.py` under `scripts/Universal/`; kept `requirements.txt` + `verify.py` directly in
  `scripts/`; scaffolded `scripts/Windows/` + `scripts/MacOS/` (`.gitkeep`). Moved the pytest
  suite to `files/tests/` (+ new `conftest.py` putting `scripts/Universal/` on `sys.path`);
  moved fixtures `files/fixtures/` to `files/test-files/`; added `files/test-logs/`. Updated
  `request_manager.py` repo-root depth (`parents[2]` to `parents[3]`), the tests' fixture path
  (`files/fixtures` to `files/test-files`), and `verify.py` (`TESTS_DIR` to `files/tests/`).
  Rebuilt both launchers from the new templates as `Setup_and_Run-Web-Novel-Scraper.{bat,command}`
  (wired to `.venv` in root, `scripts/requirements.txt`, `scripts/Universal/app.py`, +
  contained Playwright Chromium). Reconciled `.gitignore`, the plan section 2/deletion-test,
  and the Briefing architecture section. Legacy reference scripts + lexicons consolidated under
  `files/legacy-reference/`. `verify` green: 60 passed. - Claude Code
- 2026-06-24 - Phase 4 complete: `webnovel_dynamic` adapter (`__NEXT_DATA__` TOC to
  ordered `ChapterMeta`, persisted chapter index, JSON-path body + DOM fallback, junk
  filtering). `wnd_*` fixtures + `test_phase4_wnd.py` (9 cases). Suite: 60 tests. - Claude Code
- 2026-06-24 - Phase 3 complete: `freewebnovel` adapter generalized over all 4 FWN
  novels; Approach-B generated TOC URLs (fixes the legacy silent-skip bug); title/body
  extraction with invisible-char stripping. Regression + Phase-3 tests; pre-phase corrections
  (verify exit-5 = FAIL; name reconciliation). Suite: 51 tests. - Claude Code
- 2026-06-24 - Phase 2 complete: `request_manager` (HTTP + cloudscraper CF fallback,
  Playwright path, shared cache, retries) and `pdf_builder` (the one ReportLab layout, pypdf
  post-processing). Offline Phase-2 tests. - Claude Code
- 2026-06-24 - Phase 1 complete: `models`, `catalog` (8 rows), `adapters/base`, registry
  (disabled-adapter guard), enabled skeletons + 3 disabled stubs. 15 Phase-1 tests. - Claude Code
- 2026-06-24 - Phase 0 complete: scaffold, `.gitignore`, pinned `requirements.txt`,
  `verify.py` gate, package skeleton, `cf_bypass.py` ported, de-templated docs. - Claude Code

---

## Session Sync Log (newest first)

### 2026-06-30 - HOME-PC - 0.2.0 fully implemented (Phases 0–5); NOT COMMITTED; Unreleased
0.2.0 fast-primary + single-lane hard-chapter rescue is implemented across all five
phases and `verify` is green at **233 passed**. **NOT committed, NOT pushed, NOT tagged;
0.2.0 stays Unreleased/undated** until the user's manual live pass (§7) signs off. No git
run this session (`git` is not installed on this machine and this working copy is not a
git repo — the "preserve local changes / no reset" invariant is trivially satisfied).

- **Phase 5 (this entry) — docs + version only.** Changed `md-instructions/CHANGELOG.md`
  (new `## [0.2.0] — Unreleased`), `md-instructions/Briefing.md` (architecture + Current
  Version + What Has Been Built + Known Issues + Next Steps), `README.md` (one Headless
  override note), `md-instructions/handoff.md` (Current Focus + this entry); added
  `files/tests/test_release_metadata.py` (4 tests). `verify.py` unchanged (its `## [X.Y.Z]`
  docs pattern already accepts the undated Unreleased heading).
- **Suite:** 233 passed (Phases 0–4 baseline 229 collected + 4 release-metadata).

**Consolidated code-vs-plan divergences across all 5 phases (and how each was handled):**
1. **Git unavailable (Phase 0).** No `git`, no `.git`. The plan's Phase-0 git audit
   couldn't run; the no-mutate-local-changes invariant is trivially met. Reported.
2. **Host-limiter wait can't use a real-clock `cancel_event.wait` under a fake clock
   (Phase 1).** It waits via the single injected `sleep`, sliced ≤0.25s with cancel
   re-checks — deterministic, still promptly cancelable. Adapted, not weakened.
3. **403 reclassified (Phase 1).** Body-first classification routes 403/CF-503 to
   `ChallengeFetchError`; `PERMANENT_STATUSES` is now `(404, 410)`. Old `_RetryableFetch`/
   `_PermanentStatus` kept defined (back-compat) but no longer raised. Deliberate.
4. **Limiter built but not wired into the live pipeline until Phase 3 (Phase 1 scope).**
   `host_limiter=None` default kept every existing path byte-for-byte unchanged.
5. **Rescue per-attempt budget vs nav+CF (Phase 2).** A single REAL attempt can approach
   2× the budget (nav AND CF wait); the deadline check bounds the LADDER, but the real
   per-chapter ceiling is ~180s + one attempt's overshoot, and an in-flight `page.goto`
   may run to its nav timeout. The deterministic suite proves the LOGICAL ≤180 bound.
   Documented in `_default_fetch`, not weakened.
6. **Single-engine rescue fetch reaches manager internals (Phase 2).** The default rescue
   fetch drives one concrete engine via `manager._fetch_uncached_strategy` + parses via
   `adapter._extract_chapter` so the worker owns escalation precisely; rescued content is
   intentionally not cached. Same-package use, by design.
7. **GUI-passed `rm` ignored on the rescue path (Phase 3, transitional) — now CLOSED in
   Phase 4.** The GUI no longer builds/passes a manager at all; the pipeline owns/replaces
   it via the factory seam. Divergence retired, not weakened.
8. **Rescue pool initial mode latched at run start (Phase 3).** If the breaker later
   switches the PRIMARY to visible, an already-queued rescue job still begins from the
   headless rung but only ever escalates, so it reaches headful/chromium regardless; after
   a trip the conductor first retries hard chapters SYNCHRONOUSLY on the visible primary.
   The "rescue never starts weaker than the primary" invariant holds at construction. Noted.
9. **Final drain uses thread-coordination polling, not a logical fake-clock wait (Phase 3).**
   `pool.join(0.1)` loop while draining; the worker makes real progress so it can't hang a
   fake-clock test (the cancel test proves it). The only LOGICAL waits anywhere are the
   limiter's and the rescue worker's `_cancelable_sleep`, both off the single injected
   `sleep`/`monotonic`. No new real-clock logical wait introduced.
10. **Legacy-path pipeline edit required to drop the `SiteSpec.use_browser` mutation
    safely (Phase 4).** Removing the GUI mutation exposed that the adapter-less legacy path
    read `use_browser` off the catalog row (FWN = True). Threaded `runtime_site_spec` + the
    job's timeout/headless into the legacy branch so behaviour is job-driven and preserved;
    the sweep logic and the injected-adapter branch are untouched. Minimal, documented — not
    scope creep. NO invariant was impractical; none was weakened; nothing forced a STOP.

**Open question for the live pass (NOT guessed here):** whether a HEADLESS primary clears
FreeWebNovel's current Cloudflare at all — and therefore how often the breaker must fall
back to a visible browser, or hard chapters to the visible rescue lane — is unproven
offline. Pass A (headful) is the detector baseline (ch-102 should clear on the first
visible camoufox attempt, NOT rescued); Pass B (headless) exercises the breaker + rescue
architecture. Both passes run cache-OFF + fresh folder.

**WatchGuard EPDR note (IT security behaviour, NOT a code bug):** on this machine the
endpoint protection may block the first run of a freshly-written executable/script and
then allow it on retry — first-run-block-then-allow is expected IT behaviour, not a defect
in the scraper.

**Cancellation caveat (restated):** Stop / window-close is prompt *between* polls and
attempts, not necessarily mid-navigation; an in-flight `page.goto` may run to its own
navigation timeout before the worker thread exits (the non-daemon worker + poll-until-exit
close waits for that exit before destroying the window).

### 2026-06-29 - HOME-PC - 0.1.3 ladder refinement (stealth-Chromium fallback)
Bounded headful stealth-Chromium fallback after camoufox on the FWN path, on
`feature/v0.1.3-headful-camoufox` (same branch, on top of the prior 0.1.3 commit).

- Changed: `scripts/Universal/webnovel_scraper/request_manager.py`
  (`HEADFUL_PRIMARY_LADDER` + `HTTP_FIRST_PRIMARY_LADDER` gain a trailing
  `playwright_stealth` rung; new `STEALTH_LATCHED_LADDER` + `_STEALTH_STRATEGIES`;
  `_camoufox_exhausted` latch in `__init__`; `fetch` selects the latched ladder;
  `_fetch_with_retry_ladder` gains `browser_primary` and sets the latch on a stealth
  rung).
- Changed: `files/tests/test_headful_camoufox.py` (12→20; new `_FakeBrowsers`
  two-engine mock incl. `sync_playwright`; updated bounded-recovery tests; new
  stealth-fallback / reuse / headful / latch / launch-failure / sweep tests).
- Changed: `files/tests/test_phase2.py` (`test_fetch_use_browser_is_bounded_two_engine_headful`,
  HTTP-first-opt-in ladder now 5 rungs, `fake_ladder` accepts `browser_primary`).
- Changed: `README.md`, `md-instructions/CHANGELOG.md` (0.1.3 two-engine ladder),
  `md-instructions/Briefing.md` (FWN ladder camoufox→stealth, count 153→157),
  `md-instructions/handoff.md` (this entry + Current Focus + Work Log).
- Note: `verify` green (**157 passed**). Committed on the branch; push attempted —
  if blocked, run `git push -u origin feature/v0.1.3-headful-camoufox`. NO merge,
  tag, or release.

### 2026-06-29 - HOME-PC - committed (a2bd1b4); push BLOCKED by session policy
0.1.3 headful-camoufox-primary for FreeWebNovel, on `feature/v0.1.3-headful-camoufox`
(branched off the merged-0.1.2 `main`). Committed locally; the user must run
`git push -u origin feature/v0.1.3-headful-camoufox` (no merge/tag/release).

- Changed: `scripts/Universal/webnovel_scraper/request_manager.py` (headless default
  False; `try_http_first` param + `_cf_warmed_hosts`; `HEADFUL_PRIMARY_LADDER` +
  `HTTP_FIRST_PRIMARY_LADDER`; bounded browser-primary ladder + retry cap in
  `fetch`; `_warm_camoufox_session` + warm-up call in `_fetch_camoufox_once`;
  `_reset_camoufox` clears warmed hosts).
- Changed: `scripts/Universal/webnovel_scraper/catalog.py` (4 freewebnovel rows
  `use_browser=True`).
- Changed: `scripts/Universal/webnovel_scraper/models.py` (`SiteSpec.use_browser`
  doc; `ScrapeJob.http_first` + max_retries doc).
- Changed: `scripts/Universal/webnovel_scraper/pipeline.py` (thread `job.http_first`
  into the pipeline-built RequestManager).
- Changed: `scripts/Universal/app.py` (`DEFAULT_HEADLESS=False`,
  `DEFAULT_BROWSER_MODE=True`, `DEFAULT_HTTP_FIRST=False`; browser default ON; new
  "Try fast HTTP first" checkbox; relabelled browser/headless checkboxes; wire
  `http_first` into job + RequestManager; `_refresh_browser_state` manages the new
  checkbox).
- Added:   `files/tests/test_headful_camoufox.py` (12 cases).
- Changed: `files/tests/test_phase2.py` (bounded browser-primary ladder + HTTP-first
  opt-in cases), `files/tests/test_phase8_gui.py` (headless OFF / browser ON /
  HTTP-first OFF defaults).
- Changed: `md-instructions/CHANGELOG.md` (0.1.3 section), `md-instructions/Briefing.md`
  (new FWN fetch architecture + legacy diff + version), `md-instructions/handoff.md`
  (this entry, Current Focus, Work Log).
- Note: `verify` green (153 passed). Committed locally (a2bd1b4); push blocked by
  the session permission policy — user runs
  `git push -u origin feature/v0.1.3-headful-camoufox`. NOT merged to main, NOT
  tagged, NOT released. Live headful-camoufox FWN clearance UNPROVEN — needs the
  chapter-102 live test.

### 2026-06-29 - CSPW-PC (work PC) - NOT committed / NOT pushed (no git on this PC)
0.1.2 Cloudflare avoidance + fresh-install fixes. **git is not installed on
CSPW-PC and there is no admin to install it**, so the branch/commit/push could not
be done here. The complete change set is in the working tree, `verify` green (138
passed). Finish the branch on HOME-PC (has git + admin): get this tree's changed
files into the HOME-PC clone, then run the command block the agent provided
(create `feature/v0.1.2-cf-avoidance` off `main`, commit as
Elijah Matthew <129206189+elmatthe@users.noreply.github.com>, push). Do NOT touch
`main`; do NOT tag/release (re-test live first).

- Added:   `scripts/Universal/webnovel_scraper/browser_env.py` (setdefault
  `PLAYWRIGHT_BROWSERS_PATH` → contained `files/bin/ms-playwright`).
- Changed: `scripts/Universal/webnovel_scraper/request_manager.py` (warm-up GET +
  host-derived Referer + Sec-Fetch-Site chaining via `_http_get`/`_warmed_hosts_for`/
  `_apply_request_headers`; removed static `webnovel.com` Referer from
  `BROWSER_HEADERS`; `_looks_like_browser_launch_failure` + no-backoff advance in
  the retry ladder; import `browser_env.ensure_browsers_path`).
- Changed: `scripts/Universal/webnovel_scraper/cf_bypass.py` (import
  `browser_env.ensure_browsers_path` so the stealth path also points at the
  contained Chromium).
- Changed: `Setup_and_Run-Web-Novel-Scraper.bat` (gated `python -m playwright
  install chromium` contained via `PLAYWRIGHT_BROWSERS_PATH`; header comment).
- Changed: `Setup_and_Run-Web-Novel-Scraper.command` (added the missing gated
  camoufox fetch; relabelled Chromium step).
- Added:   `files/tests/test_cf_avoidance.py` (10 offline cases).
- Changed: `md-instructions/Briefing.md`, `md-instructions/CHANGELOG.md`
  (new `[0.1.2]` section), `md-instructions/handoff.md` (this entry + focus + work
  log).
- Note: `verify` green (138 passed). `files/bin/ms-playwright` and `files/cache/`
  are gitignored — do not commit. The browser engines are downloaded by re-running
  the launcher on each machine.

### 2026-06-28 - HOME-PC - COMMITTED + PUSHED as v0.1.1
0.1.1 release: committed all uncommitted 0.1.1 work as one new commit on `main` on
top of the v0.1.0 orphan commit `0bb4fe5`, tagged `v0.1.1` (annotated), pushed
`main` + tag to `origin`, and published the GitHub release. The complete file set
committed in this release (everything below, accumulated across the three prior
0.1.1 sync entries):

- Changed: `scripts/Universal/app.py`,
  `scripts/Universal/webnovel_scraper/request_manager.py`,
  `scripts/Universal/webnovel_scraper/cf_bypass.py`,
  `scripts/Universal/webnovel_scraper/models.py`,
  `scripts/Universal/webnovel_scraper/pipeline.py`,
  `scripts/Universal/webnovel_scraper/adapters/freewebnovel.py`,
  `scripts/Universal/webnovel_scraper/adapters/webnovel_dynamic.py`.
- Changed: `files/tests/test_phase2.py`, `files/tests/test_phase5_pipeline.py`,
  `files/tests/test_phase8_gui.py`.
- Added:   `files/tests/test_brotli_extraction_fix.py`,
  `files/tests/test_stealth_rescue.py`.
- Added:   `files/test-files/fwn_chapter_current_ok.html`,
  `files/test-files/fwn_chapter_brotli_garbage.html`.
- Changed: `README.md`, `md-instructions/Briefing.md`,
  `md-instructions/CHANGELOG.md`, `md-instructions/handoff.md` (this entry +
  release work-log entry).
- Deleted: `md-instructions/scrape_noble_queen_issue_summary.md`.
- Note: `verify` green (128 passed). Commit `0bb4fe5` (v0.1.0) left untouched.

### 2026-06-28 - HOME-PC - not committed (left in working tree, per user)
0.1.1 Cloudflare ladder: playwright-stealth rescue rungs + stronger end-of-run sweep
(camoufox live-proven insufficient against a real FWN challenge).

- Changed: `scripts/Universal/webnovel_scraper/request_manager.py` (renamed strategy
  constants to `FETCH_STRATEGY_PLAYWRIGHT_STEALTH[_FRESH]` with `…_BROWSER…` aliases;
  extended `DEFAULT_ESCALATION_LADDER` + `BROWSER_ESCALATION_LADDER` with the two
  stealth rungs after camoufox_fresh; updated dispatch + ladder/MAX_RETRIES comments;
  added `_teardown_chromium` and the one-engine-per-thread teardown calls in
  `_fetch_camoufox_once` / `_fetch_browser_once`).
- Changed: `scripts/Universal/webnovel_scraper/cf_bypass.py` (docstring now documents
  the live ladder order + the one-engine-per-thread constraint).
- Unchanged (verified): `scripts/Universal/webnovel_scraper/pipeline.py` — the
  Phase-9C sweep already covers all non-permanent/non-extraction failures in all
  three modes and re-walks the full ladder via `rm.fetch`; extending the ladder
  strengthens the sweep automatically. `cloudflare_detection.py` already content-aware.
- Added: `files/tests/test_stealth_rescue.py` (8 cases).
- Changed: `files/tests/test_phase2.py` (2 ladder tests updated for the longer
  ladders + the new stealth rungs).
- Changed: `md-instructions/CHANGELOG.md`, `md-instructions/Briefing.md` (new ladder,
  backlog item #1 CANCELLED, Known Issues camoufox-insufficient, count 120→128),
  `md-instructions/handoff.md` (this entry, work log, Open Issues #11).
- Note: `verify` green: 128 passed. Live FWN stealth clearance UNPROVEN until the
  next live pass. Not committed; plan not deleted.

### 2026-06-28 - HOME-PC - not committed (left in working tree, per user)
0.1.1 output-folder nesting fix (doubled-folder bug) + handoff doc-sync nits.

- Changed: `scripts/Universal/app.py` (parent kept in `self._output_parent` Path,
  set only by Browse; read-only field shows the resolved target preview via
  `_refresh_output_preview`; one `_resolve_output_dir` helper for preview + run;
  preview refreshed on novel change, browse, folder-name keystroke, run-finished).
- Unchanged: `scripts/Universal/webnovel_scraper/pipeline.py` (`resolve_output_dir`
  was already correct; left as-is). `chapter_index.json` stays in the output dir
  (resume source of truth) — decision flagged, no move.
- Changed: `files/tests/test_phase5_pipeline.py` (+3 nesting/sibling/old-prefix
  cases), `files/tests/test_phase8_gui.py` (+1 single-level target-preview case;
  updated the defaults test to the new `_output_parent` Path).
- Changed: `md-instructions/CHANGELOG.md` (0.1.1 nesting-fix entry),
  `md-instructions/Briefing.md` (nesting follow-up + count 116→120),
  `md-instructions/handoff.md` (this entry, work log, issue #9 severity
  Major→Critical, issue #10, Deleted line on the prior sync entry).
- Note: `verify` green: 120 passed. Brotli fix confirmed live, untouched. Not
  committed; plan not deleted.

### 2026-06-28 - HOME-PC - not committed (left in working tree, per user)
0.1.1 post-live-pass fixes (brotli extraction, misclassification, output folder).

- Changed: `scripts/Universal/webnovel_scraper/request_manager.py` (drop `br` from
  `Accept-Encoding`; add `_looks_garbled`; guard fresh fetches and self-heal
  garbled cache reads).
- Changed: `scripts/Universal/webnovel_scraper/models.py` (new
  `EmptyExtractionError`).
- Changed: `scripts/Universal/webnovel_scraper/adapters/freewebnovel.py`,
  `.../adapters/webnovel_dynamic.py` (raise `EmptyExtractionError` on empty body).
- Changed: `scripts/Universal/webnovel_scraper/pipeline.py` (classify
  `EmptyExtractionError` as extraction failure — `RunReport.extraction_failed`, no
  block, excluded from sweep; `resolve_output_dir` gained `parent_dir`/`base_name`
  + default name `{slug}-N`).
- Changed: `scripts/Universal/app.py` (Output-folder Browse… picker + Folder-name
  field; wired to `resolve_output_dir`).
- Added:   `files/test-files/fwn_chapter_current_ok.html`,
  `files/test-files/fwn_chapter_brotli_garbage.html`.
- Added:   `files/tests/test_brotli_extraction_fix.py` (7 cases).
- Changed: `files/tests/test_phase5_pipeline.py` (default name + custom
  parent/name), `files/tests/test_phase8_gui.py` (output-folder defaults).
- Changed: `README.md`, `md-instructions/Briefing.md`,
  `md-instructions/CHANGELOG.md` (0.1.1 section), `md-instructions/handoff.md`.
- Deleted: `md-instructions/scrape_noble_queen_issue_summary.md` (stale issue
  summary, superseded; was already staged for deletion in the working tree).
- Note: `verify` green: 116 passed. Not committed; plan not deleted.

### 2026-06-27 - HOME-PC - not committed (left in working tree, per user)
Phase 9 live-scrape hardening.

- Changed: `scripts/Universal/webnovel_scraper/request_manager.py` (content-aware
  `is_cloudflare_challenge`; `MAX_RETRIES` 4→6).
- Changed: `scripts/Universal/webnovel_scraper/cf_bypass.py` (content-aware
  `is_cloudflare_challenge` with strong/ambient/content markers).
- Changed: `scripts/Universal/webnovel_scraper/models.py` (`ScrapeJob.max_retries` 4→6).
- Changed: `scripts/Universal/webnovel_scraper/pipeline.py` (`_Pacer` auto-slowdown,
  `sleep_fn` seam, second-pass sweep for all three modes, `_is_permanent_failure`,
  `RunReport.rescued`/`permanent_failed`/`auto_slowdowns`/`effective_delay`).
- Changed: `scripts/Universal/app.py` (delay relabel + 2.0 default + anti-detection hint).
- Added:   `files/test-files/wnd_g_data_post_redirect_chapter.html`,
  `files/test-files/wnd_cloudflare_challenge.html`.
- Added:   `files/tests/test_phase9.py` (16 offline cases, 9A–9D).
- Changed: `files/tests/test_phase5_pipeline.py` (exhausted-chapter test updated
  for the second-pass sweep + injected sleep).
- Changed: `README.md` (delay knob, relentless-retry/sweep note, browser-off
  auto-escalates note).
- Changed: `md-instructions/Briefing.md`, `md-instructions/CHANGELOG.md`,
  `md-instructions/handoff.md` (Phase 9 recorded).
- Note: `verify` green: 101 passed. Do not delete the implementation plan; not committed.

### 2026-06-27 - HOME-PC - not committed (left in working tree, per user)
FreeWebNovel Cloudflare resilience pass.

- Changed: `scripts/Universal/webnovel_scraper/request_manager.py` (central
  retry ladder, backoff+jitter, permanent status handling, strategy escalation,
  fresh-browser retry path, configurable retry defaults).
- Changed: `scripts/Universal/webnovel_scraper/cf_bypass.py` (browser navigation
  surfaces 403/404 as permanent HTTP failures).
- Changed: `scripts/Universal/webnovel_scraper/models.py` (`ScrapeJob`
  carries retry defaults for future GUI exposure).
- Changed: `scripts/Universal/webnovel_scraper/pipeline.py` (threads retry
  defaults into pipeline-owned request managers; existing failed-chapter
  tolerance regression-covered).
- Changed: `scripts/Universal/app.py` (threads default retry settings into the
  GUI-owned request manager without adding controls).
- Changed: `files/tests/test_phase2.py` (retry ladder, backoff, no-404-retry,
  browser ladder, attempt-2/3/4 success tests).
- Changed: `files/tests/test_phase5_pipeline.py` (exhausted chapter failure is
  recorded and run continues).
- Changed: `md-instructions/Briefing.md`, `md-instructions/CHANGELOG.md`,
  `md-instructions/handoff.md` (Critical mitigation status recorded honestly).
- Note: `verify` green: 82 passed. Do not delete the implementation plan yet.

### 2026-06-27 - HOME-PC - not committed (left in working tree, per user)
Phase 8 bug hunt + release pass. No code changed (no criticals found). Left
uncommitted alongside the rest of the tree; the user force-pushes the whole repo
as one release.

- Added:   `files/test-logs/v0.1.0_pre-release.md` (final manual release log;
  gitignored per AI-WORKSPACE â€” a working QA log, not shipped).
- Changed: `md-instructions/Briefing.md` (Phase 8 recorded; version ->
  release-ready; Known Issues + Next Steps updated).
- Changed: `md-instructions/CHANGELOG.md` (Phase 8 entry â€” no-criticals result,
  six flagged minors/suggestions, release-log pointer).
- Changed: `md-instructions/handoff.md` (Phase 8 focus, Open Issues table of the
  six flagged items, work-log + this sync entry).
- Unchanged: all `scripts/` code (no criticals â†’ no fixes); suite stays 71 tests.
- Note:    `verify` green (71 passed). Implementation-plan drop intentionally NOT
  deleted (user removes it after the live test).

### 2026-06-27 - HOME-PC - not committed (left in working tree, per user)
Phase 7 launcher finalization + README rewrite. Left uncommitted alongside the
rest of the tree; the user force-pushes the whole repo as one release.

- Changed: `Setup_and_Run-Web-Novel-Scraper.bat` (launch via venv `pythonw.exe`
  with `python` fallback; idempotent deps via `.venv\requirements.lock` + `fc /b`).
- Changed: `README.md` (legacy editor readme -> minimal user-facing guide: what it
  does, supported novels/sites table w/ 0.1.0 status, double-click quick start,
  SmartScreen note, Downloads output path, browser-mode note, Windows-only).
- Unchanged: `Setup_and_Run-Web-Novel-Scraper.command` (already a full macOS
  launcher with an untested-on-Mac note; left as-is per the Phase 7 instruction).
- Changed: `md-instructions/Briefing.md`, `md-instructions/CHANGELOG.md`,
  `md-instructions/handoff.md` (Phase 7 recorded; Next Steps -> Phase 8 bug hunt).
- Note:    `verify` green (71 passed). No test-suite change (launcher/README only).

### 2026-06-27 - HOME-PC - not committed (left in working tree, per user)
Phase 6 GUI implementation. Left uncommitted alongside the rest of the tree; the
user force-pushes the whole repo as one release.

- Changed: `scripts/Universal/app.py` (Phase-0 stub -> full `ScraperApp(tk.Tk)`
  single-window GUI: catalog-driven dropdowns with disabled greying, range/delay/timeout,
  output-mode radio, browser/headless/cache toggles, progress bar, scrolled log,
  Start/Stop on a daemon thread with `self.after`-marshalled UI updates).
- Changed: `md-instructions/Briefing.md`, `md-instructions/CHANGELOG.md`,
  `md-instructions/handoff.md` (Phase 6 recorded; Next Steps -> Phase 7 launcher + README).
- Note:    `verify` green (71 passed). GUI is a thin shell; no test-suite change.

### 2026-06-27 - HOME-PC - not committed (left in working tree, per user)
Phase 5 pipeline implementation. Left uncommitted alongside the still-uncommitted
restructure; the user force-pushes the whole tree as one release. The changes
below are staged in the working tree only.

- Changed: `scripts/Universal/webnovel_scraper/pipeline.py` (stub -> full `run_scrape`
  orchestration: TOC persistence, range clamp, resume, three output modes, cancel,
  disabled-adapter refusal, `resolve_output_dir`, `RunReport`).
- Added:   `files/tests/test_phase5_pipeline.py` (11 offline pipeline tests, fake adapter).
- Changed: `.gitignore` (removed the duplicated inline-comment runtime-dir lines; the
  clean standalone patterns remain).
- Changed: `md-instructions/Briefing.md`, `md-instructions/CHANGELOG.md`,
  `md-instructions/handoff.md` (Phase 5 recorded; Next Steps -> Phase 6 GUI).
- Note:    `verify` green (71 passed).

### 2026-06-26 - HOME-PC - not pushed
Validation cleanup only; do not begin Phase 5 until these current-state fixes are reviewed.

- Changed: `.gitignore` (effective generated-folder ignore patterns added).
- Changed: `.claude/settings.local.json` (stale `Scripts/` / `files/fixtures/` permissions moved to current paths).
- Changed: `Setup_and_Run-Web-Novel-Scraper.bat` (user-scope-only Python install, python.org fallback, existing venv detection, no `%CD%` display).
- Changed: `Setup_and_Run-Web-Novel-Scraper.command` (removed machine-scope Python install path; user-scope Homebrew path only).
- Changed: `md-instructions/Briefing.md` (fixture path drift corrected to `files/test-files/`).
- Changed: `md-instructions/handoff.md` (stale template-launcher note removed; this entry added).
- Removed: `.pytest_cache/` and `.agents/` generated root folders.
- Note: `verify` green after cleanup (60 passed).

### 2026-06-25 - HOME-PC - not pushed (user will force-push the whole repo as one release)
Structural refactor only - no git run this session. The user force-pushes the full repo
as a single 0.1.0 release later; this log is the record of what moved so the push is clean.

- Renamed: `Scripts/` to `scripts/` (lowercase).
- Moved:   `scripts/webnovel_scraper/` to `scripts/Universal/webnovel_scraper/`
- Moved:   `scripts/app.py` to `scripts/Universal/app.py`
- Kept:    `scripts/requirements.txt`, `scripts/verify.py` (directly under `scripts/`)
- Added:   `scripts/Windows/.gitkeep`, `scripts/MacOS/.gitkeep`
- Moved:   pytest suite `Scripts/tests/*.py` to `files/tests/` (test_phase1-4, test_regression,
           test_scaffold, `__init__.py`)
- Added:   `files/tests/conftest.py` (inserts `scripts/Universal/` onto `sys.path`)
- Moved:   `files/fixtures/*.html` to `files/test-files/` (9 fixtures)
- Added:   `files/test-logs/.gitkeep`
- Moved:   legacy reference into `files/legacy-reference/` - the old top-level scripts
           (`freewebnovel-webscraper.py`, `scrape_noble_queen-v3.py`,
           `scrape_noble_queen_webnovel-v1.py`, `sm_pdf_editor-v8.2.py`, `ss_pdf_editor-v1.py`,
           `index_loader.py`, `cf_bypass.py`, `test_chapter_range_loop.py`,
           `requirements-noble-queen.txt`, `Shadow_Slave_Instructions-1.txt`, `README.md`),
           the `Index_Names_Lists/` lexicons, and the old `files/tests/` harness
           (`old-tests/test_heading_extraction.py` + its fixture).
- Changed: `request_manager.py` (`parents[2]` to `parents[3]`); the 3 tests' fixture path
           (`files/fixtures` to `files/test-files`); `verify.py` (`TESTS_DIR` to `files/tests/`).
- Added:   `Setup_and_Run-Web-Novel-Scraper.bat` and `.command` (built from the new templates).
- Removed: old stub launchers `Setup_and_Run.bat` / `Setup_and_Run.command` (replaced).
- Confirmed absent: old template launchers `Setup_and_Run-template.bat` /
           `Setup_and_Run-template.command`.
- Changed: `.gitignore`, `md-instructions/Briefing.md`, the implementation plan section 2 /
           deletion-test / launcher references, `md-instructions/handoff.md`, `CHANGELOG.md`.
- Note:    `verify` green (60 passed). Pull this whole tree on other machines before continuing.
