# Noble Queen live scrape issue summary - 2026-06-27

## Run

- Site: `webnovel_dynamic`
- Novel: `the-noble-queen`
- Mode: separate per-chapter PDFs
- Requested range: chapter 1 through all available chapters
- TOC result: 864 chapters, range 1-864
- Output directory: `C:\Users\ematthew\Downloads\webscraped_the-noble-queen-2`
- Live log: `files/test-logs/scrape_noble_queen_separate.log`

## Stop state

- User requested stop during the live run.
- A cancel file was written and detected at `2026-06-27 16:18:24`.
- The scrape did not return promptly because it was inside a retry/backoff path, so the two scrape PIDs were terminated.
- Confirmed stopped: PIDs `14604` and `27728` were no longer present after termination.

## Output produced

- PDFs present: 12
- Written chapters: 1, 2, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14
- Failed chapters: 3, 4
- Last written PDF before stop: `Chapter 14_ A Friendly Spar..pdf`
- File sizes ranged from 8,256 to 10,815 bytes. No obviously empty/stub PDFs were observed among the written files.

## Why chapters failed

Chapters 3 and 4 were not scraped because the fetch layer received Cloudflare challenge pages instead of the chapter HTML. The failures happened before the adapter could parse chapter content, so this was not the expected `__NEXT_DATA__`/`g_data` parsing failure mode.

Evidence:

- Chapter 3 failed at `2026-06-27 16:06:44`.
- URL: `https://dynamic.webnovel.com/story/28684090500376805/76998433549896904`
- Error: `Cloudflare challenge still present after camoufox fetch`
- Chapter 4 failed at `2026-06-27 16:11:46`.
- URL: `https://dynamic.webnovel.com/story/28684090500376805/76998674470730088`
- Error: `Cloudflare challenge still present after camoufox fetch`

Each failed chapter exhausted the retry ladder:

- `http` returned a Cloudflare challenge.
- `cloudscraper` returned a Cloudflare challenge.
- `camoufox` still saw a Cloudflare challenge.
- `camoufox_fresh` still saw a Cloudflare challenge through the final retry.

## Main issue

The monitored expectation was that WebNovel Dynamic would stay on the plain HTTP path. That did not hold in this live run. Starting at chapter 3, plain HTTP and cloudscraper repeatedly received Cloudflare challenge pages. Later chapters sometimes recovered through `camoufox`, but the run was no longer operating as the expected browser-off/plain-HTTP WND scrape.

The likely issue is live-site anti-bot/rate-limit behavior on WebNovel Dynamic for this machine/session. It was not a shipping-code parse bug observed in the adapter: the TOC succeeded, chapter bodies that reached real HTML produced valid PDFs, and the failed chapters failed at the challenge-detection/fetch layer before `__NEXT_DATA__` or `g_data` extraction.
