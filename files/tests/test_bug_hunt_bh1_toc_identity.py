"""BH-1 regression tests: an output folder's old ``chapter_index.json`` is never trusted blindly.

Before v0.3.0 the pipeline wrote ``chapter_index.json`` into the output folder and trusted it on
the next run (0.2.1 added a novel/site identity block so a reused folder could not drive another
novel's run; schema v2 added displayed numbers and URL slugs). That file is now *legacy input*:
03.2a stops writing it and routes it, only when a run touches that folder, through the strict
``ChapterManifestStore.import_legacy_index``, which

* imports only a complete schema-2 file whose identity block equals the current source and whose
  every row validates (so the identity-less v2 dict the old loader accepted is now rejected);
* rejects anything else with a readable warning and lets the run rediscover from the site;
* never modifies, moves, deletes or overwrites the file.

These tests pin that behaviour (they replace the old ones that asserted the unsafe trust path).
"""

from __future__ import annotations

import json

import pytest

from webnovel_scraper import catalog, pipeline
from webnovel_scraper.chapter_store import LEGACY_INDEX_SCHEMA
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
        {"index": m.index, "url": m.url, "title": m.title, "source_id": m.source_id}
        for m in metas
    ]


def _current_identity() -> dict:
    spec = catalog.get_spec(CURRENT_NOVEL, CURRENT_ADAPTER)
    return {
        "novel_slug": spec.novel_slug,
        "adapter_key": spec.adapter_key,
        "source_url": spec.url,
        "base_url": spec.base_url,
    }


STALE_IDENTITY = {
    "novel_slug": "the-noble-queen",
    "adapter_key": "webnovel_dynamic",
    "source_url": "https://dynamic.webnovel.com/story/28684090500376805",
    "base_url": "https://dynamic.webnovel.com",
}


def _write_legacy(tmp_path, payload) -> bytes:
    path = tmp_path / pipeline.INDEX_FILENAME
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path.read_bytes()


def _v2(identity, metas, **extra) -> dict:
    payload = {"schema_version": LEGACY_INDEX_SCHEMA, "chapters": _rows(metas), **extra}
    if identity is not None:
        payload["identity"] = identity
    return payload


def _unchanged(tmp_path, before: bytes) -> bool:
    return (tmp_path / pipeline.INDEX_FILENAME).read_bytes() == before


def test_matching_complete_legacy_index_is_imported_without_rebuilding_toc(tmp_path) -> None:
    before = _write_legacy(
        tmp_path, _v2(_current_identity(), [_meta(1), _meta(2)], complete=True)
    )
    adapter = RecordingAdapter([_meta(10), _meta(11)])
    logs: list[str] = []

    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.SEPARATE, start=1, end=2), adapter=adapter, log=logs.append
    )

    assert adapter.built == 0
    assert adapter.fetched == [1, 2]
    assert report.effective_range == (1, 2)
    assert len(report.written) == 2
    assert any("imported into the saved chapter list" in m for m in logs)
    assert _unchanged(tmp_path, before)


def test_mismatched_identity_is_not_used_and_the_file_is_left_alone(tmp_path) -> None:
    before = _write_legacy(tmp_path, _v2(STALE_IDENTITY, [_meta(50)], complete=True))
    adapter = RecordingAdapter([_meta(1), _meta(2), _meta(3)])
    logs: list[str] = []

    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.SEPARATE, start=1, end=3), adapter=adapter, log=logs.append
    )

    assert any("was not used" in m and "does not match the requested source" in m for m in logs)
    assert adapter.built == 1
    assert adapter.fetched == [1, 2, 3]
    assert report.effective_range == (1, 3)
    assert _unchanged(tmp_path, before)  # never overwritten: this is the old unsafe behaviour


def test_pre_identity_bare_list_is_rejected_and_rediscovered(tmp_path) -> None:
    before = _write_legacy(tmp_path, _rows([_meta(4), _meta(5)]))
    adapter = RecordingAdapter([_meta(4), _meta(5)])
    logs: list[str] = []

    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.SEPARATE, start=4, end=5), adapter=adapter, log=logs.append
    )

    assert any("bare list" in m and "was not used" in m for m in logs)
    assert adapter.built == 1
    assert adapter.fetched == [4, 5]
    assert report.effective_range == (4, 5)
    assert _unchanged(tmp_path, before)


def test_v1_schema_dict_is_rejected_even_with_matching_identity(tmp_path) -> None:
    payload = _v2(_current_identity(), [_meta(1), _meta(2)], complete=True)
    payload["schema_version"] = 1
    before = _write_legacy(tmp_path, payload)
    adapter = RecordingAdapter([_meta(1), _meta(2)])
    logs: list[str] = []

    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.SEPARATE, start=1, end=2), adapter=adapter, log=logs.append
    )

    assert any("schema version 1" in m for m in logs)
    assert adapter.built == 1
    assert adapter.fetched == [1, 2]
    assert report.effective_range == (1, 2)
    assert _unchanged(tmp_path, before)


def test_identity_less_v2_index_is_no_longer_trusted(tmp_path) -> None:
    """The old loader accepted a v2 dict with NO identity block (``_load_index``); that was the
    unsafe trust path. It is now rejected and the TOC is discovered from the site."""
    before = _write_legacy(tmp_path, _v2(None, [_meta(70), _meta(71)], complete=True))
    adapter = RecordingAdapter([_meta(1), _meta(2)])
    logs: list[str] = []

    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.SEPARATE, start=1, end=2), adapter=adapter, log=logs.append
    )

    assert any("no identity block" in m for m in logs)
    assert adapter.built == 1
    assert adapter.fetched == [1, 2]            # not the stale 70/71 rows
    assert report.effective_range == (1, 2)
    assert _unchanged(tmp_path, before)


@pytest.mark.parametrize("flags", [{"complete": False}, {}], ids=["incomplete", "unflagged"])
def test_incomplete_or_unflagged_legacy_checkpoints_are_not_trusted(tmp_path, flags) -> None:
    before = _write_legacy(tmp_path, _v2(_current_identity(), [_meta(1), _meta(2)], **flags))
    adapter = RecordingAdapter([_meta(1), _meta(2)])
    logs: list[str] = []

    pipeline.run_scrape(
        _job(tmp_path, OutputMode.SEPARATE, start=1, end=2), adapter=adapter, log=logs.append
    )

    assert adapter.built == 1
    assert any("was not used" in m for m in logs)
    assert _unchanged(tmp_path, before)


def test_corrupt_legacy_index_is_ignored_with_a_diagnostic(tmp_path) -> None:
    path = tmp_path / pipeline.INDEX_FILENAME
    path.write_bytes(b"{ this is not json")
    adapter = RecordingAdapter([_meta(1), _meta(2)])
    logs: list[str] = []

    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.SEPARATE, start=1, end=2), adapter=adapter, log=logs.append
    )

    assert adapter.built == 1 and len(report.written) == 2
    assert any("was not used" in m for m in logs)
    assert path.read_bytes() == b"{ this is not json"


def test_mismatch_rediscovers_before_chunked_resume_can_skip_stale_range(tmp_path) -> None:
    before = _write_legacy(tmp_path, _v2(STALE_IDENTITY, [_meta(1), _meta(2)], complete=True))
    # This is the stale chunk that would have made a stale 1-2 range look complete.
    (tmp_path / "Shadow_Slave_Chapters_1-2.pdf").write_bytes(b"stale other-run pdf")

    fresh_metas = [_meta(10), _meta(11), _meta(12)]
    adapter = RecordingAdapter(fresh_metas)

    report = pipeline.run_scrape(
        _job(tmp_path, OutputMode.CHUNKED, start=10, end=12, chunk_size=2),
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
    assert _unchanged(tmp_path, before)
