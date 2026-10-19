"""Supreme Magus .com page-observation policy, shared by manifest and discovery.

These are historical observations, not promises that a website will never change.
No URL failure or omitted displayed label certifies source absence under this policy.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import re

POLICY = "sm-com-page-identity-v1"
STATUSES = {"observed", "extra", "unknown", "unreadable", "inaccessible",
            "conflict", "saved_unknown", "candidate"}


class SourceBoundChanged(RuntimeError):
    """The fresh index bound is below a previously observed source position."""


def requires_verification(source) -> bool:
    return (source.novel_slug == "supreme-magus" and source.host == "freewebnovel.com"
            and source.adapter_key == "freewebnovel")


@dataclass(frozen=True)
class PageObservation:
    url: str
    status: str
    provenance: str
    labels: tuple[str, ...]
    observed_utc: str
    detail: str = ""
    prior_labels: tuple[str, ...] | None = None
    prior_observed_utc: str | None = None

    def __post_init__(self):
        from .chapter_manifest import ManifestError, _check_timestamp, _text
        from .source_identity import ChapterLabel, SourceIdentityError
        if not isinstance(self.status, str) or self.status not in STATUSES:
            raise ManifestError("Unknown page observation status.")
        if not isinstance(self.provenance, str) or self.provenance not in {"current-page", "current-response", "historical-cache", "policy-invalidation"}:
            raise ManifestError("Unknown page observation provenance.")
        if self.observed_utc is None:
            raise ManifestError("Page observations require an acquisition timestamp.")
        _check_timestamp(self.observed_utc, "Page observed_utc")
        _text(self.detail, "Observation detail", limit=1000, allow_empty=True)
        if not isinstance(self.url, str) or not isinstance(self.labels, (list, tuple)):
            raise ManifestError("Observation URL must be text and labels must be a sequence.")
        object.__setattr__(self, "labels", tuple(self.labels))
        try:
            for label in self.labels:
                ChapterLabel(label)
        except SourceIdentityError as exc:
            raise ManifestError(str(exc)) from exc
        if self.status == "observed" and (self.provenance != "current-page" or len(self.labels) != 1):
            raise ManifestError("An observed identity requires one exact label from a current page.")
        if self.status == "extra" and (self.provenance != "current-page" or self.labels):
            raise ManifestError("A recognized extra requires a current page and no chapter label.")
        if self.prior_labels is not None:
            if self.resolved:
                raise ManifestError("Resolved observations cannot retain an unresolved prior identity.")
            if not isinstance(self.prior_labels, (tuple, list)) or len(self.prior_labels) > 1:
                raise ManifestError("Prior identity must contain one exact label or an empty extra identity.")
            object.__setattr__(self, "prior_labels", tuple(self.prior_labels))
            try:
                for label in self.prior_labels:
                    ChapterLabel(label)
            except SourceIdentityError as exc:
                raise ManifestError(str(exc)) from exc
            if self.prior_observed_utc is None:
                raise ManifestError("Prior identity requires its acquisition timestamp.")
            _check_timestamp(self.prior_observed_utc, "Prior identity observed_utc")
        elif self.prior_observed_utc is not None:
            raise ManifestError("Prior acquisition timestamp requires a prior identity.")

    @property
    def resolved(self):
        return self.status in {"observed", "extra"}

    def to_json(self):
        return dict(url=self.url, status=self.status, provenance=self.provenance,
                    labels=list(self.labels), observed_utc=self.observed_utc, detail=self.detail,
                    prior_labels=list(self.prior_labels) if self.prior_labels is not None else None,
                    prior_observed_utc=self.prior_observed_utc)

    @classmethod
    def from_json(cls, value):
        from .chapter_manifest import ManifestError, _exact_keys
        _exact_keys(value, {"url", "status", "provenance", "labels", "observed_utc", "detail",
                            "prior_labels", "prior_observed_utc"}, "Page observation")
        if not isinstance(value["labels"], list):
            raise ManifestError("Page observation labels must be a list.")
        return cls(**value)


@dataclass(frozen=True)
class Verification:
    policy: str
    bound: int
    bound_utc: str
    bound_confirmed: bool
    pages: tuple[PageObservation, ...]

    def __post_init__(self):
        from .chapter_manifest import ManifestError, _check_timestamp
        if self.policy != POLICY:
            raise ManifestError(f"Verification policy must be {POLICY}; revalidation is required.")
        if type(self.bound) is not int or not 1 <= self.bound <= 9_999_999:
            raise ManifestError("Verification bound must be a positive source slug.")
        if self.bound_utc is None:
            raise ManifestError("Source bound requires an acquisition timestamp.")
        _check_timestamp(self.bound_utc, "Source bound_utc")
        if type(self.bound_confirmed) is not bool:
            raise ManifestError("Source bound confirmation must be boolean.")
        object.__setattr__(self, "pages", tuple(self.pages))
        if any(not isinstance(p, PageObservation) for p in self.pages):
            raise ManifestError("Verification pages must be page observations.")
        urls = [p.url for p in self.pages]
        if urls != sorted(set(urls)):
            raise ManifestError("Page observations must have unique, sorted URLs.")

    @classmethod
    def create(cls, *, bound, bound_utc, bound_confirmed, pages):
        return cls(POLICY, bound, bound_utc, bound_confirmed, tuple(sorted(pages, key=lambda p: p.url)))

    def slug(self, source, url):
        from .chapter_manifest import ManifestError
        pattern = re.escape(source.index_url) + r"/chapter-([1-9][0-9]{0,6})"
        match = re.fullmatch(pattern, url) if isinstance(url, str) else None
        if not match or int(match[1]) > self.bound:
            raise ManifestError(f"Observation URL {url!r} is outside this source bound.")
        return int(match[1])

    def complete_for(self, source):
        slugs = {self.slug(source, p.url) for p in self.pages if p.resolved}
        return self.bound_confirmed and len(slugs) == self.bound

    def validate_manifest(self, manifest):
        from .chapter_manifest import ManifestError, accounted_numbers, accounted_spans, MAX_LABEL_NUMBER
        if not requires_verification(manifest.source):
            raise ManifestError("This verification policy is scoped to Supreme Magus .com only.")
        if manifest.provenance.method != "walk" or manifest.provenance.legacy_index_sha256:
            raise ManifestError("Page verification cannot originate from legacy metadata.")
        observations = {p.url: p for p in self.pages}
        for page in self.pages:
            self.slug(manifest.source, page.url)
        chapters = {c.page_url: c for c in manifest.chapters}
        for url, page in observations.items():
            chapter = chapters.get(url)
            if page.status == "observed":
                if not chapter or page.labels != (chapter.label.text,) or chapter.slug != self.slug(manifest.source, url):
                    raise ManifestError("Observed page and exact chapter identity disagree.")
            elif chapter:
                raise ManifestError("An unresolved page or extra cannot certify a chapter.")
        if set(chapters) - set(observations) or manifest.absent:
            raise ManifestError("Every chapter needs an observation; this policy certifies no source absences.")
        complete = self.complete_for(manifest.source)
        spans = accounted_spans(((0, MAX_LABEL_NUMBER),), accounted_numbers(manifest.chapters, ())) if complete else ()
        if manifest.spans != spans or manifest.toc_complete:
            raise ManifestError("Coverage requires all source positions resolved; novel-end claims are not persisted.")

    def invalidate(self, reason):
        return replace(self, pages=tuple(replace(p, status="saved_unknown", provenance="policy-invalidation",
                                                detail=reason, prior_labels=None, prior_observed_utc=None) for p in self.pages))

    def pending_tail(self, source, margin=64):
        """Retain independent comparison evidence across interrupted revalidation."""
        cutoff = max(0, self.bound - margin)
        return replace(self, pages=tuple(
            replace(p, status="saved_unknown", provenance="policy-invalidation", detail="Tail revalidation pending.",
                    prior_labels=p.labels if p.resolved else p.prior_labels,
                    prior_observed_utc=p.observed_utc if p.resolved else p.prior_observed_utc)
            if self.slug(source, p.url) > cutoff else p for p in self.pages))

    def to_json(self):
        return dict(policy=self.policy, bound=self.bound, bound_utc=self.bound_utc,
                    bound_confirmed=self.bound_confirmed, pages=[p.to_json() for p in self.pages])

    @classmethod
    def from_json(cls, value):
        from .chapter_manifest import ManifestError, _exact_keys
        _exact_keys(value, {"policy", "bound", "bound_utc", "bound_confirmed", "pages"}, "Verification")
        if not isinstance(value["pages"], list):
            raise ManifestError("Verification pages must be a list.")
        return cls(value["policy"], value["bound"], value["bound_utc"], value["bound_confirmed"],
                   tuple(PageObservation.from_json(p) for p in value["pages"]))
