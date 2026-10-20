"""Versioned application provenance, not authentication of external story prose.

The immutable header anchors source/selection/manifest/grouping. Publications
reference exact accepted content events and cover their own byte hashes. No bodies
are persisted here. Policy changes must choose new identifiers, never grandfather.
"""
from __future__ import annotations

import hashlib
import json
import re

RESUME_POLICY = "semantic-resume-v1"
EXTRACTION_POLICY = "chapter-extraction-identity-v1"


def evidence_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def content_reference(record):
    return {"position": record["position"], "seq": record["seq"],
            "sha256": evidence_digest({k: v for k, v in record.items() if k != "crc"})}


def header_context(header):
    # Explicit versioned fields keep optional UI/report metadata out of the trust
    # boundary, and give future policy changes a reviewable compatibility contract.
    return evidence_digest({
        "source": header.source.to_json(), "run": [header.novel_slug, header.run_number, header.created_utc],
        "selection": [header.selection_sha256, header.selection_count, header.first_label, header.last_label,
                      list(header.requested), list(header.effective)],
        "manifest": header.manifest_sha256, "formats": list(header.formats),
        "grouping": [header.grouping_mode, header.chunk_size],
        "resume_policy": header.resume_policy, "extraction_policy": header.extraction_policy,
        "outcome_policy": header.outcome_policy,
    })


def publication_certificate(header, record, contents):
    payload = {k: v for k, v in record.items() if k not in ("crc", "certificate", "seq", "utc", "type")}
    bindings = {"policy": RESUME_POLICY, "context": header_context(header),
                "contents": [content_reference(contents[p]) for p in record["pages"]]}
    return {**bindings, "sha256": evidence_digest({"publication": payload, **bindings})}


def observed_label(content):
    # CP1's confirmed composite heading renders both numbers; its identity is the
    # final whole label. Fractions elsewhere retain every digit, including zeroes.
    shown = content.label_text
    composite = re.fullmatch(r"\d+(?:\.\d+)?-(\d+)", shown)
    return composite[1] if composite else shown


def require_policy(header):
    from .content_integrity import OUTCOME_POLICY
    from .run_journal import JournalError
    if (header.resume_policy != RESUME_POLICY or header.extraction_policy != EXTRACTION_POLICY
            or header.outcome_policy != OUTCOME_POLICY):
        raise JournalError("Semantic Resume certificate is legacy, missing or incompatible. "
                           "Start a new isolated run; old files will not be upgraded or repaired.")
    if not isinstance(header.manifest_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", header.manifest_sha256):
        raise JournalError("Semantic Resume lacks a valid manifest snapshot digest. Start a new run.")


def check_event(replay, record):
    """Validate every historical relationship in sequence, before updating replay."""
    from .run_journal import JournalError
    if replay.header.resume_policy is None:
        # Backwards-readable legacy analysis never grants semantic authority.
        return
    require_policy(replay.header)
    kind = record["type"]
    if kind == "content":
        p = record["position"]
        if (record.get("extraction_policy") != EXTRACTION_POLICY
                or record.get("observed_label") != record["label"] or p not in replay.attempts):
            raise JournalError("Semantic content certificate lacks matching identity/policy/attempt evidence. Start a new run.")
        old = replay.contents.get(p)
        if old and old["body_sha256"] != record["body_sha256"]:
            raise JournalError("Contradictory accepted body fingerprints; start a new run.")
    elif kind == "publish":
        from .models import OutputMode
        from .run_output import plan_groups
        if replay.semantic_groups is None:
            replay.semantic_groups = {g.key: g.positions for g in plan_groups(replay.header.selection_count,
                                      OutputMode(replay.header.grouping_mode), replay.header.chunk_size)}
        positions = replay.semantic_groups.get(record["key"])
        pages, missing = record["pages"], record["missing"]
        if (positions is None or not pages or pages != [p for p in positions if p in pages]
                or missing != [p for p in positions if p not in pages]):
            raise JournalError("Semantic publication contradicts immutable group positions/order.")
        if record["format"] not in replay.header.formats and not record.get("fallback_for"):
            raise JournalError("Semantic publication has an unrequested format without fallback evidence.")
        if record.get("fallback_for") and not all(any(
                f["position"] == p and f.get("reason_code") == "PDF_GLYPH_UNSUPPORTED"
                for f in replay.failures) for p in pages):
            raise JournalError("Semantic fallback publication lacks requested-PDF glyph failure evidence.")
        try:
            expected = publication_certificate(replay.header, record, replay.contents)
        except KeyError:
            raise JournalError("Semantic publication lacks accepted content evidence. Start a new run.") from None
        # Canonical encoding also distinguishes JSON booleans from integers;
        # Python container equality alone would accept True as position/seq 1.
        if evidence_digest(record.get("certificate")) != evidence_digest(expected):
            raise JournalError("Semantic publication certificate contradicts its content or publication evidence. Start a new run.")
        owner = (record["key"], record["format"])
        prior = replay.publication_owners.setdefault(record["path"].casefold(), owner)
        if prior != owner:
            raise JournalError("Semantic publications claim the same file for different groups.")
    elif kind == "content_conflict":
        p = record.get("position")
        old = replay.contents.get(p) if type(p) is int else None
        if (old is None or record.get("accepted") != content_reference(old)
                or not isinstance(record.get("observed_body_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", record["observed_body_sha256"])
                or record["observed_body_sha256"] == old["body_sha256"]):
            raise JournalError("Malformed semantic content-conflict evidence.")


def validate_admission(replay):
    from .run_journal import JournalError
    require_policy(replay.header)
    if replay.conflicts:
        raise JournalError("A later fetch changed accepted canonical content. Existing publications "
                           "were preserved; review the conflict and start a new run.")
