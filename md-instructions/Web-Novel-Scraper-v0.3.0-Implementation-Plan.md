# Web Novel Scraper — v0.3.0 Implementation Plan

**Status:** Approved product requirements. Checkpoint 00 (planning/baseline) complete 2026-10-17 on `feature/v0.3.0-modernization`; Checkpoint 01 step 01.0 (flat `scripts/` layout) complete 2026-10-17; step 01.1 (Light/Dark UI foundation and three-section window shell) complete 2026-10-18; step 02.1 (Activity Summary/Detailed logging and thread-safe event bridge) complete 2026-10-18 (`d7116db`); step 02.2 (progress, ETA, counters and run-state accuracy) complete 2026-10-18 (`3269aa7`); step 02.3 (website-first selection, preferences, Resume placeholder and counter fix) complete 2026-10-18 and approved by the owner for Windows (Light/Dark, minimum size, simulated 150%, Website menu), with launcher, native-scaling and macOS checks carried forward; step 03.0 (source identity, safe run allocation, manifest/journal contracts) and 03.1 (chapter manifest store, legacy import) are complete and approved; step 03.2a (manifest preflight, discovery and pipeline integration) is complete and approved 2026-10-18, subject to the owner overrides recorded in §10 (End auto-clamp, automatic cache/list updates, Windows-only release); 03.2b onward is executed autonomously against this plan (§10). Technical clarifications from Checkpoint 00 are in §8 and do not change owner decisions.  
**Created:** 2026-10-14  
**Planning / review:** ChatGPT; **execution:** Claude Code in the local repository.  
**Target repository:** `https://github.com/elmatthe/web-novel-scraper`  
**Target instruction drop:** `md-instructions/Web-Novel-Scraper-v0.3.0-Implementation-Plan.md`

## 1. Goal and release contract

Ship a stable, accessible Windows/macOS Tkinter desktop application that visually matches the author's Audiobook Creation Tool (ACT) / PDF-Splitter, has intuitive web-novel/site selection, reliable cache-first chapter discovery, consistent TXT/PDF outputs, and supports *Lord of the Mysteries* and *Lord of Mysteries 2: Circle of Inevitability* on individually verified `.net`/`.com` sources. Preserve legitimate text without site clutter or unwanted prose edits. Deliver v0.3.0 only after the release gate and explicit owner approval. **Owner override 2026-10-18 (ADR 013): the v0.3.0 release gate is Windows only; macOS acceptance is deferred to a later owner-directed branch (§10).**

**Implementation order is mandatory:** first complete and approve the UI (checkpoints 01–02); only then implement chapter-list/cache changes, new sources, and content/output refactors (03–05). Reliability and release gates come last. Work in small, independently verified commits on a working branch; do not merge, tag, publish releases, or rewrite Git history without separate approval.

### Repository baseline (read-only GitHub inspection, 2026-10-14)

- `main` HEAD: `e7dc407d096f09446e8135eac702f25ad6941ad8` (2026-07-21). Latest visible GitHub release is v0.2.0; `md-instructions/CHANGELOG.md` marks v0.2.1 **Unreleased**. Re-check live Git state when starting; do not assume v0.2.1 formally released.
- GUI: `scripts/Universal/app.py` (one vertical control pane, single scrolled log, basic progress). Core: `scripts/Universal/webnovel_scraper/` (`catalog.py`, `models.py`, `pipeline.py`, `pdf_builder.py`, adapters, request manager, recovery machinery). Runtime code is under `scripts/`, tests/fixtures under `files/`.
- Current enabled catalog: Shadow Slave, Reverend Insanity, Renegade Immortal, Supreme Magus on `freewebnovel.com`; The Noble Queen on dynamic WebNovel. Empire Novel, Telegraph, Novel Bin are intentionally disabled stubs; do not enable incidentally.
- Existing protection: per-site adapter contract, Cloudflare retry/rescue, cancellation, atomic PDF writes, index identity checking, schema v2 for displayed numbering versus URL slugs, fractional insert chapters, bounded chapter walks, and regression tests. Maintain these safeguards.
- Relevant prior source files: `md-instructions/Briefing.md`, `md-instructions/handoff.md`, `md-instructions/DECISIONS.md`, `md-instructions/CHANGELOG.md`, `md-instructions/0.2.1_release_plan.md`, `md-instructions/0.2.1_bug-hunt.md`. These contain old/unresolved instructions; reconcile against current Git and the approved v0.3.0 requirements instead of blindly executing old plans. Preserve existing casing rather than renaming historical docs incidentally.
- Repository tree inspected does **not** show `AGENTS.md` or `AI-WORKSPACE.md` at the root. The owner separately supplied `AI-WORKSPACE.md` and `Vibe-coding-chat-workflow.md` for this planning work. Check whether those rules are present locally before assuming local agents load them. Respect their essential scope, containment, checkpoints and Git approval rules; do not undertake a general repository-governance migration as a side effect.
- Reference UI sources: `elmatthe/PDF-Splitter` at `scripts/pdf_splitter/app.py`, `appearance.py`, `activity.py`, `settings.py`; `elmatthe/audiobook-creation-tool` at `scripts/Universal/shared/appearance.py`, `job_control.py`, `job_ui.py` and relevant window examples. Use design patterns, not wholesale copying of unrelated application engines.

### Verified baseline (Checkpoint 00, local repository, 2026-10-17)

Supersedes the read-only GitHub snapshot above wherever they differ.

- `origin/main` = `main` = `e7dc407` (fetched; no newer remote commits). Latest GitHub release/tag: **v0.2.0** (tag → `5127b38`, released 2026-06-30). Tags: `v0.1.0`, `v0.1.1`, `v0.2.0`. **0.2.1 is unreleased**: merged to `main` via PR #4 (`68d1dcc`), never tagged; its plan's live Phase B/C reproductions and Phase F release never ran. v0.3.0 supersedes it (see §6 and §8 C3).
- **Working branch `feature/v0.3.0-modernization` starts at `577b5db`** (owner-approved 2026-10-17), not bare `main`: `577b5db` is `main` plus one previously local-only, unpushed commit on `fix/freewebnovel-offset-and-output-collisions` (FWN offset reconciliation by binary search, incremental `chapter_index.json` checkpoints, distinct outputs for repeated displayed numbers, TOC-gap reporting, URL-only rescue dedupe; 292 tests and a live Supreme Magus `1-5000` acceptance recorded in `handoff.md`). These are baseline behaviour from here on (§8 C1).
- **Verification baseline:** `python scripts/verify.py` (repo `.venv`, Python 3.13.12; all 10 pinned requirements already installed) at `577b5db` in a clean temporary worktree: **PASS — pytest 292 passed in 6.87 s; deps PASS; docs PASS.** (Run outside the main working copy only because of the uncommitted layout move below, which breaks in-place test imports until step 01.0.)
- **Uncommitted local layout move (owner-approved for adoption, §8 C2):** this checkout has `scripts/Universal/*` moved to `scripts/*` with byte-identical contents, `scripts/Windows/.gitkeep` and `scripts/MacOS/.gitkeep` deleted, and `md-instructions/0.2.1_release_plan.md`, `0.2.1_bug-hunt.md`, `Decisions-template.md` deleted. Committed code still lives at `scripts/Universal/`; step 01.0 commits the flat layout. After 01.0, every `scripts/Universal/...` path in this plan means `scripts/...`.
- Enabled catalog confirmed (`catalog.py`): Shadow Slave, Reverend Insanity, Renegade Immortal, Supreme Magus on `freewebnovel` (`.com`, `use_browser=True`); The Noble Queen on `webnovel_dynamic`. `empire_novel`, `telegraph`, `novel_bin` remain disabled stubs.
- Tracked doc names are `md-instructions/Briefing.md`, `CHANGELOG.md`, `DECISIONS.md`, `handoff.md` (this Windows checkout displays `Changelog.md`/`Decisions.md`/`Handoff.md` because `core.ignorecase=true`; Git keeps the tracked casing and `verify.py` reads `CHANGELOG.md`). Do not rename them. `AI-WORKSPACE.md` is present locally but gitignored on purpose (`1184a12`); `AGENTS.md` and `.ai/` are absent. No governance migration.
- Reference UI copies are supplied locally (untracked, **never commit**, delete after use) under `scripts/scripts-to-use-from-my-other-repo's-GUIs/`. ACT copies match GitHub `master` `62b0c09`. PDF-Splitter copies are a **local working copy newer than GitHub `main` `edcbd4f`**: `appearance.py` and `launcher.py` match, but `app.py`, `activity.py`, `settings.py`, `jobs.py` and `dialogs.py` differ, and `gui_runtime.py` is not on GitHub. Borrow from the local copy and record provenance (§8 C5).

## 2. Locked decisions and UX defaults

1. **Appearance/layout:** ACT/PDF-Splitter's compact bordered `ttk` sections, semantic Light/Dark palettes, state-preserving theme toggle, uniform buttons/fields, scalable layout and bottom status/preferences controls. PDF-Splitter is the primary single-window shell reference (ACT's navigation sidebar is not required). Target both Windows and macOS, with platform-native behavior where needed.
2. **Three numbered control sections on the left:** **1. Web Novel Selection** (website, novel, start/end chapters, available coverage); **2. Output Options** (independent PDF/TXT checkboxes and **exactly one** grouping choice: Individual Chapters / Whole Selected Range / Every X Chapters); **3. Run** (output location, Start/Cancel, progress/counters/phase/ETA, Resume Existing Run entry). A right-side **Activity** panel spans the control stack and offers **Summary** and **Detailed** tabs, persistent technical session logs and Clear Log. No extraneous tool sidebar.
3. **Startup defaults:** PDF **on**, TXT **off**, grouping **Individual Chapters**; never permit both formats off. Retain currently selected settings across appearance changes. Persist appropriate ordinary user preferences without caching sensitive browser/session credentials. A blank end means through the discovered available end; validate start/end before work.
4. **Output root:** `Path.home() / "Downloads" / "Web-Novel-Scraper-Outputs"`. Each **new** run gets `<canonical-novel-name>-1`, `-2`, ... (first free positive integer), **per novel independent of site**, e.g. `lord-of-the-mysteries-1`; reserve the directory safely to avoid races/overwrites. TXT and PDF files may be stored in clearly labeled `TXT/` and `PDF/` children of the run folder, with deterministic chapter/range names; any resume metadata stays in that run folder. Only an explicit **Resume Existing Run** action reuses an earlier folder after validation. Do not put public book outputs under `files/` or `scripts/`.
5. **Site choices:** Add *Lord of the Mysteries* and *Lord of Mysteries 2: Circle of Inevitability* (display canonical title) and expose separate selectable **Free Web Novel (.net)** / **Free Web Novel (.com)** choices for each **only if that novel/site pairing can be validated**. The user's requested `.net` chapter samples are:
   - `https://freewebnovel.net/lord-of-the-mysteries/chapter-1-crimson.html`
   - `https://freewebnovel.net/lord-of-mysteries-2-circle-of-inevitability/chapter-1-1-foreigners.html`
   The `.com` catalogue candidates requiring live validation are:
   - `https://freewebnovel.com/novel/lord-of-the-mysteries`
   - `https://freewebnovel.com/novel/lord-of-mysteries-2-circle-of-inevitability`
   The user's two exact `.net` URLs were **not independently retrievable** in planning (search/browser cache miss); `.net` is not declared dead. Collect live site evidence during checkpoint 04. An unverified/unavailable choice is clearly disabled or marked unavailable, with reason. Do not silently replace `.net` with `.com` or mix chapter bodies, editions, and numbering within a run. User may start separate runs from either available source. Preserve existing `.com` adapter identity and use separate site/manifest identities for `.net` where necessary.
6. **Browser policy:** For browser-required sources, attempt a **short, bounded headless browser** path first; if challenged/blocked/invalid, escalate to **visible Playwright-controlled browser** (headed, local device), allowing manual interaction with site challenges if necessary. Record why fallback occurred; preserve cookies/session within the active run where appropriate; enforce timeouts, cancellation and resource teardown. Do not repeatedly launch hidden retries or claim manual interaction succeeded without confirmation. Preserve tested Camoufox/Playwright recovery as compatible fallbacks until a verified replacement proves equivalent. For sites already supported by reliable direct HTTP (e.g. dynamic WebNovel), continue efficient HTTP-first behavior; do not launch a browser without benefit. Avoid automatic solver services, paid proxies, unauthorized installs or excessive requests.
7. **Manifest-first:** Validated chapter indexes and source-specific HTML cache are normal workflow, not an optional expert mode. Track curated, verified baseline JSON chapter manifests under `scripts/webnovel_scraper/chapter_lists/<site-key>/<novel-slug>.json` (a shipping runtime asset; path updated for the flat layout adopted at 01.0 — originally `scripts/Universal/webnovel_scraper/...`); store newly discovered/extended mutable lists under ignored `files/runtime-data/chapter-lists/`, and HTML/request cache in an ignored repo-contained runtime location. Exact names/migration are implementation details; respect repository containment and existing cache migration. Do not write runtime updates into tracked baselines automatically.
8. **Text policy:** Keep real narrative, paragraph boundaries, numbers, meaningful Unicode punctuation, non-English letters and genuine author material. Strip invisible/control/garbled Unicode carefully (e.g. BOM/zero-width artifacts), not all non-ASCII characters. Remove source HTML chrome, reader comments, next/previous controls, translator/editor credit lines (including combined forms), watermarks when clearly identifiable, and duplicate/reworded chapter heading **only when structurally/confidently identified**. No novel-specific prose corrections, dictionary edits, profanity removal, stylistic rewriting, generic short-paragraph filters or broad substring deletion of valid narrative.
9. **Canonical heading:** Exactly one `Chapter N: Title` line per chapter in both TXT and PDF, no forced period. Preserve legitimate fractional chapter numbering (`Chapter 627.2: Title`). If title is unrecoverable, use a deterministic explicit fallback such as `Chapter N: Untitled` **and surface a warning** rather than fabricating the author's title. Source page may label chapter `1 - 1` or differ from URL slug: derive the displayed chapter identity from validated source evidence, never naive URL guesses.
10. **PDF style:** Preserve the existing ReportLab print style unless a focused verified correction is needed: US Letter, 0.5-inch margins, Times-Roman 11pt/15pt justified body, Helvetica-Bold 14pt/18pt heading in `#134252`, chapter begins on a new page, prevent orphaned heading-only pages, atomic file publication. TXT must contain the same normalized text and paragraph sequence and use UTF-8. Choosing both must not fetch/clean each chapter twice.

## 3. Architecture / data contracts

### Manifest and cache ownership

- `SiteSpec`/catalog remains the product catalogue and disabled-site guard; site adapters discover and extract; a new or refactored **ChapterIndexStore** owns versioned, source-identified manifest I/O, validation, runtime overlay and refresh. Request manager owns fetched HTML cache. Pipeline orchestrates preflight, fetching, normalization, rendering and run reporting. GUI stays thin and Tk-thread-safe.
- Manifest schema must include schema version, novel slug, adapter/site key, source/index URL, generation and validation timestamps (where applicable), exact chapter entries `{display_number, url, title, source_id, url_slug_or_equivalent}`, sorted ordering, provenance/confidence where needed, and **coverage of the requested range**. Entries must round-trip fractional chapter numbers losslessly (avoid lossy float serialization for numeric labels), avoid duplicate chapter identities/URL collisions (identity = source URL/slug, not displayed number; see §8 C8), and validate safe URL host/scheme.
- Coverage is **set membership**, not `min/max` only. When user requests chapters outside or inside gaps of a validated manifest: refresh/discover/extend missing coverage before content fetching; preserve verified entries; reject stale, mismatched, corrupted or contradictory data with actionable messages. Missing requested chapters or uncertain mapping must not be silently clamped into an apparently successful run. *Owner override 2026-10-18 (ADR 011):* an End past the last chapter a fresh, complete discovery lists **is** clamped to that chapter, visibly (Activity, report, `effective_range` beside `requested_range`); everything else that cannot be confirmed still refuses (§9.3). An empty first-run store must automatically generate a manifest covering the selected range, not require manual preparation. Distinguish source actually lacks a chapter from cache merely lacking it. Respect legitimate fractional/bonus entries and show the effective selected range.
- HTML cache validity: never serve Cloudflare/challenge pages, garbled/truncated files or wrong novel/site identity as chapter HTML. Retain pre-existing backoff and recovery behavior, avoid re-fetching valid cached HTML, and provide explicit refresh/invalidate behavior when source changes. Prefer atomic JSON/cache writes and bounded update/repair paths. Keep a deterministic, documented conversion for any old output-scoped `chapter_index.json` (schema v2) and run metadata, without cross-site contamination or destructive cleanup.
- Output run manifest/journal captures source identity, selected chapter set, grouping, requested output formats, completed/published outputs and failures; **Resume Existing Run** refuses mismatched novel, site, range/grouping/format semantics unless a clearly supported safe compatibility path exists. Only skip validated complete output artifacts; never trust filename presence alone. New run naming never overwrites or nests runs.

### Unified output pipeline

`site-specific HTML → ChapterMeta + extracted paragraph content → one deterministic normalize/clean pass → canonical ChapterContent → TXT and/or PDF renderers → atomic publication + run report`.

- Move cross-site normalization away from adapter-only novel-specific heuristics. Site adapters are responsible for extracting page structure, not guessing legitimate prose to delete. Remove or narrow broad `NOISE_SNIPPETS` and short-text filters that risk deleting story paragraphs; explicit fixtures should prove preservation.
- Duplicate **heading** logic must work when chapter title wording differs (e.g. `Chapter 1 - Crimson` vs. `Chapter 1: Crimson`, or an extra header bearing the same book and chapter), without deleting real repeated dialogue/prose. Do not over-aggressively deduplicate body text across chapters; detect a duplicate/mismatched chapter number and report it rather than silently publishing the wrong content.
- Render output groups predictably; only one grouping mode is active for both formats. `Every X chapters` requires positive integer X. Combined TXT/PDF for a chosen grouping share boundaries and chapter ordering. Handle partial failures explicitly: prevent a 'successful full book' assertion if chapters are missing.
- Public output and status vocabulary: discovered, cached, fetched, rendered, already complete/resumed, skipped (reason), failed (reason), cancelled, final counts. Detailed tab logs HTTP/cache/retry/challenge progress; Summary tab logs milestones, recoveries requiring attention, and final results. Technical logs are session-scoped in ignored runtime data; no novel body text or secrets in logs.

### Progress, responsiveness and platform behavior

- Styled determinate progress matches PDF-Splitter: stages `Preparing chapter list / Scraping / Building output / Complete`, chapter `completed/selected`, failed/skipped/resumed counts, percentage, current chapter, and ETA where supported; unknown ETA is shown as `—`, never a fabricated time. Count terminal chapter outcomes exactly once; distinguish `100% processed` with failures from `100% successful`.
- Right-side Activity has Summary/Detailed tabs and a capped, thread-safe queue/pump. Preserve sequence of user-relevant events, coalesce repetitive routine progress if necessary. Never invoke `tk` from worker threads. Run controls and close/cancel remain responsive while I/O or browser waits occur.
- Window widths/heights derived from widgets and display scaling (PDF-Splitter approach), not fixed oversized minima. Controls remain usable when restored from maximized; Activity absorbs extra width. Test typical laptop widths, scaling, very long novel/site names, Light/Dark live toggles and native Windows/macOS themes. No whole-window horizontal overflow.
- Preferences & Data may contain advanced timeout/delay/browser/cache maintenance so primary workflow remains uncluttered, but existing effective settings remain accessible. Only add settings the app actually supports; persist safe stable preferences and test stale/corrupt settings recovery.

## 4. Bounded implementation checkpoints

### 00 — Orientation, baselines, plan authority (PLANNING ONLY)
- Read necessary current repo source/docs and reference UI modules; verify `git status`, branches, SHA, and v0.2.1 unreleased status. Identify existing tests/requirements, platform launchers and known problems; reconcile historical docs without implementing their unrelated old releases.
- Create `feature/v0.3.0-modernization` from clean current `main` (or explain if unsafe). Place this approved plan at its target `md-instructions/` path and update current handoff with branch, baseline evidence, checkpoints and next action. Do **not** change application/runtime code or requirements in this checkpoint.
- **Verify:** Run existing `python scripts/verify.py` (or documented `.venv` equivalent) once if dependencies are already installed; report exact results and any environment limitation. Commit/push the plan checkpoint on its feature branch only, when authorized by workspace rules.
- **Result (2026-10-17): COMPLETE.** Branch `feature/v0.3.0-modernization` created at `577b5db` (owner-approved base); verify PASS (292 passed); plan finalized with §1 verified baseline and §8 clarifications; `handoff.md`, `Briefing.md` (current-version status) and `CHANGELOG.md` (`577b5db` entries) reconciled. No application code, requirements or environment changed.

### 01 — Visual/theme foundation (UI ONLY)
- **01.0 — Flat `scripts/` layout (owner-approved 2026-10-17; separate commit, no behaviour change, before any UI work).** Commit the existing local move `scripts/Universal/*` → `scripts/*` (contents byte-identical to `577b5db`) plus the `scripts/Windows/.gitkeep` / `scripts/MacOS/.gitkeep` deletions, then repair every dependent path: `files/tests/conftest.py` (`sys.path` → `scripts/`); `MAIN_SCRIPT` in `Setup_and_Run-Web-Novel-Scraper.bat` (`scripts\app.py`) and `.command` (`scripts/app.py`) plus their header comments; repo-root derivation in `webnovel_scraper/request_manager.py` and `webnovel_scraper/browser_env.py` (`parents[3]` → `parents[2]`, otherwise the HTML cache and Playwright browsers resolve **outside** the repository); stale path comments in `app.py`; `files/bypass_eval.py` `sys.path`; README/Briefing architecture text. Commit the local deletions of `0.2.1_release_plan.md`, `0.2.1_bug-hunt.md` and `Decisions-template.md` in the same step unless the owner objects (their open items are carried in `handoff.md`). Stage moved paths explicitly — **never `git add scripts/` or `git add -A`** while the untracked reference folder exists. **Verify:** full gate green at ≥292 passed, plus a new regression test asserting `DEFAULT_CACHE_ROOT` and `CONTAINED_BROWSERS_PATH` resolve inside the repo; `git diff -M --stat` shows renames, not rewrites; one GUI launch via the launcher's interpreter path (smoke).
- Adapt compact ACT/PDF-Splitter appearance tokens/styles and preferences to the scraper, preserving existing `ttk` behavior, Mac Aqua differences and state-preserving Light/Dark switching. Rebuild the shell with exactly three bordered left sections and right Activity area; preserve existing functional controls in appropriate sections before moving them to advanced settings.
- **Verify:** focused theme/layout tests; manual **Windows UI visual gate** (Light/Dark, restored/maximized, scaled/min-size, all sections legible) and initial macOS smoke where available. No scraper/data/pipeline behavior changes. Owner approval of the UI direction before proceeding.

- **01.0 Result (2026-10-17): COMPLETE.** The move was committed as 20 renames (17 unchanged; `app.py`, `browser_env.py` and `request_manager.py` carry only the path-line repairs) plus the `.gitkeep` deletions, and every dependent path listed above was repaired (README had none). `files/tests/test_flat_layout.py` adds 8 offline guards: import root, isolated `scripts/app.py` load, in-repo cache and browser paths, both launchers' `MAIN_SCRIPT`, and a fixture chapter → real parser → pipeline → PDF. In-place verify PASS: **300 passed** (292 + 8). Startup smoke test via `.venv\Scripts\pythonw.exe scripts\app.py` passed. **Owner decision:** the three `md-instructions` deletions were *not* committed; they are deferred to a separate documentation-cleanup decision, which overrides this step's "unless the owner objects" clause. Also fixed: Checkpoint 00's Briefing status note put "v0.3.0" first in `## Current Version` and failed two `test_release_metadata.py` guards.
- **01.1 Result (2026-10-18): COMPLETE, awaiting owner review.**
  - **Appearance:** new `scripts/webnovel_scraper/ui/appearance.py` (PDF-Splitter `appearance.py`, local copy, identical to `edcbd4f`; Treeview/Spinbox/Inline styles dropped; a Combobox-matched `Compact.TMenubutton` added for the Website picker).
  - **Settings:** new `ui/settings.py`, schema v1, appearance only, saved in `files/runtime-data/settings.json`, which is now gitignored per C6.
  - **Shell:** `app.py` rebuilt with three bordered sections on the left, Activity (the existing log) on the right, a status bar with the Light/Dark toggle, and `_fit_window` measured geometry.
  - **Advanced settings:** delay, timeout, browser, headless, HTTP-first and cache moved, unchanged, into an Advanced Settings window built at startup.
  - **Unchanged:** the run lifecycle, job config, `DEFAULT_HEADLESS`, output naming and the catalog.
  - **Tests:** C10 guarantees carried forward; 47 tests added; new `files/tests/pytest.ini` (`--capture=sys`) removes an intermittent Tk start-up skip caused by pytest's fd capture.
  - **Gate:** in place 347 passed, 0 skipped; GUI/UI tests also pass on Python 3.12.10; startup smoke test passed.
  - **Pending:** the manual Windows visual gate and the macOS smoke test.
  - **Deferred to 02:** website-first selection, Summary/Detailed tabs, Clear Log, ETA and broader preference persistence.

### 02 — Functional UI, logs and progress (UI/CONTROLLER ONLY)
- Wire site/novel/range selections, independent format selections (PDF default; TXT may be disabled with explanation until 05), mutually exclusive grouping, source availability, output preview, normal Start/Cancel and explicit Resume affordance. Implement Activity Summary/Detailed + technical logging, styled phase-aware progress, safe thread-pump and persistence. An unimplemented control must be visibly disabled, not pretend to work.
- **Verify:** UI/controller tests for event ordering, log filtering, no Tk calls off-thread, invalid selections, theme state retention, close/cancel, resize/compact layout, no fabricated successful TXT output. **Second manual UI approval gate**. Do not start checkpoints 03–05 before approval.

- **02.1 Result (2026-10-18): COMPLETE, awaiting owner review.** UI/controller only. No scraper, adapter, pipeline, PDF, cache, catalog, dependency or default changes.
  - **Activity:** Summary/Detailed `Compact.TNotebook` with Clear Log (display only). Warnings and errors are labelled and coloured in Light and Dark. A view follows new lines unless the user has scrolled up, and each tab keeps its history across tab and appearance switches.
  - **Event bridge:** new `ui/activity.py`, adapted from the PDF-Splitter local copy (C5). The worker never calls Tk: the pipeline's `log`/`progress_cb` and the outcome only `emit` onto a bounded locked channel, which an always-on Tk-thread `ActivityPump` drains in bounded slices. Progress coalesces; warnings, errors, Summary lines and the run's finish are never dropped. `destroy()` stops the pump and refuses later events.
  - **Pipeline text:** new `ui/run_activity.py`. 19 literal markers, copied from existing messages and guarded by a source-drift test, lift warnings, recoveries and milestones into Summary; every raw line stays in Detailed.
  - **Outcome:** built from the returned `RunReport`, never the percentage.
  - **Session logs:** one per session in ignored `files/runtime-data/logs/` (C6). Written at emit, so the log is complete and in true order (ADR 006). Redacted and capped; a failure is one warning.
  - **Fixes:** Stop and Close report before signalling cancel.
  - **Tests:** 90 added; C10 guarantees unchanged and passing; the shell's worker check is stricter.
  - **Gate:** 437 passed, 0 skipped (three runs); 149 GUI/UI tests pass on 3.12.10; 11/11 mutations caught; startup smoke test passed (session log created and closed).
  - **Pending:** the Activity visual check and one live run (owner).
  - **Next (02.2):** phase-aware progress, counts, ETA (`—` when unknown) and run states that tell "100% processed with failures" from "100% successful". Done, below.

- **02.2 Result (2026-10-18): COMPLETE, awaiting owner review.** UI/controller plus one observability-only pipeline hook (DECISIONS.md #007). No scraping-algorithm, adapter, PDF, cache, catalog, output-folder, dependency or default changes.
  - **Run section:** phase (Preparing chapter list / Scraping chapters / Building output / Stopping, then Complete, Finished with problems, no PDFs, Stopped, Failed, Refused), an honest indeterminate bar until the total is known, `done / total · %`, counters (already present live; PDFs, failed, recovered after the end from `RunReport`) and an ETA (`—` when unknown). New `ui/run_progress.py`; `format_eta` adapted from PDF-Splitter.
  - **Accuracy:** counts are selected source pages, never PDFs, and never double-count retries, rescue or distinct pages sharing a displayed number. 100% processed is not success; a late routine update cannot overwrite the final state; an open-ended end is never reported as truncated; explicit-end shortfalls and site-list gaps are reported.
  - **Instrumentation:** optional `run_scrape(..., phase_cb=None)`; behaviour-neutral (tested with and without it).
  - **Layout:** one extra row; minimum window 635×590 (was 635×571), width unchanged.
  - **Gate:** 554 passed, 0 skipped; 305 UI/pipeline tests pass on 3.12.10; 12/12 mutations caught; startup smoke test passed.
  - **Deferred:** a live current-chapter name and live failed/recovered counters; Resume Existing Run; website-first selection.
  - **Pending:** the Run-section visual check (Light/Dark, minimum size, 125%/150%) and one live run (owner).

- **02.3 Result (2026-10-18): COMPLETE, awaiting owner review (UI acceptance gate).** UI/controller only: no scraper, adapter, catalog, PDF, TXT, cache, manifest, output-folder, headless-default or dependency change. A new decision, DECISIONS.md #008, records that a website is a host, not an adapter.
  - **Section 1:** Website, Novel, Start chapter, End chapter. Novel choices follow the website; "coming soon" websites are listed but cannot be chosen; the novel is kept across a website change when offered. Start passes the same catalog row's novel and adapter. New `ui/selection.py`.
  - **Resume Existing Run:** shown disabled with an explanatory note, wired to nothing (Checkpoint 03 builds it).
  - **Preferences (settings schema 2):** appearance, website, novel, grouping, chunk size, delay, timeout, browser, headless, HTTP-first, cache; saved at discrete moments; schema-1 files upgrade on the next save; invalid or unavailable values fall back safely; defaults unchanged (headless off).
  - **Fixes:** the final counters wrap instead of clipping; "coming soon" websites are legible in the Dark menu on Windows.
  - **Window:** minimum 635x632 at 100% (was 635x590), width unchanged.
  - **Gate:** 679 passed, 0 skipped (554 + 125); 420 UI tests pass on 3.12.10; 15/15 mutations caught; startup smoke test passed.
  - **Pending (owner):** the UI acceptance gate (Light/Dark, minimum/maximized, 125%/150%), one live run, macOS Aqua and the `.bat` double-click.
  - **Next:** Checkpoint 03 (manifest-first cache and resume), only after owner review.

### 03 — Manifest-first cache and resume architecture
- Implement per-site tracked baseline manifests in `scripts/` and untracked mutable runtime overlays/cache, provenance, schema and migration, preflight coverage/extend/refresh, chapter identity/offset guards, and safe output allocation/resume journal. Existing site adapters and output behavior remain functional.
- **Verify:** offline fixtures for empty/stale/corrupt/mismatched stores; sparse coverage; out-of-range automatic extension; fractional chapters; source URL identity, false success prevention; no duplicate fetches; reuse vs fresh folder; concurrent folder allocation; interrupted write/restart. Focus on regression preservation.

- **03.0 Result (2026-10-18): COMPLETE, awaiting owner review.** Contracts and safe filesystem mechanisms only. No catalog, adapter, pipeline, GUI, `RequestManager`, PDF, cache, output-folder or dependency change; the Resume button is still disabled. New `source_identity.py`, `run_storage.py` and `atomic_io.py` (all unwired) and the full data contracts in §9. Tests: `test_source_identity.py` and `test_run_storage.py`. Next: 03.1 (§9.9).

- **03.1 Result (2026-10-18): COMPLETE, awaiting owner review.** The chapter-manifest storage layer and a read-only legacy v2 import, both unwired: no pipeline, GUI, catalog, adapter, `RequestManager`, PDF, cache, output-folder or dependency change, and `pipeline._load_index` is untouched. New `chapter_manifest.py` and `chapter_store.py`; tests `test_chapter_manifest.py`, `test_chapter_store.py`, `test_chapter_legacy_import.py` (247). Contracts, API boundaries and decisions: §9.10. Next: 03.2 (§9.3, §9.6).

- **03.2a Result (2026-10-18): COMPLETE, awaiting owner review.** The validated chapter manifest is now authoritative for chapter preflight, discovery, coverage and selection in **both** pipeline paths. No catalog, adapter, `RequestManager`, HTML-cache, PDF, TXT, output-folder or dependency change; the Resume button is still disabled. New `chapter_preflight.py`, `file_lock.py`; `chapter_store.py` gains a single-writer lock, `record_discovery`, `refresh_runtime`, `restore_backup`; `pipeline.py` loses `_load_index`/`_save_index`/`_load_partial_index`. Contracts and decisions: §9.11, ADR 010. Next: 03.2b (§9.6 HTML cache), then 03.3.

### 04 — Two novel integrations, independently per domain
- Verify both user-specified `.net` source paths and both `.com` catalogue candidates LIVE without presuming equivalent markup or numbered URL templates; inspect TOC/pagination, descriptive slug mapping, chapter display titles, paragraphs, and failure behavior. Add only validated site/novel catalogue combinations with source-specific fixtures and adapter branches as needed; keep unverified sources visibly unavailable.
- For browser-needed cases implement bounded headless-first strategy and headed Playwright/manual fallback; retain working existing recovery. No mixed-host chapter sets in one run.
- **Verify:** offline source fixtures plus small user-approved live samples from each available pairing (early, middle, and later chapter, plus site-specific offset/fractional cases if observed); HTML challenge, 404, offline and manually cancelled scenarios. Report source coverage and known unavailable paths transparently.

### 05 — Canonical text, TXT/PDF grouping and cleanup
- Introduce one canonical `ChapterContent` cleanup pipeline; narrow/remove prior aggressive word/short-paragraph edits. Exactly one heading with `Chapter N: Title`, reliable translator/editor/chrome removal, meaningful Unicode preserved, no repeated title. Enable UTF-8 TXT and PDF independently or together with identical chapter data, all three mutually exclusive grouping modes and atomic publication. Preserve PDF style.
- **Verify:** golden/snapshot fixtures for all enabled sources; `Chapter 1: Crimson` and `Chapter 1: Foreigners`; same canonical TXT text vs. extracted PDF text (allow PDF layout line wraps); editor/translator combined lines; real short dialogue/prose kept; invisible Unicode stripped; Unicode letters/punctuation retained; orphan headings and no unintended source/title substitutions.

### 06 — Reliability, efficiency and usability hardening
- Review confirmed bugs in cancellation, headless-to-headed escalation, browser lifecycle, retry budgets, rate limiting, chapter mapping, no-network cached runs, stalled workers, missing chapters, cumulative progress and resource teardown. Fix only reproduced or evidence-backed issues; retain test coverage for previous v0.2.x fixes.
- **Verify:** focused regression cases for each fix; full suite at checkpoint, long-run/resume stress on consented public sources with polite request pacing; logs/counters accurately reflect partial failures. No masking failed tests.

### 07 — Windows acceptance, docs and v0.3.0 release readiness (macOS deferred, ADR 013)
- Verify `Setup_and_Run-Web-Novel-Scraper.bat` and `.command`, user-scope dependency setup, browser prerequisites/consent, fresh install/repair, directories and platform-specific browser fallback. Update README, Briefing, Decisions/CHANGELOG/Handoff and version metadata to reflect actual shipping behavior, not intended behavior. Keep user-facing instructions simple.
- **Owner override 2026-10-18:** the release gate is **Windows only**; keep the `.command` launcher and portable code, but macOS acceptance, launcher testing and macOS-specific fixes move to a later owner-directed branch. Never claim macOS readiness for v0.3.0.
- **Verify:** **the Windows manual gate** (macOS gate deferred) covering 3 sections, resizing, Light/Dark, Summary/Detailed, progress, `.com` and `.net` choices, HTML cache first-run/refetch, TXT/PDF/both/grouping, resume, cancellation, and no overwrite. Run full automated suite and inspect generated PDFs/TXTs. Owner confirms signoff for each gate. **Do not merge/tag/publish** until explicitly authorized after reports and review.

## 5. Acceptance requirements (must all hold for release)

- Default no-setup-confusion GUI launches on supported Windows targets (macOS deferred, ADR 013), Light/Dark in-place, left 3 sections and Activity on right, usable when not maximized; summary and detailed logs distinct and correct.
- `.net` and `.com` visibly separate, source availability truthful for each novel, selected source identity preserved through manifest, cache, body retrieval and resume. Both novels supported on every pairing that passed validation; unverified pairings never falsely marked available.
- Every supported job starts from a validated chapter list, discovers selected-range gaps, never guesses title/URL mappings without evidence, and never silently drops requested chapters. HTML cache is reused only when valid; no challenge page considered valid content.
- PDF default, TXT optional, grouping exclusive; both outputs use one normalized text source; headings exactly `Chapter N: Title`; only approved cleanup performed; original story meaning, short paragraphs and meaningful Unicode preserved.
- All new runs write to `~/Downloads/Web-Novel-Scraper-Outputs/<novel-name>-x` without overwrite; explicit Resume Existing Run works only with validated matching metadata/artifacts.
- Headless-first for browser-needed sites with bounded visible Playwright/manual fallback and truthful failure; HTTP-capable sources remain efficient; existing cancellation/rate limits/Cloudflare and offset behaviors are not regressed.
- Automated, offline regression suite passes; new behavioral changes have tests; the Windows manual gate documented with evidence (macOS deferred); no secrets, raw novels or runtime caches committed; release occurs only after separate authorization.

## 6. Deferred / out of scope

- No GUI framework migration to Qt/Electron/web; no ACT multi-tool sidebar; no arbitrary pasted URL scraper/general-purpose search engine; no autonomous cross-domain mixing of chapter content in one run; no new named novels beyond these two; no support for disabled stub adapters unless separately approved.
- No bulk dumping full novel text or mutable HTML caches into Git; no font/copyright redesign; no new paid services, proxies, automatic CAPTCHA solver, or unsolicited provider installation.
- No multi-worker rescue architecture overhaul unless necessary to fix a proven regression and separately scoped. Don't resurrect old `0.2.1` instructions as an implicit release phase.

## 7. Agent workflow / handoff

- Planning chat does not implement code. Claude Code owns repository edits on the approved feature branch. For each checkpoint: inspect only necessary files; implement bounded changes; run focused tests while coding, broader verification at checkpoints; perform requested manual gate; update the existing handoff; commit and push feature branch; report branch/SHA/diff paths, tests, unverified platform/source behavior and the next exact checkpoint.
- Stop for material ambiguity, destructive operations, new installs/services, platform manual-only gates, or explicit owner approval. Never claim live website availability or platform success solely from offline fakes.
- At final closure, transfer enduring requirements to permanent docs/tests, delete the temporary instruction drop **only once owner-approved release closure allows**, and ask for explicit merge/release authorization.

## 8. Checkpoint 00 technical clarifications and risks (2026-10-17)

These clarify how to carry out the approved decisions above. They do not change scope. Where one needs an owner choice, it says so.

- **C1 — Base commit and inherited fixes.** The branch inherits `577b5db` (§1). Its behaviour is regression baseline for 03–06: binary-search offset reconciliation (keep the `count + 64` fetch ceiling), `complete: false` incremental index checkpoints with resume seeding, slug-keyed reconciliation, `RunReport.toc_gaps`, URL-only `RescuePool` dedupe, and distinct SEPARATE filenames for repeated displayed numbers (`Chapter 3664_ … [slug 3679].pdf`). The manifest store (03) must absorb these rather than re-derive them. Pushing this branch publishes `577b5db` to GitHub for the first time; `fix/freewebnovel-offset-and-output-collisions` remains a local-only branch at the same commit.
- **C2 — Flat layout.** Done as step 01.0 (§4). Risk: until 01.0 lands, this checkout's tests cannot import `webnovel_scraper` in place, both launchers point at a missing `scripts/Universal/app.py`, and `parents[3]` path math resolves the cache and Playwright browsers one level above the repository. Do not run the app or scrape from this checkout before 01.0.
- **C3 — 0.2.1 status.** 0.2.1 stays an unreleased CHANGELOG section. Its unrun live items were BUG-1/BUG-3 (Cloudflare recovery reproduction) and BUG-2 (Stop latency and atomic write under failure). They are not re-run as a phase; checkpoint 06 covers cancellation, atomic writes and recovery with evidence-backed tests. How the 0.2.1 entries appear in the v0.3.0 release notes is an owner decision at 07.
- **C4 — Doc names.** Keep the tracked casing (§1). New tracked docs are not created without need.
- **C5 — UI reuse map (01–02).** Borrow cleanly, adapt to three sections, and record the source repo and "local working copy, 2026-10-17, newer than `edcbd4f`" in the commit and CHANGELOG:
  - PDF-Splitter `appearance.py`: `Compact.*` clam-cloned ttk styles, Light/Dark palettes, Aqua variants, `contrast_ratio`, `build_bundle`, `toggle_appearance`, listeners and `style_tk_widget` for `tk.Text`. It is stdlib/Tk only and imports nothing but the appearance constants from `settings.py`.
  - PDF-Splitter `activity.py` (stdlib only): `ActivityEvent` with severity, summary flag and `coalesce_key`; a bounded `ActivityChannel` (500 pending); a line-capped `ActivityModel`; `SessionLog`; and `ActivityPump` on Tk `after()`. It already meets §3's capped thread-safe pump, Summary/Detailed split and session logs.
  - PDF-Splitter `settings.py` patterns: a schema-versioned, validated, atomic JSON store with diagnostics and stale/corrupt recovery, plus `downloads_dir()` / `default_output_root()`.
  - PDF-Splitter `app.py` shell: a two-column body (controls column at weight 0 with a measured `minsize`; Activity at weight 1); numbered `Labelframe` sections; a Summary/Detailed `Notebook` with Clear Log; and a status bar with the appearance toggle, `Preferences & Data…` and `Open Log Folder`. `_fit_window()` measures the minimum size, then sets the geometry from widget sizes, `tk scaling` and a screen fraction.
  - PDF-Splitter `jobs.format_eta` / `format_counts`, which show `ETA: —` when the time is unknown.
  - ACT `job_control.py` / `job_ui.py` (about 2.7k and 2.6k lines, a full job state machine): pattern reference only (`MainThreadGuard`, `EtaEstimator`, `ProgressView`). Do not import them wholesale.
  - Delete the reference folder only after the 02 borrowing is complete.
- **C6 — Runtime data.** Use `files/runtime-data/` for `settings.json`, `logs/` and `chapter-lists/`, as PDF-Splitter does. Add it to `.gitignore` when first created; today only `files/cache/`, `files/bin/`, `files/output/`, `files/test-output/` and similar are ignored. Briefing must name it as a designated runtime location, because AI-WORKSPACE allows the app to depend on `files/` only where Briefing designates it. The existing HTML cache stays at `files/cache/`; it is keyed by slug today and must gain a site key before `.net` and `.com` coexist (03).
- **C7 — Output root (decision 4).** Resolve Downloads the way PDF-Splitter does: `%USERPROFILE%\Downloads` on Windows, `~/Downloads` elsewhere, falling back to the home folder if missing. Windows Known-Folder/OneDrive redirection is a documented limitation. Reuse `pipeline.resolve_output_dir` (already `<root>/<slug>-N`, first free N), but replace its `.exists()` probe with an atomic `mkdir(exist_ok=False)` reservation loop (03). Section 3 keeps a user-changeable output location; the run folder name follows decision 4.
- **C8 — Chapter identity.** Supreme Magus legitimately has two source pages that both display chapter 3664 (slugs 3678 and 3679). A manifest entry's identity is its source URL/slug/`source_id`. A repeated `display_number` from distinct pages is preserved and reported, not deduplicated. Both get the heading `Chapter 3664: Title` (decision 9), and their output names must stay distinct.
- **C9 — Headless-first vs. current evidence (decision 6).** Today's tested defaults are `DEFAULT_BROWSER_MODE=True` and `DEFAULT_HEADLESS=False` (a visible Camoufox primary), because the 0.1.1–0.1.3 live runs showed headless engines did not clear FWN Cloudflare. 0.2.0 already has a pipeline-owned, headless-only breaker that switches to a visible browser and stays there for the run (≥5 consecutive or ≥9 of 20 challenges), plus a single visible rescue lane. Implement decision 6 by reusing that breaker: one short, bounded headless probe per run, then latch to visible for the run. Do not retry headless per chapter. The `DEFAULT_HEADLESS is False` assertion in `test_phase8_gui.py` changes to the approved behaviour at 04; this is an intentional update, not a weakened check. Whether headless clears live FWN today stays unknown until 04's live samples.
- **C10 — Existing GUI guarantees.** `test_phase4_gui.py` (7 tests) and `test_phase8_gui.py` (5 tests) pin: delay default 3.0; no prebuilt manager; a non-daemon worker; close that polls until the worker exits; Tk callbacks safe after destroy; job config threaded to the pipeline; and output-target preview. 01–02 must carry these guarantees onto the new shell with equal or stronger assertions. Do not delete them to reach green. They skip when no Tk display exists.
- **C11 — Output modes and TXT.** No TXT renderer exists; output is PDF only (`pdf_builder.create_pdf_from_text` with pypdf/pdfplumber heading-page cleanup). `OutputMode.SEPARATE` / `CHUNKED` / `SINGLE` map to Individual Chapters / Every X Chapters / Whole Selected Range. Keep the enum values for resume and test stability and change only the user-facing labels. The current range filename is `{stem}_Chapters_{a}-{b}.pdf`.
- **C12 — Cleanup targets confirmed.** `NOISE_SNIPPETS` (`adapters/freewebnovel.py`, substring filter) is the main narrowing target at 05, together with any short-paragraph filters found there.
- **C13 — Python version risk.** Both launchers install Python **3.12** (winget `Python.Python.3.12` / python.org 3.12.8; Homebrew `python@3.12`), while the dev `.venv` and Briefing say 3.13. New and borrowed code must stay 3.12-compatible. 07 must prove a fresh 3.12 install, or the owner decides to move the launchers to 3.13.
- **C14 — Docs debt (not fixed incidentally).** `Briefing.md` (76 lines) and `handoff.md` (24 lines) contain UTF-8 mojibake (`â€”`), and `handoff.md` is about 1,750 lines. Clean both in the 07 docs pass. Checkpoint 00 corrected only Briefing's stale "Current Version" status and added the missing `577b5db` CHANGELOG entries.
- **C15 — Gate location.** After 01.0, the gate is again `.venv` Python running `scripts/verify.py` in place. Each checkpoint reports the exact pass count against the 292 baseline.

## 9. Checkpoint 03 data contracts (finalized in 03.0, 2026-10-18)

Design for 03.1–03.4. Only §9.1 is implemented; everything else is the contract those steps build to. Where this section and §8 C1/C7/C8 overlap, they agree.

### 9.1 Implemented in 03.0 (not yet wired into the GUI or pipeline)

- `scripts/webnovel_scraper/source_identity.py` (pure, no I/O):
  - `SourceIdentity(novel_slug, adapter_key, origin, index_url, identifiers)`: frozen, validated, normalised. `origin` is `https://<host>` and the host must be in `SUPPORTED_HOSTS` with an adapter expected for it; `index_url` must be on that same host (no query, fragment, port, userinfo or `..`). `site_key` is the host (`freewebnovel.net` is not `freewebnovel.com`, though both use adapter `freewebnovel`). `fingerprint()` is 16 hex digits of SHA-256 over canonical JSON. `from_site_spec(spec)` builds one from a catalog row without changing the catalog. `to_json`/`from_json` are strict (exact key set).
  - `ChapterLabel`: a displayed chapter number as canonical decimal **text** (`"627.2"`, `"627.10"`; up to 7 integer and 6 fractional digits; leading integer zeros dropped, fractional digits kept). Sorts by `Decimal`; `from_index`/`to_index` bridge to `ChapterMeta.index` (an integral float collapses to int, as `pipeline._index_number` does).
  - `ChapterIdentity`: `page_url` (normalised, on the source's host, fragment dropped), `label`, `slug`, `source_id`, `title`. **Equality and hash use `page_url` only.** `duplicate_labels()` reports labels shown by more than one page (Supreme Magus 3664 on slugs 3678 and 3679); `ensure_distinct_pages()` rejects one URL listed twice.
- `scripts/webnovel_scraper/run_storage.py`: `allocate_run_dir(root, novel_slug)` reserves `<root>/<slug>-N` (first free N >= 1, per novel, no website parameter) with an exclusive `os.mkdir`, then writes `run-reservation.json` (`schema_version` 1, `kind`, `novel_slug`, `number`, `created_utc`) atomically. A failed marker write removes the folder only if it is still empty; a non-empty leftover is kept and never reused. Existing folders, files and links (including an empty abandoned folder) are skipped, never reused. `preview_next_run_dir` only reads the listing. `open_existing_run(root, path, expected_novel_slug=None)` is the future Resume's read-only gate: a real directory directly inside `root`, named `<slug>-N`, with a matching marker. `default_output_root()` is `~/Downloads/Web-Novel-Scraper-Outputs` (the home folder if there is no Downloads).
- `scripts/webnovel_scraper/atomic_io.py`: `write_json_atomic` (unique temp file in the target folder, fsync, `os.replace`; the old file survives any failure).

### 9.2 Chapter manifest (03.1)

One schema for both layers, `schema_version: 1`, `kind: "chapter-manifest"`:

```json
{"schema_version": 1, "kind": "chapter-manifest", "layer": "baseline | runtime",
 "source": { "...SourceIdentity.to_json()..." },
 "provenance": {"generated_utc": "...", "validated_utc": "...", "app_version": "0.3.0",
                "method": "toc | walk | probe | legacy-migration", "legacy_index_sha256": null, "notes": ""},
 "coverage": {"toc_complete": false, "spans": [["1", "3700"]]},
 "chapters": [{"label": "627.2", "page_url": "...", "title": "...", "slug": 640, "source_id": null}],
 "absent":   [{"label": "88", "evidence": "page 404 and neighbours present"}],
 "content_sha256": "<over canonical chapters + absent + spans>"}
```

- **Locations.** Baseline (tracked, read-only at run time): `scripts/webnovel_scraper/chapter_lists/<site_key>/<novel_slug>.json`. Runtime overlay (ignored): `files/runtime-data/chapter-lists/<site_key>/<novel_slug>.json`. `<site_key>` is the host, so `.net` and `.com` never share a file. Only an explicit developer action edits a baseline; the scraper never writes one.
- **Coverage is set membership.** A *span* `[a, b]` means every integer label in it is either in `chapters` or in `absent` (source-confirmed missing), and fractional labels exist only if listed. A request `[x, y]` is satisfied only if it lies inside the union of spans. A gap in a sparse TOC is *not covered* until examined; it is never assumed absent.
- **Order and uniqueness.** `chapters` is sorted by `(label.sort_key, slug, page_url)`; `page_url` is unique; `slug` is unique when present; repeated labels are allowed for distinct pages (preserved and reported, never merged).
- **Validation on load.** Any failure rejects that layer. An invalid **runtime overlay** is quarantined as `<name>.corrupt-<utc>[-N]` (never deleted, never overwritten) and the loader falls to the baseline or rediscovery. A tracked **baseline** is *never* renamed, quarantined or rewritten at run time; it is rejected with a diagnostic and left exactly as found (owner clarification, 2026-10-18; see §9.10). Checks: schema, kind and layer; `source` equals the expected `SourceIdentity` exactly; every `page_url` is on the source host; labels are canonical; entries are sorted and unique; spans are sorted and non-overlapping with every integer inside accounted for; `content_sha256` matches; size cap 20 MB.
- **Writes** are `write_json_atomic` only, after validation.
- **Effective manifest** = baseline plus overlay, keyed by `page_url`. The same page with a different label, title or slug, or two pages claiming one slug, is a *contradiction*: nothing is silently merged; the affected labels become uncovered and are rediscovered, and the choice is logged.

### 9.3 Preflight, refresh and extend (03.2)

1. Load the effective manifest and compute the uncovered sub-spans of the requested range. A blank end means the discovered tail, re-checked every run from the index page's tail only.
2. No uncovered span means no TOC network traffic. Otherwise discover only those spans through the existing adapter `build_chapter_index` (the 577b5db binary-search offset reconciliation and its `count + 64` ceiling are kept). Verified entries are preserved.
3. Persist after each reconciled window: coverage grows only over spans actually examined, so an interrupted discovery keeps its partial progress. This absorbs today's `complete: false` checkpoints.
4. A requested label with neither an entry nor `absent` evidence makes the run refuse, naming the missing labels. **Superseded in part (owner, 2026-10-18, ADR 011):** a finite End above every known chapter is clamped to the last chapter a fresh, tail-confirmed discovery lists, with the adjustment logged and kept in `RunReport.range_notes`; a Start past the list reports "none available"; failed, empty, unconfirmed or contradictory discoveries still refuse. "Source lacks it" (evidence recorded) is reported as *skipped (reason)*, distinct from "the cache lacks it".
5. **Refresh chapter list** (explicit) renames the overlay to `.bak-<utc>` and rediscovers; it never touches the baseline.

### 9.4 Legacy `chapter_index.json` migration (03.1; the old file is only ever read)

The output-scoped file (`INDEX_SCHEMA_VERSION` 2) is never modified, moved or deleted. It is imported **into the runtime overlay** only when all of these hold: a dict with `schema_version == 2`; an `identity` block (`novel_slug`, `adapter_key`, `source_url`, `base_url`) that yields a `SourceIdentity` equal to the current source (`source_url` as `index_url`, `base_url` as `origin`); `complete` is true; every row converts (`ChapterLabel.from_index`, URL on the host, unique pages). Otherwise it is ignored with a logged reason: bare lists, v1, missing identity, `complete: false`, host mismatch or any bad row. This deliberately **tightens** today's `_load_index`, which still trusts a v2 dict whose identity block is missing.

Imported coverage is the maximal contiguous integer runs only: TOC gaps stay uncovered, because the old file never recorded evidence of absence, and the tail is not assumed to be the end. Provenance is `method: "legacy-migration"` plus the file's SHA-256 (no absolute paths). Import is idempotent and skipped where the overlay already covers. Pre-0.3.0 `<slug>-N` folders directly under `~/Downloads` are never moved, renamed or reserved against; with no reservation marker they are not resumable in v0.3.0 (owner decision, 2026-10-18; see §9.10).

### 9.5 Run journal and Resume (03.3)

`run-journal.jsonl` in the run folder: one header line, then append-only event lines, each `{"seq", "type", ..., "crc"}` with fsync per record. A crash costs at most a torn last line, which replay ignores. A damaged line anywhere else makes the journal invalid: Resume refuses and offers a new run.

- **Header:** `run` (slug, number), `source` plus fingerprint, `selection` (hash of the ordered `page_url` list, first and last label), `formats`, `grouping` (`mode`, `chunk_size`), `manifest` (`content_sha256` of each layer used), `app_version`, `created_utc`.
- **Events:** `publish` {output key, relative path, size, sha256, covered `page_url`s}, `skip` {page_url, reason}, `failure` {page_url, reason}, `end` {state}.
- **Publication order:** render to a temp name in the destination folder, fsync, `os.replace`, then append `publish` (fsync). A file on disk without a `publish` record is *unverified* and is re-rendered over the same deterministic name; orphan temp files matching our pattern are removed on resume. A recorded output counts as complete only if the file exists and its size and SHA-256 match; filename presence alone is never trusted.
- **Resume** requires, through `open_existing_run` plus the header: the same novel, the same source fingerprint (so `.net` is not `.com`), the same selection set, formats, grouping and chunk size. Any difference refuses, naming the differing field. Resumed outputs count as *already complete/resumed*.

### 9.6 HTML cache boundaries (03.2; `RequestManager` is untouched in 03.0)

Today: `files/cache/<novel_slug>/<sha256(url)[:24]>.html`, raw HTML only, written non-atomically, checked on read for undecodable text and Cloudflare challenges. The URL hash already keeps `.net` and `.com` files apart, but nothing records or checks provenance.

- **Layout:** `files/cache/<site_key>/<novel_slug>/<hash>.html` plus a sidecar `<hash>.json` {`url`, `host`, `novel_slug`, `fetched_utc`, `byte_len`, `sha256`, `classification`, `strategy`}.
- **Cross-host contamination:** a hit needs the sidecar host to equal the request host and the source host; a redirect to another host is not cached under the requested key.
- **Wrong novel:** the directory and sidecar carry `novel_slug`; extraction checks the chapter label and title against the expected `ChapterIdentity` (the offset guard). One mismatch quarantines the entry and refetches once; a second fails the chapter rather than looping.
- **Challenge pages:** the existing detection runs on read and again before write; a chapter page also needs a non-empty body container.
- **Truncated or corrupt HTML:** a sidecar length or hash mismatch means refetch; writes become temp file plus `os.replace`.
- **No needless refetch:** a valid hit is served with no network call; chapter pages never expire on their own; TOC/index pages re-check only their tail. An explicit invalidate marks the entry `.invalid`.
- **Migration:** legacy entries are copied, never moved or deleted, into the new layout after the same validation, with `origin: "legacy-copy"`.
- **Implemented in 03.2b (2026-10-18):** see §9.12 and ADR 012.

### 9.7 Failure and interruption matrix

| Interruption | Result |
|---|---|
| Crash between `mkdir` and the marker | The empty or partial folder remains, is skipped by every later allocation, and is never resumable |
| Crash while writing a manifest or marker | Old file intact; a stray temp file is ignored |
| Crash after `os.replace`, before the journal record | Output is unverified and is re-rendered atomically |
| Torn last journal line | Ignored on replay |
| Damaged earlier journal line, or manifest hash mismatch | Journal: refuse and offer a new run. Manifest: quarantine and rediscover |
| Output edited or truncated after publication | Size or SHA-256 mismatch, so it is re-rendered |
| Source identity or selection changed | Resume refused, naming the differing field |

### 9.8 Compatibility risks found in 03.0 (each owned by the step named)

1. `_load_index` trusts a v2 dict with **no** identity block (03.1 tightens it, §9.4).
2. `ChapterMeta.index` is `int | float`, and a float cannot tell `627.1` from `627.10`. Manifests store label text; conversion happens only at the adapter boundary (03.1).
3. The HTML cache is keyed by novel slug only, has no provenance, and is written non-atomically (§9.6, 03.2).
4. The FWN adapter's URL logic (`_chapter_link_re`, `chapter_url_for`) assumes the `.com` `/novel/<slug>/chapter-N` shape, while `.net` pages are `/<slug>/chapter-1-crimson.html`. That is adapter work for Checkpoint 04; the identity layer already accepts both.
5. `resolve_output_dir` probes with `.exists()` (race-prone) and supports a custom **Folder name** (a GUI field). Decision 4 names new runs `<slug>-N`, so 03.3 retires that field when it integrates the allocator (owner-approved 2026-10-18). Today's behaviour is unchanged until then.
6. `pipeline._chapter_identity` keys on `(index, slug, source_id, url)`; the new identity keys on the page URL. They agree for every real row, but 03.1 must not let the legacy tuple drive manifest identity.

### 9.9 Next steps

- **03.1** Manifest store: schema, validation, atomic I/O, baseline plus overlay merge, quarantine, legacy v2 import (§9.2, §9.4). No pipeline change. **Done 2026-10-18, see §9.10.**
- **03.2a** Coverage, discovery/extend/refresh, single-writer lock and false-success prevention (§9.3). **Done 2026-10-18, see §9.11.**
- **03.2b** HTML cache layout, sidecars, provenance and validation; legacy HTML-cache copy (§9.6).
- **03.3** Run journal, allocator integration (new output root, retire Folder name) and the real Resume button (§9.5).
- **03.4** End-to-end regression and acceptance: interrupted write and restart, sparse coverage, fractional and duplicate labels, no duplicate fetches, fresh versus resumed folders.

No owner decision blocks 03.0. The owner has since decided (2026-10-18) to retire the Folder name field in 03.3 and that pre-0.3.0 folders are never automatically resumable (§9.10).

### 9.10 Checkpoint 03.1 result: chapter manifest store (2026-10-18)

**Owner decisions after the 03.0 review (binding):**
1. The GUI **Folder name** field is retired in 03.3, when the `<slug>-N` allocator is integrated.
2. Pre-v0.3.0 output folders are not automatically resumable. The app never moves, overwrites or deletes them.

**Contract clarifications (binding):**
- Tracked baseline manifests under `scripts/` are never renamed, quarantined or rewritten at run time. Quarantine applies only to mutable runtime overlays. An invalid baseline is rejected with an actionable diagnostic and left untouched; rediscovery then proceeds from the overlay or from nothing.
- 03.3 must keep the approved output-root contract exactly: `run_storage.default_output_root()` is `~/Downloads/Web-Novel-Scraper-Outputs`, and falls back to the home folder only when no Downloads folder exists, as built in 03.0. It must not silently substitute the home directory.

**Implemented (unwired).**
- `chapter_manifest.py` (pure): `ChapterManifest` (schema §9.2; `create`, `empty`, strict `from_json`/`to_json`, `covers`, `uncovered_spans`, `duplicate_labels`, `content_sha256`), `Provenance`, `AbsentChapter`, `parse_manifest_bytes`, `load_strict_json`, `ManifestError`, and the span helpers `coalesce_spans`, `uncovered_spans`, `accounted_numbers`, `accounted_spans`, `whole_number`. Every invariant is checked on construction, so a built manifest is as valid as a loaded one. Identity and label validation is `source_identity`'s, not re-implemented.
- `chapter_store.py`: `ChapterManifestStore(baseline_root, runtime_root, clock, app_version)` with `baseline_path`, `runtime_path`, `load_layer`, `load_effective`, `save_runtime`, `import_legacy_index` and `now_utc`. There is deliberately **no** method that writes a baseline. Also `merge_manifests` and `EffectiveManifest`, `ManifestConflict`, `LayerLoad`, `EffectiveLoad`, `parse_legacy_index`, `LegacyImportResult`, `LegacyIndexRejected`, `ManifestStoreError`. Default roots are `scripts/webnovel_scraper/chapter_lists/` (nothing committed there yet) and the ignored `files/runtime-data/chapter-lists/`.

**API boundary for 03.2.** Call `load_effective(source)`; read `.effective` (`chapters`, `absent`, `spans`, `covers`, `uncovered_spans`, `conflicts`, `notices()`) and `.notices` for Activity lines. Persist each reconciled window with `save_runtime(manifest)` where `manifest = effective_or_overlay.as_manifest("runtime", Provenance(...))` or `ChapterManifest.create(...)`. 03.2 must not read manifest files directly, and must replace `pipeline._load_index`'s trust path by calling `import_legacy_index` for the old file (§9.8 risk 1). `ChapterIdentity.to_index()`/`ChapterLabel.to_index()` stay the only label-to-`ChapterMeta.index` bridge.

**Behaviour fixed while implementing (not spelled out earlier).**
- *Coverage soundness.* A span may not hold a whole number that no chapter or confirmed absence accounts for; checked with a sorted list and `bisect`, so work is bounded by the entry count, never by span width. Spans are limited to 0..9,999,999. Absent labels must be whole numbers inside a span (a fraction cannot be confirmed absent) and may not also be listed chapters. `toc_complete: true` needs a single contiguous span holding every whole-numbered chapter (or none when there are none), and never comes from a legacy migration.
- *Provenance.* `method: legacy-migration` requires `legacy_index_sha256` and is not allowed in a baseline; the digest may also ride on a runtime overlay of another method after a merge.
- *Merge.* A page missing a title, slug or source_id in one layer takes it from the other; two different known values are a conflict, as is a different label. A conflicted page, or both pages sharing a slug, are dropped and both labels leave coverage. For absence evidence that a chapter contradicts, the evidence is discarded, the chapter entry is kept and its label leaves coverage. The effective `toc_complete` is true only when there is no conflict, every contributing layer says so and one span remains. Baseline evidence text wins a tie between layers. File age is never consulted.
- *Quarantine.* The target name `<name>.corrupt-<utc>[-N]` is claimed exclusively (`O_EXCL`) before the rename, so an existing file is never overwritten, on Windows or POSIX. A rename failure leaves the file in place (`quarantine-failed`), and `save_runtime` then refuses to overwrite it. An I/O error while reading is `unreadable` and nothing is moved. Size is checked from `stat` before reading (20 MB cap).
- *Legacy v2 import.* Rejected, with a logged reason and no write: bare list, any other schema version (a float `2.0` or `true` is not `2`), missing or malformed identity block, `complete` that is not literally `true` (a missing flag is rejected, tightening `_load_index`, which treats it as true), cross-source identity, an empty or malformed chapter list, a row with unknown keys or a non-numeric index, a bad or off-host URL, duplicate pages or slugs, oversized or unparseable files. The identity comparison covers `novel_slug`, `adapter_key`, `source_url` and `base_url`; an empty `base_url` falls back to the source URL's origin. The old identity never recorded `identifiers`, so they cannot be compared. Coverage is the maximal contiguous whole-number runs. Results: `imported`, `already-covered` (every candidate page is known and every span covered by baseline plus overlay: no write, so import is idempotent and never rewrites newer overlay coverage), `conflict` (it contradicts the baseline or overlay: no write), `rejected`, `error` (publishing failed: nothing changed). When an overlay exists, the import is merged into it and the overlay's own method and chapter metadata are kept.

**Known limits and risks.** The store does not lock across processes, so two writers doing a read-modify-write could lose an update (03.2 persists from one run at a time). No real baseline manifests are shipped yet; they need a verified developer-side generation step. `pipeline._load_index` still accepts a v2 dict with no identity block and treats a missing `complete` as true until 03.2 replaces it. A directory sitting where an overlay should be is set aside like a corrupt file where the operating system allows the rename (it did on Windows), and is otherwise reported `quarantine-failed` and left alone; either way nothing in it is lost.

### 9.11 Checkpoint 03.2a result: manifest preflight, discovery and pipeline integration (2026-10-18)

**One preflight, two paths.** `chapter_preflight.resolve_chapters` is called by `_drive` (legacy/injected adapter) and `_run_with_rescue` (FreeWebNovel conductor) through one pipeline helper, `_resolve_chapters`, so selection rules exist once. The only per-path difference is the injected `discover(start_hint, end_hint, resume_metas, index_progress_cb)` callable: `_build_chapter_index(adapter, ...)` on the legacy path, `_bootstrap_toc(primary, ...)` (headless-to-visible TOC fallback unchanged) on the rescue path. Both pass `manifest_store=` (tests inject one; the default is `pipeline.default_manifest_store()`).

**Coverage and discovery.**
- A finite range the effective manifest fully covers makes **no TOC request** and writes nothing. Otherwise one adapter call covers the span from the first to the last uncovered label (so a FWN run still gets `start_hint`/`end_hint`, the binary-search offset reconciliation and the `count + 64` ceiling), seeded with every known chapter as `resume_metas`.
- An open end always re-checks the tail: `[max(start, last_known - TAIL_RECHECK_MARGIN (64)), open)` plus any uncovered span below it.
- **A discovery claims only what it was asked about and accounted for.** Coverage = the unbroken runs of whole numbers the returned chapters account for, *clipped to the hinted window* (`clipped_spans`); the adapter's padding beyond the window is stored as evidence but claims nothing. Interrupted-discovery checkpoints (`index_progress_cb`) are bounded further, to the last label whose slug has been resolved. A restart is seeded with what was kept and asks again only for the unclaimed part.
- Persisted through `store.record_discovery` (never a bare delta: it merges into the existing overlay, refuses evidence that contradicts the baseline or overlay, reports `unchanged` without writing). Off-host, malformed or self-contradictory entries are ignored, named in a WARNING and never stored.
- **Refusal, never clamping** *(superseded for oversized End by ADR 011, §9.12)*. Any requested whole number the list neither holds nor records as confirmed-absent refuses the run (`ChapterListRefused`, shown as the existing "Run refused" outcome) naming the missing chapters: "the site's chapter list ends at chapter N, so chapters N+1 to M are not available". A finite `1-5000` on a 4402-chapter novel is therefore refused; leave End blank for "through the last chapter".
- **Sparse-TOC gaps.** A hole *inside* the list the site just returned (the adapter skipped number g but listed numbers on both sides) is reported as a TOC gap exactly as before (`report.toc_gaps`, the WARNING, Activity) and the run continues on the listed chapters. It is never recorded as absent, never counted as covered, and so a later run asks the site again. A hole longer than `MAX_TOC_GAP_RUN` (200), or any gap the discovery did not bracket, is refused as an unconfirmed range. A confirmed absence (evidence in the manifest) is a reasoned skip: `report.absent_skipped`, a NOTE, and a line in the summary.
- Selection is by `ChapterIdentity` (source page URL). The legacy `ChapterMeta` bridge keeps the exact label text in `extra["manifest_label"]`, so `627.1` and `627.10` (same float) stay two pages; duplicate displayed numbers (3664) stay two pages. Discovery through a float-only adapter cannot recover `627.10` from `627.1`; manifest entries can.

**Legacy index.** `pipeline._load_index` (which trusted an identity-less v2 dict and a missing `complete`) is gone, and the pipeline no longer writes `chapter_index.json`. When a run touches an output folder that already holds one, it is offered to `import_legacy_index` (strict; imported / already covered / rejected / conflict, each logged readably) and is never modified, moved, deleted or overwritten. Refresh does not re-import it.

**Concurrency.** `file_lock.FileLock` (`msvcrt.locking` / `fcntl.flock`, stdlib) gives each source a non-blocking cross-process writer lock beside its runtime overlay (`<name>.json.lock`, ignored). The OS frees it if the holder dies, so there is no stale lock to clean up. The preflight holds it across load, reconcile and publish. `save_runtime`, `import_legacy_index`, `record_discovery`, `refresh_runtime` and `restore_backup` all take it (re-entrant for the holder). A second run still **reads** a covered list (with a WARNING, and without quarantining anything, `load_effective(quarantine=False)`) but is refused visibly when it would have to discover or refresh. Limit: advisory locks on network file systems are not guaranteed.

**Refresh.** `ScrapeJob.refresh_chapter_list` (default off). The preflight sets the current overlay aside as `<name>.json.bak-<utc>[-N]` (exclusive name, never replaced; the baseline is untouched) and rediscovers; if the refresh is cancelled or fails before anything new was saved, the backup is put back, otherwise its name stays in the log. GUI: a one-shot box "Refresh chapter list on the next run" in Advanced Settings (not a saved preference, cleared at every Start, locked during a run, named in the start summary).

**Files.** New: `chapter_preflight.py`, `file_lock.py`; tests `test_chapter_preflight.py` (54), `test_chapter_store_writer.py` (21), `test_ui_chapter_list.py` (6). Changed: `chapter_store.py`, `pipeline.py`, `models.py` (one field), `app.py`, `ui/run_activity.py` (the dead "Clamped requested range" marker removed), and test fixtures (see below).

**Test changes (not weakened).** Fake adapters used non-site URLs (`https://example/1`) that no validated manifest can hold; they now use on-host URLs. Tests that asserted the removed behaviour were replaced by explicit safety regressions: the clamp test is now a refusal test, `test_bug_hunt_bh1_toc_identity.py` now pins the strict import (including that an identity-less v2 index is rejected and that the old file is byte-identical afterwards), the checkpoint test asserts manifest persistence, and tests that relied on `end=5` against a 3-4 chapter fake now ask for the range the fake has. `conftest.py` gives every test its own empty store.

**Known limits and risks.**
- A persistent sparse-TOC gap stays uncovered, so a run whose range spans one re-asks the adapter each time (cheap: everything known seeds it, and the HTML cache serves repeats). An "examined but unlisted" manifest field would fix that; it is a schema change and was left out.
- The HTML cache is still keyed by novel slug only and TOC pages may be served from it (03.2b). Until then the open-end tail re-check can read a cached index page.
- No real baseline manifests are shipped yet (they need verified developer-side generation).
- `Folder name` and the `<slug>-N` allocator are untouched (03.3).
- Not run live: no real source, browser or network was used.

### 9.12 Checkpoint 03.2b result and owner overrides A/B (2026-10-18)

**Override A — End auto-clamp (ADR 011, superseding ADR 010 decision 3).** `chapter_preflight._select`
now classifies each uncovered part of the request against *this run's* discovery:
- above every known chapter, finite End, discovery tail-confirmed → **clamped** (`effective_range`
  ends at the last listed chapter; `NOTE: range adjusted:` in Activity; `RunReport.range_notes`;
  nothing recorded absent or covered, so later runs extend);
- below the first listed chapter by at most `MAX_TOC_GAP_RUN` → Start raised, noted;
- inside the returned list, at most 200 → sparse TOC gap, unchanged (reported, skipped, never absent);
- anything else (no discovery this run, empty discovery, `tail_confirmed=False`, a hole below a newer
  saved chapter, a huge hole) → refused with the reason. A Start past every chapter → "None of
  chapters X-Y are available".

`models.ChapterIndex(list, tail_confirmed=)` lets an adapter say its list may be short; the WebNovel
adapter sets it when it lists fewer chapters than its own `chapterCnt`. A run with gaps inside its
range now ends "Finished with gaps" (completed with problems) instead of "Completed".

**03.2b / Override B — validated HTML cache (ADR 012).** New `html_cache.py`
(`files/cache/<host>/<novel>/<key>.html` + `<key>.json` provenance; validation on read; atomic
pairs; challenge/garbled/truncated/redirected pages never stored; invalidation to
`<key>.invalid.html`; on-demand legacy *copy*). `RequestManager` gains `site_key`, `refresh_cache`,
`invalidate_cache`, `cache_root`, `legacy_cache_dir`; final URLs are tracked per attempt. Index pages
are fetched with `use_cache=False` by both enabled adapters, so a stale index can no longer hide new
chapters. `adapters.base.extract_validated` sets aside unusable pages and refetches a cached one once.
The FreeWebNovel adapter now refuses a page that shows another chapter number than the manifest
expects (`models.ChapterMismatchError`, an extraction-class failure). Rescued pages are cached after a
successful extraction. GUI: one-shot "Re-download saved chapter pages on the next run" in Advanced
Settings (`ScrapeJob.refresh_html_cache`).

**Evidence (offline, from the owner's 1.2 GB legacy cache; nothing copied or committed):** every
cached Shadow Slave (3,065) and Reverend Insanity (2,334) page shows exactly its URL slug's number;
Renegade Immortal is an offset novel from slug 718 (slug 718 shows "717 (2)"), which the existing
reconciliation already handles; Supreme Magus is the known offset novel. So the offset guard does not
fire on correctly reconciled lists. Found for 05: en-dash separators survive into titles
("Chapter 714: – The Moongazer…").

**Tests:** `test_html_cache.py` (44), preflight clamp regressions on both paths, WebNovel
tail-confirmation, GUI re-download box; earlier cache tests updated to the provenance contract (a raw
file without a sidecar is now a miss by design). Mutation check: 15/15 caught.

**Known limits:** the cache does not lock across processes (pairs are self-validating, so a race only
costs a refetch); legacy pages are copied on demand, so both layouts coexist until the owner deletes
the old folders.

### 9.13 Checkpoints 03.3-03.4 result: run folders, journal and Resume (2026-10-18)

**Implemented (ADR 014).** New `run_journal.py` (`RunHeader`, `RunJournal.create/resume`,
`replay_journal`, `load_selection`, CRC-32 records), `run_output.py` (`plan_groups`, `RunOutputs`:
groups, names, render-then-record publication, checksum-verified skips, audit) and `run_index.py`
(`describe_run`, `list_runs` for the GUI). `pipeline._prepare_run` is the one start-of-run step for
both paths (new run: preflight, then reserve `<slug>-N`, selection + header; resume: validate the
folder, journal, selection, source, formats, grouping, chapter-list consistency, lock).
`pipeline._finish_run` publishes what is pending, records failures and the end state, writes
`run-audit.json` and releases `run.lock`. The legacy driver is one fetch loop plus one sweep for all
three modes; the rescue conductor hands content and failures to `RunOutputs` keyed by page.
`ScrapeJob` gains `formats`, `allocate_run`, `resume`. `pdf_builder.create_pdf` now `fsync`s before
publishing.

**GUI.** Folder name retired; the Output folder field previews the next `<slug>-N` under the root
(default `~/Downloads/Web-Novel-Scraper-Outputs`, Browse changes the root). Resume Existing Run lists
the unfinished runs in the root (newest first, with progress) or accepts another run folder, and
resumes the chosen run from its journal. Refusals (`RunStorageError`, `JournalError`) end as "Run
refused" with the reason. A test-suite safety net fails any unexpected modal dialog instead of
hanging.

**Interruption matrix (§9.7) as tested:** crash after `os.replace` before the record → file re-rendered;
torn last journal line → ignored and trimmed; damaged earlier line → refused, nothing changed; edited or
truncated output → re-rendered; orphan `*.part` → removed on resume; source, formats, grouping or chunk
size changed → refused naming the field; manifest now contradicting a selected page → refused; two
windows on one folder → refused; new run into a folder that holds a run → refused.

**Tests:** `test_run_resume.py` (32, both pipeline paths, all three groupings), GUI Resume tests in
`test_ui_selection_window.py`; earlier tests moved to journal-based resume and `PDF/` paths. Mutation
check: 16/16 caught. Gate: see the CHANGELOG entry.

**Known limits:** grouped files are written at the end of a run (a crash mid-run re-renders them from
the HTML cache); `pipeline.resolve_output_dir` remains only for its old tests and is unused by the app;
the Resume list reads journals only (checksums are verified when the resume starts).

### 9.14 Checkpoint 05 result: canonical text, TXT and PDF (2026-10-18; done before 04)

Done before 04 because the new novels need the corrected cleanup and headings (ADR 015).
- `content.py` (normalisation, titles, the one heading, body cleanup, interface lines, site
  promotions), `txt_builder.py`, structured `pdf_builder.create_pdf` (`build_story`, `markup` with
  installed-font fallback for characters outside WinAnsi), `ChapterContent.label`/`heading`.
- FreeWebNovel: first body container present wins (`#article`, `#chapter-content`, ...); NOISE_SNIPPETS,
  the short-line rule and consecutive de-duplication removed. WebNovel: the JUNK_KEYWORDS substring
  filter replaced by whole-paragraph interface detection; `_title_only` removed.
- GUI: PDF/TXT boxes live (at least one), formats on the job, counts and outcome text per format.
- Evidence (offline, owner's cache): §9.12 and ADR 015; on 2,000 sampled `.com` pages the new
  extraction removed only credit lines (382), "COMMENT" artifacts (11), promotions and repeated
  headings, with no failures and no untitled chapters.
- Known limits: Reverend Insanity's inline comment-count digits ("1") stay (a countdown would look
  the same); a PDF character with no installed font is left as is and listed in
  `pdf_builder.missing_glyphs`; macOS font fallback is untested (deferred, ADR 013).

### 9.15 Checkpoint 04 result: Lord of the Mysteries 1/2 on .com and .net (2026-10-18)

**Live evidence** (rate-limited, 3.5 s per host; robots.txt allows both; `/ajax-` never used; ~130
requests in total; metadata only kept, in the session scratch area, nothing committed):

| Novel | Website | Index / TOC | Chapters listed | Sampled live (adapter) | E2E run | Result |
|---|---|---|---|---|---|---|
| Lord of the Mysteries | `.com` | `/novel/lord-of-the-mysteries` (identity) | 1-1432 | 1, 700, 1303, 1432 (HTTP), 2 headless + 3 visible Camoufox | 1-3 PDF+TXT, headless | **Enabled** |
| Lord of the Mysteries | `.net` | `/lord-of-the-mysteries.html?page=1..36` | 1432 entries, 1-1432 (1303 at a misspelt `chaspter-` address; dropped until §9.18) | 1, 700, 1180, 1302, 1303, 1304, 1432 | 1-3; 1301-1305 (all five since §9.18) | **Enabled** |
| LOTM 2: Circle of Inevitability | `.com` | `/novel/lord-of-mysteries-2-circle-of-inevitability` | 1-1181 | 1, 600, 888, 1181 | 1-3 single file, headless | **Enabled** |
| LOTM 2: Circle of Inevitability | `.net` | `/lord-of-mysteries-2-circle-of-inevitability.html?page=1..30` | 1-1181 | 1, 600, 888, 1069, 1180, 1181 | 1-3 chunked; 1180-5000 clamped to 1181 | **Enabled** |

Also smoke-tested live after the 05 cleanup changes: Shadow Slave, Reverend Insanity, Renegade
Immortal, Supreme Magus (offset reconciliation, chapters 4-5) on `.com` headless, and The Noble Queen
on WebNovel (HTTP): all saved. `.com`/`.net` sampled chapters agree in paragraph count (LOTM2 888:
52 vs 53) and are byte-identical for late chapters. Known source irregularities: `.net` LOTM list
gives 1303 a misspelt address (`chaspter-1303-…`; dropped until §9.18); `.net` pages without titles (LOTM2 888, 1069; LOTM 1180 list entry) -> `Untitled` with a
warning; `.net` titles repeat the number ("1 Foreigners") and double-escape quotes (handled); a stray
quote in one source title ("New Life“") is kept as published. Not run: any full-novel download.

**Implemented** (ADR 016): `adapters/freewebnovel_net.py`, catalog rows (4), registry, host table,
headless-first default and one-challenge breaker, standard-path limiter, website labels.
**Tests:** `test_freewebnovel_net.py` (12) and updated catalog/headless/breaker tests.

### 9.16 Checkpoint 06 result: reliability hardening (2026-10-18)

Fixed (each with a regression in `test_reliability.py`, 10 tests):
- **Stop during output building** was ignored until every grouped file was rendered (a whole-novel
  PDF of 1,432 chapters measured 85 s, ~111 MB peak). Now a zero-size layout checkpoint per chapter
  stops a multi-chapter PDF at the next chapter, a check between files stops TXT/PDF builds, and
  after Stop no grouped file is built (Resume builds them from the HTML cache). The chapter in
  progress when Stop is pressed is still saved.
- **Cache-only operation:** an open-ended run whose saved list already covers Start..its last chapter
  no longer fails when the site cannot be reached, or when another window holds the list lock; it uses
  the saved list with a prominent WARNING and a range note, and never treats the failure as the end.
  A finite range, or a saved list with a hole, still refuses.
- **Memory on long runs:** chapter text is released once its file is published (titles kept for the
  audit), so an individual-chapter run no longer holds the whole novel's text.
- **Pacing on the standard path:** list pages are paced like chapters (Checkpoint 04).
- Bounded stress: 300 chapters with intermittent and permanent failures — progress, failed/rescued
  counts, journal records and resume all add up exactly.

Reviewed and unchanged (evidence-backed behaviour retained): retry/rescue ladders, the headless ->
visible latch (now one challenge), browser teardown (live: no Camoufox process left), rate limiter,
worker-thread rules (no Tk off the Tk thread). Mutation check: 8 rules, all caught except one
equivalent mutant (`finish(cancelled=True)` early return; the between-file check gives the same result).

### 9.17 Checkpoint 07 result: Windows setup, independent review and release readiness (2026-10-18)

Setup (ADR 017): the launcher trusts only real checks (`scripts/setup_check.py`: `python`, `base`,
`deps`, `browsers`) and repairs only what is missing. Python 3.12/3.13 final releases only, every
candidate executed, never a virtual environment as the base; WinGet, else the python.org 3.12.10
installer through `scripts/install_python.ps1` (SHA-256 + PSF Authenticode, per-user, PATH
unchanged, `PSModulePath` cleared first). Browser engines checked on disk at every start (Camoufox
executable without downloading, GeoIP file, Playwright's `INSTALLATION_COMPLETE` for the expected
Chromium revisions); marker files removed. Refusals before any install: folder path over 140
characters, unwritable folder, launcher started from inside the ZIP. `VIRTUAL_ENV`, `PYTHONHOME`,
`PYTHONPATH` cleared. Start-up stderr saved and shown. The window warns about a missing engine at
start-up and is DPI aware. `__version__` 0.3.0; README rewritten.

Launcher evidence (scratch copy in `W N (1) & !`, mapped to a short drive letter; the owner's `.venv`
never touched): fresh build; a too-long path refused (a run from a 218-character path first failed
with "The filename or extension is too long", which is what led to the guard); GeoIP repair; a broken
environment rebuilt from the base install; a start-up crash shown; everyday start in 1.9 s; a 3.12-only
build; an unwritable folder; no usable Python with the install declined. The full suite also passed
inside the fresh 3.12 environment with exactly the pinned packages. The python.org installer download
and verification passed (and failed closed when PowerShell could not load its signature module).

Independent review (read-only reviewer over df08070..fb7dfe9), each finding reproduced, fixed and
pinned by a test that fails without the fix:
1. (Critical) a resume that fetched fewer pages could replace a fuller grouped file: never again.
2. (Important) the credit-line filter deleted prose ("Editor-in-Chief Zhou frowned"): a credit now
   needs a colon or a spaced dash.
3. (Important) .net downward widening never ran, so unread chapters became invented gaps: widening
   uses the window's pages only, and adapters report the spans they read (`ChapterIndex.examined`);
   the preflight refuses rather than calling an unread number a gap.
4. (Minor) .net page selector without ranges clamped End to page 1: pages estimated, tail not vouched.
5. (Minor) a part-written journal line followed by another record blocked Resume: cut off on failure;
   journal lines no longer get CRLF from `os.open` text mode on Windows.

Found while preparing acceptance: on the standard path a chapter served by the saved-page cache still
waited the inter-fetch delay (a cached 1,432-chapter .net run: ~72 min of idle waiting). Fixed in
`_fetch_one` (no wait, counted as `RunReport.from_cache`, shown in the summary); confirmed on the real
stack by repeating two earlier live runs from their saved pages.

Acceptance aid: `files/audit_run.py` checks a finished run folder against its selection and journal
(order, repeats, gaps, checksums, TXT/PDF headings, failures) without printing chapter text.

Verification: 1,456 tests pass on Python 3.13.12 and 3.12.10; `scripts/verify.py` PASS.

### 9.18 Owner acceptance round 1: fixes (2026-10-18)

The owner's nine small GUI runs (29 PDFs, no chapter failures) found two problems.

1. **.net LOTM 1301-1305 saved four chapters.** Confirmed with two requests (list page 33, chapter
   1303): the site lists 1303 at `/lord-of-the-mysteries/chaspter-1303-coming-to-life.html`, and the
   page is real (heading "Chapter 1303", full text). Root cause: `_entry()` accepted only
   `<novel>/chapter-*.html`. A URL-prefix defect, not a label or discovery one. Fix: another spelling
   is accepted only when the address is one `word-N[-slug].html` page directly in the novel's folder,
   HTTPS on the same host, and N equals the whole chapter number the entry's text shows; it is kept
   verbatim and noted once in the log. Nothing is fabricated. Discovery stays bounded (pages 1 and
   32-34 for this window), and the chapter page's displayed-number check still applies. Live re-run
   (scratch copy of the owner's saved list): all five saved, the saved list corrected itself from
   159 to 160 entries, `audit_run.py` passes.
2. **Progress bar jerky across runs.** Confirmed on screen with `identify()`: in indeterminate mode
   Tk advances the bar one unit of `maximum` per 50 ms tick, and `_render_run` left `maximum` at 1 (a
   fresh window: the block flipped end to end every tick) or at the last run's total (3-5 for a small
   range: fifth-of-the-bar jumps). Fix: indeterminate mode always uses `maximum=100`, starting from 0.
   Also confirmed, by simulation: cached chapters (instant since 07) were counted in the ETA's rate,
   so after 40 cached chapters the ETA read 41 s for 203 s of work and climbed for ten chapters.
   Fix: `phase_cb` gains a fourth argument, `from_cache` (the report's count, sent before the tick
   that includes it); the window attaches it to each progress update, and the tracker leaves those
   chapters out of the rate as it does resume skips.
   Checked and found sound: counts never go backwards or past the total, each run starts from a
   clean state, events of one run cannot reach the next, Stop/failure/refusal states, and Tk access
   only on the Tk thread.

Verification: 1,476 tests pass on Python 3.13.12 and 3.12.10; `scripts/verify.py` PASS; each fix
mutation-checked.

### 9.19 Composite chapter headings (Lord of Mysteries 2, .com 1130–1138; 2026-10-18)

The owner's whole-novel run of Lord of Mysteries 2 (.com) failed chapters 1130–1138 ("shows chapter
1129.1 / 1130 / 1131 … but the list expects 1130 / 1131 / 1132 …"; 1127–1129 and 1139–1140 were fine).

1. **Reproduced** with two polite live requests (`chapter-1130`, `chapter-1131`, Camoufox, scratch
   cache). The page `<span class="chapter">` and `<title>` read `Chapter 1129.1 - 1130 Initiative` and
   `Chapter 1130 - 1131 Individual Preparations`; the body's first line is `Chapter 1130 Initiative` /
   `Chapter 1131 Individual Preparations`. `_normalize_heading_line` reads `Chapter A - B Title` as
   chapter A with title "B Title"; A is 1129.1 / 1130 ≠ the expected 1130 / 1131, so
   `ChapterMismatchError` (correctly, given what it was told). The list, the addresses and the
   expected numbers were right; the heading reader was not.
2. **Fix** (`adapters/freewebnovel.py`, `_composite_heading`, used by `_extract_chapter` and by the
   displayed-number probe `_fetch_and_read`, so offset discovery does not see a false offset): a
   heading `Chapter A - B Title` is a pair only if B is a whole number with 0 < B − A ≤ 1 and the
   body's first two lines contain a heading `Chapter B <same title>` (case-insensitive). The chapter
   identity stays `B` (= `meta.index`, which must equal the list's expected number, so the displayed
   number check still applies); only `ChapterContent.label` carries the display pair, giving the file
   heading `Chapter 1129.1-1130: Initiative`. Number validation is not relaxed anywhere else, side
   chapters (`1129.1`) are never merged with ordinary ones, and nothing is invented.
3. **Live check** (scratch list copy, scratch cache/output, delay 3.5 s): 1127–1140 → 14 chapters, 28
   files (PDF + TXT), none failed, `audit_run.py --pdf-text` passes, 14 distinct bodies of 1,450–1,860
   words, no leftover story heading line. Headings: 1127, 1128, `1129.1-1130`, 1129, `1130-1131` …
   `1137-1138`, 1139, 1140.
4. **Resume:** failed pages were never written, and the wrong-chapter pages were set aside in the
   cache, so a Resume of the owner's run fetches 1130–1138 afresh and keeps everything else
   (separate-file mode fetches only those nine; grouped mode rebuilds the unfinished group without ever
   shrinking a fuller file). Tested offline end to end.

Verification: 1495 tests pass; `scripts/verify.py` PASS; the new tests fail when the fix is removed.

## 10. Owner overrides and autonomous completion mandate (2026-10-18)

The owner approved 03.2a and authorised autonomous completion of 03.2b through 07 for a **Windows-only
release candidate**, with these binding overrides (implemented first):
- **A.** Auto-clamp an oversized End to the reliably established latest chapter (ADR 011, §9.12).
- **B.** HTML cache and chapter manifests update automatically: new TOC discoveries extend the manifest,
  newly selected chapters are cached after validation, valid HTML is reused without network calls, the
  open-ended tail check uses a fresh index, completed novels extend on later runs, explicit
  refresh/invalidation stays available, nothing is pre-downloaded merely because it was listed, and no
  novel HTML/text/cache is committed (ADR 012, §9.12).
- **C.** Windows-only release target; macOS acceptance deferred without deleting portable code or Mac
  files (ADR 013).

Checkpoint commits continue on `feature/v0.3.0-modernization`; no merge, tag or release without
separate owner approval. The final step is an independent Codex verification handoff.
