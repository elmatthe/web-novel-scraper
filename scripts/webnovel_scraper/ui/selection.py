"""Website-first selection: which websites exist, which novels each offers, and which
catalog row a (website, novel) choice means. No Tk, no I/O, no scraping.

The catalog is a flat list of :class:`~webnovel_scraper.models.SiteSpec` rows, each one a
novel on one site. Section 1 of the window now asks for the *website* first and then for a
novel that website supports. This module derives that two-level view from the catalog and
adds nothing to it.

A website is identified by its **host** (``freewebnovel.com``), taken from the row's
``base_url`` or, where a row has none, its ``url``. That is deliberately *not* the row's
``adapter_key``: the adapter is an implementation detail and one adapter may serve several
websites (a future ``.net`` and ``.com`` edition of the same site, for example, differ in
host and may well share an adapter). Two rows are the same website only if their hosts
match, whatever adapters they use. The choice is resolved back to a catalog row here, and
Start passes that row's ``novel_slug`` and ``adapter_key`` to the pipeline exactly as before.

Only enabled rows can be chosen: a website with no enabled row is listed as "coming soon"
but cannot be selected, and the novel list of a website holds only novels it can run, so a
disabled combination cannot reach Start.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence
from urllib.parse import urlsplit

from ..models import SiteSpec

__all__ = [
    "Website", "NovelChoice", "website_key", "website_key_for_host", "websites", "novels_for",
    "resolve", "settle",
]


@dataclass(frozen=True)
class Website:
    """One website as the Website menu lists it."""

    key: str          # the host: the stable identifier (also what preferences store)
    label: str        # menu text; carries the host only if two sites share a display name
    enabled: bool     # at least one catalog row for it can run


@dataclass(frozen=True)
class NovelChoice:
    """One novel a website offers, as the Novel list shows it."""

    slug: str
    title: str


def website_key(spec: SiteSpec) -> str:
    """The website a catalog row belongs to: its host, lower-case, without a leading ``www.``."""
    host = urlsplit(spec.base_url or spec.url).hostname or ""
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or spec.adapter_key   # a row with no usable URL still gets a stable key


def website_key_for_host(host: str) -> str:
    """The website key of a bare host (as a run's source records it): lower-case, no ``www.``."""
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def websites(specs: Iterable[SiteSpec]) -> list[Website]:
    """Every website in the catalog, in the order its first row appears."""
    order: list[str] = []
    names: dict[str, str] = {}
    enabled: dict[str, bool] = {}
    for spec in specs:
        key = website_key(spec)
        if key not in names:
            order.append(key)
            names[key] = spec.display_name
            enabled[key] = False
        enabled[key] = enabled[key] or spec.enabled
    # Two different hosts that share a display name are told apart by their host.
    counts: dict[str, int] = {}
    for key in order:
        counts[names[key]] = counts.get(names[key], 0) + 1
    return [
        Website(key, names[key] if counts[names[key]] == 1 else f"{names[key]} ({key})",
                enabled[key])
        for key in order
    ]


def novels_for(key: str, specs: Iterable[SiteSpec]) -> list[NovelChoice]:
    """The novels website *key* can run, in catalog order (enabled rows only)."""
    seen: list[str] = []
    choices: list[NovelChoice] = []
    for spec in specs:
        if website_key(spec) == key and spec.enabled and spec.novel_slug not in seen:
            seen.append(spec.novel_slug)
            choices.append(NovelChoice(spec.novel_slug, spec.novel_title))
    return choices


def resolve(key: str, slug: str, specs: Iterable[SiteSpec]) -> SiteSpec | None:
    """The enabled catalog row for this website and novel, or ``None`` if there is none."""
    for spec in specs:
        if website_key(spec) == key and spec.novel_slug == slug and spec.enabled:
            return spec
    return None


def settle(key: str, slug: str, specs: Sequence[SiteSpec]) -> tuple[str, str] | None:
    """The nearest valid ``(website, novel)`` to the requested pair, or ``None`` if the
    catalog has nothing runnable at all.

    A requested website that cannot run (unknown, removed or disabled) gives way to the
    first one that can. Within the website, the requested novel is kept if the website
    offers it, otherwise the website's first novel is used. This is how a stored
    preference, a website change and a catalog edit all end on a combination that can run.
    """
    runnable = [w for w in websites(specs) if w.enabled and novels_for(w.key, specs)]
    if not runnable:
        return None
    chosen = next((w for w in runnable if w.key == key), runnable[0])
    novels = novels_for(chosen.key, specs)
    novel = next((n for n in novels if n.slug == slug), novels[0])
    return chosen.key, novel.slug
