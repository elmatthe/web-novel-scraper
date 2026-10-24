"""Guarded, developer-only full publication from the owner's certified SM manifest.

Invoked by option_b_runner --mode full. No discovery, recovery network or retries.
Canonical bodies and transport evidence belong to a new sibling evidence directory.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
import uuid

import option_b_runner as runner
from webnovel_scraper import pipeline
from webnovel_scraper.chapter_manifest import parse_manifest_bytes
from webnovel_scraper.chapter_store import ChapterManifestStore
from webnovel_scraper.content_integrity import body_fingerprint, classify_failure
from webnovel_scraper.models import ChapterContent, OutputMode, ScrapeJob
from webnovel_scraper.run_journal import file_sha256, load_selection, replay_journal
from webnovel_scraper.semantic_resume import observed_label, validate_admission
from webnovel_scraper.txt_builder import chapters_to_txt

END, COUNT = 4552, 4561
FRACTIONS = ("627.2", "627.3", "627.4", "627.5", "627.6", "952.2", "952.3", "1923.1", "1923.2")
MANIFEST_SHA256 = "8ca34cbb02f3d987aaac6ce138e6bb1c962293ac6a5a2921b9fa2f5e5ccbca4d"
CERTIFIED_ROOT = runner.REPO / "files/run-temp/sm-full-discovery-20261022-01"
CLAIM = "full-publication-invocation.json"


def now():
    return datetime.now(timezone.utc).isoformat()


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def append(path, value):
    with Path(path).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def validate_config(cfg):
    if (cfg.start != 1 or cfg.end != END or type(cfg.cap) is not int or cfg.cap != COUNT
            or set(cfg.formats) != {"txt", "pdf"} or len(cfg.formats) != 2
            or cfg.grouping != "chunked" or cfg.chunk_size != 100
            or not cfg.resume or cfg.run is not None or cfg.bound is not None or cfg.slugs):
        raise ValueError("full mode requires the exact certified range, cap, both formats, chunked/100, existing workspace and no live resume/discovery")


class ReadOnlyStore(ChapterManifestStore):
    """Production preflight with every persistence/discovery route refused."""
    def writer_lock(self, source):
        self.assert_compatible(source)
        return nullcontext()

    def load_layer(self, source, layer, **kwargs):
        return super().load_layer(source, layer, quarantine=False)

    def save_runtime(self, *_args, **_kwargs):
        raise ValueError("certified manifest is read-only")

    record_discovery = save_runtime
    refresh_runtime = save_runtime
    import_legacy_index = save_runtime


def preflight(cfg, ws):
    validate_config(cfg)
    ws.check()
    path = ws.path("runtime/freewebnovel.com/supreme-magus.json")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != MANIFEST_SHA256:
        raise ValueError("certified manifest SHA-256 differs")
    manifest = parse_manifest_bytes(data, expected_source=runner.SOURCE, expected_layer="runtime")
    chapters = manifest.chapters
    if (not manifest.verification or not manifest.verification.complete_for(runner.SOURCE)
            or not manifest.covers(1, END) or len(chapters) != COUNT
            or [c.label.value for c in chapters] != sorted({c.label.value for c in chapters})
            or [int(c.label.value) for c in chapters if c.label.value == int(c.label.value)] != list(range(1, END + 1))
            or tuple(c.label.text for c in chapters if c.label.value != int(c.label.value)) != FRACTIONS
            or len({c.page_url for c in chapters}) != COUNT):
        raise ValueError("full publication identity/coverage/fraction preconditions failed")
    if [c.slug for c in chapters] != list(range(3, COUNT + 3)) and COUNT == 4561:
        raise ValueError("certified source slug order differs")
    store = ReadOnlyStore(baseline_root=ws.path("baseline"), runtime_root=ws.path("runtime"))
    def refuse(**_kwargs):
        raise ValueError("full mode cannot discover or modify certification")
    selection = runner.resolve_chapters(runner.SPEC, start=1, end=END, store=store,
        discover=refuse, log=lambda _: None)
    if ([runner.SOURCE.chapter_identity(m.url, runner.identity_from_meta(runner.SOURCE, m).label,
            slug=m.slug, source_id=m.source_id, title=m.title).to_json() for m in selection.chapters]
            != [c.to_json() for c in chapters] or selection.toc_gaps or selection.discovered):
        raise ValueError("production selection differs from certified inventory")
    return store, chapters


def snapshot(root):
    return {p.relative_to(root).as_posix(): {"size": size, "sha256": digest}
        for p in sorted(root.rglob("*")) if p.is_file() and not p.name.endswith(".lock")
        for size, digest in [file_sha256(p)]}


def body_content(row):
    return ChapterContent(index=row["index"], label=row["label"], title=row["title"], paragraphs=row["paragraphs"])


def audit_publication(folder, evidence, chapters, *, log=lambda _: None):
    """Compare all bytes, identities, canonical TXT and extracted PDF text, offline."""
    from audit_run import audit
    from pypdf import PdfReader
    failures, concerns = [], []
    base = audit(folder, pdf_text=False)
    for problem in base["problems"]:
        (concerns if problem.startswith("suspected duplicate canonical body:") else failures).append(problem)
    replay = replay_journal(folder / runner.JOURNAL_FILENAME)
    validate_admission(replay)
    selected = load_selection(folder, replay.header)
    if [c.to_json() for c in selected] != [c.to_json() for c in chapters]:
        failures.append("immutable run selection differs from certified manifest")
    if (replay.header.formats not in (("txt", "pdf"), ("pdf", "txt"))
            or replay.header.grouping_mode != "chunked" or replay.header.chunk_size != 100):
        failures.append("both requested formats and chunked/100 are required")
    bodies, hashes = {}, []
    missing_titles = [c.label.text for c in chapters if not c.title]
    body_records = {}
    ledger = evidence / "accepted.jsonl"
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record["position"] in body_records:
                failures.append("duplicate accepted-body evidence position")
            body_records[record["position"]] = record
    for p, chapter in enumerate(chapters):
        path = evidence / "bodies" / f"{p:05d}.json"
        if not path.exists():
            continue
        row = json.loads(path.read_text(encoding="utf-8"))
        size, digest = file_sha256(path)
        if body_records.get(p) != dict(position=p, size=size, sha256=digest):
            failures.append(f"chapter {chapter.label.text}: durable body hash not recorded/intact")
        content = body_content(row)
        fingerprint = body_fingerprint(content.paragraphs)
        accepted = replay.contents.get(p, {})
        if (row["position"] != p or row["url"] != chapter.page_url or observed_label(content) != chapter.label.text
                or content.title != (chapter.title or "")
                or (fingerprint != (accepted.get("body_sha256"), accepted.get("body_bytes"), accepted.get("paragraph_count")))):
            failures.append(f"chapter {chapter.label.text}: canonical body/identity differs from journal")
        bodies[p] = content
        hashes.append(dict(path=str(path.relative_to(evidence)), size=size, sha256=digest))
        if not chapter.title:
            if content.title or "Untitled" not in content.heading:
                failures.append(f"chapter {chapter.label.text}: missing original title not identified")
    checks = []
    for group in runner_group_plan(len(chapters)):
        contents = [bodies[p] for p in group.positions if p in bodies]
        for fmt in ("txt", "pdf"):
            record = replay.publishes.get((group.key, fmt))
            if not record:
                continue  # base audit accounts for every missing group/format
            path = folder / record["path"]
            if not path.is_file() or len(contents) != len(group.positions):
                continue
            try:
                if fmt == "txt":
                    match = path.read_bytes() == chapters_to_txt(contents).encode("utf-8")
                    pages = None
                else:
                    reader = PdfReader(str(path), strict=True)
                    texts = [page.extract_text() or "" for page in reader.pages]
                    expected = "".join("".join((c.heading, *c.paragraphs)) for c in contents)
                    # PDF line wrapping and justification can change whitespace only.
                    match = "".join("".join(texts).split()) == "".join(expected.split())
                    pages = len(texts)
                    if not texts or any(not text.strip() for text in texts):
                        failures.append(f"{record['path']}: empty/unreadable PDF page")
                checks.append(dict(group=group.key, format=fmt, canonical_text_match=match, pdf_pages=pages))
                log(f"Audit {group.key} {fmt.upper()}: canonical text match={match}, PDF pages={pages}")
                if not match:
                    failures.append(f"{record['path']}: canonical text/sections/order mismatch")
            except Exception as exc:
                failures.append(f"{record['path']}: unreadable {type(exc).__name__}: {exc}")
    attempts = []
    transport = evidence / "http.jsonl"
    if transport.exists():
        attempts = [json.loads(line) for line in transport.read_text(encoding="utf-8").splitlines()
                    if json.loads(line)["kind"] == "attempt"]
    urls = [row["url"] for row in attempts]
    if len(urls) > COUNT or len(set(urls)) != len(urls) or set(urls) - {c.page_url for c in chapters}:
        failures.append("HTTP attempt cap/allowlist/one-attempt constraint violated")
    if any(b["monotonic"] - a["monotonic"] < 3 for a, b in zip(attempts, attempts[1:])):
        failures.append("HTTP request pacing below 3 seconds")
    published = base["chapter_counts"].get("saved", 0)
    return dict(run=folder.name, evidence=evidence.name, attempts=len(attempts),
        selected=len(chapters), accepted_chapters=len(bodies), published_chapters=published,
        file_counts={fmt: sum(r["format"] == fmt and (folder / r["path"]).is_file()
                              for r in replay.publishes.values()) for fmt in ("txt", "pdf")},
        audit_failures=failures, content_concerns=concerns, missing_original_titles=missing_titles,
        known_discovery_content_concerns={"duplicate_body_pairs": [["1913", "1915"], ["1914", "1916"]],
            "missing_original_titles": ["1921", "1922"], "policy": "retain every identity and body; no invented correction"},
        unsupported_pdf_glyphs=[f for f in replay.failures if f.get("reason_code") == "PDF_GLYPH_UNSUPPORTED"],
        complete_publication_verified=not failures and published == len(chapters)
            and len(checks) == 2 * len(runner_group_plan(len(chapters))),
        canonical_checks=checks, body_artifact_hashes=hashes, publication_audit=base,
        artifact_hashes=snapshot(folder), last_journal_end=replay.last_end)


def runner_group_plan(count):
    from webnovel_scraper.run_output import plan_groups
    return plan_groups(count, OutputMode.CHUNKED, 100)


def publish(cfg, ws, *, connection_factory, sleep, monotonic):
    store, chapters = preflight(cfg, ws)
    if connection_factory is None and ws.root.resolve() != CERTIFIED_ROOT.resolve():
        raise ValueError("live full mode requires the existing certified discovery workspace")
    if ws.path(f"outputs/{CLAIM}").exists():
        raise ValueError("full-publication invocation already claimed; no automatic live restart")
    before = snapshot(ws.root)
    evidence = ws.path(f"outputs/full-evidence-{uuid.uuid4().hex}")
    evidence.mkdir()
    (evidence / "bodies").mkdir()
    write_new(evidence / "preservation-before.json", before)
    cancel = threading.Event()
    report = pipeline.RunReport(output_dir=ws.path("outputs"), formats=cfg.formats)
    def log(message):
        append(evidence / "progress.jsonl", dict(utc=now(), message=message))
        print(message, flush=True)
    job = ScrapeJob(novel_slug=runner.SPEC.novel_slug, adapter_key=runner.SPEC.adapter_key,
        start=1, end=END, output_mode=OutputMode.CHUNKED, chunk_size=100,
        formats=cfg.formats, output_dir=ws.path("outputs"), allocate_run=True,
        use_cache=False, use_browser=False, max_retries=0, delay=cfg.interval)
    def refuse(**_kwargs):
        raise ValueError("full mode cannot discover")
    run = pipeline._prepare_run(job, runner.SPEC, store, refuse, report, log, cancel, None, None,
                               preserve_manifest_metadata=True)
    if run is None:
        raise ValueError("full selection unexpectedly empty")
    rm, stop_reason = None, None
    try:
        write_new(ws.path(f"outputs/{CLAIM}"), dict(utc=now(), run=run.run_dir.name,
            evidence=evidence.name, cap=COUNT, manifest_sha256=MANIFEST_SHA256,
            simulation=connection_factory is not None))
        def sink(kind, event):
            append(evidence / "http.jsonl", dict(kind=kind, utc=now(), monotonic=monotonic(), **event))
        rm = runner.RestrictedManager(ws, cap=COUNT, allowed={c.page_url: 1 for c in chapters},
            connection_factory=connection_factory, interval=cfg.interval, timeout=cfg.timeout,
            sleep=sleep, monotonic=monotonic, event_sink=sink)
        adapter = runner.SampleAdapter(request_manager=rm, log=log)
        for p, meta in enumerate(run.fetch_list):
            if cancel.is_set():
                raise runner.Stop("cancelled")
            run.outputs.attempt(meta)
            try:
                content = adapter.fetch_chapter(meta, runner.SPEC)
                # Preserve certified title spelling, including metadata the ordinary
                # cleaner may trim (chapter 2475); never invent an original title.
                _number, source_title, _shown = runner._observed_heading(runner.BeautifulSoup(rm.last_body, "html.parser"))
                if (source_title or "") != (chapters[p].title or ""):
                    raise ValueError("source identity/title contradicts certified manifest")
                content = replace(content, title=chapters[p].title or "")
            except Exception as exc:
                run.outputs.fail(meta, classify_failure(exc))
                raise runner.Stop(f"source identity/access/extraction stop at {meta.url}: {exc}") from exc
            if observed_label(content) != chapters[p].label.text:
                raise runner.Stop("source identity contradiction")
            path = evidence / "bodies" / f"{p:05d}.json"
            write_new(path, dict(position=p, url=meta.url, **asdict(content)))
            size, digest = file_sha256(path)
            append(evidence / "accepted.jsonl", dict(position=p, size=size, sha256=digest))
            run.outputs.add(meta, content)
            run.outputs.publish_ready()
            if (p + 1) % 10 == 0 or p + 1 == COUNT:
                log(f"Full publication: {p + 1}/{COUNT} accepted; {rm.attempts} HTTP attempts")
        pipeline._finish_run(run, report, log)
    except BaseException as exc:
        stop_reason = f"{type(exc).__name__}: {exc}"
        report.cancelled = True
        # Completed groups already exist; incomplete groups and canonical bodies stay durable.
        pipeline._abort_run(run, log)
        log(f"STOP: {stop_reason}; no second live invocation will be started")
    finally:
        if rm is not None:
            rm.close()
    result = audit_publication(run.run_dir, evidence, chapters, log=log)
    preservation_failures = [relative for relative, expected in before.items()
        if not (ws.root / relative).is_file()
        or dict(zip(("size", "sha256"), file_sha256(ws.root / relative))) != expected]
    result.update(stop_reason=stop_reason, preservation_failures=preservation_failures,
        manifest_sha256=MANIFEST_SHA256, output_location=str(run.run_dir), audited_utc=now())
    if preservation_failures or stop_reason:
        result["complete_publication_verified"] = False
    write_new(evidence / "publication-audit.json", result)
    print(json.dumps({k: result[k] for k in ("attempts", "accepted_chapters", "published_chapters", "file_counts",
        "stop_reason", "audit_failures", "content_concerns", "complete_publication_verified")}, indent=2), flush=True)
    return result


def recover_offline(cfg, ws, evidence):
    """Explicit local recovery only. There is deliberately no live resume path.

    Production admission verifies the journal before repairing a torn tail. Durable
    bodies must match their acquisition ledger and any prior accepted fingerprints.
    Completed groups are preserved; incomplete groups remain unclaimed.
    """
    store, chapters = preflight(cfg, ws)
    runner._no_links(evidence)
    claim = json.loads(ws.path(f"outputs/{CLAIM}").read_text(encoding="utf-8"))
    if evidence.parent != ws.path("outputs") or claim["evidence"] != evidence.name:
        raise ValueError("offline recovery evidence does not belong to this invocation")
    folder = ws.path(f"outputs/{claim['run']}")
    cancel = threading.Event()
    report = pipeline.RunReport(output_dir=folder, formats=cfg.formats)
    job = ScrapeJob(novel_slug=runner.SPEC.novel_slug, adapter_key=runner.SPEC.adapter_key,
        start=1, end=END, output_mode=OutputMode.CHUNKED, chunk_size=100,
        formats=cfg.formats, output_dir=folder, resume=True, use_cache=False, use_browser=False, delay=cfg.interval)
    def refuse(**_kwargs):
        raise ValueError("offline recovery cannot discover")
    run = pipeline._prepare_run(job, runner.SPEC, store, refuse, report, lambda _: None, cancel, None, None)
    try:
        records = {r["position"]: r for line in (evidence / "accepted.jsonl").read_text(encoding="utf-8").splitlines()
                   for r in [json.loads(line)]}
        by_url = {c.page_url: p for p, c in enumerate(chapters)}
        for meta in run.fetch_list:
            p = by_url[meta.url]
            record = records.get(p)
            if record is None:
                continue
            path = evidence / "bodies" / f"{p:05d}.json"
            size, digest = file_sha256(path)
            if record != dict(position=p, size=size, sha256=digest):
                raise ValueError("offline recovery body ledger mismatch")
            row = json.loads(path.read_text(encoding="utf-8"))
            if row["position"] != p or row["url"] != meta.url:
                raise ValueError("offline recovery identity mismatch")
            content = body_content(row)
            if observed_label(content) != chapters[p].label.text or content.title != (chapters[p].title or ""):
                raise ValueError("offline recovery content identity/title mismatch")
            run.outputs.attempt(meta)
            run.outputs.add(meta, content)
        run.outputs.publish_ready()
        report.cancelled = True  # Never publish partial groups as complete.
        pipeline._finish_run(run, report, lambda _: None)
    except BaseException:
        pipeline._abort_run(run, lambda _: None)
        raise
    result = audit_publication(folder, evidence, chapters)
    write_new(evidence / f"offline-recovery-audit-{uuid.uuid4().hex}.json", result)
    return result
