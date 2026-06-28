"""NovelBin adapter — STUB (disabled), deferred past 0.1.0.

Known issue: was Cloudflare-blocked in the old v3 scraper. Listed in the GUI
(greyed) until a real implementation (and a working CF path) lands here.
"""

from __future__ import annotations

from ..models import ChapterContent, ChapterMeta, SiteSpec
from .base import BaseAdapter


class NovelBinAdapter(BaseAdapter):
    key = "novel_bin"
    enabled = False

    def build_chapter_index(self, spec: SiteSpec) -> list[ChapterMeta]:
        raise NotImplementedError("adapter disabled")

    def fetch_chapter(self, meta: ChapterMeta, spec: SiteSpec) -> ChapterContent:
        raise NotImplementedError("adapter disabled")
