"""Chapter manifest schema, serialization and strict validation (v0.3.0 Checkpoint 03.1).

Offline and pure: no files, network or browser. Every manifest here is synthetic; no real
chapter list is committed.
"""

from __future__ import annotations

import copy
import json

import pytest

from webnovel_scraper.chapter_manifest import (
    MAX_MANIFEST_BYTES,
    AbsentChapter,
    ChapterManifest,
    ManifestError,
    Provenance,
    parse_manifest_bytes,
)
from webnovel_scraper.source_identity import ChapterLabel, SourceIdentity

COM = "https://freewebnovel.com"
NET = "https://freewebnovel.net"


def src(origin=COM, index_url=f"{COM}/novel/demo", slug="demo", **kw) -> SourceIdentity:
    return SourceIdentity(
        novel_slug=slug, adapter_key="freewebnovel", origin=origin, index_url=index_url, **kw
    )


def prov(method="toc", **kw) -> Provenance:
    base = dict(
        generated_utc="2026-10-18T10:00:00Z",
        validated_utc="2026-10-18T11:00:00Z",
        app_version="0.3.0",
        method=method,
        legacy_index_sha256=None,
        notes="",
    )
    base.update(kw)
    return Provenance(**base)


def row(source, label, slug=None, title=None, source_id=None, path=None):
    page = f"{source.origin}/novel/demo/chapter-{path or label}"
    return source.chapter_identity(
        page, ChapterLabel.parse(label), slug=slug, title=title, source_id=source_id
    )


def rows(source, labels):
    return [row(source, str(n), slug=n) for n in labels]


def build(source=None, *, layer="runtime", chapters=None, absent=(), spans=(), complete=False,
          provenance=None):
    source = source or src()
    return ChapterManifest.create(
        layer=layer,
        source=source,
        provenance=provenance or prov(),
        toc_complete=complete,
        spans=spans,
        chapters=chapters if chapters is not None else [],
        absent=absent,
    )


def reload(manifest: ChapterManifest, layer=None) -> ChapterManifest:
    payload = json.loads(json.dumps(manifest.to_json()))
    return ChapterManifest.from_json(payload, expected_source=manifest.source,
                                     expected_layer=layer or manifest.layer)


# ── valid shapes ──────────────────────────────────────────────────────────────
def test_empty_manifest_is_valid_and_round_trips():
    manifest = build()
    again = reload(manifest)
    assert again == manifest
    assert again.chapters == () and again.spans == () and again.absent == ()
    assert manifest.to_json()["schema_version"] == 1
    assert manifest.to_json()["kind"] == "chapter-manifest"


def test_json_has_the_exact_documented_top_level_keys():
    payload = build(chapters=rows(src(), [1, 2]), spans=[(1, 2)]).to_json()
    assert set(payload) == {
        "schema_version", "kind", "layer", "source", "provenance", "coverage",
        "chapters", "absent", "content_sha256",
    }
    assert set(payload["coverage"]) == {"toc_complete", "spans"}
    assert set(payload["provenance"]) == {
        "generated_utc", "validated_utc", "app_version", "method",
        "legacy_index_sha256", "notes",
    }
    assert payload["coverage"]["spans"] == [["1", "2"]]


def test_chapters_round_trip_through_json_exactly():
    source = src()
    chapters = [
        row(source, "1", slug=1, title="Start", source_id="a1"),
        row(source, "2", slug=2),
    ]
    manifest = build(source, chapters=chapters, spans=[(1, 2)], complete=True)
    again = reload(manifest)
    assert [c.to_json() for c in again.chapters] == [c.to_json() for c in manifest.chapters]
    assert again.content_sha256 == manifest.content_sha256


def test_fractional_labels_are_kept_as_exact_text():
    source = src()
    chapters = [row(source, "627"), row(source, "627.1"), row(source, "627.10"), row(source, "627.2")]
    manifest = build(source, chapters=chapters, spans=[(627, 627)])
    raw = json.dumps(manifest.to_json())
    again = ChapterManifest.from_json(json.loads(raw), expected_source=source, expected_layer="runtime")
    assert [c.label.text for c in again.chapters] == ["627", "627.1", "627.10", "627.2"]
    assert '"627.10"' in raw and "627.1," in raw.replace('"627.1"', "627.1,")


def test_fractional_chapters_are_ordered_numerically_then_by_text():
    source = src()
    chapters = [row(source, "627.10"), row(source, "627.2"), row(source, "627.1")]
    manifest = build(source, chapters=chapters)
    assert [c.label.text for c in manifest.chapters] == ["627.1", "627.10", "627.2"]


def test_duplicate_displayed_numbers_on_distinct_pages_are_preserved():
    source = src()
    a = row(source, "3664", slug=3678, path="3678")
    b = row(source, "3664", slug=3679, path="3679")
    manifest = build(source, chapters=[b, a], spans=[(3664, 3664)])
    again = reload(manifest)
    assert [c.slug for c in again.chapters] == [3678, 3679]
    assert set(again.duplicate_labels()) == {ChapterLabel.parse("3664")}


def test_chapters_sort_by_label_then_slug_then_page_url():
    source = src()
    chapters = [row(source, "10", slug=10), row(source, "9", slug=9), row(source, "9", slug=8, path="9b")]
    manifest = build(source, chapters=chapters)
    assert [(c.label.text, c.slug) for c in manifest.chapters] == [("9", 8), ("9", 9), ("10", 10)]


def test_digest_covers_chapters_absent_and_spans_and_is_stable():
    source = src()
    chapters = rows(source, [1, 2, 3])
    first = build(source, chapters=chapters, spans=[(1, 3)])
    shuffled = build(source, chapters=list(reversed(chapters)), spans=[(1, 3)])
    assert first.content_sha256 == shuffled.content_sha256
    assert len(first.content_sha256) == 64
    # Provenance does not feed the digest, evidence does.
    other_prov = build(source, chapters=chapters, spans=[(1, 3)], provenance=prov(notes="x"))
    assert other_prov.content_sha256 == first.content_sha256
    fewer = build(source, chapters=chapters[:2], spans=[(1, 2)])
    assert fewer.content_sha256 != first.content_sha256
    absent = build(source, chapters=[chapters[0], chapters[2]],
                   absent=[AbsentChapter(ChapterLabel.parse("2"), "page 404, neighbours present")],
                   spans=[(1, 3)])
    assert absent.content_sha256 != first.content_sha256


def test_digest_differs_when_only_the_span_differs():
    source = src()
    chapters = rows(source, [1, 2, 3])
    a = build(source, chapters=chapters, spans=[(1, 3)])
    b = build(source, chapters=chapters, spans=[(1, 2)])
    assert a.content_sha256 != b.content_sha256


def test_parse_bytes_round_trip_of_canonical_file_text():
    manifest = build(chapters=rows(src(), [1, 2]), spans=[(1, 2)])
    data = json.dumps(manifest.to_json(), indent=2).encode()
    parsed = parse_manifest_bytes(data, expected_source=manifest.source, expected_layer="runtime")
    assert parsed == manifest


# ── source, layer and header checks ──────────────────────────────────────────
def test_cross_host_manifest_is_rejected():
    com = src(COM, f"{COM}/novel/demo")
    net = src(NET, f"{NET}/demo")
    payload = build(com, chapters=rows(com, [1]), spans=[(1, 1)]).to_json()
    with pytest.raises(ManifestError, match="source"):
        ChapterManifest.from_json(payload, expected_source=net, expected_layer="runtime")


def test_different_novels_on_one_host_are_different_sources():
    a = src(slug="demo")
    b = src(slug="other", index_url=f"{COM}/novel/other")
    payload = build(a).to_json()
    with pytest.raises(ManifestError, match="source"):
        ChapterManifest.from_json(payload, expected_source=b, expected_layer="runtime")


def test_identifiers_must_match_exactly():
    a = src(identifiers={"book_id": "1"})
    b = src(identifiers={"book_id": "2"})
    with pytest.raises(ManifestError, match="source"):
        ChapterManifest.from_json(build(a).to_json(), expected_source=b, expected_layer="runtime")


@pytest.mark.parametrize("field,value", [
    ("schema_version", 2), ("schema_version", "1"), ("schema_version", True),
    ("kind", "run-journal"), ("kind", None), ("layer", "cache"), ("layer", "baseline"),
])
def test_bad_header_fields_are_rejected(field, value):
    manifest = build()
    payload = manifest.to_json()
    payload[field] = value
    with pytest.raises(ManifestError):
        ChapterManifest.from_json(payload, expected_source=manifest.source, expected_layer="runtime")


def test_wrong_layer_is_rejected_both_ways():
    runtime = build(layer="runtime")
    with pytest.raises(ManifestError, match="layer"):
        ChapterManifest.from_json(runtime.to_json(), expected_source=runtime.source,
                                  expected_layer="baseline")


def test_unknown_expected_layer_is_a_programming_error():
    with pytest.raises(ManifestError):
        ChapterManifest.from_json(build().to_json(), expected_source=src(), expected_layer="cache")


def test_missing_or_extra_top_level_keys_are_rejected():
    manifest = build()
    for mutate in (lambda p: p.pop("absent"), lambda p: p.update(extra=1), lambda p: p.pop("coverage")):
        payload = manifest.to_json()
        mutate(payload)
        with pytest.raises(ManifestError):
            ChapterManifest.from_json(payload, expected_source=manifest.source, expected_layer="runtime")


def test_non_object_payload_is_rejected():
    for payload in ([], "x", 3, None):
        with pytest.raises(ManifestError):
            ChapterManifest.from_json(payload, expected_source=src(), expected_layer="runtime")


# ── provenance ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("kw", [
    dict(method="guess"),
    dict(method=None),
    dict(generated_utc="yesterday"),
    dict(generated_utc="2026-13-40T00:00:00Z"),
    dict(validated_utc=5),
    dict(app_version=""),
    dict(app_version="v 1 .0"),
    dict(notes="x" * 2000),
    dict(notes="bad\x00note"),
    dict(legacy_index_sha256="abc"),
    dict(legacy_index_sha256="Z" * 64),
    dict(method="legacy-migration", legacy_index_sha256=None),
])
def test_invalid_provenance_is_rejected(kw):
    with pytest.raises(ManifestError):
        prov(**kw)


def test_provenance_timestamps_may_be_null():
    provenance = prov(generated_utc=None, validated_utc=None)
    assert reload(build(provenance=provenance)).provenance == provenance


def test_baseline_cannot_claim_legacy_migration():
    source = src()
    with pytest.raises(ManifestError, match="baseline"):
        build(source, layer="baseline",
              provenance=prov(method="legacy-migration", legacy_index_sha256="a" * 64))


def test_legacy_migration_provenance_keeps_its_digest_and_never_claims_complete():
    source = src()
    chapters = rows(source, [1, 2])
    manifest = build(source, chapters=chapters, spans=[(1, 2)],
                     provenance=prov(method="legacy-migration", legacy_index_sha256="b" * 64))
    assert reload(manifest).provenance.legacy_index_sha256 == "b" * 64
    with pytest.raises(ManifestError, match="complete"):
        build(source, chapters=chapters, spans=[(1, 2)], complete=True,
              provenance=prov(method="legacy-migration", legacy_index_sha256="b" * 64))


def test_provenance_contains_no_absolute_path_fields():
    keys = set(build().to_json()["provenance"])
    assert not {"path", "source_path", "file"} & keys


# ── chapters ─────────────────────────────────────────────────────────────────
def test_chapter_page_on_another_host_is_rejected():
    source = src()
    payload = build(source, chapters=rows(source, [1]), spans=[(1, 1)]).to_json()
    payload["chapters"][0]["page_url"] = "https://freewebnovel.net/novel/demo/chapter-1"
    with pytest.raises(ManifestError, match="host"):
        ChapterManifest.from_json(payload, expected_source=source, expected_layer="runtime")


@pytest.mark.parametrize("label", ["1e3", "-1", "01.", "1,5", "", " 3", "12345678", "1.1234567"])
def test_non_canonical_label_text_is_rejected(label):
    source = src()
    payload = build(source, chapters=rows(source, [1]), spans=[(1, 1)]).to_json()
    payload["chapters"][0]["label"] = label
    with pytest.raises(ManifestError):
        ChapterManifest.from_json(payload, expected_source=source, expected_layer="runtime")


def test_numeric_label_values_are_rejected_in_json():
    source = src()
    payload = build(source, chapters=rows(source, [1]), spans=[(1, 1)]).to_json()
    payload["chapters"][0]["label"] = 1.5
    with pytest.raises(ManifestError, match="label"):
        ChapterManifest.from_json(payload, expected_source=source, expected_layer="runtime")


def test_duplicate_page_urls_are_rejected():
    source = src()
    a = row(source, "1", slug=1)
    clone = source.chapter_identity(a.page_url, ChapterLabel.parse("2"), slug=2)
    with pytest.raises(ManifestError, match="more than once"):
        build(source, chapters=[a, clone])


def test_duplicate_slugs_are_rejected_but_missing_slugs_are_not():
    source = src()
    a = row(source, "1", slug=5, path="a")
    b = row(source, "2", slug=5, path="b")
    with pytest.raises(ManifestError, match="slug"):
        build(source, chapters=[a, b])
    no_slug = [row(source, "1", path="a"), row(source, "2", path="b")]
    assert len(build(source, chapters=no_slug).chapters) == 2


def test_unsorted_stored_chapters_are_rejected():
    source = src()
    payload = build(source, chapters=rows(source, [1, 2, 3]), spans=[(1, 3)]).to_json()
    payload["chapters"].reverse()
    with pytest.raises(ManifestError, match="sorted"):
        ChapterManifest.from_json(payload, expected_source=source, expected_layer="runtime")


def test_bad_chapter_row_shapes_are_rejected():
    source = src()
    for mutate in (
        lambda c: c.pop("title"),
        lambda c: c.update(extra=1),
        lambda c: c.update(slug=-1),
        lambda c: c.update(slug=True),
        lambda c: c.update(slug="7"),
        lambda c: c.update(source_id=""),
        lambda c: c.update(title=5),
        lambda c: c.update(title="x" * 5000),
        lambda c: c.update(title="bad\x00"),
    ):
        payload = build(source, chapters=rows(source, [1]), spans=[(1, 1)]).to_json()
        mutate(payload["chapters"][0])
        with pytest.raises(ManifestError):
            ChapterManifest.from_json(payload, expected_source=source, expected_layer="runtime")


def test_chapters_must_be_a_list():
    source = src()
    payload = build(source).to_json()
    payload["chapters"] = {"a": 1}
    with pytest.raises(ManifestError):
        ChapterManifest.from_json(payload, expected_source=source, expected_layer="runtime")


# ── coverage ─────────────────────────────────────────────────────────────────
def test_sparse_coverage_with_a_gap_is_valid_and_gap_is_not_covered():
    source = src()
    manifest = build(source, chapters=rows(source, [1, 2, 3, 7, 8]), spans=[(1, 3), (7, 8)])
    assert manifest.covers(1, 3) and manifest.covers(7, 8) and manifest.covers(2, 2)
    assert not manifest.covers(1, 8)
    assert not manifest.covers(4, 6)
    assert manifest.uncovered_spans(1, 10) == [(4, 6), (9, 10)]
    assert manifest.uncovered_spans(2, 3) == []


def test_unexamined_gap_is_never_treated_as_absent():
    source = src()
    manifest = build(source, chapters=rows(source, [1, 2, 3, 7, 8]), spans=[(1, 3), (7, 8)])
    assert manifest.absent == ()
    assert manifest.uncovered_spans(4, 6) == [(4, 6)]


def test_chapters_outside_any_span_are_kept_but_not_claimed():
    source = src()
    manifest = build(source, chapters=rows(source, [1, 2, 50]), spans=[(1, 2)])
    assert not manifest.covers(50, 50)
    assert len(manifest.chapters) == 3


def test_span_over_unaccounted_integer_is_a_false_claim_and_rejected():
    source = src()
    with pytest.raises(ManifestError, match="not accounted"):
        build(source, chapters=rows(source, [1, 2, 4]), spans=[(1, 4)])


def test_span_beyond_listed_chapters_is_rejected():
    source = src()
    with pytest.raises(ManifestError, match="not accounted"):
        build(source, chapters=rows(source, [1, 2]), spans=[(1, 3)])


def test_source_confirmed_absence_fills_a_gap():
    source = src()
    manifest = build(
        source,
        chapters=[row(source, "1", slug=1), row(source, "3", slug=3)],
        absent=[AbsentChapter(ChapterLabel.parse("2"), "page 404 and neighbours present")],
        spans=[(1, 3)],
    )
    again = reload(manifest)
    assert again.covers(1, 3)
    assert [a.label.text for a in again.absent] == ["2"]
    assert again.absent[0].evidence == "page 404 and neighbours present"


def test_fractional_labels_do_not_satisfy_integer_coverage():
    source = src()
    with pytest.raises(ManifestError, match="not accounted"):
        build(source, chapters=[row(source, "1"), row(source, "2.5"), row(source, "3")], spans=[(1, 3)])


def test_fractional_entries_inside_a_span_are_explicitly_represented():
    source = src()
    manifest = build(source, chapters=[row(source, "1"), row(source, "1.5"), row(source, "2")],
                     spans=[(1, 2)])
    assert [c.label.text for c in manifest.chapters] == ["1", "1.5", "2"]


@pytest.mark.parametrize("spans", [
    [(3, 1)],                    # reversed
    [(1, 2), (2, 3)],            # overlapping
    [(5, 6), (1, 2)],            # unsorted
    [(1, 3), (2, 2)],            # nested
])
def test_malformed_spans_are_rejected(spans):
    source = src()
    with pytest.raises(ManifestError):
        ChapterManifest(
            layer="runtime", source=source, provenance=prov(), toc_complete=False,
            spans=tuple(spans), chapters=tuple(rows(source, range(1, 7))), absent=(),
        )


@pytest.mark.parametrize("bad", [["1"], ["1", "2", "3"], [1, 2], ["a", "b"], ["1.5", "3"], ["-1", "3"], "12"])
def test_bad_span_json_shapes_are_rejected(bad):
    source = src()
    payload = build(source, chapters=rows(source, [1, 2, 3]), spans=[(1, 3)]).to_json()
    payload["coverage"]["spans"] = [bad]
    with pytest.raises(ManifestError):
        ChapterManifest.from_json(payload, expected_source=source, expected_layer="runtime")


def test_hostile_huge_span_is_rejected_quickly_without_walking_it():
    source = src()
    payload = build(source, chapters=rows(source, [1, 2, 3]), spans=[(1, 3)]).to_json()
    payload["coverage"]["spans"] = [["0", "9999999"]]
    with pytest.raises(ManifestError, match="not accounted"):
        ChapterManifest.from_json(payload, expected_source=source, expected_layer="runtime")


def test_span_bounds_beyond_the_label_range_are_rejected():
    source = src()
    payload = build(source).to_json()
    payload["coverage"]["spans"] = [["1", "99999999"]]
    with pytest.raises(ManifestError):
        ChapterManifest.from_json(payload, expected_source=source, expected_layer="runtime")


def test_toc_complete_must_be_a_real_bool():
    source = src()
    payload = build(source).to_json()
    payload["coverage"]["toc_complete"] = 1
    with pytest.raises(ManifestError):
        ChapterManifest.from_json(payload, expected_source=source, expected_layer="runtime")


def test_toc_complete_needs_one_contiguous_span_holding_every_whole_chapter():
    source = src()
    chapters = rows(source, [1, 2, 7, 8])
    with pytest.raises(ManifestError, match="complete"):
        build(source, chapters=chapters, spans=[(1, 2), (7, 8)], complete=True)
    with pytest.raises(ManifestError, match="complete"):
        build(source, chapters=rows(source, [1, 2, 3]), spans=[(1, 2)], complete=True)
    ok = build(source, chapters=rows(source, [1, 2, 3]), spans=[(1, 3)], complete=True)
    assert ok.toc_complete is True
    assert build(complete=True).toc_complete is True  # a source with nothing to list


# ── absent evidence ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("evidence", ["", "   ", "x" * 600, "bad\x00", None, 5])
def test_absent_evidence_must_be_real_text(evidence):
    with pytest.raises(ManifestError):
        AbsentChapter(ChapterLabel.parse("2"), evidence)


def test_absent_label_cannot_also_be_a_listed_chapter():
    source = src()
    with pytest.raises(ManifestError, match="absent"):
        build(source, chapters=rows(source, [1, 2, 3]),
              absent=[AbsentChapter(ChapterLabel.parse("2"), "claimed missing")], spans=[(1, 3)])


def test_absent_labels_must_be_whole_numbers_inside_a_span():
    source = src()
    chapters = [row(source, "1", slug=1), row(source, "3", slug=3)]
    with pytest.raises(ManifestError, match="fraction"):
        build(source, chapters=chapters,
              absent=[AbsentChapter(ChapterLabel.parse("2.5"), "gone")], spans=[(1, 1)])
    with pytest.raises(ManifestError, match="span"):
        build(source, chapters=chapters,
              absent=[AbsentChapter(ChapterLabel.parse("9"), "gone")], spans=[(1, 1)])


def test_duplicate_absent_labels_are_rejected():
    source = src()
    chapters = [row(source, "1", slug=1), row(source, "3", slug=3)]
    twice = [AbsentChapter(ChapterLabel.parse("2"), "a"), AbsentChapter(ChapterLabel.parse("2"), "b")]
    with pytest.raises(ManifestError, match="more than once"):
        build(source, chapters=chapters, absent=twice, spans=[(1, 3)])


# ── digest tamper detection ──────────────────────────────────────────────────
def test_bad_content_hash_is_rejected():
    source = src()
    payload = build(source, chapters=rows(source, [1, 2]), spans=[(1, 2)]).to_json()
    payload["content_sha256"] = "0" * 64
    with pytest.raises(ManifestError, match="content_sha256"):
        ChapterManifest.from_json(payload, expected_source=source, expected_layer="runtime")


def test_a_swapped_chapter_url_breaks_the_digest():
    source = src()
    payload = build(source, chapters=rows(source, [1, 2]), spans=[(1, 2)]).to_json()
    tampered = copy.deepcopy(payload)
    tampered["chapters"][1]["page_url"] = f"{COM}/novel/demo/chapter-evil"
    with pytest.raises(ManifestError, match="content_sha256"):
        ChapterManifest.from_json(tampered, expected_source=source, expected_layer="runtime")


@pytest.mark.parametrize("value", [None, 5, "short", "G" * 64, "A" * 64])
def test_malformed_content_hash_is_rejected(value):
    source = src()
    payload = build(source).to_json()
    payload["content_sha256"] = value
    with pytest.raises(ManifestError):
        ChapterManifest.from_json(payload, expected_source=source, expected_layer="runtime")


# ── raw bytes ────────────────────────────────────────────────────────────────
def parse(data: bytes, source=None):
    return parse_manifest_bytes(data, expected_source=source or src(), expected_layer="runtime")


def test_oversized_bytes_are_rejected_before_parsing():
    with pytest.raises(ManifestError, match="larger"):
        parse(b" " * (MAX_MANIFEST_BYTES + 1))


@pytest.mark.parametrize("data", [
    b"", b"{", b"[]", b"null", b"\xff\xfe\x00{}", b'{"a": NaN}', b'{"a": 1, "a": 2}',
], ids=["empty", "truncated", "list", "null", "not-utf8", "nan", "duplicate-key"])
def test_malformed_bytes_are_rejected_without_crashing(data):
    with pytest.raises(ManifestError):
        parse(data)


def test_hostile_nesting_is_rejected_without_crashing():
    with pytest.raises(ManifestError):
        parse(b"[" * 100000 + b"]" * 100000)


def test_byte_order_mark_is_not_silently_accepted():
    manifest = build()
    data = b"\xef\xbb\xbf" + json.dumps(manifest.to_json()).encode()
    with pytest.raises(ManifestError):
        parse(data, manifest.source)
