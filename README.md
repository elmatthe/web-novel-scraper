# Web Novel Scraper

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
| Shadow Slave | Empire Novel | ⏳ Coming soon |
| Shadow Slave | Telegraph | ⏳ Coming soon |
| The Noble Queen | WebNovel (Dynamic) | ✅ Available |
| The Noble Queen | Novel Bin | ⏳ Coming soon |
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
   runs). Everything else (the program's packages and its browser engines) is
   installed **inside the project folder**.
4. When setup finishes, the app window opens. Every time after that, the same
   double-click checks that nothing is missing, repairs anything that is, and opens
   the app.

If the program ever stops starting, delete the **`.venv`** folder inside the
project folder and double-click the setup file again; it rebuilds everything.

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
  files that are already saved are checked and kept, and only what is missing is done.

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
- **Refresh chapter list on the next run**: rebuilds the saved chapter list from the
  website once (the old list is kept as a backup).

### Browser windows

Free Web Novel (.com) is behind Cloudflare's bot protection, so the app uses a real
browser engine for it (Camoufox, with stealth Chromium as a fallback; both are
installed by the setup file). It starts hidden; if a visible browser window opens
during a run, that is expected. Don't close it, and keep it **on screen** (not
minimised) while it works, because a minimised browser can stall a Cloudflare check.
It closes itself when the run ends.

Free Web Novel (.net) and WebNovel (Dynamic) don't need a browser, and no window
appears for them.

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

## Platform

Windows only (Windows 10 and 11). macOS support is planned for a later release.
