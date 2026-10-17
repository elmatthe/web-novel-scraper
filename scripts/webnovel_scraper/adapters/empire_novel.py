"""Empire Novel adapter — STUB (disabled), deferred past 0.1.0.

Listed in the GUI (greyed) so enabling later is a one-line catalog flip plus a
real implementation here.

Note for whoever implements this: check whether Empire Novel's chapter
numbering (if any) can drift from any URL/slug numbering the same way
FreeWebNovel's does (see fix-chapter-number-offset, models.py's
``ChapterMeta.index``/``slug`` split) before assuming the two always agree.
"""

from __future__ import annotations

from ..models import ChapterContent, ChapterMeta, SiteSpec
from .base import BaseAdapter


class EmpireNovelAdapter(BaseAdapter):
    key = "empire_novel"
    enabled = False

    def build_chapter_index(self, spec: SiteSpec) -> list[ChapterMeta]:
        raise NotImplementedError("adapter disabled")

    def fetch_chapter(self, meta: ChapterMeta, spec: SiteSpec) -> ChapterContent:
        raise NotImplementedError("adapter disabled")
