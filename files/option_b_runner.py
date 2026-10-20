#!/usr/bin/env python3
"""Developer-only Supreme Magus acceptance stages. Default: read-only, no network.

This runner deliberately does not use RequestManager's retry/browser ladder.
See option_b_runner.md for the separate approval and workspace contracts.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True  # Inspection must not create import caches outside the root.

import argparse
from contextlib import ExitStack
from dataclasses import dataclass, replace
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time
from urllib.parse import urljoin, urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from bs4 import BeautifulSoup
from webnovel_scraper import catalog
from webnovel_scraper.adapters.freewebnovel import (
    FreeWebNovelAdapter, _PageSpool, _candidate_title_texts, _extract_paragraphs,
    _observed_heading, chapter_url_for,
)
from webnovel_scraper.chapter_manifest import ChapterManifest, Provenance
from webnovel_scraper.chapter_preflight import ChapterListRefused, clipped_spans, identity_from_meta, resolve_chapters
from webnovel_scraper.chapter_store import ChapterManifestStore, ManifestStoreError
from webnovel_scraper.cloudflare_detection import is_cloudflare_challenge
from webnovel_scraper.host_rate_limiter import HostRateLimiter
from webnovel_scraper.models import ChapterIndex, ChapterMismatchError, OutputMode, ScrapeJob
from webnovel_scraper.pipeline import run_scrape
from webnovel_scraper.request_manager import RequestManager, ScrapeCancelled
from webnovel_scraper.source_identity import SourceIdentity
from webnovel_scraper.chapter_verification import Verification
from webnovel_scraper.run_journal import JOURNAL_FILENAME, JournalError, replay_journal
from webnovel_scraper.run_storage import default_output_root
from webnovel_scraper.semantic_resume import require_policy
from webnovel_scraper.file_lock import FileLock

SPEC = replace(catalog.get_spec("supreme-magus", "freewebnovel"), use_browser=False)
SOURCE = SourceIdentity.from_site_spec(SPEC)
DIRECTORIES = ("baseline", "runtime", "cache", "spool", "outputs", "logs", "audits")
MARKER = "option-b-workspace.json"
WORKSPACE_ID = {"kind": "supreme-magus-option-b", "version": 1, "source": SOURCE.to_json()}
MAX_BODY = 8 * 1024 * 1024


class Stop(ScrapeCancelled):
    """Latched runner failure: propagate through adapter/pipeline cancellation gates."""


@dataclass(frozen=True)
class Config:
    root: Path
    mode: str = "dry-run"
    cap: int = 0
    slugs: tuple[int, ...] = ()
    bound: int | None = None
    resume: bool = False
    permit_network: bool = False
    interval: float = 3.0
    timeout: float = 30.0
    start: int | None = None
    end: int | None = None
    formats: tuple[str, ...] = ()
    grouping: str | None = None
    chunk_size: int = 2
    run: str | None = None


def _no_links(path):
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"linked/reparse path refused: {part}")
        if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            raise ValueError(f"hard-linked path refused: {part}")


def _checked_root(root):
    root = Path(root)
    if not root.is_absolute() or ".." in root.parts:
        raise ValueError("acceptance root must be absolute without relative traversal")
    if any(":" in p or p.endswith((" ", ".")) for p in root.parts[1:]):
        raise ValueError("ambiguous path component refused")
    _no_links(root)
    root = root.resolve()
    protected = [REPO / p for p in ("files/runtime-data", "files/cache", "files/output",
        "files/temp", "scripts/chapter-lists", "scripts/webnovel_scraper/chapter_lists",
        "files/run-temp/chapter-discovery", ".git", ".agents", ".codex")]
    protected += [Path.home() / "Downloads", default_output_root()]
    for path in protected:
        path = path.resolve()
        if root.is_relative_to(path) or path.is_relative_to(root):
            raise ValueError(f"acceptance root conflicts with protected path: {path}")
    for parent in root.parents:
        if (parent / MARKER).exists():
            raise ValueError("nested acceptance workspaces are refused")
    return root


def inspect(cfg):
    root = _checked_root(cfg.root)
    if root.exists():
        if not cfg.resume:
            raise ValueError("existing root refused; explicit compatible resume required")
        Workspace(root).check()
    if cfg.mode not in ("dry-run", "probe", "discovery", "sample", "audit"):
        raise ValueError("unknown stage")
    if type(cfg.cap) is not int or cfg.cap < 0:
        raise ValueError("request cap must be a nonnegative integer")
    if type(cfg.resume) is not bool or type(cfg.permit_network) is not bool:
        raise ValueError("resume and network permission must be explicit booleans")
    if not math.isfinite(cfg.interval) or cfg.interval < 3 or not math.isfinite(cfg.timeout) or not 0 < cfg.timeout <= 60:
        raise ValueError("pacing must be >=3 seconds and timeout in (0,60]")
    if cfg.mode in ("probe", "discovery"):
        if type(cfg.bound) is not int or cfg.bound < 1:
            raise ValueError("explicit approved source bound required")
    if cfg.mode == "probe":
        if not 1 <= len(cfg.slugs) <= 12 or any(type(s) is not int or not 1 <= s <= cfg.bound for s in cfg.slugs):
            raise ValueError("probe requires 1..12 valid in-bound integer slugs")
        if len(set(cfg.slugs)) != len(cfg.slugs):
            raise ValueError("duplicate probe slug")
        if cfg.cap < len(cfg.slugs) + 1:
            raise ValueError("cap must cover index plus explicit probes")
    if cfg.mode == "discovery" and cfg.cap < cfg.bound + 1:
        raise ValueError("discovery cap must cover independently approved bound plus index")
    if cfg.mode == "sample":
        if (type(cfg.start) is not int or type(cfg.end) is not int or not 1 <= cfg.start <= cfg.end
                or cfg.end - cfg.start >= 12 or cfg.cap < 1):
            raise ValueError("sample requires finite range of at most 12 whole labels and a cap")
        if (not cfg.formats or len(set(cfg.formats)) != len(cfg.formats)
                or any(f not in ("pdf", "txt") for f in cfg.formats)
                or cfg.grouping not in ("separate", "chunked", "single") or cfg.chunk_size < 1):
            raise ValueError("explicit formats and grouping required")
    if cfg.run is not None and not re.fullmatch(r"supreme-magus-[1-9][0-9]*", cfg.run):
        raise ValueError("run must name a newly allocated isolated run")
    if cfg.mode == "audit" and not cfg.run:
        raise ValueError("audit requires an isolated run name")
    if cfg.mode in ("dry-run", "audit") and cfg.permit_network:
        raise ValueError("this mode does not permit network")
    return dict(mode=cfg.mode, source=SOURCE.to_json(), root=str(root),
        paths={n: str(root / n) for n in DIRECTORIES}, network_permitted=cfg.permit_network,
        request_cap=cfg.cap, browser_policy="prohibited, including startup and warm-up",
        pacing_seconds=cfg.interval, timeout_seconds=cfg.timeout, concurrency=1,
        slugs=list(cfg.slugs), approved_bound=cfg.bound,
        expected_artifacts={"dry-run": [], "probe": ["logs: attempts and diagnostic observations; no manifest"],
            "discovery": ["runtime: schema-2 checkpoints; no publications"],
            "sample": ["outputs: allocated selection, journal, explicit PDF/TXT; logs: run receipt"],
            "audit": ["audits: read-only artifact examination report"]}[cfg.mode],
        stop_conditions=["cap", "cancel", "redirect", "access/challenge/rate limit", "transport failure",
                         "source bound differs from explicit approval", "filesystem incompatibility"],
        not_permitted=["automatic next stage", "owner migration or Resume", "browser/rescue/retry ladder",
                       "owner/default cache, spool, manifest or output writes", "source completeness from probes",
                       "production approval"])


class Workspace:
    def __init__(self, root):
        self.root = root

    @classmethod
    def open(cls, cfg, *, simulation=None):
        root = _checked_root(cfg.root)
        if root.exists():
            if not cfg.resume:
                raise ValueError("existing root refused; explicit compatible resume required")
            ws = cls(root)
            ws.check()
            origin = json.loads((root / MARKER).read_text(encoding="utf-8"))["evidence_origin"]
            if simulation is not None and origin != ("offline-synthetic" if simulation else "live-preparation"):
                raise ValueError("synthetic/live workspace evidence cannot be mixed")
            return ws
        if cfg.resume:
            raise ValueError("resume requires an existing compatible workspace")
        # Parent must exist: the runner never invents an unapproved ancestor tree.
        root.mkdir()
        for name in DIRECTORIES:
            (root / name).mkdir()
        with (root / MARKER).open("x", encoding="utf-8") as f:
            json.dump({**WORKSPACE_ID, "evidence_origin": "offline-synthetic" if simulation else "live-preparation"},
                      f, sort_keys=True)
        return cls(root)

    def check(self):
        _checked_root(self.root)
        for base, dirs, names in os.walk(self.root, followlinks=False):
            for name in dirs + names:
                _no_links(Path(base) / name)
        marker = json.loads((self.root / MARKER).read_text(encoding="utf-8"))
        origin = marker.pop("evidence_origin", None)
        if marker != WORKSPACE_ID or origin not in ("offline-synthetic", "live-preparation"):
            raise ValueError("incompatible isolated workspace")
        if set(p.name for p in self.root.iterdir()) != {*DIRECTORIES, MARKER}:
            raise ValueError("unexpected workspace entry")
        if not all((self.root / n).is_dir() for n in DIRECTORIES):
            raise ValueError("workspace directory missing")
        if any((self.root / "baseline").iterdir()):
            raise ValueError("isolated baseline must stay empty; owner import is prohibited")

    def path(self, relative):
        rel = Path(relative)
        if rel.is_absolute() or ".." in rel.parts or not rel.parts or rel.parts[0] not in DIRECTORIES:
            raise ValueError("relative workspace path escape refused")
        if any(":" in p or p.endswith((" ", ".")) for p in rel.parts):
            raise ValueError("ambiguous relative workspace path refused")
        result = self.root / rel
        _no_links(result)
        if not result.resolve().is_relative_to(self.root):
            raise ValueError("workspace path escape refused")
        return result

    def record(self, folder, prefix, value):
        # Exclusive files, never replace a prior run's evidence.
        self.check()
        import uuid
        path = self.path(folder) / f"{prefix}-{uuid.uuid4().hex}.json"
        with path.open("x", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        return path

    def store(self):
        self.check()
        return ChapterManifestStore(baseline_root=self.path("baseline"), runtime_root=self.path("runtime"))


class RestrictedManager(RequestManager):
    """One HTTP request per counted attempt; no redirects/retries/browser/session warm-up.

    HTTPSConnection.request does not replay a request on failure. A fresh connection
    has no redirect/session adapter. The injected factory is for offline tests only.
    Budget is consumed immediately BEFORE request(); exceptions never refund it.
    """
    def __init__(self, ws, *, cap, allowed, connection_factory=None,
                 interval=3.0, timeout=30.0, sleep=time.sleep, monotonic=time.monotonic):
        super().__init__(SPEC.novel_slug, use_cache=False, cache_root=ws.path("cache"),
                         site_key=SOURCE.site_key, max_retries=0, http_timeout=timeout,
                         host_limiter=HostRateLimiter(interval, sleep=sleep, monotonic=monotonic))
        self.ws, self.cap, self.allowed = ws, cap, dict(allowed)
        self.attempts, self.counts, self.events = 0, {}, []
        self.stopped = None
        self._connection = connection_factory or http.client.HTTPSConnection
        self._serial = threading.Lock()

    def start(self):
        return self  # Explicitly no inherited startup or warm-up.

    def _fetch_with_retry_ladder(self, *_args, **_kwargs):
        self.stop("inherited retry/browser ladder prohibited")

    @property
    def session(self):
        self.stop("inherited HTTP session/warm-up prohibited")

    def stop(self, reason):
        self.stopped = self.stopped or reason
        self.cancel_event.set()
        raise Stop(self.stopped)

    def fetch(self, url, *, use_browser=False, **_ignored_ladder_options):
        with self._serial:
            self.ws.check()
            if self.stopped or self.cancel_event.is_set():
                self.stop(self.stopped or "cancelled")
            if use_browser:
                self.stop("browser navigation prohibited")
            if url not in self.allowed or self.counts.get(url, 0) >= self.allowed[url]:
                self.stop("unapproved URL or per-URL attempt cap")
            parts = urlsplit(url)
            if parts.scheme != "https" or parts.netloc != "freewebnovel.com" or parts.query or parts.fragment:
                self.stop("unexpected source URL")
            if self.attempts >= self.cap:
                self.stop("hard navigation cap exhausted")
            self.host_limiter.acquire(url, cancel_event=self.cancel_event)
            if self.cancel_event.is_set():
                self.stop("cancelled before navigation")
            event = dict(url=url, attempt=self.attempts + 1, outcome="attempted")
            conn = None
            try:
                conn = self._connection(parts.hostname, timeout=self._http_timeout)
                self.attempts += 1
                self.counts[url] = self.counts.get(url, 0) + 1
                self.events.append(event)
                conn.request("GET", parts.path, headers={"User-Agent": "Web-Novel-Scraper isolated acceptance",
                    "Accept": "text/html", "Accept-Encoding": "identity", "Connection": "close"})
                response = conn.getresponse()
                event["status"] = response.status
                raw = response.read(MAX_BODY + 1)
                event["sha256"] = hashlib.sha256(raw).hexdigest()
                if len(raw) > MAX_BODY:
                    self.stop("response exceeds finite size cap")
                if 300 <= response.status < 400:
                    event["redirect"] = response.getheader("Location")
                    self.stop("redirect refused before following it")
                if response.status != 200:
                    self.stop(f"inaccessible HTTP {response.status}")
                if response.getheader("Content-Encoding", "identity").lower() != "identity":
                    self.stop("unexpected compressed response")
                body = raw.decode("utf-8-sig", errors="strict")
                soup = BeautifulSoup(body, "html.parser")
                heading = " ".join(n.get_text(" ") for n in soup.select("title, h1"))
                if is_cloudflare_challenge(body) or re.search(r"access denied|error\s*1015|rate.?limit|forbidden", heading, re.I):
                    self.stop("access/challenge/rate-limit document")
                event["outcome"] = "ok"
                self._last_final_url = url
                self.last_body = body
                return body
            except Stop as exc:
                event["outcome"] = str(exc)
                raise
            except Exception as exc:
                event["outcome"] = f"transport exception: {type(exc).__name__}"
                self.stop(event["outcome"])
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        self.stop("transport close failure")


def _fresh_bound(rm, cfg):
    html = rm.fetch(SPEC.url, use_cache=False)
    slugs = []
    for anchor in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        match = re.fullmatch(re.escape(SPEC.url) + r"/chapter-([1-9][0-9]*)", urljoin(SPEC.url, anchor["href"]))
        if match:
            slugs.append(int(match[1]))
    if not slugs or max(slugs) != cfg.bound:
        rm.stop("fresh source bound differs from explicit approval or is unknown")
    return max(slugs)


def _probe(cfg, rm, adapter, result):
    _fresh_bound(rm, cfg)
    known = {}
    for slug in cfg.slugs:
        url = chapter_url_for(SPEC, slug)
        try:
            adapter._fetch_and_read(rm, SPEC, slug, known)
        except Stop as exc:
            result["observations"].append(dict(url=url, labels=[], status="inaccessible",
                provenance="current-response", detail=str(exc)))
            raise
        fact = known[slug].evidence
        soup = BeautifulSoup(rm.last_body, "html.parser")
        body = _extract_paragraphs(soup)
        labels = list(fact.labels)
        if fact.status == "conflict":
            try:
                _observed_heading(soup)
            except ChapterMismatchError as exc:
                labels = list(exc.observed_labels)
        result["observations"].append(dict(url=url, labels=labels, status=fact.status,
            provenance=fact.provenance, detail=fact.detail,
            observed_labels=labels, headings=[t[:1000] for t in _candidate_title_texts(soup)],
            body_paragraphs=len(body), body_sha256=hashlib.sha256("\n\n".join(body).encode("utf-8")).hexdigest(),
            response_sha256=rm.events[-1].get("sha256")))
        # Parser access detection is a second fail-stop boundary, never a rescue.
        if fact.status == "inaccessible":
            rm.stop("inaccessible probe document")
    # No store calls, selection, verification object or manifest certificate here.


def _discovery(cfg, rm, adapter, ws, result):
    store = ws.store()
    with store.writer_lock(SOURCE):
        store.assert_compatible(SOURCE)
        saved = store.load_effective(SOURCE, quarantine=False).runtime.manifest
        if saved and saved.verification.complete_for(SOURCE):
            rm.stop("discovery already complete; revalidation requires a new workspace")
        _fresh_bound(rm, cfg)
        if saved and saved.verification.bound != cfg.bound:
            rm.stop("saved source bound changed; separate disposition required")
        if saved and any(p.status == "conflict" for p in saved.verification.pages):
            rm.stop("saved conflict requires separate reviewed disposition")
        seeds = ChapterIndex(verified_manifest=saved)

        def publish(index, *_progress):
            ws.check()
            verification = index.verification
            if not isinstance(verification, Verification):
                rm.stop("discovery lacks policy evidence")
            if rm.cancel_event.is_set():
                verification = replace(verification, bound_confirmed=False)
            identities = [identity_from_meta(SOURCE, meta) for meta in index]
            manifest = ChapterManifest.create(layer="runtime", source=SOURCE,
                provenance=Provenance(verification.bound_utc, store.now_utc(), store.app_version, "walk"),
                chapters=identities, verification=verification,
                spans=clipped_spans(identities, None, None) if verification.complete_for(SOURCE) else ())
            store.save_runtime(manifest)
            result["manifest_created"] = True
            result["complete"] = verification.complete_for(SOURCE)
            result["observations"] = [p.to_json() for p in verification.pages]

        # Reuse the CP1/CP3 observation walker, never its index/fallback machinery.
        # Interrupted callbacks retain completed independent observations atomically.
        index = adapter._reconcile_window(rm, SPEC, 1, cfg.bound, {},
            resume_metas=seeds, index_progress_cb=publish, bound_confirmed=True)
        if rm.cancel_event.is_set():
            rm.stop("discovery cancelled; observations retained without complete coverage")
        publish(index)


class AcceptanceAdapter(FreeWebNovelAdapter):
    def _fetch_and_read(self, rm, spec, slug, known, **kwargs):
        number = super()._fetch_and_read(rm, spec, slug, known, **kwargs)
        if known[slug].evidence.status == "inaccessible":
            rm.events[-1]["parser_outcome"] = "inaccessible"
            rm.stop("parser detected inaccessible source page")
        return number


class SampleAdapter(AcceptanceAdapter):
    def build_chapter_index(self, *_args, **_kwargs):
        raise Stop("sample mode cannot discover; complete covered manifest required")

    def fetch_chapter(self, meta, spec, **kwargs):
        if SourceIdentity.from_site_spec(spec) != SOURCE:
            raise Stop("sample source changed")
        return super().fetch_chapter(meta, SPEC, **kwargs)


def _sample_selection(cfg, ws):
    store = ws.store()
    store.assert_compatible(SOURCE)
    loaded = store.load_effective(SOURCE, quarantine=False)
    manifest = loaded.runtime.manifest
    if (loaded.effective.has_conflicts or not manifest or not manifest.verification
            or not manifest.verification.complete_for(SOURCE)):
        raise ValueError("sample requires complete compatible isolated schema-2 evidence")
    if not manifest.covers(cfg.start, cfg.end):
        raise ValueError("exact requested chapter coverage missing; sample cannot clamp or discover")
    def refuse(**_kwargs):
        raise ValueError("requested chapter coverage missing; sample cannot discover")
    selection = resolve_chapters(SPEC, start=cfg.start, end=cfg.end, store=store,
        discover=refuse, log=lambda _: None)
    if not selection.chapters or len(selection.chapters) > 12:
        raise ValueError("sample requires at most 12 selected source pages including fractions/duplicates")
    if cfg.cap < len(selection.chapters):
        raise ValueError("sample request cap below selected page count")
    return store, selection


def _owned_run(ws, name):
    if not name or not re.fullmatch(r"supreme-magus-[1-9][0-9]*", name):
        raise ValueError("isolated run name required")
    path = ws.path(f"outputs/{name}")
    if not path.is_dir() or not (path / "run-reservation.json").is_file():
        raise ValueError("run was not newly allocated in this isolated workspace")
    # A reservation alone does not authorize imported historical state.
    receipts = [json.loads(p.read_text(encoding="utf-8")) for p in ws.path("logs").glob("sample-*.json")]
    if not any(r.get("run") == name for r in receipts):
        raise ValueError("run lacks an isolated sample receipt")
    require_policy(replay_journal(path / JOURNAL_FILENAME).header)
    return path


def _sample(cfg, rm, adapter, ws, store, result, sleep, monotonic):
    folder = _owned_run(ws, cfg.run) if cfg.run else ws.path("outputs")
    if cfg.run and replay_journal(folder / JOURNAL_FILENAME).header.requested != (cfg.start, cfg.end):
        raise ValueError("Resume requires the exact original requested selection")
    job = ScrapeJob(novel_slug=SPEC.novel_slug, adapter_key=SPEC.adapter_key,
        start=cfg.start, end=cfg.end, delay=cfg.interval, output_mode=OutputMode(cfg.grouping),
        use_cache=False, output_dir=folder, chunk_size=cfg.chunk_size, formats=cfg.formats,
        max_retries=0, use_browser=False, request_timeout=cfg.timeout,
        allocate_run=cfg.run is None, resume=cfg.run is not None)
    report = run_scrape(job, adapter=adapter, request_manager=rm, manifest_store=store,
        cancel_event=rm.cancel_event, host_limiter=rm.host_limiter,
        sleep_fn=sleep, monotonic_fn=monotonic, log=result["messages"].append)
    result.update(run=report.output_dir.name, cancelled=report.cancelled,
        accounting=report.accounting, complete=not report.cancelled and not report.failed,
        summary=report.summary())
    header = replay_journal(report.output_dir / JOURNAL_FILENAME).header
    result.update(selection_sha256=header.selection_sha256, manifest_sha256=header.manifest_sha256)
    if rm.stopped:
        raise Stop(rm.stopped)


def audit_isolated(cfg, ws):
    from audit_run import audit
    ws.check()
    path = _owned_run(ws, cfg.run)
    result = audit(path, pdf_text=True)
    header = replay_journal(path / JOURNAL_FILENAME).header
    result["selection_sha256"] = header.selection_sha256
    result["manifest_sha256"] = header.manifest_sha256
    result["artifact_hashes"] = [{"path": str(p.relative_to(path)), "size": p.stat().st_size,
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(path.rglob("*")) if p.is_file()]
    result["production_approved"] = False
    ws.record("audits", "audit", result)
    return result


def execute(cfg, *, connection_factory=None, sleep=time.sleep, monotonic=time.monotonic):
    report = inspect(cfg)
    if cfg.mode == "dry-run":
        return report
    if cfg.mode != "audit" and connection_factory is None and not cfg.permit_network:
        raise ValueError("separate owner approval/configuration required for this live stage")
    if connection_factory is not None and cfg.permit_network:
        raise ValueError("offline simulation and network permission are mutually exclusive")
    if cfg.mode in ("sample", "audit") and not cfg.resume:
        raise ValueError("this stage requires explicit resume of the compatible isolated workspace")
    ws = Workspace.open(cfg, simulation=None if cfg.mode == "audit" else connection_factory is not None)
    with FileLock(ws.path("logs/.stage.lock")):
        return _execute_stage(cfg, ws, report, connection_factory, sleep, monotonic)


def _execute_stage(cfg, ws, report, connection_factory, sleep, monotonic):
    if cfg.mode == "audit":
        return audit_isolated(cfg, ws)
    allowed = {SPEC.url: 1}
    store = None
    if cfg.mode == "sample":
        store, selection = _sample_selection(cfg, ws)
        allowed = {m.url: 2 for m in selection.chapters}  # Existing pipeline's one sweep is also capped.
    else:
        slugs = cfg.slugs if cfg.mode == "probe" else range(1, cfg.bound + 1)
        allowed.update({chapter_url_for(SPEC, s): 1 for s in slugs})
    rm = RestrictedManager(ws, cap=cfg.cap, allowed=allowed, connection_factory=connection_factory,
        interval=cfg.interval, timeout=cfg.timeout, sleep=sleep, monotonic=monotonic)
    result = dict(mode=cfg.mode, configuration=report, observations=[], messages=[], complete=False,
        manifest_created=False, production_approved=False, simulation=connection_factory is not None)
    with ExitStack() as cleanup:
        def spool():
            ws.check()
            value = _PageSpool(ws.path("spool"))
            cleanup.callback(value.file.close)
            return value
        cls = SampleAdapter if cfg.mode == "sample" else AcceptanceAdapter
        adapter = cls(request_manager=rm, log=result["messages"].append, spool_factory=spool)
        try:
            if cfg.mode == "probe":
                _probe(cfg, rm, adapter, result)
            elif cfg.mode == "discovery":
                _discovery(cfg, rm, adapter, ws, result)
            else:
                _sample(cfg, rm, adapter, ws, store, result, sleep, monotonic)
            return result
        except BaseException as exc:
            result["complete"] = False
            result["stop_reason"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            result["attempts"] = rm.attempts
            result["transport"] = rm.events
            ws.record("logs", cfg.mode, result)
            rm.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--mode", choices=("dry-run", "probe", "discovery", "sample", "audit"), default="dry-run")
    parser.add_argument("--cap", type=int, default=0)
    parser.add_argument("--bound", type=int)
    parser.add_argument("--slugs", nargs="+", type=int, default=[])
    parser.add_argument("--resume-workspace", action="store_true")
    parser.add_argument("--permit-network", action="store_true", help="future separately approved selected live stage only")
    parser.add_argument("--execute", action="store_true", help="execute only the selected stage; otherwise inspect read-only")
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--start", type=int)
    parser.add_argument("--end", type=int)
    parser.add_argument("--formats", nargs="+", choices=("pdf", "txt"), default=[])
    parser.add_argument("--grouping", choices=("separate", "chunked", "single"))
    parser.add_argument("--chunk-size", type=int, default=2)
    parser.add_argument("--run", help="new isolated run name, for sample Resume or read-only audit")
    args = parser.parse_args(argv)
    cfg = Config(root=args.root, mode=args.mode, cap=args.cap, bound=args.bound,
        slugs=tuple(args.slugs), resume=args.resume_workspace, permit_network=args.permit_network,
        interval=args.interval, timeout=args.timeout, start=args.start, end=args.end,
        formats=tuple(args.formats), grouping=args.grouping, chunk_size=args.chunk_size, run=args.run)
    try:
        result = execute(cfg) if args.execute else inspect(cfg)
    except Stop as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 3
    except (ValueError, OSError, ChapterListRefused, ManifestStoreError, JournalError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
