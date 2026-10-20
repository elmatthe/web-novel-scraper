#!/usr/bin/env python3
"""Check one finished run folder against its own record (v0.3.0 acceptance tool, NOT shipped).

Reads only the run folder: the fixed selection (``run-selection.json``, the chapters the run set
out to save), the journal (``run-journal.jsonl``, every file it published with its checksum and
pages, every failure) and the files in ``PDF/`` and ``TXT/``. Nothing is fetched or changed, and no
chapter text is printed or written: the report holds chapter numbers, titles, addresses, file
names and checksums only.

Usage (from the project folder):
    .venv\\Scripts\\python files\\audit_run.py "<run folder>" [--json report.json] [--pdf-text]

Checks:
  * the selection: order of chapter numbers, numbers on more than one page, whole numbers missing
    between the first and last chapter (the run's notes say which ones the site's list skips);
  * every planned output file in every format: recorded, present, same size and SHA-256 as
    recorded, and which chapters it holds or lacks;
  * every chapter: saved in every format, failed (with the recorded reason), or not saved;
  * TXT files: the chapter headings, in order, are exactly the chapters the journal says the file
    holds; replacement characters (U+FFFD) are counted;
  * with ``--pdf-text``: every expected heading appears, in order, in the PDFs' text (slower);
  * files the run did not publish: anything in ``PDF/``, ``TXT/`` or the run folder itself that the
    journal does not record (a file copied in by hand, a leftover temporary file, a duplicate under
    another name), with its size, SHA-256, the chapter its name says it is, and whether the journal
    already records a file for that chapter. A run with such a file does not pass.

Exit code 0 when everything checks out, 1 when anything does not (the report says what).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from webnovel_scraper.models import OutputMode  # noqa: E402
from webnovel_scraper.run_accounting import page_accounting  # noqa: E402
from webnovel_scraper.run_journal import (  # noqa: E402
    JOURNAL_FILENAME, LOCK_FILENAME, SELECTION_FILENAME, file_sha256, load_selection, replay_journal,
)
from webnovel_scraper.run_output import FORMAT_DIRS, plan_groups  # noqa: E402
from webnovel_scraper.run_storage import RESERVATION_FILENAME  # noqa: E402
from webnovel_scraper.txt_builder import CHAPTER_SEPARATOR  # noqa: E402

_HEADING = re.compile(r"^Chapter (\S+?): ", re.MULTILINE)


# A page the site shows as "Chapter 1129.1 - 1130" is written "Chapter 1129.1-1130: ..."; the chapter it
# is, for the manifest, is the final number (the first one is the page before it).
_COMPOSITE = re.compile(r"^\d+(?:\.\d+)?-(\d+)$")


def _identity(shown: str) -> str:
    composite = _COMPOSITE.match(shown)
    return composite.group(1) if composite else shown


def _headings_in_txt(text: str) -> list[str]:
    return [_identity(m.group(1)) for block in text.split(CHAPTER_SEPARATOR) if (m := _HEADING.match(block))]


def _pdf_lacks_heading(path: Path, expected: list[str]) -> str | None:
    """The first expected chapter whose heading is not found, in order, in the PDF's text (None when
    all are). Extracted PDF text has no chapter boundaries, so a story line that happens to start
    with "Chapter 9:" is skipped over rather than counted; the exact check is the TXT one."""
    from pypdf import PdfReader

    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    position = 0
    for label in expected:
        found = re.compile(rf"^Chapter (?:\d+(?:\.\d+)?-)?{re.escape(label)}: ", re.MULTILINE).search(text, position)
        if found is None:
            return label
        position = found.end()
    return None


_RUN_FILES = {JOURNAL_FILENAME, SELECTION_FILENAME, LOCK_FILENAME, RESERVATION_FILENAME, "run-audit.json"}
_TEMPORARY = (".part", ".tmp")
_FILE_CHAPTER = re.compile(r"^Chapter (\S+?)_ ")


def _unrecorded_files(run_dir: Path, recorded: set[str], labels: list[str], held: dict) -> list[dict]:
    """Every file under the run folder that the journal does not record (never follows links).
    ``recorded`` holds the journal's relative paths, case-folded. Read-only: it lists and hashes."""
    found: list[tuple[str, str | None]] = []            # (relative path, format of its folder)
    folders = {name.casefold(): fmt for fmt, name in FORMAT_DIRS.items()}
    for entry in sorted(run_dir.iterdir(), key=lambda e: e.name):
        if entry.name.casefold() in folders and entry.is_dir() and not entry.is_symlink():
            for base, dirs, names in os.walk(entry):
                dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
                for name in sorted(names):
                    found.append((Path(base, name).relative_to(run_dir).as_posix(), folders[entry.name.casefold()]))
        elif entry.name not in _RUN_FILES:
            found.append((entry.name, None))
    out = []
    for relative, fmt in found:
        if relative.casefold() in recorded:
            continue
        path = run_dir / relative
        if path.is_symlink() or not path.is_file():
            out.append({"path": relative, "kind": "link or folder", "size": None, "sha256": None})
            continue
        size, digest = file_sha256(path)
        row = {"path": relative, "kind": "temporary" if relative.endswith(_TEMPORARY) else "unrecorded",
               "size": size, "sha256": digest}
        named = _FILE_CHAPTER.match(path.name)
        if named is not None and fmt is not None:
            identity = _identity(named.group(1))
            row["chapter"] = identity
            if identity in labels:
                row["recorded_file_for_that_chapter"] = held.get(fmt, {}).get(labels.index(identity))
        out.append(row)
    return out


def audit(run_dir: Path, *, pdf_text: bool = False) -> dict:
    replay = replay_journal(run_dir / JOURNAL_FILENAME)
    header = replay.header
    from webnovel_scraper.semantic_resume import validate_admission
    from webnovel_scraper.run_journal import JournalError
    try:
        validate_admission(replay)
        semantic_reason = "Current application provenance; byte integrity is checked separately. External story correctness is not authenticated."
        semantic_eligible = True
    except JournalError as exc:
        semantic_reason, semantic_eligible = str(exc), False
    chapters = load_selection(run_dir, header)
    labels = [c.label.text for c in chapters]
    values = [c.label.value for c in chapters]
    problems: list[str] = []

    # ── the selection ─────────────────────────────────────────────────────
    out_of_order = [labels[i] for i in range(1, len(values)) if values[i] < values[i - 1]]
    repeated = sorted((label for label, n in Counter(labels).items() if n > 1), key=Decimal)
    wholes = {int(v) for v in values if v == int(v)}
    missing_numbers = [n for n in range(int(values[0]), int(values[-1]) + 1) if n not in wholes] if values else []
    if out_of_order:
        problems.append(f"chapters out of order after: {', '.join(out_of_order[:20])}")

    # ── files ────────────────────────────────────────────────────────────
    groups = plan_groups(len(chapters), OutputMode(header.grouping_mode), header.chunk_size)
    actual_formats = tuple(dict.fromkeys((*header.formats, *(r["format"] for r in replay.publishes.values()))))
    files, held, good_records = [], {fmt: {} for fmt in actual_formats}, {}
    for group in groups:
        for fmt in actual_formats:
            record = replay.publishes.get((group.key, fmt))
            if fmt not in header.formats and record is None:
                continue
            entry = {"group": group.key, "format": fmt, "chapters": [labels[p] for p in group.positions]}
            entry["semantic_status"] = "INCOMPLETE" if semantic_eligible else "LEGACY UNVERIFIED" if header.resume_policy is None else "INCOMPATIBLE"
            entry["requested"] = fmt in header.formats
            if record is None:
                entry["state"] = "not written"
                files.append(entry)
                continue
            path = run_dir / record["path"]
            entry.update(file=record["path"], recorded_sha256=record["sha256"],
                         lacks=[labels[p] for p in record["missing"]], fallback_for=record.get("fallback_for"))
            if not path.is_file():
                entry["state"] = "missing on disk"
                problems.append(f"{record['path']} is recorded but missing")
            else:
                size, digest = file_sha256(path)
                if (size, digest) != (record["size"], record["sha256"]):
                    entry["state"] = "changed since it was written"
                    problems.append(f"{record['path']} does not match its recorded checksum")
                else:
                    good_records[(group.key, fmt)] = record
                    entry["state"] = "ok" if not record["missing"] else "ok but incomplete"
                    if semantic_eligible:
                        entry["semantic_status"] = "INCOMPLETE" if record["missing"] else "SUSPICIOUS" if any(
                            replay.contents.get(p, {}).get("duplicate_of") is not None for p in record["pages"]) else "CERTIFIED"
                    for position in record["pages"]:
                        held[fmt][position] = record["path"]
                    expected = [labels[p] for p in record["pages"]]
                    if fmt == "txt":
                        text = path.read_text(encoding="utf-8")
                        found = _headings_in_txt(text)
                        entry["replacement_characters"] = text.count("\ufffd")
                        entry["headings_match"] = found == expected
                        if found != expected:
                            problems.append(f"{record['path']}: headings {found[:5]}... differ from the "
                                            f"recorded chapters {expected[:5]}...")
                    elif pdf_text:
                        lacking = _pdf_lacks_heading(path, expected)
                        entry["headings_match"] = lacking is None
                        if lacking is not None:
                            problems.append(f"{record['path']}: no heading for chapter {lacking} (in order)")
            files.append(entry)

    # ── files the run did not publish ────────────────────────────────────
    recorded = {record["path"].casefold() for record in replay.publishes.values()}
    unrecorded = _unrecorded_files(run_dir, recorded, labels, held)
    if unrecorded:
        duplicates = sum(1 for row in unrecorded if row.get("recorded_file_for_that_chapter"))
        problems.append(
            f"{len(unrecorded)} file(s) in the run folder are not in the run's record "
            f"({duplicates} for a chapter the journal already records a file for): "
            + ", ".join(row["path"] for row in unrecorded[:5]) + (" ..." if len(unrecorded) > 5 else ""))

    # ── chapters ─────────────────────────────────────────────────────────
    failures = {f["position"]: f["reason"] for f in replay.failures}
    outcomes, accounting = page_accounting(len(chapters), header.formats, replay.attempts,
        replay.contents, replay.failures, good_records, explicit=header.outcome_policy is not None)
    rows, counts = [], Counter()
    for position, chapter in enumerate(chapters):
        where = {fmt: held[fmt].get(position) for fmt in actual_formats
                 if fmt in header.formats or position in held[fmt]}
        if all(outcome == "success" for outcome in outcomes[position]["requested_format_outcomes"].values()):
            status = "saved"
        elif position in failures:
            status = "failed"
        else:
            status = "not saved"
        counts[status] += 1
        rows.append({"position": position, "label": chapter.label.text, "title": chapter.title,
                     "page_url": chapter.page_url, "status": status, "files": where,
                     "reason": failures.get(position) if status != "saved" else None,
                     "attempted": outcomes[position]["attempted"],
                     "processed": outcomes[position]["processed"],
                     "requested_format_outcomes": outcomes[position]["requested_format_outcomes"],
                     "fallback_formats": outcomes[position]["fallback_formats"],
                     "body_evidence": outcomes[position]["content"]})
    if counts["failed"] or counts["not saved"]:
        problems.append(f"{counts['failed']} chapter(s) failed and {counts['not saved']} are not saved "
                        "in every format")

    integrity = [record for record in replay.contents.values() if record.get("duplicate_of") is not None]
    for record in integrity:
        other = record["duplicate_of"]
        problems.append(f"suspected duplicate canonical body: position {other} chapter {labels[other]} "
                        f"({chapters[other].page_url}) and position {record['position']} chapter {record['label']} "
                        f"({record['page_url']}); {record['body_sha256']}. Both pages retained; equality does not prove absence.")
    return {
        "run_folder": str(run_dir),
        "novel": header.novel_slug,
        "source": header.source.index_url,
        "requested": list(header.requested),
        "effective": list(header.effective),
        "formats": list(header.formats),
        "grouping": header.grouping_mode,
        "chunk_size": header.chunk_size,
        "notes": list(header.notes),
        "last_session_end": replay.last_end,
        "selected": len(chapters),
        "first": labels[0] if labels else None,
        "last": labels[-1] if labels else None,
        "numbers_on_more_than_one_page": repeated,
        "whole_numbers_missing_from_the_selection": missing_numbers,
        "chapter_counts": dict(counts),
        "files": files,
        "unrecorded_files": unrecorded,
        "chapters": rows,
        "accounting": accounting,
        "failure_evidence": replay.failures,
        "semantic_resume": {"eligible": semantic_eligible, "reason": semantic_reason,
                            "policy": header.resume_policy, "extraction_policy": header.extraction_policy},
        "content_conflicts": replay.conflicts,
        "integrity_diagnostics": integrity,
        "problems": problems,
    }


def _print(report: dict) -> None:
    print(f"Run       {report['run_folder']}")
    print(f"Source    {report['source']}  ({report['novel']})")
    print(f"Range     requested {report['requested'][0]}-{report['requested'][1]}, "
          f"saved range {report['effective'][0]}-{report['effective'][1]}; "
          f"{report['selected']} chapter pages ({report['first']} to {report['last']})")
    print(f"Output    {', '.join(f.upper() for f in report['formats'])}, {report['grouping']}"
          + (f" ({report['chunk_size']} per file)" if report["grouping"] == "chunked" else ""))
    print(f"Ended     {report['last_session_end']}")
    for note in report["notes"]:
        print(f"Note      {note}")
    missing = report["whole_numbers_missing_from_the_selection"]
    if missing:
        shown = ", ".join(map(str, missing[:30])) + (" ..." if len(missing) > 30 else "")
        print(f"Gaps      {len(missing)} whole number(s) not in the selection: {shown}")
    if report["numbers_on_more_than_one_page"]:
        print(f"Repeats   numbers on more than one page: {', '.join(report['numbers_on_more_than_one_page'])}")
    states = Counter(f["state"] for f in report["files"])
    print(f"Files     {len(report['files'])} planned: " + ", ".join(f"{n} {s}" for s, n in states.items()))
    if report["unrecorded_files"]:
        print(f"Extra     {len(report['unrecorded_files'])} file(s) the journal does not record "
              "(listed in the report with checksums)")
    fffd = sum(f.get("replacement_characters", 0) for f in report["files"])
    if fffd:
        print(f"Unicode   {fffd} replacement character(s) in TXT files (text the site served damaged)")
    print("Chapters  " + ", ".join(f"{n} {s}" for s, n in report["chapter_counts"].items()))
    for row in report["chapters"]:
        if row["status"] != "saved":
            print(f"  chapter {row['label']}: {row['status']}" + (f" ({row['reason']})" if row["reason"] else ""))
    print()
    if report["problems"]:
        print("PROBLEMS:")
        for problem in report["problems"]:
            print(f"  - {problem}")
    else:
        print("All checks passed.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("run_folder", type=Path)
    parser.add_argument("--json", type=Path, help="also write the full report here")
    parser.add_argument("--pdf-text", action="store_true", help="also check headings inside the PDFs")
    args = parser.parse_args(argv)
    report = audit(args.run_folder, pdf_text=args.pdf_text)
    _print(report)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Full report: {args.json}")
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
