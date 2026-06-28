"""Telegraph adapter — STUB (disabled), deferred past 0.1.0.

Known shape: a single flat all-volumes page, structurally unlike a paginated
TOC. Listed in the GUI (greyed) until a real implementation lands here.
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
