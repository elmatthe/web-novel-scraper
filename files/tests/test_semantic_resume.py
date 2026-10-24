"""CP5 synthetic semantic admission/provenance regressions, with no owner data."""
import json
import threading
from dataclasses import replace

import pytest

from webnovel_scraper.run_journal import JOURNAL_FILENAME, JournalError, record_crc, replay_journal
from webnovel_scraper.models import OutputMode
from .test_run_resume import Site, job, run, resume, new_run, store


def rewrite(folder, change):
    path = folder / JOURNAL_FILENAME
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    change(records)
    for record in records:
        record["crc"] = record_crc(record)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def snapshot(folder):
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def test_hash_valid_legacy_wrong_body_cannot_resume_or_trim_tail(tmp_path, store):
    first = new_run(tmp_path, store, Site(1), end=1, formats=("txt",))
    folder = first.output_dir
    replay = replay_journal(folder / JOURNAL_FILENAME)
    publication = next(iter(replay.publishes.values()))
    wrong = folder / publication["path"]
    wrong.write_bytes(b"Chapter 99: Wrong story\n\nA different synthetic chapter.\n")
    from webnovel_scraper.run_journal import file_sha256
    size, digest = file_sha256(wrong)

    def legacy(records):
        records[0].pop("resume_policy", None)
        records[0].pop("extraction_policy", None)
        for r in records:
            if r["type"] == "publish":
                r.update(size=size, sha256=digest)
                r.pop("certificate", None)
    rewrite(folder, legacy)
    path = folder / JOURNAL_FILENAME
    path.write_bytes(path.read_bytes() + b'{"torn":')
    orphan = folder / "TXT" / ".wns-output-abc12345.part"
    orphan.write_bytes(b"preserve")
    before = snapshot(folder)
    with pytest.raises(JournalError, match="[Ss]emantic|[Ll]egacy|certificate"):
        resume(store, folder, Site(1), end=1, formats=("txt",))
    assert snapshot(folder) == before


def test_new_publication_binds_content_event_and_policy(tmp_path, store):
    first = new_run(tmp_path, store, Site(1), end=1, formats=("txt",))
    replay = replay_journal(first.output_dir / JOURNAL_FILENAME)
    publication = next(iter(replay.publishes.values()))
    assert replay.header.resume_policy == "semantic-resume-v1"
    assert replay.header.extraction_policy == "chapter-extraction-identity-v1"
    assert publication["certificate"]["contents"] == [
        {"position": 0, "seq": replay.contents[0]["seq"],
         "sha256": publication["certificate"]["contents"][0]["sha256"]}]


def test_independent_content_edit_invalidates_publication(tmp_path, store):
    first = new_run(tmp_path, store, Site(1), end=1)
    def tamper(records):
        next(r for r in records if r["type"] == "content")["body_sha256"] = "0" * 64
    rewrite(first.output_dir, tamper)
    before = snapshot(first.output_dir)
    with pytest.raises(JournalError):
        resume(store, first.output_dir, Site(1), end=1)
    assert snapshot(first.output_dir) == before


@pytest.mark.parametrize("kind,field,value", [
    ("header", "resume_policy", None),
    ("header", "resume_policy", "semantic-resume-v99"),
    ("header", "extraction_policy", "chapter-extraction-identity-v2"),
    ("content", "extraction_policy", None),
    ("content", "extraction_policy", "obsolete"),
    ("content", "observed_label", "627.10"),
    ("content", "label", "627.1"),
    ("content", "body_sha256", "a" * 64),
    ("content", "page_url", "https://freewebnovel.com/novel/shadow-slave/chapter-99"),
    ("publish", "certificate", None),
    ("publish", "certificate", {"policy": "semantic-resume-v99"}),
    ("publish", "pages", [1]),
    ("publish", "pages", [0, 0]),
    ("publish", "missing", [1]),
    ("publish", "key", "range"),
    ("publish", "key", []),
    ("publish", "sha256", "a" * 64),
    ("publish", "fallback_for", "pdf"),
])
def test_tampered_evidence_refuses_before_any_mutation(tmp_path, store, kind, field, value):
    first = new_run(tmp_path, store, Site(2), end=2, formats=("txt",))
    folder = first.output_dir
    rewrite(folder, lambda records: next(r for r in records if r["type"] == kind).__setitem__(field, value))
    # Admission must not even create a missing lock, nor tidy the torn tail/temp.
    (folder / "run.lock").unlink()
    path = folder / JOURNAL_FILENAME
    path.write_bytes(path.read_bytes() + b'{"torn":')
    orphan = folder / "TXT" / ".wns-output-abc12345.part"
    orphan.write_bytes(b"protected synthetic orphan")
    before = snapshot(tmp_path)
    site = Site(2)
    with pytest.raises(JournalError):
        resume(store, folder, site, end=2, formats=("txt",))
    assert snapshot(tmp_path) == before
    assert site.fetched == [] and site.built == 0


@pytest.mark.parametrize("damage", ["position", "seq", "sha256", "context", "publication", "bool-position"])
def test_publication_reference_cannot_be_reassociated(tmp_path, store, damage):
    first = new_run(tmp_path, store, Site(2), end=2)
    def change(records):
        pub = next(r for r in records if r["type"] == "publish")
        if damage in ("position", "seq", "sha256"):
            pub["certificate"]["contents"][0][damage] = {"position": 1, "seq": 999, "sha256": "b" * 64}[damage]
        elif damage == "bool-position":
            pub["certificate"]["contents"][0]["position"] = False
        else:
            pub["certificate"]["context" if damage == "context" else "sha256"] = "c" * 64
    rewrite(first.output_dir, change)
    before = snapshot(first.output_dir)
    with pytest.raises(JournalError, match="certificate"):
        resume(store, first.output_dir, Site(2), end=2)
    assert snapshot(first.output_dir) == before


@pytest.mark.parametrize("mode", list(OutputMode))
@pytest.mark.parametrize("formats", [("pdf",), ("txt",), ("pdf", "txt")])
def test_certified_cancel_resume_and_complete_skip(tmp_path, store, mode, formats):
    cancel = threading.Event()
    first = run(store, job(tmp_path / "out", end=4, allocate_run=True, mode=mode, formats=formats),
                Site(4, cancel=cancel, stop_at=2), cancel=cancel)
    assert first.cancelled
    second_site = Site(4)
    second = resume(store, first.output_dir, second_site, end=4, mode=mode, formats=formats)
    assert second_site.fetched == [3, 4]
    assert second.accounting["requested_success"] == 4
    third_site = Site(4)
    third = resume(store, first.output_dir, third_site, end=4, mode=mode, formats=formats)
    assert third_site.fetched == [] and third.accounting["requested_success"] == 4
    replay = replay_journal(first.output_dir / JOURNAL_FILENAME)
    from webnovel_scraper.run_journal import file_sha256
    assert replay.last_end == "complete"
    for publication in replay.publishes.values():
        assert file_sha256(first.output_dir / publication["path"]) == (publication["size"], publication["sha256"])
        assert len(publication["certificate"]["contents"]) == len(publication["pages"])


@pytest.mark.parametrize("mode", [OutputMode.CHUNKED, OutputMode.SINGLE])
def test_changed_content_preserves_partial_publications_and_refuses_next_resume(tmp_path, store, mode, monkeypatch):
    # Historical semantic runs without durable bodies still re-fetch, and must
    # retain their original conflict guard rather than accepting changed text.
    from webnovel_scraper import run_output
    monkeypatch.setattr(run_output, "save_body", lambda *_: None)
    first = new_run(tmp_path, store, Site(4, fail={2}), end=4, mode=mode, formats=("txt", "pdf"))
    replay = replay_journal(first.output_dir / JOURNAL_FILENAME)
    before = {r["path"]: (first.output_dir / r["path"]).read_bytes() for r in replay.publishes.values()}
    class ChangedSite(Site):
        def fetch_chapter(self, meta, spec, **kw):
            content = super().fetch_chapter(meta, spec, **kw)
            if meta.index == 1:
                content.paragraphs = ["Contradictory synthetic new body."]
            return content
    resume(store, first.output_dir, ChangedSite(4), end=4, mode=mode, formats=("txt", "pdf"))
    after = replay_journal(first.output_dir / JOURNAL_FILENAME)
    assert len(after.conflicts) == 1
    assert after.contents[0]["body_sha256"] == replay.contents[0]["body_sha256"]
    assert {path: (first.output_dir / path).read_bytes() for path in before} == before
    before_refusal = snapshot(first.output_dir)
    with pytest.raises(JournalError, match="changed accepted canonical content"):
        resume(store, first.output_dir, Site(4), end=4, mode=mode, formats=("txt", "pdf"))
    assert snapshot(first.output_dir) == before_refusal


@pytest.mark.parametrize("mode", [OutputMode.CHUNKED, OutputMode.SINGLE])
def test_partial_groups_reuse_only_same_accepted_bodies(tmp_path, store, mode):
    first = new_run(tmp_path, store, Site(4, fail={2}), end=4, mode=mode, formats=("txt",))
    replay = replay_journal(first.output_dir / JOURNAL_FILENAME)
    assert any(r["missing"] for r in replay.publishes.values())
    site = Site(4)
    second = resume(store, first.output_dir, site, end=4, mode=mode, formats=("txt",))
    assert site.fetched == [2]
    assert second.accounting["requested_success"] == 4
    assert all(not r["missing"] for r in replay_journal(first.output_dir / JOURNAL_FILENAME).publishes.values())


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_modified_certified_output_is_recovered_without_false_skip(tmp_path, store, missing):
    first = new_run(tmp_path, store, Site(2), end=2, formats=("txt",))
    path = first.written[0]
    if missing:
        path.unlink()
    else:
        path.write_bytes(b"Edited synthetic output")
    site = Site(2)
    resume(store, first.output_dir, site, end=2, formats=("txt",))
    assert site.fetched == []
    assert b"Body paragraph of chapter 1" in path.read_bytes()


def test_torn_current_journal_is_repaired_only_after_successful_admission(tmp_path, store):
    first = new_run(tmp_path, store, Site(2), end=2)
    path = first.output_dir / JOURNAL_FILENAME
    path.write_bytes(path.read_bytes() + b'{"seq":')
    site = Site(2)
    resume(store, first.output_dir, site, end=2)
    assert site.fetched == [] and not replay_journal(path).torn_tail


def test_txt_renderer_must_publish_the_accepted_canonical_bytes(tmp_path, store, monkeypatch):
    from webnovel_scraper import pipeline
    def wrong_renderer(fmt, contents, path, title, **kw):
        path.write_bytes(b"Chapter 99: A wrong body with a valid hash\n")
    monkeypatch.setattr(pipeline, "render_output", wrong_renderer)
    with pytest.raises(JournalError, match="TXT publication differs"):
        new_run(tmp_path, store, Site(1), end=1, formats=("txt",))
    folder = tmp_path / "out/shadow-slave-1"
    assert not replay_journal(folder / JOURNAL_FILENAME).publishes


def test_renderer_cannot_change_content_after_acceptance(tmp_path, store, monkeypatch):
    from webnovel_scraper import pipeline
    real = pipeline.render_output
    def mutate_renderer(fmt, contents, path, title, **kw):
        contents[0].paragraphs = ["Renderer substituted synthetic prose."]
        return real(fmt, contents, path, title, **kw)
    monkeypatch.setattr(pipeline, "render_output", mutate_renderer)
    with pytest.raises(JournalError, match="Renderer input changed"):
        new_run(tmp_path, store, Site(1), end=1, formats=("txt",))
    assert not replay_journal(tmp_path / "out/shadow-slave-1" / JOURNAL_FILENAME).publishes


def sm_run(tmp_path, labels, *, resume_folder=None, store=None, pages=None, cancel=None):
    from webnovel_scraper import pipeline
    from webnovel_scraper.models import ScrapeJob
    from .test_verified_manifest_lifecycle import Pages, SPEC, store_at
    from webnovel_scraper.adapters.freewebnovel import FreeWebNovelAdapter
    saved = store or store_at(tmp_path)
    pages = pages or Pages(labels)
    scraper_job = ScrapeJob(novel_slug="supreme-magus", adapter_key="freewebnovel",
                           start=min((int(float(v)) for v in labels.values()), default=1),
                           end=max((int(float(v)) for v in labels.values()), default=2000),
                           delay=0, output_mode=OutputMode.SEPARATE, formats=("txt",),
                           output_dir=resume_folder or tmp_path / "out", use_cache=False,
                           allocate_run=resume_folder is None, resume=resume_folder is not None)
    report = pipeline.run_scrape(scraper_job, adapter=FreeWebNovelAdapter(pages),
                                manifest_store=saved, log=lambda _: None, sleep_fn=lambda _: None,
                                cancel_event=cancel)
    return report, saved, pages


@pytest.mark.parametrize("labels", [
    {1: 627, 2: "627.1", 3: "627.10", 4: 628},
    {1: 1462, 2: 1463, 3: 1462},
    {1: 1923, 2: "1923.1", 3: "1923.2", 4: 1924},
    {1: 1953, 2: 1954, 3: 1955, 4: 1956},
])
def test_supreme_magus_exact_fractions_duplicates_and_new_run_snapshot(tmp_path, labels):
    first, store, _ = sm_run(tmp_path, labels)
    replay = replay_journal(first.output_dir / JOURNAL_FILENAME)
    exact = [c["label"] for c in replay.contents.values()]
    assert sorted(exact) == sorted(map(str, labels.values()))
    assert replay.contents[0]["extraction_policy"] == replay.header.extraction_policy
    again, _, pages = sm_run(tmp_path, {}, store=store, resume_folder=first.output_dir)
    assert pages.calls == [] and len(again.skipped_existing) == len(labels)


@pytest.mark.parametrize("state", ["schema1", "invalidated", "pending", "missing-url", "wrong-label",
                                      "changed-digest", "old-policy", "historical-cache", "conflict"])
def test_supreme_magus_manifest_incompatibility_has_zero_mutations(tmp_path, state):
    from .test_verified_manifest_lifecycle import SOURCE, old_manifest
    from webnovel_scraper.chapter_manifest import ChapterManifest
    first, store, _ = sm_run(tmp_path, {1: 1, 2: 2, 3: 3})
    path = store.runtime_path(SOURCE)
    manifest = ChapterManifest.from_json(json.loads(path.read_text(encoding="utf-8")),
                                         expected_source=SOURCE, expected_layer="runtime")
    if state == "schema1":
        raw = old_manifest("runtime")
    elif state in ("invalidated", "pending"):
        verification = manifest.verification.invalidate("Synthetic invalidation") if state == "invalidated" else manifest.verification.pending_tail(SOURCE)
        raw = replace(manifest, chapters=(), spans=(), verification=verification).to_json()
    elif state == "changed-digest":
        raw = replace(manifest, provenance=replace(manifest.provenance, generated_utc="2026-10-19T12:00:00Z")).to_json()
    else:
        raw = manifest.to_json()
        if state == "missing-url":
            raw["chapters"].pop()
        elif state == "wrong-label":
            raw["chapters"][0]["label"] = "627.10"
        elif state == "old-policy":
            raw["verification"]["policy"] = "sm-com-page-identity-v0"
        elif state == "historical-cache":
            raw["verification"]["pages"][0]["provenance"] = "historical-cache"
        elif state == "conflict":
            raw["verification"]["pages"][0]["status"] = "conflict"
    path.write_text(json.dumps(raw), encoding="utf-8")
    before = snapshot(tmp_path)
    with pytest.raises(JournalError):
        sm_run(tmp_path, {}, store=store, resume_folder=first.output_dir)
    assert snapshot(tmp_path) == before


def test_supreme_magus_cp4_hash_valid_run_requires_new_run(tmp_path):
    first, store, _ = sm_run(tmp_path, {1: 1, 2: 2, 3: 3})
    def old(records):
        records[0].pop("resume_policy")
        records[0].pop("extraction_policy")
        for record in records:
            record.pop("certificate", None)
            record.pop("extraction_policy", None)
            record.pop("observed_label", None)
    rewrite(first.output_dir, old)
    before = snapshot(tmp_path)
    with pytest.raises(JournalError, match="legacy"):
        sm_run(tmp_path, {}, store=store, resume_folder=first.output_dir)
    assert snapshot(tmp_path) == before


def test_fallback_remains_txt_and_resume_still_requires_pdf(tmp_path, store, monkeypatch):
    from webnovel_scraper import pdf_builder
    monkeypatch.setattr(pdf_builder, "_fallback_fonts", lambda: [])
    class GlyphSite(Site):
        def fetch_chapter(self, meta, spec, **kw):
            content = super().fetch_chapter(meta, spec, **kw)
            content.paragraphs = ["Synthetic glyph \U0001f9ec requires fallback."]
            return content
    first = new_run(tmp_path, store, GlyphSite(1), end=1)
    replay = replay_journal(first.output_dir / JOURNAL_FILENAME)
    [publication] = replay.publishes.values()
    assert publication["format"] == "txt" and publication["fallback_for"] == "pdf"
    assert publication["certificate"]
    site = GlyphSite(1)
    second = resume(store, first.output_dir, site, end=1)
    assert site.fetched == [] and second.accounting["requested_success"] == 0
    assert second.accounting["requested_failure"] == second.accounting["fallback_preserved"] == 1


def test_plan_fetch_is_idempotent(tmp_path, store):
    from webnovel_scraper import pipeline, chapter_preflight
    from webnovel_scraper.run_journal import RunJournal, load_selection
    from webnovel_scraper.run_output import RunOutputs
    first = new_run(tmp_path, store, Site(2, fail={2}), end=2)
    replay = replay_journal(first.output_dir / JOURNAL_FILENAME)
    chapters = load_selection(first.output_dir, replay.header)
    outputs = RunOutputs(run_dir=first.output_dir, journal=RunJournal.resume(first.output_dir, replay),
                         metas=[chapter_preflight.meta_from_identity(c) for c in chapters],
                         labels=[c.label.text for c in chapters], mode=OutputMode.SEPARATE,
                         chunk_size=2, formats=("pdf",), title="T", stem="T", replay=replay,
                         render=pipeline.render_output, report=first, log=lambda _: None)
    before = snapshot(first.output_dir)
    assert outputs.plan_fetch() == outputs.plan_fetch()
    assert outputs.resumed_positions == [0]
    assert snapshot(first.output_dir) == before


@pytest.mark.parametrize("novel", ["lord-of-the-mysteries", "lord-of-mysteries-2-circle-of-inevitability"])
@pytest.mark.parametrize("key", ["freewebnovel", "freewebnovel_net"])
def test_supported_com_net_pairings_keep_certified_resume(tmp_path, store, novel, key):
    from webnovel_scraper import pipeline
    from webnovel_scraper.models import ScrapeJob, ChapterMeta
    class Pairing(Site):
        def build_chapter_index(self, spec, **kw):
            return [ChapterMeta(n, spec.url.removesuffix(".html") + f"/chapter-{n}", slug=n) for n in (1, 2)]
    request = ScrapeJob(novel_slug=novel, adapter_key=key, start=1, end=2, delay=0,
                        output_mode=OutputMode.SEPARATE, use_cache=False, formats=("txt",),
                        output_dir=tmp_path / "out", allocate_run=True)
    first = pipeline.run_scrape(request, adapter=Pairing(2), manifest_store=store,
                                log=lambda _: None, sleep_fn=lambda _: None)
    site = Pairing(2)
    second = pipeline.run_scrape(replace(request, allocate_run=False, resume=True, output_dir=first.output_dir),
                                 adapter=site, manifest_store=store, log=lambda _: None, sleep_fn=lambda _: None)
    assert not site.fetched and second.accounting["requested_success"] == 2
    # Changing the host is refused before any run/store mutation.
    other_key = "freewebnovel_net" if key == "freewebnovel" else "freewebnovel"
    before = snapshot(tmp_path)
    with pytest.raises(JournalError, match="different source"):
        pipeline.run_scrape(replace(request, adapter_key=other_key, allocate_run=False, resume=True,
                                    output_dir=first.output_dir), adapter=Pairing(2), manifest_store=store,
                            log=lambda _: None)
    assert snapshot(tmp_path) == before


def test_certified_pdf_provenance_agrees_with_targeted_text_inspection(tmp_path, store):
    from pypdf import PdfReader
    from .test_content_integrity_accounting import audit_run
    first = new_run(tmp_path, store, Site(2), end=2, formats=("pdf", "txt"), mode=OutputMode.SINGLE)
    [pdf] = [p for p in first.written if p.suffix == ".pdf"]
    text = "\n".join(p.extract_text() for p in PdfReader(pdf).pages)
    assert text.index("Chapter 1: Title 1") < text.index("Chapter 2: Title 2")
    assert all(f"Body paragraph of chapter {n}, ordinary prose." in text for n in (1, 2))
    before = snapshot(first.output_dir)
    audit = audit_run.audit(first.output_dir, pdf_text=True)
    assert audit["semantic_resume"]["eligible"] and audit["problems"] == []
    assert all(f["semantic_status"] == "CERTIFIED" for f in audit["files"])
    assert snapshot(first.output_dir) == before
