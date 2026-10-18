"""Single-writer lock, discovery recording and explicit refresh in the chapter manifest store
(v0.3.0 Checkpoint 03.2a). Offline; real files under ``tmp_path``, including a real second process.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import threading
import time
from datetime import datetime, timezone

import pytest

from webnovel_scraper.chapter_manifest import ChapterManifest, Provenance
from webnovel_scraper.chapter_store import (
    ChapterManifestStore,
    ManifestLockBusy,
    ManifestStoreError,
)
from webnovel_scraper.file_lock import FileLock, LockBusy
from webnovel_scraper.source_identity import ChapterLabel, SourceIdentity

COM = "https://freewebnovel.com"
NOW = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)
SOURCE = SourceIdentity(novel_slug="demo", adapter_key="freewebnovel", origin=COM,
                        index_url=f"{COM}/novel/demo")
SCRIPTS = __import__("pathlib").Path(__file__).resolve().parents[2] / "scripts"


def ch(label, slug=None, title=None):
    return SOURCE.chapter_identity(f"{COM}/novel/demo/chapter-{slug or label}",
                                   ChapterLabel.parse(str(label)), slug=slug or int(float(label)),
                                   title=title)


def make_store(tmp_path):
    return ChapterManifestStore(tmp_path / "b", tmp_path / "r", clock=lambda: NOW)


def eventually_free(path, seconds=10.0):
    """The OS frees a dead process's lock, but not always the instant ``wait()`` returns (Windows
    documents the delay as resource-dependent), so poll briefly instead of asserting a race."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            with FileLock(path):
                return True
        except LockBusy:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.05)


def runtime(numbers, spans):
    return ChapterManifest.create(
        layer="runtime", source=SOURCE,
        provenance=Provenance("2026-10-18T10:00:00Z", "2026-10-18T11:00:00Z", "0.3.0", "toc"),
        spans=spans, chapters=[ch(n) for n in numbers])


# ── the raw lock ──────────────────────────────────────────────────────────────
def test_file_lock_is_exclusive_between_handles_and_releases_cleanly(tmp_path):
    path = tmp_path / "x.lock"
    first = FileLock(path)
    first.acquire()
    with pytest.raises(LockBusy):
        FileLock(path).acquire()
    first.release()
    with FileLock(path):          # free again
        pass
    first.release()               # idempotent


def test_file_lock_cannot_be_acquired_twice_by_one_object(tmp_path):
    lock = FileLock(tmp_path / "x.lock")
    lock.acquire()
    try:
        with pytest.raises(RuntimeError):
            lock.acquire()
    finally:
        lock.release()


_HOLDER = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, {scripts!r})
    from webnovel_scraper.file_lock import FileLock
    lock = FileLock(sys.argv[1])
    lock.acquire()
    print("held", flush=True)
    sys.stdin.readline()          # hold until the parent says stop (or dies)
""")


def test_the_lock_excludes_a_real_second_process_and_dies_with_it(tmp_path):
    path = tmp_path / "x.lock"
    proc = subprocess.Popen([sys.executable, "-I", "-c", _HOLDER.format(scripts=str(SCRIPTS)), str(path)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "held"
        with pytest.raises(LockBusy):
            FileLock(path).acquire()
    finally:
        proc.kill()               # a crash, not a clean release
        proc.wait(timeout=10)
    assert eventually_free(path)  # the OS freed it: no stale lock for a person to clean up


def test_the_store_lock_excludes_a_real_second_process(tmp_path):
    store = make_store(tmp_path)
    lock_file = store.lock_path(SOURCE)
    lock_file.parent.mkdir(parents=True)
    proc = subprocess.Popen([sys.executable, "-I", "-c", _HOLDER.format(scripts=str(SCRIPTS)), str(lock_file)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "held"
        with pytest.raises(ManifestLockBusy, match="Another run or program instance"):
            store.record_discovery(SOURCE, [ch(1)], ((1, 1),))
        with pytest.raises(ManifestLockBusy):
            store.save_runtime(runtime([1], [(1, 1)]))
        assert not store.runtime_path(SOURCE).exists()          # nothing was written
        assert store.import_legacy_index(SOURCE, tmp_path / "missing.json").status == "rejected"
    finally:
        proc.kill()
        proc.wait(timeout=10)
    assert eventually_free(lock_file)
    store.record_discovery(SOURCE, [ch(1)], ((1, 1),))          # free after the holder died
    assert store.runtime_path(SOURCE).is_file()


# ── the store's lock ──────────────────────────────────────────────────────────
def test_writer_lock_is_reentrant_for_its_holder_and_blocks_other_stores_and_threads(tmp_path):
    store, other = make_store(tmp_path), make_store(tmp_path)
    with store.writer_lock(SOURCE):
        with store.writer_lock(SOURCE):                         # re-entry (save_runtime relies on it)
            store.save_runtime(runtime([1], [(1, 1)]))
        with pytest.raises(ManifestLockBusy):
            with other.writer_lock(SOURCE):
                pass
        seen = []

        def contender():
            try:
                with store.writer_lock(SOURCE):
                    seen.append("entered")
            except ManifestLockBusy:
                seen.append("busy")

        thread = threading.Thread(target=contender)
        thread.start()
        thread.join(10)
        assert seen == ["busy"]
    with other.writer_lock(SOURCE):                             # released once the outermost exits
        pass


def test_the_lock_is_released_when_the_body_raises(tmp_path):
    store, other = make_store(tmp_path), make_store(tmp_path)
    with pytest.raises(ZeroDivisionError):
        with store.writer_lock(SOURCE):
            1 / 0
    with other.writer_lock(SOURCE):
        pass


def test_a_sources_locks_are_independent(tmp_path):
    store = make_store(tmp_path)
    net = SourceIdentity(novel_slug="demo", adapter_key="freewebnovel", origin="https://freewebnovel.net",
                         index_url="https://freewebnovel.net/demo")
    with store.writer_lock(SOURCE):
        with make_store(tmp_path).writer_lock(net):             # .net is not blocked by .com
            pass


def test_save_runtime_refuses_while_another_writer_holds_the_lock(tmp_path):
    store, other = make_store(tmp_path), make_store(tmp_path)
    with other.writer_lock(SOURCE):
        with pytest.raises(ManifestLockBusy):
            store.save_runtime(runtime([1], [(1, 1)]))
    assert not store.runtime_path(SOURCE).exists()


def test_two_stores_updating_in_turn_never_lose_each_others_chapters(tmp_path):
    first, second = make_store(tmp_path), make_store(tmp_path)
    first.record_discovery(SOURCE, [ch(1), ch(2)], ((1, 2),))
    second.record_discovery(SOURCE, [ch(3), ch(4)], ((3, 4),))      # reads the file first, then merges
    first.record_discovery(SOURCE, [ch(5)], ((5, 5),))
    merged = make_store(tmp_path).load_effective(SOURCE).effective
    assert [c.label.text for c in merged.chapters] == ["1", "2", "3", "4", "5"]
    assert merged.spans == ((1, 5),)


def test_parallel_writers_in_threads_each_win_or_are_refused_never_corrupt(tmp_path):
    outcomes = []

    def writer(n):
        store = make_store(tmp_path)
        try:
            outcomes.append(store.record_discovery(SOURCE, [ch(n)], ((n, n),)).status)
        except ManifestLockBusy:
            outcomes.append("busy")

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(1, 9)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert set(outcomes) <= {"recorded", "unchanged", "busy"}
    final = make_store(tmp_path).load_layer(SOURCE, "runtime")
    assert final.status == "loaded"                                 # always a valid file
    recorded = outcomes.count("recorded")
    assert len(final.manifest.chapters) == recorded                 # every acknowledged write survived


# ── record_discovery ──────────────────────────────────────────────────────────
def test_record_discovery_keeps_the_existing_overlay_and_never_saves_a_bare_delta(tmp_path):
    store = make_store(tmp_path)
    store.save_runtime(runtime([1, 2, 3], [(1, 3)]))
    record = store.record_discovery(SOURCE, [ch(4), ch(5)], ((4, 5),))
    assert record.status == "recorded"
    saved = store.load_layer(SOURCE, "runtime").manifest
    assert [c.label.text for c in saved.chapters] == ["1", "2", "3", "4", "5"]
    assert saved.spans == ((1, 5),)
    assert record.effective.covers(1, 5)


def test_record_discovery_is_idempotent_and_reports_unchanged_without_writing(tmp_path):
    store = make_store(tmp_path)
    store.record_discovery(SOURCE, [ch(1), ch(2)], ((1, 2),))
    path = store.runtime_path(SOURCE)
    before = path.stat().st_mtime_ns
    again = store.record_discovery(SOURCE, [ch(1), ch(2)], ((1, 2),))
    assert again.status == "unchanged" and path.stat().st_mtime_ns == before


def test_record_discovery_refuses_evidence_that_contradicts_the_overlay_or_baseline(tmp_path):
    store = make_store(tmp_path)
    store.save_runtime(runtime([1, 2], [(1, 2)]))
    clash = SOURCE.chapter_identity(f"{COM}/novel/demo/chapter-1", ChapterLabel("9"), slug=1)
    record = store.record_discovery(SOURCE, [clash], ())
    assert record.status == "conflict" and "contradict" in record.reason
    assert [c.label.text for c in store.load_layer(SOURCE, "runtime").manifest.chapters] == ["1", "2"]

    base = ChapterManifest.create(
        layer="baseline", source=SOURCE,
        provenance=Provenance("2026-10-18T10:00:00Z", "2026-10-18T11:00:00Z", "0.3.0", "toc"),
        spans=[(7, 7)], chapters=[ch(7)])
    store.baseline_path(SOURCE).parent.mkdir(parents=True)
    store.baseline_path(SOURCE).write_text(json.dumps(base.to_json()), encoding="utf-8")
    clash = SOURCE.chapter_identity(f"{COM}/novel/demo/chapter-7", ChapterLabel("8"), slug=7)
    assert store.record_discovery(SOURCE, [clash], ()).status == "conflict"


def test_record_discovery_rejects_invalid_evidence_without_writing(tmp_path):
    store = make_store(tmp_path)
    twice = [ch(1, slug=5), ch(2, slug=5)]                          # two pages claiming slug 5
    record = store.record_discovery(SOURCE, twice, ())
    assert record.status == "rejected"
    assert not store.runtime_path(SOURCE).exists()


def test_record_discovery_reports_a_save_failure_and_still_returns_a_usable_projection(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    monkeypatch.setattr(ChapterManifestStore, "save_runtime",
                        lambda self, m: (_ for _ in ()).throw(OSError("disk full")))
    record = store.record_discovery(SOURCE, [ch(1), ch(2)], ((1, 2),))
    assert record.status == "error" and "disk full" in record.reason
    assert record.effective.covers(1, 2)                            # usable for this run only


# ── refresh backups ───────────────────────────────────────────────────────────
def test_refresh_runtime_returns_none_without_an_overlay(tmp_path):
    assert make_store(tmp_path).refresh_runtime(SOURCE) is None


def test_refresh_runtime_sets_the_overlay_aside_under_a_unique_name(tmp_path):
    store = make_store(tmp_path)
    names = []
    for _ in range(3):
        store.save_runtime(runtime([1], [(1, 1)]))
        before = store.runtime_path(SOURCE).read_bytes()
        backup = store.refresh_runtime(SOURCE)
        assert not store.runtime_path(SOURCE).exists()
        assert backup.read_bytes() == before and ".bak-20261018T120000Z" in backup.name
        names.append(backup.name)
    assert len(set(names)) == 3 and names[1].endswith("-1") and names[2].endswith("-2")


def test_refresh_runtime_never_touches_the_baseline(tmp_path):
    store = make_store(tmp_path)
    base = ChapterManifest.create(
        layer="baseline", source=SOURCE,
        provenance=Provenance("2026-10-18T10:00:00Z", "2026-10-18T11:00:00Z", "0.3.0", "toc"),
        spans=[(1, 1)], chapters=[ch(1)])
    path = store.baseline_path(SOURCE)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(base.to_json()), encoding="utf-8")
    before = path.read_bytes()
    store.save_runtime(runtime([2], [(2, 2)]))
    store.refresh_runtime(SOURCE)
    assert path.read_bytes() == before
    assert store.load_effective(SOURCE).effective.covers(1, 1)


def test_a_backup_that_cannot_be_made_leaves_the_overlay_where_it_was(tmp_path, monkeypatch):
    from webnovel_scraper import chapter_store
    store = make_store(tmp_path)
    store.save_runtime(runtime([1], [(1, 1)]))
    before = store.runtime_path(SOURCE).read_bytes()
    monkeypatch.setattr(chapter_store, "_quarantine_move",
                        lambda s, t: (_ for _ in ()).throw(PermissionError("locked")))
    with pytest.raises(ManifestStoreError, match="could not be backed up"):
        store.refresh_runtime(SOURCE)
    assert store.runtime_path(SOURCE).read_bytes() == before


def test_restore_backup_only_when_no_overlay_exists_again(tmp_path):
    store = make_store(tmp_path)
    store.save_runtime(runtime([1], [(1, 1)]))
    backup = store.refresh_runtime(SOURCE)
    assert store.restore_backup(SOURCE, backup) is True
    assert store.load_layer(SOURCE, "runtime").status == "loaded"
    assert not backup.exists()
    # an overlay written meanwhile is never replaced by an older backup
    backup2 = store.refresh_runtime(SOURCE)
    store.save_runtime(runtime([9], [(9, 9)]))
    assert store.restore_backup(SOURCE, backup2) is False
    assert backup2.exists()
    assert store.restore_backup(SOURCE, tmp_path / "no-such.bak") is False


def test_a_reader_without_the_lock_does_not_quarantine(tmp_path):
    store = make_store(tmp_path)
    path = store.runtime_path(SOURCE)
    path.parent.mkdir(parents=True)
    path.write_text("junk", encoding="utf-8")
    loaded = store.load_effective(SOURCE, quarantine=False)
    assert loaded.runtime.status == "invalid" and "left in place" in loaded.runtime.error
    assert path.read_text(encoding="utf-8") == "junk"
    assert store.load_effective(SOURCE).runtime.status == "quarantined"
