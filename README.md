# Web Novel Scraper

**Version 0.3.0 · Windows 10/11.**

A simple desktop app for Windows that downloads web-novel chapters from supported
websites and saves them as clean, print-ready **PDF** and/or plain **TXT** files.
Pick a website and a novel, choose a chapter range and how the chapters are grouped
into files, and click **Start**. The app fetches the chapters and writes the files to
your Downloads folder. It only removes site clutter (navigation links, comment
prompts, adverts for the site); the words of the chapters are kept as published, and
every chapter starts with one heading such as `Chapter 12: The Lost Tomb`.

## Supported novels and websites

| Novel | Website | Status in this version |
|-------|---------|------------------------|
| Shadow Slave | Free Web Novel (.com) | ✅ Available |
| The Noble Queen | WebNovel (Dynamic) | ✅ Available |
| Reverend Insanity | Free Web Novel (.com) | ✅ Available |
| Renegade Immortal | Free Web Novel (.com) | ✅ Available |
| Supreme Magus | Free Web Novel (.com) | ✅ Available |
| Lord of the Mysteries | Free Web Novel (.com) | ✅ Available |
| Lord of the Mysteries | Free Web Novel (.net) | ✅ Available |
| Lord of Mysteries 2: Circle of Inevitability | Free Web Novel (.com) | ✅ Available |
| Lord of Mysteries 2: Circle of Inevitability | Free Web Novel (.net) | ✅ Available |

"Coming soon" websites are listed in the app but greyed out; they are planned for a
later release.

## Quick start

You do **not** need to install anything yourself, and you never need to open a
terminal.

1. Download the project as a ZIP and unzip it anywhere (Desktop, Downloads, a USB
   stick, wherever you like).
2. Double-click **`Setup_and_Run-Web-Novel-Scraper.bat`**.
3. The first time, the setup window gets everything ready. If no suitable Python
   (3.12 or 3.13) is on your PC, it asks first and then installs Python 3.12 for
   your account only (no admin needed; the official installer is checked before it
   runs). Packages and Chromium are installed **inside the project folder**.
   Camoufox also uses its normal per-user browser cache.
4. When setup finishes, the app window opens. Every time after that, the same
   double-click checks that nothing is missing, repairs anything that is, and opens
   the app.

If the program ever stops starting, delete the **`.venv`** folder inside the
project folder and double-click the setup file again; it rebuilds everything.

The `.venv` and `files` folders are created automatically. A fresh download needs
only the Windows setup file and `scripts/`; no saved settings, caches, test files
or previous downloads are needed. Internet access is required for first-time setup.

Keep the project folder somewhere short and writable, such as Downloads or the
Desktop: Windows cannot load the program from a folder path longer than about 140
characters, or from a protected place such as Program Files. The setup window
explains it if that happens. Extract the whole ZIP first; don't run the setup
file from inside the ZIP.

### First-run security note

Because this file was downloaded from the internet and isn't code-signed, Windows
may warn you the **first** time you run it ("Windows protected your PC"). Click
**More info**, then **Run anyway**. This is normal and only happens once.

## Using the app

**1. Web Novel Selection**

- Choose the **Website**, then the **Novel** (greyed-out websites aren't available yet).
- Set the **Start** and **End** chapter. Leave End blank to get every chapter up to the
  latest one. If End is past the novel's latest chapter, the app lowers it to the
  latest chapter and says so in the Activity panel. If the website's own chapter list
  is missing a chapter, the app reports it instead of guessing.

**2. Output Options**

- Tick **PDF**, **TXT** or both (at least one). PDF is on by default.
- Choose how chapters are grouped into files:
  - **Individual chapters**: one file per chapter
  - **Every N chapters per file**: for example 10 chapters in each file
  - **Whole selected range**: everything in one file

**3. Run**

- Click **Start**. Use **Stop** to stop cleanly at any time; files already finished
  are kept.
- **Resume Existing Run** continues a stopped or unfinished run in its own folder. The
  files and saved chapter bodies are checked and reused, and only missing chapters
  need fetching. Missing or damaged output files can be rebuilt from verified saved
  bodies even when the HTML cache is off. Completed chunks are saved during downloading.
  Warnings about gaps in the website's chapter list remain visible after Resume.

The app does not give up on a chapter easily: each chapter is retried through
escalating methods, and after the main pass it makes a **second pass** over any
chapters that failed (short blocks often clear a few minutes later). At the end the
Activity panel lists any chapter that still could not be saved.

### Advanced Settings

You normally won't need these:

- **Delay between fetches (seconds)**: the politeness control. Higher is slower but
  less likely to be blocked; the default (3 seconds) is a good balance. If a website
  starts blocking, the app also slows down on its own.
- **Use browser mode**: on by default for Free Web Novel (.com); leave it on.
- **Start with a hidden (headless) browser**: on by default. The browser starts
  hidden. If Cloudflare shows a check, the app switches to a **visible** browser for
  the rest of the run; complete the check in that window if one is shown.
- **Try fast HTTP first**: off by default; leave it off unless you're experimenting.
- **Use HTML cache** and **Re-download saved chapter pages on the next run**: chapter
  pages are saved and reused, so a repeat run is much faster. The re-download box
  fetches them fresh once.
- **Refresh chapter list on the next run**: checks the live source for updates once.
  Supreme Magus retains its verified prefix and checks the last 64 source slugs
  and new slugs; conflicting identities require full revalidation. A full refresh
  keeps the previous list as a backup. An interrupted update cannot certify an
  unfinished tail.

For repeat runs, leave **Use HTML cache on**, **Re-download saved chapter pages
off**, and **Refresh chapter list off**. Verified saved chapter lists need no
website request. A saved upper bound is historical: the Activity panel warns
that newer chapters were not checked. Incomplete saved coverage requires a
refresh or a covered range. Missing or rejected HTML still needs fetching;
cache reuse is not an offline-only mode. Cache hits bypass website pacing.
Chapter lists and HTML are kept separately for each source and novel. HTML
checksums, provenance and chapter identity are checked before reuse; rejected
pages are retained for diagnosis. The Activity panel reports cache hits/misses,
website requests, saved-body reuse and time spent fetching and rendering.

### Browser windows

Free Web Novel (.com) is behind Cloudflare's bot protection, so the app uses a real
browser engine for it (Camoufox, with stealth Chromium as a fallback; both are
installed by the setup file). It starts hidden; if a visible browser window opens
during a run, that is expected. Don't close it, and keep it **on screen** (not
minimised) while it works, because a minimised browser can stall a Cloudflare check.
It closes itself when the run ends.

Free Web Novel (.net) normally needs no browser and shows no window. WebNovel (Dynamic)
usually doesn't either, but if the site shows a security check it falls back to the same
browser engine, so a browser window may appear for it too; leave it open and on screen.

## Where the files go

Each run gets its own numbered folder, so a new run never overwrites an old one:

```
C:\Users\<you>\Downloads\Web-Novel-Scraper-Outputs\lord-of-the-mysteries-1\
    PDF\    the PDF files
    TXT\    the TXT files (when TXT is ticked)
```

Click **Browse…** to choose a different parent folder. Each run folder also holds a
small run record (`run-journal.jsonl`, `run-audit.json`) that lets **Resume** pick up
exactly where a run stopped and lists every chapter that was saved or missed.
Keep the run's records and saved chapter bodies together so Resume can verify them.

PDFs use the fonts installed on Windows. If a character cannot be represented,
the app reports its Unicode number, keeps the complete original text in a TXT file
(even when only PDF was selected), and marks the PDF unfinished. Linked PDF/TXT
folders are refused; choose a normal output folder.

### Source-content warnings

Missing titles, repeated bodies and gaps in a site's chapter list are reported;
the app preserves accepted source text instead of inventing or editing chapters.
Supreme Magus has fractional chapter labels, unnumbered extras, repeated source
bodies and untitled pages. URL chapter slugs can differ from displayed numbers.
Lord of Mysteries 2 on .com has confirmed two-number headings around chapters
1130–1138. Some .net entries lack titles or have misspelt URLs. These cases have
regression coverage. Renaming a published file makes its recorded path invalid;
keep original filenames and journals together for Resume and auditing.

## Development and regression testing

Run the complete offline gate from the repository root using the existing
environment:

```powershell
.venv/Scripts/python.exe -B scripts/verify.py
```

It runs `files/tests/`, checks exact dependency pins and checks permanent docs.
For a focused test, use `python -B -m pytest files/tests/<test_file>.py -q` with
that environment. Tests isolate preferences, manifests and outputs; fixtures
live in `files/test-files/`. Real adapter/GUI tests cover all nine enabled
novel/site choices, publication grouping, cache reuse and Stop/Resume.
`files/tests/windows_clean_install_smoke.py` is a separate, explicit acceptance
utility that installs dependencies and opens a GUI; it is outside the offline gate.

To add a novel on an existing site, add a `SiteSpec` in
`scripts/webnovel_scraper/catalog.py` with its exact index URL and identifiers,
then add sanitized fixtures and adapter/pipeline regressions. Novel slugs must
remain stable; website identity also includes the adapter, host and index URL.
For a new website, implement `BaseAdapter.build_chapter_index` and
`fetch_chapter` under `scripts/webnovel_scraper/adapters/`, register it in
`registry.py`, and keep its catalog entries disabled until verified. Use the
request manager for transport, pacing and caching; validate observed chapter
identity before returning `ChapterContent`. Keep exact fractional labels and
URL slugs separate. See [developer guidance](md-instructions/Briefing.md).

Reusable tools in `files/` include `audit_run.py` (read-only publication audit),
`audit_html_cache.py` (read-only cache/extraction audit), `bypass_eval.py`
(independent transport diagnostics), and `option_b_runner.py` with
`full_publication.py` and `offline_pdf_repair.py` (isolated discovery,
publication and offline repair). See [runner usage](files/option_b_runner.md).
Live diagnostic modes require an intentional run; the offline test gate never
downloads novels. Local historical references and acceptance datasets are
gitignored and are not required to launch the application.

## Platform

Windows only (Windows 10 and 11). macOS support is planned for a later release.
