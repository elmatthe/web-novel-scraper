# webnovel-scraper - Handoff

## Current Focus
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

No blocking Critical issues. Item **#7 is mitigated/closed for scraper resilience**
(fatal halt/mass-failure behavior fixed; live Cloudflare success still requires
manual validation). Items **#1 and #2 are fixed** (user-approved follow-up,
2026-06-27); **#3â€“#6 remain deferred** (Suggestion-level, awaiting user
direction). Full detail for the older Phase 8 findings is in
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
