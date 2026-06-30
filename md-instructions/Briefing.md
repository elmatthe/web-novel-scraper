# Briefing â€” webnovel-scraper

## What This Project Does
A Windows-first desktop tool that scrapes web-novel chapters from supported sites
and renders them into clean, print-ready PDFs. A single-window GUI lets a
non-technical user pick a novel and a source site, choose a chapter range and
output mode (separate / chunked / single), and run the scrape. There is **no
prose editing** â€” only extraction-level noise/whitespace cleanup; the words of
titles and bodies are preserved as published.

## Tech Stack
- Language: Python 3.13 (platform-neutral; macOS support is a later pass)
- GUI: tkinter / ttk (single window, no separate launcher)
- Key Libraries: requests, beautifulsoup4, cloudscraper, playwright (+ playwright-stealth
  for the Cloudflare browser path), reportlab (PDF layout), pypdf + pdfplumber (PDF
  post-processing), pytest (tests)

## Architecture
The repo follows the AI-WORKSPACE cross-platform layout: program code ships from
`scripts/` (shared code in `scripts/Universal/`, with `scripts/Windows/` and
`scripts/MacOS/` for OS-only glue), while dev-only material (the pytest suite,
fixtures, QA logs, runtime cache/output, legacy reference) lives under `files/`
and never ships. The pytest suite is at `files/tests/`, its fixtures at
`files/test-files/`. Clean module boundaries under
`scripts/Universal/webnovel_scraper/`:
- `models.py` â€” normalized dataclasses (`SiteSpec`, `ChapterMeta`, `ChapterContent`,
  plus `OutputMode` and `ScrapeJob`).
- `catalog.py` â€” the Novel/Site matrix; single source of truth for GUI + pipeline.
- `adapters/` â€” one module per site behind a `BaseAdapter` ABC (`base.py`); a registry
  maps `site_key -> adapter`. 0.1.0 ships two enabled adapters (`freewebnovel`,
  `webnovel_dynamic`) and three disabled stubs (`empire_novel`, `novel_bin`, `telegraph`).
- `request_manager.py` â€” HTTP/browser fetching with configurable per-fetch
  retries, exponential backoff + jitter, and an on-disk HTML cache. **0.2.0** adds
  **typed `FetchError` subclasses** (`NotFound` / `Challenge` / `Transient` /
  `RateLimited`) with **body-first classification** (a real payload is success even
  under 403/503; 404/410 → NotFound; 429 → RateLimited + limiter cooldown; 5xx →
  Transient; 403 is no longer permanent), a **fast/HTTP-probe split**
  (`fetch(..., fast_path=True)` walks a short bounded fast ladder; HTTP probes are
  extra and never consume the browser budget), **explicit per-manager timeouts**
  (`http_timeout` / `browser_nav_timeout` / `cloudflare_timeout` — the old mutated
  module-global `FETCH_TIMEOUT` is retired), and a `last_fetch_info` record for the
  circuit breaker. **Two fetch shapes (since 0.1.3):**
  - **FreeWebNovel = bounded two-engine HEADFUL browser ladder** (`use_browser=True`).
    Primary: ONE persistent, VISIBLE (headless=False) camoufox browser created once
    per run and reused for every chapter; its session is warmed once per host
    (`_warm_camoufox_session` navigates the same page to the site origin so Cloudflare
    seats a `cf_clearance` cookie in the browser context). A blocked chapter walks the
    SHORT bounded ladder `HEADFUL_PRIMARY_LADDER = (camoufox, camoufox, camoufox_fresh,
    playwright_stealth)` — same-page camoufox retries, ONE fresh-camoufox recovery,
    then ONE escalation to **headful stealth-Chromium** (`cf_bypass.create_stealth_
    browser`/`fetch_with_stealth`, VISIBLE) — the exact legacy visible engine,
    historically proven to clear FWN's Cloudflare. Retry budget capped to the ladder
    length: **per-chapter cap 4 attempts** (5 with `try_http_first`). Once camoufox is
    exhausted in a run, a latch (`_camoufox_exhausted`) routes later chapters + sweep
    retries straight to the ONE persistent stealth-Chromium browser
    (`STEALTH_LATCHED_LADDER`, reused not relaunched — the two engines can't share a
    thread). The stealth engine uses the contained `PLAYWRIGHT_BROWSERS_PATH →
    files/bin/ms-playwright` Chromium the launcher installs; a missing engine is a
    non-blocking immediate failure. No `playwright_stealth_fresh`/cloudscraper/http on
    this default path. Mirrors the legacy scraper (visible persistent browser, one
    fetch per chapter). With the opt-in `try_http_first`, two cheap HTTP rungs precede
    camoufox.
  - **Non-Cloudflare / opt-in = HTTP path** (`use_browser=False`, e.g.
    WebNovel-dynamic). Plain HTTP (with the 0.1.2 homepage warm-up + host-derived
    Referer/Sec-Fetch-Site) escalating through the legacy
    `DEFAULT_ESCALATION_LADDER` `http -> cloudscraper -> camoufox -> camoufox_fresh
    -> playwright_stealth -> playwright_stealth_fresh`. Only one browser engine is
    live per thread — the fetch methods tear the other engine down before starting
    one. The Cloudflare-aware browser path lives in `cf_bypass.py`.
- `cloudflare_detection.py` â€” the one shared `is_cloudflare_challenge` detector
  imported by both `request_manager` and `cf_bypass` (so they cannot drift). Strong
  interstitial markers flag immediately; ambient beacon markers clear only on
  *structural* payload evidence (`__NEXT_DATA__`, `g_data.chapInfo` with non-empty
  contents, or a real chapter-body container) â€” never by page length.
- `host_rate_limiter.py` â€” **(0.2.0)** a `HostRateLimiter` shared by the primary and
  the rescue lane so both obey ONE per-host pace: per-host FIFO ticket ordering (no
  starvation), positive-only jitter, a global floor `HOST_MIN_INTERVAL = 3.0s`, a
  `raise_interval` ratchet (never lowers), and a 429 host-cooldown. The `_Pacer` feeds
  it; the shared effective interval is `max(HOST_MIN_INTERVAL, job.delay, pacer.current)`.
- `rescue_pool.py` â€” **(0.2.0)** the **single-lane** hard-chapter rescue. `RescuePool`
  owns ONE dedicated worker thread (`RESCUE_MAX_WORKERS = 1`, a hard cap the constructor
  enforces; the 1–5 toggle is deferred to 0.2.1) with a bounded job queue
  (`RESCUE_MAX_PENDING = 16`). The worker builds/owns its own `RequestManager` +
  `FreeWebNovelAdapter`, walks a fixed monotonically-escalating ladder-as-data
  (`headless_camoufox` → fresh → `headful_camoufox` → `headful_chromium`) whose initial
  rung follows the primary, enforces a per-chapter processing deadline
  (`RESCUE_MAX_ELAPSED_PER_CHAPTER = 180s` from dequeue), and emits exactly ONE terminal
  `RescueResult` per accepted job (including queued-then-cancelled).
- `pdf_builder.py` â€” the one ReportLab layout (Letter, Times-Roman body, Helvetica-Bold
  headings, one chapter per page).
- `pipeline.py` â€” TOC-first, resumable orchestration over the three output modes. The
  legacy HTTP/WebNovel-dynamic path keeps adaptive auto-slowdown (`_Pacer`) and its
  second-pass sweep. **(0.2.0)** On the FreeWebNovel-browser path a **conductor** runs a
  fast-primary loop and hands genuinely-hard chapters to the single rescue lane (the SOLE
  retrier there, built lazily on the first hard chapter), gated by a scope check
  (`adapter_key == "freewebnovel"` AND `job.use_browser`). It also owns a **headless-only
  circuit breaker** (armed only on a headless-started run; trips on **≥5 consecutive** OR
  **≥9-of-20** primary network challenges; recreates the primary as visible and latches),
  a **TOC/index bootstrap fallback** (headless-block → visible retry → clean abort), and a
  **429 host cooldown** (`RATE_LIMIT_RETRY_BUDGET = 2`, never escalated to rescue). Config
  travels on an immutable `ScrapeJob` (`use_browser` / `headless` / `request_timeout` /
  `rescue_workers == 1`) + a per-run `runtime_site_spec` copy; the pipeline owns/replaces
  the active manager (no GUI-prebuilt manager, no mutated catalog row).
- `app.py` â€” the tkinter GUI entry point.
Networking never lives in parsers; parsing never lives in the GUI; the PDF builder
knows nothing about sites. `scripts/verify.py` is the mechanical gate (pytest +
dependency-pinning + docs-freshness) run before every phase commit; it runs the
suite in `files/tests/` (a `conftest.py` there puts `scripts/Universal/` on
`sys.path` so `import webnovel_scraper` resolves).

## Current Version
**0.2.0 (Unreleased — implemented, pending the user's manual live test).**
**Fast-primary + single-lane hard-chapter rescue.** 0.1.3 ran one persistent VISIBLE
browser for *every* FreeWebNovel chapter — correct but slow, since most chapters need
no bypass. 0.2.0 splits the work: a **fast primary pass** races the easy chapters, and
only genuinely hard chapters (a real Cloudflare challenge) are handed to **one**
dedicated background **rescue lane** that escalates a fixed ladder
(`headless_camoufox` → fresh → `headful_camoufox` → `headful_chromium`) under a 180s
per-chapter deadline. There is exactly ONE rescue lane (`RESCUE_MAX_WORKERS = 1`, a hard
cap; the 1–5 toggle is **deferred to 0.2.1**). The primary and rescue share one
`HostRateLimiter`. A **pipeline-owned, headless-only circuit breaker** (armed only when
the run started headless; trips on ≥5 consecutive OR ≥9-of-20 primary network challenges)
recreates the primary as a VISIBLE browser and latches there for the rest of the run, so
rescue never starts weaker than the primary. Fetch failures are now **typed** with
**body-first classification**; the old mutated `FETCH_TIMEOUT` global is replaced by
explicit per-manager timeouts; config travels on an immutable `ScrapeJob` and the pipeline
(not the GUI) owns/replaces the active manager. The GUI delay default rises to 3.0s, the
Headless hint is honest about the visible-override + visible rescue browser, rescue/breaker
activity surfaces in the existing log pane, and the scrape worker is non-daemon with a
poll-until-exit window-close. Suite: **229 offline tests** (`verify` green: 228 passed,
1 expected no-Tk-display skip).

**Honest / open question:** offline tests prove wiring, flow, and lifecycle. Whether a
**headless** primary can clear FreeWebNovel's *current* live Cloudflare — and therefore how
often the breaker has to fall back to a visible browser, or hard chapters to the visible
rescue lane — is unproven and is exactly what the manual live pass (Pass A detector
baseline, Pass B headless architecture; see Next Steps) answers. Cancellation note: Stop /
window-close is prompt *between* polls/attempts; an in-flight `page.goto` may still run to
its own navigation timeout before the worker exits.

### Prior: 0.1.3 (headful-camoufox-primary for FreeWebNovel)
**Headful-camoufox-primary** rewrite of the FreeWebNovel fetch path, matched to the legacy
scraper. Root cause of the persistent FWN Cloudflare failures, confirmed by diffing the
now-present legacy file: the rewrite was HTTP-first + headless + a six-engine ladder, and
FWN's Cloudflare clears for a *visible real browser* but blocks *headless automation*,
while the relaunch storm made it more aggressive. 0.1.3 ran FWN through a **bounded
two-engine HEADFUL ladder**: ONE persistent VISIBLE camoufox browser from request #1
(defaults flipped: `headless=False`, FWN catalog rows `use_browser=True`, GUI headless OFF
/ browser ON), reused across chapters with a one-time browser-session warm-up, then a
bounded fallback to **headful stealth-Chromium** (the legacy visible engine) when camoufox
couldn't clear a chapter. It also shipped the **detection/timing fix**: the shared
`cloudflare_detection.has_real_payload` is now content-aware for FreeWebNovel (`#article` +
FWN body selectors, with a non-trivial-text guard) and `fetch_camoufox` positively WAITS
for the real chapter DOM before capturing — so a page camoufox clears on screen is no longer
discarded as "challenge still present." Suite at 0.1.3: **163 offline tests**, `verify` green.

### Legacy diff finding (0.1.3, independently verified)
This real clone DOES contain `files/legacy-reference/freewebnovel-webscraper.py`
(the 0.1.2 pass ran on a non-git copy that lacked it). Diffed and confirmed:
legacy GUI defaulted use_playwright=True + headless=False (VISIBLE from request #1);
`HtmlFetcher.start()` made ONE browser/page reused by `fetch()` for every chapter;
no HTTP-first, no escalation ladder; the only "trick" was headful + persistent +
one-fetch-per-chapter. **Correction:** the legacy gated camoufox behind
`and self.playwright_headless`, so its *default visible* engine was actually
headful stealth-Chromium, not camoufox (camoufox was its headless-only path). The
fix is engine-independent; 0.1.3 uses headful camoufox (stronger anti-detect, and
the engine this codebase already warms/reuses).

### Prior: 0.1.2 (HTTP-layer Cloudflare avoidance)
Added HTTP-layer Cloudflare *avoidance* (homepage warm-up GET + host-derived
Referer/Sec-Fetch-Site on top of persistent session/cookie reuse), fixed the
Chromium install gap so the playwright-stealth rungs can launch on a fresh setup
(contained in `files/bin/ms-playwright` via `PLAYWRIGHT_BROWSERS_PATH`), and made a
browser-launch failure a non-blocking immediate skip (no 100-second freeze).
Suite: **138 offline tests**. (0.1.2 could not diff the legacy file — it was absent
from that non-git working copy; 0.1.3 did the diff.)

### Prior: 0.1.1 (release-ready â€” pending the user's manual live-scrape pass + tag/force-push).
Phase 9 (live-scrape hardening) and its review fixes are implemented; the 0.1.1
post-live-pass session added the brotli body-extraction fix, the
extraction-failure misclassification fix, the user-choosable output folder, a
follow-up fix to that feature's doubled-folder nesting bug, and the playwright-
stealth Cloudflare rescue rungs + strengthened end-of-run sweep (after camoufox was
live-proven insufficient against a real FWN challenge).
Suite is **128 offline tests**, `verify` green.

## What Has Been Built
- **Phase 0 (scaffold) â€” complete:** git repo initialized on `feature/v0.1.0-build`;
  `.gitignore`; pinned `scripts/requirements.txt`; `scripts/verify.py` gate; the full
  `webnovel_scraper/` package skeleton (empty modules + adapters registry); `cf_bypass.py`
  ported verbatim into the package; smoke test; de-templated docs.
- **Phase 1 (models, catalog, base adapter, registry, stubs) â€” complete:** `models.py`
  (`OutputMode`, `SiteSpec`, `ChapterMeta`, `ChapterContent`, `ScrapeJob`); `catalog.py`
  (8 rows as data + lookups); `adapters/base.py` (`BaseAdapter` ABC + `safe_filename`);
  `registry.py` with the disabled-adapter guard; enabled skeletons + 3 disabled stubs,
  all registered; 15 offline Phase 1 tests. Fixtures now live in `files/test-files/`.
- **Phase 2 (request manager + PDF builder) â€” complete:** `request_manager.py`
  (`RequestManager`: HTTP path w/ cloudscraper CF fallback, Playwright stealth path,
  shared `files/cache/{slug}/` cache, retries/backoff, `FetchError`; plus the Phase 3
  `fetch()` facade + `cancel_event`/`ScrapeCancelled` + `start()`); `pdf_builder.py`
  (the one ReportLab layout ported from the legacy editor, `pypdf` post-processing,
  `chapters_to_text` + `create_pdf`); offline Phase 2 tests.
- **Phase 3 (FreeWebNovel adapter) â€” complete:** `adapters/freewebnovel.py`
  (`FreeWebNovelAdapter`), generalized over all four FWN novels. TOC via **Approach B**
  (generated chapter URLs from `1..count`, gap-free by construction â€” fixes the legacy
  silent-skip bug); `fetch_chapter` ports title + body extraction with invisible-char
  stripping, two-pass prefer-titled heading, noise/credit/duplicate filtering, and a
  degraded-title fallback (`Chapter N.`). Added `SiteSpec.use_browser/base_url/chapter_count`.
  Regression + Phase 3 tests with new `files/test-files/` fixtures. Pre-Phase-3 corrections folded in
  (verify exit-5 = FAIL; plan/Briefing name reconciliation; `.gitignore` `.agents/`).
  Suite: 51 offline tests, `verify` green.
- **Phase 4 (WebNovel-dynamic adapter) â€” complete:** `adapters/webnovel_dynamic.py`
  (`WebNovelDynamicAdapter`), ported from `scrape_noble_queen-v3.py`. TOC from the
  `__NEXT_DATA__` JSON (`volumeItems[].chapterItems[]`) into ordered `ChapterMeta`,
  persisted to `files/cache/{slug}/chapter_index.json` so re-runs skip the TOC
  fetch (missing/empty `__NEXT_DATA__` raises rather than returning empty; gaps +
  `chapterCnt` mismatch surfaced as warnings). `fetch_chapter` prefers the
  `chapterInfo.contents` JSON path with a `CONTENT_SELECTORS` DOM fallback,
  `_is_junky` nav/comment filtering, whitespace-only cleanup, and the shared
  heading normalization. Plain HTTP (no browser). Added `base_url` to the Noble
  Queen catalog row. New `wnd_*` fixtures + `test_phase4_wnd.py` (9 cases).
  Suite: 60 offline tests, `verify` green.
- **Phase 5 (pipeline + output modes + resume) â€” complete:** `pipeline.py`
  (`run_scrape`) â€” TOC-first, resumable orchestration over the three output
  modes. It resolves the `SiteSpec` from the catalog and **refuses a disabled
  row before the adapter is built or touched** (pipeline-layer defense in depth,
  on top of GUI greying and the stub's `NotImplementedError`). It builds the
  chapter index once and persists it to `chapter_index.json` in the output dir
  (loaded on a re-run into the same dir, so resume never re-fetches the TOC);
  clamps the requested `[start, end]` to the available TOC (logged); skips a
  chapter whose PDF already exists (resume) and records a single failed chapter
  without aborting; honours the per-fetch `delay` and a `cancel_event` between
  chapters. Output modes: **SEPARATE** (one PDF per chapter, `safe_filename(heading)`),
  **CHUNKED** (`{Stem}_Chapters_{a}-{b}.pdf`), **SINGLE** (`{Stem}_All_Chapters.pdf`),
  each through `pdf_builder.create_pdf` (which strips heading-only pages).
  `resolve_output_dir()` returns the next free `~/Downloads/{slug}-N` (optionally
  a user-chosen parent folder and/or custom folder name via the GUI Browse… picker).
  Returns a `RunReport` (written / skipped / failed counts + a resume hint).
  New `test_phase5_pipeline.py` (11 offline cases: the three modes, resume skips,
  range clamp, disabled-adapter refusal incl. all three stubs, cancellation,
  output-dir auto-increment) driven by a fake adapter â€” no network. Suite: 71
  offline tests, `verify` green.
- **Phase 6 (single-window tkinter GUI) â€” complete:** `app.py`
  (`ScraperApp(tk.Tk)`) â€” a thin shell over the tested pipeline. The catalog
  drives a Novel dropdown (titles) and a Site selector built as a
  `ttk.Menubutton` + `tk.Menu` so disabled rows are shown **greyed and
  non-selectable** with a "(coming soon)" suffix (a `Combobox` can't disable
  single items); selecting a novel auto-picks its first enabled site, and a
  novel with no enabled site shows a clear hint and a disabled Start. Controls:
  start/end chapter (blank end = all, via an `ALL_CHAPTERS` sentinel the
  pipeline clamps down), delay (default 1.2 s), timeout, output-mode radio
  (Separate / Chunked + chapters-per-PDF entry enabled only in Chunked /
  Single), "Use Playwright browser mode" + "Headless browser" checkboxes
  (enabled only for browser-capable adapters â€” `freewebnovel`; inert for the
  HTTP-only `webnovel_dynamic`), "Use HTML cache" (default on), a determinate
  progress bar, a scrolled log pane, and Start/Stop. The scrape runs on a daemon
  thread; **all** UI updates marshal back via `self.after(0, ...)`. Start builds
  a `ScrapeJob`, resolves the dir via `pipeline.resolve_output_dir`, constructs
  a `RequestManager` (headless/cache from the toggles), and calls
  `pipeline.run_scrape` with a `threading.Event`; Stop sets that event and the
  pipeline/request-manager halt between chapters. Timeout is honoured by setting
  the module-level `request_manager.FETCH_TIMEOUT` (read at call time) and the
  browser toggle by setting `spec.use_browser` for the run â€” neither touches a
  tested module's code. Start cannot dispatch a disabled site (GUI gate on top
  of the Phase 5 pipeline refusal). No headful test in `verify`; the GUI is kept
  a thin shell so all real logic stays in the tested modules. Suite still 71
  offline tests, `verify` green.
- **Phase 7 (Windows launcher + README) â€” complete:**
  `Setup_and_Run-Web-Novel-Scraper.bat` finalized to the plan Â§7 spec and the
  launcher portability checklist â€” self-locating via `%~dp0` (no hardcoded
  paths, no `%USERPROFILE%`/`%CD%`), `py -3`â†’`python`â†’existing-venv detection
  (3.10+ warn), user-scope-only Python install (winget `--scope user` with a
  python.org `InstallAllUsers=0 PrependPath=1` fallback, re-detect after
  install), self-healing in-repo `.venv`, the camoufox browser engine fetched
  once via `python -m camoufox fetch` (gated by a `.venv\camoufox.fetched`
  sentinel for fast re-runs), SmartScreen
  first-run note, and console-stays-open-on-failure with `pause`. Two
  finalization fixes this pass: (1) the GUI now launches via the venv's
  **`pythonw.exe`** (was `python`) so no extra console appears behind the
  tkinter window â€” the setup window stays as the live log; falls back to
  `python` only if `pythonw.exe` is absent; (2) **idempotent deps** â€” a
  `.venv\requirements.lock` copy is written after a good install and binary-
  compared (`fc /b`) on later runs, so a second double-click skips the pip
  install and goes straight to launch unless `requirements.txt` changed (or the
  venv was just rebuilt). `Setup_and_Run-Web-Novel-Scraper.command` is the
  already-written full macOS launcher carrying a "not yet runtime-tested on a
  Mac" note â€” left as-is (macOS runtime is a later pass). Root `README.md`
  rewritten as a minimal, user-facing guide (what it does, the supported
  novels/sites table with 0.1.0 status, double-click quick start, SmartScreen
  note, Downloads output location, browser-mode note, Windows-only). Suite
  unchanged at 71 offline tests, `verify` green.
- **Phase 8 (bug hunt + release pass) â€” complete:** systematic AI-WORKSPACE bug
  hunt across **every** module (not just touched ones) for deprecated libraries,
  missing error handling, hardcoded paths, fresh-machine breakage, edge cases
  (empty TOC, zero/out-of-range, all-fail, TOC-fetch failure, corrupt cache,
  output-dir errors), GUI thread-safety, resource leaks, imports, debug
  artifacts, and platform-neutrality. **Result: no Critical defects** â€” the edge
  cases are all handled without crashing, every module imports under the venv,
  all UI updates marshal via `self.after`, there are no bare excepts and no
  `os.startfile`/`cmd /c`/`\`-literal paths in the package. Six Minor/Suggestion
  items were **logged and left for the user** (not fixed, per AI-WORKSPACE): the
  WND adapter persists its own `chapter_index.json` ignoring the cache toggle
  (#1, can serve a stale TOC across runs over time); headless default vs README
  (#2); degraded `Chapter N..pdf` double-dot filename (#3); `spec.use_browser`
  mutates the shared catalog row (#4); `PdfReader` not closed in
  `remove_single_heading_pages` (#5); a mid-write PDF failure would be treated as
  complete by resume (#6). The final manual release log is at
  `files/test-logs/v0.1.0_pre-release.md` (launcher portability walkthrough, GUI
  load, novel/site selection incl. disabled greying, resume/Stop/output offline
  proofs, and the live FWN+WND scrapes across all three output modes structured
  but marked skipped for the user's manual pass). No code change â†’ suite
  unchanged at 71 offline tests, `verify` green.

- **Post-live-pass Critical mitigation (2026-06-27) â€” complete:** a live Codex
  pass found FreeWebNovel first-time uncached chapter fetches could fail on a
  Cloudflare block in the browser path, risking mass failed chapters on a long
  run. `RequestManager.fetch()` now owns one central retry ladder with
  configurable defaults (`max_retries=4`, `retry_base_delay=5.0`), exponential
  backoff + jitter, permanent 403/404 short-circuiting, retryable 5xx/network/CF
  handling, and strategy escalation from HTTP to cloudscraper to Playwright
  stealth to a fresh Playwright browser context/new UA. `ScrapeJob` and the GUI /
  pipeline construction thread the retry knobs without adding GUI controls.
  Pipeline behavior was verified/strengthened: a chapter that exhausts retries
  is logged, recorded in `RunReport.failed`, listed in the summary, and the run
  continues. This fixes the fatal/mass-failure behavior and maximizes FWN
  recovery; Cloudflare bypass itself remains a live-site risk until the user's
  manual FWN scrape confirms current success. Suite: 82 offline tests, `verify`
  green.

- **Phase 9 (live-scrape hardening) â€” complete:** five-part hardening pass driven
  by the live Noble Queen run where intermittent Cloudflare dropped chapters 3 & 4.
  - **9A â€” GUI rate-limit control:** the delay field is now the user-facing
    "Delay between fetches (seconds)" anti-detection knob (default **2.0s**,
    fractional, validated non-negative), bound straight to `ScrapeJob.delay`. The
    three user choices are novel, site, and inter-fetch delay.
  - **9B â€” adaptive auto-slowdown:** a new pipeline `_Pacer` raises the *effective*
    inter-fetch delay each time a chapter fetch is classified as a block/challenge
    (multiplier 1.5, floor 2.0s, ceiling 30.0s), logged, and reported on
    `RunReport.auto_slowdowns` / `effective_delay`. This across-chapter pacing is
    distinct from and on top of the request-manager's per-attempt exponential
    backoff; both coexist.
  - **9C â€” relentless per-chapter retry + second-pass sweep:** the give-up
    threshold is explicit and generous (`MAX_RETRIES`/`ScrapeJob.max_retries`
    raised **4 â†’ 6**, i.e. up to 7 escalating attempts via the
    `http â†’ cloudscraper â†’ camoufox â†’ camoufox_fresh` ladder, later attempts
    hammering camoufox_fresh). After the main range, the pipeline runs a **second
    pass over the failed list** at the auto-slowed delay (SEPARATE post-loop;
    CHUNKED/SINGLE before the group/file PDF is written so a rescued chapter is
    included). Rescued chapters move to `RunReport.rescued`. A permanent 403/404 is
    classified into `RunReport.permanent_failed` and **short-circuits** â€” never
    swept, so a dead chapter can't hang the run.
  - **9D â€” WebNovel camoufox rescue:** the open Critical's most likely root cause
    was found and fixed offline. `request_manager.is_cloudflare_challenge` was
    re-flagging a *cleared* post-redirect WebNovel page as a challenge because that
    page still carries Cloudflare's ambient `/cdn-cgi/challenge-platform/` beacon
    script; the old single-marker `or` check mis-read the beacon as a live
    challenge, so camoufox's good HTML looked like "challenge still present" and the
    ladder escalated to failure. Detection is now content-aware (strong
    interstitial markers flag immediately; ambient beacon markers only flag when no
    real payload is present), in both `request_manager` and `cf_bypass`. The
    `g_data.book` / `g_data.chapInfo` parse path (added in the prior Codex pass) is
    confirmed co-equal with `__NEXT_DATA__` and reachable whether the HTML came from
    HTTP **or** a browser rung. New `wnd_g_data_post_redirect_chapter.html` (g_data
    present, `__NEXT_DATA__` absent, beacon present) and `wnd_cloudflare_challenge.html`
    fixtures back the regression tests.
  - **9E â€” docs:** README + this Briefing note that browser-mode-off still
    auto-escalates to a browser engine on a block (a starting path, not a hard cap),
    and document the delay knob + auto-slowdown.
  - New `test_phase9.py` (16 offline cases) covers all five parts; one Phase-5 test
    updated for the sweep. Suite: **101 offline tests**, `verify` green.
  - **Review fixes (2026-06-27):** the detector was extracted into a shared
    `cloudflare_detection.py` (imported by both `request_manager` and `cf_bypass`,
    no duplicated logic); the old 40 KB length-only clearance was removed so ambient
    beacons clear only on *structural* payload evidence. Auto-slowdown now actually
    sleeps the raised delay after a block; chunked mode runs the second-pass sweep
    **once** over all non-permanent failures (not per chunk); HTTP 401 is reclassified
    transient (only 403/404 are permanent); and the `_Pacer` ceiling is now an
    absolute cap on the base delay too. +5 regression tests → **106 offline tests**.

- **0.1.1 post-live-pass fixes (2026-06-28) â€” complete:** two live-discovered
  defects plus a requested feature.
  - **Brotli body-extraction fix (Critical).** Live Shadow Slave chapters 3+ all
    failed "Could not extract body paragraphs" while chapters 1â€“2 worked. Root
    cause: `request_manager` advertised `Accept-Encoding: gzip, deflate, br`, but
    `requests` cannot decode Brotli without the optional `brotli` package, so a
    brotli-encoded chapter came back as U+FFFD-replacement-char garbage (~15 KB,
    no `<html>`) that yielded zero paragraphs. (Chapters 1â€“2 had been cached clean
    from an earlier run; 3+ hit the brotli path fresh.) The adapter selectors were
    never wrong â€” the real current FWN markup extracts 50 paragraphs once correctly
    decoded. Fix: drop `br` from the header (gzip/deflate are always decodable); add
    a `_looks_garbled` guard that treats an undecodable response (>2% replacement
    chars) as a retryable fetch failure so the ladder escalates instead of caching
    garbage; and self-heal a previously-poisoned cache entry on read (a garbled
    cache file is ignored and re-fetched).
  - **Extraction-failure misclassification fix.** An empty-extraction outcome (a
    fully-fetched, non-challenge page with no body) was routed into the
    block/challenge path: `pipeline._fetch_one` called `pacer.register_block()`,
    driving the auto-slowdown up (5.2â†’7.9â†’â€¦â†’30s) on what was not a Cloudflare
    block. Now its own `models.EmptyExtractionError` class (raised by both the FWN
    and WND adapters): recorded in `RunReport.failed` and the new
    `RunReport.extraction_failed`, but **never** registers a block (no
    auto-slowdown) and is **excluded from the second-pass sweep** (re-fetching the
    same page yields the same empty body).
  - **User-choosable output folder (feature).** The default output folder name is
    now `{slug}-N` (e.g. `shadow-slave-1`), renamed from `webscraped_{slug}-N`.
    `resolve_output_dir` gained optional `parent_dir` + `base_name` params
    (defaulting to the prior behaviour, with the `-N` no-overwrite increment
    applied to any custom parent+name). `app.py` added an **Output folder** row: a
    read-only parent display + a native **Browse…** picker (`filedialog.
    askdirectory`) and an optional **Folder name** entry (blank = the novel slug).
    All path logic stays in `resolve_output_dir`; the GUI is a thin shell.
  - **Output-folder nesting follow-up fix.** The feature above shipped with a
    doubled-folder bug: a live run wrote `…/Downloads/webscraped_shadow-slave-1/
    shadow-slave-1`. `resolve_output_dir` was correct, but the GUI's read-only
    field both displayed and held the *parent* and `Browse…` wrote the picked
    folder back into it, so a prior/browsed output folder became the next run's
    `parent_dir` (nesting one level per run). Fixed: `app.py` keeps the chosen
    parent in a dedicated `self._output_parent` Path (default `~/Downloads`,
    changed only by Browse), and the read-only field now shows a live preview of
    the resolved **target** (`<parent>/{name}-N`) which is never fed back as a
    parent; `resolve_output_dir` is called via one `_resolve_output_dir` helper for
    both preview and run, always from the stored parent. `chapter_index.json` stays
    in the output dir (resume source of truth; relocating would break same-folder
    resume) — flagged cosmetic, left in place.
  - New `files/test-files/fwn_chapter_current_ok.html` (real current FWN chapter,
    sanitised) + `fwn_chapter_brotli_garbage.html` (the actual poisoned cache
    artifact). New `test_brotli_extraction_fix.py` (7 cases); `test_phase5_pipeline.py`
    (+3 nesting/sibling/old-prefix cases) and `test_phase8_gui.py` (+1 single-level
    target-preview case) extended for the output-folder default/feature/nesting fix.
    Suite: **120 offline tests**, `verify` green.
  - **Cloudflare ladder: playwright-stealth rescue rungs + stronger sweep.** A full
    Shadow Slave stress-scrape (1–3065) hit the first genuine FWN Cloudflare
    challenge and camoufox **failed every attempt** (chapters 102, 174, …). The
    dormant Chromium playwright-stealth strategy is now wired back as the last-resort
    rungs: `http → cloudscraper → camoufox → camoufox_fresh → playwright_stealth →
    playwright_stealth_fresh` (constants renamed `FETCH_STRATEGY_PLAYWRIGHT_STEALTH
    [_FRESH]`, old `…_BROWSER…` names kept as aliases; `BROWSER_ESCALATION_LADDER`
    gained the same rungs; `MAX_RETRIES` stays 6 = 7 attempts, enough for all six
    rungs). Camoufox and Chromium-stealth can't share a thread (each runs its own
    sync-Playwright → "Sync API inside the asyncio loop"), so the fetch methods tear
    the other engine fully down before starting one (`_teardown_chromium` /
    `_reset_camoufox`). The Phase-9C end-of-run sweep now re-walks this **full**
    ladder for every CF-skipped (non-permanent, non-extraction) chapter, giving the
    Chromium-stealth rescue the main pass camoufox couldn't provide; rescued chapters
    are written in all three modes, still-failing stay in `RunReport.failed` + the
    summary. New `test_stealth_rescue.py` (8 cases) + two `test_phase2.py` ladder
    tests updated. Suite: **128 offline tests**, `verify` green. **Honest status:**
    offline tests prove only the wiring/flow; whether Chromium stealth actually
    clears a live FWN challenge is unproven until the next live run.

- **0.1.2 Cloudflare avoidance + fresh-install fixes (2026-06-29) â€” complete on
  `feature/v0.1.2-cf-avoidance`:** driven by a live work-PC run (Shadow Slave
  100â€“110, fresh zip) with three problems.
  - **Task 1 â€” HTTP-layer CF avoidance.** `request_manager._http_get` adds a
    once-per-host **warm-up GET** to the site origin (acquires `cf_clearance` into
    the persistent session before chapter fetches â€” the key gap on resume runs
    where the TOC is cached), a **host-derived `Referer`** (replacing the hardcoded
    cross-site `webnovel.com` referer), and **`Sec-Fetch-Site` chaining**
    (`none` warm-up â†’ `same-origin` chapters). The persistent `requests.Session` +
    cookie reuse already existed and is unchanged; brotli fix + garbled self-heal
    preserved. **The legacy scraper was gitignored/absent so this is best-practice,
    not a confirmed port.**
  - **Task 2 â€” Chromium install gap.** Both launchers now install **both** engines:
    the `.bat` gained `python -m playwright install chromium` (contained in
    `files\bin\ms-playwright` via `PLAYWRIGHT_BROWSERS_PATH`, sentinel-gated); the
    `.command` gained the camoufox fetch it was missing. New
    `webnovel_scraper/browser_env.py` defaults `PLAYWRIGHT_BROWSERS_PATH` to the
    contained path at import (setdefault), imported by `request_manager` + `cf_bypass`,
    so the engine is found at runtime even outside the launcher.
  - **Task 3 â€” non-blocking launch failure.** `_looks_like_browser_launch_failure`
    classifies an engine-missing / launch error as an **immediate** strategy failure
    that advances the ladder with **no** backoff sleep (kills the 100-second
    "retrying in 102.7sâ€¦" freeze). A clear one-line log points to re-running setup;
    an exhausted chapter is recorded failed and the run continues.
  - **Task 4 â€” ladder.** Shape unchanged
    (`http â†’ cloudscraper â†’ camoufox â†’ camoufox_fresh â†’ playwright_stealth â†’
    playwright_stealth_fresh`); no strategy removed. Rationale: Task 1 avoids the
    challenge (the real fix); the browser rungs are the now-launchable, non-blocking
    safety net.
  - New `files/tests/test_cf_avoidance.py` (10 cases). Suite: **138 offline tests**,
    `verify` green.
- **0.1.3 headful-camoufox-primary for FreeWebNovel (2026-06-29) â€” complete on
  `feature/v0.1.3-headful-camoufox`:** the legacy file (present in this real clone)
  was diffed and confirmed the FWN failures came from HTTP-first + headless + a
  six-engine ladder; FWN clears for a VISIBLE persistent browser and blocks headless
  automation. Fix: FWN now runs through one persistent VISIBLE camoufox browser from
  request #1.
  - **Task 1 â€” defaults flipped.** `RequestManager.headless` `True â†’ False`; the
    four `freewebnovel` catalog rows set `use_browser=True`; `app.DEFAULT_HEADLESS`
    `True â†’ False`, browser-mode default ON. GUI/job/spec defaults agree.
  - **Task 2 â€” one persistent warmed visible browser.** Camoufox browser created
    once per run and reused for every chapter; new `_warm_camoufox_session` warms the
    *browser* session once per host (origin GET â†’ cf_clearance in the browser
    context). A normal success never recreates the browser.
  - **Task 3 â€” killed the storm.** Browser-primary walks the bounded
    `HEADFUL_PRIMARY_LADDER = (camoufox, camoufox, camoufox_fresh, playwright_stealth)`
    with the retry budget capped to the ladder length (per-chapter cap 4): camoufox
    primary, then ONE escalation to **headful stealth-Chromium** (the legacy visible
    engine) when camoufox can't clear. The stealth engine is persistent + reused via a
    `_camoufox_exhausted` run latch (later chapters/sweep go straight to it,
    `STEALTH_LATCHED_LADDER`, never relaunched); contained `files/bin/ms-playwright`
    Chromium; a missing engine is a non-blocking immediate failure. End-of-run sweep
    is one pass, auto-bounded, and gets the fallback. Failed recording, run-continues,
    and the auto-slowdown pacer retained.
  - **Task 4 â€” HTTP-first opt-in.** New GUI "Try fast HTTP first" checkbox (default
    off) + `ScrapeJob.http_first` / `RequestManager.try_http_first`; WebNovel-dynamic
    keeps `use_browser=False` plain-HTTP fast path; all 0.1.1/0.1.2 fixes preserved.
  - `files/tests/test_headful_camoufox.py` (20 cases, both engines mocked);
    `test_phase2.py` + `test_phase8_gui.py` updated. Suite: **157 offline tests**,
    `verify` green.
- **0.2.0 fast-primary + single-lane hard-chapter rescue (Unreleased) â€” implemented,
  pending the manual live test.** Built in five phases (each verified before the next):
  - **Phase 1 â€” typed failures + classification + limiter + run-config.** Typed
    `FetchError` subclasses + body-first classification (403 no longer permanent; 429 â†’
    limiter cooldown); fast/HTTP-probe split (`fetch(..., fast_path=True)`); explicit
    per-manager timeouts (the mutated `FETCH_TIMEOUT` global retired); new
    `host_rate_limiter.py` (`HostRateLimiter`); `ScrapeJob` run-config
    (`use_browser`/`headless`/`request_timeout`/`rescue_workers == 1`) + `runtime_site_spec`.
  - **Phase 2 â€” single-lane `RescuePool` (`rescue_pool.py`).** ONE dedicated worker
    thread (hard cap `RESCUE_MAX_WORKERS = 1`), bounded queue, ladder-as-data with
    monotonic escalation + initial-mode-follows-primary, 180s per-chapter deadline,
    one-terminal-result-per-job (incl. queued-then-cancelled), worker-crash â†’ `pool_failed`.
  - **Phase 3 â€” pipeline conductor.** Fast-primary loop + the single rescue lane as the
    sole FWN-browser retirer (lazy, scope-gated to `freewebnovel` + `use_browser`),
    headless-only circuit breaker (≥5 consecutive OR ≥9-of-20; recreate-visible + latch),
    TOC bootstrap fallback (headless-block → visible retry → abort), 429 cooldown
    (`RATE_LIMIT_RETRY_BUDGET = 2`), `RunReport` rescue/breaker metrics. The legacy
    `_drive`/sweep path (WebNovel-dynamic, injected adapters) is byte-for-byte unchanged.
  - **Phase 4 â€” GUI wiring.** Delay default 3.0s; the pipeline owns/replaces the manager
    (no GUI-prebuilt `RequestManager`, no `SiteSpec.use_browser` mutation, `FETCH_TIMEOUT`
    not written anywhere in `app.py`); honest Headless hint; rescue/breaker lines in the
    existing log pane (all GUI updates via `self.after`, rescue threads never touch tk);
    non-daemon worker + poll-until-exit window-close. A small legacy-path edit threads
    `runtime_site_spec` + job timeout/headless so dropping the spec mutation is safe.
  - **Phase 5 â€” docs + version.** This CHANGELOG/Briefing/README/handoff pass + a light
    offline release-metadata test. New test files
    `test_phase1_rescue_core.py`/`test_phase2_rescue_pool.py`/`test_phase3_rescue_conductor.py`/`test_phase4_gui.py`.
    Suite: **229 offline tests** (`verify` green: 228 passed, 1 expected no-Tk-display skip).
    No commit, no tag, no date until the live pass.

## Known Issues
- **(0.2.0) Headless-primary clearance of FreeWebNovel â€” the open live question.**
  0.2.0 lets the primary *start* headless (fast) and arms a circuit breaker that
  recreates the primary as visible if headless is broadly blocked, with hard chapters
  going to the visible rescue lane. Whether a **headless** primary actually clears FWN's
  current Cloudflare at all â€” and therefore how often the breaker trips or chapters fall
  to rescue â€” is **unproven offline** and is exactly what the manual live pass answers
  (Pass A baseline headful; Pass B headless architecture). The single rescue lane has full
  offline lifecycle coverage but has **not** been exercised against a live challenge.
  Cancellation is prompt *between* polls/attempts, not necessarily mid-navigation (an
  in-flight `page.goto` may run to its nav timeout before the worker exits).
- **WebNovel camoufox rescue (was the open Critical):** the most likely root cause
  â€” over-eager challenge detection mis-flagging cleared post-redirect pages â€” is
  **fixed and proven offline** (9D). Honest caveat: whether camoufox actually
  clears a *genuinely* challenged WebNovel chapter on a given day is live-site
  dependent and can only be confirmed by the user's manual pass; the code can no
  longer fail a chapter that camoufox *did* clear, and intermittent failures now
  get the relentless ladder + second-pass sweep instead of an immediate skip.
- **FreeWebNovel Cloudflare — 0.1.3 adopts the legacy headful-persistent-browser
  architecture; UNPROVEN live.** Both camoufox (0.1.1) and Chromium stealth were
  live-proven insufficient at *clearing* a real FWN challenge in HEADLESS automation
  with a per-chapter relaunch ladder; 0.1.2's HTTP-layer avoidance was unconfirmed.
  The legacy diff (0.1.3) pinpointed the actual working config: a single VISIBLE
  persistent browser, one fetch per chapter, no escalation storm. 0.1.3 now does
  exactly that as a **bounded two-engine headful ladder** — headful camoufox primary
  (reused + warmed), then a bounded fallback to **headful stealth-Chromium** (the
  legacy visible engine) when camoufox can't clear, persistent + reused via a run
  latch. The 0.1.2 HTTP avoidance is retained on the opt-in HTTP path. **Whether
  either headful engine clears FWN's *current* live Cloudflare is unconfirmed** —
  this matches the legacy working shape but needs a live pass over a known-bad
  chapter (e.g. 102); the log will show which engine clears it. A visible browser
  window WILL appear during a FWN scrape (expected). If neither clears, the next
  levers are a **residential proxy**, a **manual solve** in the visible window, or
  `nodriver` (strategy 3, not yet wired). If the visible browser reaches valid HTML
  but extraction fails, that is a selector/timing issue, not browser mode.
- Both enabled adapters (`freewebnovel`, `webnovel_dynamic`) are implemented;
  `empire_novel`, `novel_bin`, `telegraph` remain intentional disabled stubs for
  a later version.
- Six Minor/Suggestion items from the Phase 8 hunt were flagged for review in
  `files/test-logs/v0.1.0_pre-release.md` (Issues Found table). Items #1 and #2
  were fixed in the prior follow-up; #3-#6 remain deferred/awaiting user
  direction.

## Next Steps
- **0.2.0 manual live pass (do this first â€” §7 of the plan).** **Both passes: HTML
  cache OFF + a fresh output folder** (the cache is keyed by slug and lives outside the
  output folder, so a fresh `shadow-slave-N` folder alone does NOT force a fresh fetch â€”
  a cached chapter would prove nothing).
  - **Pass A â€” detector baseline (headful).** Shadow Slave / Free Web Novel, **Headless
    OFF**, **HTTP-first OFF**, **cache OFF**, delay 3.0, chapters 100â€“110, Separate,
    fresh folder. *Acceptance:* ch-102 succeeds on the **first visible camoufox attempt**,
    **not** rescued.
  - **Pass B â€” headless architecture.** Same but **Headless ON**, chapters 100â€“110, cache
    OFF, fresh folder. Watch whether the headless primary clears chapters, whether the
    breaker trips and switches to a visible browser, and whether any watch-list chapter
    enters/exits the single rescue lane. Regression watch list (NOT assumed hard):
    102, 175, 233, 268, 271, 278, 307, 330, 334, 379, 403, 489, 508, 517, 518, 534, 546,
    587, 629, 633, 642, 715, 853, 860, 951, 998, 1039, 1251, 1521, 1897, 2018, 2142, 2420,
    2817, 3019, 3062.
- **After acceptance:** tag 0.2.0, force-push as one release, and (per the plan) keep the
  instruction drop until the user says otherwise. 0.2.1 (the user-selectable 1–5 rescue
  worker toggle) may only start once 0.2.0 is accepted with either a genuine hard chapter
  having entered+exited the single rescue lane live, or the user explicitly accepting the
  offline rescue-lifecycle evidence.
- **Still deferred:** the six Minor/Suggestion items from the Phase 8 hunt (#3â€“#6 open),
  and the cosmetic "Use browser mode" checkbox rename.

### Post-0.1.0 cleanup backlog
Deferred tidy-ups to do AFTER the 0.1.0 live pass and force-push (not before --
see the note at the end of this list):

1. **~~Drop `playwright-stealth` and the unreachable Chromium-stealth code.~~
   CANCELLED / SUPERSEDED (2026-06-28).** The 1–3065 live stress-scrape proved
   camoufox **insufficient** against a real FreeWebNovel Cloudflare challenge, so
   strategy-1 Chromium playwright-stealth was wired back into the live ladder as the
   last-resort rescue rungs (`… → camoufox_fresh → playwright_stealth →
   playwright_stealth_fresh`). `playwright-stealth` is now **required**, not
   droppable. Do not remove it.
2. **Rename the GUI "Use Playwright browser mode" checkbox** (`app.py`) and its
   mirror in `README.md` to a neutral "Use browser mode" label — the browser path
   now spans camoufox *and* Chromium playwright-stealth, so "Playwright browser
   mode" is imprecise. (Cosmetic; still open.)
3. **~~Update the stale `cf_bypass.py` ladder-description comments.~~ DONE
   (2026-06-28).** The cf_bypass docstring now documents the live ladder order, and
   the request_manager ladder comment is current. The old "Playwright stealth ->
   fresh Playwright context" comments no longer exist.

**Note:** the earlier "keep strategy-1 through the live pass in case camoufox
fails" caveat has now played out exactly — camoufox failed live, strategy-1 is the
wired rescue. Whether strategy-1 itself clears a live FWN challenge is still
unproven (see Known Issues).
