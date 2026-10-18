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
  * with ``--pdf-text``: every expected heading appears, in order, in the PDFs' text (slower).

Exit code 0 when everything checks out, 1 when anything does not (the report says what).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from webnovel_scraper.models import OutputMode  # noqa: E402
from webnovel_scraper.run_journal import (  # noqa: E402
    JOURNAL_FILENAME, file_sha256, load_selection, replay_journal,
)
from webnovel_scraper.run_output import plan_groups  # noqa: E402
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


def audit(run_dir: Path, *, pdf_text: bool = False) -> dict:
    replay = replay_journal(run_dir / JOURNAL_FILENAME)
    header = replay.header
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
    files, held = [], {fmt: {} for fmt in header.formats}
    for group in groups:
        for fmt in header.formats:
            record = replay.publishes.get((group.key, fmt))
            entry = {"group": group.key, "format": fmt, "chapters": [labels[p] for p in group.positions]}
            if record is None:
                entry["state"] = "not written"
                files.append(entry)
                continue
            path = run_dir / record["path"]
            entry.update(file=record["path"], recorded_sha256=record["sha256"],
                         lacks=[labels[p] for p in record["missing"]])
            if not path.is_file():
                entry["state"] = "missing on disk"
                problems.append(f"{record['path']} is recorded but missing")
            else:
                size, digest = file_sha256(path)
                if (size, digest) != (record["size"], record["sha256"]):
                    entry["state"] = "changed since it was written"
                    problems.append(f"{record['path']} does not match its recorded checksum")
                else:
                    entry["state"] = "ok" if not record["missing"] else "ok but incomplete"
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

    # ── chapters ─────────────────────────────────────────────────────────
    failures = {f["position"]: f["reason"] for f in replay.failures}
    rows, counts = [], Counter()
    for position, chapter in enumerate(chapters):
        where = {fmt: held[fmt].get(position) for fmt in header.formats}
        if all(where.values()):
            status = "saved"
        elif position in failures:
            status = "failed"
        else:
            status = "not saved"
        counts[status] += 1
        rows.append({"position": position, "label": chapter.label.text, "title": chapter.title,
                     "page_url": chapter.page_url, "status": status, "files": where,
                     "reason": failures.get(position) if status != "saved" else None})
    if counts["failed"] or counts["not saved"]:
        problems.append(f"{counts['failed']} chapter(s) failed and {counts['not saved']} are not saved "
                        "in every format")

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
        "chapters": rows,
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
