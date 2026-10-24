"""Synthetic full-publication contract; HTTP is replaced below the budget gate."""
import hashlib
import json
from dataclasses import replace

import pytest

from files.tests.test_option_b_runner import (Clock, Reply, Wire, config, discovery, no_network, page)
import option_b_runner as runner
import full_publication as full


def cfg(tmp_path, **changes):
    return replace(config(tmp_path, mode="full", resume=True, cap=4561,
        start=1, end=4552, formats=("txt", "pdf"), grouping="chunked", chunk_size=100), **changes)


@pytest.mark.parametrize("changes", [dict(cap=4562), dict(cap=4560), dict(start=2),
    dict(end=4553), dict(formats=("pdf",)), dict(formats=("txt",)),
    dict(grouping="single"), dict(chunk_size=99), dict(run="supreme-magus-1"), dict(bound=4563)])
def test_full_preconditions_are_independent_of_sample(tmp_path, changes):
    with pytest.raises(ValueError):
        full.validate_config(cfg(tmp_path, **changes))


def test_sample_limit_stays_in_force(tmp_path):
    with pytest.raises(ValueError, match="sample"):
        runner.inspect(replace(cfg(tmp_path), mode="sample"))


@pytest.fixture
def small(tmp_path, monkeypatch):
    discovery(tmp_path)
    path = tmp_path / "acceptance/runtime/freewebnovel.com/supreme-magus.json"
    store = runner.Workspace(tmp_path / "acceptance").store()
    manifest = store.load_effective(runner.SOURCE, quarantine=False).runtime.manifest
    store.save_runtime(replace(manifest, chapters=tuple(replace(c, title="Synthetic title") for c in manifest.chapters)))
    monkeypatch.setattr(full, "END", 3)
    monkeypatch.setattr(full, "COUNT", 3)
    monkeypatch.setattr(full, "FRACTIONS", ())
    monkeypatch.setattr(full, "MANIFEST_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    return replace(cfg(tmp_path), end=3, cap=3)


def run(cfg, wire):
    clock = Clock()
    return runner.execute(cfg, connection_factory=wire, sleep=clock.sleep, monotonic=clock.monotonic)


def test_full_publishes_both_formats_and_audits_every_body(small):
    wire = Wire([Reply(page(i)) for i in range(1, 4)])
    result = run(small, wire)
    assert result["complete_publication_verified"]
    assert result["attempts"] == len(wire.calls) == 3
    assert result["published_chapters"] == 3
    assert result["file_counts"] == {"txt": 1, "pdf": 1}
    assert result["preservation_failures"] == []
    with pytest.raises(ValueError, match="invocation"):
        run(small, Wire([]))


def test_access_stop_keeps_durable_bodies_and_does_not_retry(small):
    wire = Wire([Reply(page(1)), Reply(status=429), Reply(page(3))])
    result = run(small, wire)
    assert result["attempts"] == len(wire.calls) == 2
    assert not result["complete_publication_verified"]
    assert result["accepted_chapters"] == 1
    assert "429" in result["stop_reason"]
    assert result["published_chapters"] == 0
    assert len(list((small.root / "outputs").glob("full-evidence-*/bodies/*.json"))) == 1


def test_identity_contradiction_stops_immediately(small):
    wire = Wire([Reply(page(77)), Reply(page(2))])
    result = run(small, wire)
    assert result["attempts"] == len(wire.calls) == 1
    assert not result["complete_publication_verified"]
    assert "identity" in result["stop_reason"].lower()


def test_duplicate_bodies_remain_published_and_flagged(small):
    result = run(small, Wire([Reply(page(i, "The same synthetic prose.")) for i in range(1, 4)]))
    assert result["published_chapters"] == 3
    assert len(result["content_concerns"]) >= 2
    assert result["complete_publication_verified"]


def test_missing_format_and_tampered_txt_fail_audit(small):
    result = run(small, Wire([Reply(page(i)) for i in range(1, 4)]))
    folder = small.root / "outputs" / result["run"]
    next(folder.glob("TXT/*.txt")).write_text("tampered", encoding="utf-8")
    next(folder.glob("PDF/*.pdf")).unlink()
    report = full.audit_publication(folder, small.root / "outputs" / result["evidence"], full.preflight(small, runner.Workspace(small.root))[1])
    assert not report["complete_publication_verified"]
    assert report["audit_failures"]


def install_manifest(tmp_path, monkeypatch, labels, titles=None):
    from webnovel_scraper.chapter_manifest import ChapterManifest, Provenance
    from webnovel_scraper.chapter_verification import PageObservation, Verification
    from webnovel_scraper.source_identity import ChapterLabel
    ws = runner.Workspace.open(config(tmp_path), simulation=True)
    stamp = "2026-10-22T00:00:00Z"
    chapters = [runner.SOURCE.chapter_identity(runner.chapter_url_for(runner.SPEC, slug),
        ChapterLabel(label), slug=slug, title=(titles or {}).get(label, "Synthetic title"))
        for slug, label in enumerate(labels, 3)]
    observations = [PageObservation(runner.chapter_url_for(runner.SPEC, n), "extra", "current-page", (), stamp)
                    for n in (1, 2)]
    observations += [PageObservation(c.page_url, "observed", "current-page", (c.label.text,), stamp) for c in chapters]
    end = max(int(c.label.value) for c in chapters)
    manifest = ChapterManifest.create(layer="runtime", source=runner.SOURCE,
        provenance=Provenance(stamp, stamp, "0.3.0", "walk"), chapters=chapters, spans=((1, end),),
        verification=Verification.create(bound=len(labels) + 2, bound_utc=stamp, bound_confirmed=True, pages=observations))
    ws.store().save_runtime(manifest)
    path = ws.path("runtime/freewebnovel.com/supreme-magus.json")
    monkeypatch.setattr(full, "END", end)
    monkeypatch.setattr(full, "COUNT", len(labels))
    monkeypatch.setattr(full, "FRACTIONS", tuple(c.label.text for c in chapters if c.label.value != int(c.label.value)))
    monkeypatch.setattr(full, "MANIFEST_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    return replace(cfg(tmp_path), cap=len(labels), end=end), ws, chapters


def test_entire_4561_identity_selection_and_grouping(tmp_path, monkeypatch):
    labels = []
    for n in range(1, 4553):
        labels.append(str(n))
        labels.extend(f for f in full.FRACTIONS if f.startswith(f"{n}."))
    configured, ws, expected = install_manifest(tmp_path, monkeypatch, labels)
    store, chapters = full.preflight(configured, ws)
    assert [c.to_json() for c in chapters] == [c.to_json() for c in expected]
    groups = full.runner_group_plan(len(chapters))
    assert len(groups) == 46 and len(groups[-1].positions) == 61
    assert [p for g in groups for p in g.positions] == list(range(4561))
    assert tuple(c.label.text for c in chapters if "." in c.label.text) == full.FRACTIONS


def test_fractions_exact_titles_and_untitled_are_published(tmp_path, monkeypatch):
    labels = ["1", "1.2", "2", "3"]
    configured, ws, chapters = install_manifest(tmp_path, monkeypatch, labels,
        {"2": "", "3": "3 Exact original title"})
    replies = [Reply(page("1")), Reply(page("1.2")),
        Reply("<h1>Chapter 2</h1><div id='article'><p>Untitled chapter body.</p></div>"),
        Reply("<h1>Chapter 3: 3 Exact original title</h1><div id='article'><p>Original body.</p></div>")]
    result = run(configured, Wire(replies))
    assert result["complete_publication_verified"]
    assert result["missing_original_titles"] == ["2"]
    text = next((ws.path("outputs") / result["run"]).glob("TXT/*.txt")).read_text(encoding="utf-8")
    assert "Chapter 1.2: Synthetic title" in text
    assert "Chapter 2: Untitled" in text
    assert "Chapter 3: 3 Exact original title" in text


def test_interruption_keeps_completed_group_and_offline_journal_recovery(tmp_path, monkeypatch):
    labels = [str(n) for n in range(1, 102)]
    configured, ws, chapters = install_manifest(tmp_path, monkeypatch, labels)
    wire = Wire([*[Reply(page(n)) for n in range(1, 101)], OSError("synthetic interruption")])
    result = run(configured, wire)
    assert result["attempts"] == 101 and result["published_chapters"] == 100
    assert result["file_counts"] == {"txt": 1, "pdf": 1}
    folder = ws.path("outputs") / result["run"]
    before = {p: p.read_bytes() for p in folder.rglob("*") if p.suffix in (".txt", ".pdf")}
    journal = folder / runner.JOURNAL_FILENAME
    with journal.open("ab") as handle:
        handle.write(b'{"torn":')
    recovered = full.recover_offline(configured, ws, ws.path("outputs") / result["evidence"])
    assert recovered["attempts"] == 101 and recovered["published_chapters"] == 100
    assert not recovered["complete_publication_verified"]
    assert all(p.read_bytes() == data for p, data in before.items())


def test_offline_recovery_builds_saved_bodies_without_another_request(small, monkeypatch):
    original = full.pipeline.RunOutputs._publish
    def interrupt_before_publish(self, group):
        if len(self.contents) == 3:
            raise KeyboardInterrupt()
        return original(self, group)
    monkeypatch.setattr(full.pipeline.RunOutputs, "_publish", interrupt_before_publish)
    result = run(small, Wire([Reply(page(i)) for i in range(1, 4)]))
    assert result["accepted_chapters"] == 3 and result["published_chapters"] == 0
    monkeypatch.setattr(full.pipeline.RunOutputs, "_publish", original)
    recovered = full.recover_offline(small, runner.Workspace(small.root), small.root / "outputs" / result["evidence"])
    assert recovered["complete_publication_verified"] and recovered["attempts"] == 3


def test_pdf_omission_and_unsupported_glyph_are_audit_failures(small, monkeypatch):
    from webnovel_scraper import pdf_builder
    monkeypatch.setattr(pdf_builder, "_fallback_fonts", lambda: [])
    result = run(small, Wire([Reply(page(i, f"Unsupported rune \u16b1 {i}.")) for i in range(1, 4)]))
    assert result["file_counts"] == {"txt": 1, "pdf": 0}
    assert not result["complete_publication_verified"]
    assert len(result["unsupported_pdf_glyphs"]) == 3


def test_pdf_missing_body_is_rejected_before_certificate_and_fails_audit(small, monkeypatch):
    render = full.pipeline.render_output
    def omit_pdf_paragraph(fmt, contents, path, title, **kwargs):
        if fmt == "pdf":
            contents = [replace(c, paragraphs=c.paragraphs[:-1]) for c in contents]
        return render(fmt, contents, path, title, **kwargs)
    monkeypatch.setattr(full.pipeline, "render_output", omit_pdf_paragraph)
    result = run(small, Wire([Reply(page(i)) for i in range(1, 4)]))
    assert result["file_counts"] == {"txt": 1, "pdf": 0}
    assert not result["complete_publication_verified"]
    replay = full.replay_journal(small.root / "outputs" / result["run"] / "run-journal.jsonl")
    assert any(f.get("reason_code") == "RENDER_ERROR" and "canonical text/sections/order mismatch" in f["reason"]
               for f in replay.failures)
    assert result["audit_failures"]


def test_http_intent_is_durable_before_transmission_and_hard_cap(tmp_path):
    ws = runner.Workspace.open(config(tmp_path), simulation=True)
    clock, events = Clock(), []
    wire = Wire([Reply(page(1)), Reply(page(2))])
    ledger = ws.path("outputs/http-test.jsonl")
    def sink(kind, event):
        full.append(ledger, dict(kind=kind, **event))
        events.append((kind, len(wire.calls)))
    rm = runner.RestrictedManager(ws, cap=1,
        allowed={runner.chapter_url_for(runner.SPEC, n): 1 for n in (1, 2)},
        connection_factory=wire, sleep=clock.sleep, monotonic=clock.monotonic, event_sink=sink)
    rm.fetch(runner.chapter_url_for(runner.SPEC, 1))
    assert events[0] == ("attempt", 0) and events[1] == ("result", 1)
    with pytest.raises(runner.Stop):
        rm.fetch(runner.chapter_url_for(runner.SPEC, 2))
    assert len(wire.calls) == 1
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 2


def test_manifest_change_refused_without_requests_or_new_outputs(small):
    path = small.root / "runtime/freewebnovel.com/supreme-magus.json"
    with path.open("ab") as handle:
        handle.write(b"\n")
    wire = Wire([])
    with pytest.raises(ValueError, match="SHA-256"):
        run(small, wire)
    assert not wire.calls and not list((small.root / "outputs").iterdir())
