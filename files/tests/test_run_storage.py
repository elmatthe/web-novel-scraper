"""Safe output-directory allocation (v0.3.0 Checkpoint 03.0). Offline, tmp_path only.

New runs reserve ``<root>/<canonical-novel-slug>-N`` with an atomic exclusive mkdir. A
preview is never a reservation, nothing is ever overwritten or reused, and reopening an
existing run is a separate, validated action (``open_existing_run``).
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from webnovel_scraper import run_storage
from webnovel_scraper.atomic_io import write_json_atomic
from webnovel_scraper.run_storage import (
    RunStorageError,
    allocate_run_dir,
    default_output_root,
    open_existing_run,
    preview_next_run_dir,
)

SLUG = "lord-of-the-mysteries"
REPO_ROOT = Path(__file__).resolve().parents[2]


def listing(path: Path) -> list[str]:
    return sorted(p.name for p in path.iterdir())


# ── default root ─────────────────────────────────────────────────────────────
def test_default_root_is_downloads_web_novel_scraper_outputs(tmp_path, monkeypatch):
    (tmp_path / "Downloads").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert default_output_root() == tmp_path / "Downloads" / "Web-Novel-Scraper-Outputs"


def test_default_root_falls_back_to_home_without_a_downloads_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert default_output_root() == tmp_path / "Web-Novel-Scraper-Outputs"


def test_default_root_is_never_inside_the_repository():
    root = default_output_root().resolve()
    assert REPO_ROOT not in root.parents and root != REPO_ROOT


# ── numbering ────────────────────────────────────────────────────────────────
def test_first_run_is_number_one_and_has_a_valid_reservation_marker(tmp_path):
    run = allocate_run_dir(tmp_path / "out", SLUG)
    assert run.path == tmp_path / "out" / f"{SLUG}-1"
    assert (run.novel_slug, run.number) == (SLUG, 1)
    assert run.path.is_dir()
    marker = json.loads((run.path / run_storage.RESERVATION_FILENAME).read_text(encoding="utf-8"))
    assert marker["schema_version"] == 1
    assert marker["kind"] == "web-novel-scraper-run"
    assert (marker["novel_slug"], marker["number"]) == (SLUG, 1)
    assert isinstance(marker["created_utc"], str) and marker["created_utc"].endswith("Z")


def test_every_new_run_gets_a_fresh_numbered_folder(tmp_path):
    runs = [allocate_run_dir(tmp_path, SLUG) for _ in range(3)]
    assert [r.number for r in runs] == [1, 2, 3]
    assert [r.path.name for r in runs] == [f"{SLUG}-{n}" for n in (1, 2, 3)]


def test_numbering_is_independent_per_novel(tmp_path):
    a = allocate_run_dir(tmp_path, SLUG)
    b = allocate_run_dir(tmp_path, "shadow-slave")
    c = allocate_run_dir(tmp_path, SLUG)
    assert (a.number, b.number, c.number) == (1, 1, 2)


def test_numbering_ignores_the_website_by_construction(tmp_path):
    import inspect

    assert list(inspect.signature(allocate_run_dir).parameters)[:2] == ["root", "novel_slug"]
    assert not {"site", "host", "adapter_key", "website"} & set(
        inspect.signature(allocate_run_dir).parameters
    )


def test_the_first_free_number_is_used(tmp_path):
    (tmp_path / f"{SLUG}-1").mkdir()
    (tmp_path / f"{SLUG}-3").mkdir()
    assert allocate_run_dir(tmp_path, SLUG).number == 2
    assert allocate_run_dir(tmp_path, SLUG).number == 4


def test_a_novel_whose_slug_is_a_prefix_does_not_confuse_numbering(tmp_path):
    (tmp_path / "lord-of-mysteries-2-1").mkdir()
    assert allocate_run_dir(tmp_path, "lord-of-mysteries").number == 1


# ── collisions: nothing existing is overwritten or reused ─────────────────────
def test_existing_runs_are_never_touched(tmp_path):
    first = tmp_path / f"{SLUG}-1"
    first.mkdir()
    (first / "keep.pdf").write_bytes(b"precious")
    run = allocate_run_dir(tmp_path, SLUG)
    assert run.number == 2
    assert (first / "keep.pdf").read_bytes() == b"precious"
    assert listing(first) == ["keep.pdf"]


def test_an_empty_abandoned_folder_is_skipped_not_reused(tmp_path):
    (tmp_path / f"{SLUG}-1").mkdir()
    assert allocate_run_dir(tmp_path, SLUG).number == 2
    assert listing(tmp_path / f"{SLUG}-1") == []


def test_a_file_squatting_on_the_name_is_skipped(tmp_path):
    (tmp_path / f"{SLUG}-1").write_text("not a folder", encoding="utf-8")
    assert allocate_run_dir(tmp_path, SLUG).number == 2
    assert (tmp_path / f"{SLUG}-1").read_text(encoding="utf-8") == "not a folder"


def make_directory_link(link: Path, target: Path) -> None:
    """A symlink, or on Windows without symlink rights a junction (needs no privilege)."""
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        if os.name != "nt":
            raise
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                   check=True, capture_output=True)


def test_a_directory_link_squatting_on_the_name_is_skipped(tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    make_directory_link(tmp_path / f"{SLUG}-1", target)
    assert allocate_run_dir(tmp_path, SLUG).number == 2
    assert listing(target) == []


def test_a_claim_made_after_the_scan_is_detected_by_the_exclusive_mkdir(tmp_path, monkeypatch):
    """The directory listing is only a hint: a stale scan must still end in a free folder."""
    (tmp_path / f"{SLUG}-1").mkdir()
    monkeypatch.setattr(run_storage, "_taken_numbers", lambda root, slug: set())
    run = allocate_run_dir(tmp_path, SLUG)
    assert run.number == 2
    assert listing(tmp_path / f"{SLUG}-1") == []


def test_exhausting_the_number_space_is_an_error_not_an_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(run_storage, "MAX_RUN_NUMBER", 3)
    for n in (1, 2, 3):
        (tmp_path / f"{SLUG}-{n}").mkdir()
    with pytest.raises(RunStorageError, match="free run number"):
        allocate_run_dir(tmp_path, SLUG)


# ── concurrency ──────────────────────────────────────────────────────────────
def test_concurrent_threads_never_claim_the_same_folder(tmp_path):
    count = 16
    barrier = threading.Barrier(count)
    results, errors = [], []

    def work():
        try:
            barrier.wait()
            results.append(allocate_run_dir(tmp_path, SLUG))
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert sorted(r.number for r in results) == list(range(1, count + 1))
    assert len({r.path for r in results}) == count


def test_concurrent_processes_never_claim_the_same_folder(tmp_path):
    count = 6
    with ProcessPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(allocate_run_dir, tmp_path, SLUG) for _ in range(count)]
        results = [f.result(timeout=120) for f in futures]
    assert sorted(r.number for r in results) == list(range(1, count + 1))
    assert len({r.path for r in results}) == count
    assert listing(tmp_path) == sorted(f"{SLUG}-{n}" for n in range(1, count + 1))


# ── invalid names and roots ──────────────────────────────────────────────────
@pytest.mark.parametrize(
    "slug",
    ["", "..", ".", "../evil", "a/b", "a\\b", "C:\\x", "Upper", "a b", "a..b", "-a", "a-",
     "con\x00", "x" * 81, None, 5],
)
def test_invalid_slugs_are_rejected_before_anything_is_created(tmp_path, slug):
    root = tmp_path / "out"
    with pytest.raises(RunStorageError):
        allocate_run_dir(root, slug)
    assert not root.exists()


def test_the_root_must_be_a_directory(tmp_path):
    squatter = tmp_path / "out"
    squatter.write_text("file", encoding="utf-8")
    with pytest.raises(RunStorageError, match="not a folder"):
        allocate_run_dir(squatter, SLUG)
    with pytest.raises(RunStorageError):
        preview_next_run_dir(squatter, SLUG)


def test_a_missing_root_is_created(tmp_path):
    run = allocate_run_dir(tmp_path / "deep" / "er" / "out", SLUG)
    assert run.path.is_dir()


def test_the_allocated_path_is_a_direct_child_of_the_root(tmp_path):
    run = allocate_run_dir(tmp_path, SLUG)
    assert run.path.parent == tmp_path


# ── failure and cleanup ──────────────────────────────────────────────────────
def test_a_failed_reservation_marker_removes_the_empty_folder(tmp_path, monkeypatch):
    def boom(path, payload):
        raise OSError("disk full")

    monkeypatch.setattr(run_storage, "write_json_atomic", boom)
    with pytest.raises(RunStorageError, match="disk full"):
        allocate_run_dir(tmp_path, SLUG)
    assert listing(tmp_path) == []
    monkeypatch.undo()
    assert allocate_run_dir(tmp_path, SLUG).number == 1


def test_cleanup_never_deletes_content_and_the_leftover_is_not_reused(tmp_path, monkeypatch):
    def partial(path, payload):
        Path(path).with_name("half-written.tmp").write_text("x", encoding="utf-8")
        raise OSError("interrupted")

    monkeypatch.setattr(run_storage, "write_json_atomic", partial)
    with pytest.raises(RunStorageError, match="interrupted"):
        allocate_run_dir(tmp_path, SLUG)
    leftover = tmp_path / f"{SLUG}-1"
    assert leftover.is_dir() and (leftover / "half-written.tmp").exists()  # not recursively removed
    monkeypatch.undo()
    assert allocate_run_dir(tmp_path, SLUG).number == 2  # partial folder is never silently reused


def test_an_unexpected_mkdir_error_is_reported_and_not_retried_forever(tmp_path, monkeypatch):
    calls = []

    def deny(path):
        calls.append(path)
        raise PermissionError("denied")

    monkeypatch.setattr(run_storage, "_mkdir_exclusive", deny)
    with pytest.raises(RunStorageError, match="denied"):
        allocate_run_dir(tmp_path, SLUG)
    assert len(calls) == 1


# ── preview is not a reservation ─────────────────────────────────────────────
def test_preview_creates_nothing_even_for_a_missing_root(tmp_path):
    root = tmp_path / "out"
    assert preview_next_run_dir(root, SLUG) == root / f"{SLUG}-1"
    assert not root.exists()


def test_preview_matches_an_uncontended_allocation(tmp_path):
    (tmp_path / f"{SLUG}-1").mkdir()
    preview = preview_next_run_dir(tmp_path, SLUG)
    assert preview_next_run_dir(tmp_path, SLUG) == preview  # repeatable: it reserves nothing
    assert allocate_run_dir(tmp_path, SLUG).path == preview


def test_a_run_claimed_after_a_preview_is_never_handed_out_again(tmp_path):
    preview = preview_next_run_dir(tmp_path, SLUG)
    other = allocate_run_dir(tmp_path, SLUG)  # someone else gets there first
    mine = allocate_run_dir(tmp_path, SLUG)
    assert other.path == preview
    assert mine.path != preview


@pytest.mark.parametrize("slug", ["../x", "", "A"])
def test_preview_validates_the_slug_too(tmp_path, slug):
    with pytest.raises(RunStorageError):
        preview_next_run_dir(tmp_path, slug)


# ── reopening an existing run is a separate, validated action ─────────────────
def test_open_existing_run_accepts_an_allocated_run_without_changing_it(tmp_path):
    run = allocate_run_dir(tmp_path, SLUG)
    (run.path / "PDF").mkdir()
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    opened = open_existing_run(tmp_path, run.path, expected_novel_slug=SLUG)
    assert opened == run
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == before


def test_open_existing_run_does_not_require_an_expected_slug(tmp_path):
    run = allocate_run_dir(tmp_path, SLUG)
    assert open_existing_run(tmp_path, run.path).novel_slug == SLUG


def test_open_existing_run_rejects_a_different_novel(tmp_path):
    run = allocate_run_dir(tmp_path, SLUG)
    with pytest.raises(RunStorageError, match="different novel"):
        open_existing_run(tmp_path, run.path, expected_novel_slug="shadow-slave")


def test_open_existing_run_rejects_folders_outside_or_below_the_root(tmp_path):
    other_root = tmp_path / "other"
    run = allocate_run_dir(other_root, SLUG)
    with pytest.raises(RunStorageError, match="not directly inside"):
        open_existing_run(tmp_path / "mine", run.path)
    nested = tmp_path / "mine" / "sub"
    nested_run = allocate_run_dir(nested, SLUG)
    with pytest.raises(RunStorageError, match="not directly inside"):
        open_existing_run(tmp_path / "mine", nested_run.path)


def test_open_existing_run_rejects_traversal_in_the_path(tmp_path):
    run = allocate_run_dir(tmp_path, SLUG)
    sneaky = tmp_path / "x" / ".." / run.path.name
    with pytest.raises(RunStorageError, match="not directly inside"):
        open_existing_run(tmp_path, sneaky)


@pytest.mark.parametrize("name", ["notarun", f"{SLUG}", f"{SLUG}-0", f"{SLUG}-01", f"{SLUG}--1",
                                  "Upper-1", f"{SLUG}-1.bak"])
def test_open_existing_run_rejects_names_that_are_not_run_folders(tmp_path, name):
    (tmp_path / name).mkdir()
    with pytest.raises(RunStorageError):
        open_existing_run(tmp_path, tmp_path / name)


def test_open_existing_run_rejects_missing_or_file_paths(tmp_path):
    with pytest.raises(RunStorageError, match="does not exist"):
        open_existing_run(tmp_path, tmp_path / f"{SLUG}-1")
    (tmp_path / f"{SLUG}-1").write_text("x", encoding="utf-8")
    with pytest.raises(RunStorageError, match="not a folder"):
        open_existing_run(tmp_path, tmp_path / f"{SLUG}-1")


def test_open_existing_run_rejects_a_link_even_with_a_valid_marker(tmp_path):
    real = allocate_run_dir(tmp_path / "real", SLUG)
    make_directory_link(tmp_path / f"{SLUG}-1", real.path)
    with pytest.raises(RunStorageError, match="not a folder"):
        open_existing_run(tmp_path, tmp_path / f"{SLUG}-1")


def test_open_existing_run_requires_a_matching_reservation_marker(tmp_path):
    legacy = tmp_path / f"{SLUG}-1"  # looks like a run folder, but this app never reserved it
    legacy.mkdir()
    (legacy / "chapter_index.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RunStorageError, match="reservation"):
        open_existing_run(tmp_path, legacy)
    assert listing(legacy) == ["chapter_index.json"]  # inspected, never modified


@pytest.mark.parametrize("payload", [
    "not json",
    "[]",
    json.dumps({"schema_version": 2, "kind": "web-novel-scraper-run", "novel_slug": SLUG,
                "number": 1, "created_utc": "2026-10-18T00:00:00Z"}),
    json.dumps({"schema_version": 1, "kind": "something-else", "novel_slug": SLUG,
                "number": 1, "created_utc": "2026-10-18T00:00:00Z"}),
    json.dumps({"schema_version": 1, "kind": "web-novel-scraper-run", "novel_slug": "shadow-slave",
                "number": 1, "created_utc": "2026-10-18T00:00:00Z"}),
    json.dumps({"schema_version": 1, "kind": "web-novel-scraper-run", "novel_slug": SLUG,
                "number": 2, "created_utc": "2026-10-18T00:00:00Z"}),
    json.dumps({"schema_version": 1, "kind": "web-novel-scraper-run", "novel_slug": SLUG,
                "number": 1}),
])
def test_open_existing_run_rejects_a_corrupt_or_mismatched_marker(tmp_path, payload):
    run = allocate_run_dir(tmp_path, SLUG)
    (run.path / run_storage.RESERVATION_FILENAME).write_text(payload, encoding="utf-8")
    with pytest.raises(RunStorageError):
        open_existing_run(tmp_path, run.path)


# ── atomic JSON write helper ─────────────────────────────────────────────────
def test_write_json_atomic_replaces_the_target_and_leaves_no_temp_file(tmp_path):
    target = tmp_path / "x.json"
    write_json_atomic(target, {"a": 1})
    write_json_atomic(target, {"a": 2, "é": "ü"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 2, "é": "ü"}
    assert listing(tmp_path) == ["x.json"]


def test_write_json_atomic_keeps_the_old_file_if_replacement_fails(tmp_path, monkeypatch):
    target = tmp_path / "x.json"
    write_json_atomic(target, {"a": 1})

    def fail(src, dst):
        raise OSError("locked")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="locked"):
        write_json_atomic(target, {"a": 2})
    monkeypatch.undo()
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}
    assert listing(tmp_path) == ["x.json"]


def test_write_json_atomic_does_not_touch_the_target_for_unserialisable_data(tmp_path):
    target = tmp_path / "x.json"
    write_json_atomic(target, {"a": 1})
    with pytest.raises(TypeError):
        write_json_atomic(target, {"a": object()})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}
    assert listing(tmp_path) == ["x.json"]
