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
  retries, exponential backoff + jitter, and the ordered Cloudflare escalation
  ladder `http -> cloudscraper -> camoufox -> camoufox_fresh -> playwright_stealth
  -> playwright_stealth_fresh` (camoufox leads the browser rungs; the Chromium
  playwright-stealth rungs are the last-resort rescue after a live FWN challenge was
  found to defeat camoufox). Only one browser engine is live per thread — the fetch
  methods tear the other engine down before starting one, so the ladder can walk
  from camoufox into the stealth rungs within a single chapter's attempts. Plus an
  on-disk HTML cache and the Cloudflare-aware browser path (`cf_bypass.py`).
- `cloudflare_detection.py` â€” the one shared `is_cloudflare_challenge` detector
  imported by both `request_manager` and `cf_bypass` (so they cannot drift). Strong
  interstitial markers flag immediately; ambient beacon markers clear only on
  *structural* payload evidence (`__NEXT_DATA__`, `g_data.chapInfo` with non-empty
  contents, or a real chapter-body container) â€” never by page length.
- `pdf_builder.py` â€” the one ReportLab layout (Letter, Times-Roman body, Helvetica-Bold
  headings, one chapter per page).
- `pipeline.py` â€” TOC-first, resumable orchestration over the three output modes,
  with adaptive auto-slowdown (`_Pacer`) and a second-pass sweep over failed
  chapters (Phase 9).
- `app.py` â€” the tkinter GUI entry point.
Networking never lives in parsers; parsing never lives in the GUI; the PDF builder
knows nothing about sites. `scripts/verify.py` is the mechanical gate (pytest +
dependency-pinning + docs-freshness) run before every phase commit; it runs the
suite in `files/tests/` (a `conftest.py` there puts `scripts/Universal/` on
`sys.path` so `import webnovel_scraper` resolves).

## Current Version
0.1.1 (release-ready â€” pending the user's manual live-scrape pass + tag/force-push).
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

## Known Issues
- **WebNovel camoufox rescue (was the open Critical):** the most likely root cause
  â€” over-eager challenge detection mis-flagging cleared post-redirect pages â€” is
  **fixed and proven offline** (9D). Honest caveat: whether camoufox actually
  clears a *genuinely* challenged WebNovel chapter on a given day is live-site
  dependent and can only be confirmed by the user's manual pass; the code can no
  longer fail a chapter that camoufox *did* clear, and intermittent failures now
  get the relentless ladder + second-pass sweep instead of an immediate skip.
- **FreeWebNovel Cloudflare bypass — OPEN (camoufox proven insufficient live).**
  The 1–3065 stress-scrape hit a real FWN managed challenge and **camoufox cleared
  none of it** (chapters 102, 174, … all failed every camoufox attempt). Scraper
  *resilience* is solid (blocked chapters retry the full ladder, are recorded/
  skipped, and the run continues; the end-of-run sweep re-tries them), but the
  *bypass* is not yet proven: the Chromium playwright-stealth rescue rungs are now
  wired in after camoufox as the intended fix. **Whether stealth actually clears a
  live FWN challenge is unconfirmed** — needs a fresh live pass over the known-bad
  chapters. If stealth also fails, the next levers are headful mode, a residential
  proxy, or `nodriver` (strategy 3 in `cf_bypass.py`, not yet wired).
- Both enabled adapters (`freewebnovel`, `webnovel_dynamic`) are implemented;
  `empire_novel`, `novel_bin`, `telegraph` remain intentional disabled stubs for
  a later version.
- Six Minor/Suggestion items from the Phase 8 hunt were flagged for review in
  `files/test-logs/v0.1.0_pre-release.md` (Issues Found table). Items #1 and #2
  were fixed in the prior follow-up; #3-#6 remain deferred/awaiting user
  direction.

## Next Steps
- **User's manual live pass** (the only thing left for 0.1.0): run the live
  scrapes the release log structures but leaves skipped â€” 3 FreeWebNovel chapters
  across all three output modes (with browser mode for Cloudflare and at least
  one uncached chapter to validate the retry ladder), 3
  WebNovel-dynamic chapters, confirm resume skips existing PDFs, Stop cancels
  cleanly, and output lands in `~/Downloads/{slug}-N` (or a custom browsed folder).
- After the live pass: decide on the six flagged Minor/Suggestion items, tag
  0.1.0, force-push the repo as one release, and delete the implementation-plan
  instruction drop.

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
