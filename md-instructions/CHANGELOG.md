# Changelog — webnovel-scraper

All notable changes to this project are recorded here. Versions follow semantic
versioning.

## [0.1.3] — 2026-06-29

Headful-browser-primary pass for FreeWebNovel, matched to the legacy scraper. Root
cause of the persistent FWN Cloudflare failures: the rewrite went **HTTP-first +
headless + a six-engine escalation ladder**, and FWN's Cloudflare clears for a
*visible real browser* but blocks *headless automation* — and the relaunch storm
made it more aggressive. The legacy scraper avoided all of this by running one
persistent VISIBLE browser, one fetch per chapter. This release adopts that
architecture as a **bounded two-engine headful ladder**: headful camoufox primary
(persistent, warmed, reused), then a bounded fallback to **headful stealth-Chromium**
— the exact legacy visible engine — when camoufox can't clear a chapter, with no
return to the relaunch storm.

### Legacy diff (independently verified this session)
Unlike the 0.1.2 pass (whose non-git working copy lacked the gitignored legacy
file), this real clone contains `files/legacy-reference/freewebnovel-webscraper.py`,
so the claims were checked against it:
- **Confirmed.** The legacy GUI defaulted `playwright_var=BooleanVar(value=True)`
  and `headless_var=BooleanVar(value=False)` (lines 1594–1595) — a VISIBLE browser
  from request #1.
- **Confirmed.** `HtmlFetcher.start()` created ONE browser+context+page and
  `fetch()` reused that same `self._page` for every chapter (lines 351–497). The
  only recreation was `reset_browser()` on a Cloudflare/timeout retry (lines
  1311–1352), with a backoff schedule — never a per-chapter engine ladder.
- **Confirmed.** With `use_playwright=True` (the default) the legacy went straight
  to the browser branch and never did HTTP-first; there was no 6-rung escalation
  ladder.
- **Confirmed (no special trick).** No bespoke header/UA/cookie/warm-up scheme —
  the working ingredient was simply *headful + persistent + one-fetch-per-chapter*.
- **One correction.** The legacy gated camoufox behind `and self.playwright_headless`
  (line 379), so in its **default visible** config it actually used **headful
  stealth-Chromium** (`create_stealth_browser`), not camoufox — camoufox was its
  *headless-only* path. The architectural fix (headful + persistent + reuse) is
  engine-independent. 0.1.3 uses **headful camoufox** as the primary engine (stronger
  anti-detect, and the one this codebase already warms/reuses) **and then falls back
  to headful stealth-Chromium** — the exact engine the legacy scraper's visible
  default used, the one historically proven to clear FWN's Cloudflare — when camoufox
  can't clear a chapter. Best of both, still bounded.

### Changed — headful camoufox is the primary, default FreeWebNovel fetch path
- **Defaults flipped to visible browser-primary.** `RequestManager.headless`
  default `True → False` (request_manager.py); the four FreeWebNovel catalog rows
  now carry `use_browser=True` (catalog.py) so a FWN scrape runs through camoufox
  in a VISIBLE window from request #1; the GUI "Headless browser" checkbox defaults
  **OFF** (`app.DEFAULT_HEADLESS True → False`) and browser mode defaults **ON**
  (`DEFAULT_BROWSER_MODE`). GUI, job, and spec defaults agree. A visible camoufox
  window WILL appear during a FWN scrape — expected, like the old tool.
- **One persistent warmed VISIBLE browser, reused across chapters.** The camoufox
  browser+context+page is created once per run and reused for every chapter (the
  existing lazy `_ensure_camoufox_page` reuse). New `_warm_camoufox_session`:
  before the first chapter on a host, the same page navigates to the site origin so
  Cloudflare seats a `cf_clearance` cookie in the **browser** context (the 0.1.2
  warm-up only warmed the HTTP session). Warm-up is once per host per browser,
  best-effort, and cleared when the browser is recreated. A normal successful
  chapter fetch never recreates the browser/context/page.
- **Bounded TWO-engine headful ladder (camoufox → stealth-Chromium).**
  Browser-primary fetches walk a SHORT bounded ladder
  `HEADFUL_PRIMARY_LADDER = (camoufox, camoufox, camoufox_fresh, playwright_stealth)`
  — a couple of same-page camoufox retries, ONE fresh-camoufox recovery, then ONE
  escalation to **headful stealth-Chromium** (`cf_bypass.create_stealth_browser` /
  `fetch_with_stealth`, run VISIBLE) — with the retry budget capped to the ladder
  length in `fetch` (**per-chapter cap: 4 attempts**, or 5 with HTTP-first). This
  brings back ONLY the legacy's proven engine, not the storm: there is no
  `playwright_stealth_fresh`, no cloudscraper/http on the default browser path. The
  stealth-Chromium engine obeys the contained `PLAYWRIGHT_BROWSERS_PATH →
  files/bin/ms-playwright` (the Chromium the launcher already installs) and a
  missing/unlaunchable Chromium is an immediate non-blocking strategy failure (no
  100-second freeze; chapter recorded, run continues). **Stealth engine is
  persistent + reused:** once camoufox is exhausted for the run (a chapter reached
  the stealth rung), a latch (`_camoufox_exhausted`) routes later chapters and sweep
  retries straight to the one persistent stealth-Chromium browser
  (`STEALTH_LATCHED_LADDER`, ≤2 same-page attempts) — never relaunched per chapter
  (required because the two engines' sync-Playwright loops cannot coexist on a
  thread, so replaying camoufox between fallbacks would force a Chromium relaunch).
  The end-of-run sweep is unchanged (one pass over non-permanent failures) and is
  therefore bounded; it now also gets the camoufox→stealth-Chromium fallback.
  Failed-chapter recording, run-continues resilience, and the auto-slowdown pacer are
  all retained.
- **HTTP-first is now explicit opt-in.** All HTTP/cloudscraper code is kept. A new
  GUI checkbox "Try fast HTTP first (may trip Cloudflare)" defaults **OFF**
  (`ScrapeJob.http_first` / `RequestManager.try_http_first`); when enabled the
  browser-primary path tries two cheap HTTP rungs before camoufox
  (`HTTP_FIRST_PRIMARY_LADDER`). Non-Cloudflare paths are untouched:
  WebNovel-dynamic stays `use_browser=False` on its plain-HTTP fast path, and the
  legacy `DEFAULT_ESCALATION_LADDER` (with the stealth rescue rungs) still backs
  the non-browser path. All 0.1.1/0.1.2 fixes (brotli, EmptyExtractionError,
  output-folder, non-blocking launch, contained Chromium, HTTP warm-up, resumable
  pipeline, three output modes, PDF build, catalog) are preserved.

### Fixed — premature read + cleared-page CF misdetection on the camoufox FWN path
A live chapter-102 test proved (visually, on screen) that headful camoufox **does**
clear FreeWebNovel's Cloudflare challenge — the real chapter rendered in the window —
yet the scraper logged "Cloudflare challenge still present after camoufox fetch" on
every attempt, retried, then hung on the stealth rung. The browser had the real
content; the scraper threw it away. Root cause was **detection/timing, not bypass**:
- **Cleared-page misdetection (primary).** The shared `cloudflare_detection.has_real_payload`
  structural check (Phase 9D, built for WebNovel) only recognized WebNovel's containers
  plus two *incidental* FWN wrapper classes (`.m-read` and the brittle `class="txt"`
  exact-substring). It had **no knowledge of FreeWebNovel's actual primary content
  container, `<div id="article">`**. On a live camoufox-cleared FWN chapter — which
  still carries Cloudflare's ambient `/cdn-cgi/challenge-platform/` beacon, and whose
  `class="txt"` substring does not survive Firefox serialization / inline-style /
  multi-class variance — `has_real_payload` returned `False`, the ambient beacon then
  tripped `is_cloudflare_challenge → True`, and the fetched chapter was discarded at
  `request_manager._fetch_camoufox_once`. The saved fixtures never reproduced it
  because they were captured *without* the ambient beacon. **Fix:** the shared detector
  is now content-aware for FWN — `#article` (the adapter's stable id-based container,
  both quote styles) plus the FWN body selectors were added to the structural
  real-payload check, with a **non-trivial-text guard** (`_MIN_BODY_TEXT_CHARS`) so an
  empty `<div id="article"></div>` shell on a challenge template never false-clears.
  The change only ever makes the detector recognize *more* real content — it cannot
  newly flag a page that previously cleared.
- **Premature read / wait-for-clearance (secondary).** `cf_bypass.fetch_camoufox` now
  POLLS positively for the real chapter DOM: it breaks the moment `has_real_payload`
  is true (even with the ambient beacon still present) and otherwise keeps waiting
  while the page still looks like a challenge — so capture never happens during the
  post-clearance transitional window where the interstitial is gone but the body has
  not yet rendered. A non-chapter origin GET (session warm-up) still returns promptly.
- **Net effect:** with both fixes, a chapter camoufox clears on screen now WRITES on
  the first camoufox attempt — no escalation, no stealth hang. The stealth-Chromium
  fallback (already bounded + firm-timeout) remains the last resort but should rarely
  fire on FWN now.

### Tests / docs
- `files/tests/test_camoufox_cleared_detection.py` (NEW, 6 offline cases): the exact
  live regression — a cleared FWN page with `#article` body **plus** the ambient
  beacon is classified CLEARED by both detector re-export sites and the browser fetch
  succeeds on a single camoufox attempt (no escalation); `fetch_camoufox` **waits**
  through transitional empty-body reads and captures only the populated chapter; a
  genuine interstitial is still flagged and still escalates (retryable); an empty
  `#article` shell with only the ambient beacon is still a challenge (text guard).
- `files/tests/test_headful_camoufox.py` (now 20 offline cases, both engines mocked
  at the `cf_bypass` + `sync_playwright` seams — no real launch): FWN browser-primary
  + visible + HTTP-first-off defaults agree; camoufox created ONCE and warmed once on
  the happy path; a normal success is a single camoufox attempt; a blocked chapter
  escalates to the stealth-Chromium fallback **exactly once** (per-chapter cap 4);
  the stealth fallback is **headful** (headless=False asserted) and its browser is
  **created once per run and reused** across multiple fallback chapters (run latch);
  a stealth Chromium launch failure is non-blocking (no backoff, chapter recorded,
  run continues); the end-of-run sweep can rescue via the camoufox→stealth fallback;
  headless can be forced; HTTP-first can be opted in; the non-browser path stays on
  HTTP. Updated `test_phase2.py` (bounded two-engine ladder + HTTP-first-opt-in) and
  `test_phase8_gui.py` (headless OFF / browser ON / HTTP-first OFF). Suite: **163
  offline tests**, `verify` green.
- **Honest status:** offline tests prove the wiring/flow only. With the detection/timing
  fix above, the next live chapter-102 test should show **chapter 102 WRITE on the first
  camoufox attempt** (the browser already clears it on screen) — no escalation, no hang.
  If a chapter ever genuinely cannot clear live, the stealth-Chromium fallback is still
  there, and the last lever is a residential proxy or a manual solve in the visible window.

## [0.1.2] — 2026-06-29

Cloudflare-avoidance pass driven by a live work-PC run (Shadow Slave, chapters
100–110, fresh GitHub zip) that surfaced three problems: slow + repeated CF
challenges, the playwright-stealth rungs crashing on a fresh install because
setup never downloaded Chromium, and the run **freezing** on a 100-second backoff
before retrying a browser launch that structurally could not succeed.

> **Honest note on the headline (Task 1):** the legacy FreeWebNovel scraper
> (`files/legacy-reference/freewebnovel-webscraper.py`) is **gitignored**
> (`.gitignore`: `files/legacy-reference/`) and therefore was **not present** in
> the working tree this session, so it could **not** be diffed against the current
> request layer. Rather than invent a "legacy trick," this pass implemented the
> CF-avoidance behaviour the brief itself names as the most probable win —
> persistent session + cookie reuse (already present) **plus** a homepage warm-up
> GET and correct browser-like Referer / Sec-Fetch-Site. Whether this matches what
> the legacy scraper did is unverified; whether it actually stops the live
> challenges can only be confirmed by the next live full run from HOME-PC.

### Added — HTTP-layer Cloudflare avoidance (primary/attempt-1 path)
- **Once-per-host warm-up GET.** Before the first chapter on a host is requested,
  the persistent session now GETs the site **origin** (homepage) so Cloudflare
  issues a `cf_clearance` cookie into the session — mirroring a human opening the
  site before reading. This matters most on a **resume** run, where the cached TOC
  means the first network hit would otherwise be a chapter URL with no cookies.
  The warm-up is best-effort: a warm-up error is swallowed and the real fetch still
  runs. (`request_manager._http_get` / `_warmed_hosts_for`.)
- **Host-derived Referer + correct Sec-Fetch-Site.** The old code sent a hardcoded
  `Referer: https://www.webnovel.com/` on **every** request — including
  FreeWebNovel ones — a cross-site referer that doesn't match the host being
  fetched (a bot-tell). Now the warm-up looks like an address-bar navigation
  (`Sec-Fetch-Site: none`, no Referer) and every subsequent same-host request looks
  like an in-site click (`Sec-Fetch-Site: same-origin`, `Referer` = the site
  origin). The static `Referer` was removed from `BROWSER_HEADERS`.
- **Persistent session + cookie reuse were already present** (one
  `requests.Session` per `RequestManager`, reused for the whole run) and are
  unchanged; the warm-up + header chaining build on top of them. The 0.1.1 brotli
  fix (no `br` in `Accept-Encoding`) and the garbled-content self-heal are
  preserved.

### Fixed — Chromium install gap (fresh-install stealth rungs could never launch)
- The Windows launcher ran `python -m camoufox fetch` but **never** downloaded
  Chromium, so the `playwright_stealth` / `playwright_stealth_fresh` rungs added in
  0.1.1 could not launch on any machine that only ran setup ("Executable doesn't
  exist … playwright install"). The macOS launcher had the mirror-image gap —
  it installed Chromium but never fetched camoufox.
- **Both launchers now install BOTH engines.** `Setup_and_Run-Web-Novel-Scraper.bat`
  gained a Chromium step (`python -m playwright install chromium` — Chromium only,
  never full Chrome) contained in the repo at `files\bin\ms-playwright` via
  `PLAYWRIGHT_BROWSERS_PATH` (portable, no admin), gated by a `.venv\playwright.fetched`
  sentinel. `Setup_and_Run-Web-Novel-Scraper.command` gained the camoufox fetch it
  was missing (gated by `.venv/camoufox.fetched`).
- **Runtime respects the same path.** New `webnovel_scraper/browser_env.py` defaults
  `PLAYWRIGHT_BROWSERS_PATH` to the contained `files/bin/ms-playwright` at import
  time (via `os.environ.setdefault`, so the launcher's explicit value always wins),
  imported by both `request_manager` and `cf_bypass`. So Chromium is found where
  setup put it even when the program is started outside the launcher (a dev running
  `app.py`, the test suite).

### Fixed — browser-launch failure no longer freezes the run (non-blocking)
- A browser executable-missing / launch error is now classified as an **immediate,
  non-retryable strategy failure** that advances to the next ladder rung **without**
  the long exponential backoff sleep. Previously such an error fell into the generic
  retry branch and slept 5 → 15 → 45 → 120 s before re-attempting a launch that
  could never succeed — the live "retrying in 102.7s with playwright_stealth_fresh"
  freeze. New `request_manager._looks_like_browser_launch_failure` matches Playwright
  "Executable doesn't exist" / "playwright install" messages, missing-engine markers,
  and any `ImportError` / `FileNotFoundError`. A clear one-line log points the user to
  re-run setup. A chapter that exhausts the whole ladder is recorded in
  `RunReport.failed` and the run continues (confirmed by test).

### Ladder (Task 4) — unchanged shape, now reliable + non-blocking
- The escalation ladder stays
  `http → cloudscraper → camoufox → camoufox_fresh → playwright_stealth →
  playwright_stealth_fresh`. **Reasoning:** with Task 1 reducing how often CF
  triggers at all, `http`/`cloudscraper` should clear most chapters cheaply;
  camoufox remains the primary browser rung; the Chromium playwright-stealth rungs
  are kept as the **last-resort** rescue (Task 2 makes them actually launchable;
  Task 3 makes them fully non-blocking if an engine is still missing). No strategy
  was removed — the user wants a working fallback chain, not fewer options. Both
  camoufox and Chromium-stealth were live-proven *insufficient* against a real FWN
  challenge in 0.1.1, so the genuine fix is Task 1 (avoid the challenge), with the
  browser rungs as the safety net.

### Tests / docs
- New `files/tests/test_cf_avoidance.py` (10 cases): warm-up-once + Referer/
  Sec-Fetch-Site chaining + cookie carry-over across chapters on a persistent
  session; warm-up-failure-is-swallowed; `BROWSER_HEADERS` browser-like without
  brotli or the static cross-site Referer; persistent-session reuse; `browser_env`
  defaults the contained path and never overrides an explicit one; the launch-failure
  classifier; launch failure skips backoff and advances every rung (no long sleep);
  a real transient block still backs off; and a pipeline run continues past a chapter
  that exhausts on a launch failure. All 0.1.1 tests stay green. Suite: **138 offline
  tests**, `verify` green.
- **Honest risk:** the legacy scraper could not be diffed (gitignored/absent), so the
  HTTP-avoidance is a best-practice hypothesis, not a confirmed port. It can only be
  validated by the user's next live full run from HOME-PC. If FWN still challenges,
  the remaining levers are headful browser mode and a residential proxy.

## [0.1.1] — 2026-06-28

Post-live-pass fixes: two live-discovered defects from a Shadow Slave
FreeWebNovel scrape, plus a requested output-folder feature — and follow-up fixes
to a nesting bug that feature shipped with and to the Cloudflare escalation ladder.

### Changed — Cloudflare ladder: playwright-stealth rescue rungs + stronger end-of-run sweep
- **Live finding.** A full Shadow Slave stress-scrape (chapters 1–3065) produced
  the project's first genuine FreeWebNovel Cloudflare challenge. The result was
  negative: on chapters 102, 174 (and likely others) the whole
  `http → cloudscraper → camoufox → camoufox_fresh (×4)` ladder ran and **every
  camoufox attempt** returned "Cloudflare challenge still present after camoufox
  fetch." Camoufox alone does **not** clear a real FWN challenge; those chapters
  were correctly recorded failed and skipped, leaving gaps. This reverses the
  standing assumption that camoufox is sufficient.
- **Rescue rungs wired in.** The dormant Chromium playwright-stealth strategy
  (strategy-1 in `cf_bypass.py`) is now wired back into the live ladder as the
  **last-resort** rungs after camoufox:
  `http → cloudscraper → camoufox → camoufox_fresh → playwright_stealth →
  playwright_stealth_fresh`. The strategy constants were renamed
  `FETCH_STRATEGY_BROWSER[_FRESH]` → `FETCH_STRATEGY_PLAYWRIGHT_STEALTH[_FRESH]`
  (old names kept as aliases) so the live log reads clearly. `BROWSER_ESCALATION_
  LADDER` (browser-mode) gained the same two rungs. `MAX_RETRIES` stays 6 (7
  attempts) — exactly enough to walk all six rungs once with one extra on the final
  stealth rung; the permanent 403/404 short-circuit is intact.
- **One engine per thread.** Camoufox and our Chromium-stealth path each run their
  own sync-Playwright, which cannot coexist on one thread ("Sync API inside the
  asyncio loop"). The fetch methods now tear the *other* engine fully down before
  starting one (`_teardown_chromium` stops the Chromium driver before camoufox;
  `_reset_camoufox` runs before Chromium stealth), so the ladder can walk from
  camoufox into the stealth rungs within a single chapter's attempt sequence.
- **Stronger end-of-run sweep.** The Phase-9C second-pass sweep over non-permanent,
  non-extraction failures now has teeth: because the ladder it re-walks includes the
  stealth rescue rungs (and a failed chapter is never cached), every CF-skipped
  chapter gets a genuine end-of-session retry through the **full** ladder — the
  Chromium-stealth rescue it never had before — at the auto-slowed delay. Rescued
  chapters move to `RunReport.rescued` and are written (SEPARATE: own PDF post-loop;
  CHUNKED/SINGLE: before the group/file PDF). Anything still failing stays in
  `RunReport.failed` and is listed in the summary with the resume hint.
- `is_cloudflare_challenge` already clears a stealth-cleared page content-aware
  (strong markers flag; ambient beacon clears on structural payload such as the FWN
  `class="txt"` body container), so a genuinely-cleared stealth fetch is recognised
  as success — no detection change needed.
- **Honest status:** offline tests prove the wiring/flow (the ladder reaches the
  stealth rungs, the sweep re-walks the full ladder, a stealth-rung clear is
  rescued and written). Whether Chromium playwright-stealth actually defeats a live
  FWN Cloudflare challenge is **unproven** until the user's next live run — camoufox
  was proven insufficient; strategy-1 is the wired rescue still awaiting live
  confirmation.
- Tests: new `files/tests/test_stealth_rescue.py` (ladder order incl. the two
  stealth rungs; a chapter advancing camoufox_fresh → stealth; one-engine-per-thread
  teardown both directions; end-to-end sweep-reaches-stealth-and-rescues in all
  three output modes; fails-every-rung stays failed + in summary). Updated two
  `test_phase2.py` ladder tests for the longer ladders. Suite: **128 offline
  tests**, `verify` green.

### Fixed — output folder nested a folder per run (doubled-folder bug)
- **Symptom.** A live run logged `Output: …\Downloads\webscraped_shadow-slave-1\
  shadow-slave-1` — the new `{slug}-N` folder was created *inside* the previous
  run's leftover folder instead of at the top of Downloads.
- **Root cause.** `resolve_output_dir` was correct, but the GUI's read-only
  "Output folder" field both displayed *and* held the **parent** directory, and
  `Browse…` wrote the picked folder into that same field, which was then passed
  back as `parent_dir`. The original Task 2 spec called for the read-only field to
  show the **target** path while Browse picks the parent; conflating the two meant
  a previously-created (or browsed) output folder could become the next run's
  parent, nesting one level per run.
- **Fix.** `app.py` now keeps the chosen parent in a dedicated `self._output_parent`
  `Path` (default `~/Downloads`, changed **only** by `Browse…`), and the read-only
  field shows a live preview of the resolved **target** (`<parent>/{name}-N`),
  which is never fed back as a parent. `resolve_output_dir` is called from one
  helper (`_resolve_output_dir`) for both the preview and the actual run, always
  from the stored parent Path — so a resolved output dir can never be reused as a
  parent. Confirmed: the pre-rename `webscraped_{slug}-N` folders do not interfere
  with the new `{slug}-N` collision scan; a fresh default run yields exactly
  `~/Downloads/{slug}-1` at the top level.
- `chapter_index.json` stays in the output dir (it is the documented, output-dir-
  scoped resume source of truth; relocating it would break "re-run into the same
  folder to resume"). Flagged as a cosmetic item, left in place per policy.
- Tests: +3 `resolve_output_dir` cases in `test_phase5_pipeline.py` (default is
  single-level; the `-N` increment is a sibling, never nested; old `webscraped_`
  folders are ignored) and +1 GUI case in `test_phase8_gui.py` (the target preview
  is one level under Downloads and the parent passed is the Downloads base, not the
  target). Suite: **120 offline tests**, `verify` green.

### Fixed — FreeWebNovel body extraction (brotli decode)
- **Root cause.** On the live scrape, chapters 1–2 wrote PDFs but chapters 3+ all
  failed with "Could not extract body paragraphs". The HTTP layer advertised
  `Accept-Encoding: gzip, deflate, br`, but `requests` cannot decode Brotli
  without the optional `brotli` package — so a brotli-encoded chapter page came
  back as U+FFFD-replacement-char garbage (~15 KB, no real HTML), which yielded
  zero paragraphs. Chapters 1–2 happened to be cached clean from an earlier run;
  3+ hit the brotli path fresh this session. The adapter's body selectors were
  **not** the problem — the correctly-decoded current FWN markup extracts a full
  body (50 paragraphs) with the unchanged selectors.
- **Fix.** `request_manager.BROWSER_HEADERS` no longer advertises `br` (gzip and
  deflate are always decodable; the page content is identical). Added a
  `_looks_garbled` guard: a response with >2% U+FFFD replacement characters is an
  undecodable content-encoding and is treated as a **retryable** fetch failure, so
  the escalation ladder advances instead of caching garbage. Cache reads now
  self-heal — a previously-poisoned (garbled) cache entry is ignored and
  re-fetched rather than served.

### Fixed — extraction-failure misclassified as a Cloudflare block
- An empty-extraction outcome (a fully-fetched, non-challenge page with no
  extractable body) was being routed into the block/challenge path:
  `pipeline._fetch_one` called `pacer.register_block()`, ratcheting the adaptive
  auto-slowdown up (5.2 → 7.9 → … → 30 s) on what was never a Cloudflare block.
- Empty extraction is now its own `models.EmptyExtractionError` class, raised by
  both the FreeWebNovel and WebNovel-dynamic adapters. The pipeline records it in
  `RunReport.failed` and the new `RunReport.extraction_failed`, **does not**
  register an auto-slowdown block, and **excludes it from the second-pass sweep**
  (re-fetching the same page yields the same empty body). Genuine Cloudflare
  blocks still take the slowdown/sweep path unchanged.

### Added — user-choosable output folder + name
- The default output folder name is now `{slug}-N` (e.g. `shadow-slave-1`),
  renamed from `webscraped_{slug}-N`.
- `pipeline.resolve_output_dir` gained optional `parent_dir` and `base_name`
  parameters (both default to the prior behaviour). A custom name is sanitised for
  the filesystem and the `-N` no-overwrite auto-increment is applied to any
  custom parent + name, so a custom run never overwrites an existing folder.
- `app.py` added an **Output folder** row: a read-only display of the chosen
  parent folder with a native **Browse…** picker (`tkinter.filedialog.
  askdirectory`) and an optional **Folder name** entry (blank = the novel slug).
  All path resolution stays in `resolve_output_dir`; the GUI remains a thin shell
  and the daemon-thread + Stop/cancel flow is unchanged.

### Tests / docs
- New fixtures `files/test-files/fwn_chapter_current_ok.html` (the real current
  FWN chapter, sanitised) and `fwn_chapter_brotli_garbage.html` (the actual
  poisoned cache artifact from the live run).
- New `files/tests/test_brotli_extraction_fix.py` (7 cases: current markup
  extracts; garble detection; no-brotli header; garbled HTTP escalates and is not
  cached; garbled cache self-heals; empty-extraction classified-not-block and
  not-swept in separate + single modes). `test_phase5_pipeline.py` updated for the
  new default name + custom parent/name; `test_phase8_gui.py` extended for the
  output-folder defaults. Suite: **116 offline tests**, `verify` green.

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
