# Web Novel Scraper

A simple desktop app for Windows that downloads web-novel chapters from supported
sites and saves them as clean, print-ready PDFs. Pick a novel and a source site,
choose a chapter range and how you want the chapters split up, and click **Start** —
the app fetches the chapters and writes the PDFs to your Downloads folder. It only
cleans up site clutter (navigation links, comment sections, blank lines); the actual
words of the chapters and their titles are left exactly as published.

## Supported novels and sites

| Novel | Site | Status in this version |
|-------|------|------------------------|
| Shadow Slave | Free Web Novel | ✅ Available |
| Shadow Slave | Empire Novel | ⏳ Coming soon |
| Shadow Slave | Telegraph | ⏳ Coming soon |
| The Noble Queen | WebNovel | ✅ Available |
| The Noble Queen | Novel Bin | ⏳ Coming soon |
| Reverend Insanity | Free Web Novel | ✅ Available |
| Renegade Immortal | Free Web Novel | ✅ Available |
| Supreme Magus | Free Web Novel | ✅ Available |

"Coming soon" sites are listed in the app but greyed out — they're planned for a
later release.

## Quick start

You do **not** need to install Python or anything else yourself, and you don't need
to open a terminal.

1. Download the project as a ZIP and unzip it anywhere (Desktop, Downloads, a USB
   stick — wherever you like).
2. Double-click **`Setup_and_Run-Web-Novel-Scraper.bat`**.
3. The first time, a setup window opens and gets everything ready. If Python isn't on
   your PC, it will ask permission (just for your account — no admin needed) and
   install it for you. It then sets up everything else inside the project folder.
4. When setup finishes, the app window opens. Every time after that, the same
   double-click takes you straight to the app.

### First-run security note

Because this file was downloaded from the internet and isn't code-signed, Windows
may warn you the **first** time you run it ("Windows protected your PC"). Click
**More info**, then **Run anyway**. This is normal and only happens once.

## Using the app

1. Pick a **Novel**, then a **Site** (greyed-out sites aren't available yet).
2. Set the **start** and **end** chapter (leave the end blank to grab everything).
3. Set the **Delay between fetches (seconds)** — this is your anti-detection /
   politeness control. Higher is slower but less likely to be blocked; the default
   (2 seconds) is a good balance. If the site starts blocking mid-run the app also
   **raises this delay automatically** to back off, then keeps going.
4. Choose how to save the chapters:
   - **Separate** — one PDF per chapter
   - **Chunked** — a set number of chapters per PDF
   - **Single** — all chapters in one PDF
5. Click **Start**. Use **Stop** to cancel cleanly at any time.

The app does not give up on a chapter easily: each chapter is retried several
times through escalating methods, and after the main run finishes it makes a
**second pass** over any chapters that failed (intermittent blocks often clear a
few minutes later). Only chapters that are genuinely missing (a real "not found")
are skipped quickly so a long run never hangs.

### Cloudflare handling

Some sources (Free Web Novel especially, and occasionally WebNovel) sit behind
Cloudflare's bot protection, but you normally don't need to do anything about it.
A normal download starts on the fast path and **automatically switches to a real
browser engine** (camoufox, downloaded once during setup — no admin needed)
whenever Cloudflare actually blocks a page, then goes back to the fast path — so
most runs need no special setting.

Note that turning browser mode **off does not mean "never use the browser"** — it
just means *start* on the fast path. The app can still escalate to the browser
engine on its own when a page is blocked. The browser is a starting choice, not a
hard cap.

**Use Playwright browser mode** is an optional override: it forces the browser
engine from the very first request instead of only when Cloudflare blocks. It's
only worth ticking if Cloudflare is actively challenging *every* page and you
want to skip the quick first attempts on each chapter. Leave **Headless** on
unless you want to watch the browser work.

## Where the files go

By default, finished PDFs are saved to your Downloads folder in a new folder named
for the novel, for example:

```
C:\Users\<you>\Downloads\shadow-slave-1\
```

You can change this before you start: click **Browse…** next to **Output folder**
to pick any folder with the normal Windows picker, and optionally type a **Folder
name** of your own (leave it blank to use the novel's name).

Either way, a number (`-1`, `-2`, …) is added to the end, so each run creates a
fresh folder and a new run never overwrites an old one. The app also remembers what
it already downloaded, so if you stop and re-run **into the same folder**, it skips
chapters you already have.

## Platform

Windows only for now. macOS support is planned for a later release.
