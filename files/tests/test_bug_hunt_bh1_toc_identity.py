"""BH-1 regression tests: output-dir TOC identity.

The pipeline persists ``chapter_index.json`` in the output folder for resume. As
of 0.2.1 that file carries novel/site identity so reusing a folder for a different
novel or source warns and rebuilds instead of silently trusting the stale TOC.

Schema v2 (fix-chapter-number-offset) additionally rebuilds ANY older payload —
the pre-identity bare list and v1 dicts alike — because v2 changed what ``index``
means (displayed chapter number, not URL slug) and added the ``slug`` column, so
old rows are stale by meaning even when the identity matches. The former
"legacy lists remain trusted" allowance is deliberately superseded (one logged
TOC re-fetch on first resume of an old folder).
"""

from __future__ import annotations

import json

from webnovel_scraper import catalog, pipeline
from webnovel_scraper.models import ChapterContent, ChapterMeta, OutputMode, ScrapeJob, SiteSpec


CURRENT_NOVEL = "shadow-slave"
CURRENT_ADAPTER = "freewebnovel"


class RecordingAdapter:
    def __init__(self, metas: list[ChapterMeta]) -> None:
        self.metas = metas
        self.built = 0
        self.fetched: list[int] = []
        self.warnings: list[str] = []

    def build_chapter_index(self, spec: SiteSpec) -> list[ChapterMeta]:
        self.built += 1
        return list(self.metas)

    def fetch_chapter(self, meta: ChapterMeta, spec: SiteSpec) -> ChapterContent:
        self.fetched.append(meta.index)
        return ChapterContent(
            index=meta.index,
            title=f"Fresh {meta.index}",
            paragraphs=[f"Body for chapter {meta.index}, long enough to render."],
        )


def _job(tmp_path, mode: OutputMode, *, start=1, end=99, chunk_size=2) -> ScrapeJob:
    return ScrapeJob(
        novel_slug=CURRENT_NOVEL,
        adapter_key=CURRENT_ADAPTER,
        start=start,
        end=end,
        delay=0.0,
        output_mode=mode,
        use_cache=False,
        output_dir=tmp_path,
        chunk_size=chunk_size,
    )


def _meta(index: int, *, host: str = "https://freewebnovel.com") -> ChapterMeta:
    return ChapterMeta(
        index=index,
        url=f"{host}/novel/shadow-slave/chapter-{index}",
        title=f"Chapter {index}",
        source_id=str(index),
    )


def _rows(metas: list[ChapterMeta]) -> list[dict]:
    return [
        {
            "index": m.index,
            "url": m.url,
            "title": m.title,
            "source_id": m.source_id,
        }
        for m in metas
    ]


def _write_identity_index(tmp_path, *, identity: dict, metas: list[ChapterMeta]) -> None:
    (tmp_path / pipeline.INDEX_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": pipeline.INDEX_SCHEMA_VERSION,
                "identity": identity,
                "chapters": _rows(metas),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _current_identity() -> dict:
    spec = catalog.get_spec(CURRENT_NOVEL, CURRENT_ADAPTER)
    return pipeline._index_identity(spec)


def test_same_identity_index_is_trusted_without_rebuilding_toc(tmp_path) -> None:
    _write_identity_index(tmp_path, identity=_current_identity(), metas=[_meta(1), _meta(2)])
    adapter = RecordingAdapter([_meta(10), _meta(11)])

    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.SEPARATE, start=1, end=2),
        adapter=adapter,
        log=lambda _m: None,
    )

    assert adapter.built == 0
    assert adapter.fetched == [1, 2]
    assert report.effective_range == (1, 2)
    assert len(report.written) == 2


def test_mismatched_identity_warns_rebuilds_and_overwrites_index(tmp_path) -> None:
    stale_identity = {
        "novel_slug": "the-noble-queen",
        "adapter_key": "webnovel_dynamic",
        "source_url": "https://dynamic.webnovel.com/story/28684090500376805",
        "base_url": "https://dynamic.webnovel.com",
    }
    _write_identity_index(tmp_path, identity=stale_identity, metas=[_meta(50)])
    adapter = RecordingAdapter([_meta(1), _meta(2), _meta(3)])
    logs: list[str] = []

    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.SEPARATE, start=1, end=3),
        adapter=adapter,
        log=logs.append,
    )

    assert any("WARNING: chapter_index.json belongs to a different novel/site" in m for m in logs)
    assert adapter.built == 1
    assert adapter.fetched == [1, 2, 3]
    assert report.effective_range == (1, 3)

    raw = json.loads((tmp_path / pipeline.INDEX_FILENAME).read_text(encoding="utf-8"))
    assert raw["schema_version"] == pipeline.INDEX_SCHEMA_VERSION
    assert raw["identity"] == _current_identity()
    assert [row["index"] for row in raw["chapters"]] == [1, 2, 3]


def test_pre_identity_list_only_index_now_rebuilds(tmp_path) -> None:
    """Schema v2 supersedes the 0.2.1 legacy-list trust: a bare-list index holds
    slug-space rows with no slug column, so it is rebuilt (logged), not resumed."""
    legacy_metas = [_meta(4), _meta(5)]
    (tmp_path / pipeline.INDEX_FILENAME).write_text(
        json.dumps(_rows(legacy_metas), indent=2),
        encoding="utf-8",
    )
    adapter = RecordingAdapter([_meta(4), _meta(5)])
    logs: list[str] = []

    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.SEPARATE, start=4, end=5),
        adapter=adapter,
        log=logs.append,
    )

    assert any("old pre-identity format" in m for m in logs)
    assert adapter.built == 1
    assert adapter.fetched == [4, 5]
    assert report.effective_range == (4, 5)

    raw = json.loads((tmp_path / pipeline.INDEX_FILENAME).read_text(encoding="utf-8"))
    assert raw["schema_version"] == pipeline.INDEX_SCHEMA_VERSION


def test_v1_schema_dict_index_rebuilds_even_with_matching_identity(tmp_path) -> None:
    """A v1 dict (identity present and matching, but old schema) is rebuilt: its
    rows predate the displayed-number meaning of ``index`` and lack ``slug``."""
    (tmp_path / pipeline.INDEX_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "identity": _current_identity(),
                "chapters": _rows([_meta(1), _meta(2)]),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    adapter = RecordingAdapter([_meta(1), _meta(2)])
    logs: list[str] = []

    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.SEPARATE, start=1, end=2),
        adapter=adapter,
        log=logs.append,
    )

    assert any("older than the current" in m for m in logs)
    assert adapter.built == 1
    assert adapter.fetched == [1, 2]
    assert report.effective_range == (1, 2)


def test_mismatch_rebuilds_before_chunked_resume_can_skip_stale_range(tmp_path) -> None:
    stale_identity = {
        "novel_slug": "the-noble-queen",
        "adapter_key": "webnovel_dynamic",
        "source_url": "https://dynamic.webnovel.com/story/28684090500376805",
        "base_url": "https://dynamic.webnovel.com",
    }
    _write_identity_index(tmp_path, identity=stale_identity, metas=[_meta(1), _meta(2)])
    # This is the stale chunk that would have made a stale 1-2 range look complete.
    (tmp_path / "Shadow_Slave_Chapters_1-2.pdf").write_bytes(b"stale other-run pdf")

    fresh_metas = [_meta(10), _meta(11), _meta(12)]
    adapter = RecordingAdapter(fresh_metas)

    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.CHUNKED, start=1, end=99, chunk_size=2),
        adapter=adapter,
        log=lambda _m: None,
    )

    assert adapter.built == 1
    assert adapter.fetched == [10, 11, 12]
    assert report.effective_range == (10, 12)
    assert sorted(p.name for p in report.written) == [
        "Shadow_Slave_Chapters_10-11.pdf",
        "Shadow_Slave_Chapters_12-12.pdf",
    ]
    assert (tmp_path / "Shadow_Slave_Chapters_1-2.pdf").read_bytes() == b"stale other-run pdf"
