"""Telegraph adapter — STUB (disabled), deferred past 0.1.0.

Known shape: a single flat all-volumes page, structurally unlike a paginated
TOC. Listed in the GUI (greyed) until a real implementation lands here.

Note for whoever implements this: check whether Telegra.ph's chapter numbering
(if any) can drift from any URL/slug numbering the same way FreeWebNovel's does
(see fix-chapter-number-offset, models.py's ``ChapterMeta.index``/``slug``
split) before assuming the two always agree.
"""

from __future__ import annotations

from ..models import ChapterContent, ChapterMeta, SiteSpec
from .base import BaseAdapter


class TelegraphAdapter(BaseAdapter):
    key = "telegraph"
    enabled = False

    def build_chapter_index(self, spec: SiteSpec) -> list[ChapterMeta]:
        raise NotImplementedError("adapter disabled")

    def fetch_chapter(self, meta: ChapterMeta, spec: SiteSpec) -> ChapterContent:
        raise NotImplementedError("adapter disabled")
