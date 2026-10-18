"""Source identity contract (v0.3.0 Checkpoint 03.0). Offline, pure, no I/O.

A source is a novel on a specific website origin, read through a specific adapter
implementation. ``adapter_key`` alone is never the identity: ``freewebnovel.net`` and
``freewebnovel.com`` share one adapter and must stay two different sources. A chapter's
identity is its source page, never its displayed number (plan §8 C8).
"""

from __future__ import annotations

import json

import pytest

from webnovel_scraper import catalog
from webnovel_scraper.models import ChapterMeta
from webnovel_scraper.source_identity import (
    ChapterIdentity,
    ChapterLabel,
    SourceIdentity,
    SourceIdentityError,
    duplicate_labels,
    ensure_distinct_pages,
    validate_novel_slug,
)

NET = "https://freewebnovel.net"
COM = "https://freewebnovel.com"
LOTM_NET = f"{NET}/lord-of-the-mysteries"
LOTM_COM = f"{COM}/novel/lord-of-the-mysteries"


def fwn(origin: str = COM, index_url: str = LOTM_COM, slug: str = "lord-of-the-mysteries",
        **kw) -> SourceIdentity:
    return SourceIdentity(
        novel_slug=slug, adapter_key="freewebnovel", origin=origin, index_url=index_url, **kw
    )


# ── chapter labels ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", ["0", "1", "3664", "627.2", "627.10", "0.5", "1.001"])
def test_label_text_round_trips_losslessly(text):
    assert ChapterLabel.parse(text).text == text


def test_label_keeps_trailing_zero_as_a_distinct_label():
    a, b = ChapterLabel.parse("627.1"), ChapterLabel.parse("627.10")
    assert a != b
    assert a.value == b.value  # same number, different published label


def test_label_from_index_matches_legacy_numbers():
    assert ChapterLabel.from_index(3664).text == "3664"
    assert ChapterLabel.from_index(627.2).text == "627.2"
    # An integral float collapses exactly as pipeline._index_number does.
    assert ChapterLabel.from_index(627.0).text == "627"


def test_label_to_index_returns_int_or_float_for_chapter_meta():
    assert ChapterLabel.parse("12").to_index() == 12
    assert isinstance(ChapterLabel.parse("12").to_index(), int)
    assert ChapterLabel.parse("627.2").to_index() == 627.2


def test_label_normalises_leading_zeros_of_the_integer_part_only():
    assert ChapterLabel.parse("007").text == "7"
    assert ChapterLabel.parse("007.50").text == "7.50"


def test_labels_sort_numerically_not_textually():
    labels = [ChapterLabel.parse(t) for t in ("10", "627.2", "9", "627", "628")]
    assert [x.text for x in sorted(labels, key=lambda x: x.sort_key)] == [
        "9", "10", "627", "627.2", "628",
    ]


@pytest.mark.parametrize(
    "bad",
    ["", " ", "abc", "-1", "+1", "1e3", " 1", "1 ", "1.", ".5", "1.2.3", "１２", "1_000",
     "12345678", "1.1234567", True, False, None, float("nan"), float("inf"), -2, -0.5, [1]],
)
def test_label_rejects_malformed_values(bad):
    with pytest.raises(SourceIdentityError):
        ChapterLabel.parse(bad)


# ── slugs ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("slug", ["shadow-slave", "lord-of-mysteries-2-circle-of-inevitability", "a1"])
def test_valid_novel_slugs(slug):
    assert validate_novel_slug(slug) == slug


@pytest.mark.parametrize(
    "slug",
    ["", "Shadow-Slave", "../evil", "a/b", "a\\b", "a b", "-a", "a-", "a--b", ".", "..",
     "a.b", "x" * 81, None, 3],
)
def test_invalid_novel_slugs(slug):
    with pytest.raises(SourceIdentityError):
        validate_novel_slug(slug)


# ── source identity: hosts are distinct, adapters are shared ──────────────────
def test_net_and_com_are_distinct_sources_sharing_one_adapter():
    net = fwn(NET, LOTM_NET)
    com = fwn(COM, LOTM_COM)
    assert net.adapter_key == com.adapter_key == "freewebnovel"
    assert net != com
    assert net.fingerprint() != com.fingerprint()
    assert net.site_key == "freewebnovel.net" and com.site_key == "freewebnovel.com"
    assert not net.same_source(com)


def test_identical_inputs_give_equal_identity_and_stable_fingerprint():
    a, b = fwn(), fwn()
    assert a == b and hash(a) == hash(b)
    assert a.fingerprint() == b.fingerprint()
    assert len(a.fingerprint()) == 16


def test_different_novels_on_the_same_host_are_distinct():
    a = fwn()
    b = fwn(index_url=f"{COM}/novel/shadow-slave", slug="shadow-slave")
    assert a.site_key == b.site_key
    assert a != b and a.fingerprint() != b.fingerprint()


def test_same_novel_slug_and_origin_but_different_index_url_is_distinct():
    a = fwn(index_url=f"{COM}/novel/supreme-magus-novel", slug="supreme-magus")
    b = fwn(index_url=f"{COM}/novel/supreme-magus", slug="supreme-magus")
    assert a != b


def test_index_url_trailing_slash_and_host_case_are_normalised():
    a = fwn(index_url=LOTM_COM + "/")
    b = fwn(origin="https://FreeWebNovel.com", index_url="https://FREEWEBNOVEL.com/novel/lord-of-the-mysteries")
    assert a == fwn() == b
    assert a.index_url == LOTM_COM


def test_adapter_must_belong_to_the_host():
    with pytest.raises(SourceIdentityError, match="adapter"):
        SourceIdentity(
            novel_slug="x", adapter_key="webnovel_dynamic", origin=COM,
            index_url=f"{COM}/novel/x",
        )


def test_unknown_adapter_key_is_rejected():
    with pytest.raises(SourceIdentityError, match="adapter"):
        SourceIdentity(novel_slug="x", adapter_key="nope", origin=COM, index_url=f"{COM}/novel/x")


@pytest.mark.parametrize(
    "origin",
    [
        "http://freewebnovel.com",            # unsupported scheme
        "ftp://freewebnovel.com",
        "https://evil.example",               # unexpected host
        "https://freewebnovel.com.evil.example",
        "https://freewebnovel.org",           # near-miss TLD
        "https://user@freewebnovel.com",      # userinfo
        "https://freewebnovel.com:8443",      # explicit port
        "https://freewebnovel.com/novel",     # an origin has no path
        "https://freewebnovel.com?x=1",
        "https://freewebnovel.com#frag",
        "freewebnovel.com",                   # no scheme
        "",
        None,
    ],
)
def test_invalid_origins_are_rejected(origin):
    with pytest.raises(SourceIdentityError):
        fwn(origin=origin)


@pytest.mark.parametrize(
    "index_url",
    [
        f"{NET}/lord-of-the-mysteries",              # .com origin, .net index URL
        "http://freewebnovel.com/novel/x",           # scheme differs
        f"{COM}/novel/x?page=2",                     # query
        f"{COM}/novel/x#toc",                        # fragment
        f"{COM}",                                    # no path
        f"{COM}/",                                   # root only
        f"{COM}/novel/../../admin",                  # traversal
        f"{COM}/novel/./x",
        f"{COM}\\novel\\x",
        f"{COM}/novel/x y",
        f"https://user@freewebnovel.com/novel/x",
        f"https://freewebnovel.com:443/novel/x",
        "novel/x",
        "",
        None,
    ],
)
def test_mismatched_or_unsafe_index_urls_are_rejected(index_url):
    with pytest.raises(SourceIdentityError):
        fwn(index_url=index_url)


def test_source_identifiers_are_validated_and_order_independent():
    a = SourceIdentity(
        novel_slug="the-noble-queen", adapter_key="webnovel_dynamic",
        origin="https://dynamic.webnovel.com",
        index_url="https://dynamic.webnovel.com/story/28684090500376805",
        identifiers={"book_id": "28684090500376805", "region": "en"},
    )
    b = SourceIdentity(
        novel_slug="the-noble-queen", adapter_key="webnovel_dynamic",
        origin="https://dynamic.webnovel.com",
        index_url="https://dynamic.webnovel.com/story/28684090500376805",
        identifiers=[("region", "en"), ("book_id", "28684090500376805")],
    )
    assert a == b and a.fingerprint() == b.fingerprint()
    assert dict(a.identifiers) == {"book_id": "28684090500376805", "region": "en"}


def test_source_identifiers_change_identity():
    base = dict(
        novel_slug="the-noble-queen", adapter_key="webnovel_dynamic",
        origin="https://dynamic.webnovel.com",
        index_url="https://dynamic.webnovel.com/story/1",
    )
    assert SourceIdentity(**base, identifiers={"book_id": "1"}) != SourceIdentity(
        **base, identifiers={"book_id": "2"}
    )


@pytest.mark.parametrize(
    "identifiers",
    [{"Book Id": "1"}, {"": "1"}, {"book_id": ""}, {"book_id": "a\nb"}, {"book_id": 5},
     {"book_id": "x" * 201}, [("book_id",)], "book_id=1"],
)
def test_invalid_identifiers_are_rejected(identifiers):
    with pytest.raises(SourceIdentityError):
        fwn(identifiers=identifiers)


def test_source_identity_is_immutable():
    ident = fwn()
    with pytest.raises(Exception):
        ident.novel_slug = "other"  # type: ignore[misc]


# ── serialisation ────────────────────────────────────────────────────────────
def test_source_identity_json_round_trip():
    ident = fwn(identifiers={"book_id": "7"})
    payload = json.loads(json.dumps(ident.to_json()))
    assert SourceIdentity.from_json(payload) == ident


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("origin"),
    lambda d: d.update(extra="x"),
    lambda d: d.update(origin="https://evil.example"),
    lambda d: d.update(novel_slug="../x"),
    lambda d: d.update(identifiers="nope"),
    lambda d: d.update(adapter_key=5),
])
def test_from_json_rejects_tampered_or_incomplete_payloads(mutate):
    payload = fwn().to_json()
    mutate(payload)
    with pytest.raises(SourceIdentityError):
        SourceIdentity.from_json(payload)


@pytest.mark.parametrize("payload", [None, [], "x", 3])
def test_from_json_rejects_non_objects(payload):
    with pytest.raises(SourceIdentityError):
        SourceIdentity.from_json(payload)


# ── catalog compatibility (the catalog itself is unchanged) ───────────────────
def test_every_current_catalog_row_has_a_valid_distinct_identity():
    specs = catalog.all_specs()
    idents = [SourceIdentity.from_site_spec(s) for s in specs]
    assert len({i.fingerprint() for i in idents}) == len(specs)
    for spec, ident in zip(specs, idents):
        assert ident.novel_slug == spec.novel_slug
        assert ident.adapter_key == spec.adapter_key


def test_supreme_magus_keeps_catalog_slug_distinct_from_the_site_slug():
    spec = catalog.get_spec("supreme-magus", "freewebnovel")
    ident = SourceIdentity.from_site_spec(spec)
    assert ident.novel_slug == "supreme-magus"
    assert ident.index_url.endswith("/novel/supreme-magus-novel")
    assert ident.site_key == "freewebnovel.com"


def test_from_site_spec_carries_the_book_id():
    ident = SourceIdentity.from_site_spec(catalog.get_spec("the-noble-queen", "webnovel_dynamic"))
    assert dict(ident.identifiers) == {"book_id": "28684090500376805"}


def test_from_site_spec_falls_back_to_the_url_origin_without_a_base_url():
    spec = catalog.get_spec("shadow-slave", "telegraph")
    assert spec.base_url == ""
    assert SourceIdentity.from_site_spec(spec).origin == "https://telegra.ph"


def test_from_site_spec_rejects_a_base_url_that_disagrees_with_the_index_url():
    from dataclasses import replace

    spec = replace(catalog.get_spec("shadow-slave", "freewebnovel"), base_url=NET)
    with pytest.raises(SourceIdentityError):
        SourceIdentity.from_site_spec(spec)


# ── chapter identity: the source page, never the displayed number ─────────────
def sm_page(slug: int, label: str = "3664") -> ChapterIdentity:
    source = fwn(COM, f"{COM}/novel/supreme-magus-novel", "supreme-magus")
    return source.chapter_identity(
        f"{COM}/novel/supreme-magus-novel/chapter-{slug}",
        ChapterLabel.parse(label), slug=slug, title="Title",
    )


def test_two_pages_displaying_chapter_3664_stay_distinguishable():
    a, b = sm_page(3678), sm_page(3679)
    assert a.label == b.label
    assert a != b and a.page_key != b.page_key
    assert hash(a) != hash(b) or a != b
    assert len({a, b}) == 2
    ensure_distinct_pages([a, b])  # distinct pages: fine
    dups = duplicate_labels([a, b, sm_page(3680, "3665")])
    assert list(dups) == [ChapterLabel.parse("3664")]
    assert dups[ChapterLabel.parse("3664")] == [a, b]


def test_same_page_is_the_same_identity_whatever_the_label_or_fragment():
    a = sm_page(3678, "3664")
    source = fwn(COM, f"{COM}/novel/supreme-magus-novel", "supreme-magus")
    b = source.chapter_identity(
        f"HTTPS://FreeWebNovel.com/novel/supreme-magus-novel/chapter-3678#comments",
        ChapterLabel.parse("1"),
    )
    assert a.page_key == b.page_key and a == b


def test_duplicate_page_urls_are_a_contradiction():
    a = sm_page(3678)
    with pytest.raises(SourceIdentityError, match="more than once"):
        ensure_distinct_pages([a, sm_page(3678, "3665")])


def test_a_chapter_url_on_another_host_is_rejected_for_the_source():
    net_source = fwn(NET, LOTM_NET)
    with pytest.raises(SourceIdentityError, match="host"):
        net_source.chapter_identity(f"{COM}/novel/lord-of-the-mysteries/chapter-1", ChapterLabel.parse("1"))


@pytest.mark.parametrize("url", [
    "http://freewebnovel.net/lord-of-the-mysteries/chapter-1-crimson.html",
    "https://user@freewebnovel.net/lord-of-the-mysteries/chapter-1-crimson.html",
    "https://freewebnovel.net:8443/lord-of-the-mysteries/chapter-1.html",
    "https://freewebnovel.net/lord-of-the-mysteries/../x.html",
    "https://freewebnovel.net/",
    "https://freewebnovel.net",
    "",
    None,
])
def test_unsafe_chapter_urls_are_rejected(url):
    with pytest.raises(SourceIdentityError):
        fwn(NET, LOTM_NET).chapter_identity(url, ChapterLabel.parse("1"))


def test_net_chapter_page_with_html_slug_is_accepted():
    ident = fwn(NET, LOTM_NET).chapter_identity(
        f"{NET}/lord-of-the-mysteries/chapter-1-crimson.html", ChapterLabel.parse("1"),
        title="Crimson",
    )
    assert ident.page_url.endswith("chapter-1-crimson.html")


def test_chapter_identity_from_meta_keeps_fractional_labels_losslessly():
    source = fwn(COM, f"{COM}/novel/supreme-magus-novel", "supreme-magus")
    meta = ChapterMeta(
        index=627.2, url=f"{COM}/novel/supreme-magus-novel/chapter-640", title="Insert",
        source_id="640", slug=640,
    )
    ident = ChapterIdentity.from_meta(source, meta)
    assert ident.label.text == "627.2"
    assert (ident.slug, ident.source_id) == (640, "640")
    assert ident.label.to_index() == meta.index


def test_chapter_identity_json_round_trip_is_lossless():
    source = fwn(NET, LOTM_NET)
    ident = source.chapter_identity(
        f"{NET}/lord-of-the-mysteries/chapter-627-2-insert.html", ChapterLabel.parse("627.10"),
        slug=None, source_id="abc123", title="Insert",
    )
    payload = json.loads(json.dumps(ident.to_json()))
    back = ChapterIdentity.from_json(source, payload)
    assert back == ident
    assert back.label.text == "627.10" and back.source_id == "abc123" and back.title == "Insert"


def test_chapter_identity_from_json_revalidates_against_the_source():
    payload = fwn(NET, LOTM_NET).chapter_identity(
        f"{NET}/lord-of-the-mysteries/chapter-1.html", ChapterLabel.parse("1")
    ).to_json()
    with pytest.raises(SourceIdentityError):
        ChapterIdentity.from_json(fwn(COM, LOTM_COM), payload)  # .net page under a .com source
    payload["label"] = "1e3"
    with pytest.raises(SourceIdentityError):
        ChapterIdentity.from_json(fwn(NET, LOTM_NET), payload)
