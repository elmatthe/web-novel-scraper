# webnovel-scraper - Handoff

## Current Focus (newest)
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
