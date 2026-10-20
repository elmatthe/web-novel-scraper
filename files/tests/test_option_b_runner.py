"""Offline acceptance-runner safety checks; no live fixtures or owner data."""
from __future__ import annotations

import json
from pathlib import Path
import socket
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import option_b_runner as runner


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("offline tests attempted real network/browser startup")
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(runner.http.client, "HTTPSConnection", denied)
    monkeypatch.setattr(runner.RequestManager, "start", denied)
    monkeypatch.setattr(runner.RequestManager, "fetch", denied)
    monkeypatch.setattr(runner.RequestManager, "_fetch_camoufox_once", denied)
    monkeypatch.setattr(runner.RequestManager, "_fetch_browser_once", denied)


def config(tmp_path, **kw):
    return runner.Config(root=tmp_path / "acceptance", **kw)


def test_dry_run_is_read_only_and_network_denied(tmp_path):
    cfg = config(tmp_path)
    report = runner.inspect(cfg)
    assert report["network_permitted"] is False
    assert report["request_cap"] == 0
    assert set(report["paths"]) == set(runner.DIRECTORIES)
    assert not cfg.root.exists()
    with pytest.raises(ValueError, match="approval"):
        runner.execute(config(tmp_path, mode="probe", slugs=(1,), bound=1, cap=2))
    assert not cfg.root.exists()


def test_fresh_workspace_and_explicit_compatible_resume(tmp_path):
    cfg = config(tmp_path)
    ws = runner.Workspace.open(cfg)
    assert all(ws.path(name).is_dir() for name in runner.DIRECTORIES)
    with pytest.raises(ValueError, match="existing"):
        runner.Workspace.open(cfg)
    assert runner.Workspace.open(config(tmp_path, resume=True)).root == ws.root
    with pytest.raises(ValueError, match="escape|relative"):
        ws.path("../outside")


@pytest.mark.parametrize("name", ["files/runtime-data/x", "files/cache/x", "files/output/x",
    "files/temp/x", "scripts/chapter-lists/x", "scripts/webnovel_scraper/chapter_lists/x"])
def test_owner_roots_refused(name):
    with pytest.raises(ValueError, match="protected"):
        runner.inspect(runner.Config(root=runner.REPO / name))


def test_existing_empty_and_nested_paths_refused(tmp_path):
    cfg = config(tmp_path)
    cfg.root.mkdir()
    with pytest.raises(ValueError, match="existing"):
        runner.Workspace.open(cfg)
    with pytest.raises(ValueError):
        runner.inspect(runner.Config(root=Path("relative")))
    with pytest.raises(ValueError):
        runner.inspect(runner.Config(root=tmp_path / "parent" / ".." / "escape"))
    other = tmp_path / "other"
    runner.Workspace.open(runner.Config(root=other))
    with pytest.raises(ValueError, match="nested"):
        runner.Workspace.open(runner.Config(root=other / "logs" / "child"))


class Reply:
    def __init__(self, body="", status=200, headers=None):
        self.data = body.encode("utf-8")
        self.status = status
        self.headers = headers or {}
    def read(self, size):
        return self.data[:size]
    def getheader(self, name, default=None):
        return self.headers.get(name, default)


class Wire:
    """Fake the single HTTP connection, below the runner's navigation gate."""
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.connections = 0
    def __call__(self, host, *, timeout):
        self.connections += 1
        wire = self
        class Connection:
            def request(self, method, path, **kw):
                wire.calls.append((host, method, path, kw))
                if wire.replies and isinstance(wire.replies[0], Exception):
                    raise wire.replies.pop(0)
            def getresponse(self):
                return wire.replies.pop(0)
            def close(self):
                pass
        return Connection()


def page(label, body=None):
    return f"<h1>Chapter {label}: Synthetic title</h1><div id='article'><p>{body or 'Unique synthetic prose ' + str(label)}</p></div>"


def index(bound):
    return f"<a href='{runner.SPEC.url}/chapter-{bound}'>Last</a>"


class Clock:
    def __init__(self):
        self.now = 1000.0
    def monotonic(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


def execute(cfg, wire, clock=None):
    clock = clock or Clock()
    return runner.execute(cfg, connection_factory=wire, sleep=clock.sleep, monotonic=clock.monotonic)


def manager(tmp_path, replies, cap=2, urls=None):
    ws = runner.Workspace.open(config(tmp_path))
    wire = Wire(replies)
    clock = Clock()
    rm = runner.RestrictedManager(ws, cap=cap, allowed=urls or {runner.SPEC.url: 1,
        runner.chapter_url_for(runner.SPEC, 1): 1}, connection_factory=wire,
        sleep=clock.sleep, monotonic=clock.monotonic)
    return rm, wire


def test_hard_budget_counts_actual_attempt_and_latches_exhaustion(tmp_path):
    url = runner.chapter_url_for(runner.SPEC, 1)
    rm, wire = manager(tmp_path, [Reply(page(1)), Reply(page(1))], cap=1, urls={url: 5})
    rm.fetch(url)
    for _ in range(3):
        with pytest.raises(runner.Stop):
            rm.fetch(url, max_retries=100)
    assert len(wire.calls) == rm.attempts == 1


@pytest.mark.parametrize("reply", [Reply(status=429), Reply(page(1), status=403),
    Reply("<title>Access denied</title>"), Reply("<title>Just a moment...</title><div id='cf-challenge-running'></div>"),
    Reply(status=302, headers={"Location": "https://example.invalid/"}), OSError("broken connection")])
def test_failure_redirect_or_exception_stops_all_later_attempts(tmp_path, reply):
    rm, wire = manager(tmp_path, [reply, Reply(page(1))])
    with pytest.raises(runner.Stop):
        rm.fetch(runner.SPEC.url)
    with pytest.raises(runner.Stop):
        rm.fetch(runner.chapter_url_for(runner.SPEC, 1), max_retries=100)
    assert rm.attempts == len(wire.calls) == 1


def test_start_browser_and_cancellation_cannot_navigate(tmp_path):
    rm, wire = manager(tmp_path, [])
    rm.start()
    assert wire.connections == rm.attempts == 0
    with pytest.raises(runner.Stop):
        rm.fetch(runner.SPEC.url, use_browser=True)
    assert not wire.calls
    rm.cancel_event.set()
    with pytest.raises(runner.Stop):
        rm.fetch(runner.SPEC.url)
    assert not wire.calls


@pytest.mark.parametrize("slugs", [tuple(range(1, 14)), (1, 1), (0,), (-1,), (True,), ("1",), (4,)])
def test_bad_probe_list_rejected_before_workspace_or_requests(tmp_path, slugs):
    cfg = config(tmp_path, mode="probe", slugs=slugs, bound=3, cap=13)
    with pytest.raises(ValueError):
        runner.inspect(cfg)
    assert not cfg.root.exists()


def test_probe_observes_exact_labels_without_full_scan_or_manifest(tmp_path, monkeypatch):
    def no_walker(*a, **kw):
        raise AssertionError("probe invoked full walker")
    monkeypatch.setattr(runner.FreeWebNovelAdapter, "build_chapter_index", no_walker)
    monkeypatch.setattr(runner.FreeWebNovelAdapter, "_reconcile_window", no_walker)
    composite = "<h1>Chapter 1129.1 - 1130 Initiative</h1><div id='article'><p>Chapter 1130 Initiative</p><p>Synthetic prose.</p></div>"
    wire = Wire([Reply(index(6)), Reply(page("627.10")), Reply(composite),
        Reply(page("627.10")), Reply("<h1>Unknown title</h1><div id='article'><p>Body</p></div>"),
        Reply("<h1>Chapter 4</h1><h1>Chapter 5</h1><div id='article'><p>Body</p></div>"),
        Reply("<h1>Chapter 6</h1>")])
    result = execute(config(tmp_path, mode="probe", slugs=(1, 2, 3, 4, 5, 6), bound=6, cap=7), wire)
    facts = result["observations"]
    assert [f["status"] for f in facts] == ["observed", "observed", "observed", "unknown", "conflict", "unreadable"]
    assert [f["labels"] for f in facts[:3]] == [["627.10"], ["1130"], ["627.10"]]
    assert result["complete"] is False and result["manifest_created"] is False
    assert not list((tmp_path / "acceptance/runtime").rglob("*.json"))
    assert not list((tmp_path / "acceptance/spool").iterdir())
    assert len(wire.calls) == 7


def test_twelve_probes_are_allowed_and_stop_on_first_inaccessible(tmp_path):
    wire = Wire([Reply(index(12)), Reply(page(1)), Reply(status=404), Reply(page(3))])
    cfg = config(tmp_path, mode="probe", slugs=tuple(range(1, 13)), bound=12, cap=13)
    with pytest.raises(runner.Stop):
        execute(cfg, wire)
    assert len(wire.calls) == 3
    logs = list((cfg.root / "logs").glob("probe-*.json"))
    saved = json.loads(logs[0].read_text())
    assert saved["observations"][-1]["status"] == "inaccessible"
    assert saved["complete"] is False


@pytest.mark.parametrize("mode", ["probe", "discovery"])
def test_changed_bound_stops_before_any_chapter(tmp_path, mode):
    wire = Wire([Reply(index(4)), Reply(page(1))])
    cfg = config(tmp_path, mode=mode, bound=3, cap=4, slugs=(1,) if mode == "probe" else ())
    with pytest.raises(runner.Stop, match="bound"):
        execute(cfg, wire)
    assert len(wire.calls) == 1


def discovery(tmp_path, replies=None, *, bound=3, resume=False):
    wire = Wire(replies or [Reply(index(bound)), *[Reply(page(n)) for n in range(1, bound + 1)]])
    cfg = config(tmp_path, mode="discovery", bound=bound, cap=bound + 1, resume=resume)
    return execute(cfg, wire), wire


def test_discovery_schema2_and_no_publication(tmp_path):
    result, wire = discovery(tmp_path)
    ws = runner.Workspace.open(config(tmp_path, resume=True))
    manifest = ws.store().load_effective(runner.SOURCE, quarantine=False).runtime.manifest
    assert manifest.to_json()["schema_version"] == 2
    assert manifest.verification.policy == "sm-com-page-identity-v1"
    assert manifest.verification.complete_for(runner.SOURCE)
    assert not manifest.toc_complete
    assert result["complete"] and len(wire.calls) == 4
    assert not list(ws.path("spool").iterdir())
    assert not list(ws.path("outputs").iterdir())


def test_interrupted_discovery_restart_retains_independent_observations(tmp_path):
    with pytest.raises(runner.Stop):
        discovery(tmp_path, [Reply(index(3)), Reply(page(1)), OSError("interrupted"), Reply(page(3))])
    ws = runner.Workspace.open(config(tmp_path, resume=True))
    partial = ws.store().load_effective(runner.SOURCE, quarantine=False).runtime.manifest
    assert not partial.verification.complete_for(runner.SOURCE)
    assert len(partial.chapters) == 1
    acquired = partial.verification.pages[0].observed_utc
    result, wire = discovery(tmp_path, [Reply(index(3)), Reply(page(2)), Reply(page(3))], resume=True)
    assert result["complete"] and len(wire.calls) == 3
    complete = ws.store().load_effective(runner.SOURCE, quarantine=False).runtime.manifest
    assert complete.verification.pages[0].observed_utc == acquired


def test_unresolved_discovery_never_certifies_or_renders(tmp_path):
    result, _ = discovery(tmp_path, [Reply(index(3)), Reply(page(1)), Reply("<h1>Unknown</h1>"), Reply(page(3))])
    assert not result["complete"]
    cfg = config(tmp_path, mode="sample", cap=3, start=1, end=3, formats=("txt",), grouping="separate", resume=True)
    wire = Wire([])
    with pytest.raises(ValueError, match="complete"):
        execute(cfg, wire)
    assert not wire.calls and not list((cfg.root / "outputs").iterdir())


def sample_cfg(tmp_path, *, mode="separate", formats=("pdf", "txt"), run=None):
    return config(tmp_path, mode="sample", cap=6, start=1, end=3,
        formats=formats, grouping=mode, resume=True, run=run)


@pytest.mark.parametrize("grouping,count", [("separate", 3), ("chunked", 2), ("single", 1)])
def test_finite_sample_publications_and_audit_hashes(tmp_path, grouping, count):
    discovery(tmp_path)
    wire = Wire([Reply(page(n)) for n in range(1, 4)])
    result = execute(sample_cfg(tmp_path, mode=grouping), wire)
    assert result["complete"] and len(wire.calls) == 3
    folder = tmp_path / "acceptance" / "outputs" / result["run"]
    assert len(list(folder.glob("PDF/*.pdf"))) == count
    assert len(list(folder.glob("TXT/*.txt"))) == count
    before = {str(p): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    audit = runner.execute(config(tmp_path, mode="audit", resume=True, run=result["run"]))
    assert not audit["problems"]
    assert audit["semantic_resume"]["eligible"]
    assert not audit["production_approved"]
    assert {str(p): p.read_bytes() for p in folder.rglob("*") if p.is_file()} == before
    import hashlib
    assert all(hashlib.sha256((folder / f["file"]).read_bytes()).hexdigest() == f["recorded_sha256"]
               for f in audit["files"])


def test_unicode_fallback_preserves_txt_and_refuses_pdf_acceptance(tmp_path, monkeypatch):
    from webnovel_scraper import pdf_builder
    monkeypatch.setattr(pdf_builder, "_fallback_fonts", lambda: [])
    discovery(tmp_path)
    wire = Wire([Reply(page(1, "An unsupported \u16b1 rune.")), Reply(page(2)), Reply(page(3))])
    result = execute(sample_cfg(tmp_path, formats=("pdf",)), wire)
    folder = tmp_path / "acceptance/outputs" / result["run"]
    assert not result["complete"]
    assert "\u16b1" in next(folder.glob("TXT/*.txt")).read_text(encoding="utf-8")
    assert len(wire.calls) == 3


def test_duplicate_body_diagnostics_are_preserved(tmp_path):
    discovery(tmp_path)
    result = execute(sample_cfg(tmp_path), Wire([Reply(page(n, "Identical canonical prose.")) for n in range(1, 4)]))
    audit = runner.execute(config(tmp_path, mode="audit", resume=True, run=result["run"]))
    assert len(audit["integrity_diagnostics"]) == 2
    assert any("duplicate" in p for p in audit["problems"])
    folder = tmp_path / "acceptance/outputs" / result["run"]
    assert len(list(folder.glob("TXT/*.txt"))) == 3


def test_cancelled_sample_semantic_resume_and_old_journal_refusal(tmp_path):
    from webnovel_scraper.run_journal import record_crc, JournalError
    discovery(tmp_path)
    cfg = sample_cfg(tmp_path)
    wire = Wire([Reply(page(1)), Reply(status=429), Reply(page(3))])
    with pytest.raises(runner.Stop):
        execute(cfg, wire)
    assert len(wire.calls) == 2
    run = next((cfg.root / "outputs").iterdir()).name
    result = execute(sample_cfg(tmp_path, run=run), Wire([Reply(page(2)), Reply(page(3))]))
    assert result["complete"]
    # Completed compatible Resume should issue no new navigation.
    nothing = Wire([])
    assert execute(sample_cfg(tmp_path, run=run), nothing)["complete"]
    assert not nothing.calls
    journal = cfg.root / "outputs" / run / runner.JOURNAL_FILENAME
    rows = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    rows[0].pop("resume_policy")
    for row in rows:
        row["crc"] = record_crc(row)
    journal.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    before = journal.read_bytes()
    refused = Wire([])
    with pytest.raises(JournalError):
        execute(sample_cfg(tmp_path, run=run), refused)
    assert not refused.calls and journal.read_bytes() == before


def test_junction_or_symlink_and_reparse_descendants_refused(tmp_path):
    import os
    target, linked = tmp_path / "target", tmp_path / "linked"
    target.mkdir()
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(linked))
    else:
        linked.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="linked/reparse"):
        runner.inspect(runner.Config(root=linked / "new"))
    ws = runner.Workspace.open(config(tmp_path))
    inner = ws.path("outputs") / "link"
    if os.name == "nt":
        _winapi.CreateJunction(str(target), str(inner))
    else:
        inner.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="linked/reparse"):
        runner.Workspace.open(config(tmp_path, resume=True))


def test_symbolic_link_attribute_refused_before_resolve(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import stat
    original = Path.lstat
    chosen = tmp_path / "symlink"
    def info(path, *a, **kw):
        if path == chosen:
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0x400)
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, "lstat", info)
    with pytest.raises(ValueError, match="linked/reparse"):
        runner.inspect(runner.Config(root=chosen / "new"))


def test_unknown_or_tampered_workspace_refused(tmp_path):
    ws = runner.Workspace.open(config(tmp_path))
    path = ws.root / runner.MARKER
    raw = json.loads(path.read_text())
    raw["version"] = 99
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="incompatible"):
        runner.Workspace.open(config(tmp_path, resume=True))


def test_offline_evidence_cannot_be_used_for_live_stage(tmp_path):
    discovery(tmp_path)
    cfg = runner.replace(sample_cfg(tmp_path), permit_network=True)
    with pytest.raises(ValueError, match="synthetic/live"):
        runner.execute(cfg)


def test_retried_requests_each_consume_a_navigation_slot(tmp_path):
    url = runner.chapter_url_for(runner.SPEC, 1)
    rm, wire = manager(tmp_path, [Reply(page(1)) for _ in range(3)], cap=2, urls={url: 3})
    clock = Clock()
    rm.host_limiter = runner.HostRateLimiter(3, sleep=clock.sleep, monotonic=clock.monotonic)
    rm.fetch(url, max_retries=500)
    rm.fetch(url, max_retries=500)
    with pytest.raises(runner.Stop):
        rm.fetch(url, max_retries=500)
    assert rm.attempts == len(wire.calls) == 2


@pytest.mark.parametrize("api", ["fetch_html", "fetch_html_browser", "session"])
def test_inherited_transport_entrypoints_cannot_escape_budget(tmp_path, api):
    rm, wire = manager(tmp_path, [])
    with pytest.raises(runner.Stop):
        if api == "session":
            _ = rm.session
        else:
            getattr(rm, api)(runner.SPEC.url)
    assert not wire.calls and wire.connections == 0


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("host", ["freewebnovel.com", "example.invalid"])
def test_redirect_hops_are_never_followed(tmp_path, status, host):
    rm, wire = manager(tmp_path, [Reply(status=status, headers={"Location": f"https://{host}/next"})])
    with pytest.raises(runner.Stop, match="redirect"):
        rm.fetch(runner.SPEC.url)
    assert rm.attempts == len(wire.calls) == 1


def test_cancel_during_pacing_prevents_second_request(tmp_path):
    rm, wire = manager(tmp_path, [Reply(page(1)), Reply(page(2))])
    clock = Clock()
    def cancel_sleep(seconds):
        clock.sleep(seconds)
        rm.cancel_event.set()
    rm.host_limiter = runner.HostRateLimiter(3, sleep=cancel_sleep, monotonic=clock.monotonic)
    rm.fetch(runner.SPEC.url)
    with pytest.raises(runner.ScrapeCancelled):
        rm.fetch(runner.chapter_url_for(runner.SPEC, 1))
    assert rm.attempts == len(wire.calls) == 1


def test_index_cannot_exceed_one_attempt(tmp_path):
    rm, wire = manager(tmp_path, [Reply(index(1)), Reply(index(1))], cap=100)
    rm.fetch(runner.SPEC.url)
    with pytest.raises(runner.Stop):
        rm.fetch(runner.SPEC.url)
    assert len(wire.calls) == 1


def test_explicit_twelve_page_probe_list(tmp_path):
    wire = Wire([Reply(index(12)), *[Reply(page(n)) for n in range(1, 13)]])
    result = execute(config(tmp_path, mode="probe", slugs=tuple(range(1, 13)), bound=12, cap=13), wire)
    assert len(result["observations"]) == 12
    assert result["attempts"] == 13 and not result["complete"]


def test_offline_full_sample_never_touches_default_store_cache_or_owner_outputs(tmp_path, monkeypatch):
    from webnovel_scraper import pipeline
    owner = tmp_path / "owner"
    protected = [owner / "files/runtime-data/chapter-lists/freewebnovel.com/supreme-magus.json",
        owner / "files/cache/unchanged.html", owner / "Downloads/supreme-magus-1/TXT/protected.txt"]
    for p in protected:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"unchanged owner sentinel")
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in protected}
    def no_defaults():
        raise AssertionError("default manifest store used")
    monkeypatch.setattr(pipeline, "default_manifest_store", no_defaults)
    discovery(tmp_path)
    execute(sample_cfg(tmp_path), Wire([Reply(page(n)) for n in range(1, 4)]))
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in protected} == before
    root = tmp_path / "acceptance"
    assert not list((root / "cache").iterdir()) and not list((root / "baseline").iterdir())


def test_sample_refuses_uncovered_clamped_range(tmp_path):
    discovery(tmp_path)
    cfg = runner.replace(sample_cfg(tmp_path), end=4)
    with pytest.raises(ValueError, match="coverage"):
        execute(cfg, Wire([]))
    assert not list((cfg.root / "outputs").iterdir())


def test_workspace_stage_lock_refuses_concurrent_execution(tmp_path):
    from webnovel_scraper.file_lock import LockBusy
    ws = runner.Workspace.open(config(tmp_path), simulation=True)
    with runner.FileLock(ws.path("logs/.stage.lock")):
        with pytest.raises(LockBusy):
            execute(config(tmp_path, mode="probe", bound=1, slugs=(1,), cap=2, resume=True), Wire([]))


def test_cli_default_and_selected_stage_inspection_do_not_write(tmp_path, capsys):
    root = tmp_path / "cli"
    assert runner.main(["--root", str(root)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "dry-run" and not report["network_permitted"] and not root.exists()
    assert runner.main(["--root", str(root), "--mode", "probe", "--slugs", "1", "--bound", "1", "--cap", "2"]) == 0
    assert not root.exists()


@pytest.mark.parametrize("mode", ["probe", "discovery"])
def test_parser_access_failure_stops_walker_immediately(tmp_path, mode):
    cfg = config(tmp_path, mode=mode, slugs=(1, 2, 3) if mode == "probe" else (), bound=3, cap=4)
    wire = Wire([Reply(index(3)), Reply("<div>Access denied</div>"), Reply(page(2)), Reply(page(3))])
    with pytest.raises(runner.Stop, match="inaccessible"):
        execute(cfg, wire)
    assert len(wire.calls) == 2


def test_last_page_cancellation_cannot_certify_full_discovery(tmp_path, monkeypatch):
    original = runner.AcceptanceAdapter._fetch_and_read
    def cancelling(adapter, rm, spec, slug, known, **kw):
        value = original(adapter, rm, spec, slug, known, **kw)
        if slug == 3:
            rm.cancel_event.set()
        return value
    monkeypatch.setattr(runner.AcceptanceAdapter, "_fetch_and_read", cancelling)
    with pytest.raises(runner.Stop, match="cancelled"):
        discovery(tmp_path)
    ws = runner.Workspace.open(config(tmp_path, resume=True))
    manifest = ws.store().load_effective(runner.SOURCE, quarantine=False).runtime.manifest
    assert len(manifest.chapters) == 3 and not manifest.verification.complete_for(runner.SOURCE)
    assert manifest.spans == ()


def test_default_output_root_also_refused_when_downloads_fallback_is_used(tmp_path, monkeypatch):
    fallback = tmp_path / "default-output-fallback"
    monkeypatch.setattr(runner, "default_output_root", lambda: fallback)
    with pytest.raises(ValueError, match="protected"):
        runner.inspect(runner.Config(root=fallback / "new"))
