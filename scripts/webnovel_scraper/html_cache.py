"""Validated, provenance-carrying on-disk HTML cache (v0.3.0 Checkpoint 03.2b, plan §9.6).

Layout: ``<root>/<host>/<novel_slug>/<key>.html`` plus a sidecar ``<key>.json``. ``key`` is the
first 24 hex digits of SHA-256 over the exact page URL (the same key the pre-0.3.0 cache used),
and ``<host>`` is the page URL's own host, so ``freewebnovel.net`` and ``freewebnovel.com`` pages,
and two novels on one host, can never share a file.

The sidecar records where and how the bytes were obtained::

    {"schema_version": 1, "kind": "html-cache-entry", "url": ..., "final_url": ..., "host": ...,
     "novel_slug": ..., "fetched_utc": ..., "byte_len": N, "sha256": ..., "classification": "valid",
     "strategy": "camoufox", "origin": "network" | "legacy-copy", "app_version": "0.3.0"}

A hit needs a matching, well-formed sidecar *and* HTML whose length and SHA-256 match it, decodes
as UTF-8, is not mis-decoded binary, is not a Cloudflare challenge and looks complete. Anything
else is a miss, reported once, never served: an HTML file without its sidecar (an interrupted
write), a sidecar for another URL, host or novel, a truncated or edited file. A miss simply leads
to a fresh fetch, whose result replaces the bad pair, so a bad entry costs one request, not a loop.

Writes publish the HTML and then the sidecar, each through a temp file in the same folder,
``fsync`` and ``os.replace``; a crash between the two leaves a pair that fails the hash check.

What is cached is decided by the caller: :class:`~webnovel_scraper.request_manager.RequestManager`
caches only pages that came back clean from the host that was asked, and an adapter that later
finds a cached page unusable (no chapter body, the wrong chapter) calls :meth:`HtmlCache.invalidate`,
which keeps the bytes aside as ``<key>.invalid.html`` (one per key, for diagnosis) and stops them
being served or re-imported. Chapter pages never expire on their own; table-of-contents pages are
never cached at all (the adapters fetch them fresh, so a new chapter is always seen).

Legacy migration: the pre-0.3.0 cache lived at ``<root>/<novel_slug>/<key>.html`` (same key, no
host, no sidecar). On a miss, that file is offered as a candidate after the same content checks and
*copied* into the new layout with ``origin: "legacy-copy"``; the old file is never moved, changed
or deleted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

from .cloudflare_detection import is_cloudflare_challenge

SCHEMA_VERSION = 1
ENTRY_KIND = "html-cache-entry"
APP_VERSION = "0.3.0"
ORIGIN_NETWORK = "network"
ORIGIN_LEGACY = "legacy-copy"
# A real chapter page is tens of kilobytes; anything near this is not a page we want to keep.
MAX_ENTRY_BYTES = 20 * 1024 * 1024
_SIDECAR_KEYS = frozenset({
    "schema_version", "kind", "url", "final_url", "host", "novel_slug", "fetched_utc",
    "byte_len", "sha256", "classification", "strategy", "origin", "app_version",
})
_HEX64 = re.compile(r"[0-9a-f]{64}")
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?")
# A served page always ends its document; a body cut off mid-transfer usually does not.
_DOCUMENT_END = re.compile(rb"</(?:html|body)\s*>", re.IGNORECASE)

# Same signature as request_manager's garble detector (kept local to avoid an import cycle).
_REPLACEMENT_CHAR = "�"


def cache_key_for(url: str) -> str:
    """The cache key of a page URL (the pre-0.3.0 key, so legacy files map one to one)."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


def page_host(url: str) -> str:
    """The lower-case host of ``url`` (``""`` when it has none or is not a URL string)."""
    if not isinstance(url, str):
        return ""
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def looks_garbled(html: str) -> bool:
    """Mis-decoded binary (an undecodable content-encoding): dominated by U+FFFD."""
    if not html or len(html) < 200:
        return False
    bad = html.count(_REPLACEMENT_CHAR)
    return bad >= 32 and bad / len(html) > 0.02


def content_problem(html: str) -> Optional[str]:
    """Why ``html`` must not be cached or served from the cache, or ``None`` when it may be."""
    if not html or not html.strip():
        return "the page is empty"
    if looks_garbled(html):
        return "the page is undecodable (garbled bytes)"
    if is_cloudflare_challenge(html):
        return "the page is a Cloudflare challenge"
    data = html.encode("utf-8", errors="surrogatepass")
    if len(data) > MAX_ENTRY_BYTES:
        return "the page is too large to keep"
    if not _DOCUMENT_END.search(data):
        return "the page looks truncated (no closing </body> or </html>)"
    return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _same_page(requested: str, final: Optional[str]) -> bool:
    """Whether a fetch ended on the page that was asked for (same host and path; a trailing
    slash or fragment does not matter). A redirect elsewhere is never cached under the key."""
    if not final:
        return True
    try:
        a, b = urlsplit(requested), urlsplit(final)
    except ValueError:
        return False
    return (
        (a.hostname or "").lower() == (b.hostname or "").lower()
        and a.path.rstrip("/") == b.path.rstrip("/")
    )


def _write_atomic(path: Path, data: bytes) -> None:
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


class HtmlCache:
    """One novel's validated HTML cache under ``root`` (see the module docstring).

    ``site_key``, when given, is the only host this cache will hold (a source's pages never land
    under another host). ``legacy_root`` is where the pre-0.3.0 ``<novel_slug>/<key>.html`` files
    live (``None`` disables the legacy lookup).
    """

    def __init__(
        self,
        root: Path,
        novel_slug: str,
        *,
        site_key: Optional[str] = None,
        legacy_root: Optional[Path] = None,
        log: Optional[Callable[[str], None]] = None,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self.root = Path(root)
        self.novel_slug = str(novel_slug)
        self.site_key = site_key.lower() if site_key else None
        self.legacy_root = Path(legacy_root) if legacy_root is not None else None
        self._log = log or (lambda _m: None)
        self._clock = clock
        self._reported: set[tuple[str, str]] = set()

    # ── paths ────────────────────────────────────────────────────────────────
    def _host_for(self, url: str) -> Optional[str]:
        host = page_host(url)
        if not host or not _HOST.fullmatch(host) or not _SLUG.fullmatch(self.novel_slug):
            return None
        if self.site_key is not None and host != self.site_key:
            return None
        return host

    def folder_for(self, url: str) -> Optional[Path]:
        host = self._host_for(url)
        return None if host is None else self.root / host / self.novel_slug

    def html_path(self, url: str) -> Optional[Path]:
        folder = self.folder_for(url)
        return None if folder is None else folder / f"{cache_key_for(url)}.html"

    def sidecar_path(self, url: str) -> Optional[Path]:
        folder = self.folder_for(url)
        return None if folder is None else folder / f"{cache_key_for(url)}.json"

    def invalid_path(self, url: str) -> Optional[Path]:
        folder = self.folder_for(url)
        return None if folder is None else folder / f"{cache_key_for(url)}.invalid.html"

    def legacy_path(self, url: str) -> Optional[Path]:
        if self.legacy_root is None or not _SLUG.fullmatch(self.novel_slug):
            return None
        return self.legacy_root / self.novel_slug / f"{cache_key_for(url)}.html"

    # ── reading ──────────────────────────────────────────────────────────────
    def _report(self, url: str, reason: str) -> None:
        key = (url, reason)
        if key not in self._reported:
            self._reported.add(key)
            self._log(f"  cache entry for {url} not used: {reason}; fetching the page again.")

    def _sidecar_problem(self, url: str, raw: object, data: bytes) -> Optional[str]:
        if not isinstance(raw, dict) or set(raw) != _SIDECAR_KEYS:
            return "its provenance record is malformed"
        if raw.get("schema_version") != SCHEMA_VERSION or type(raw.get("schema_version")) is not int:
            return "its provenance record has an unknown schema version"
        if raw.get("kind") != ENTRY_KIND:
            return "its provenance record is not a cache entry"
        if raw.get("url") != url:
            return "its provenance record belongs to another page"
        if raw.get("host") != page_host(url) or (self.site_key and raw.get("host") != self.site_key):
            return "its provenance record belongs to another website"
        if raw.get("novel_slug") != self.novel_slug:
            return "its provenance record belongs to another novel"
        if raw.get("classification") != "valid" or raw.get("origin") not in (ORIGIN_NETWORK, ORIGIN_LEGACY):
            return "its provenance record does not mark it valid"
        if type(raw.get("byte_len")) is not int or raw["byte_len"] != len(data):
            return "the saved page is truncated or was changed (length mismatch)"
        digest = raw.get("sha256")
        if not isinstance(digest, str) or not _HEX64.fullmatch(digest):
            return "its provenance record has no valid checksum"
        if hashlib.sha256(data).hexdigest() != digest:
            return "the saved page is corrupt or was changed (checksum mismatch)"
        return None

    def read(self, url: str) -> Optional[tuple[str, str]]:
        """``(html, origin)`` for a valid entry of ``url``, or ``None`` (a miss, logged once)."""
        html_path, sidecar_path = self.html_path(url), self.sidecar_path(url)
        if html_path is None or sidecar_path is None:
            return None
        if not html_path.is_file():
            return self._read_legacy(url)
        try:
            if html_path.stat().st_size > MAX_ENTRY_BYTES:
                self._report(url, "the saved page is too large")
                return None
            data = html_path.read_bytes()
        except OSError as exc:
            self._report(url, f"it could not be read ({exc})")
            return None
        try:
            raw = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._report(url, "its provenance record is missing (an interrupted save)")
            return None
        except (OSError, ValueError) as exc:
            self._report(url, f"its provenance record is unreadable ({exc})")
            return None
        problem = self._sidecar_problem(url, raw, data)
        if problem is None:
            try:
                html = data.decode("utf-8")
            except UnicodeDecodeError:
                problem = "the saved page is not valid UTF-8"
            else:
                problem = content_problem(html)
        if problem is not None:
            self._report(url, problem)
            return None
        return html, raw["origin"]

    def _read_legacy(self, url: str) -> Optional[tuple[str, str]]:
        legacy = self.legacy_path(url)
        invalid = self.invalid_path(url)
        if legacy is None or not legacy.is_file() or (invalid is not None and invalid.exists()):
            return None
        try:
            if legacy.stat().st_size > MAX_ENTRY_BYTES:
                return None
            html = legacy.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        if content_problem(html) is not None:
            return None
        # Copy (never move) into the new layout; the adapter's own checks may still reject it.
        if self.store(url, html, strategy="legacy", origin=ORIGIN_LEGACY):
            self._log(f"  copied the pre-0.3.0 cached page for {url} into the new cache layout.")
        return html, ORIGIN_LEGACY

    # ── writing ──────────────────────────────────────────────────────────────
    def store(
        self,
        url: str,
        html: str,
        *,
        strategy: str,
        final_url: Optional[str] = None,
        origin: str = ORIGIN_NETWORK,
    ) -> bool:
        """Publish ``html`` for ``url`` if it is fit to keep. Returns whether it was stored. Never
        raises: a cache that cannot be written only costs a later re-fetch."""
        html_path, sidecar_path = self.html_path(url), self.sidecar_path(url)
        if html_path is None or sidecar_path is None:
            return False
        if not _same_page(url, final_url):
            self._log(f"  not caching {url}: the site answered from {final_url}.")
            return False
        problem = content_problem(html)
        if problem is not None:
            self._log(f"  not caching {url}: {problem}.")
            return False
        data = html.encode("utf-8", errors="surrogatepass")
        sidecar = {
            "schema_version": SCHEMA_VERSION,
            "kind": ENTRY_KIND,
            "url": url,
            "final_url": final_url or url,
            "host": page_host(url),
            "novel_slug": self.novel_slug,
            "fetched_utc": self._clock(),
            "byte_len": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "classification": "valid",
            "strategy": str(strategy),
            "origin": origin,
            "app_version": APP_VERSION,
        }
        try:
            html_path.parent.mkdir(parents=True, exist_ok=True)
            _write_atomic(html_path, data)
            _write_atomic(
                sidecar_path, (json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            )
        except OSError as exc:
            self._log(f"  (cache write failed for {url}: {exc})")
            return False
        invalid = self.invalid_path(url)
        if invalid is not None and invalid.exists():
            try:
                invalid.unlink()   # a newer valid copy supersedes the one set aside
            except OSError:
                pass
        return True

    def invalidate(self, url: str, reason: str) -> bool:
        """Stop serving ``url``'s entry: its HTML is kept aside as ``<key>.invalid.html`` (replacing
        an older one) and its sidecar removed. Returns whether an entry was set aside."""
        html_path, sidecar_path, invalid = (
            self.html_path(url), self.sidecar_path(url), self.invalid_path(url)
        )
        if html_path is None or sidecar_path is None or invalid is None:
            return False
        moved = False
        try:
            if html_path.exists():
                os.replace(html_path, invalid)
                moved = True
            elif self.legacy_path(url) is not None and self.legacy_path(url).is_file():
                # Remember that the legacy copy is unusable without touching it.
                invalid.parent.mkdir(parents=True, exist_ok=True)
                invalid.write_bytes(b"")
                moved = True
            if sidecar_path.exists():
                sidecar_path.unlink()
        except OSError as exc:
            self._log(f"  (could not set aside the cached page for {url}: {exc})")
            return False
        if moved:
            self._log(f"  cached page for {url} set aside: {reason}.")
        return moved
