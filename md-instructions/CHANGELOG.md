# Changelog — webnovel-scraper

All notable changes to this project are recorded here. Versions follow semantic
versioning.

## [0.3.0] — Unreleased (Windows release candidate on `feature/v0.3.0-modernization`; awaiting owner acceptance)

**Release scope (owner decision 2026-10-18, ADR 013):** v0.3.0 is a **Windows-only** release
candidate. The macOS launcher and portable code are kept, but macOS acceptance is deferred to a
later branch; nothing here claims macOS readiness.

### Fixed — corrective checkpoint 1 after the independent review of `838ca04` (2026-10-18)
- **C1 (critical): a two-number page is only ever the chapter its second number names.** A page headed
  `Chapter 1130 - 1131 Individual Preparations` (story: `Chapter 1131 …`) was refused as chapter 1132
  but accepted as chapter **1130**: its identity was settled only when the second number happened to
  equal the expected chapter, otherwise the first number was used. The page's identity is now taken
  from the confirmed pair first, then compared with the expected chapter, so it is refused under its
  first number (whole or fractional) as under any other. Confirmed on the real cached chapter-1131 page:
  expected 1130 refused, 1131 accepted as `Chapter 1130-1131: Individual Preparations`, 1132 refused.
  The saved-page cache sets such a page aside, and a run publishes nothing for the refused chapter.
- **The audit tool lists files the run did not publish** (`files/audit_run.py`): anything in `PDF/`,
  `TXT/` or the run folder that the journal does not record (hand-copied files, leftover `.part`/`.tmp`,
  a duplicate under another name), with size, SHA-256, the chapter its name says it is and the file the
  journal records for that chapter. Such a folder no longer passes. It reads and hashes only.
- **I1 (Lord of Mysteries 2 original run): analysed and rehearsed, not executed.** See the acceptance
  document ("Recovering the original Lord of Mysteries 2 (.com) run"). Nothing in the owner's folder
  was changed.
- **Tests:** 1505 total.

### Fixed — composite chapter headings: Lord of Mysteries 2 (.com) chapters 1130–1138 (2026-10-18)
- **Chapters 1130–1138 of Lord of Mysteries 2 on Free Web Novel (.com) were reported as failed.** For
  those pages the website's heading shows two numbers, e.g. `Chapter 1129.1 - 1130 Initiative` at
  `chapter-1130` and `Chapter 1130 - 1131 Individual Preparations` at `chapter-1131`, while the story
  itself begins `Chapter 1130 Initiative` / `Chapter 1131 Individual Preparations`. The reader took the
  first number as the chapter, so the page looked like a different chapter and was rightly refused by
  the wrong-chapter check.
- A heading with two numbers is now read as a pair only when everything agrees: the second is a whole
  number above the first by at most one, it equals the chapter the list expects at that address, and the
  story's own opening line says `Chapter <second> <same title>`. The chapter keeps its real number
  (1130, 1131, … for selection, the saved list, Resume and the audit); the file shows the pair the site
  shows: `Chapter 1129.1-1130: Initiative`, `Chapter 1130-1131: Individual Preparations` (the story's
  repeated opening line is removed as for any chapter). A page showing another chapter, a heading whose
  pair is not confirmed by the story, or any other pair of numbers is still refused.
- `files/audit_run.py` understands these headings (a pair counts as its final number).
- **Existing incomplete runs:** nothing to repair. The refused pages were never saved, and finished
  files, cached pages and saved lists are untouched. Choose the same run folder with **Resume Existing
  Run** (with the same ranges and file types): in separate-file mode only the missing chapters are
  fetched, in grouped mode the unfinished group is rebuilt (a fuller file is never replaced).
- **Tests:** 1495 total.

### Owner acceptance results to date (2026-10-18)
- Owner-confirmed in the GUI: nine small ranges (29 PDFs, no chapter failures); .net Lord of the
  Mysteries 1301–1305 saves all five chapters (10/10 files audited); Reverend Insanity 1–13 (14/14
  audited); Lord of the Mysteries (.com) 1430–99999 lowered to 1430–1432 (6/6 audited) and Start 1433
  refused with nothing written; Reverend Insanity 14–43 stopped and resumed in the same folder
  (30 chapters, six grouped files, audit passed).
- **Whole novels (owner-reported 2026-10-18):** Shadow Slave (.com), The Noble Queen (WebNovel) and
  Lord of the Mysteries (.net) passed; Lord of Mysteries 2 (.com) failed on chapters 1130–1138 (fixed
  above; its whole-novel re-run is not yet reported).
- **Not yet confirmed:** the clean rebuild and window checks, cache refresh, new-chapter discovery,
  whole-novel runs of the other pairings and the re-run of Lord of Mysteries 2 (.com). v0.3.0 stays
  Unreleased until they are.

### Fixed — owner acceptance, round 1 (2026-10-18)
- **Free Web Novel (.net): Lord of the Mysteries chapter 1303 is no longer missing.** The site does
  list it, but at a misspelt address (`/lord-of-the-mysteries/chaspter-1303-coming-to-life.html`),
  and the chapter-list reader accepted only addresses starting `chapter-`. A link spelt differently
  is now accepted only when it is a single page in the same novel's folder on the same site (HTTPS)
  and the number in its address equals the chapter number its text shows. It is used exactly as the
  site lists it (never corrected or guessed) and noted once in Detailed; the page itself passes the
  usual checks (it shows chapter 1303). A saved chapter list without 1303 corrects itself the next
  time a run reads that part of the list.
- **The progress bar moves smoothly while the chapter list is prepared.** The moving block was drawn
  on whatever scale the last draw left (1 in a new window, the last run's chapter count after a run),
  so it flashed from end to end or jumped a fifth of the bar at a time. Every run now uses one fine
  scale.
- **The time estimate no longer treats chapters from saved pages as speed.** They finish at once, so
  after a burst of them the ETA showed about a fifth of the real time and then rose for ten chapters.
  They are now left out of the rate, like chapters a resume skips.
- **Tests:** 1,476 total.

### Added / Fixed — Windows setup, repair and release readiness (Checkpoint 07)
- **The setup file now checks what is really there and repairs only what is missing** (ADR 017):
  - Uses only the tested Python 3.13 or 3.12 (final releases) and actually runs each candidate: a
    Microsoft Store "python" shortcut, a broken install, an older or newer Python, or another
    project's virtual environment is skipped. With none available it offers to install Python 3.12
    for your user only: through the Windows Package Manager when present, otherwise (or if that
    fails) with the official python.org installer, which is run only after its SHA-256 and its
    Python Software Foundation signature check out. PATH is not changed.
  - Rebuilds the program's environment when it is missing or broken, and reinstalls packages when
    they changed or no longer import.
  - Checks the browser engines on disk at every start (no more marker files) and downloads only
    the missing part: the Camoufox browser, its GeoIP data, or Chromium. If one is still missing it
    says which website will not work.
  - Refuses, with a plain explanation, a folder whose path is too long for Windows to load the
    program from (over 140 characters), a folder it cannot write to, and a setup file started from
    inside the downloaded ZIP.
  - Works from folders whose names contain spaces, `&`, `!` or brackets, and ignores Python
    settings left over from other tools.
  - If the program fails to start, the error is shown in the setup window and saved to
    `files\runtime-data\logs\last-launch-errors.log`.
- **The window warns at start-up when a browser engine is missing**, and draws sharply on scaled
  (125%/150%) Windows displays.
- **Version 0.3.0** in the package (`__version__`), and the README rewritten for this release.
- **Verified on Python 3.12.10 and 3.13.12:** the full test suite on both, and the setup file run
  end to end from a scratch copy (a fresh build, a broken environment rebuilt, a start-up crash shown,
  the everyday start in about 2 seconds, and a build on 3.12 alone).

### Fixed — findings of the independent code review (Checkpoint 07)
- **Resume never shrinks a saved file.** If a resume could fetch fewer chapters of a grouped file
  than the file already held, the smaller file used to replace it. The earlier file is now kept and
  the group stays open for a later resume; chapters it holds are not reported as failed.
- **More story kept:** "Editor-in-Chief Zhou frowned…", "Ed—he was…" and similar opening or closing
  lines were removed as translator credits. A credit now needs a colon or a spaced dash.
- **Free Web Novel (.net) never reports chapters it did not read as missing:** when a list's numbers
  run ahead of their positions, the run now reads back to the requested Start instead of calling the
  unread chapters gaps, and a gap is only ever reported inside list pages that were read.
- **Free Web Novel (.net) with an unreadable page selector** estimates the pages and does not shorten
  End to the first page.
- **Repeat runs from saved pages are fast on Free Web Novel (.net) and WebNovel:** a chapter served
  from the saved-page cache no longer waits the delay between fetches (a cached 1,432-chapter run
  waited about 72 minutes for nothing). The run summary now says how many chapter pages came from
  the cache ("From the saved-page cache: N chapter page(s), no request to the site").
- **Run journal:** a write that fails part-way (a full disk) is cut off, so the journal stays usable
  for Resume; every journal line now ends the same way on Windows.
- **Acceptance aid:** `files/audit_run.py <run folder>` checks a finished run against its own record
  (chapter order, repeats and gaps, every file's checksum, TXT headings vs the recorded chapters,
  PDF headings with `--pdf-text`, failed chapters with reasons) without printing any chapter text.
- **Tests:** 1,456 total (31 new in `test_setup_bootstrap.py`, 5 in `test_audit_run.py`; new tests for
  each review finding and the cache-hit pacing).

### Fixed — Stop, offline runs and memory on long runs (Checkpoint 06)
- **Stop is prompt while files are being built.** A long "Whole selected range" or "Every X chapters"
  file now stops at the next chapter when Stop is pressed (it used to finish every remaining file,
  which could take minutes). Files already finished are kept; Resume builds the rest from the saved
  pages. The chapter being fetched when Stop is pressed is still saved.
- **Offline-friendly "through the last chapter" runs:** if the site cannot be reached (or another
  window is updating the chapter list) and the saved list already covers the request, the run uses it
  and says clearly that newer chapters were not looked for, instead of failing.
- **Lower memory on long runs:** each chapter's text is released once its file is written.
- **Tests:** 1,402 total (10 new in `test_reliability.py`, including a 300-chapter run with
  intermittent and permanent failures whose counts, journal and resume must add up exactly).

### Added — Lord of the Mysteries 1 and 2 on Free Web Novel (.com) and (.net) (Checkpoint 04)
- **Two new novels, each on two websites:** *Lord of the Mysteries* and *Lord of Mysteries 2: Circle
  of Inevitability* on **Free Web Novel (.com)** and **Free Web Novel (.net)**, each checked live
  (chapter lists, early/middle/late chapters, small end-to-end runs). The two websites are separate
  choices with separate chapter lists, saved pages and run folders; a run never mixes them.
- **Free Web Novel (.net)** reads the site's paginated chapter list (only the pages a run needs) and
  works over plain HTTP, with no browser window. (Its Lord of the Mysteries list was thought to skip
  chapter 1303; it lists it at a misspelt address, read since the round-1 acceptance fix above.)
- **Browser runs start hidden (headless) by default.** At the first Cloudflare check the app opens a
  visible browser for the rest of the run and retries that chapter there. A saved choice is kept.
- The Website menu now says "Free Web Novel (.com)" and "Free Web Novel (.net)".
- **Tests:** 1,392 total (12 new in `test_freewebnovel_net.py`; catalog, headless-default and breaker
  tests updated to the approved behaviour).

### Added / Changed — TXT output, one canonical chapter text and faithful PDFs (Checkpoint 05)
- **TXT output:** tick **TXT**, **PDF** or both (PDF on, TXT off at every start). TXT files are UTF-8
  and land in the run folder's `TXT/`, with exactly the same chapters, order and file grouping as the
  PDFs. Unticking the last format is not possible, so every run produces something.
- **Story text is no longer deleted.** The old cleanup threw away short lines and any paragraph
  containing words like "comment", "report" or "login". Measured on cached pages it removed thousands
  of real lines ("Painful!", "Boom!", "He sighed.", "***", quoted thoughts, a story's own repeated
  lines, "He reported to the captain"). Now only what is clearly not the story goes: translator/editor
  credit lines at the start or end, a repeated chapter heading at the top, whole lines of reader
  interface ("Next Chapter", "COMMENT") and the website's own promotional sentences ("Continue
  reading on freewebnovel.com"), never an author's note.
- **One heading per chapter, the same in TXT and PDF:** `Chapter 12: Title` (no added period), with
  the site's junk and repeated numbers removed ("Chapter 1 - 1 Foreigners" -> "Chapter 1: Foreigners",
  "Chapter 714 – Title" -> "Chapter 714: Title"); `Chapter 12: Untitled` (with a warning) when a page
  has no title. Fractional chapters (`Chapter 627.2: …`) now get a real heading in the PDF.
- **PDFs keep every character:** letters and symbols the standard print font cannot draw (Chinese,
  Greek, arrows, stars) are drawn from an installed Windows font instead of printing as the wrong
  glyphs; ordinary text looks exactly as before (same layout and fonts). Headings never sit alone at
  the bottom of a page.
- **Tests:** 1,376 total (69 new in `test_content_output.py`: cleanup rules, both FreeWebNovel page
  structures, TXT/PDF word-for-word equality, every format x grouping combination, Unicode in PDFs).
  Older tests now expect headings without the forced period and a story's repeated lines kept.

### Added / Changed — Numbered run folders, a checksummed run journal and real Resume (Checkpoints 03.3-03.4)
- **Every run gets its own folder:** `~/Downloads/Web-Novel-Scraper-Outputs/<novel>-N` (for example
  `lord-of-the-mysteries-1`), numbered per novel whatever the website, reserved safely so two runs
  can never share or overwrite one. The folder is only made once the chapter list is settled, so a
  refused run leaves nothing behind. The **Folder name** field is gone; **Browse…** chooses where the
  run folders go. Older folders are never moved, changed or reused.
- **Files are in `PDF/` inside the run folder.** The whole-range file is named by its range
  (`Shadow_Slave_Chapters_1-500.pdf`) instead of `..._All_Chapters.pdf`.
- **Resume Existing Run works.** It lists the unfinished runs (newest first, with how many files are
  already saved) or lets you pick a run folder, and continues that run exactly as it was set up: the
  same chapters, formats and grouping, with no chapter-list request. Every saved file is checked by
  its SHA-256; only missing, damaged or never-recorded files are made again, and only their chapters
  are fetched. A run whose source, formats or grouping no longer match, whose journal is damaged, or
  that is open in another window is refused with the reason, and nothing is changed.
- **Interruption-safe:** each folder keeps `run-selection.json` (the run's chapters), an append-only
  `run-journal.jsonl` (checksummed records of every published file and every failure) and
  `run-audit.json` (chapter by chapter: what was expected, which file holds it, its checksum, what
  failed and why; no chapter text). Files are written to a temporary name, flushed and swapped in
  atomically; a crash at any point is recovered by Resume. A file's name alone is never taken as
  proof that it was saved.
- **Grouped files:** two pages that show the same chapter number no longer collide; a group that
  had a failed chapter is still written (and recorded as incomplete), and a later Resume completes it.
  Stopping a run never writes a partial grouped file.
- **Tests:** 1,306 total (34 net new, including 32 in `test_run_resume.py` covering both pipeline paths
  and every grouping mode). Tests that resumed by re-running into the same folder now resume
  explicitly; tests of the old filename-based skip became "a stray file is never trusted"
  regressions. The suite now fails, rather than hangs, if a dialog opens unexpectedly. Mutation check
  16/16 caught.

### Changed — End auto-clamp and a validated, self-updating HTML cache (Checkpoint 03.2b + owner overrides A/B)
- **An End past the novel's last chapter is clamped again (owner decision, ADR 011, superseding the
  03.2a refusal).** `1-5000` on a novel whose site lists 3,700 chapters now scrapes `1-3700` and says
  so in Activity (`NOTE: range adjusted: …`), in the run summary and on the report
  (`requested_range` stays as asked, `effective_range` is what was scraped). Only a discovery made
  in the same run, that returned normally and vouches for its tail, may set the end; a failed,
  empty or possibly short list still refuses. A Start past every listed chapter ends as "None of
  chapters X-Y are available" with nothing fetched. Nothing past the end is recorded, so the next run
  finds newly published chapters. A run whose range has gaps in the site's own list now finishes as
  "Finished with gaps" rather than "Completed".
- **The HTML cache is now validated and self-maintaining (ADR 012).** Pages are kept per website and
  novel (`files/cache/<host>/<novel>/`) with a provenance record (address, time, size, SHA-256,
  method). A saved page is reused with no network request only if every check passes; a challenge
  page, garbled or truncated download, a page answered from another address, or a page that turns
  out to show the wrong chapter is never served (it is set aside and fetched again once). Writes are
  atomic. `.net` and `.com` can never share pages.
- **New chapters can no longer be hidden by a stale index:** the table-of-contents page is always
  fetched fresh and never cached, so an open-ended run (blank End) or an extension always sees the
  site's current last chapter. Chapter pages stay cached indefinitely.
- **Offset guard:** a Free Web Novel page that shows a different chapter number than the chapter list
  expects is never published as that chapter; the chapter fails visibly with a hint to refresh the
  chapter list.
- **Rescued chapters are cached** once they extract correctly, so a later run needs no rescue.
- **Existing cache kept safe:** pages saved by earlier versions (`files/cache/<novel>/`) are copied
  into the new layout when first needed, after the same checks; the old files are never moved,
  changed or deleted.
- **Re-download saved chapter pages on the next run:** a new one-shot box in Advanced Settings fetches
  the selected chapters from the site again and replaces the saved copies.
- **Tests:** 1,272 total (61 net new: `test_html_cache.py`, clamp regressions on both pipeline paths,
  WebNovel tail confirmation, GUI re-download box). Cache tests that wrote bare HTML files now use the
  provenance contract; tests that asserted the 03.2a refusal now assert the clamp. Mutation check
  15/15 caught.

### Changed — Manifest-first chapter lists in both pipeline paths (Checkpoint 03.2a)
- **The chapter list now comes from the validated, source-specific manifest** (tracked baseline +
  ignored runtime overlay under `files/runtime-data/chapter-lists/<host>/<novel>.json`) instead of
  an output-folder `chapter_index.json`. Both the standard path and the FreeWebNovel
  rescue path use one shared preflight (`chapter_preflight.py`). `.com` and `.net` keep separate
  lists. No dependency, catalog, adapter, request-manager, cache, PDF or output-folder change.
- **Fewer TOC requests:** a finite range the saved list already covers makes no TOC request. Only the
  uncovered part is discovered (the offset binary search and the `count + 64` ceiling are
  unchanged), seeded with everything already known, and saved after each reconciled window. An
  interrupted discovery keeps what it resolved and claims only that. An open-ended range re-checks
  a bounded tail every run so a stale last chapter is not trusted.
- **No more silent clamping:** a finite range the list cannot provide (for example `1-5000` on a
  4402-chapter novel) is refused with the missing chapters named, before anything is fetched.
  Leave End blank for "through the last available chapter". A hole inside the site's own list is
  still reported as a TOC gap and skipped, and is never recorded as a confirmed absence; a
  confirmed absence is a reasoned skip.
- **The old `chapter_index.json` is no longer written or trusted.** An existing one is checked by
  the strict importer (matching identity, complete, every row valid); anything else is ignored with
  a readable reason and the list is discovered from the site. The file is never changed. The
  pipeline's own `_load_index` (which accepted an identity-less v2 file) is removed.
- **Safe with two programs open:** a per-source cross-process writer lock (stdlib) covers load,
  merge and save. A second writer is refused with a clear message instead of losing an update; a
  covered list can still be read. The lock is released automatically if a program dies.
- **Refresh chapter list:** a one-shot checkbox in Advanced Settings rebuilds the saved list on the
  next run. The old list is kept as `<name>.json.bak-<utc>`, the tracked baseline is untouched, and
  a refresh that fails or is cancelled before saving puts the old list back.
- **Exact labels:** `627.1` and `627.10` stay two pages through the legacy bridge, and Supreme
  Magus's two chapter-3664 pages stay distinct through the manifest.
- **Tests:** 84 net new (81 in `test_chapter_preflight.py`, `test_chapter_store_writer.py` and
  `test_ui_chapter_list.py`), including a real second process holding the lock, interrupted
  discovery, corrupt/conflicting lists, cross-host separation and the real FreeWebNovel adapter on a
  synthetic offset novel. Test fixtures use on-host chapter URLs; tests that asserted clamping or
  the trusting index loader were replaced by refusal and strict-import regressions. A mutation check
  of 12 key rules found no surviving mutant that applies on Windows. The suite also passes on
  Python 3.12.10.

### Added — Chapter manifest store and legacy index import (Checkpoint 03.1, no behaviour change)
- **Nothing in the app changes yet.** The new store is tested and unwired: the GUI, pipeline
  (including `pipeline._load_index`), catalog, adapters, `RequestManager`, PDF/TXT output, output
  folders and Resume button are untouched, and no dependency was added.
- **`webnovel_scraper/chapter_manifest.py`:** the manifest schema of plan §9.2 (`schema_version`
  1, source identity, provenance, coverage spans, chapters, source-confirmed absences,
  `content_sha256`) with strict validation on build and on load: exact source match, host-scoped
  page URLs, canonical decimal-text labels (`627.2` and `627.10` stay distinct), unique pages and
  slugs, stable order, duplicate displayed numbers kept, and a 20 MB cap. Coverage cannot claim
  more than the entries show: every whole number in a span must be a chapter or a
  source-confirmed absence, and an unexamined gap is never treated as absent.
- **`webnovel_scraper/chapter_store.py`:** loads a tracked baseline
  (`scripts/webnovel_scraper/chapter_lists/<host>/<novel>.json`, read-only at run time) and a
  mutable runtime overlay (`files/runtime-data/chapter-lists/<host>/<novel>.json`, Git-ignored),
  and merges them by source page URL. Contradictions (a page with two labels, titles or slugs,
  two pages with one slug, absence versus a listed chapter) are reported, never resolved by file
  age, and the affected labels leave coverage until rediscovered. `freewebnovel.com` and
  `freewebnovel.net` never share a file.
- **Safe writes and quarantine:** overlays are validated, then published atomically (the old file
  survives any failure). A corrupt overlay is renamed to `<name>.corrupt-<utc>[-N]` (never
  deleted or overwritten), and loading falls back to the baseline. A corrupt tracked baseline is
  only reported and is never renamed or rewritten. A rename failure leaves the file in place and
  blocks overwriting it.
- **Legacy `chapter_index.json` import (explicit call only):** read-only, schema 2 only, with a
  matching identity block, `complete: true` and every row valid. Bare lists, v1, missing identity,
  incomplete or cross-site files are rejected with a reason. Only contiguous whole-number runs
  become coverage, the file's SHA-256 is recorded, and the import is idempotent and never
  overwrites newer overlay coverage. The old file is never moved, rewritten or deleted.
- **Docs:** plan §9.10 records the owner decisions (Folder name field retired in 03.3;
  pre-0.3.0 folders never automatically resumable), the two contract clarifications (baselines are
  never quarantined; keep the approved Downloads output root) and the API boundaries for 03.2.
  ADR 009 has an addendum.
- **Tests:** 247 added (`test_chapter_manifest.py`, `test_chapter_store.py`,
  `test_chapter_legacy_import.py`), offline and deterministic, including atomic-publication
  failure and recovery, quarantine collisions and I/O failures, and the legacy file's bytes and
  modification time unchanged. Mutation checks on the validators, the quarantine rules and the
  import gates found no surviving mutant. The suite also passes on Python 3.12.10.

### Added — Source identity, safe run allocation and manifest/journal contracts (Checkpoint 03.0, no behaviour change)
- **Nothing in the app changes yet.** Three new modules are tested and unwired: the GUI, pipeline,
  catalog, adapters, `RequestManager`, PDF output and Resume button are untouched, and runs are
  still named by `pipeline.resolve_output_dir`.
- **`webnovel_scraper/source_identity.py`:** a validated source identity (canonical novel slug,
  adapter, https origin on a supported host, index URL on that host, source identifiers). `.net`
  and `.com` are different sources even though they share an adapter. A chapter is identified by
  its source page URL, so two pages that both display chapter 3664 stay distinct. Displayed numbers
  are stored as canonical text, so `627.2` and `627.10` survive serialization.
- **`webnovel_scraper/run_storage.py`:** an allocator for `<root>/<novel-slug>-N`
  (default root `~/Downloads/Web-Novel-Scraper-Outputs`). It reserves each new run with an
  exclusive `mkdir`, so concurrent runs cannot claim the same folder. Existing and abandoned
  folders are skipped, never overwritten or reused. A preview reserves nothing, and
  `open_existing_run` is the read-only validation gate for the later Resume.
- **`webnovel_scraper/atomic_io.py`:** an atomic JSON write (temp file, fsync, replace).
- **Contracts:** plan §9 defines the baseline and runtime chapter manifests, coverage as set
  membership, the legacy `chapter_index.json` import policy (read-only, identity-matched,
  tightening the current trust of identity-less files), the run journal, the HTML cache
  boundaries and the interruption matrix. ADR 009 records the identity decisions.
- **Tests:** 201 added (`test_source_identity.py`, `test_run_storage.py`), offline and
  deterministic; they include concurrent threads and processes.

### Added / Changed — Website-first selection, preferences, Resume placeholder and counter fix (Checkpoint 02.3)
- **Section 1 is now Website, Novel, Start chapter, End chapter**
  (`scripts/app.py`, new `scripts/webnovel_scraper/ui/selection.py`, no Tk).
  - Pick the website first, then a novel that website can run. Both come from the
    unchanged catalog. Websites with no enabled source stay listed as "coming soon"
    and cannot be chosen; the Novel list holds only runnable novels, so a disabled
    combination can never reach Start.
  - Changing the website keeps the novel if the new website offers it, otherwise it
    takes that website's first novel. Either change refreshes the website text,
    browser options (inert for the plain-HTTP site), Start, the hint and the output
    preview. Start passes the same catalog row's novel and adapter as before.
  - A website is identified by its **host**, not its adapter (DECISIONS.md #008), so
    a later `.net` and `.com` entry for one novel stays two choices even if they share
    an adapter. No catalog entry was added (those are validated in Checkpoint 04).
  - The selection survives Light/Dark switching, Advanced Settings and a run.
- **Resume Existing Run** is shown in Section 3, disabled, with "Available after
  resume support is implemented." It has no command, is never enabled by a run, a
  selection change or a theme change, and cannot start a scrape or reuse a folder.
  The real feature belongs to Checkpoint 03.
- **Ordinary preferences are saved** (`ui/settings.py`, schema 2): appearance,
  website and novel, grouping, chunk size, delay, timeout, and the browser,
  headless, HTTP-first and cache switches.
  - Saved at discrete moments only: a website, novel or grouping choice, closing
    Advanced Settings, Start, and closing the window; never per keystroke. A theme
    change saves the theme and resets nothing else.
  - A schema-1 (appearance-only) file opens unchanged and becomes schema 2 on the
    next save. A bad field is replaced alone by its default; a bad or newer file
    falls back to defaults and the original is kept; a failed save is named once in
    the status bar and the window keeps working.
  - A saved website or novel that is gone, disabled or unknown falls back to a
    runnable pair; until the user chooses one, the saved choice is left as it is.
  - Fresh-install defaults are unchanged (Individual chapters, chunk 10, delay 3.0,
    timeout 30, browser on, headless **off**, HTTP-first off, cache on, PDF on, TXT off).
  - Never saved: chapter range, folder name, output location, progress or results,
    credentials, cookies, session data or novel text. Output folders are still
    `Downloads/<novel>-N` (that changes in Checkpoint 03).
- **Fixed: the final counter line clipped at the minimum width.** The counters now
  wrap onto further lines (the whole row once the run has ended; beside the ETA while
  it shows). The longest form is reserved in the measured minimum and the row's height
  is held, so nothing below it moves. All five counters were checked readable at the
  minimum, default and maximized sizes and at simulated 125% and 150% scaling.
- **Fixed: "coming soon" websites in the Dark menu.** Windows draws a disabled menu
  entry embossed, which read brighter than the real choices. In Dark on Windows they
  are now drawn dim and do nothing when chosen; elsewhere they stay ordinary disabled
  entries.
- **Layout:** the minimum window is 635x632 at 100% (was 635x590): the Resume row
  and the reserved counter lines cost height only; the width is unchanged and Activity
  still takes all growth.
- **Tests:** 125 added; the baseline 554 is intact. Four superseded expectations were
  updated (website-first order and values, the file now holds every preference, the
  counters wrap). Gate: 679 passed, 0 skipped.
- **Not in this change:** any scraper, adapter, catalog, PDF, TXT, cache, manifest,
  output-folder, headless-default or dependency change, and the real Resume Existing Run.

### Added / Changed — Run progress, ETA, counters and accurate run state (Checkpoint 02.2)
- **Section 3 (Run) now tells the truth about the run** (`scripts/app.py`, new
  `scripts/webnovel_scraper/ui/run_progress.py`).
  - **Phase:** Idle, Preparing chapter list, Scraping chapters, Building output,
    Stopping, then Complete, Finished with problems, Finished with no PDFs,
    Stopped, Run failed or Run refused. Warnings and errors recolour the status
    in Light and Dark.
  - **Progress:** while the chapter list is prepared there is no number and the
    bar is indeterminate. Once the total is known it reads `done / total · %`
    (rounded down, so 100% means every selected chapter). It counts selected
    source pages, never PDFs, so two pages that share a displayed number count
    separately.
  - **Counters:** "already present" while running; after the end PDFs written,
    failed, recovered, already present and numbers missing from the site's list,
    all read from the run report.
  - **ETA:** measured on the monotonic clock from the chapters actually
    fetched. It starts after the chapter list is built, leaves out chapters
    resume skipped, waits for 3 chapters and 5 s, follows the slower of the whole
    run and the recent chapters, and turns back into `—` during a long silence,
    while stopping or building, and after the end. Formatting adapted from
    PDF-Splitter `jobs.py`.
  - **Never success by percentage:** 100% processed with failures ends as
    "Finished with problems"; a normal end whose own count stops short is not
    "Completed"; late routine updates cannot overwrite the final state.
  - **Outcome text:** an all-already-present run says no new PDFs were written;
    an explicit end beyond the site's list and gaps in the list are reported; an
    open-ended "through the last chapter" request is never called cut short.
- **Pipeline instrumentation (observability only, DECISIONS.md #007):** an
  optional `run_scrape(..., phase_cb=None)` reports `"scraping"` (with the
  selected total and the already-present count) and `"building"`. Existing
  callers, ticks, retries, rescue, cancellation and the run report are unchanged
  (a test runs with and without it); a raising observer is ignored.
- **Layout:** one extra compact row in Section 3. The minimum window is
  635×590 (was 635×571); the width is unchanged and Activity still takes all
  growth.
- **Tests:** 117 added. The 02.1 fakes gained `phase_cb=None`, and its status and
  count assertions follow the new texts. Gate: 554 passed, 0 skipped.
- **Not in this change (deferred):** a live current-chapter name and live
  failed/recovered counters (the callbacks cannot give them reliably), Resume
  Existing Run, website-first selection, and structured pipeline events.

### Added / Changed — Activity Summary/Detailed logging and thread-safe event bridge (Checkpoint 02.1, UI/controller only)
- **Summary and Detailed Activity tabs with Clear Log** (`scripts/app.py`,
  PDF-Splitter's Activity design).
  - **Summary** shows:
    - the run start: novel and website, chapter range, output and folder;
    - preparation milestones;
    - warnings and recoveries;
    - Stop and Close requests;
    - the final outcome and counts.
  - **Detailed** shows all of that plus every existing technical line, unchanged
    (fetches, retries, cache, browser and recovery, failures, and a traceback for
    an unexpected error).
  - Warnings and errors are labelled in words and coloured, in Light and Dark.
  - Both logs are read-only, monospaced and scrollable. A view follows new lines
    unless you have scrolled up to read, and a tab opened later starts at its
    newest line.
  - Switching tabs or appearance keeps each history and the selected tab.
  - Clear Log empties the display only.
- **Run outcome from the run report, not the percentage.** A new
  `scripts/webnovel_scraper/ui/run_activity.py` turns the pipeline's `RunReport`
  into the final Summary lines: Completed, Finished with problems (listing failed,
  not-found and empty chapters), Finished but nothing written, Stopped, Failed
  (unexpected error), or Refused. A run that processed every chapter but lost some
  is never called a success. The pipeline's existing text messages reach Summary
  through a short table of literal markers copied from those messages, guarded by
  a test that fails if a message is reworded. Every line still reaches Detailed
  unchanged.
- **Thread-safe event bridge.** A new `scripts/webnovel_scraper/ui/activity.py` is
  adapted from PDF-Splitter `scripts/pdf_splitter/activity.py` (the author's local
  working copy supplied 2026-10-17, newer than GitHub `edcbd4f`), itself from the
  Audiobook Creation Tool v0.6.6. It provides structured events, a bounded locked
  channel, a capped display model, the session log and a Tk-thread pump.
  - **No Tk from the worker.** The scrape worker no longer calls Tk at all, not
    even `after`: the pipeline's `log`/`progress_cb` callbacks and the outcome
    only queue events, and the window drains them on its own timer in bounded
    slices.
  - **What can be coalesced or dropped.** Progress updates replace each other
    while queued. Warnings, errors, Summary lines and the run's finish are never
    dropped; routine detail left out of a flooded display is counted and
    announced.
  - **After the window closes,** late events are refused, so nothing touches it.
  - **Changes from PDF-Splitter:** the session log is written when an event is
    emitted, not when it is drawn, so it stays complete and in true order (ADR
    006). A Tk-thread report first shows everything already queued.
- **Per-session technical logs.** Each window session writes one UTF-8 log under
  `files/runtime-data/logs/` (gitignored), named `session-YYYYMMDD-HHMMSS.log`.
  - Each line is timestamped and flushed as written; the log is closed on exit.
  - It holds message text only: no chapter text, and cookie or authorization
    values are redacted. Overlong messages are capped.
  - An unwritable folder or a failed write shows one Summary warning and the
    scraper carries on.
  - Clear Log never deletes it.
- **Behaviour fixes in the run controls:**
  - Stop and Close now report their request before signalling cancel, so it
    always appears before what it causes.
  - A run that ends while the window is closing no longer re-enables Start.
- **Unchanged:**
  - the three sections, Light/Dark switching, measured geometry (minimum
    635×571 at 100%; the tabs add 4 px), Start/Stop, validation and progress;
  - the Advanced Settings window, output naming and `DEFAULT_HEADLESS = False`;
  - every scraper, adapter, pipeline and PDF module.

  Not in this step: phase-aware progress and ETA, run-state redesign,
  website-first selection and Resume Existing Run (later Checkpoint 02 steps).
- **Tests:** 90 new offline tests in `test_ui_activity.py`,
  `test_ui_run_activity.py` and `test_ui_activity_panel.py`. They cover:
  - Summary/Detailed routing and severity;
  - ordering across threads;
  - queue capacity and coalescing;
  - completion under load;
  - no Tk from the worker (by spy and by source inspection);
  - Stop and graceful-close order;
  - nothing after destroy;
  - session-log naming, redaction, concurrency and failure;
  - Clear Log keeping the log;
  - both tabs across Light/Dark;
  - following new lines;
  - layout at the minimum size;
  - a full offline run through the real pipeline.

  `test_ui_shell.py` now reads the two tabs, and its worker check is stricter;
  `test_phase4_gui.py`/`test_phase8_gui.py` are unchanged. Suite: 437 passed,
  0 skipped.

### Added / Changed — Light/Dark UI foundation and window shell (Checkpoint 01.1, UI only)
- **Light/Dark appearance.** New `scripts/webnovel_scraper/ui/appearance.py`,
  adapted from PDF-Splitter `scripts/pdf_splitter/appearance.py` (the author's
  local working copy supplied 2026-10-17, newer than GitHub `edcbd4f`; this file
  is identical there), which came from the Audiobook Creation Tool v0.6.6. It
  provides compact `Compact.*` ttk styles cloned from `clam`, Light and Dark
  palettes, native Aqua controls on macOS, and an in-place switch that keeps every
  typed value, selection, focus and a running scrape. Not taken: the Treeview,
  Spinbox and Inline-button styles. Added: a Website-picker style that matches
  the Novel dropdown, and colouring for its classic menu.
- **Saved appearance choice.** New `scripts/webnovel_scraper/ui/settings.py`
  (trimmed from PDF-Splitter's validated store) saves only the appearance, in
  `files/runtime-data/settings.json`. That folder is now gitignored. A missing,
  corrupt, foreign or newer file opens in Light with a status-bar note, a load
  never rewrites the file, and the first save keeps a copy of an unusable
  original.
- **New window shell** (`scripts/app.py`, PDF-Splitter layout):
  - three bordered sections on the left: **1. Web Novel Selection**,
    **2. Output Options** and **3. Run**;
  - an **Activity** panel on the right holding the existing log;
  - a bottom status bar with the Light/Dark toggle.

  Window size is measured from the widgets and display scaling rather than
  fixed: the controls column keeps its natural width, Activity takes all extra
  space when the window is widened or maximized, and long novel names or paths
  never widen the window.
- **Advanced Settings window.** Delay, timeout, browser mode, headless,
  HTTP-first and HTML cache moved there unchanged, opened from section 3. The run
  still reads them at Start and locks them during a run.
- **Unchanged behaviour:**
  - the start/stop/close run lifecycle, non-daemon worker and job settings;
  - output folder naming;
  - `DEFAULT_HEADLESS = False`;
  - the catalog;
  - every scraper, pipeline and PDF module.

  New: a run-state line and a `done / total chapters` count, both fed by the
  existing progress callback.
- **UI decisions:**
  - Novel stays above Website, because the Website menu lists the selected
    novel's sites. A website-first flow is Checkpoint 02 work.
  - PDF shows ticked and locked and TXT shows "not available yet" until a TXT
    renderer exists (Checkpoint 05).
  - The grouping choices are relabelled "Individual chapters", "Whole selected
    range" and "Every X chapters per PDF", with the same three output modes as
    before.
  - Activity has no Summary/Detailed tabs yet (Checkpoint 02).
  - On Windows the app is not made DPI-aware (same as PDF-Splitter), so Windows
    scales the whole window above 100%.
- **Tests:** 47 new offline tests in `test_ui_settings.py`, `test_ui_appearance.py`
  and `test_ui_shell.py`. They cover:
  - Light/Dark switching and state preservation;
  - persistence and recovery from corrupt settings;
  - the three-section structure;
  - Activity resizing, minimum size, maximize/restore and long names;
  - Start/Stop;
  - no Tk calls from the worker thread;
  - settings staying inside the repository.

  The existing `test_phase4_gui.py`/`test_phase8_gui.py` guarantees still run;
  the headless-hint check now reads the note under the checkbox. New
  `files/tests/pytest.ini` sets `--capture=sys`. This fixes an intermittent "no
  Tk display" skip: under pytest's default fd capture, about 1 in 300 Tk windows
  on Windows failed to start. Suite: 347 passed, 0 skipped.

### Changed — flat `scripts/` layout (step 01.0, no behaviour change)
- Program code moved from `scripts/Universal/` to `scripts/`. The entry point
  is now `scripts/app.py` and the package is `scripts/webnovel_scraper/`; file
  contents are unchanged. The empty `scripts/Windows/` and `scripts/MacOS/`
  placeholders were removed.
- Updated to match: both `Setup_and_Run` launchers (`MAIN_SCRIPT`), the test
  bootstrap (`files/tests/conftest.py`), `files/bypass_eval.py`, and the
  repo-root depth in `request_manager.py` / `browser_env.py`. The HTML cache
  (`files/cache/`) and the contained Playwright browsers
  (`files/bin/ms-playwright/`) stay inside the repository.
- New `files/tests/test_flat_layout.py` (8 offline tests) guards the import
  root, the in-repo cache/browser paths, the launcher entry points, and one
  fixture chapter through parser → pipeline → PDF. Suite: 300 passed.

## [0.2.1] — Unreleased

- Reusing an output folder for a different novel/site no longer silently mixes
  TOCs and content: `chapter_index.json` now records the novel, adapter, and
  source-site identity. If the saved index belongs to a different target, the run
  logs a warning and automatically rebuilds the TOC for the current novel instead
  of trusting stale data.

### Fixed — whole-novel FreeWebNovel reconciliation, repeated displayed numbers, TOC gaps
Commit `577b5db` (2026-07-27). It was local-only until it became the base of the
v0.3.0 working branch `feature/v0.3.0-modernization` (2026-10-17).

- **Faster, resumable offset reconciliation.** `freewebnovel.py` binary-searches
  each offset transition and fetches only the real insert/discontinuity clusters.
  A realistic 4,417-slug synthetic needs 131 chapter-page fetches. The live
  Supreme Magus TOC reconciled in 314 s with 101 fetches. Progress is logged as
  `reconciled through slug N of M`. The `count + 64` fetch ceiling is unchanged.
- **Checkpointed chapter index.** `pipeline.py` atomically writes
  `chapter_index.json` with `complete: false` while reconciling. A stopped or
  crashed run resumes from those rows, and success replaces the file with
  `complete: true`.
- **Missing source pages are not fatal.** A 404 slug during reconciliation (live
  Supreme Magus: 1471, 1472, 1653) is logged and treated as an offset
  discontinuity.
- **Repeated displayed numbers keep both chapters.** When two source pages show
  the same chapter number (Supreme Magus 3664 at slugs 3678 and 3679), both are
  indexed and both PDFs are written. The later one gets a ` [slug N]` filename
  suffix. Resume matching is per source page. A repeated output path within one
  run raises an error instead of overwriting, and the duplicate is listed in the
  final warnings. Before this fix, one PDF silently replaced the other: the run
  reported `Written: 4408` but left 4,407 files on disk.
- **Rescue deduplicates by URL.** `rescue_pool.py` deduplicates jobs by source URL
  only, so two pages that show the same chapter number are both rescued.
- **TOC gaps are reported.** Integer chapter numbers missing inside a TOC
  (Renegade Immortal: 1416–1419, 1421–1424) are logged, stored on
  `RunReport.toc_gaps` and listed in the final summary. Such a run no longer
  looks complete.
- Tests: regression coverage for each fix; full gate 292 passed. Live acceptance:
  Supreme Magus `1-5000`, one PDF per chapter, visible browser, cache off.
  It produced a complete 4,414-row index, 4,414 distinct PDFs and `Failed: 0`.

### Fixed — FreeWebNovel chapters addressed by URL slug instead of the site's displayed number
On some FreeWebNovel novels (confirmed: Supreme Magus), the URL slug in
`.../chapter-N` counts every page — real chapters plus joke-chapter/glossary
extras and fractional ".x" side-chapters — while the page header displays the
author's own numbering. The scraper used the slug as the chapter number, so a
user typing a displayed chapter number (e.g. 4207) fetched the wrong page
(slug 4207 displays "Chapter 4192"; the page that actually displays "Chapter
4207" lives at slug 4222). The offset is not constant — it grows from 2 to 15
across Supreme Magus and keeps growing as the author publishes more inserts —
so a fixed per-novel offset was ruled out by measurement.

- **Addressing is now by the site's DISPLAYED chapter number**, not the URL
  slug. `ChapterMeta`/`ChapterContent.index` mean the displayed number
  everywhere downstream (filenames, resume matching, the range clamp, PDF
  headings, `RunReport`); a new `ChapterMeta.slug` field carries the URL slug
  the adapter actually fetches. `index` is `int | float`: fractional
  side-chapters ("Chapter 627.2") are kept as their own addressable label
  under a unique name, sorting naturally between their integer neighbours.
  Unnumbered/negative extras (a joke chapter, a "Chapter -1" volume glossary)
  are skipped and logged — no longer silently emitted as a degraded
  "Chapter N."
- **Auto-detected per novel, zero extra cost for the common case.**
  `build_chapter_index` already fetches the index/TOC page to discover the
  chapter count; that same page's anchors carry both the URL slug and the
  site's own "Chapter N: Title" text, so identity vs. offset is decided from
  that SAME fetch — no dedicated probe, no extra network call — for every
  novel where slug and displayed number already agree (Shadow Slave, Reverend
  Insanity, Renegade Immortal): output is byte-identical to before, proven by
  a test asserting the fetch count is unchanged. Only a genuinely offset novel
  pays a resolution cost: a binary search over the monotone displayed(slug)
  relationship anchors near the requested range, then a padded per-page walk
  reconciles each chapter's real displayed number (fetched HTML is cached and
  reused by the content fetch, so the walk and the real fetch are one network
  hit per chapter, not two).
- **The user's requested chapter range bounds the resolution walk.** The
  pipeline now passes the requested start/end through to
  `build_chapter_index` as hints, so a live run on an offset novel jumps near
  the requested range instead of scanning unbounded from slug 1 (a ~4400-slug
  novel's full-novel walk would otherwise cost ~4400 extra fetches before any
  content is written). Every other adapter (and every existing test fake) is
  unaffected — the pipeline only forwards the hints when the adapter's own
  method declares them, so nothing outside FreeWebNovel's code changed shape.
- **`chapter_index.json` schema bumped to v2** (rows now carry `slug`) —
  ANY older index (including a v1 file whose identity still matches) is
  rebuilt on the next resume rather than trusted, since old rows are
  slug-space with no slug column at all. One logged TOC re-fetch per affected
  output folder, then it resumes correctly from then on.
- New fixtures: sanitized live Supreme Magus captures (`files/test-files/fwn_sm_*.html`)
  covering the offset regression (slug 4222 → displayed "Chapter 4207"), a
  fractional side-chapter (slug 630 → "Chapter 627.2"), and the unnumbered
  joke-chapter/glossary extras. New `files/tests/test_chapter_number_offset.py`
  covers the model, the fixtures, identity zero-extra-fetch, binary search +
  bounded walk (including an end-to-end pipeline-level proof that a live-shaped
  request near a high chapter number stays bounded), and the
  `_discover_max_chapter` slug-vs-displayed fallback fix.
- See `md-instructions/DECISIONS.md` for the full reasoning (why `index` means
  the displayed number, why identity detection is free, why the walk is
  bounded, why the schema bump supersedes the 0.2.1 legacy-index-trust
  allowance above).

## [0.2.0] — 2026-06-30

Fast-primary scraping with a **single background hard-chapter rescue lane**. The
0.1.3 architecture ran one persistent VISIBLE browser for *every* FreeWebNovel
chapter, which is slow when most chapters need no bypass at all. 0.2.0 splits the
work: a fast primary pass races through the easy chapters, and only the genuinely
hard ones (a real Cloudflare challenge) are handed to **one** dedicated background
rescue worker that escalates a fixed strategy ladder. There is exactly **one**
rescue lane in 0.2.0 — `RESCUE_MAX_WORKERS = 1`, enforced as a hard cap (the pool
constructor rejects any other count); the user-selectable 1–5 worker toggle is
**deferred to 0.2.1**. No release tag/date until the manual live pass signs off.

### Added — single-lane hard-chapter rescue (`rescue_pool.py`, new)
- A `RescuePool` owning **one** dedicated worker thread (no thread pool) with a
  bounded job queue (`RESCUE_MAX_PENDING = 16`) and an unbounded results queue. The
  worker builds and owns its **own** `RequestManager` and `FreeWebNovelAdapter` on
  its own thread (sharing only the run's `HostRateLimiter` and `cancel_event`), and
  tears the browser down in a `finally`.
- **Ladder-as-data, monotonically escalating.** The rescue ladder is a fixed list
  of `RescueStep`s — `headless_camoufox` → fresh `headless_camoufox` →
  `headful_camoufox` → `headful_chromium` — that the worker walks per chapter and
  only ever escalates: once it latches a stronger mode it never drops back for a
  later chapter. The **initial rung follows the primary** (a headless-primary run
  starts at the headless rung; a visible-primary run skips the headless rungs), so
  rescue never starts *weaker* than the primary.
- **Per-chapter processing deadline** of `RESCUE_MAX_ELAPSED_PER_CHAPTER = 180s`
  (measured from dequeue; queue wait is not charged). Before each attempt the worker
  computes the remaining budget, refuses to start a rung it cannot finish, and caps
  the attempt timeout to what is left.
- **One terminal result per accepted job** — including a job that was queued and
  then cancelled (it terminalizes as `cancelled`, never silently dropped). Immutable
  `RescueResult` (`rescued` / `rescue_exhausted` / `cancelled` / `not_found` /
  `extraction_failed` / `pool_failed`); a worker crash terminalizes the active job
  and drains the backlog as `pool_failed` rather than hanging.

### Added — global fair host limiter (`host_rate_limiter.py`, new)
- A `HostRateLimiter` shared by the primary and the rescue lane so both obey **one**
  per-host pace. Per-host FIFO ticket ordering (no starvation), a positive-only
  jitter after the interval, a global floor `HOST_MIN_INTERVAL = 3.0s`, a
  `raise_interval` ratchet that never lowers, and a 429 host-cooldown
  (`note_rate_limited` / `blocked_until`). The pipeline `_Pacer` feeds it: the shared
  effective interval is `max(HOST_MIN_INTERVAL, job.delay, pacer.current)`, so the
  adaptive auto-slowdown now paces the rescue worker too.

### Added — typed failures + body-first classification (`request_manager.py`)
- Typed `FetchError` subclasses: `NotFoundFetchError` (404/410, terminal),
  `ChallengeFetchError`, `TransientFetchError`, and `RateLimitedFetchError`
  (carrying `retry_after`). Classification is now **body-first**: a real chapter
  payload is a success even under a 403/503; a Cloudflare body (including a CF-style
  503) is a `Challenge`; 404/410 is `NotFound`; 429 is `RateLimited` (and notifies
  the shared limiter's cooldown rather than escalating to the browser); 5xx is
  `Transient`. **403 is no longer treated as permanent** (`PERMANENT_STATUSES` is now
  `(404, 410)`). A `last_fetch_info` record (cache vs. network, and the outcome
  class) is exposed for the circuit breaker.
- **Fast/HTTP-probe split.** A `fetch(..., fast_path=True)` mode walks a short, bounded
  fast ladder (camoufox ×2, no stealth rung) used by the primary and TOC; optional
  HTTP probes are *extra* and never consume the browser budget.
- **Explicit per-manager timeouts.** The constructor now takes explicit
  `http_timeout` / `browser_nav_timeout` / `cloudflare_timeout`, **retiring the old
  mutated module-global `FETCH_TIMEOUT`** entirely.

### Added — pipeline conductor, TOC bootstrap, headless-only breaker (`pipeline.py`)
- A fast-primary loop that races the easy chapters and hands each genuinely hard
  chapter to the single rescue lane (built lazily on the first hard chapter, so an
  easy run never spins up a worker). Rescue is the **sole** retrier on the
  FreeWebNovel-browser path; the old end-of-run sweep is replaced there. WebNovel
  (Dynamic) keeps its unchanged HTTP-only sweep.
- **Scope gate.** Rescue is enabled only for `adapter_key == "freewebnovel"` **and**
  `job.use_browser` — a WebNovel-dynamic or HTTP-only run never builds a pool.
- **Pipeline-owned, headless-only circuit breaker.** The breaker is *armed only when
  the run started headless* (so rescue never starts weaker than the primary), counts
  only uncached primary **network** fetches, and trips on **≥5 consecutive** network
  challenges **OR ≥9 challenges within a rolling window of 20**
  (`BREAKER_CONSECUTIVE_CHALLENGES = 5`, `BREAKER_WINDOW = 20`,
  `BREAKER_WINDOW_CHALLENGES = 9`). On a trip the pipeline **recreates the primary
  manager/adapter as visible and latches** there for the rest of the run (closing the
  old pair exactly once), and synchronously retries the triggering hard chapter on the
  now-visible primary before falling back to rescue.
- **TOC/index bootstrap fallback.** If building the chapter index is blocked under a
  headless primary, the pipeline retries it once with a visible primary, and aborts
  cleanly if it is still blocked.
- **429 host cooldown.** A rate-limited primary fetch is retried on the shared limiter
  cooldown up to `RATE_LIMIT_RETRY_BUDGET = 2` times (never escalated to rescue); a
  persistent 429 is surfaced as transient so resume can pick it up.
- `RunReport` gained rescue/breaker metrics (`rescue_exhausted` ⊆ `failed`,
  `rescue_queue_peak`, `rescue_jobs_submitted` / `_completed`, `rescue_worker_failures`,
  `circuit_breaker_tripped`, `primary_switched_visible`, `rescue_strategy`) and two
  summary lines.

### Added — `ScrapeJob` run-config (`models.py`)
- `ScrapeJob` now carries the run configuration that used to be set by mutating
  globals/catalog rows: `use_browser`, `headless`, `request_timeout`, and
  `rescue_workers` (validated `== 1` in `__post_init__` — the single-lane invariant).
  A new `runtime_site_spec(spec, job)` returns a per-run `SiteSpec` copy with
  `use_browser` taken from the job, so fetching is driven by the job **without
  mutating the shared catalog row**.

### Changed — GUI wiring (`app.py`)
- The **delay default is now 3.0s** (was 2.0), matching the shared host-limiter floor.
- **The pipeline owns the request manager.** The GUI no longer pre-builds a
  `RequestManager` or mutates `SiteSpec.use_browser`; it builds an immutable
  `ScrapeJob` (use_browser / headless / request_timeout / `rescue_workers == 1`) and
  hands it to the pipeline, which constructs, replaces (on a breaker trip), and closes
  the active manager itself.
- **Accurate Headless hint.** The Headless checkbox now reads: *"Headless primary
  browser (advanced). If Cloudflare broadly blocks headless mode, the app may
  automatically open and keep a visible browser for the rest of the run; hard chapters
  may also open a visible rescue browser."*
- **Rescue/breaker activity surfaces in the existing log pane** (no new widgets) — e.g.
  "chapter N → rescue", "chapter N rescued", "chapter N failed after all methods", and
  "primary mode broadly blocked — switching to a visible browser…". Progress ticks only
  on terminal chapter states (including rescued chapters); all GUI updates are marshalled
  via `self.after(...)` and rescue threads never touch tkinter.
- **Non-daemon worker + poll-until-exit close.** The scrape worker is now non-daemon so
  the pipeline's teardown `finally` always runs; closing the window while a scrape is
  running signals `cancel_event`, marks the app closing, and polls on the Tk event loop
  until the worker exits before destroying — rather than destroying immediately.

### Tests
- New `files/tests/test_phase1_rescue_core.py`, `test_phase2_rescue_pool.py`,
  `test_phase3_rescue_conductor.py`, and `test_phase4_gui.py` (typed-failure /
  classification / limiter / pool / conductor / GUI-wiring coverage), plus a light
  offline release-metadata check. Existing assertions were adjusted (not deleted) to
  track the deliberate behaviour changes (typed `ChallengeFetchError`,
  `PERMANENT_STATUSES = (404, 410)`). No live network and no real browser launch in the
  suite. Suite: **229 offline tests** (`verify` green: 228 passed, 1 expected
  no-Tk-display skip).
- **Honest status:** offline tests prove wiring/flow/lifecycle. Whether a **headless**
  primary can clear FreeWebNovel's current Cloudflare (and how often the breaker has to
  fall back to a visible browser, or hard chapters to the visible rescue lane) is the
  open question the manual live pass answers.

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
