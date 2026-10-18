"""The run-folder acceptance checker (``files/audit_run.py``, v0.3.0 07). Offline: runs are made by
the real pipeline against a fake site, then checked, tampered with and checked again."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from webnovel_scraper import pipeline
from webnovel_scraper.chapter_store import ChapterManifestStore
from webnovel_scraper.models import ChapterContent, ChapterMeta, OutputMode, ScrapeJob

_spec = importlib.util.spec_from_file_location("audit_run", Path(__file__).resolve().parents[1] / "audit_run.py")
audit_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_run)


class Site:
    def __init__(self, numbers, fail=()):
        self.numbers, self.fail = list(numbers), set(fail)

    def build_chapter_index(self, spec, **_kw):
        return [ChapterMeta(index=n, url=f"{spec.url}/chapter-{n}", slug=n) for n in self.numbers]

    def fetch_chapter(self, meta, spec, **_kw):
        if meta.index in self.fail:
            raise RuntimeError("HTTP 503")
        return ChapterContent(meta.index, f"Title {meta.index}", [f"Body of {meta.index}.", "Chapter 9: a quoted line"])


def make_run(tmp_path, site, *, end=6, formats=("pdf", "txt"), mode=OutputMode.CHUNKED):
    job = ScrapeJob(novel_slug="shadow-slave", adapter_key="freewebnovel", start=1, end=end, delay=0.0,
                    output_mode=mode, chunk_size=2, use_cache=False, output_dir=tmp_path / "out",
                    allocate_run=True, formats=formats)
    report = pipeline.run_scrape(job, adapter=site, log=lambda _m: None, sleep_fn=lambda _s: None,
                                 manifest_store=ChapterManifestStore(tmp_path / "b", tmp_path / "r"))
    return report.output_dir


def test_a_complete_run_passes_every_check(tmp_path, capsys):
    folder = make_run(tmp_path, Site(range(1, 7)))
    assert audit_run.main([str(folder), "--pdf-text", "--json", str(tmp_path / "audit.json")]) == 0
    out = capsys.readouterr().out
    assert "All checks passed." in out and "Chapters  6 saved" in out
    report = json.loads((tmp_path / "audit.json").read_text(encoding="utf-8"))
    assert [f["state"] for f in report["files"]] == ["ok"] * 6
    assert all(f["headings_match"] for f in report["files"])
    assert "Body of" not in (tmp_path / "audit.json").read_text(encoding="utf-8")   # never chapter text


def test_a_changed_file_and_a_failed_chapter_are_reported(tmp_path, capsys):
    folder = make_run(tmp_path, Site(range(1, 7), fail={4}))
    txt = sorted((folder / "TXT").glob("*.txt"))[0]
    txt.write_text(txt.read_text(encoding="utf-8") + "edited", encoding="utf-8")
    assert audit_run.main([str(folder)]) == 1
    out = capsys.readouterr().out
    assert "does not match its recorded checksum" in out
    assert "chapter 4: failed (HTTP 503)" in out
    report = audit_run.audit(folder)
    states = {f["file"]: f["state"] for f in report["files"] if "file" in f}
    assert states[f"TXT/{txt.name}"] == "changed since it was written"
    assert any(f["state"] == "ok but incomplete" and f["lacks"] == ["4"] for f in report["files"])


def test_gaps_in_the_selection_are_listed(tmp_path):
    folder = make_run(tmp_path, Site([1, 2, 5, 6]), mode=OutputMode.SEPARATE, formats=("txt",))
    report = audit_run.audit(folder)
    assert report["whole_numbers_missing_from_the_selection"] == [3, 4]
    assert report["problems"] == []


@pytest.mark.parametrize("text,expected", [
    ("Chapter 1: A\n\nBody.\n\n\nChapter 2: B\n\nChapter 7: quoted\n", ["1", "2"]),
    ("Chapter 627.2: Side\n\nx\n", ["627.2"]),
])
def test_only_the_heading_at_the_start_of_each_chapter_counts(text, expected):
    assert audit_run._headings_in_txt(text) == expected
