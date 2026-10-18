"""Read-only import of a legacy output-scoped ``chapter_index.json`` (v0.3.0 Checkpoint 03.1,
plan §9.4). Offline; every legacy file is synthetic and lives under ``tmp_path``.

The legacy file must never be moved, rewritten or deleted, whatever the outcome.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from webnovel_scraper import chapter_store
from webnovel_scraper.chapter_manifest import ChapterManifest, Provenance
from webnovel_scraper.chapter_store import (
    ChapterManifestStore,
    LegacyIndexRejected,
    parse_legacy_index,
)
from webnovel_scraper.source_identity import ChapterLabel, SourceIdentity

COM = "https://freewebnovel.com"
NET = "https://freewebnovel.net"
NOW = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)


def src(origin=COM, index_url=f"{COM}/novel/demo", slug="demo") -> SourceIdentity:
    return SourceIdentity(novel_slug=slug, adapter_key="freewebnovel", origin=origin,
                          index_url=index_url)


def legacy_rows(origin, numbers, host_slug="demo"):
    return [{"index": n, "url": f"{origin}/novel/{host_slug}/chapter-{n}", "title": None,
             "source_id": None, "slug": int(n) if float(n).is_integer() else None}
            for n in numbers]


def legacy_payload(source=None, numbers=range(1, 6), **over) -> dict:
    source = source or src()
    payload = {
        "schema_version": 2,
        "identity": {"novel_slug": source.novel_slug, "adapter_key": source.adapter_key,
                     "source_url": source.index_url, "base_url": source.origin},
        "complete": True,
        "chapters": legacy_rows(source.origin, list(numbers)),
    }
    payload.update(over)
    return payload


@pytest.fixture
def store(tmp_path):
    return ChapterManifestStore(tmp_path / "baseline", tmp_path / "runtime", clock=lambda: NOW)


@pytest.fixture
def legacy_dir(tmp_path):
    folder = tmp_path / "old-output" / "demo-1"
    folder.mkdir(parents=True)
    return folder


def write_legacy(folder: Path, payload) -> Path:
    path = folder / "chapter_index.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def fingerprint(folder: Path) -> dict:
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(folder.iterdir())}


def runtime_tree(store, source) -> list:
    return sorted(p.name for p in store.runtime_path(source).parent.glob("*")) \
        if store.runtime_path(source).parent.exists() else []


# ── accepted imports ─────────────────────────────────────────────────────────
def test_valid_v2_index_is_imported_into_the_runtime_overlay(store, legacy_dir):
    s = src()
    path = write_legacy(legacy_dir, legacy_payload(s, range(1, 11)))
    result = store.import_legacy_index(s, path)
    assert result.status == "imported", result.reason
    assert result.chapters == 10
    layer = store.load_layer(s, "runtime")
    manifest = layer.manifest
    assert manifest.layer == "runtime"
    assert manifest.spans == ((1, 10),)
    assert manifest.toc_complete is False                    # the tail is not assumed to end here
    assert [c.label.text for c in manifest.chapters] == [str(n) for n in range(1, 11)]
    assert manifest.chapters[0].slug == 1
    assert manifest.provenance.method == "legacy-migration"
    assert manifest.provenance.validated_utc == "2026-10-18T12:00:00Z"


def test_provenance_records_the_digest_of_the_legacy_bytes_and_no_path(store, legacy_dir):
    s = src()
    path = write_legacy(legacy_dir, legacy_payload(s))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    result = store.import_legacy_index(s, path)
    assert result.legacy_sha256 == digest
    manifest = store.load_layer(s, "runtime").manifest
    assert manifest.provenance.legacy_index_sha256 == digest
    on_disk = store.runtime_path(s).read_text(encoding="utf-8")
    assert str(legacy_dir) not in on_disk and "old-output" not in on_disk


def test_only_contiguous_whole_number_runs_become_coverage(store, legacy_dir):
    s = src()
    path = write_legacy(legacy_dir, legacy_payload(s, [1, 2, 3, 7, 8, 9]))
    assert store.import_legacy_index(s, path).status == "imported"
    manifest = store.load_layer(s, "runtime").manifest
    assert manifest.spans == ((1, 3), (7, 9))
    assert manifest.uncovered_spans(1, 12) == [(4, 6), (10, 12)]   # gap and unknown tail
    assert manifest.absent == ()                                    # a gap is not an absence


def test_fractional_labels_are_listed_but_never_widen_coverage(store, legacy_dir):
    s = src()
    path = write_legacy(legacy_dir, legacy_payload(s, [1, 2, 626, 627, 627.2, 628, 629]))
    assert store.import_legacy_index(s, path).status == "imported"
    manifest = store.load_layer(s, "runtime").manifest
    assert "627.2" in [c.label.text for c in manifest.chapters]
    assert manifest.spans == ((1, 2), (626, 629))


def test_integral_float_collapses_but_precision_already_lost_is_not_invented(store, legacy_dir):
    s = src()
    rows = legacy_rows(COM, [1, 2])
    rows[0]["index"] = 1.0
    rows.append({"index": 627.1, "url": f"{COM}/novel/demo/chapter-627-1", "title": "Bonus",
                 "source_id": "b1", "slug": None})
    path = write_legacy(legacy_dir, legacy_payload(s, chapters=rows))
    assert store.import_legacy_index(s, path).status == "imported"
    labels = [c.label.text for c in store.load_layer(s, "runtime").manifest.chapters]
    assert labels == ["1", "2", "627.1"]


def test_duplicate_displayed_numbers_on_distinct_pages_are_both_imported(store, legacy_dir):
    s = src()
    rows = legacy_rows(COM, [3663])
    rows += [{"index": 3664, "url": f"{COM}/novel/demo/chapter-3678", "title": None,
              "source_id": None, "slug": 3678},
             {"index": 3664, "url": f"{COM}/novel/demo/chapter-3679", "title": None,
              "source_id": None, "slug": 3679}]
    path = write_legacy(legacy_dir, legacy_payload(s, chapters=rows))
    assert store.import_legacy_index(s, path).status == "imported"
    manifest = store.load_layer(s, "runtime").manifest
    assert [c.slug for c in manifest.chapters if c.label.text == "3664"] == [3678, 3679]
    assert manifest.covers(3663, 3664)


def test_empty_base_url_falls_back_to_the_source_url_origin(store, legacy_dir):
    s = src()
    payload = legacy_payload(s)
    payload["identity"]["base_url"] = ""
    path = write_legacy(legacy_dir, payload)
    assert store.import_legacy_index(s, path).status == "imported"


def test_import_into_the_net_site_uses_its_own_path(store, legacy_dir):
    net = src(NET, f"{NET}/demo")
    rows = [{"index": n, "url": f"{NET}/demo/chapter-{n}-x.html", "title": None,
             "source_id": None, "slug": n} for n in range(1, 4)]
    path = write_legacy(legacy_dir, legacy_payload(net, chapters=rows))
    assert store.import_legacy_index(net, path).status == "imported"
    assert store.runtime_path(net).parent.name == "freewebnovel.net"
    assert not store.runtime_path(src()).exists()


# ── every rejected legacy shape ──────────────────────────────────────────────
def rejected(legacy_dir, store, payload, *, raw=None, source=None) -> str:
    source = source or src()
    path = legacy_dir / "chapter_index.json"
    if raw is not None:
        path.write_bytes(raw)
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    before = fingerprint(legacy_dir)
    result = store.import_legacy_index(source, path)
    assert result.status == "rejected", (result.status, result.reason)
    assert result.reason
    assert fingerprint(legacy_dir) == before                  # the legacy file is untouched
    assert not store.runtime_path(source).exists()             # and nothing was imported
    return result.reason


def test_bare_list_index_is_rejected(store, legacy_dir):
    assert "list" in rejected(legacy_dir, store, legacy_rows(COM, [1, 2, 3])).lower()


def test_schema_v1_dict_is_rejected(store, legacy_dir):
    assert "schema" in rejected(legacy_dir, store, legacy_payload(schema_version=1)).lower()


@pytest.mark.parametrize("version", [3, "2", None, 2.0, True, 0],
                         ids=["three", "text", "null", "float", "bool", "zero"])
def test_other_schema_versions_are_rejected(store, legacy_dir, version):
    rejected(legacy_dir, store, legacy_payload(schema_version=version))


def test_missing_identity_is_rejected_even_though_the_old_loader_trusted_it(store, legacy_dir):
    payload = legacy_payload()
    del payload["identity"]
    assert "identity" in rejected(legacy_dir, store, payload).lower()


@pytest.mark.parametrize("identity", [None, "demo", [], {}, {"novel_slug": "demo"}],
                         ids=["null", "string", "list", "empty", "partial"])
def test_unusable_identity_blocks_are_rejected(store, legacy_dir, identity):
    rejected(legacy_dir, store, legacy_payload(identity=identity))


def test_incomplete_index_is_rejected(store, legacy_dir):
    assert "complete" in rejected(legacy_dir, store, legacy_payload(complete=False)).lower()


def test_missing_complete_flag_is_rejected(store, legacy_dir):
    payload = legacy_payload()
    del payload["complete"]
    rejected(legacy_dir, store, payload)


@pytest.mark.parametrize("flag", ["true", 1, None])
def test_non_boolean_complete_flag_is_rejected(store, legacy_dir, flag):
    rejected(legacy_dir, store, legacy_payload(complete=flag))


def test_cross_host_identity_is_rejected(store, legacy_dir):
    net = src(NET, f"{NET}/demo")
    payload = legacy_payload(net)
    rows = [{"index": n, "url": f"{NET}/demo/chapter-{n}.html", "title": None, "source_id": None,
             "slug": n} for n in range(1, 4)]
    payload["chapters"] = rows
    assert "match" in rejected(legacy_dir, store, payload, source=src()).lower()


def test_identity_for_another_novel_or_adapter_is_rejected(store, legacy_dir):
    payload = legacy_payload()
    payload["identity"]["novel_slug"] = "other"
    rejected(legacy_dir, store, payload)
    payload = legacy_payload()
    payload["identity"]["adapter_key"] = "telegraph"
    rejected(legacy_dir, store, payload)
    payload = legacy_payload()
    payload["identity"]["source_url"] = f"{COM}/novel/other"
    rejected(legacy_dir, store, payload)


def test_identity_naming_an_unsupported_host_is_rejected(store, legacy_dir):
    payload = legacy_payload()
    payload["identity"]["base_url"] = "https://evil.example"
    payload["identity"]["source_url"] = "https://evil.example/novel/demo"
    rejected(legacy_dir, store, payload)


@pytest.mark.parametrize("mutate", [
    lambda r: r.pop("url"),
    lambda r: r.pop("index"),
    lambda r: r.update(index="12"),
    lambda r: r.update(index=True),
    lambda r: r.update(index=-3),
    lambda r: r.update(index=10**9),
    lambda r: r.update(index=None),
    lambda r: r.update(url=None),
    lambda r: r.update(url="not a url"),
    lambda r: r.update(url="http://freewebnovel.com/novel/demo/chapter-1"),
    lambda r: r.update(url="https://freewebnovel.net/novel/demo/chapter-1"),
    lambda r: r.update(slug=-1),
    lambda r: r.update(slug="7"),
    lambda r: r.update(slug=True),
    lambda r: r.update(title=7),
    lambda r: r.update(title="bad\x00title"),
    lambda r: r.update(source_id=""),
    lambda r: r.update(extra="field"),
], ids=[f"row-{i}" for i in range(18)])
def test_malformed_chapter_rows_are_rejected(store, legacy_dir, mutate):
    payload = legacy_payload(numbers=range(1, 4))
    mutate(payload["chapters"][1])
    rejected(legacy_dir, store, payload)


def test_row_that_is_not_an_object_is_rejected(store, legacy_dir):
    payload = legacy_payload()
    payload["chapters"][0] = [1, "x"]
    rejected(legacy_dir, store, payload)


def test_duplicate_page_urls_are_rejected(store, legacy_dir):
    payload = legacy_payload(numbers=range(1, 4))
    payload["chapters"][2]["url"] = payload["chapters"][1]["url"]
    payload["chapters"][2]["slug"] = None
    assert "more than once" in rejected(legacy_dir, store, payload)


def test_empty_chapter_list_is_rejected(store, legacy_dir):
    rejected(legacy_dir, store, legacy_payload(chapters=[]))


@pytest.mark.parametrize("chapters", [None, {}, "x", 5])
def test_chapters_that_are_not_a_list_are_rejected(store, legacy_dir, chapters):
    rejected(legacy_dir, store, legacy_payload(chapters=chapters))


@pytest.mark.parametrize("raw", [b"", b"{", b"null", b"3", b"\xff\xfe", b'{"schema_version": 2, "schema_version": 2}',
                                 b'{"schema_version": NaN}'],
                         ids=["empty", "truncated", "null", "number", "not-utf8", "dup-key", "nan"])
def test_unparseable_files_are_rejected(store, legacy_dir, raw):
    rejected(legacy_dir, store, None, raw=raw)


def test_oversized_legacy_file_is_rejected_without_reading_it_all(store, legacy_dir, monkeypatch):
    monkeypatch.setattr(chapter_store, "MAX_MANIFEST_BYTES", 100)
    rejected(legacy_dir, store, legacy_payload(numbers=range(1, 30)))


def test_hostile_nesting_is_rejected(store, legacy_dir):
    rejected(legacy_dir, store, None, raw=b"[" * 50000 + b"]" * 50000)


def test_missing_legacy_file_is_reported_not_raised(store, tmp_path):
    result = store.import_legacy_index(src(), tmp_path / "nowhere" / "chapter_index.json")
    assert result.status == "rejected" and "read" in result.reason.lower()


def test_legacy_path_that_is_a_folder_is_rejected(store, legacy_dir):
    result = store.import_legacy_index(src(), legacy_dir)
    assert result.status == "rejected"


# ── idempotence and overlay protection ───────────────────────────────────────
def test_import_is_idempotent(store, legacy_dir):
    s = src()
    path = write_legacy(legacy_dir, legacy_payload(s, range(1, 8)))
    assert store.import_legacy_index(s, path).status == "imported"
    runtime = store.runtime_path(s)
    first = (runtime.read_bytes(), runtime.stat().st_mtime_ns)
    again = store.import_legacy_index(s, path)
    assert again.status == "already-covered"
    assert (runtime.read_bytes(), runtime.stat().st_mtime_ns) == first


def test_import_does_not_overwrite_newer_validated_runtime_coverage(store, legacy_dir):
    s = src()
    runtime = ChapterManifest.create(
        layer="runtime", source=s, toc_complete=False, spans=[(1, 10)],
        provenance=Provenance("2026-10-18T10:00:00Z", "2026-10-18T11:00:00Z", "0.3.0", "toc"),
        chapters=[s.chapter_identity(f"{COM}/novel/demo/chapter-{n}", ChapterLabel.parse(str(n)),
                                     slug=n, title=f"Fresh {n}") for n in range(1, 11)],
    )
    store.save_runtime(runtime)
    before = (store.runtime_path(s).read_bytes(), store.runtime_path(s).stat().st_mtime_ns)
    path = write_legacy(legacy_dir, legacy_payload(s, range(1, 6)))
    result = store.import_legacy_index(s, path)
    assert result.status == "already-covered"
    assert (store.runtime_path(s).read_bytes(), store.runtime_path(s).stat().st_mtime_ns) == before


def test_import_that_extends_the_overlay_keeps_existing_entries_byte_for_byte(store, legacy_dir):
    s = src()
    existing = ChapterManifest.create(
        layer="runtime", source=s, toc_complete=False, spans=[(1, 3)],
        provenance=Provenance("2026-10-18T10:00:00Z", "2026-10-18T11:00:00Z", "0.3.0", "walk"),
        chapters=[s.chapter_identity(f"{COM}/novel/demo/chapter-{n}", ChapterLabel.parse(str(n)),
                                     slug=n, title=f"Fresh {n}") for n in range(1, 4)],
    )
    store.save_runtime(existing)
    path = write_legacy(legacy_dir, legacy_payload(s, range(1, 8)))
    result = store.import_legacy_index(s, path)
    assert result.status == "imported"
    merged = store.load_layer(s, "runtime").manifest
    assert merged.covers(1, 7) and not merged.covers(8, 8)
    assert [c.title for c in merged.chapters[:3]] == ["Fresh 1", "Fresh 2", "Fresh 3"]
    assert merged.provenance.method == "walk"                          # not relabelled
    assert merged.provenance.legacy_index_sha256 == result.legacy_sha256


def test_import_covered_by_the_baseline_writes_nothing(store, legacy_dir):
    s = src()
    base = ChapterManifest.create(
        layer="baseline", source=s, toc_complete=True, spans=[(1, 9)],
        provenance=Provenance("2026-10-18T10:00:00Z", "2026-10-18T11:00:00Z", "0.3.0", "toc"),
        chapters=[s.chapter_identity(f"{COM}/novel/demo/chapter-{n}", ChapterLabel.parse(str(n)),
                                     slug=n) for n in range(1, 10)],
    )
    store.baseline_path(s).parent.mkdir(parents=True)
    store.baseline_path(s).write_text(json.dumps(base.to_json()), encoding="utf-8")
    path = write_legacy(legacy_dir, legacy_payload(s, range(1, 6)))
    assert store.import_legacy_index(s, path).status == "already-covered"
    assert not store.runtime_path(s).exists()


def test_legacy_data_that_contradicts_the_overlay_is_not_imported(store, legacy_dir):
    s = src()
    existing = ChapterManifest.create(
        layer="runtime", source=s, toc_complete=False, spans=[(1, 2)],
        provenance=Provenance("2026-10-18T10:00:00Z", "2026-10-18T11:00:00Z", "0.3.0", "toc"),
        chapters=[s.chapter_identity(f"{COM}/novel/demo/chapter-{n}", ChapterLabel.parse(str(n)),
                                     slug=n) for n in (1, 2)],
    )
    store.save_runtime(existing)
    before = store.runtime_path(s).read_bytes()
    payload = legacy_payload(s, range(1, 6))
    payload["chapters"][0]["index"] = 99               # same page, different displayed number
    path = write_legacy(legacy_dir, payload)
    result = store.import_legacy_index(s, path)
    assert result.status == "conflict" and "chapter-1" in result.reason
    assert store.runtime_path(s).read_bytes() == before


def test_legacy_data_that_contradicts_the_baseline_is_not_imported(store, legacy_dir):
    s = src()
    base = ChapterManifest.create(
        layer="baseline", source=s, toc_complete=False, spans=[(1, 2)],
        provenance=Provenance("2026-10-18T10:00:00Z", "2026-10-18T11:00:00Z", "0.3.0", "toc"),
        chapters=[s.chapter_identity(f"{COM}/novel/demo/chapter-{n}", ChapterLabel.parse(str(n)),
                                     slug=n) for n in (1, 2)],
    )
    store.baseline_path(s).parent.mkdir(parents=True)
    store.baseline_path(s).write_text(json.dumps(base.to_json()), encoding="utf-8")
    payload = legacy_payload(s, range(1, 6))
    payload["chapters"][1]["slug"] = 77                # same page, different slug
    path = write_legacy(legacy_dir, payload)
    assert store.import_legacy_index(s, path).status == "conflict"
    assert not store.runtime_path(s).exists()


# ── the legacy file is never touched ─────────────────────────────────────────
@pytest.mark.parametrize("variant", ["imported", "again", "rejected"])
def test_legacy_file_is_never_modified_moved_or_deleted(store, legacy_dir, variant):
    s = src()
    payload = legacy_payload(s, range(1, 6))
    if variant == "rejected":
        payload["complete"] = False
    path = write_legacy(legacy_dir, payload)
    before = fingerprint(legacy_dir)
    store.import_legacy_index(s, path)
    if variant == "again":
        store.import_legacy_index(s, path)
    assert fingerprint(legacy_dir) == before
    assert sorted(p.name for p in legacy_dir.iterdir()) == ["chapter_index.json"]


def test_import_does_not_leave_files_next_to_the_legacy_folder(store, legacy_dir):
    s = src()
    path = write_legacy(legacy_dir, legacy_payload(s))
    siblings = sorted(p.name for p in legacy_dir.parent.iterdir())
    store.import_legacy_index(s, path)
    assert sorted(p.name for p in legacy_dir.parent.iterdir()) == siblings


def test_failed_publication_during_import_reports_an_error_and_keeps_everything(store, legacy_dir, monkeypatch):
    s = src()
    path = write_legacy(legacy_dir, legacy_payload(s))
    before = fingerprint(legacy_dir)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("webnovel_scraper.atomic_io.os.replace", boom)
    result = store.import_legacy_index(s, path)
    monkeypatch.undo()
    assert result.status == "error" and "disk full" in result.reason
    assert fingerprint(legacy_dir) == before
    assert not store.runtime_path(s).exists()
    assert store.import_legacy_index(s, path).status == "imported"      # and a retry works


def test_import_never_scans_for_legacy_files_on_its_own(store, legacy_dir):
    s = src()
    write_legacy(legacy_dir, legacy_payload(s))
    store.load_effective(s)
    assert not store.runtime_path(s).exists()           # nothing is imported without a call


# ── the pure parser ──────────────────────────────────────────────────────────
def test_parse_legacy_index_returns_a_valid_runtime_manifest():
    s = src()
    data = json.dumps(legacy_payload(s, range(1, 4))).encode()
    manifest = parse_legacy_index(s, data, now_utc="2026-10-18T12:00:00Z", app_version="0.3.0")
    assert isinstance(manifest, ChapterManifest) and manifest.covers(1, 3)
    assert manifest.provenance.legacy_index_sha256 == hashlib.sha256(data).hexdigest()


def test_parse_legacy_index_raises_a_specific_rejection():
    with pytest.raises(LegacyIndexRejected):
        parse_legacy_index(src(), b"[]", now_utc="2026-10-18T12:00:00Z", app_version="0.3.0")


def test_legacy_loader_in_the_pipeline_is_unchanged_by_this_checkpoint():
    """03.1 adds a safer migration API but leaves pipeline._load_index alone (03.2 swaps it)."""
    from webnovel_scraper import pipeline
    assert pipeline.INDEX_SCHEMA_VERSION == 2
    assert not hasattr(pipeline, "chapter_store") and not hasattr(pipeline, "ChapterManifestStore")
