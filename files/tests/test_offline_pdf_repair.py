"""Repair must preserve its source and refuse incomplete or contradictory evidence."""
import json

import pytest

from files.tests.test_full_publication import small, run
from files.tests.test_option_b_runner import Reply, Wire, no_network, page
import full_publication as full
import offline_pdf_repair as repair


def source(cfg):
    result = run(cfg, Wire([Reply(page(i)) for i in (1, 2, 3)]))
    return cfg.root / "outputs" / result["evidence"]


def test_new_repair_uses_existing_pipeline_and_preserves_source(small, tmp_path):
    evidence = source(small)
    before = full.snapshot(small.root)
    result = repair.rebuild(small, evidence, tmp_path / "repair")
    assert result["complete_publication_verified"]
    assert result["attempts"] == 0
    assert result["published_chapters"] == 3
    assert result["file_counts"] == {"txt": 1, "pdf": 1}
    assert result["preservation_failures"] == []
    assert full.snapshot(small.root) == before
    with pytest.raises(ValueError, match="exists"):
        repair.rebuild(small, evidence, tmp_path / "repair")


def test_embedded_critical_outlines_match_local_font(small, tmp_path):
    line = "Glyphs: \u1c0d \u1c45 \u1c47 \uaaa7 \uaac0"
    from webnovel_scraper import pdf_builder
    from webnovel_scraper.models import ChapterContent
    if pdf_builder.unsupported_characters([ChapterContent(1, "T", [line])]):
        pytest.skip("critical local fallback font is not installed")
    original = run(small, Wire([Reply(page(1, line)), Reply(page(2)), Reply(page(3))]))
    evidence = small.root / "outputs" / original["evidence"]
    result = repair.rebuild(small, evidence, tmp_path / "repair")
    assert result["complete_publication_verified"]
    assert {c["character"] for c in result["embedded_glyph_checks"]} == {
        f"U+{ord(char):04X}" for char in repair.CRITICAL_GLYPHS}


@pytest.mark.parametrize("damage", ["missing", "ledger", "body", "journal"])
def test_source_corruption_refused_before_new_output(small, tmp_path, damage):
    evidence = source(small)
    body = evidence / "bodies/00001.json"
    if damage == "missing":
        body.unlink()
    elif damage == "ledger":
        with (evidence / "accepted.jsonl").open("a") as handle:
            handle.write((evidence / "accepted.jsonl").read_text().splitlines()[0] + "\n")
    elif damage == "body":
        row = json.loads(body.read_text())
        row["paragraphs"] = ["A contradictory body, with a matching rewritten acquisition ledger."]
        body.write_text(json.dumps(row))
        rows = [json.loads(line) for line in (evidence / "accepted.jsonl").read_text().splitlines()]
        size, digest = full.file_sha256(body)
        rows[1] = dict(position=1, size=size, sha256=digest)
        (evidence / "accepted.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
    else:
        journal = small.root / "outputs/supreme-magus-1/run-journal.jsonl"
        data = journal.read_bytes()
        journal.write_bytes(data[:-5])
    with pytest.raises((ValueError, RuntimeError), match="body|ledger|journal|torn"):
        repair.rebuild(small, evidence, tmp_path / "repair")
    assert not (tmp_path / "repair").exists()
