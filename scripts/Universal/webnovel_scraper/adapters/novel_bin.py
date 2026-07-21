"""NovelBin adapter — STUB (disabled), deferred past 0.1.0.

Known issue: was Cloudflare-blocked in the old v3 scraper. Listed in the GUI
(greyed) until a real implementation (and a working CF path) lands here.

Note for whoever implements this: check whether NovelBin's chapter numbering
(if any) can drift from any URL/slug numbering the same way FreeWebNovel's does
(see fix-chapter-number-offset, models.py's ``ChapterMeta.index``/``slug``
split) before assuming the two always agree.
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
