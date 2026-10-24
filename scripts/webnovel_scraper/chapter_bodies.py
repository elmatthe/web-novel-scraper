"""Durable canonical bodies bound to accepted journal content events.

Only explicitly recorded, intact artifacts are reusable. Older runs without
these artifacts keep their existing cache/fetch behavior; orphan files never
become accepted content. Admission reads these before repairing any journal.
"""
from __future__ import annotations

import hashlib
import json

from .atomic_io import write_json_atomic
from .content_integrity import body_fingerprint
from .models import ChapterContent
from .run_journal import JournalError, file_sha256
from .run_storage import checked_output_path
from .semantic_resume import EXTRACTION_POLICY, observed_label
from .source_identity import ChapterLabel

BODY_FOLDER = "chapter-bodies"


def save_body(run_dir, position, meta, content):
    row = dict(schema=1, extraction_policy=EXTRACTION_POLICY, position=position,
               page_url=meta.url, label=observed_label(content), render_label=content.label_text, title=content.title,
               paragraphs=content.paragraphs)
    digest = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    relative = f"{BODY_FOLDER}/{position:05d}-{digest}.json"
    path = checked_output_path(run_dir, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    checked_output_path(run_dir, relative)
    write_json_atomic(path, row)
    size, sha256 = file_sha256(path)
    return dict(path=relative, size=size, sha256=sha256)


def load_body(run_dir, record):
    """Return a verified body, or None for a historical content event without one."""
    artifact = record.get("body_artifact")
    if artifact is None:
        return None
    try:
        if (not isinstance(artifact, dict) or set(artifact) != {"path", "size", "sha256"}
                or not isinstance(artifact["path"], str)
                or not artifact["path"].startswith(BODY_FOLDER + "/")):
            raise ValueError("invalid artifact reference")
        path = checked_output_path(run_dir, artifact["path"])
        if file_sha256(path) != (artifact["size"], artifact["sha256"]):
            raise ValueError("artifact hash differs")
        row = json.loads(path.read_text(encoding="utf-8"))
        if (row["schema"] != 1 or row["extraction_policy"] != EXTRACTION_POLICY
                or row["position"] != record["position"] or row["page_url"] != record["page_url"]
                or row["label"] != record["label"] or not isinstance(row["title"], str)
                or not isinstance(row["paragraphs"], list)
                or not all(isinstance(p, str) for p in row["paragraphs"])):
            raise ValueError("artifact identity/policy differs")
        content = ChapterContent(ChapterLabel(row["label"]).to_index(), row["title"],
                                 row["paragraphs"], label=row["render_label"])
        if (observed_label(content) != record["observed_label"]
                or body_fingerprint(content.paragraphs) != (
                    record["body_sha256"], record["body_bytes"], record["paragraph_count"])):
            raise ValueError("canonical fingerprint differs")
        return content
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        raise JournalError(f"Saved body at position {record['position']} cannot be verified ({exc}). "
                           "Preserve this run and start a new run.") from exc
