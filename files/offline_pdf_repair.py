"""Developer-only, network-denied republication of verified durable chapter bodies.

The source run is read-only. A fresh run uses the ordinary pipeline, journal,
publication certificates and canonical-text audit; no resume checks are bypassed.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
import struct
import threading
from unittest.mock import patch

import full_publication as full
import option_b_runner as runner
from webnovel_scraper import pdf_builder, pipeline
from webnovel_scraper.content_integrity import body_fingerprint
from webnovel_scraper.models import OutputMode, ScrapeJob
from webnovel_scraper.run_journal import file_sha256, load_selection, replay_journal
from webnovel_scraper.semantic_resume import observed_label, validate_admission
from webnovel_scraper.txt_builder import chapters_to_txt

CRITICAL_GLYPHS = set("\u1c0d\u1c45\u1c47\uaaa7\uaac0")


def deny_network(*_args, **_kwargs):
    raise RuntimeError("offline PDF repair prohibits all network access")


def verified_source(cfg, evidence):
    ws = runner.Workspace(cfg.root)
    store, chapters = full.preflight(cfg, ws)
    runner._no_links(evidence)
    claim = json.loads(ws.path(f"outputs/{full.CLAIM}").read_text(encoding="utf-8"))
    if evidence != ws.path(f"outputs/{claim['evidence']}"):
        raise ValueError("body evidence does not belong to the source invocation")
    folder = ws.path(f"outputs/{claim['run']}")
    replay = replay_journal(folder / runner.JOURNAL_FILENAME)
    validate_admission(replay)
    if replay.torn_tail:
        raise ValueError("source journal has a torn tail; read-only repair refused")
    effective_digest = pipeline._manifest_digest(store.load_effective(runner.SOURCE, quarantine=False).effective)
    if (replay.header.manifest_sha256 != effective_digest
            or [c.to_json() for c in load_selection(folder, replay.header)]
            != [c.to_json() for c in chapters]):
        raise ValueError("source journal selection/manifest mismatch")
    records = [json.loads(line) for line in (evidence / "accepted.jsonl").read_text().splitlines()]
    ledger = {r["position"]: r for r in records}
    if len(records) != len(ledger) or set(ledger) != set(range(len(chapters))):
        raise ValueError("body ledger is incomplete or contains duplicate positions")
    bodies = []
    for p, chapter in enumerate(chapters):
        path = evidence / "bodies" / f"{p:05d}.json"
        if not path.is_file():
            raise ValueError(f"durable body missing at position {p}")
        size, digest = file_sha256(path)
        if ledger[p] != dict(position=p, size=size, sha256=digest):
            raise ValueError(f"body acquisition ledger mismatch at position {p}")
        row = json.loads(path.read_text(encoding="utf-8"))
        content = full.body_content(row)
        accepted = replay.contents.get(p, {})
        if (row["position"] != p or row["url"] != chapter.page_url
                or observed_label(content) != chapter.label.text or content.title != (chapter.title or "")
                or body_fingerprint(content.paragraphs) != (accepted.get("body_sha256"),
                    accepted.get("body_bytes"), accepted.get("paragraph_count"))):
            raise ValueError(f"canonical body/identity differs from journal at position {p}")
        bodies.append(content)
    # Validate all historical publication bytes and every accepted TXT section.
    for record in replay.publishes.values():
        if file_sha256(folder / record["path"]) != (record["size"], record["sha256"]):
            raise ValueError(f"historical output hash differs: {record['path']}")
    for group in full.runner_group_plan(len(chapters)):
        record = replay.publishes.get((group.key, "txt"))
        expected = chapters_to_txt([bodies[p] for p in group.positions]).encode("utf-8")
        if not record or (folder / record["path"]).read_bytes() != expected:
            raise ValueError(f"validated TXT differs from canonical body: {group.key}")
    pdf_builder.require_glyph_coverage(bodies)
    return store, chapters, bodies, folder


def embedded_glyph_audit(folder, chapters, bodies):
    """Check the actual embedded outlines and Unicode maps, not just extraction.

    ReportLab subsets simple glyphs verbatim. Match the five formerly missing
    glyphs against the selected local font's outlines, rejecting .notdef/boxes.
    """
    from pypdf import PdfReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFontFile

    checks = []
    replay = replay_journal(folder / runner.JOURNAL_FILENAME)
    for group in full.runner_group_plan(len(chapters)):
        needed = CRITICAL_GLYPHS.intersection("".join(
            "".join((bodies[p].heading, *bodies[p].paragraphs)) for p in group.positions))
        if not needed:
            continue
        path = folder / replay.publishes[group.key, "pdf"]["path"]
        reader = PdfReader(path, strict=True)
        seen_fonts, found = set(), set()
        for page in reader.pages:
            for ref in page["/Resources"]["/Font"].values():
                font = ref.get_object()
                key = str(font.get("/BaseFont"))
                if key in seen_fonts or "/ToUnicode" not in font:
                    continue
                seen_fonts.add(key)
                cmap = font["/ToUnicode"].get_data().decode("ascii")
                for code, target in re.findall(r"<([0-9A-Fa-f]{2})>\s+<([0-9A-Fa-f]{4,})>", cmap):
                    char = bytes.fromhex(target).decode("utf-16-be")
                    if char not in needed:
                        continue
                    embedded = TTFontFile(BytesIO(font["/FontDescriptor"]["/FontFile2"].get_data()))
                    # Read ReportLab's generated format-6 cmap directly: its font
                    # reader in 4.5.1 accidentally inverts that format's mapping.
                    subset_cmap = embedded.get_table("cmap")
                    offset = struct.unpack_from(">I", subset_cmap, 8)[0]
                    fmt, _size, _lang, first, count = struct.unpack_from(">5H", subset_cmap, offset)
                    byte_code = int(code, 16)
                    if fmt != 6 or not first <= byte_code < first + count:
                        raise ValueError("unexpected embedded subset cmap")
                    gid = struct.unpack_from(">H", subset_cmap, offset + 10 + 2 * (byte_code - first))[0]
                    local_name = next(name for name, coverage in pdf_builder._fallback_fonts()
                                      if ord(char) in coverage)
                    local = pdfmetrics.getFont(local_name).face
                    original_gid = local.charToGlyph[ord(char)]
                    outline = embedded.get_table("glyf")[embedded.glyphPos[gid]:embedded.glyphPos[gid + 1]]
                    original = local.get_table("glyf")[local.glyphPos[original_gid]:local.glyphPos[original_gid + 1]]
                    # Subsetting aligns each outline to a four-byte boundary.
                    if (not gid or not original or outline[:len(original)] != original
                            or len(outline) - len(original) not in range(4)
                            or any(outline[len(original):])):
                        raise ValueError(f"embedded U+{ord(char):04X} outline differs or is missing")
                    checks.append(dict(group=group.key, character=f"U+{ord(char):04X}",
                        font=key, local_font_sha256=file_sha256(Path(local.filename))[1],
                        embedding_fsType=struct.unpack_from(">H", local.get_table("OS/2"), 8)[0],
                        embedded_outline_matches_local=True))
                    found.add(char)
        if found != needed:
            raise ValueError(f"PDF lacks verified glyphs: {needed - found!r}")
    return checks


def rebuild(cfg, source_evidence, destination, *, log=lambda _: None):
    """Create exactly one fresh contained repair; an existing destination is refused."""
    source_evidence = Path(source_evidence)
    destination = runner._checked_root(destination)
    if destination.exists():
        raise ValueError("repair destination already exists; historical artifacts cannot be overwritten")
    if destination.is_relative_to(cfg.root) or cfg.root.is_relative_to(destination):
        raise ValueError("repair destination overlaps source workspace")
    with ExitStack() as offline:
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "socket.getaddrinfo",
                       "socket.create_connection", "http.client.HTTPConnection.request",
                       "webnovel_scraper.request_manager.RequestManager.fetch"):
            offline.enter_context(patch(target, deny_network))
        store, chapters, bodies, source_folder = verified_source(cfg, source_evidence)
        before = full.snapshot(cfg.root)
        destination.mkdir(parents=True, exist_ok=False)
        evidence = destination / "evidence"
        (evidence / "bodies").mkdir(parents=True)
        full.write_new(evidence / "source-verification.json", dict(
            source=str(source_folder), source_evidence=str(source_evidence),
            manifest_sha256=full.MANIFEST_SHA256, accepted_bodies=len(bodies),
            acquisition_ledger_verified=True, canonical_journal_fingerprints_verified=True,
            historical_txt_exact=True, source_snapshot=before, mode="offline-local-republication",
            network_requests=0, journal_attempts_mean="local body processing; no HTTP attempts"))
        shutil.copyfile(source_evidence / "accepted.jsonl", evidence / "accepted.jsonl")
        for p in range(len(bodies)):
            shutil.copyfile(source_evidence / "bodies" / f"{p:05d}.json", evidence / "bodies" / f"{p:05d}.json")
        report = pipeline.RunReport(output_dir=destination / "outputs", formats=cfg.formats)
        job = ScrapeJob(novel_slug=runner.SPEC.novel_slug, adapter_key=runner.SPEC.adapter_key,
            start=1, end=full.END, output_mode=OutputMode.CHUNKED, chunk_size=100,
            formats=cfg.formats, output_dir=destination / "outputs", allocate_run=True,
            use_cache=False, use_browser=False, max_retries=0, delay=cfg.interval)
        run = pipeline._prepare_run(job, runner.SPEC, store, deny_network, report, log,
            threading.Event(), None, None, preserve_manifest_metadata=True)
        if run is None:
            raise ValueError("offline selection unexpectedly empty")
        try:
            if len(run.fetch_list) != len(bodies):
                raise ValueError("fresh repair selection differs from accepted bodies")
            for p, meta in enumerate(run.fetch_list):
                if meta.url != chapters[p].page_url:
                    raise ValueError("fresh repair chapter ordering differs")
                run.outputs.add(meta, bodies[p])
                if (p + 1) % 100 == 0 or p + 1 == len(bodies):
                    run.outputs.publish_ready()
                    log(f"Offline repair: {p + 1}/{len(bodies)} chapters processed")
        except BaseException:
            pipeline._abort_run(run, log)
            raise
        pipeline._finish_run(run, report, log)
        result = full.audit_publication(run.run_dir, evidence, chapters, log=log)
        result["embedded_glyph_checks"] = embedded_glyph_audit(run.run_dir, chapters, bodies)
        result["preservation_failures"] = [relative for relative, expected in before.items()
            if not (cfg.root / relative).is_file()
            or dict(zip(("size", "sha256"), file_sha256(cfg.root / relative))) != expected]
        # Check the copied acquisition evidence as well as the new journal bindings.
        result["evidence_hashes"] = full.snapshot(evidence)
        result["output_location"] = str(run.run_dir)
        result["mode"] = "offline-local-republication"
        result["intentional_blank_pages"] = []  # this layout has no blank-page convention
        result["source_http_history"] = "preserved unchanged; historical pacing failures are not erased"
        if result["preservation_failures"]:
            result["complete_publication_verified"] = False
        full.write_new(evidence / "publication-audit.json", result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    cfg = runner.Config(root=full.CERTIFIED_ROOT, mode="full", cap=full.COUNT,
        resume=True, start=1, end=full.END, formats=("txt", "pdf"), grouping="chunked", chunk_size=100)
    claim = json.loads((cfg.root / "outputs" / full.CLAIM).read_text())
    result = rebuild(cfg, cfg.root / "outputs" / claim["evidence"], args.destination,
                     log=lambda msg: print(msg, flush=True))
    print(json.dumps({k: result[k] for k in ("file_counts", "published_chapters", "audit_failures",
        "preservation_failures", "output_location", "complete_publication_verified")}, indent=2))
    return 0 if result["complete_publication_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
