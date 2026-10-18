# webnovel-scraper — Decisions (ADR Log)

Append-only record of non-obvious design decisions and their reasoning. Newest on top.
See AI-WORKSPACE.md for how this fits alongside Briefing, Changelog, and Handoff.

---

## 015 — One canonical chapter text; cleanup only where measured evidence makes it confident — 2026-10-18 — Claude (Opus 5.5)

**Status:** Accepted (v0.3.0 Checkpoint 05)

**Context:** Each adapter cleaned text its own way. Measured on the owner's pre-0.3.0 cache, the
FreeWebNovel filters (substring `NOISE_SNIPPETS`, a 12-character short-line rule, consecutive
de-duplication) deleted 2,274 real paragraphs from 1,398 pages ("Painful!", "Boom!", "He sighed.",
"***", "’Crap...’", repeated battle cries), and WebNovel's keyword filter deleted 381 real paragraphs
from 890 pages ("He reported...", "her husband's comment"). Headings carried a forced period, were
detected in the PDF by regex (so fractional `Chapter 627.2:` headings were styled as body text), and
characters outside the Times-Roman encoding (CJK, Greek, arrows, stars) printed as wrong glyphs.

**Decision:**
1. `content.py` is the single cleanup: character normalisation (invisible/control removed, Unicode
   spaces to spaces, everything else kept), title cleanup (site suffixes, a repeated chapter number,
   surrounding separators, double-escaped entities), exactly one heading `Chapter N: Title`
   (`Chapter N: Untitled` with a warning), and body cleanup limited to: credit lines among the first
   or last three paragraphs (306 of 306 sampled were in the first three), a repeated heading among the
   first two paragraphs that starts with the chapter number, whole-paragraph reader-interface lines
   (a short exact list; one hit in 709,458 cached paragraphs), and the website's own promotional
   sentences when they are the whole paragraph or its last sentence (author's notes are exempt).
2. Adapters only find structure: FreeWebNovel reads the first body container present (`#article`,
   `#chapter-content`, ...) instead of the largest one; inline markup is joined as read (no spaces
   inserted before punctuation).
3. The PDF is built from chapter structure (heading flowable with `keepWithNext`, body paragraphs),
   in the established style; nothing is guessed from text patterns. Runs of characters the standard
   font cannot encode are drawn from an installed Windows font that has them (Times New Roman, Segoe
   UI, Segoe UI Symbol, Microsoft YaHei, ...), so ordinary text looks exactly as before.
4. TXT is UTF-8 without BOM, `\n` line endings, heading + blank line + paragraphs, chapters separated
   by two blank lines, published atomically; TXT and PDF render the same `ChapterContent` list, so
   their words are identical (tested by extracting the PDF text).

**Alternatives considered:**
- *Keep the filters and add exceptions.* Rejected: every exception was another guess about prose.
- *Embed a bundled Unicode font for all text.* Rejected: a style and licensing change outside scope.
- *Drop a lone digit paragraph (Reverend Insanity's inline comment counts).* Rejected: a countdown is
  legitimate prose; it is a documented limitation.

**Consequences:** Output text is longer where the old filters deleted prose, and headings lose the
forced period. Translator notes inside chapters are kept. A page with none of the known body
containers fails extraction visibly instead of scraping the whole page.

---

## 014 — A run is a folder with a fixed selection and a checksummed journal; Resume trusts only that — 2026-10-18 — Claude (Opus 5.5)

**Status:** Accepted (v0.3.0 Checkpoints 03.3-03.4; implements plan §9.5/§9.7)

**Context:** Before 03.3 a "resume" was any re-run into a folder: a PDF whose name looked right was
skipped, a custom Folder name could point anywhere, and the chapter list was re-derived each time, so
a resumed run could silently change its chapters, formats or source. Grouped files were keyed by
displayed number, so two pages sharing a number collided.

**Decision:**
1. **Folder.** A new run reserves `<output root>/<novel-slug>-N` (03.0 allocator, exclusive `mkdir`)
   only after its chapter list is settled; a refused run leaves no folder. The Folder name field is
   retired. The root defaults to `~/Downloads/Web-Novel-Scraper-Outputs`.
2. **Fixed selection.** `run-selection.json` holds the run's pages (URL, label, slug, ids, title) in
   order; its SHA-256 is in the journal header. A resume uses exactly these pages and makes no
   table-of-contents request, however the site's list has changed since; if the current saved list
   now gives one of those pages another number, the resume is refused.
3. **Journal.** `run-journal.jsonl`: header + `publish`/`failure`/`resume`/`end` records, CRC-32 per
   line, consecutive `seq`, `fsync` per record. A torn last line is ignored and trimmed; any other
   damage refuses the resume. No chapter text is ever written to it.
4. **Publication.** Render to a temp file beside the target, `fsync`, `os.replace`, hash, then
   record. A group/format counts as done only if its latest record lacks no page and the file's size
   and SHA-256 match; filename presence alone never counts. An unrecorded file is rendered again.
5. **Resume is journal-authoritative.** Formats, grouping, chunk size and range come from the
   header; a job that disagrees, another novel, or another source fingerprint is refused, naming the
   difference. The GUI builds the resume job *from* the journal and only asks which run.
6. **Groups.** Outputs live in `PDF/` (and `TXT/`, Checkpoint 05); one grouping for every format;
   grouped files are named `<Novel>_Chapters_<first>-<last>` from the first and last *selected*
   labels (the whole-range file no longer says "All_Chapters"); content is keyed by page, so equal
   displayed numbers never collide; a name another group owns gets the group key appended. Grouped
   files are published at the end; on Stop only groups with every page obtained are published; a
   group published with pages missing records them and is completed by a later resume.
7. **One worker per folder.** `run.lock` (the 03.2a `FileLock`) is held for the whole run.
8. **Audit.** Each session rewrites `run-audit.json`: per selected page its label, URL, slug, title,
   status (saved / resumed / failed / not saved), reason, and the file and SHA-256 holding it.

**Alternatives considered:**
- *Store only the selection hash and re-derive pages on resume.* Rejected: a refreshed or grown list
  would silently change the run.
- *Publish complete chunks during the run.* More crash-resilient but changes the established
  "Building output" phase; the HTML cache already makes re-fetching cheap. Revisit if long grouped
  runs show a need.
- *Keep outputs at the folder root.* Rejected: TXT and PDF side by side plus four metadata files is
  confusing; format folders keep the run folder readable.

**Consequences:** Pre-0.3.0 folders are never resumable and never touched (owner decision). Tests that
re-ran into one folder to "resume" now reserve a run and resume it explicitly; tests of the filename
scan were replaced by "a stray file is never trusted" regressions.

---

## 013 — v0.3.0 is a Windows-only release candidate; macOS acceptance is deferred — 2026-10-18 — Claude (Opus 5.5)

**Status:** Accepted (owner decision, 2026-10-18; supersedes the two-platform gate in plan §4 07 and §5)

**Context:** The plan required separate Windows and Apple Silicon/macOS manual gates before release.
No macOS machine is part of this execution, and the owner wants a reliable Windows release first.

**Decision:** v0.3.0's release gate is Windows only. The portable code and the macOS launcher
(`Setup_and_Run-Web-Novel-Scraper.command`) are kept as they are and are not deleted, but macOS
acceptance, launcher testing and macOS-specific fixes move to a later owner-directed branch. No
document may claim macOS readiness for v0.3.0. The `fcntl` lock branch and the Aqua styling remain
untested on macOS and are listed as such.

**Consequences:** Windows bootstrap, scaling and browser readiness get full attention in 07. A later
macOS pass starts from the deferred list in `handoff.md`.

---

## 012 — The HTML cache is validated, provenance-carrying and host-scoped; index pages are never cached — 2026-10-18 — Claude (Opus 5.5)

**Status:** Accepted (v0.3.0 Checkpoint 03.2b; implements plan §9.6 and owner override B)

**Context:** Until 03.2b the cache was `files/cache/<novel>/<hash>.html`: raw HTML, no provenance,
written non-atomically, checked on read only for garbled bytes and challenges. The table-of-contents
page was cached like any chapter, so an open-ended re-run could be served an old index and never see
newly published chapters. `.net` and `.com` would have shared a folder.

**Decision:**
1. **Layout and provenance.** `files/cache/<host>/<novel>/<key>.html` plus `<key>.json` (schema 1:
   URL, final URL, host, novel, fetch time, byte length, SHA-256, classification, strategy, origin,
   app version). `key` is the old key (`sha256(url)[:24]`). A manager scoped to a source caches only
   that source's host.
2. **A hit is proven, not assumed.** The sidecar must match the page, host and novel; length and hash
   must match; the bytes must be UTF-8, not garbled, not a challenge and end their document. Any
   failure is a logged miss; the next fetch replaces the pair, so a bad entry costs one request.
3. **Atomic pairs.** HTML then sidecar, each temp + `fsync` + `os.replace`; an interrupted pair fails
   the hash check.
4. **Only usable pages stay.** The request manager never caches a challenge, an undecodable,
   truncated or empty page, or a page the site answered from another address. An adapter that cannot
   extract the chapter (no body, or the page shows another chapter number than the manifest expects:
   the new offset guard) sets the entry aside as `<key>.invalid.html`; if it came from the cache it
   fetches once more, and a second failure fails the chapter (no loop). Rescued pages are cached
   only after they extract as the right chapter.
5. **Index pages are always fresh.** FreeWebNovel and WebNovel fetch their index with
   `use_cache=False`, so the tail re-check and any extension always see the site as it is now.
   Chapter pages never expire on their own.
6. **Legacy copy.** A miss looks for the pre-0.3.0 `<novel>/<key>.html`; if it passes the same checks
   it is *copied* in with `origin: "legacy-copy"`. The old file is never moved, changed or deleted.
7. **Explicit refresh.** `ScrapeJob.refresh_html_cache` (a one-shot box in Advanced Settings) skips
   cached reads for one run and saves the fresh pages over the old ones.

**Alternatives considered:**
- *Commit pages only after extraction (deferred write).* Cleaner in theory, but it threads a commit
  call through every adapter and the rescue path; write-then-invalidate gives the same guarantee.
- *Expire chapter pages by age.* Rejected: a published chapter does not change, and age-based
  refetching costs requests for nothing. Explicit refresh covers revisions.
- *Cache index pages with a short time-to-live.* Rejected: the index is one request per discovery,
  and any staleness can hide new chapters.
- *Move the 1.2 GB legacy cache into the new layout in one pass.* Rejected: destructive and slow;
  on-demand copying keeps the owner's data untouched.

**Consequences:** The first run after upgrading copies legacy pages as it reads them (both copies
stay on disk until the owner deletes the old folders). A chapter whose page now shows another number
fails visibly instead of being published under the wrong number; refreshing the chapter list fixes a
genuinely moved novel.

---

## 011 — An oversized End is clamped to the last chapter a fresh, complete discovery lists — 2026-10-18 — Claude (Opus 5.5)

**Status:** Accepted (owner decision, 2026-10-18). **Supersedes ADR 010 decision 3 ("Refuse, do not
clamp") for oversized ranges only;** every other part of ADR 010 stands.

**Context:** 03.2a refused `1-5000` on a 3,700-chapter list. The owner rejected that: the habit of
typing a large End must keep working, and an honest clamp is what the user means.

**Decision:**
1. **End clamp.** When a discovery made *this run* returned normally and vouches for its tail, a
   requested span lying entirely above every chapter known at all is "not published yet": the End
   is clamped to the last listed chapter. `requested_range` stays as asked; `effective_range` is
   what is scraped; a `NOTE: range adjusted:` line goes to Activity Summary, `RunReport.range_notes`
   and the summary block. Nothing above the list is recorded as absent or covered, so a later run
   asks the site again and a finished novel can be extended.
2. **What never clamps.** A failed, cancelled or blocked discovery (it raises); no discovery this
   run; a discovery that returned nothing for the window; an adapter whose list may be short
   (`ChapterIndex(tail_confirmed=False)`, e.g. WebNovel listing fewer chapters than its own
   `chapterCnt`); a hole below a newer saved chapter (the site's answer was short). These still
   refuse with the reason named.
3. **Start.** A Start past every listed chapter refuses as "None of chapters X-Y are available"
   (nothing fetched). A Start at most `MAX_TOC_GAP_RUN` (200) below the first listed chapter is
   raised to it with a note; a larger leading hole still refuses.
4. **Sparse TOC gaps are unchanged:** reported, skipped, never recorded absent. A run with gaps
   inside its range now ends as "Finished with gaps" (completed with problems), never as a clean
   "Completed", so a partial book is never presented as complete.

**Alternatives considered:**
- *Keep refusing (ADR 010).* Rejected by the owner.
- *Clamp from the saved manifest without asking the site.* Rejected: a saved last chapter may be
  stale; only fresh evidence may say where the novel ends.
- *Record the numbers past the end as absent.* Rejected: it invents evidence and would block the
  next extension.

**Consequences:** `1-5000` on a 3,700-chapter novel scrapes 1-3700 and says so. Both pipeline paths
behave identically (shared preflight). Regression tests cover both paths, the unconfirmed-tail case,
growth on a later run and duplicate/fractional pages under a clamp.

---

## 010 — The chapter manifest, not the output folder, decides the chapter list; coverage is claimed only for what was examined — 2026-10-18 — Claude (Sonnet 5.5)

**Status:** Accepted (v0.3.0 Checkpoint 03.2a)

**Context:** Until 03.1 the pipeline trusted an output-folder `chapter_index.json` (including a
v2 dict with no identity block), clamped a finite range to whatever the list held and reported
success, and wrote the index non-atomically per run. 03.1 built the validated, source-specific
store but left the pipeline alone. Wiring it in has four hard questions: what a discovery may
claim to have covered, what to do with a hole in the list, how two processes share one overlay,
and how a user rebuilds a list they distrust.

**Decision:**
1. **One preflight.** `chapter_preflight.resolve_chapters` is the only place a run's chapters are
   chosen; both pipeline paths call it through `_resolve_chapters` and differ only in the
   `discover` callable they inject. Selection is by source page URL; the exact label text rides
   in `ChapterMeta.extra["manifest_label"]` across the float bridge.
2. **A discovery claims its asked window, no more.** Coverage is the unbroken whole-number runs
   the returned chapters account for, clipped to the `[start_hint, end_hint]` window the adapter
   was asked about; padding the adapter returns beyond it is kept as evidence but claims nothing.
   An interrupted discovery's checkpoints stop at the last resolved slug. An open end always
   re-checks a bounded tail (`TAIL_RECHECK_MARGIN = 64`) instead of trusting a stale last chapter.
3. **Refuse, do not clamp.** A requested number with no entry and no recorded absence refuses the
   run with the missing chapters named. Source-confirmed absence (manifest evidence) is a reasoned
   skip. A hole *inside* a list the site just returned is a **TOC gap**: reported as before and
   skipped, never recorded as absent and never counted as covered. A gap longer than 200, or one
   the discovery did not bracket, is refused as unconfirmed.
4. **Single writer.** A per-source advisory file lock (`file_lock.py`, stdlib) is held across load,
   reconcile and publish; a second writer is refused visibly, and a reader without the lock never
   moves a file. The OS releases it if the holder dies.
5. **Refresh is explicit and non-destructive.** `ScrapeJob.refresh_chapter_list` backs the overlay
   up as `.bak-<utc>[-N]` and rediscovers; the baseline is never touched; a refresh that fails or is
   cancelled before saving anything puts the backup back. The GUI exposes a one-shot box.
6. **The output folder's index is legacy input.** The pipeline no longer writes it and reads it only
   through `import_legacy_index`; it is never modified.

**Alternatives considered:**
- *Treat every uncovered label inside the returned span as unresolved and refuse.* Strictly safest,
  but it would make Supreme Magus unrunnable for a range that crosses any sparse-TOC gap, which the
  577b5db acceptance run exercised; gaps stay reported, not hidden.
- *Record the sparse-TOC gap as `absent` so it is not rediscovered.* Rejected: it invents evidence
  of absence the source never gave.
- *Add an "examined but unlisted" field to the schema.* Would stop the repeated re-ask, but it
  changes the approved 03.1 schema; left as a follow-up.
- *Hold the lock only while writing.* Leaves the read-modify-write window open between load and
  publish, which is the lost-update the lock exists to close.
- *`pid` lock files.* Stale after a crash and unsafe to probe on Windows; OS locks need no cleanup.

**Consequences:** A finite end beyond the site's list is now refused (previously clamped), so the
live `1-5000` Supreme Magus habit needs a blank End. Existing output folders' indexes are imported
or ignored with a logged reason, never trusted blindly. Runs write a runtime overlay under
`files/runtime-data/chapter-lists/`. A range spanning a persistent sparse-TOC gap re-asks the
adapter on each run. Offline fixtures and a mutation check cover the behaviour; nothing was run
against a live site.

---

## 009 — Source identity is host-scoped, chapters are identified by page, labels are text — 2026-10-18 — Claude (Sonnet 5.5)

**Status:** Accepted (v0.3.0 Checkpoint 03.0)

**Context:** Checkpoint 03 adds manifests, a validated HTML cache, a run journal and
Resume, and Checkpoint 04 adds `freewebnovel.net` beside `.com` under the same adapter.
Keying any of these by `adapter_key`, by displayed chapter number, or by a float number would
let one source's data pass for another's. Supreme Magus already has two pages that both
display chapter 3664, and `627.1` and `627.10` cannot be told apart as floats.

**Decision:**
1. A source is `(canonical novel slug, adapter key, https origin, index URL, identifiers)`,
   validated against a table of supported hosts and the adapters expected for each. The host
   is the website identity (consistent with ADR 008); `site_key` is the host.
2. A chapter is identified by its normalised source page URL on that host. The displayed
   number, `slug`, `source_id` and title are evidence and never part of equality.
3. A displayed number is canonical decimal text (`ChapterLabel`), converted to `int | float`
   only at the existing `ChapterMeta.index` boundary.
4. New runs reserve `<root>/<slug>-N` with an exclusive `mkdir`, and a `run-reservation.json`
   marker. Any existing entry, including an empty abandoned folder, is skipped and never
   reused. Resume may open only a folder whose name and marker agree (`open_existing_run`).
   A preview reserves nothing.
5. The contracts for the manifest, legacy migration, journal and cache are in plan §9. The
   legacy output-scoped index is only read, and imported only when its identity matches.

**Alternatives considered:**
- Keep `_index_identity` (slug, adapter, URLs) and extend it: no host allow-list, no chapter
  identity, and it keeps trusting an index with no identity block.
- Key chapters by `(displayed number, slug)`: breaks when a site re-slugs, and cannot
  represent a page whose number the source changes.
- Reuse an empty abandoned run folder: simpler, but it cannot be told apart from a folder
  another process is about to fill.

**Consequences:** Adding a website means adding its host and adapter to `SUPPORTED_HOSTS`
(Checkpoint 04), deliberately. The identity layer is not yet used by the pipeline, so nothing
changes for users until 03.1 to 03.3 integrate it. The GUI's Folder name field is retired when
03.3 adopts `<slug>-N`.

**Addendum (2026-10-18, after the 03.0 review, recorded in 03.1):**
- *Owner decisions.* (1) The Folder name field is retired in 03.3. (2) Pre-v0.3.0 output folders
  are not automatically resumable; they are never moved, overwritten or deleted.
- *Clarification 1.* Tracked baseline manifests under `scripts/` are never renamed, quarantined
  or rewritten at run time. Quarantine (`<name>.corrupt-<utc>[-N]`) applies only to mutable
  runtime overlays; an invalid baseline is rejected with a diagnostic and left untouched.
- *Clarification 2.* 03.3 keeps the approved output-root contract exactly
  (`default_output_root()`: `~/Downloads/Web-Novel-Scraper-Outputs`, the home folder only when
  there is no Downloads folder) and never substitutes the home directory silently.
- Detail and the 03.1 API boundaries: plan §9.10.

---

## 008 — Website-first selection: a website is a host, not an adapter — 2026-10-18 — Claude (Sonnet 5.5)

**Status:** Accepted (v0.3.0 Checkpoint 02.3)

**Context:** Section 1 now asks for the Website first and then a Novel that website can
run. The catalog is a flat list of rows (a novel on a site) keyed by `(novel_slug,
adapter_key)`. Later checkpoints must be able to offer separate `.net` and `.com` entries
for the same novel, and those need not use different adapters. The checkpoint forbids new
catalog entries and any change to the scraping engine.
- `adapter_key` names an implementation, not a place. Keying the Website menu by it would
  merge two websites that share an adapter, or split one website that has two.
- A saved preference must survive catalog edits without ever selecting something that
  cannot run.

**Decision:**
1. A website is identified by the **host** of a row's `base_url` (else `url`), lower-case,
   without a leading `www.` (`ui/selection.py`, pure, no Tk). Rows with the same host are
   one website whatever their adapters; rows with different hosts are different websites
   even if they share an adapter. Two websites that share a display name are labelled with
   their host.
2. The Website menu lists every website in catalog order. A website with no enabled row is
   shown "(coming soon)" and cannot be chosen. The Novel list holds only the novels the
   chosen website can run (enabled rows), so a disabled combination can never reach Start.
3. `selection.settle(website, novel)` returns the nearest runnable pair: an unknown or
   disabled website gives way to the first runnable one; the novel is kept if that website
   offers it, else its first novel. It serves a website change, a saved preference and a
   catalog edit alike.
4. Start still passes the resolved catalog row's `novel_slug` and `adapter_key` in the
   `ScrapeJob`. The pipeline and the catalog are unchanged.
5. Preferences store the website host and novel slug as plain identifiers (settings schema
   2). Until the user picks a website or novel in a session, the saved choice is left as it
   is, so opening and closing the window rewrites nothing and a website that is temporarily
   unavailable is remembered.
6. On Windows in Dark mode the "coming soon" menu entries are drawn dim and inert rather
   than as `state=disabled`, because Windows embosses disabled menu entries and they then
   read brighter than the real choices. Everywhere else they are ordinary disabled entries.

**Alternatives considered:**
- (a) Key websites by `adapter_key`. Rejected: it merges `.net` and `.com` entries that
  share an adapter.
- (b) Add a `site_id` field to `SiteSpec`. Rejected for now: it changes the model and
  catalog for no present benefit; the host already identifies the website. Revisit in
  Checkpoint 04 if hosts prove ambiguous.
- (c) Show disabled novels in the Novel list. Rejected: a Combobox cannot grey single
  entries, and an unrunnable choice is better absent.
- (d) A custom-drawn dropdown for the Website list. Rejected as a UI redesign.

**Consequences:**
- Adding a `.net` or `.com` row later needs only a catalog row with its own host; the
  Website menu, Novel list and saved preference follow with no UI change.
- Two catalog rows with the same host and novel would be one choice (the first enabled
  row wins). The catalog does not have such a pair.
- Stored identifiers are validated for shape only; the window checks them against the
  catalog at start-up.

---

## 007 — Run progress: a tracker over existing callbacks plus one observability-only pipeline observer — 2026-10-18 — Claude (Sonnet 5.5)

**Status:** Accepted (v0.3.0 Checkpoint 02.2)

**Context:** the Run section needs the real phase, honest counts and an ETA.
- The pipeline's only signals are `log(str)` and `progress_cb(done, total)`.
- `progress_cb` fires after the chapters are known, once per chapter at its end
  state (saved, failed or already present). It cannot tell the phase, how many
  chapters resume skipped, or whether a chapter failed for good or is about to be
  rescued.
- The checkpoint forbids wider pipeline refactoring and forbids guessing from
  message text.

**Decision:**
1. Add one optional `phase_cb(phase, total, already_present)` to `run_scrape`,
   carried by the existing `_Progress` tracker. It is called with `"scraping"`
   when the selected chapters are known and when resume skips more, and with
   `"building"` before CHUNKED/SINGLE output is assembled. A raising observer is
   swallowed. Nothing else in the pipeline changes.
2. `ui/run_progress.py` (pure) holds all the logic: `RunTracker`, `EtaEstimator`,
   `FinalCounts`. Counts that cannot be known live (failed, recovered, PDFs) come
   only from the final `RunReport`.
3. The ETA uses the worker's monotonic timestamp on each event, the share of
   `done` that was actually fetched, and the slower of the whole-run and
   recent-chapter pace. It is read when the window redraws; there is no timer.
4. Phase events are never coalesced, except the per-skip updates, which coalesce
   like progress.

**Alternatives considered:**
- (a) Infer the phase from log text. Rejected: guessed patterns drift.
- (b) Infer "already present" from jumps in `done`. Rejected: a burst of skipped
  chapters would make the ETA wildly optimistic.
- (c) A current-chapter callback. Deferred: it needs hooks in several fetch paths
  and the current chapter's identity is not unique (duplicate displayed numbers).
- (d) A timer thread or `after` polling for the ETA. Rejected: the checkpoint asks
  for no polling.
- (e) Structured pipeline events. Deferred to a later checkpoint.

**Consequences:**
- `run_scrape` gained an optional keyword, so existing callers and test fakes
  that use `**kwargs` keep working. Fakes with a fixed signature need
  `phase_cb=None`.
- `RunReport` lists failures by displayed number, so two failed pages with one
  number count once in the failed total (processed counts are exact).
- The legacy single/chunked path reports "building" only just before its PDFs.
- A later checkpoint can add a current-chapter hook beside this one.

---

## 006 — Activity: log at emit, Tk only on the UI thread, and a literal-marker bridge for pipeline text — 2026-10-18 — Claude (Opus 5.5)

**Status:** Accepted (v0.3.0 Checkpoint 02.1)

**Context:** v0.3.0 replaces the single GUI log with PDF-Splitter's Summary/Detailed
Activity (plan §3, §8 C5).
- Until now the worker marshalled every line, progress update and the run's end
  through `self.after(0, …)`: a Tk call from a non-Tk thread.
- The pipeline speaks only through `log(str)` and `progress_cb(done, total)`. The
  checkpoint forbids pipeline changes and a large regex classifier, yet the
  warnings must reach Summary.
- PDF-Splitter's `activity.py` writes its session log as the UI thread *draws*
  events. Its bounded channel may drop routine detail from a flooded queue, so
  such a line never reaches the log either.

**Decision:**
1. The worker only calls `Activity.emit` (a locked, bounded queue). A pump owned
   by the Tk thread, scheduled with the window's real `after` and always on while
   the window lives, drains it in bounded slices. `destroy()` stops the pump and
   closes the Activity, so late events are refused.
2. The session log is written when an event is emitted (`SessionLog` is locked;
   any thread may write), not when it is drawn. Display drops then cost only
   display lines, which are counted and announced, and the log keeps true
   emission order across threads.
3. A UI-thread `report` first routes everything still queued, and every line
   queued before a job event (progress, run finished) is drawn before that event
   is handled.
4. Pipeline text is classified by `run_activity.PIPELINE_MESSAGE_RULES`: 19
   literal prefix/substring markers, each copied from an existing message. A test
   asserts that every marker still occurs in the package source. Unmatched lines
   go to Detailed only.
5. The run outcome comes from the returned `RunReport`, never from the progress
   count.

**Alternatives considered:**
- (a) Keep `after(0, …)` from the worker. Rejected: it is a Tk call off the Tk
  thread, the very thing the plan forbids.
- (b) PDF-Splitter's draw-time logging. Rejected: a dropped or still-queued line
  would be missing from the diagnostic log.
- (c) A regex classifier for arbitrary log text. Rejected by the checkpoint, and
  it would drift silently.
- (d) Make the pipeline emit structured events. Rejected: no pipeline changes in
  this checkpoint. The marker table is the documented bridge until a later
  checkpoint does that.
- (e) Start and stop the pump per run, as PDF-Splitter does without an adapter.
  Rejected: a pipeline helper thread may still log after the worker returns.

**Consequences:**
- The worker-side code reads only the queue, the cancel flag and its two
  enqueue-only callbacks (an AST test pins this).
- Writing to disk on the producer thread costs one line-buffered write per
  message, negligible at scraper pace.
- A reworded pipeline message fails `test_every_marker_still_occurs_in_the_pipeline_source`
  instead of silently disappearing from Summary.
- Stop/Close report before signalling cancel, so the request always precedes what
  it causes.
- When 02.2 adds phase-aware progress and ETA, it builds on the same
  `ProgressUpdate`/`RunFinished` job events.

---

## 005 — Bounded-window TOC hints threaded via signature introspection, not the adapter contract — 2026-07-20 — Claude (Fable 5)

**Status:** Accepted
**Context:** Phase 2 built `FreeWebNovelAdapter.build_chapter_index`'s optional
`start_hint`/`end_hint` (binary search + padded walk) but deliberately left both
`pipeline.py` call sites unchanged, flagging it as a deviation: without hints, an
offset novel's index build walks the *entire* discovered slug range from 1,
which for a ~4400-chapter novel like Supreme Magus means ~4400 extra fetches
once per output folder. Wiring `job.start`/`job.end` through was the obvious
next step, but `build_chapter_index(self, spec)` is the `BaseAdapter` ABC
contract, and roughly a dozen test fakes across the suite (plus
`WebNovelDynamicAdapter` and the disabled stubs) implement exactly that plain
signature — no `**kwargs`, no `start_hint`/`end_hint`.
**Decision:** Added a small pipeline-level helper, `_build_chapter_index(adapter,
spec, *, fast_path=None, start_hint=None, end_hint=None)`, that uses
`inspect.signature(adapter.build_chapter_index).parameters` to decide which
kwargs the adapter's own method actually declares, and only passes those. Both
`pipeline.py` call sites (the rescue-conductor's `_PrimaryEngine.build_toc` via
`_bootstrap_toc`, and the legacy `_drive` path) now call through this helper
with `start_hint=job.start, end_hint=_end_hint_for(job)`. A large/unbounded
`job.end` (the GUI's blank-end-means-all sentinel, `app.ALL_CHAPTERS = 10**9`)
is folded to `None` by `_end_hint_for` (threshold `10**6`, chosen to sit
comfortably below the real sentinel and far above any real chapter count)
rather than being passed through literally — passing `10**9` as a search target
would still resolve correctly (the binary search degrades gracefully to
"return the last slug"), but only after wasting probe fetches converging on an
unreachable target.
**Alternatives considered:** (a) Add `start_hint`/`end_hint` to the
`BaseAdapter` ABC and every adapter/fake — rejected: touches non-FWN code the
fix drop explicitly scoped out, and is pure churn for adapters that will never
use the hints (WND's TOC already agrees with display numbers; the stubs are
unimplemented). (b) `isinstance(adapter, FreeWebNovelAdapter)` check in
`pipeline.py` — rejected: forces `pipeline.py` (which currently has zero
imports from `adapters/`, operating entirely through injected
factories/fakes) to import a concrete adapter class, a real layering
regression. (c) Broad `try/except TypeError` around the call — rejected: would
silently swallow a genuine `TypeError` raised *inside* a real
`build_chapter_index` call (e.g. a bad URL format bug), masking real bugs as
"unsupported kwarg."
**Consequences:** Every existing adapter and test fake is byte-for-byte
unaffected (proven: the full 285-test Phase 1/2 suite passed unchanged after
this wiring, plus 2 new tests). Only `FreeWebNovelAdapter` — detected by its own
declared parameters, not by name or type — receives the hints. A live run on an
offset novel now resolves near the requested range instead of scanning from
slug 1; proven by `test_pipeline_run_near_high_chapter_stays_bounded_on_offset_novel`
(a 500-slug synthetic novel, chapter 116 requested, fetch count asserted < 60
instead of the ~120+ an unbounded from-slug-1 walk would need). If a future
adapter also wants bounded hints, it just declares the same two parameter
names — no registry/ABC change required, by construction.

---

## 004 — Bounded-window walk defaults to the whole novel when no hint is given — 2026-07-19 — Claude (Fable 5)

**Status:** Accepted (see #005 for the follow-up that actually supplies hints from the pipeline)
**Context:** `build_chapter_index`'s offset-resolution walk needs to know how
much of the novel to reconcile. The drop's own wording — "~12 probes over a
4400-slug space" — confirms a binary search (logarithmic cost) is meant to
anchor the walk, not a full linear scan; but at Phase 2 time no caller yet
supplied `start_hint`/`end_hint` (that wiring was deliberately deferred to
close cleanly — see #005).
**Decision:** When both hints are `None`, the walk covers the whole discovered
slug range (`1..count`) — correct, but potentially the most expensive path.
When either hint is given, binary search anchors near it and the walk is
padded ±8 slugs (`_WALK_PADDING`) around the resolved boundary, absorbing a
local run of insert pages (Supreme Magus has a run of five between slugs 629
and 635) without missing the true boundary by a couple of slugs.
**Alternatives considered:** Always require a hint (raise if omitted) —
rejected: would break direct/test callers of `build_chapter_index` that
reasonably want the full novel (e.g. building a complete catalog view), and
adds a mandatory parameter to a method whose ABC contract has none.
**Consequences:** A direct call with no hints on a genuinely offset novel is
slow (by design, but bounded and one-time per output folder, since
`chapter_index.json` schema v2 persists the resolved result). #005 means real
pipeline runs always supply hints in practice, so this "no hint → full walk"
branch is now mainly exercised by direct/test callers, not live runs.

---

## 003 — Chapter identity/index means the site's DISPLAYED number; slug is a separate field — 2026-07-19 — Claude (Fable 5)

**Status:** Accepted
**Context:** FreeWebNovel's URL slugs (`.../chapter-N`) count every page —
real chapters plus joke-chapter/glossary extras and fractional ".x"
side-chapters — while the page header displays the author's own numbering. On
most novels the two agree; on Supreme Magus they drift apart by a
monotone-non-decreasing, still-growing offset (measured 2 → 15 across the
novel — never constant, ruling out a simple per-novel offset field). Before
this fix, `meta.index` **was** the slug number, which entered filenames,
`chapter_index.json`, the range clamp, and PDF headings — so a user typing a
displayed chapter number (what they read on the site) fetched the wrong page.
**Decision:** `ChapterMeta.index` / `ChapterContent.index` now mean the site's
DISPLAYED chapter number — what a user types and reads — everywhere
downstream (filenames, resume matching, the range clamp, PDF headings,
`RunReport`). The URL slug (what the adapter actually fetches) moved to a new
`ChapterMeta.slug: int | None` field, used only inside the adapter to build
fetch URLs. `index` is `int | float`: fractional side-chapters (e.g. "Chapter
627.2") are kept as their own addressable label — not merged into, or
discarded in favor of, the nearest integer — sorting naturally between their
integer neighbours since Python's numeric ordering already does the right
thing (`627 < 627.2 < 628`).
**Alternatives considered:** (a) Resolve the slug↔displayed mapping once and
bake it into `range()` at index-build time for the whole novel up front —
close to what was chosen, but doesn't handle fractional inserts discovered
only by visiting the page, since they're not knowable from a slug count alone.
(b) A per-novel constant offset field on `SiteSpec`/catalog — rejected outright
by measurement (task 2 of the investigation): the offset is not constant
within Supreme Magus, so a fixed field would silently drift wrong as the
novel accumulates more inserts. (c) Post-fetch reconciliation only (fetch by
slug, relabel from the header after the fact) — rejected as insufficient
alone: it fixes the *label* but not the *address* — a user typing "4207"
would still fetch slug 4207 (the wrong page) and only discover the mislabel
after the fact; the chosen design fetches the *right* page in the first place.
**Consequences:** Every identity novel (slug == displayed for the whole book —
confirmed for Shadow Slave, Reverend Insanity, Renegade Immortal) is
byte-identical to pre-fix output by construction (see #002). Any future
non-FWN adapter that also drifts slug-vs-displayed can reuse the same
`index`/`slug` split without a model change.

---

## 002 — Identity vs. offset is auto-detected for free from the index page, not a dedicated probe — 2026-07-19 — Claude (Fable 5)

**Status:** Accepted
**Context:** The user's decision (#3, "apply to all FWN novels" via
auto-detected probing) requires a hard, testable invariant: an identity novel
(the common case) must cost **zero extra fetches** and produce byte-identical
output to before the fix — not "probably free," provably free, so a
regression can't silently creep back in as fetch count. A naive design (fetch
one extra chapter page up front to check `slug == displayed`) would violate
this: even one wasted fetch on every identity novel's every run is a real,
measurable regression, and several existing offline tests construct a
`FakeRM` registered with *only* the index-page URL (no chapter URLs at all) —
any extra fetch would raise `KeyError` and fail loudly, which is exactly the
tripwire this decision is designed to satisfy rather than dodge.
**Decision:** `build_chapter_index` already fetches the index/TOC page (to
discover the highest slug via `_discover_max_chapter`). That same page's
rendered anchors carry BOTH the URL slug (from `href`) and the site's own
"Chapter N: Title" link text — so a second pass over the same, already-fetched
soup harvests `(slug, displayed)` pairs for free. If every harvested pair
agrees, the novel is identity: return the exact pre-fix generated list, no
further network activity. If any pair disagrees, it's confirmed offset with
no extra fetch either. Only when the index page's anchor text is completely
uninformative (harvests zero pairs — true today only in synthetic tests, not
on any real captured FreeWebNovel TOC page) does the code fall back to one
genuine probe fetch to decide identity vs. offset.
**Alternatives considered:** A dedicated probe fetch on every `build_chapter_index`
call, unconditionally — simpler code, but fails the hard zero-extra-fetch
requirement and the existing `FakeRM`-with-no-chapter-URLs tests outright.
**Consequences:** `test_identity_novel_build_chapter_index_makes_zero_extra_fetches`
proves this directly (asserts `rm.fetched == [index_url]`, nothing else). The
tradeoff is that identity detection depends on the index page's anchors being
informative — true for every FreeWebNovel TOC page captured/observed so far,
but if a future site redesign strips anchor text, the code degrades to the
one-probe fallback rather than silently mis-detecting (never assumes identity
without evidence).

---

## 001 — chapter_index.json schema v2 rebuilds ANY older payload, superseding the 0.2.1 legacy-list-trust allowance — 2026-07-19 — Claude (Fable 5)

**Status:** Accepted (supersedes the 0.2.1 BH-1 "legacy lists remain trusted"
behaviour for the specific case of a pre-v2 index)
**Context:** 0.2.1 added `chapter_index.json` identity (novel/site) so reusing
an output folder for a *different* target rebuilds instead of trusting a
stale TOC — but a pre-identity bare-list file, or a v1 dict with a *matching*
identity, was still trusted and resumed as-is. This fix changes what
`ChapterMeta.index` *means* (site-displayed number, not URL slug — see #003)
and adds the `slug` column. An old index file's rows are slug-space with no
slug column at all — trusting one on an offset novel would silently resume
against the wrong chapter numbers, exactly the bug this fix removes.
**Decision:** Bumped `INDEX_SCHEMA_VERSION` 1 → 2. `_load_index` now rebuilds
(with a logged `NOTE:`, one TOC re-fetch) both the pre-identity bare list AND
any v1-tagged dict, even when its identity matches the current run — schema
staleness is checked independently of identity staleness.
**Alternatives considered:** Leave old indexes trusted and let `fetch_chapter`
silently re-derive correct numbers on the next run — rejected: the whole
point of the persisted index is to avoid re-deriving it, and a resumed run
would keep writing wrongly-addressed content in the meantime.
**Consequences:** Any existing output folder from before this fix pays exactly
one TOC re-fetch on its next resume (logged, not silent), then resumes
correctly from then on. `test_pre_identity_list_only_index_now_rebuilds` and
`test_v1_schema_dict_index_rebuilds_even_with_matching_identity` pin this.
