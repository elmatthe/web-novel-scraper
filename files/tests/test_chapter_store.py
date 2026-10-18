"""Chapter manifest store: layers, merging, quarantine and atomic publication
(v0.3.0 Checkpoint 03.1). Offline; every manifest is synthetic and lives under ``tmp_path``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from webnovel_scraper import chapter_store
from webnovel_scraper.chapter_manifest import (
    MAX_MANIFEST_BYTES,
    AbsentChapter,
    ChapterManifest,
    ManifestError,
    Provenance,
)
from webnovel_scraper.chapter_store import (
    ChapterManifestStore,
    ManifestStoreError,
    merge_manifests,
)
from webnovel_scraper.source_identity import ChapterLabel, SourceIdentity

COM = "https://freewebnovel.com"
NET = "https://freewebnovel.net"
NOW = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)


def src(origin=COM, index_url=f"{COM}/novel/demo", slug="demo") -> SourceIdentity:
    return SourceIdentity(novel_slug=slug, adapter_key="freewebnovel", origin=origin,
                          index_url=index_url)


def prov(method="toc", **kw) -> Provenance:
    base = dict(generated_utc="2026-10-18T10:00:00Z", validated_utc="2026-10-18T11:00:00Z",
                app_version="0.3.0", method=method, legacy_index_sha256=None, notes="")
    base.update(kw)
    return Provenance(**base)


def ch(source, label, slug=None, title=None, path=None, source_id=None):
    return source.chapter_identity(
        f"{source.origin}/novel/demo/chapter-{path or label}", ChapterLabel.parse(label),
        slug=slug, title=title, source_id=source_id,
    )


def nums(source, numbers, **kw):
    return [ch(source, str(n), slug=n, **kw) for n in numbers]


def manifest(source=None, layer="runtime", chapters=(), spans=(), absent=(), complete=False,
             provenance=None) -> ChapterManifest:
    source = source or src()
    return ChapterManifest.create(layer=layer, source=source, provenance=provenance or prov(),
                                  toc_complete=complete, spans=spans, chapters=chapters,
                                  absent=absent)


def full(source, numbers, layer="runtime", **kw) -> ChapterManifest:
    numbers = list(numbers)
    return manifest(source, layer, chapters=nums(source, numbers),
                    spans=[(numbers[0], numbers[-1])], **kw)


@pytest.fixture
def store(tmp_path):
    return ChapterManifestStore(tmp_path / "baseline", tmp_path / "runtime", clock=lambda: NOW)


def put(path: Path, m: ChapterManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(m.to_json(), indent=2), encoding="utf-8")


def snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file()}


# ── locations ────────────────────────────────────────────────────────────────
def test_paths_are_site_key_then_novel_slug(store, tmp_path):
    s = src()
    assert store.baseline_path(s) == tmp_path / "baseline" / "freewebnovel.com" / "demo.json"
    assert store.runtime_path(s) == tmp_path / "runtime" / "freewebnovel.com" / "demo.json"


def test_com_and_net_never_share_a_path_or_a_source(store):
    com, net = src(), src(NET, f"{NET}/demo")
    assert store.baseline_path(com) != store.baseline_path(net)
    assert store.runtime_path(com) != store.runtime_path(net)
    assert com != net


def test_two_novels_on_one_website_have_their_own_files(store):
    a, b = src(), src(slug="other", index_url=f"{COM}/novel/other")
    assert store.runtime_path(a).parent == store.runtime_path(b).parent
    assert store.runtime_path(a) != store.runtime_path(b)


def test_default_locations_match_the_documented_layout():
    store = ChapterManifestStore()
    repo = Path(__file__).resolve().parents[2]
    s = src()
    assert store.baseline_path(s) == (
        repo / "scripts" / "webnovel_scraper" / "chapter_lists" / "freewebnovel.com" / "demo.json")
    assert store.runtime_path(s) == (
        repo / "files" / "runtime-data" / "chapter-lists" / "freewebnovel.com" / "demo.json")


def test_runtime_location_is_git_ignored_and_baseline_is_not():
    repo = Path(__file__).resolve().parents[2]
    lines = [ln.strip() for ln in (repo / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert "files/runtime-data/" in lines
    assert not any("chapter_lists" in ln for ln in lines)


def test_no_real_baseline_data_is_committed():
    repo = Path(__file__).resolve().parents[2]
    folder = repo / "scripts" / "webnovel_scraper" / "chapter_lists"
    assert not folder.exists() or not list(folder.rglob("*.json"))


def test_store_offers_no_way_to_write_a_baseline(store):
    assert not any("baseline" in name and ("save" in name or "write" in name) for name in dir(store))


# ── loading a single layer ───────────────────────────────────────────────────
def test_missing_layers_load_as_missing_and_effective_is_empty(store):
    load = store.load_effective(src())
    assert load.baseline.status == "missing" and load.runtime.status == "missing"
    assert load.effective.chapters == () and load.effective.spans == ()
    assert load.effective.conflicts == ()


def test_valid_baseline_loads(store):
    s = src()
    put(store.baseline_path(s), full(s, [1, 2, 3], layer="baseline"))
    layer = store.load_layer(s, "baseline")
    assert layer.status == "loaded"
    assert [c.label.text for c in layer.manifest.chapters] == ["1", "2", "3"]


def test_valid_runtime_overlay_loads(store):
    s = src()
    put(store.runtime_path(s), full(s, [1, 2]))
    assert store.load_layer(s, "runtime").status == "loaded"


def test_valid_empty_manifest_loads(store):
    s = src()
    put(store.runtime_path(s), manifest(s))
    layer = store.load_layer(s, "runtime")
    assert layer.status == "loaded" and layer.manifest.chapters == ()


def test_unknown_layer_name_is_refused(store):
    with pytest.raises(ManifestStoreError):
        store.load_layer(src(), "cache")


# ── invalid tracked baseline: read-only ──────────────────────────────────────
def test_invalid_baseline_is_rejected_and_left_untouched(store):
    s = src()
    path = store.baseline_path(s)
    path.parent.mkdir(parents=True)
    path.write_text('{"schema_version": 1, "kind": "chapter-manifest"', encoding="utf-8")
    before = snapshot(store.baseline_path(s).parents[1])
    layer = store.load_layer(s, "baseline")
    assert layer.status == "invalid" and layer.manifest is None
    assert "baseline" in layer.error.lower() and str(path.name) in layer.error
    assert snapshot(path.parents[1]) == before          # not renamed, rewritten or deleted
    assert path.exists() and not list(path.parent.glob("*.corrupt-*"))


def test_wrong_source_baseline_is_invalid_not_quarantined(store):
    s, other = src(), src(NET, f"{NET}/demo")
    path = store.baseline_path(s)
    put(path, full(other, [1, 2], layer="baseline"))
    before = path.read_bytes()
    assert store.load_layer(s, "baseline").status == "invalid"
    assert path.read_bytes() == before


def test_invalid_baseline_falls_back_to_a_valid_runtime_overlay(store):
    s = src()
    store.baseline_path(s).parent.mkdir(parents=True)
    store.baseline_path(s).write_text("not json", encoding="utf-8")
    put(store.runtime_path(s), full(s, [1, 2, 3]))
    load = store.load_effective(s)
    assert load.baseline.status == "invalid" and load.runtime.status == "loaded"
    assert load.effective.covers(1, 3)
    assert any("baseline" in n.lower() for n in load.notices)


def test_baseline_with_wrong_layer_marker_is_invalid(store):
    s = src()
    put(store.baseline_path(s), full(s, [1, 2], layer="runtime"))
    assert store.load_layer(s, "baseline").status == "invalid"


# ── corrupt runtime overlay: quarantine ──────────────────────────────────────
def corrupt(store, s, data=b'{"broken": '):
    path = store.runtime_path(s)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_corrupt_overlay_is_quarantined_with_its_bytes_preserved(store):
    s = src()
    path = corrupt(store, s, b'{"broken": ')
    layer = store.load_layer(s, "runtime")
    assert layer.status == "quarantined" and layer.manifest is None
    assert not path.exists()
    assert layer.quarantined_to.name == "demo.json.corrupt-20261018T120000Z"
    assert layer.quarantined_to.read_bytes() == b'{"broken": '
    assert layer.error
    assert store.load_layer(s, "runtime").status == "missing"


def test_quarantine_name_collision_gets_a_fresh_name_and_keeps_both(store):
    s = src()
    first = corrupt(store, s, b"one")
    a = store.load_layer(s, "runtime").quarantined_to
    corrupt(store, s, b"two")
    b = store.load_layer(s, "runtime").quarantined_to
    corrupt(store, s, b"three")
    c = store.load_layer(s, "runtime").quarantined_to
    assert len({a, b, c}) == 3
    assert [p.read_bytes() for p in (a, b, c)] == [b"one", b"two", b"three"]
    assert b.name == "demo.json.corrupt-20261018T120000Z-1"
    assert first.parent.glob("*") and not first.exists()


def test_quarantine_never_overwrites_a_preexisting_quarantine_file(store):
    s = src()
    path = corrupt(store, s, b"new evidence")
    existing = path.with_name("demo.json.corrupt-20261018T120000Z")
    existing.write_bytes(b"old evidence")
    layer = store.load_layer(s, "runtime")
    assert existing.read_bytes() == b"old evidence"
    assert layer.quarantined_to != existing and layer.quarantined_to.read_bytes() == b"new evidence"


def test_quarantine_failure_is_reported_and_the_file_stays_put(store, monkeypatch):
    s = src()
    path = corrupt(store, s, b"bad bytes")

    def deny(src_path, dst_path):
        raise PermissionError("locked by another program")

    monkeypatch.setattr(chapter_store, "_quarantine_move", deny)
    put(store.baseline_path(s), full(s, [1, 2], layer="baseline"))
    load = store.load_effective(s)
    assert load.runtime.status == "quarantine-failed"
    assert path.read_bytes() == b"bad bytes"
    assert not list(path.parent.glob("*.corrupt-*")) and not list(path.parent.glob("*.tmp"))
    assert "locked by another program" in load.runtime.error
    assert load.effective.covers(1, 2)                       # fell back to the baseline
    assert any("could not be set aside" in n for n in load.notices)


def test_oversized_overlay_is_quarantined_without_being_parsed(store):
    s = src()
    corrupt(store, s, b" " * (MAX_MANIFEST_BYTES + 1))
    layer = store.load_layer(s, "runtime")
    assert layer.status == "quarantined" and "larger" in layer.error


@pytest.mark.parametrize("data", [b"", b"[]", b"\xff\xfe", b"null", b'{"schema_version": 2}'],
                         ids=["empty", "list", "not-utf8", "null", "wrong-version"])
def test_malformed_overlays_are_quarantined(store, data):
    s = src()
    corrupt(store, s, data)
    assert store.load_layer(s, "runtime").status == "quarantined"


def test_overlay_for_another_website_is_quarantined(store):
    s, other = src(), src(NET, f"{NET}/demo")
    put(store.runtime_path(s), full(other, [1, 2]))
    assert store.load_layer(s, "runtime").status == "quarantined"


def test_tampered_overlay_hash_is_quarantined_and_baseline_still_serves(store):
    s = src()
    put(store.baseline_path(s), full(s, [1, 2, 3], layer="baseline"))
    payload = full(s, [1, 2, 3]).to_json()
    payload["chapters"][0]["title"] = "edited"
    payload["content_sha256"] = "0" * 64
    store.runtime_path(s).parent.mkdir(parents=True)
    store.runtime_path(s).write_text(json.dumps(payload), encoding="utf-8")
    load = store.load_effective(s)
    assert load.runtime.status == "quarantined"
    assert load.effective.covers(1, 3)


def test_directory_in_place_of_an_overlay_loses_nothing(store):
    s = src()
    path = store.runtime_path(s)
    path.mkdir(parents=True)
    (path / "keep.txt").write_text("keep", encoding="utf-8")
    layer = store.load_layer(s, "runtime")
    assert layer.status in ("quarantined", "quarantine-failed")
    found = list(path.parent.rglob("keep.txt"))
    assert found and found[0].read_text(encoding="utf-8") == "keep"


def test_stray_temp_file_from_an_interrupted_write_is_ignored(store):
    s = src()
    put(store.runtime_path(s), full(s, [1, 2]))
    stray = store.runtime_path(s).with_name("demo.json.k3j2h1.tmp")
    stray.write_text('{"half": ', encoding="utf-8")
    layer = store.load_layer(s, "runtime")
    assert layer.status == "loaded" and stray.exists()


# ── saving the overlay (atomic) ──────────────────────────────────────────────
def test_save_runtime_creates_folders_and_round_trips(store):
    s = src()
    m = full(s, [1, 2, 3])
    path = store.save_runtime(m)
    assert path == store.runtime_path(s) and path.is_file()
    assert store.load_layer(s, "runtime").manifest == m
    assert not list(path.parent.glob("*.tmp"))


def test_save_runtime_refuses_a_baseline_manifest(store):
    s = src()
    with pytest.raises(ManifestStoreError, match="runtime"):
        store.save_runtime(full(s, [1, 2], layer="baseline"))
    assert not store.runtime_path(s).exists()


def test_save_runtime_never_creates_the_baseline_tree(store, tmp_path):
    store.save_runtime(full(src(), [1, 2]))
    assert not (tmp_path / "baseline").exists()


def test_save_replaces_an_existing_valid_overlay(store):
    s = src()
    store.save_runtime(full(s, [1, 2]))
    store.save_runtime(full(s, [1, 2, 3]))
    assert store.load_layer(s, "runtime").manifest.covers(1, 3)
    assert not list(store.runtime_path(s).parent.glob("*.corrupt-*"))


def test_failed_publication_keeps_the_old_overlay_and_leaves_no_temp_file(store, monkeypatch):
    s = src()
    store.save_runtime(full(s, [1, 2]))
    before = store.runtime_path(s).read_bytes()

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("webnovel_scraper.atomic_io.os.replace", boom)
    with pytest.raises(OSError, match="disk full"):
        store.save_runtime(full(s, [1, 2, 3]))
    monkeypatch.undo()
    assert store.runtime_path(s).read_bytes() == before
    assert not list(store.runtime_path(s).parent.glob("*.tmp"))
    assert store.load_layer(s, "runtime").manifest.covers(1, 2)
    store.save_runtime(full(s, [1, 2, 3]))                  # and recovery works
    assert store.load_layer(s, "runtime").manifest.covers(1, 3)


def test_failed_first_publication_leaves_no_file_the_loader_could_trust(store, monkeypatch):
    s = src()

    def boom(*args, **kwargs):
        raise OSError("power cut")

    monkeypatch.setattr("webnovel_scraper.atomic_io.os.replace", boom)
    with pytest.raises(OSError):
        store.save_runtime(full(s, [1, 2]))
    monkeypatch.undo()
    assert not store.runtime_path(s).exists()
    assert store.load_layer(s, "runtime").status == "missing"


def test_invalid_manifest_is_never_published(store, monkeypatch):
    s = src()
    m = full(s, [1, 2])
    object.__setattr__(m, "spans", ((1, 5),))               # corrupt it after construction
    with pytest.raises(ManifestError):
        store.save_runtime(m)
    assert not store.runtime_path(s).exists()


def test_save_over_a_corrupt_overlay_quarantines_it_first(store):
    s = src()
    corrupt(store, s, b"precious evidence")
    store.save_runtime(full(s, [1, 2]))
    kept = list(store.runtime_path(s).parent.glob("*.corrupt-*"))
    assert len(kept) == 1 and kept[0].read_bytes() == b"precious evidence"
    assert store.load_layer(s, "runtime").status == "loaded"


def test_save_refuses_to_overwrite_a_corrupt_overlay_it_cannot_set_aside(store, monkeypatch):
    s = src()
    path = corrupt(store, s, b"precious evidence")

    def deny(a, b):
        raise PermissionError("denied")

    monkeypatch.setattr(chapter_store, "_quarantine_move", deny)
    with pytest.raises(ManifestStoreError, match="set aside"):
        store.save_runtime(full(s, [1, 2]))
    assert path.read_bytes() == b"precious evidence"


def test_oversized_manifest_is_not_published(store, monkeypatch):
    s = src()
    monkeypatch.setattr(chapter_store, "MAX_MANIFEST_BYTES", 200)
    with pytest.raises(ManifestStoreError, match="larger"):
        store.save_runtime(full(s, [1, 2, 3]))
    assert not store.runtime_path(s).exists()


# ── merging baseline + overlay ───────────────────────────────────────────────
def test_merge_of_nothing_is_empty():
    effective = merge_manifests(src(), None, None)
    assert effective.chapters == () and effective.spans == () and not effective.toc_complete


def test_merge_keeps_baseline_and_adds_runtime_discoveries():
    s = src()
    base = full(s, range(1, 4), layer="baseline")
    over = manifest(s, chapters=nums(s, [4, 5]), spans=[(4, 5)])
    effective = merge_manifests(s, base, over)
    assert [c.label.text for c in effective.chapters] == ["1", "2", "3", "4", "5"]
    assert effective.spans == ((1, 5),)
    assert effective.covers(1, 5) and effective.conflicts == ()
    assert effective.baseline_sha256 == base.content_sha256
    assert effective.runtime_sha256 == over.content_sha256


def test_merge_matches_chapters_by_page_url_not_by_label():
    s = src()
    a = ch(s, "3664", slug=3678, path="3678")
    b = ch(s, "3664", slug=3679, path="3679")
    base = manifest(s, "baseline", chapters=[a], spans=[(3664, 3664)])
    over = manifest(s, chapters=[b], spans=[(3664, 3664)])
    effective = merge_manifests(s, base, over)
    assert [c.slug for c in effective.chapters] == [3678, 3679]
    assert effective.conflicts == ()
    assert set(effective.duplicate_labels()) == {ChapterLabel.parse("3664")}


def test_identical_page_in_both_layers_appears_once():
    s = src()
    base = full(s, [1, 2], layer="baseline")
    over = full(s, [1, 2])
    effective = merge_manifests(s, base, over)
    assert len(effective.chapters) == 2 and effective.conflicts == ()


def test_missing_metadata_in_one_layer_is_filled_by_the_other():
    s = src()
    base = manifest(s, "baseline", chapters=[ch(s, "1", slug=1)], spans=[(1, 1)])
    over = manifest(s, chapters=[ch(s, "1", slug=1, title="Dawn", source_id="x9")], spans=[(1, 1)])
    effective = merge_manifests(s, base, over)
    assert effective.conflicts == ()
    assert (effective.chapters[0].title, effective.chapters[0].source_id) == ("Dawn", "x9")


@pytest.mark.parametrize("field,left,right", [
    ("label", dict(label="2"), dict(label="3")),
    ("title", dict(title="Old"), dict(title="New")),
    ("slug", dict(slug=2), dict(slug=9)),
    ("source_id", dict(source_id="a"), dict(source_id="b")),
])
def test_conflicting_page_metadata_is_reported_and_the_page_dropped(field, left, right):
    s = src()
    keep = ch(s, "1", slug=1)
    page = f"{COM}/novel/demo/chapter-same"
    def make(label="2", slug=None, title=None, source_id=None):
        return s.chapter_identity(page, ChapterLabel.parse(label), slug=slug, title=title,
                                  source_id=source_id)
    base = manifest(s, "baseline", chapters=[keep, make(**left)], spans=[(1, 2)])
    over = manifest(s, chapters=[make(**right)], spans=[(2, 2)] if "label" not in left else [])
    effective = merge_manifests(s, base, over)
    assert [c.kind for c in effective.conflicts] == [field]
    conflict = effective.conflicts[0]
    assert conflict.page_urls == (page,) and field in conflict.message
    assert page not in {c.page_url for c in effective.chapters}
    assert keep in effective.chapters                     # verified baseline entries survive
    assert effective.covers(1, 1)
    assert not effective.covers(2, 2)                     # affected coverage is unverified
    assert not effective.toc_complete


def test_label_conflict_taints_both_claimed_labels():
    s = src()
    page = f"{COM}/novel/demo/chapter-moved"
    one = s.chapter_identity(page, ChapterLabel.parse("5"), slug=5)
    two = s.chapter_identity(page, ChapterLabel.parse("6"), slug=5)
    base = manifest(s, "baseline", chapters=nums(s, [4, 7]) + [one], spans=[(4, 5)])
    over = manifest(s, chapters=[two], spans=[(6, 6)])
    effective = merge_manifests(s, base, over)
    assert effective.covers(4, 4)
    assert not effective.covers(5, 5) and not effective.covers(6, 6)
    assert set(effective.conflicts[0].labels) == {"5", "6"}


def test_two_pages_claiming_one_slug_are_both_dropped_and_reported():
    s = src()
    a = ch(s, "8", slug=8, path="a")
    b = ch(s, "9", slug=8, path="b")
    base = manifest(s, "baseline", chapters=[a, ch(s, "7", slug=7)], spans=[(7, 8)])
    over = manifest(s, chapters=[b], spans=[(9, 9)])
    effective = merge_manifests(s, base, over)
    assert [c.kind for c in effective.conflicts] == ["slug-collision"]
    assert set(effective.conflicts[0].page_urls) == {a.page_url, b.page_url}
    assert effective.covers(7, 7)
    assert not effective.covers(8, 9)
    assert {c.slug for c in effective.chapters} == {7}


def test_absent_in_one_layer_versus_chapter_in_the_other_is_a_contradiction():
    s = src()
    base = manifest(s, "baseline", chapters=nums(s, [1, 3]),
                    absent=[AbsentChapter(ChapterLabel.parse("2"), "404 page")], spans=[(1, 3)])
    over = manifest(s, chapters=[ch(s, "2", slug=2)], spans=[(2, 2)])
    effective = merge_manifests(s, base, over)
    assert [c.kind for c in effective.conflicts] == ["absent-vs-chapter"]
    assert effective.absent == ()
    assert effective.covers(1, 1) and effective.covers(3, 3)
    assert not effective.covers(2, 2)


def test_absent_evidence_from_both_layers_is_unioned():
    s = src()
    base = manifest(s, "baseline", chapters=nums(s, [1, 4]),
                    absent=[AbsentChapter(ChapterLabel.parse("2"), "a")], spans=[(1, 2)])
    over = manifest(s, chapters=nums(s, [1, 4]),
                    absent=[AbsentChapter(ChapterLabel.parse("3"), "b"),
                            AbsentChapter(ChapterLabel.parse("2"), "other wording")],
                    spans=[(1, 4)])
    effective = merge_manifests(s, base, over)
    assert [a.label.text for a in effective.absent] == ["2", "3"]
    assert effective.absent[0].evidence == "a"            # baseline evidence wins on a tie
    assert effective.covers(1, 4)


def test_merge_never_widens_coverage_past_what_entries_support():
    s = src()
    base = manifest(s, "baseline", chapters=nums(s, [1, 2, 3, 10, 11]),
                    spans=[(1, 3), (10, 11)])
    effective = merge_manifests(s, base, None)
    assert effective.uncovered_spans(1, 12) == [(4, 9), (12, 12)]


def test_toc_complete_survives_only_a_clean_single_span_merge():
    s = src()
    base = full(s, [1, 2, 3], layer="baseline", complete=True)
    assert merge_manifests(s, base, None).toc_complete is True
    over = full(s, [1, 2, 3])                               # complete=False
    assert merge_manifests(s, base, over).toc_complete is False
    grown = manifest(s, chapters=nums(s, [9]), spans=[(9, 9)])
    assert merge_manifests(s, base, grown).toc_complete is False


def test_merge_is_deterministic_and_order_independent_of_layer_age():
    s = src()
    base = full(s, [1, 2], layer="baseline")
    over = manifest(s, chapters=nums(s, [3]), spans=[(3, 3)])
    first = merge_manifests(s, base, over)
    second = merge_manifests(s, base, over)
    assert first == second


def test_merge_refuses_a_layer_from_another_source():
    s, other = src(), src(NET, f"{NET}/demo")
    with pytest.raises(ManifestError, match="source"):
        merge_manifests(s, full(other, [1, 2], layer="baseline"), None)


def test_merge_refuses_layers_filed_under_the_wrong_name():
    s = src()
    with pytest.raises(ManifestError, match="layer"):
        merge_manifests(s, full(s, [1, 2], layer="runtime"), None)
    with pytest.raises(ManifestError, match="layer"):
        merge_manifests(s, None, full(s, [1, 2], layer="baseline"))


def test_conflict_diagnostics_have_readable_notices():
    s = src()
    page = f"{COM}/novel/demo/chapter-x"
    base = manifest(s, "baseline", chapters=[s.chapter_identity(page, ChapterLabel.parse("1"))])
    over = manifest(s, chapters=[s.chapter_identity(page, ChapterLabel.parse("2"))])
    effective = merge_manifests(s, base, over)
    assert effective.has_conflicts
    notice = effective.notices()[0]
    assert "chapter-x" in notice and "rediscover" in notice.lower()


def test_effective_can_become_a_valid_runtime_manifest():
    s = src()
    base = full(s, [1, 2], layer="baseline")
    over = manifest(s, chapters=nums(s, [3]), spans=[(3, 3)])
    effective = merge_manifests(s, base, over)
    again = effective.as_manifest("runtime", prov())
    assert again.covers(1, 3) and len(again.chapters) == 3


def test_load_effective_merges_both_files_from_disk(store):
    s = src()
    put(store.baseline_path(s), full(s, [1, 2, 3], layer="baseline"))
    put(store.runtime_path(s), manifest(s, chapters=nums(s, [4]), spans=[(4, 4)]))
    load = store.load_effective(s)
    assert load.effective.covers(1, 4) and load.notices == ()


def test_load_effective_reports_conflicts_without_choosing_a_newer_file(store):
    s = src()
    page = f"{COM}/novel/demo/chapter-x"
    put(store.baseline_path(s), manifest(
        s, "baseline", chapters=[s.chapter_identity(page, ChapterLabel.parse("1"), title="A")]))
    put(store.runtime_path(s), manifest(
        s, chapters=[s.chapter_identity(page, ChapterLabel.parse("1"), title="B")]))
    load = store.load_effective(s)
    assert load.effective.has_conflicts and load.effective.chapters == ()
    assert load.notices and "chapter-x" in load.notices[0]
