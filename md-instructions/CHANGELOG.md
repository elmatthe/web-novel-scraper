# Changelog — webnovel-scraper

All notable changes to this project are recorded here. Versions follow semantic
versioning.

## [0.1.0] — 2026-06-24

### Phase 9 — Live-scrape hardening (2026-06-27)
- **9A — GUI rate-limit control.** The delay field is now the user-facing
  "Delay between fetches (seconds)" anti-detection knob: default **2.0s** (was
  1.2), fractional, validated non-negative, bound straight to `ScrapeJob.delay`.
  Labelled as a politeness/anti-detection control (higher = slower but less likely
  to be blocked). No new core plumbing — the field already threaded into the
  pipeline.
- **9B — Adaptive auto-slowdown.** New pipeline `_Pacer` raises the *effective*
  inter-fetch delay each time a chapter fetch is classified as a block/challenge
  (multiplier 1.5, floor 2.0s, ceiling 30.0s), logged, surfaced on
  `RunReport.auto_slowdowns` / `effective_delay`. Across-chapter pacing, on top of
  (not replacing) the request-manager's per-attempt exponential backoff — both
  coexist. The pipeline gained an injectable `sleep_fn` so this is fully testable
  without waiting.
- **9C — Relentless per-chapter retry + second-pass sweep.** Give-up threshold
  made explicit and generous: `MAX_RETRIES` and `ScrapeJob.max_retries` raised
  **4 → 6** (up to 7 escalating attempts; the later attempts keep hammering the
  strongest `camoufox_fresh` rung). After the main range, the pipeline runs a
  **second pass** over the non-permanent failed list at the auto-slowed delay —
  SEPARATE post-loop (rescued chapters written as their own PDFs), CHUNKED/SINGLE
  before the group/file PDF is written so a rescued chapter is included. New
  `RunReport.rescued` / `permanent_failed` fields. A true 403/404 is classified
  permanent and **short-circuits** (never swept) so a dead chapter can't hang a
  long run.
- **9D — WebNovel camoufox rescue (open Critical).** Found and fixed offline the
  most likely cause of "Cloudflare challenge still present after camoufox fetch" on
  WebNovel chapters: `request_manager.is_cloudflare_challenge` was mis-flagging a
  *cleared* post-redirect page as a challenge because that page still carries
  Cloudflare's ambient `/cdn-cgi/challenge-platform/` beacon script — the old
  single-marker `or` check read the beacon as a live challenge, so camoufox's good
  HTML looked like a challenge and the ladder escalated to failure. Detection is
  now content-aware in both `request_manager` and `cf_bypass`: strong interstitial
  markers (`just a moment`, `cf-browser-verification`, `cf_chl_opt`) flag
  immediately; ambient beacon markers flag only when no real page payload is
  present. Confirmed the `g_data.book` / `g_data.chapInfo` parse path is co-equal
  with `__NEXT_DATA__` and reachable from HTTP **or** a browser rung. Added
  `wnd_g_data_post_redirect_chapter.html` (g_data present, `__NEXT_DATA__` absent,
  beacon present) and `wnd_cloudflare_challenge.html` fixtures.
- **9E — Docs.** README + Briefing now note that browser-mode-off still
  auto-escalates to a browser engine on a block (a starting path, not a hard cap),
  and document the delay knob + auto-slowdown.
- Tests: new `files/tests/test_phase9.py` (16 offline cases across 9A–9D);
  `test_phase5_pipeline.py` exhausted-chapter test updated for the sweep. Suite:
  **101 offline tests**, `verify` green.
- Honest status on the WebNovel rescue: the over-eager-detection root cause is
  fixed and proven offline; whether camoufox clears a *genuinely* challenged
  WebNovel chapter on a given day stays live-site dependent and is for the user's
  manual pass to confirm. The code can no longer fail a chapter camoufox actually
  cleared.

### Phase 9 — Review fixes (2026-06-27)
- **Shared Cloudflare detector (Critical).** Extracted the challenge detector into
  a single new module `cloudflare_detection.py`; `request_manager` and `cf_bypass`
  now both import its `is_cloudflare_challenge` so the two can no longer drift
  (both names remain importable for callers/tests). Strong interstitial markers
  still flag immediately. The **length-only clearance was removed entirely** — a
  large body is no longer treated as a real payload by size. Ambient beacon markers
  (`/cdn-cgi/challenge-platform/`, `cf-mitigated`, `managed-challenge`) now clear
  only on **structural** evidence: a parseable `__NEXT_DATA__` blob, a
  `g_data.chapInfo` with a non-empty `contents` array, or a real chapter-body
  container element (`ChapterContent_content`, `cha-words`, `cha-content`,
  `m-read`, `class="txt"`) confirmed in the DOM — not a bare substring.
- **Auto-slowdown now sleeps after a block (Critical).** `_fetch_one` previously
  returned right after `pacer.register_block()`, so the newly-raised inter-fetch
  delay was not slept before the next chapter/sweep retry. It now sleeps the raised
  delay immediately after a non-permanent block.
- **Chunked sweep runs once over all failures (Minor).** Chunked mode no longer
  sweeps per chunk. The main pass fetches all chunks and collects non-permanent
  failures globally; a single second-pass sweep then re-attempts them once
  (permanent 403/404 excluded), slots each rescued chapter back into its own chunk,
  and the chunk PDFs are written last in the same filenames/order, sorted by index.
- **HTTP 401 is no longer permanent (Minor).** `_is_permanent_failure` regex
  tightened to `\bhttp\s*(?:403|404)\b`; 401 is now treated as transient (swept,
  triggers auto-slowdown) per policy.
- **Pacer base delay clamped to the ceiling (Minor).** The auto-slowdown ceiling
  (30.0s) is now an absolute cap including the user-supplied base delay — `_Pacer`
  clamps `current` at construction, so `RunReport.effective_delay` never exceeds it.
- Tests: 5 new regression cases in `test_phase9.py`
  (`test_single_ambient_beacon_without_payload_is_challenge_or_escalates`,
  `test_auto_slowdown_sleeps_after_block`,
  `test_chunked_sweep_runs_once_over_all_non_permanent_failures`,
  `test_http_401_is_not_permanent_and_is_swept`,
  `test_pacer_initial_delay_clamped_to_ceiling`). Suite: **106 offline tests**,
  `verify` green.

### Post-live-pass Critical mitigation - FreeWebNovel Cloudflare resilience (2026-06-27)
- Fixed the fatal/mass-failure behavior from the live-discovered FreeWebNovel
  Cloudflare Critical: a first-time uncached chapter fetch that hits a CF block
  now retries through a central request-manager ladder instead of failing once
  and moving on immediately.
- `RequestManager.fetch()` now uses configurable retry defaults
  (`max_retries=4`, `retry_base_delay=5.0`) with exponential backoff + jitter,
  permanent 403/404 short-circuiting, retryable 5xx/network/Cloudflare handling,
  and ordered strategy escalation: plain HTTP -> cloudscraper -> Playwright
  stealth -> fresh Playwright browser context/new UA.
- Threaded retry knobs through `ScrapeJob`, pipeline-created managers, and the
  GUI-owned manager construction so GUI controls can expose them later without a
  request-layer API change. No GUI controls were added in this pass.
- Strengthened the browser path so Playwright navigation sees permanent 403/404
  statuses as non-retryable failures, while repeated CF/browser failures rotate
  through fresh contexts.
- Added offline regression coverage: retry succeeds on attempts 2/3/4,
  escalation advances per attempt, browser-mode starts on the browser ladder,
  permanent 404 does not retry, backoff sleeps are computed/injected without
  waiting, and an exhausted chapter failure is recorded in `RunReport.failed`
  while the pipeline continues to the next chapter and lists the failed chapter
  in the summary.
- Status is honest: scraper resilience is fixed/closed (a blocked chapter cannot
  halt or mass-fail the run path), but Cloudflare bypass success is still
  live-site dependent until the user's manual FWN scrape validates current
  behavior. Suite: **82 offline tests**, `verify` green.

### Phase 8 — Bug hunt + release pass (2026-06-27)
- Ran the AI-WORKSPACE bug hunt across **every** module (not just recently
  touched ones): deprecated libraries, missing error handling, hardcoded paths,
  fresh-machine breakage, unhandled edge cases (empty TOC, zero/out-of-range
  range, all-chapters-fail, TOC-fetch failure, corrupt cache, output-dir
  errors), GUI thread-safety, resource leaks, imports/circular-imports, debug
  artifacts, and platform-neutrality.
- **No Critical defects found.** Verified: all 15 package modules import under
  the venv interpreter; every worker-thread UI update is marshalled via
  `self.after`; the `RequestManager` is torn down in a `finally` on every GUI
  error path; the pipeline records-and-skips a failed chapter, refuses disabled
  rows before building an adapter, and writes no partial chunk/single PDF on
  cancel; corrupt cache reads and index loads are caught and rebuilt; no bare
  `except:`; no `os.startfile` / `cmd /c` / `subprocess` shell-out / `\`-literal
  or `C:\Users\…` path in the package (only `pathlib` / `Path.home()` /
  `__file__`-relative). Edge probes (all-heading-only PDF, degraded-chapter
  filename + resume match, all-fail) confirmed graceful degradation, no crash.
- **No code was changed** — there were no criticals to fix, so the suite is
  unchanged at **71 offline tests** and `verify` stays green.
- Six **Minor/Suggestion** items were logged (not fixed — flagged for the user
  per AI-WORKSPACE): (1) `webnovel_dynamic` persists/loads its own
  `chapter_index.json` unconditionally, ignoring the "Use HTML cache" toggle and
  redundant with the pipeline's TOC persistence — can serve a stale TOC across
  runs over time; (2) the GUI headless checkbox defaults off (visible browser)
  while the README says "Leave Headless on"; (3) a degraded chapter's SEPARATE
  filename is `Chapter N..pdf` (double dot); (4) `spec.use_browser` mutates the
  shared catalog `SiteSpec` (reset each run, harmless); (5) `PdfReader` in
  `remove_single_heading_pages` is not closed (handle leak); (6) a PDF that fails
  mid-write would be treated as complete by resume (silent skip on re-run).
- Wrote the final manual release log to
  `files/test-logs/v0.1.0_pre-release.md` (AI-WORKSPACE template): per-module bug
  hunt, launcher first-run portability walkthrough (reasoned against the §7
  checklist since a clean no-Python machine wasn't available), GUI load,
  novel/site selection incl. disabled greying, resume/Stop/output offline proofs,
  and the live FreeWebNovel + WebNovel-dynamic scrapes across all three output
  modes — structured but marked `[-]` skipped ("requires manual live test") for
  the user's own pass. Remaining for 0.1.0: the user's manual live pass, then tag
  + force-push, then delete the implementation-plan drop.

### Phase 7 — Windows launcher + README
- `Setup_and_Run-Web-Novel-Scraper.bat`: finalized to the plan §7 spec and the
  launcher portability checklist. The bulk of the script (self-location via
  `%~dp0`; `py -3`→`python`→existing-venv detection with a 3.10+ warn;
  user-scope-only Python install — winget `--scope user`, python.org
  `InstallAllUsers=0 PrependPath=1 /passive` fallback, re-detect after install;
  self-healing `.venv`; Playwright Chromium contained in
  `files\bin\ms-playwright` via `PLAYWRIGHT_BROWSERS_PATH`; SmartScreen first-run
  note; `pause` on every failure) was already in place from the restructure and
  was reviewed clean. Two finalization fixes this pass:
  - **Launch via `pythonw`** (was `python`). The GUI now starts through the
    venv's `.venv\Scripts\pythonw.exe`, so the tkinter window is the only new
    window — no extra console appears behind it (the Phase 6 flag). The setup
    window remains as the live log. Falls back to plain `python` only if
    `pythonw.exe` is missing from the venv.
  - **Idempotent dependency install.** After a successful install the script
    copies `requirements.txt` to `.venv\requirements.lock`; on later runs it
    binary-compares (`fc /b`) the two and **skips** the pip install (and prints
    "Dependencies already up to date") unless they differ or the venv was just
    rebuilt — so a second double-click goes straight to the GUI, satisfying §7's
    "subsequent double-clicks just open the GUI fast."
- `Setup_and_Run-Web-Novel-Scraper.command`: the already-written full macOS
  launcher (mirrors the `.bat`, user-scope Homebrew Python) carrying a "not yet
  runtime-tested on a Mac" header note — left as-is. macOS runtime remains a
  later pass; the Python package is platform-neutral so that pass is
  launcher-only.
- `README.md`: rewritten from the stale legacy editor readme into a minimal,
  user-facing guide — what it does (one paragraph), the supported novels/sites
  table with 0.1.0 status (available vs. coming soon), a no-terminal double-click
  quick start, the SmartScreen first-run note, where output lands
  (`~/Downloads/webscraped_{slug}-N/`), a browser-mode note for the
  Cloudflare-protected Free Web Novel, and the Windows-only/macOS-later platform
  note. No dev setup, test, or architecture detail (that lives in `Briefing.md`).
- Suite unchanged at 71 offline tests; `verify` green.

### Restructure — AI-WORKSPACE cross-platform layout (2026-06-25)
Structural refactor only (no feature changes) to conform the repo to the rewritten
`AI-WORKSPACE.md`. Filesystem moves only — no git run this session (the user force-pushes
the full repo as one release).
- **`Scripts/` → `scripts/`** (lowercase). The OS split now lives *inside* `scripts/`:
  shared code moved to **`scripts/Universal/`** — the `webnovel_scraper/` package and
  `app.py`. `requirements.txt` and `verify.py` stay directly under `scripts/`.
  `scripts/Windows/` and `scripts/MacOS/` scaffolded with `.gitkeep` (Windows + Universal
  implemented this pass; macOS runtime deferred).
- **Tests are dev-only → `files/tests/`.** Moved the whole pytest suite (`test_phase1–4`,
  `test_regression`, `test_scaffold`, `__init__.py`) out of `Scripts/tests/`. Added
  `files/tests/conftest.py` that inserts `scripts/Universal/` onto `sys.path` so
  `import webnovel_scraper` resolves regardless of where pytest is invoked.
- **Fixtures → `files/test-files/`** (from `files/fixtures/`); `files/test-logs/` added for
  the final manual release pass.
- **Path fixes:** `request_manager.py` repo-root depth `parents[2]`→`parents[3]` (one level
  deeper under `Universal/`); the three adapters' tests now read `files/test-files/`;
  `verify.py` points pytest at `files/tests/` (deps still at `scripts/requirements.txt`).
- **Launchers** rebuilt from the new templates as
  `Setup_and_Run-Web-Novel-Scraper.bat` / `.command` — self-locating via the script dir,
  user-scope Python (winget `Python.Python.3.12` / Homebrew `python@3.12`), self-healing
  `.venv` in root, installs `scripts/requirements.txt`, downloads Playwright Chromium
  contained in `files/bin/ms-playwright`, launches `scripts/Universal/app.py`. The old
  generic-named stub launchers were replaced.
- **`.gitignore`** reconciled to the new hygiene list (adds `files/bin/`, `files/test-logs/`,
  `.env`; keeps `files/test-files/` un-ignored).
- **Docs:** plan §2 / deletion-test / launcher references and the `Briefing.md` architecture
  section updated to the new layout; `handoff.md` populated (de-templated).
- **Legacy reference** (old editor/scraper scripts, the `Index_Names_Lists/` lexicons, and
  the old `test_heading_extraction.py` harness) consolidated under `files/legacy-reference/`
  — kept for porting reference (Phase 5), out of the shipping `scripts/`.
- `verify` green after the move: **60 tests passed.**

### Phase 6 — Single-window tkinter GUI
- `app.py`: real `ScraperApp(tk.Tk)` (replaces the Phase-0 stub) — a thin shell
  over the tested pipeline, with no scraping/parsing/PDF logic of its own.
  - **Catalog-driven dropdowns.** A Novel `ttk.Combobox` (titles, catalog order)
    and a Site selector built as a `ttk.Menubutton` + `tk.Menu` so disabled
    rows render **greyed and non-selectable** with a "(coming soon)" suffix
    (`ttk.Combobox` cannot disable individual items). Changing the novel
    repopulates the Site menu and auto-selects its first enabled site; a novel
    with no enabled site shows a hint and leaves Start disabled.
  - **Controls.** Start chapter / End chapter (blank end = all chapters via an
    `ALL_CHAPTERS = 10**9` sentinel the pipeline clamps to the TOC), delay
    (default `1.2` s, from the legacy FreeWebNovel GUI), timeout (default 30 s),
    an output-mode radio (Separate / Chunked with a chapters-per-PDF entry
    enabled only in Chunked / Single), "Use Playwright browser mode
    (recommended for Cloudflare)" + "Headless browser" checkboxes, "Use HTML
    cache (resume re-runs)" (default on), a determinate `ttk.Progressbar`, a
    `scrolledtext` log pane, and Start / Stop buttons.
  - **Browser toggles are adapter-aware.** Enabled only for browser-capable
    adapters (`_BROWSER_CAPABLE_ADAPTERS = {"freewebnovel"}`); inert (disabled)
    for the HTTP-only `webnovel_dynamic`. Headless is enabled only while browser
    mode is both available and checked.
  - **Threading + cancel.** The scrape runs on a `daemon` thread; **every** UI
    update is marshalled back with `self.after(0, ...)` (log lines and the
    `(done, total)` progress callback). Start builds a `ScrapeJob`, resolves the
    output dir via `pipeline.resolve_output_dir`, constructs a `RequestManager`
    (headless/cache from the toggles, `log_fn` → the log pane), and calls
    `pipeline.run_scrape` with a fresh `threading.Event`. Stop sets that event;
    the request-manager raises `ScrapeCancelled` and the pipeline halts between
    chapters. The worker closes the `RequestManager` in a `finally` (the GUI
    owns it, since the pipeline only closes managers it creates itself).
  - **Wiring without touching tested modules.** The timeout field is honoured by
    setting the module-level `request_manager.FETCH_TIMEOUT` (read from globals
    at call time in `_get_text`); the browser toggle by setting `spec.use_browser`
    on the resolved catalog row for the run. Neither modifies Phase 2/5 code.
  - **Disabled-adapter gating (acceptance criterion).** Start refuses to dispatch
    a disabled site (greyed in the menu, Start disabled when none is enabled) —
    GUI gating on top of the Phase 5 pipeline-layer refusal.
- No headful GUI test is added to `verify` (per plan §8 Phase 6); all real logic
  remains in the tested modules. Suite unchanged at 71 offline tests, `verify`
  green. GUI wiring was smoke-checked by instantiating the window, driving the
  selection/validation handlers, and tearing it down (no manual click-through).

### Phase 5 — Pipeline + output modes + resume
- `pipeline.py`: real `run_scrape` orchestration (replaces the stub) — TOC-first,
  resumable, three output modes, run report, cancellation.
  - **Disabled-adapter refusal (defense in depth).** Resolves the `SiteSpec`
    from the catalog and raises `AdapterDisabledError` for a disabled row
    **before the adapter is built or any method is called** — the pipeline-layer
    guard, on top of GUI greying (Phase 6) and the stub's `NotImplementedError`.
  - **TOC + persistence.** Calls `adapter.build_chapter_index(spec)` once and
    writes the result to `chapter_index.json` in the output dir; a re-run into
    the same dir loads it instead of re-fetching the TOC (part of resume).
  - **Range clamp.** Clamps the requested `[start, end]` to the available TOC
    range and logs the adjustment; a range fully outside the TOC is surfaced as
    a warning, not an error.
  - **Resume + fault tolerance.** Skips a chapter whose PDF already exists
    (matched off the filename's `Chapter N` prefix, so no network is touched);
    a single failed `fetch_chapter` is recorded in the report and skipped, never
    fatal. The per-fetch `delay` and a `threading.Event` `cancel_event` (the
    Stop button) are honoured between chapters; a partial chunk/single PDF is
    never written on cancel (so resume stays correct).
  - **Output modes** (each through `pdf_builder.create_pdf`, which strips
    heading-only pages): SEPARATE — one PDF per chapter named
    `safe_filename(heading).pdf`; CHUNKED — `{Stem}_Chapters_{a}-{b}.pdf` for
    each group of `chunk_size`; SINGLE — `{Stem}_All_Chapters.pdf`.
  - **`resolve_output_dir(slug)`** returns the next free
    `~/Downloads/webscraped_{slug}-N` via `Path.home()` (platform-neutral; the
    path is created by the run, not the resolver). **`RunReport`** carries the
    written / skipped / failed lists, the requested vs. effective range,
    aggregated adapter warnings, a `cancelled` flag, and a `summary()` with a
    resume hint for failures.
- Tests: `tests/test_phase5_pipeline.py` (11 offline cases, driven by a fake
  adapter — no network): the three output modes produce the correct file sets;
  resume skips existing separate + single PDFs (and does not rebuild the TOC);
  range clamping; the pipeline refuses a disabled row and never calls the
  adapter (verified for all three stubs with an `ExplodingAdapter`);
  cancellation stops iteration mid-run and on a pre-set event; output-dir
  auto-increment. Suite: 71 offline tests.
- Pre-task hygiene: removed the duplicated inline-comment `.gitignore` lines for
  the `files/` runtime dirs (the clean standalone patterns above them already
  applied; the duplicates were dead weight, not active rules).

### Phase 4 — WebNovel-dynamic adapter
- `adapters/webnovel_dynamic.py`: real `WebNovelDynamicAdapter` (replaces the
  stub), ported from `scrape_noble_queen-v3.py`.
  - **`build_chapter_index`** fetches the novel's main page and parses the
    embedded `__NEXT_DATA__` JSON (`props.pageProps.data.volumeItems[].`
    `chapterItems[]`) into an ordered `ChapterMeta` list; chapter URLs are built
    as `{base_url}/story/{book_id}/{chapterId}`. The result is persisted to
    `files/cache/{slug}/chapter_index.json` (ported `load_or_build_chapter_index`
    behavior) so re-runs skip the TOC fetch; `force_refresh=True` rebuilds. A
    missing/malformed `__NEXT_DATA__` block, or a TOC that parses to an empty
    list, raises a clear `RuntimeError` (never a silent empty index). Index gaps
    and a `bookInfo.chapterCnt` mismatch are surfaced as `adapter.warnings`.
  - **`fetch_chapter`** prefers the `__NEXT_DATA__` `chapterInfo.contents[].`
    `content` path and falls back to DOM parsing over `CONTENT_SELECTORS` when
    the JSON content is absent. `_is_junky` strips nav/comment/reader-chrome
    paragraphs; only whitespace/empty-paragraph trimming is applied to the body.
    Heading is normalized to the bare title (`_title_only`); the shared
    `ChapterContent.heading` applies the `Chapter N: Title.` wrap, with a
    degraded title rendering `Chapter N.`.
  - **Dropped** (per plan §4.8 / §5): `load_do_not_touch`, `apply_with_guard`,
    `_NQ_MASTER_INDEX`, `V2_DECORATIVE_REPLACEMENTS`, `_clean_text_chars`,
    `clean_text`, `normalize_paragraph`, all FPDF layout, and the novel-specific
    `_is_junky` brand guard (`"noble queen" + "shadow slave"`).
- `catalog.py`: The Noble Queen `webnovel_dynamic` row now carries
  `base_url="https://dynamic.webnovel.com"` (alongside the existing
  `book_id="28684090500376805"`).
- Tests: `tests/test_phase4_wnd.py` (9 offline cases — TOC parse count/order/
  titles/URLs, `__NEXT_DATA__`-absent raises, JSON-path body extraction, DOM
  fallback extraction, `_is_junky` filtering, junk-paragraph filtering, index
  persistence reload-without-network, and registry/disabled-adapter consistency
  for The Noble Queen). New fixtures under `files/fixtures/`:
  `wnd_next_data_toc.html`, `wnd_next_data_chapter.html`,
  `wnd_dom_fallback_chapter.html`, `wnd_no_next_data.html`,
  `wnd_junky_content.html`. Suite: 60 offline tests.

### Phase 3 — FreeWebNovel adapter (+ pre-Phase-3 corrections)
- `adapters/freewebnovel.py`: real `FreeWebNovelAdapter` (replaces the stub),
  generalized over all four FWN novels via the catalog index URL (no per-novel
  hardcoded regex).
  - **`build_chapter_index` uses Approach B (generated URLs).** The index page is
    consulted ONLY to learn the highest chapter number; chapter URLs are then
    generated from a template over `1..count`, which is gap-free by construction.
    This is the fix for the legacy silent-skip bug (FreeWebNovel's landing page
    renders only a slice of the chapter list, so the old map-lookup dropped any
    chapter in the unrendered gap — the 2261–2327 bug). `spec.chapter_count`
    overrides the discovered count and surfaces a warning on disagreement.
  - **`fetch_chapter`** ports the title extraction (`_candidate_title_texts`,
    invisible-char stripping, two-pass prefer-titled heading, sibling-merge,
    site-junk title trimming) and body extraction (best-scoring container,
    `NOISE_SNIPPETS`, consecutive-paragraph dedup). Degraded (title-less)
    chapters keep their body and render as `Chapter N.`, recorded in
    `adapter.warnings`.
  - **Dropped** (per plan §3.2/§5): `_clean_ri_rni_paragraphs`, the RI/RNI
    title-form + em-dash + prose-editing credit regexes, `_RI/_RNI_MASTER_INDEX`,
    and `EDITOR_MAP` coupling. A generic translator-credit *noise* filter and
    consecutive-duplicate-chapter skip are kept as extraction-level cleanup.
- `models.py` `SiteSpec`: added `use_browser` (default False — HTTP first,
  browser only on a CF challenge), `base_url`, and `chapter_count`.
- `catalog.py`: FWN rows now carry `base_url="https://freewebnovel.com"`.
- `request_manager.py` (FIX E): added a unified `fetch(url, *, use_browser,
  use_cache)` facade over `fetch_html` / `fetch_html_browser`, a
  `cancel_event` (`threading.Event`) checked at the start of `fetch` raising the
  new `ScrapeCancelled`, and a no-op `start()` placeholder.
- `verify.py` (FIX A): pytest exit code 5 ("no tests collected") is now a FAIL —
  an empty gate is not a valid gate; only exit 0 passes.
- Plan reconciliation (FIX B) + `Briefing.md` (FIX C): blessed the implemented
  names `SiteSpec` / `ChapterMeta` / `ChapterContent` and `build_chapter_index` /
  `fetch_chapter` as canonical (retired `NovelRef` / `ChapterStub` / `Chapter`
  and `build_toc` / `parse_chapter`).
- `.gitignore` (FIX D): `.pytest_cache/` (already present) confirmed; added
  `.agents/` (tool-generated scratch, not in the user's locked root layout).
- Tests: `tests/test_regression.py` (6 cases ported from the legacy
  `test_heading_extraction.py` / `test_chapter_range_loop.py` knowledge —
  invisible-char titles, duplicate-heading dedup, translator-credit filtering,
  degraded-title fallback, chapter-count-mismatch warning, duplicate-content
  skip) and `tests/test_phase3_fwn.py` (TOC completeness/no-gaps, title + body
  extraction, noise filtering, degraded fallback, URL generation). New fixtures
  under `files/fixtures/`: `fwn_toc.html`, `fwn_chapter_clean.html`,
  `fwn_chapter_degraded.html`, `reverend_insanity_chapter_2261.html`. Plus FIX E
  facade/cancel tests in `test_phase2.py`. Suite: 51 offline tests.

### Phase 2 — Request manager + PDF builder
- `request_manager.py`: unified `RequestManager` (fetch only). HTTP path ported
  from `scrape_noble_queen-v3.py` (Chrome headers, retry/backoff, 403/404 permanent
  skip, 5xx retry) with a `cloudscraper` fallback on a detected Cloudflare challenge;
  Playwright stealth browser path via `cf_bypass.py` with a reused browser instance
  and `close()`. Shared on-disk cache at `files/cache/{slug}/`, keyed by SHA-256 URL
  hash. Retry/backoff constants are module-level. Permanent failures raise `FetchError`.
- `pdf_builder.py`: the single ReportLab layout ported behavior-verbatim from
  `sm_pdf_editor-v8.2.py` (`create_pdf_from_text`, `_is_chapter_heading`, `_escape_html`,
  the heading/merged-heading regexes, `remove_single_heading_pages`). PDF
  post-processing now uses `pypdf` (was deprecated PyPDF2). Added pipeline-facing
  `chapters_to_text()` and `create_pdf(chapters, output_path, title)`. None of the
  editor's spellcheck/lexicon/profanity/OCR/GUI code was ported.
- `tests/test_phase2.py`: 14 offline tests (cache-key determinism, cached fetch with
  no network, faked-session 404→FetchError + 200 success, CF detection, retry
  constants, heading detection accept/reject, feed-text joining + form-feed, real
  PDF render, heading-only-page removal).

### Phase 1 — Models, catalog, base adapter, registry, stubs
- Added the placement "deletion test" to the implementation plan (§2): code that
  the Python environment executes lives in `Scripts/`; data that can be deleted
  without breaking execution lives in `files/`.
- Corrected the fixtures location from Phase 0: moved `Scripts/tests/fixtures/` →
  `files/fixtures/` (committed reference data, tracked, not gitignored) and updated
  `.gitignore` negations accordingly.
- `models.py`: `OutputMode` enum (SEPARATE/CHUNKED/SINGLE) plus `SiteSpec`,
  `ChapterMeta`, `ChapterContent`, and `ScrapeJob` dataclasses (pure data, no I/O at import).
- `catalog.py`: all 8 novel/site rows encoded as data with `all_novel_slugs()`,
  `get_adapters_for_novel()`, `get_enabled_adapters_for_novel()`, and `get_spec()`.
- `adapters/base.py`: `BaseAdapter` ABC (`build_chapter_index`, `fetch_chapter`)
  with shared `is_enabled()` and ported `safe_filename()` (hardened to also strip
  path separators for cross-platform safety).
- `registry.py`: `register()`/`get_adapter()`/`get_adapter_for_spec()`; the last
  enforces the pipeline-layer disabled-adapter guard via `AdapterDisabledError`.
- Adapter modules: enabled skeletons (`freewebnovel`, `webnovel_dynamic`) and
  disabled stubs (`empire_novel`, `novel_bin`, `telegraph`), all registered.
- `tests/test_phase1.py`: 15 offline tests across models, catalog, and registry
  (incl. the disabled-adapter refusal).

### Phase 0 — Scaffold
- Initialized the git repository and set the working branch to `feature/v0.1.0-build`.
- Added `.gitignore` (venv, bytecode, build artifacts, runtime cache/output, output PDFs;
  keeps committed test fixtures).
- Added pinned `Scripts/requirements.txt` (exact `==` pins resolved from `.venv`):
  requests, beautifulsoup4, cloudscraper, playwright, playwright-stealth, reportlab,
  pypdf, pdfplumber, pytest.
- Added `Scripts/verify.py` — the mechanical verify gate (runs pytest, enforces
  exact-`==` dependency pinning, checks the docs are de-templated). Runs before every
  phase commit.
- Created the `webnovel_scraper/` package skeleton: `models`, `catalog`, `registry`,
  `request_manager`, `pdf_builder`, `pipeline`, and the `adapters/` package (`base`,
  `freewebnovel`, `webnovel_dynamic`, plus disabled stubs `empire_novel`, `novel_bin`,
  `telegraph`). Added `app.py` entry-point stub and a `tests/` smoke test.
- Ported `cf_bypass.py` verbatim into the package.
- De-templated `Briefing.md` and `CHANGELOG.md` with real project content.

### Dependency changes vs. the legacy scripts
- **Dropped:** `nltk`, `language-tool-python`, `symspellpy`, `fpdf2`, `PyPDF2` — all tied
  to the removed prose-editing pipeline or the old FPDF layout. `PyPDF2` (deprecated) is
  replaced by `pypdf` for PDF post-processing.
- `camoufox` remains optional/commented (strongest CF bypass) — not required.
