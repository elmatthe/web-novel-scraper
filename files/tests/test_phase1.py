"""Phase 1 tests: models, catalog, registry. Offline — no network, no file I/O."""

from __future__ import annotations

from pathlib import Path

import pytest

from webnovel_scraper import catalog
from webnovel_scraper.adapters.base import BaseAdapter
from webnovel_scraper.models import (
    ChapterContent,
    ChapterMeta,
    OutputMode,
    ScrapeJob,
    SiteSpec,
)
from webnovel_scraper.registry import AdapterDisabledError, get_adapter, get_adapter_for_spec


# ── models ───────────────────────────────────────────────────────────────────
def test_output_mode_values_exist() -> None:
    assert {m.value for m in OutputMode} == {"separate", "chunked", "single"}
    assert OutputMode.SEPARATE and OutputMode.CHUNKED and OutputMode.SINGLE


def test_site_spec_roundtrips() -> None:
    spec = SiteSpec(
        novel_slug="x",
        novel_title="X",
        adapter_key="freewebnovel",
        display_name="Free Web Novel",
        enabled=True,
        url="https://example.com/novel/x",
        book_id="123",
        url_template="https://example.com/novel/x/chapter-{n}",
    )
    assert spec.novel_slug == "x"
    assert spec.enabled is True
    assert spec.book_id == "123"
    assert "freewebnovel" in repr(spec)


def test_chapter_meta_degraded_flag() -> None:
    titled = ChapterMeta(index=1, url="u", title="A Title")
    bare = ChapterMeta(index=2, url="u", title=None)
    blank = ChapterMeta(index=3, url="u", title="   ")
    assert titled.is_degraded is False
    assert bare.is_degraded is True
    assert blank.is_degraded is True
    assert titled.extra == {}  # default factory, independent per-instance


def test_chapter_content_heading_and_raw_text() -> None:
    ch = ChapterContent(index=5, title="The Gate", paragraphs=["one", "two"])
    assert ch.heading == "Chapter 5: The Gate."
    assert ch.raw_text == "Chapter 5: The Gate.\n\none\n\ntwo"
    degraded = ChapterContent(index=6, title="", paragraphs=["body"])
    assert degraded.is_degraded is True
    assert degraded.heading == "Chapter 6."


def test_scrape_job_roundtrips() -> None:
    job = ScrapeJob(
        novel_slug="shadow-slave",
        adapter_key="freewebnovel",
        start=1,
        end=10,
        delay=1.5,
        output_mode=OutputMode.CHUNKED,
        use_cache=True,
        output_dir=Path("out"),
        chunk_size=5,
    )
    assert job.start == 1 and job.end == 10
    assert job.output_mode is OutputMode.CHUNKED
    assert job.chunk_size == 5
    assert job.output_dir == Path("out")


# ── catalog ──────────────────────────────────────────────────────────────────
def test_all_novel_slugs_unique_and_ordered() -> None:
    slugs = catalog.all_novel_slugs()
    assert slugs == [
        "shadow-slave",
        "the-noble-queen",
        "reverend-insanity",
        "renegade-immortal",
        "supreme-magus",
    ]
    assert len(slugs) == len(set(slugs)) == 5


def test_shadow_slave_has_three_sites_one_enabled() -> None:
    rows = catalog.get_adapters_for_novel("shadow-slave")
    assert len(rows) == 3
    enabled = catalog.get_enabled_adapters_for_novel("shadow-slave")
    assert len(enabled) == 1
    assert enabled[0].adapter_key == "freewebnovel"


def test_noble_queen_webnovel_dynamic_book_id() -> None:
    spec = catalog.get_spec("the-noble-queen", "webnovel_dynamic")
    assert spec.enabled is True
    assert spec.book_id == "28684090500376805"


def test_get_spec_raises_keyerror_on_unknown() -> None:
    with pytest.raises(KeyError):
        catalog.get_spec("does-not-exist", "freewebnovel")
    with pytest.raises(KeyError):
        catalog.get_spec("shadow-slave", "no_such_site")


# ── registry ─────────────────────────────────────────────────────────────────
def test_get_adapter_returns_base_adapter_instance() -> None:
    adapter = get_adapter("freewebnovel")
    assert isinstance(adapter, BaseAdapter)
    assert adapter.key == "freewebnovel"
    assert adapter.enabled is True


def test_get_adapter_unknown_raises_keyerror() -> None:
    with pytest.raises(KeyError):
        get_adapter("unknown")


def test_get_adapter_for_spec_refuses_disabled() -> None:
    disabled = catalog.get_spec("shadow-slave", "empire_novel")
    assert disabled.enabled is False
    with pytest.raises(AdapterDisabledError) as exc:
        get_adapter_for_spec(disabled)
    assert "disabled" in str(exc.value).lower()


def test_get_adapter_for_spec_returns_enabled() -> None:
    enabled = catalog.get_spec("shadow-slave", "freewebnovel")
    adapter = get_adapter_for_spec(enabled)
    assert isinstance(adapter, BaseAdapter)
    assert adapter.key == "freewebnovel"


def test_disabled_stub_adapter_raises_from_methods() -> None:
    adapter = get_adapter("telegraph")
    assert adapter.enabled is False
    spec = catalog.get_spec("shadow-slave", "telegraph")
    with pytest.raises(NotImplementedError):
        adapter.build_chapter_index(spec)
    meta = ChapterMeta(index=1, url="u", title="t")
    with pytest.raises(NotImplementedError):
        adapter.fetch_chapter(meta, spec)


# ── base helper ──────────────────────────────────────────────────────────────
def test_safe_filename_strips_illegal_chars() -> None:
    out = BaseAdapter.safe_filename('Chapter 5: A/B "quoted" <tag>|x')
    for ch in ':/\\*?"<>|':
        assert ch not in out
    assert BaseAdapter.safe_filename("   ") == "untitled"
