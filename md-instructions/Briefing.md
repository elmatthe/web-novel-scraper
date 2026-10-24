# Briefing — Web Novel Scraper

## Purpose and platform

A Windows 10/11 desktop app for downloading supported web novels into PDF and/or
TXT. Python 3.12/3.13, tkinter/ttk, requests/BeautifulSoup, Camoufox/Playwright,
ReportLab, pypdf/pdfplumber and pytest. Dependencies are exactly pinned in
`scripts/requirements.txt`. Only extraction noise and whitespace are cleaned;
source prose, exact titles and chapter identities must remain intact.

## Current Version

**0.3.0, Windows implementation accepted by the owner.** Live scraping and HTML
cache reuse succeeded across all enabled novels and websites. Latest triage found
no missing Supreme Magus content; three manually renamed PDFs explain its stale
journal paths. The Lord of the Mysteries chapter 1099 bullet encoding defect is
fixed. Its original publication/journal and the separately preserved repair stay
untouched. Other source and cancellation warnings were correct; pending outcome
reporting has regression coverage. The owner reported no remaining production defect. Final regression reconciliation
and integration results are recorded in `handoff.md`.

Main integration is authorized for the verified implementation and cleanup. No
tag or GitHub Release is part of this task. macOS acceptance remains deferred.
The `.command` launcher is retained for future platform work. A physical test
of Python installation on a Python-free PC and 125%/150% display scaling was not
established on this already configured machine; neither is an open production
defect. Do not repeat completed live downloads for this cleanup.

## Catalog

Nine enabled novel/site pairs across three adapters:

- Free Web Novel (.com): Shadow Slave, Reverend Insanity, Renegade Immortal,
  Supreme Magus, Lord of the Mysteries, and Lord of Mysteries 2: Circle of
  Inevitability.
- Free Web Novel (.net): Lord of the Mysteries and Lord of Mysteries 2: Circle
  of Inevitability.
- WebNovel (Dynamic): The Noble Queen.

Empire Novel, Telegraph and Novel Bin remain disabled future adapter stubs.
The README and `catalog.py` describe the selectable matrix.

## Architecture and invariants

- `scripts/app.py` is the GUI entry point. `scripts/webnovel_scraper/` contains
  all application logic; launching never depends on development files.
- `catalog.py` is the shared GUI/pipeline catalog; `registry.py` owns adapter
  registration. Adapters parse; the request manager owns networking; PDF/TXT
  builders know nothing about sites. GUI updates stay on the Tk thread.
- `SourceIdentity` binds novel, adapter, host, index URL and identifiers. Chapter
  labels represent observed displayed identity; slugs identify fetched URLs.
  Exact fractional labels (including trailing zeroes) must not be rounded.
- `chapter_manifest.py`, `chapter_store.py`, `chapter_preflight.py` and
  `observed_preflight.py` persist and validate source-specific saved lists.
  Supreme Magus requires independent per-page observations; inferred offsets
  cannot certify its inventory. Missing identity, title drift and contradictory
  observations are refused rather than hidden by requested metadata.
- With Refresh off, verified saved coverage is reused without TOC requests.
  Its upper bound is historical; newer chapters are explicitly untested.
  Missing coverage requires Refresh or a covered range. Refresh checks live
  TOCs; Supreme Magus revalidates 64 tail slugs plus new slugs, retains the
  verified prefix, and fully revalidates conflicts. Incomplete refresh evidence
  cannot certify an unfinished tail. Full refresh preserves the old list backup.
- `html_cache.py` stores original HTML with host/novel/URL provenance, length
  and SHA-256 sidecars under `files/cache/`. Hits must also pass page and adapter
  identity checks. WebNovel redirects are admitted only with matching chapter
  identity. Rejected HTML is retained diagnostically. Legacy HTML is copied,
  never removed. TOCs are fetched fresh only when discovery is required.
- Cache hits and same-run discovery reuse avoid website pacing. The host
  limiter paces real request attempts, rechecks monotonic deadlines and supports
  cancellation. Cache settings never promise a network-disabled run.
- .com normally uses a persistent headless Camoufox session, switching to visible
  browser recovery on challenge, with one bounded rescue worker and Chromium
  fallback. .net uses HTTP; WebNovel uses HTTP with browser fallback. Keep
  retries, cooldowns and source-list pagination bounded and source-scoped.
- Numbered output folders are allocated exclusively under the chosen output
  parent (default Downloads/Web-Novel-Scraper-Outputs). `run-selection.json`,
  checksummed `run-journal.jsonl`, `run-audit.json` and `chapter-bodies/` bind
  selection, accepted content, formats and semantic publication certificates.
  A filename alone never proves success. Different sources cannot Resume one
  another, even when the novel slug matches.
- Individual files and complete chunks publish during acquisition; whole-range
  files publish after acquisition. Stop keeps completed artifacts and accepted
  bodies. Resume admits the fixed selection/policy, verifies recorded bytes and
  bodies, skips intact certified outputs, rebuilds from bodies, and fetches only
  what remains. Legacy policy or corrupt evidence is refused before mutation.
- PDF/TXT publish atomically. PDF certificates require canonical text/order and
  no blank pages. Supplementary characters use UTF-16 Unicode mappings; bullets
  use an embedded TrueType mapping. Missing glyphs leave PDF unfinished and
  preserve complete TXT even for PDF-only requests. Embedding restrictions are
  respected. Never weaken the text verifier to make a renderer pass.
- Output symlinks/junctions/reparse points are refused. Temp cleanup recognizes
  only `.wns-output-*.part`; unrelated/ambiguous files are preserved.
- `run_accounting.py` distinguishes selected, attempted, processed, pending,
  failed, unattempted and fallback-preserved outcomes. Cancellation is not a
  manufactured failure. Pending requested publication cannot report success.
  Source-list gaps and warnings survive Resume. `run_performance.py` supplies
  transport counts, cache reuse and stage timings to Activity.

## Adding novels and adapters

For an existing site, add a stable `SiteSpec` in `catalog.py` with the exact
index URL and site identifiers. Add sanitized representative HTML/JSON fixtures
in `files/test-files/` and meaningful regressions in `files/tests/`; cover both
fresh discovery and saved-list reuse. Test fractions, extras, titleless pages,
wrong identities and changed bounds when relevant. Never import an owner's
manifest or novel into tracked fixtures.

For a new site, implement `BaseAdapter.build_chapter_index` and `fetch_chapter`,
register the class in `registry.py`, and use the shared request manager, cache
validation and normalized models. Validate observed chapter/book identity before
acceptance; never manufacture missing chapters. Keep catalog entries disabled
until offline adapter/GUI integration and intentional live acceptance succeed.
Do not add site parsing or network logic to the GUI. Retain disabled stubs and
local research/name lists as future integration resources.

## Verification and tools

Run `.venv/Scripts/python.exe -B scripts/verify.py` once at the final checkpoint.
It runs all `files/tests/` tests plus dependency-pin and permanent-document
checks. Use `python -B -m pytest files/tests/<test_file>.py -q` in that environment
for focused regressions. Tests isolate preferences, manifests and outputs; real
adapter/GUI fixtures cover all nine enabled choices and all publication modes.
No live scrape belongs in the offline suite. Keep test scratch under ignored
`files/run-temp/` and disable bytecode writes when checking protected environments.

Keep `files/audit_run.py`, `audit_html_cache.py`, `bypass_eval.py`,
`option_b_runner.py`, `full_publication.py`, `offline_pdf_repair.py` and their
regressions: they support independent diagnostics and isolated republication.
Runner mode/safety details live in `files/option_b_runner.md`.
`files/tests/windows_clean_install_smoke.py` is an explicit network-dependent
source-only installer/GUI acceptance tool, outside pytest discovery. It refuses
an existing scratch installation. Do not rerun it during offline cleanup.
The Windows launcher checks real interpreter/dependency/browser readiness and
repairs damaged imports with forced reinstall; healthy installs are reused.

## Data protection and known source warnings

Never clean `files/cache/`, `files/runtime-data/`, `files/bin/`, `.venv/`,
certified manifests, publications, owner downloads or external repair evidence.
Do not rewrite historical journals to hide manually renamed files or old PDF
failures. Historical acceptance datasets under `files/run-temp/` require a
separate archival decision. Unknown local references remain local and untouched.
Generated evidence is ignored; reusable tools and tracked fixtures remain tracked.

Untitled pages, source-confirmed gaps and duplicate bodies are warnings with
retained content. Supreme Magus has fractional labels and unnumbered extras.
LOTM2 .com chapters 1130–1138 have confirmed composite headings whose final
number determines identity. .net has some titleless entries and a misspelt
chapter-1303 URL, accepted only as source-listed. Cloudflare/site changes can
still need visible challenge completion or later retry. See `DECISIONS.md` for
architectural history, `CHANGELOG.md` for changes and `handoff.md` for current state.
