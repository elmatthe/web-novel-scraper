"""CP4 compact evidence, journal compatibility and per-page accounting contracts."""
from dataclasses import replace
import gc
import json
from pathlib import Path

import pytest

from webnovel_scraper.content_integrity import BodyFingerprints, BODY_POLICY, FailureInfo, body_fingerprint
from webnovel_scraper.run_accounting import page_accounting
from webnovel_scraper.run_journal import JournalError, RunJournal, record_crc, replay_journal
from webnovel_scraper.run_output import RunOutputs
from webnovel_scraper.models import OutputMode, ChapterContent
from .test_content_integrity_accounting import Site, run, page


def test_fingerprint_excludes_heading_and_preserves_paragraph_boundaries_and_unicode():
    body = ["Exact original 漢字.", "Boom!"]
    assert body_fingerprint(body) == body_fingerprint(list(body))
    assert body_fingerprint(["a", "bc"])[0] != body_fingerprint(["ab", "c"])[0]
    assert body_fingerprint(["é"])[0] != body_fingerprint(["e\u0301"])[0]
    assert body == ["Exact original 漢字.", "Boom!"]


def test_tracker_does_not_keep_prior_story_text_and_updates_changed_position():
    tracker = BodyFingerprints()
    for position in range(5000):
        tracker.observe(position, [f"Synthetic never-retained story prose {position}: " + "X" * 4000])
    gc.collect()
    assert len(tracker.by_position) == len(tracker.by_hash) == 5000
    assert all(len(key) == 64 for key in tracker.by_hash)
    assert all(type(p) is int for group in tracker.by_hash.values() for p in group)
    assert "Synthetic" not in repr(vars(tracker))
    one = tracker.observe(0, ["shared"])
    two = tracker.observe(1, ["shared"])
    assert one["duplicate_of"] is None and two["duplicate_of"] == 0
    tracker.observe(0, ["changed again"])
    assert tracker.observe(2, ["shared"])["duplicate_of"] == 1


def test_duplicate_source_url_is_refused_before_ambiguous_position_mapping(tmp_path):
    from webnovel_scraper.pipeline import RunReport
    from webnovel_scraper.models import ChapterMeta
    meta = ChapterMeta(1, "https://freewebnovel.com/novel/demo/chapter-1")
    with pytest.raises(ValueError, match="DUPLICATE SOURCE URL.*positions"):
        RunOutputs(run_dir=tmp_path, journal=None, metas=[meta, replace(meta)], labels=["1", "1"],
                   mode=OutputMode.SEPARATE, chunk_size=1, formats=("txt",), title="T", stem="T",
                   render=lambda *a, **k: None, report=RunReport(tmp_path), log=lambda _: None)


def test_failure_and_finalization_callbacks_are_idempotent(tmp_path):
    report, replay, _ = run(tmp_path, Site([1, 2], {1: page("Chapter 9: Wrong")}), stop_after=1)
    from webnovel_scraper.run_journal import load_selection
    from webnovel_scraper.chapter_preflight import meta_from_identity
    identities = load_selection(report.output_dir, replay.header)
    journal = RunJournal.resume(report.output_dir, replay)
    outputs = RunOutputs(run_dir=report.output_dir, journal=journal,
                         metas=[meta_from_identity(c) for c in identities], labels=[c.label.text for c in identities],
                         mode=OutputMode.SEPARATE, chunk_size=1, formats=("txt",), title="T", stem="T",
                         render=lambda *a, **k: pytest.fail("Stop must not render"), report=report, log=lambda _: None)
    info = FailureInfo("IDENTITY_MISMATCH", "synthetic mismatch", ("9",))
    outputs.fail(outputs.metas[0], info)
    outputs.fail(outputs.metas[0], info)
    outputs.finish(cancelled=True)
    outputs.finish(cancelled=True)
    after = replay_journal(report.output_dir / "run-journal.jsonl")
    assert len(after.failures) == len(replay.failures) + 1
    assert len(after.attempts) == 1


@pytest.mark.parametrize("field,value", [
    ("position", True), ("position", 999), ("label", "1.10"),
    ("page_url", "https://freewebnovel.net/novel/shadow-slave/chapter-1"),
    ("reason_code", []), ("reason_code", "SOURCE_ABSENT"),
    ("stage", "absence"), ("format", "exe"), ("http_status", "404"),
    ("observed_labels", ["not a chapter"]),
])
def test_typed_failure_tamper_is_rejected_even_with_recomputed_crc(tmp_path, field, value):
    report, _replay, _ = run(tmp_path, Site([1], {1: page("Chapter 9: Wrong")}))
    path = report.output_dir / "run-journal.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    record = next(r for r in records if r["type"] == "failure")
    record[field] = value
    record["crc"] = record_crc(record)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    with pytest.raises(JournalError):
        replay_journal(path)


@pytest.mark.parametrize("field,value", [
    ("event_version", True), ("event_version", 2), ("body_policy", "unversioned"),
    ("body_sha256", "bad"), ("paragraph_count", -1), ("body_bytes", 0.0),
    ("duplicate_of", 0), ("duplicate_of", 99),
])
def test_content_event_shape_and_policy_are_strict(tmp_path, field, value):
    report, _replay, _ = run(tmp_path, Site([1]))
    path = report.output_dir / "run-journal.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    record = next(r for r in records if r["type"] == "content")
    record[field] = value
    record["crc"] = record_crc(record)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    with pytest.raises(JournalError):
        replay_journal(path)


def test_old_journal_without_page_events_or_policy_remains_readable(tmp_path):
    report, _, _ = run(tmp_path, Site([1, 2], {2: page("Chapter 9: Wrong")}))
    path = report.output_dir / "run-journal.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    records = [r for r in records if r["type"] not in ("attempt", "content")]
    records[0].pop("outcome_policy", None)
    for seq, record in enumerate(records):
        record["seq"] = seq
        if record["type"] == "failure":
            for name in ("reason_code", "stage", "format", "observed_labels", "http_status"):
                record.pop(name, None)
        record["crc"] = record_crc(record)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    replay = replay_journal(path)
    assert replay.header.outcome_policy is None and not replay.attempts and not replay.contents
    assert len(replay.failures) == len(replay.publishes) == 1


def test_forensic_run_one_equivalent_partitions_pages_without_counting_fallback_twice():
    count = 4555
    attempts = {p: {"position": p} for p in range(2563)}
    contents = {p: {"seq": p * 3 + 1} for p in range(2279)}
    records = {}
    for p in range(2279):
        records[(f"c{p}", "pdf")] = dict(format="pdf", pages=[p], seq=p * 3 + 2)
    failures = [dict(position=p, reason="wrong page", reason_code="IDENTITY_MISMATCH", stage="fetch", seq=9000 + p)
                for p in range(2279, 2561)]
    for p in (2561, 2562):
        contents[p] = dict(seq=15000 + p)
        failures.append(dict(position=p, reason="unsupported glyph", reason_code="PDF_GLYPH_UNSUPPORTED",
                             stage="render", format="pdf", seq=20000 + p))
        records[(f"c{p}", "txt")] = dict(format="txt", pages=[p], seq=30000 + p, fallback_for="pdf")
    rows, totals = page_accounting(count, ("pdf",), attempts, contents, failures, records)
    assert totals["selected"] == 4555
    assert totals["attempted"] == totals["processed"] == 2563
    assert totals["requested_success"] == 2279 and totals["requested_failure"] == 284
    assert totals["not_attempted"] == 1992 and totals["fallback_preserved"] == 2
    assert totals["actual_artifacts"] == {"pdf": 2279, "txt": 2}
    assert totals["requested_success"] + totals["requested_failure"] + totals["not_attempted"] == count
    assert rows[2561]["requested_format_outcomes"] == {"pdf": "failure"}
    assert rows[2561]["fallback_formats"] == ["txt"]


def test_render_exception_is_per_format_and_does_not_abort_other_requested_output(tmp_path, monkeypatch):
    from webnovel_scraper import pipeline
    real = pipeline.render_output
    def renderer(fmt, *args, **kwargs):
        if fmt == "pdf":
            raise ValueError("synthetic renderer failure")
        return real(fmt, *args, **kwargs)
    monkeypatch.setattr(pipeline, "render_output", renderer)
    report, replay, _ = run(tmp_path, Site([1]), formats=("pdf", "txt"))
    assert len(report.written) == 1 and report.written[0].suffix == ".txt"
    assert replay.failures[0]["reason_code"] == "RENDER_ERROR"
    assert replay.failures[0]["stage"] == "render" and replay.failures[0]["format"] == "pdf"
    assert report.accounting["requested_failure"] == 1
    assert report.accounting["fallback_preserved"] == 0  # TXT was requested in its own right


@pytest.mark.parametrize("html,expected", [
    ("<html><title>Just a moment...</title></html>", "ACCESS_BLOCKED"),
    ("<html><h1>Error 1015: You are being rate limited</h1></html>", "RATE_LIMITED"),
    (page(body="The hero mentioned rate limits and access denied messages."), None),
])
def test_access_documents_are_not_empty_story_or_source_absence(tmp_path, html, expected):
    report, replay, _ = run(tmp_path, Site([1], {1: html}))
    if expected:
        assert replay.failures[0]["reason_code"] == expected
        assert not report.extraction_failed and not report.absent_skipped
    else:
        assert not replay.failures and len(report.written) == 1
