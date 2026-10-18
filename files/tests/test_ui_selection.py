"""v0.3.0 Checkpoint 02.3: the website-first selection model (pure, no Tk).

The model derives a two-level Website -> Novel view from the unchanged catalog. These tests
pin its rules, and that a website is identified by its host and never by its adapter, so a
later ``.net`` / ``.com`` pair can share an adapter yet stay two choices.
"""

from __future__ import annotations

import pytest

from webnovel_scraper import catalog
from webnovel_scraper.models import SiteSpec
from webnovel_scraper.ui import selection


def _spec(slug, adapter, name, url, *, enabled=True, base_url="", title=None):
    return SiteSpec(
        novel_slug=slug, novel_title=title or slug.replace("-", " ").title(),
        adapter_key=adapter, display_name=name, enabled=enabled, url=url, base_url=base_url,
    )


# ── The real catalog, unchanged ──────────────────────────────────────────────
def test_the_catalog_is_unchanged_and_has_no_net_or_com_pairs_yet() -> None:
    """Checkpoint 02.3 must not add catalog entries: separate .net/.com rows are validated
    one by one in Checkpoint 04."""
    rows = [(s.novel_slug, s.adapter_key, s.enabled) for s in catalog.all_specs()]
    assert rows == [
        ("shadow-slave", "freewebnovel", True),
        ("shadow-slave", "empire_novel", False),
        ("shadow-slave", "telegraph", False),
        ("the-noble-queen", "novel_bin", False),
        ("the-noble-queen", "webnovel_dynamic", True),
        ("reverend-insanity", "freewebnovel", True),
        ("renegade-immortal", "freewebnovel", True),
        ("supreme-magus", "freewebnovel", True),
    ]
    hosts = {selection.website_key(s) for s in catalog.all_specs()}
    assert hosts == {"freewebnovel.com", "empirenovel.com", "telegra.ph", "novelbin.com",
                     "dynamic.webnovel.com"}


def test_websites_come_from_the_catalog_in_first_seen_order() -> None:
    sites = selection.websites(catalog.all_specs())
    assert [(w.key, w.label, w.enabled) for w in sites] == [
        ("freewebnovel.com", "Free Web Novel", True),
        ("empirenovel.com", "Empire Novel", False),
        ("telegra.ph", "Telegraph", False),
        ("novelbin.com", "Novel Bin", False),
        ("dynamic.webnovel.com", "WebNovel (Dynamic)", True),
    ]


def test_each_website_offers_only_the_novels_it_can_run() -> None:
    specs = catalog.all_specs()
    assert [n.slug for n in selection.novels_for("freewebnovel.com", specs)] == [
        "shadow-slave", "reverend-insanity", "renegade-immortal", "supreme-magus"]
    assert [n.title for n in selection.novels_for("freewebnovel.com", specs)] == [
        "Shadow Slave", "Reverend Insanity", "Renegade Immortal", "Supreme Magus"]
    assert [n.slug for n in selection.novels_for("dynamic.webnovel.com", specs)] == [
        "the-noble-queen"]
    # Websites that are "coming soon" offer nothing: their rows are disabled.
    for key in ("empirenovel.com", "telegra.ph", "novelbin.com"):
        assert selection.novels_for(key, specs) == []
    assert selection.novels_for("unknown.example", specs) == []


def test_resolve_returns_the_existing_catalog_row_or_nothing() -> None:
    specs = catalog.all_specs()
    row = selection.resolve("freewebnovel.com", "supreme-magus", specs)
    assert row is catalog.get_spec("supreme-magus", "freewebnovel")
    assert selection.resolve("dynamic.webnovel.com", "the-noble-queen", specs) is (
        catalog.get_spec("the-noble-queen", "webnovel_dynamic"))
    # A novel the website does not offer, a disabled combination and unknown names: None.
    assert selection.resolve("freewebnovel.com", "the-noble-queen", specs) is None
    assert selection.resolve("empirenovel.com", "shadow-slave", specs) is None
    assert selection.resolve("novelbin.com", "the-noble-queen", specs) is None
    assert selection.resolve("nowhere.example", "shadow-slave", specs) is None
    assert selection.resolve("", "", specs) is None


def test_every_resolvable_choice_is_an_enabled_catalog_row() -> None:
    specs = catalog.all_specs()
    reachable = 0
    for site in selection.websites(specs):
        for novel in selection.novels_for(site.key, specs):
            row = selection.resolve(site.key, novel.slug, specs)
            assert row is not None and row.enabled
            assert (row.novel_slug, selection.website_key(row)) == (novel.slug, site.key)
            reachable += 1
    assert reachable == len([s for s in specs if s.enabled])


# ── Settling on a valid pair ─────────────────────────────────────────────────
@pytest.mark.parametrize(
    "website, novel, expected",
    [
        ("", "", ("freewebnovel.com", "shadow-slave")),                       # fresh install
        ("freewebnovel.com", "supreme-magus", ("freewebnovel.com", "supreme-magus")),
        ("dynamic.webnovel.com", "the-noble-queen", ("dynamic.webnovel.com", "the-noble-queen")),
        # Changing website retains the novel if offered, else takes the website's first one.
        ("dynamic.webnovel.com", "shadow-slave", ("dynamic.webnovel.com", "the-noble-queen")),
        ("freewebnovel.com", "the-noble-queen", ("freewebnovel.com", "shadow-slave")),
        # Unavailable, disabled or unknown choices fall back to a supported combination.
        ("empirenovel.com", "shadow-slave", ("freewebnovel.com", "shadow-slave")),
        ("nowhere.example", "supreme-magus", ("freewebnovel.com", "supreme-magus")),
        ("freewebnovel.com", "removed-novel", ("freewebnovel.com", "shadow-slave")),
    ],
)
def test_settle_gives_the_nearest_valid_pair(website, novel, expected) -> None:
    assert selection.settle(website, novel, catalog.all_specs()) == expected


def test_settle_is_none_when_nothing_can_run() -> None:
    disabled = [s for s in catalog.all_specs() if not s.enabled]
    assert selection.settle("freewebnovel.com", "shadow-slave", disabled) is None
    assert selection.settle("", "", []) is None


# ── Website identity is the host, not the adapter ────────────────────────────
def test_two_hosts_sharing_an_adapter_are_two_websites() -> None:
    """Future .net and .com editions may use one adapter yet must stay separate choices."""
    specs = [
        _spec("a-novel", "freewebnovel", "Free Web Novel", "https://freewebnovel.com/novel/a",
              base_url="https://freewebnovel.com"),
        _spec("a-novel", "freewebnovel", "Free Web Novel", "https://freewebnovel.net/novel/a",
              base_url="https://freewebnovel.net"),
    ]
    sites = selection.websites(specs)
    assert [w.key for w in sites] == ["freewebnovel.com", "freewebnovel.net"]
    # The same display name is told apart by host so the two never look identical.
    assert [w.label for w in sites] == [
        "Free Web Novel (freewebnovel.com)", "Free Web Novel (freewebnovel.net)"]
    com = selection.resolve("freewebnovel.com", "a-novel", specs)
    net = selection.resolve("freewebnovel.net", "a-novel", specs)
    assert com is specs[0] and net is specs[1] and com.adapter_key == net.adapter_key


def test_one_host_with_several_adapters_is_one_website() -> None:
    specs = [
        _spec("one", "adapter_a", "Example", "https://example.org/one"),
        _spec("two", "adapter_b", "Example", "https://www.example.org/two"),
    ]
    sites = selection.websites(specs)
    assert [(w.key, w.label) for w in sites] == [("example.org", "Example")]
    assert [n.slug for n in selection.novels_for("example.org", specs)] == ["one", "two"]


def test_website_key_uses_base_url_then_url_and_is_stable() -> None:
    with_base = _spec("n", "x", "X", "https://other.example/novel", base_url="https://Base.Example")
    assert selection.website_key(with_base) == "base.example"
    assert selection.website_key(_spec("n", "x", "X", "https://WWW.Site.Example/p")) == "site.example"
    # No usable URL at all: still a stable, non-empty key.
    assert selection.website_key(_spec("n", "x", "X", "")) == "x"


def test_a_novel_with_a_disabled_row_on_an_enabled_website_is_not_offered() -> None:
    specs = [
        _spec("live", "a", "Site", "https://site.example/live"),
        _spec("soon", "a", "Site", "https://site.example/soon", enabled=False),
    ]
    assert [n.slug for n in selection.novels_for("site.example", specs)] == ["live"]
    assert selection.resolve("site.example", "soon", specs) is None
    assert selection.settle("site.example", "soon", specs) == ("site.example", "live")
