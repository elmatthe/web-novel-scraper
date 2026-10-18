"""Validated source and chapter identity (v0.3.0 Checkpoint 03.0).

Pure data and validation: no I/O, no network, no import of the catalog, adapters or
request manager. Later manifest, cache and resume code keys everything on these types.

* A **source** is one novel on one website *origin*, read through one adapter
  implementation. ``freewebnovel.net`` and ``freewebnovel.com`` share the ``freewebnovel``
  adapter but are different sources; ``adapter_key`` alone is never an identity.
* A **chapter** is identified by its source page (the normalised page URL on the source's
  own host), never by the number it displays. Two pages that both display chapter 3664 are
  two chapters. ``slug`` and ``source_id`` ride along as evidence.
* A displayed chapter number is a :class:`ChapterLabel`: canonical decimal *text*, so
  ``627.2`` and ``627.10`` survive any JSON round trip untouched (a float cannot tell them
  apart).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Mapping, Optional
from urllib.parse import unquote, urlsplit


class SourceIdentityError(ValueError):
    """A source, chapter or label failed validation (message is user-actionable)."""


# Hosts each adapter implementation may be read from. The table is the "expected host"
# guard: a new website (checkpoint 04) is added here deliberately, never inferred. The
# disabled stub adapters are listed only so every current catalog row can build an identity;
# whether a source is runnable remains the catalog's decision.
SUPPORTED_HOSTS: Mapping[str, frozenset[str]] = {
    "freewebnovel.com": frozenset({"freewebnovel"}),
    "freewebnovel.net": frozenset({"freewebnovel"}),
    "dynamic.webnovel.com": frozenset({"webnovel_dynamic"}),
    "www.empirenovel.com": frozenset({"empire_novel"}),
    "telegra.ph": frozenset({"telegraph"}),
    "novelbin.com": frozenset({"novel_bin"}),
}

_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_MAX_SLUG = 80
_LABEL_RE = re.compile(r"([0-9]{1,7})(?:\.([0-9]{1,6}))?")
_IDENT_KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,39}")
_MAX_IDENT_VALUE = 200
_UNSAFE_URL_CHARS = re.compile(r"[\x00-\x20\x7f\\]")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def validate_novel_slug(slug: object) -> str:
    """Return ``slug`` if it is a canonical novel slug (lowercase ``a-z0-9`` words joined
    by single hyphens, at most 80 characters); else raise. Safe as a folder-name stem."""
    if not isinstance(slug, str) or not slug:
        raise SourceIdentityError(f"Novel slug must be a non-empty string, got {slug!r}.")
    if len(slug) > _MAX_SLUG or not _SLUG_RE.fullmatch(slug):
        raise SourceIdentityError(
            f"Invalid novel slug {slug!r}: use lowercase letters, digits and single hyphens "
            f"(at most {_MAX_SLUG} characters)."
        )
    return slug


# ── chapter label ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ChapterLabel:
    """The chapter number a source *displays*, as canonical non-negative decimal text.

    Up to 7 integer and 6 fractional digits. Leading zeros of the integer part are dropped;
    fractional digits are kept exactly (``"627.10"`` stays distinct from ``"627.1"``).
    """

    text: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", self._canonical(self.text))

    @staticmethod
    def _canonical(value: object) -> str:
        if not isinstance(value, str):
            raise SourceIdentityError(f"Chapter label must be text, got {value!r}.")
        match = _LABEL_RE.fullmatch(value)
        if match is None:
            raise SourceIdentityError(
                f"Invalid chapter label {value!r}: expected digits with an optional "
                "fractional part, such as '3664' or '627.2'."
            )
        whole, fraction = match.group(1), match.group(2)
        whole = str(int(whole))
        return whole if fraction is None else f"{whole}.{fraction}"

    @classmethod
    def parse(cls, value: object) -> "ChapterLabel":
        """Build a label from text, an ``int`` or a ``float`` (never a ``bool``)."""
        if isinstance(value, bool):
            raise SourceIdentityError("Chapter label cannot be a bool.")
        if isinstance(value, int):
            if value < 0:
                raise SourceIdentityError(f"Chapter label cannot be negative: {value!r}.")
            return cls(str(value))
        if isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")) or value < 0:
                raise SourceIdentityError(f"Invalid chapter label {value!r}.")
            if value.is_integer():
                return cls(str(int(value)))
            return cls(format(Decimal(repr(value)), "f"))
        if isinstance(value, str):
            return cls(value)
        raise SourceIdentityError(f"Invalid chapter label {value!r}.")

    @classmethod
    def from_index(cls, index: int | float) -> "ChapterLabel":
        """The label of a legacy ``ChapterMeta.index`` (an integral float collapses to int)."""
        return cls.parse(index)

    @property
    def value(self) -> Decimal:
        return Decimal(self.text)

    @property
    def sort_key(self) -> tuple[Decimal, str]:
        """Numeric order; ties (``1.1`` vs ``1.10``) fall back to the text."""
        return (self.value, self.text)

    def to_index(self) -> int | float:
        """Legacy ``ChapterMeta.index`` form: ``int`` when whole, else ``float``."""
        return int(self.value) if self.value == self.value.to_integral_value() else float(self.value)

    def __str__(self) -> str:
        return self.text


# ── URL checks ───────────────────────────────────────────────────────────────
def _split_https(url: object, what: str):
    """Parse ``url`` as a plain https URL (no userinfo/port/odd characters)."""
    if not isinstance(url, str) or not url:
        raise SourceIdentityError(f"{what} must be a non-empty string, got {url!r}.")
    if _UNSAFE_URL_CHARS.search(url):
        raise SourceIdentityError(f"{what} contains whitespace, control or backslash characters: {url!r}.")
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise SourceIdentityError(f"{what} is not a valid URL ({exc}): {url!r}.") from exc
    if parts.scheme != "https":
        raise SourceIdentityError(f"{what} must use https, got {url!r}.")
    netloc = parts.netloc
    if not netloc or "@" in netloc or ":" in netloc:
        raise SourceIdentityError(f"{what} must be a plain host with no user or port: {url!r}.")
    return parts


def _check_path(path: str, what: str, url: str) -> None:
    if not path or path == "/":
        raise SourceIdentityError(f"{what} needs a path below the host: {url!r}.")
    body = path[:-1] if path.endswith("/") else path  # one trailing slash is allowed
    for segment in body.split("/")[1:]:
        decoded = unquote(segment)
        if segment in ("", ".", "..") or decoded in (".", "..") or "/" in decoded or "\\" in decoded:
            raise SourceIdentityError(f"{what} has an unsafe path segment: {url!r}.")


def _supported_host(host: str) -> frozenset[str]:
    try:
        return SUPPORTED_HOSTS[host]
    except KeyError:
        known = ", ".join(sorted(SUPPORTED_HOSTS))
        raise SourceIdentityError(
            f"Unsupported website host {host!r}; supported hosts: {known}."
        ) from None


def _normalise_origin(origin: object) -> str:
    parts = _split_https(origin, "Origin")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise SourceIdentityError(f"Origin must be just scheme and host, got {origin!r}.")
    return f"https://{parts.netloc.lower()}"


def _normalise_index_url(index_url: object, host: str) -> str:
    parts = _split_https(index_url, "Index URL")
    if parts.netloc.lower() != host:
        raise SourceIdentityError(
            f"Index URL host {parts.netloc.lower()!r} does not match the source host {host!r}."
        )
    if parts.query or parts.fragment:
        raise SourceIdentityError(f"Index URL must not carry a query or fragment: {index_url!r}.")
    _check_path(parts.path, "Index URL", index_url)  # type: ignore[arg-type]
    return f"https://{host}{parts.path.rstrip('/')}"


def _normalise_page_url(url: object, host: str) -> str:
    parts = _split_https(url, "Chapter URL")
    if parts.netloc.lower() != host:
        raise SourceIdentityError(
            f"Chapter URL host {parts.netloc.lower()!r} does not match the source host {host!r}."
        )
    _check_path(parts.path, "Chapter URL", url)  # type: ignore[arg-type]
    query = f"?{parts.query}" if parts.query else ""
    return f"https://{host}{parts.path}{query}"  # fragment dropped: not part of the page


def _normalise_identifiers(raw: object) -> tuple[tuple[str, str], ...]:
    if isinstance(raw, str) or raw is None:
        raise SourceIdentityError(f"Source identifiers must be a mapping of pairs, got {raw!r}.")
    try:
        pairs = list(raw.items()) if isinstance(raw, Mapping) else list(raw)  # type: ignore[union-attr]
    except TypeError:
        raise SourceIdentityError(f"Source identifiers must be a mapping of pairs, got {raw!r}.") from None
    cleaned: dict[str, str] = {}
    for pair in pairs:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise SourceIdentityError(f"Source identifier must be a (key, value) pair, got {pair!r}.")
        key, value = pair
        if not isinstance(key, str) or not _IDENT_KEY_RE.fullmatch(key):
            raise SourceIdentityError(f"Invalid source identifier key {key!r}.")
        if (
            not isinstance(value, str)
            or not value
            or len(value) > _MAX_IDENT_VALUE
            or _CONTROL_CHARS.search(value)
        ):
            raise SourceIdentityError(f"Invalid value for source identifier {key!r}.")
        if key in cleaned and cleaned[key] != value:
            raise SourceIdentityError(f"Source identifier {key!r} given two different values.")
        cleaned[key] = value
    return tuple(sorted(cleaned.items()))


# ── source identity ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SourceIdentity:
    """One novel on one website origin through one adapter implementation.

    ``novel_slug`` is the canonical (catalog) slug, which can differ from the website's own
    slug (the website's lives in ``index_url``). ``identifiers`` holds source-specific ids
    such as a ``book_id``. Every field is validated and normalised on construction.
    """

    novel_slug: str
    adapter_key: str
    origin: str
    index_url: str
    identifiers: tuple = ()

    def __post_init__(self) -> None:
        validate_novel_slug(self.novel_slug)
        origin = _normalise_origin(self.origin)
        host = origin[len("https://"):]
        allowed = _supported_host(host)
        if not isinstance(self.adapter_key, str) or self.adapter_key not in allowed:
            raise SourceIdentityError(
                f"Source adapter {self.adapter_key!r} is not expected for host {host!r} "
                f"(expected one of: {', '.join(sorted(allowed))})."
            )
        object.__setattr__(self, "origin", origin)
        object.__setattr__(self, "index_url", _normalise_index_url(self.index_url, host))
        object.__setattr__(self, "identifiers", _normalise_identifiers(self.identifiers))

    @property
    def host(self) -> str:
        return self.origin[len("https://"):]

    @property
    def site_key(self) -> str:
        """Directory-safe key for the website (its host). Distinct for ``.net`` and ``.com``."""
        return self.host

    def same_source(self, other: "SourceIdentity") -> bool:
        return self == other

    def to_json(self) -> dict:
        return {
            "novel_slug": self.novel_slug,
            "adapter_key": self.adapter_key,
            "origin": self.origin,
            "index_url": self.index_url,
            "identifiers": dict(self.identifiers),
        }

    @classmethod
    def from_json(cls, payload: object) -> "SourceIdentity":
        keys = {"novel_slug", "adapter_key", "origin", "index_url", "identifiers"}
        if not isinstance(payload, dict) or set(payload) != keys:
            raise SourceIdentityError("Source identity must be an object with exactly: " + ", ".join(sorted(keys)) + ".")
        identifiers = payload["identifiers"]
        if not isinstance(identifiers, dict):
            raise SourceIdentityError("Source identifiers must be an object.")
        return cls(
            novel_slug=payload["novel_slug"],
            adapter_key=payload["adapter_key"],
            origin=payload["origin"],
            index_url=payload["index_url"],
            identifiers=identifiers,
        )

    def fingerprint(self) -> str:
        """Stable 16-hex-digit digest of the whole identity (not of any chapter data)."""
        canonical = json.dumps(self.to_json(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode("ascii")).hexdigest()[:16]

    @classmethod
    def from_site_spec(cls, spec: object) -> "SourceIdentity":
        """Identity of a catalog row (duck-typed ``SiteSpec``; the catalog is not imported).

        The origin is the row's ``base_url`` or, for rows without one, the origin of its
        index ``url``. A ``base_url`` that disagrees with ``url`` is rejected.
        """
        url = getattr(spec, "url", "")
        base = getattr(spec, "base_url", "") or ""
        if base:
            origin = base
        else:
            parts = _split_https(url, "Index URL")
            origin = f"https://{parts.netloc}"
        book_id = getattr(spec, "book_id", None)
        return cls(
            novel_slug=getattr(spec, "novel_slug", None),
            adapter_key=getattr(spec, "adapter_key", None),
            origin=origin,
            index_url=url,
            identifiers={"book_id": book_id} if book_id else {},
        )

    def chapter_identity(
        self,
        page_url: object,
        label: ChapterLabel,
        *,
        slug: Optional[int] = None,
        source_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> "ChapterIdentity":
        """Validate a chapter page against this source and return its identity."""
        if not isinstance(label, ChapterLabel):
            raise SourceIdentityError("Chapter label must be a ChapterLabel.")
        if slug is not None and (isinstance(slug, bool) or not isinstance(slug, int) or slug < 0):
            raise SourceIdentityError(f"Chapter slug must be a non-negative integer, got {slug!r}.")
        if source_id is not None and (not isinstance(source_id, str) or not source_id):
            raise SourceIdentityError(f"Chapter source_id must be a non-empty string, got {source_id!r}.")
        if title is not None and not isinstance(title, str):
            raise SourceIdentityError(f"Chapter title must be text, got {title!r}.")
        return ChapterIdentity(
            page_url=_normalise_page_url(page_url, self.host),
            label=label,
            slug=slug,
            source_id=source_id,
            title=title,
        )


# ── chapter identity ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ChapterIdentity:
    """One chapter page of a source. Equality and hashing use the page URL alone: the
    displayed ``label`` and the other fields are evidence, not identity. Build instances
    with ``SourceIdentity.chapter_identity``, which validates them against the source."""

    page_url: str
    label: ChapterLabel = field(compare=False)
    slug: Optional[int] = field(default=None, compare=False)
    source_id: Optional[str] = field(default=None, compare=False)
    title: Optional[str] = field(default=None, compare=False)

    @property
    def page_key(self) -> str:
        return self.page_url

    @classmethod
    def from_meta(cls, source: SourceIdentity, meta: object) -> "ChapterIdentity":
        """Identity of a legacy ``ChapterMeta`` (duck-typed) under ``source``."""
        return source.chapter_identity(
            getattr(meta, "url", None),
            ChapterLabel.from_index(getattr(meta, "index", None)),
            slug=getattr(meta, "slug", None),
            source_id=getattr(meta, "source_id", None),
            title=getattr(meta, "title", None),
        )

    def to_json(self) -> dict:
        return {
            "page_url": self.page_url,
            "label": self.label.text,
            "slug": self.slug,
            "source_id": self.source_id,
            "title": self.title,
        }

    @classmethod
    def from_json(cls, source: SourceIdentity, payload: object) -> "ChapterIdentity":
        keys = {"page_url", "label", "slug", "source_id", "title"}
        if not isinstance(payload, dict) or set(payload) != keys:
            raise SourceIdentityError("Chapter identity must be an object with exactly: " + ", ".join(sorted(keys)) + ".")
        return source.chapter_identity(
            payload["page_url"],
            ChapterLabel.parse(payload["label"]),
            slug=payload["slug"],
            source_id=payload["source_id"],
            title=payload["title"],
        )


def duplicate_labels(chapters: Iterable[ChapterIdentity]) -> dict[ChapterLabel, list[ChapterIdentity]]:
    """Labels shown by more than one distinct page, in first-seen order. These are real
    (Supreme Magus shows 3664 twice): report them, never merge or drop them."""
    grouped: dict[ChapterLabel, list[ChapterIdentity]] = {}
    for chapter in chapters:
        grouped.setdefault(chapter.label, []).append(chapter)
    return {label: group for label, group in grouped.items() if len(group) > 1}


def ensure_distinct_pages(chapters: Iterable[ChapterIdentity]) -> None:
    """Raise if one page URL appears twice: that is a contradiction, not a duplicate number."""
    seen: set[str] = set()
    for chapter in chapters:
        if chapter.page_key in seen:
            raise SourceIdentityError(f"Chapter page appears more than once: {chapter.page_url}")
        seen.add(chapter.page_key)
