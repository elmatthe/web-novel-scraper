"""Typed failure evidence and compact canonical-body diagnostics, never deduplication."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib

from .models import EmptyExtractionError

REASON_CODES = frozenset({
    "IDENTITY_MISMATCH", "IDENTITY_UNKNOWN", "IDENTITY_CONFLICT", "EMPTY_BODY",
    "ACCESS_BLOCKED", "RATE_LIMITED", "URL_NOT_FOUND", "NETWORK_ERROR",
    "EXTRACTION_ERROR", "PDF_GLYPH_UNSUPPORTED", "RENDER_ERROR", "RESCUE_ERROR",
    "LEGACY_UNCLASSIFIED",
    "CONTENT_CONFLICT",
})
BODY_POLICY = "sha256-paragraphs-v1"
OUTCOME_POLICY = "page-outcomes-v1"


@dataclass(frozen=True)
class FailureInfo:
    reason_code: str
    reason: str
    observed_labels: tuple[str, ...] = ()
    stage: str = "fetch"
    format: str | None = None
    http_status: int | None = None

    def fields(self) -> dict:
        return dict(reason_code=self.reason_code, reason=self.reason[:1000],
                    observed_labels=list(self.observed_labels), stage=self.stage,
                    format=self.format, http_status=self.http_status)


def classify_failure(error, *, stage="fetch", fmt=None, code=None) -> FailureInfo:
    """Classification uses exception types; legacy plain messages remain unclassified."""
    from .request_manager import ChallengeFetchError, NotFoundFetchError, RateLimitedFetchError, FetchError
    if isinstance(error, FailureInfo):
        return error
    if code is None:
        if stage == "render":
            code = "RENDER_ERROR"
        elif isinstance(error, EmptyExtractionError):
            code = error.reason_code
        elif isinstance(error, NotFoundFetchError):
            code = "URL_NOT_FOUND"
        elif isinstance(error, RateLimitedFetchError):
            code = "RATE_LIMITED"
        elif isinstance(error, ChallengeFetchError):
            code = "ACCESS_BLOCKED"
        elif isinstance(error, FetchError):
            code = "NETWORK_ERROR"
        elif isinstance(error, BaseException):
            code = "EXTRACTION_ERROR"
        else:
            code = "LEGACY_UNCLASSIFIED"
    return FailureInfo(code, str(error), tuple(getattr(error, "observed_labels", ())),
                       stage, fmt, getattr(error, "status", None))


def body_fingerprint(paragraphs) -> tuple[str, int, int]:
    """Hash exact canonical UTF-8 paragraphs with length framing; exclude headings/layout."""
    digest = hashlib.sha256((BODY_POLICY + "\0").encode("ascii"))
    size = count = 0
    for paragraph in paragraphs:
        encoded = paragraph.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        size += len(encoded)
        count += 1
    return digest.hexdigest(), size, count


class BodyFingerprints:
    """O(selected pages) fixed-size hashes/positions; no previous body text retained."""

    def __init__(self, previous=()):
        self.by_position: dict[int, str] = {}
        self.by_hash: dict[str, dict[int, None]] = {}
        for record in previous:
            self._remember(record["position"], record["body_sha256"])

    def _remember(self, position, digest):
        old = self.by_position.get(position)
        if old is not None and old != digest:
            self.by_hash[old].pop(position, None)
            if not self.by_hash[old]:
                del self.by_hash[old]
        self.by_position[position] = digest
        self.by_hash.setdefault(digest, {})[position] = None

    def observe(self, position, paragraphs):
        digest, size, count = body_fingerprint(paragraphs)
        other = next((p for p in self.by_hash.get(digest, ()) if p != position), None)
        self._remember(position, digest)
        return dict(body_policy=BODY_POLICY, body_sha256=digest, body_bytes=size,
                    paragraph_count=count, duplicate_of=other)
