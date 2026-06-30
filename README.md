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

Free Web Novel sits behind Cloudflare's bot protection, and Cloudflare clears for
a **real, visible browser** while it blocks hidden (headless) automation. So for
Free Web Novel the app uses a real browser engine **from the very first request**,
in a **visible window** — it tries camoufox first and, if that can't get through,
automatically falls back to a second visible browser engine (stealth Chromium); both
are downloaded once during setup, no admin needed. You don't need to change anything
— just press Start.

**A browser window will open and stay open while a Free Web Novel scrape runs.**
That is expected and is how the download clears Cloudflare (just like the older
tool). Don't close it; it is reused for every chapter and closes itself when the
run ends.

You normally won't touch these checkboxes:

- **Use browser mode** — on by default for Free Web Novel; leave it on.
- **Headless browser** — *advanced*, off by default. Ticking it hides the window,
  but Cloudflare usually blocks a hidden browser, so leave it **off**.
- **Try fast HTTP first** — *advanced*, off by default. It tries a couple of quick
  no-browser attempts before the browser. Plain HTTP usually trips Cloudflare on
  Free Web Novel, so leave it **off** unless you're experimenting.

WebNovel (Dynamic) is not behind Cloudflare and uses the fast no-browser path
automatically — no window appears for it.

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
