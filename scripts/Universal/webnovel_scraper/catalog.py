"""The Novel/Site catalog — the single source of truth for the GUI and pipeline.

Section 1 of the implementation plan encoded as pure data. Adding a site later
(or enabling a stub) is a one-line edit here: add/flip a `SiteSpec` row. There
is no per-novel if/elif logic anywhere — every lookup filters this one list.
No I/O at import time.
"""

from __future__ import annotations

from .models import SiteSpec

# Catalog rows in display order. The first time a novel_slug appears fixes its
# position in the Novel dropdown.
_CATALOG: list[SiteSpec] = [
    SiteSpec(
        novel_slug="shadow-slave",
        novel_title="Shadow Slave",
        adapter_key="freewebnovel",
        display_name="Free Web Novel",
        enabled=True,
        url="https://freewebnovel.com/novel/shadow-slave",
        base_url="https://freewebnovel.com",
    ),
    SiteSpec(
        novel_slug="shadow-slave",
        novel_title="Shadow Slave",
        adapter_key="empire_novel",
        display_name="Empire Novel",
        enabled=False,
        url="https://www.empirenovel.com/novel/shadow-slave",
    ),
    SiteSpec(
        novel_slug="shadow-slave",
        novel_title="Shadow Slave",
        adapter_key="telegraph",
        display_name="Telegraph",
        enabled=False,
        url="https://telegra.ph/List-of-all-volumes-01-08",
    ),
    SiteSpec(
        novel_slug="the-noble-queen",
        novel_title="The Noble Queen",
        adapter_key="novel_bin",
        display_name="Novel Bin",
        enabled=False,
        url="https://novelbin.com/b/the-noble-queen-a-shadow-slave-fanfic",
    ),
    SiteSpec(
        novel_slug="the-noble-queen",
        novel_title="The Noble Queen",
        adapter_key="webnovel_dynamic",
        display_name="WebNovel (Dynamic)",
        enabled=True,
        url="https://dynamic.webnovel.com/story/28684090500376805",
        book_id="28684090500376805",
        base_url="https://dynamic.webnovel.com",
    ),
    SiteSpec(
        novel_slug="reverend-insanity",
        novel_title="Reverend Insanity",
        adapter_key="freewebnovel",
        display_name="Free Web Novel",
        enabled=True,
        url="https://freewebnovel.com/novel/reverend-insanity",
        base_url="https://freewebnovel.com",
    ),
    SiteSpec(
        novel_slug="renegade-immortal",
        novel_title="Renegade Immortal",
        adapter_key="freewebnovel",
        display_name="Free Web Novel",
        enabled=True,
        url="https://freewebnovel.com/novel/renegade-immortal",
        base_url="https://freewebnovel.com",
    ),
    SiteSpec(
        novel_slug="supreme-magus",
        novel_title="Supreme Magus",
        adapter_key="freewebnovel",
        display_name="Free Web Novel",
        enabled=True,
        url="https://freewebnovel.com/novel/supreme-magus-novel",
        base_url="https://freewebnovel.com",
    ),
]


def all_specs() -> list[SiteSpec]:
    """Every catalog row, in display order (defensive copy)."""
    return list(_CATALOG)


def all_novel_slugs() -> list[str]:
    """Unique novel slugs in display (first-seen) order."""
    seen: list[str] = []
    for spec in _CATALOG:
        if spec.novel_slug not in seen:
            seen.append(spec.novel_slug)
    return seen


def get_adapters_for_novel(novel_slug: str) -> list[SiteSpec]:
    """All site rows for a novel (enabled + disabled), in catalog order."""
    return [s for s in _CATALOG if s.novel_slug == novel_slug]


def get_enabled_adapters_for_novel(novel_slug: str) -> list[SiteSpec]:
    """Only the enabled site rows for a novel, in catalog order."""
    return [s for s in _CATALOG if s.novel_slug == novel_slug and s.enabled]


def get_spec(novel_slug: str, adapter_key: str) -> SiteSpec:
    """Resolve a single row. Raises KeyError if no row matches."""
    for spec in _CATALOG:
        if spec.novel_slug == novel_slug and spec.adapter_key == adapter_key:
            return spec
    raise KeyError(
        f"No catalog entry for novel {novel_slug!r} on site {adapter_key!r}."
    )
