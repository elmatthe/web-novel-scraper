"""Read-only local cache/evidence inventory. Never migrate or repair owner data."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from webnovel_scraper.catalog import all_specs
from webnovel_scraper.html_cache import HtmlCache, cache_key_for, content_problem, page_host
from webnovel_scraper.chapter_manifest import parse_manifest_bytes
from webnovel_scraper.source_identity import SourceIdentity
from webnovel_scraper.chapter_preflight import meta_from_identity
from webnovel_scraper.registry import REGISTRY


def audit():
    root = REPO / "files/cache"
    rows, fingerprints = [], {}
    for spec in all_specs():
        if not spec.enabled:
            continue
        source = SourceIdentity.from_site_spec(spec)
        cache = HtmlCache(root, spec.novel_slug, site_key=source.host)
        folder = root / source.host / spec.novel_slug
        valid, sidecars, invalid, urls = 0, 0, [], set()
        paths = list(folder.glob("*.html"))
        for path in paths:
            data = path.read_bytes()
            fingerprints[str(path.relative_to(REPO))] = hashlib.sha256(data).hexdigest()
            if path.name.endswith(".invalid.html"):
                invalid.append([path.name, "previously set aside by adapter"])
                continue
            try:
                raw = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
                url = raw["url"]
                reason = cache._sidecar_problem(url, raw, data)
                if path != cache.html_path(url):
                    reason = "URL key or host/novel folder mismatch"
                if reason is None:
                    sidecars += 1
                    reason = content_problem(data.decode("utf-8"))
                if reason:
                    invalid.append([path.name, reason])
                elif cache.read(url) is not None:
                    valid += 1
                    urls.add(url)
                else:
                    invalid.append([path.name, "cache reader rejected entry"])
            except (OSError, ValueError, KeyError, TypeError) as exc:
                invalid.append([path.name, str(exc)])
        expected, manifest_notes = [], []
        for manifest_root in (REPO / "files/runtime-data/chapter-lists", REPO / "scripts/webnovel_scraper/chapter_lists"):
            path = manifest_root / source.host / (spec.novel_slug + ".json")
            if not path.is_file():
                continue
            try:
                manifest = parse_manifest_bytes(path.read_bytes(), expected_source=source,
                    expected_layer="runtime" if "runtime-data" in str(path) else "baseline")
                expected.extend(c.page_url for c in manifest.chapters)
                manifest_notes.append(dict(path=str(path.relative_to(REPO)), chapters=len(manifest.chapters),
                    spans=manifest.spans, verified=bool(manifest.verification),
                    complete=bool(manifest.verification and manifest.verification.complete_for(source))))
            except Exception as exc:
                manifest_notes.append(dict(path=str(path.relative_to(REPO)), error=str(exc)))
        legacy = list((root / spec.novel_slug).glob("*.html"))
        rows.append(dict(novel=spec.novel_slug, host=source.host, directory=str(folder.relative_to(REPO)),
            html_files=len(paths), json_files=len(list(folder.glob("*.json"))), valid_sidecars=sidecars,
            valid_entries=valid, invalid=invalid, expected_urls=len(set(expected)),
            expected_key_hits=len(set(expected) & urls), legacy_html=len(legacy), manifests=manifest_notes))
    sm_keys, sm_html, errors = set(), [], []
    # Inspect evidence roots, including isolated workspaces. Do not walk browser installs or
    # third-party overlays. Permission-denied historical test directories are reported.
    sm_prefix = "https://freewebnovel.com/novel/supreme-magus-novel/chapter-"
    sm_keys = {cache_key_for(sm_prefix + str(n)): n for n in range(1, 4566)}
    for directory, dirs, names in os.walk(REPO / "files", onerror=lambda e: errors.append(str(e))):
        dirs[:] = [d for d in dirs if d not in {"bin", "__pycache__", "py312-pinned-overlay", "node_modules"}
                   and not d.startswith("cache-audit-")]
        for name in names:
            if not name.lower().endswith((".html", ".htm")):
                continue
            path = Path(directory) / name
            try:
                data = path.read_bytes()
                key = path.stem.removesuffix(".invalid")
                if key in sm_keys or b"supreme-magus" in data.lower():
                    sm_html.append(dict(path=str(path.relative_to(REPO)), bytes=len(data),
                                        slug_from_key=sm_keys.get(key)))
            except OSError as exc:
                errors.append(str(exc))
    return dict(configurations=rows, supreme_html_evidence=sm_html, unreadable_paths=errors,
                cache_fingerprints=fingerprints)


def audit_extraction(result):
    """Read-only adapter validation; never invoke fetch_chapter or cache migration."""
    from concurrent.futures import ProcessPoolExecutor, as_completed
    with ProcessPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_audit_one_extraction, row): position
                   for position, row in enumerate(result["configurations"])
                   if "identity_valid_entries" not in row}
        for future in as_completed(futures):
            result["configurations"][futures[future]] = future.result()
            destination = REPO / "files/dev-work/html-cache-audit-2026-10-22.json"
            destination.write_text(json.dumps(result, indent=2), encoding="utf-8")


def _audit_one_extraction(row):
    specs = {(s.novel_slug, SourceIdentity.from_site_spec(s).host): s for s in all_specs() if s.enabled}
    spec = specs[row["novel"], row["host"]]
    source = SourceIdentity.from_site_spec(spec)
    manifest_path = REPO / "files/runtime-data/chapter-lists" / source.host / (spec.novel_slug+".json")
    if spec.novel_slug == "supreme-magus":
        # Read-only historical certification supplies identity comparisons only.
        # It is NEVER installed into the active store or used to grant coverage.
        manifest_path = REPO / "files/run-temp/sm-full-discovery-20261022-01/runtime/freewebnovel.com/supreme-magus.json"
    manifest = parse_manifest_bytes(manifest_path.read_bytes(), expected_source=source, expected_layer="runtime")
    identities = {c.page_url: c for c in manifest.chapters}
    adapter = REGISTRY[spec.adapter_key]()
    good, unknown, rejected = 0, 0, []
    for sidecar in (REPO / row["directory"]).glob("*.json"):
        raw = json.loads(sidecar.read_text(encoding="utf-8"))
        identity = identities.get(raw["url"])
        if identity is None:
            unknown += 1
            continue
        html = sidecar.with_suffix(".html").read_text(encoding="utf-8")
        try:
            meta = meta_from_identity(identity)
            if spec.adapter_key == "webnovel_dynamic":
                adapter._extract_chapter(html, meta)
            else:
                adapter._extract_for_source(html, meta, spec)
            good += 1
        except Exception as exc:
            rejected.append(dict(url=raw["url"], reason=str(exc)))
    row.update(identity_valid_entries=good, identity_unmapped_entries=unknown,
               identity_rejected_entries=rejected)
    print(source.host, spec.novel_slug, "chapter identities", good, "unmapped", unknown,
          "rejected", len(rejected), flush=True)
    return row


if __name__ == "__main__":
    if "--extraction-only" in sys.argv:
        destination = REPO / "files/dev-work/html-cache-audit-2026-10-22.json"
        result = json.loads(destination.read_text(encoding="utf-8"))
        audit_extraction(result)
        destination.write_text(json.dumps(result, indent=2), encoding="utf-8")
        raise SystemExit(0)
    result = audit()
    destination = REPO / "files/dev-work/html-cache-audit-2026-10-22.json"
    destination.write_text(json.dumps(result, indent=2), encoding="utf-8")
    for row in result["configurations"]:
        print(json.dumps({k: v for k, v in row.items() if k != "invalid"}))
        print("invalid reasons:", dict(Counter(reason for _, reason in row["invalid"])))
    print("Supreme HTML evidence:", len(result["supreme_html_evidence"]))
    print("Unreadable directories/files:", len(result["unreadable_paths"]))
    print("Report:", destination)
