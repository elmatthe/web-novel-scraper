#!/usr/bin/env python3
"""Scratchpad bypass bake-off worker (NOT shipped).

Runs exactly ONE bypass strategy against ONE site (index page + optionally a
chapter page) in an isolated process, then prints a JSON result line and exits.
Process isolation is deliberate: camoufox runs its own internal sync-Playwright
and our Chromium stealth path runs ours; the two cannot share a thread, and a
fresh process per strategy also guarantees no orphaned browser survives a run.

Usage:
    python files/bypass_eval.py --site {fwn,wnd} --strategy NAME [--pages index,chapter]

Strategies: http, cloudscraper, stealth_headless, stealth_visible,
            camoufox_headless, camoufox_visible

Content-success is judged by actually parsing with the shipping adapters
(real chapter count / real body paragraphs), not just "not a CF stub".
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "Universal"))

from webnovel_scraper import cf_bypass  # noqa: E402
from webnovel_scraper.adapters import freewebnovel as fwn_ad  # noqa: E402
from webnovel_scraper.adapters import webnovel_dynamic as wnd_ad  # noqa: E402
from webnovel_scraper.models import SiteSpec  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Targets. WND chapter uses a real chapterId pulled from the live TOC.
TARGETS = {
    "fwn": {
        "spec": SiteSpec(
            novel_slug="shadow-slave", novel_title="Shadow Slave",
            adapter_key="freewebnovel", display_name="Free Web Novel", enabled=True,
            url="https://freewebnovel.com/novel/shadow-slave",
            base_url="https://freewebnovel.com",
        ),
        "index": "https://freewebnovel.com/novel/shadow-slave",
        "chapter": "https://freewebnovel.com/novel/shadow-slave/chapter-1",
    },
    "wnd": {
        "spec": SiteSpec(
            novel_slug="the-noble-queen", novel_title="The Noble Queen",
            adapter_key="webnovel_dynamic", display_name="WebNovel (Dynamic)", enabled=True,
            url="https://dynamic.webnovel.com/story/28684090500376805",
            book_id="28684090500376805", base_url="https://dynamic.webnovel.com",
        ),
        "index": "https://dynamic.webnovel.com/story/28684090500376805",
        "chapter": "https://dynamic.webnovel.com/story/28684090500376805/76998299197949227",
    },
}


def judge(site: str, page: str, html: str) -> dict:
    """Return {content_ok, detail, cf} for a fetched page using real adapters."""
    cf = cf_bypass.is_cloudflare_challenge(html)
    try:
        if site == "fwn":
            if page == "index":
                spec = TARGETS["fwn"]["spec"]
                ad = fwn_ad.FreeWebNovelAdapter(log=lambda *_: None)
                n = ad._discover_max_chapter(html, spec)
                return {"content_ok": bool(n and n > 10), "detail": f"max_chapter={n}", "cf": cf}
            else:
                ad = fwn_ad.FreeWebNovelAdapter(log=lambda *_: None)
                from webnovel_scraper.models import ChapterMeta
                cc = ad._extract_chapter(html, ChapterMeta(index=1, url=""))
                return {"content_ok": len(cc.paragraphs) > 3,
                        "detail": f"paras={len(cc.paragraphs)} chars={sum(len(p) for p in cc.paragraphs)}",
                        "cf": cf}
        else:  # wnd
            if page == "index":
                d = wnd_ad.parse_next_data(html) or wnd_ad.parse_g_data_book(html)
                if not d:
                    return {"content_ok": False, "detail": "no TOC payload", "cf": cf}
                # support both shapes
                book = (d.get("props", {}).get("pageProps", {}).get("data", {})
                        if "props" in d else d).get("bookInfo") or d.get("bookInfo") or {}
                vols = ((d.get("props", {}).get("pageProps", {}).get("data", {})
                         if "props" in d else d).get("volumeItems")
                        or d.get("volumeItems") or [])
                n = sum(len((v or {}).get("chapterItems") or []) for v in vols)
                return {"content_ok": n > 10, "detail": f"chapters={n}", "cf": cf}
            else:
                raw_title, paras = wnd_ad.extract_chapter(html, 1)
                return {"content_ok": len(paras) > 3,
                        "detail": f"title={raw_title!r} paras={len(paras)} chars={sum(len(p) for p in paras)}",
                        "cf": cf}
    except Exception as e:
        return {"content_ok": False, "detail": f"parse-error: {type(e).__name__}: {e}", "cf": cf}


def fetch_http(url: str, scraper: bool) -> str:
    if scraper:
        import cloudscraper
        s = cloudscraper.create_scraper()
    else:
        import requests
        s = requests.Session()
    s.headers.update({"User-Agent": UA})
    r = s.get(url, timeout=30, allow_redirects=True)
    if r.status_code in (403, 404):
        raise RuntimeError(f"HTTP {r.status_code}")
    return r.text


def run(site: str, strategy: str, pages: list[str], cooldown: float) -> list[dict]:
    tgt = TARGETS[site]
    results = []

    # Browser engines are opened once and reused for both pages.
    pw = browser = context = page = cm = None
    try:
        if strategy in ("stealth_headless", "stealth_visible"):
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            browser, context = cf_bypass.create_stealth_browser(
                pw, headless=(strategy == "stealth_headless"))
            page = context.new_page()
        elif strategy in ("camoufox_headless", "camoufox_visible"):
            cm, page = cf_bypass.create_camoufox_browser(
                headless=(strategy == "camoufox_headless"))

        for i, pg in enumerate(pages):
            if i > 0:
                time.sleep(cooldown)
            url = tgt[pg]
            t0 = time.time()
            err = None
            html = ""
            try:
                if strategy == "http":
                    html = fetch_http(url, scraper=False)
                elif strategy == "cloudscraper":
                    html = fetch_http(url, scraper=True)
                elif strategy.startswith("stealth"):
                    html = cf_bypass.fetch_with_stealth(page, url, cf_timeout=45,
                                                        log_fn=lambda *_: None)
                elif strategy.startswith("camoufox"):
                    html = cf_bypass.fetch_camoufox(page, url, cf_timeout=45,
                                                    log_fn=lambda *_: None)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
            elapsed = round(time.time() - t0, 2)
            if err:
                results.append({"site": site, "strategy": strategy, "page": pg,
                                "ok": False, "error": err, "secs": elapsed,
                                "bytes": len(html)})
            else:
                j = judge(site, pg, html)
                results.append({"site": site, "strategy": strategy, "page": pg,
                                "ok": j["content_ok"], "detail": j["detail"],
                                "cf": j["cf"], "secs": elapsed, "bytes": len(html)})
    finally:
        # Clean teardown; process exit also guarantees no orphan survives.
        try:
            if cm is not None:
                cm.__exit__(None, None, None)
        except Exception:
            pass
        for obj, closer in ((page, "close"), (context, "close"),
                            (browser, "close"), (pw, "stop")):
            try:
                if obj is not None:
                    getattr(obj, closer)()
            except Exception:
                pass
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True, choices=["fwn", "wnd"])
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--pages", default="index,chapter")
    ap.add_argument("--cooldown", type=float, default=5.0)
    a = ap.parse_args()
    pages = [p.strip() for p in a.pages.split(",") if p.strip()]
    out = run(a.site, a.strategy, pages, a.cooldown)
    for row in out:
        print("RESULT " + json.dumps(row))


if __name__ == "__main__":
    main()
