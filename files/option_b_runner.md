# Isolated discovery and publication tools (developer only)

`option_b_runner.py` is separate from the installed application. Use the existing
repository `.venv`. The v0.3.0 owner acceptance is complete. These tools remain
useful for independent diagnostics, future source integration and offline
republication. Stage switches configure intentional future work; they do not
authorize live network requests or changes to historical owner data. No stage
implies another stage.

Read-only default, from the repository root (PowerShell):

```powershell
$acceptanceRoot = Join-Path (Get-Location) 'files/run-temp/option-b-OWNER-CHOSEN-NEW-ROOT'
.venv/Scripts/python.exe -B files/option_b_runner.py --root $acceptanceRoot
```

The absolute root must be newly named, with an existing parent. Nothing is created
by inspection. Existing roots require `--resume-workspace` and the exact compatible
workspace marker. All seven fixed directories must exist, `baseline/` must remain
empty, and no links/reparse points, hard links, unexpected top-level entries,
nested workspaces or protected-root overlaps are accepted. Downloads, application
runtime/cache/output/temp, both baseline locations and the default discovery spool
are excluded. No owner data can be imported. Treat the root as exclusively owned
by this runner; checks do not defend against a hostile process replacing filesystem
components between checks and writes. An OS file lock serializes stages per root.

Prepared modes (select exactly one with `--mode`):

| Mode | Explicit configuration | Artifacts |
|---|---|---|
| `dry-run` | `--root` | Console report only; no files or network |
| `index` | Fresh `--root`, `--cap 1`; no bound, slugs, resume, range or output options | One index GET; bounded structural receipt under `logs/`; no raw HTML, chapters or manifest |
| `probe` | `--slugs` (1–12 unique positive integers), `--bound`, `--cap` | Attempt/heading/body-hash evidence under `logs/`; no manifest/certificate |
| `discovery` | Independently approved `--bound`, `--cap` >= bound+1 | Atomic schema-2 checkpoints under `runtime/`; temporary `spool/`; no PDF/TXT |
| `sample` | Complete isolated manifest; `--start`, `--end`, `--formats`, `--grouping`, `--cap`, `--resume-workspace` | Fresh allocated run under `outputs/`, fixed selection/journal, CP4/CP5 evidence |
| `audit` | `--run supreme-magus-N`, `--resume-workspace` | Read-only examination of that new run; report/hashes under `audits/` |

Without `--execute`, every mode only inspects configuration. A future live probe,
discovery or sample additionally requires `--permit-network` after separate owner
approval. Audit prohibits network permission. There is no run-all option or approval
token. `--run` on sample means semantic Resume of a run created in this workspace,
with its exact original requested range, formats/grouping and CP5 certificate.
`--resume-workspace` alone does not Resume a publication or advance stages.

The 2026-10-22 index checkpoint authorizes one attempt to the configured Supreme
Magus index only, using a new `files/run-temp/sm-index-validation-20261022-01`
workspace. In the existing VS Code PowerShell terminal, from the repository root:

```powershell
$smIndexRoot = Join-Path (Get-Location) 'files/run-temp/sm-index-validation-20261022-01'
if (Test-Path -LiteralPath $smIndexRoot) { throw 'Index workspace already exists; do not rerun or resume' }
.venv/Scripts/python.exe -B files/option_b_runner.py --root $smIndexRoot --mode index --cap 1 --interval 3 --timeout 30 --execute --permit-network
```

This mode allows only `SPEC.url`, once, through the same no-retry/no-redirect
transport. It never creates an adapter or enters chapter, discovery or publication
machinery. It does not use an expected bound: a recognized maximum with no detected
structural uncertainty yields `observed_maximum`, with both `expected_bound` and
`maximum_differs_from_approval` null. `complete=true` means this inspection completed;
`full_novel_coverage_established` remains false. No candidates, detected pagination
or scan truncation stop with the existing structural outcomes. Transport failures
retain `not_evaluated` and attempt evidence. Do not rerun after any attempt, even on
failure. Review the single `logs/index-*.json` receipt before proposing another stage.

Configure the proposed diagnostic list for **inspection only**:

```powershell
# 4557 is a historical planning hypothesis, not a currently verified bound.
.venv/Scripts/python.exe -B files/option_b_runner.py --root $acceptanceRoot --mode probe --bound 4557 --cap 13 --slugs 1641 1642 1643 1644 1649 1650 1651 1652 1653 1953 1956 1960 --interval 3 --timeout 30
```

The future index request must show exactly the separately approved bound. Any
difference, missing same-source candidates, detected pagination or truncated link
analysis stops before requesting chapters; obtain a new reviewed configuration and
approval. A fresh bound larger than the cap never expands the budget. No
URL/displayed-number mapping is asserted by this list.

Index link recognition parses anchor `href` attributes only. As in the source adapter,
relative links resolve against the catalog origin (`https://freewebnovel.com`):
`/novel/supreme-magus-novel/chapter-N` and `novel/supreme-magus-novel/chapter-N`
are recognized, along with absolute and scheme-relative equivalents. The normalized
scheme and authority must be exactly `https` and `freewebnovel.com`, with the exact,
case-sensitive `/novel/supreme-magus-novel/chapter-N` path. One trailing slash is
allowed; query strings and fragments do not contribute chapter-number evidence.
Ports, userinfo, subdomains, lookalike hosts, other novels, path prefixes/suffixes,
encoded chapter paths, leading zeroes, signs, fractions and non-ASCII digits are
rejected. Positive slugs have at most ten ASCII digits. No display-text fallback,
HTML base override, new request URL, expanded slug list or additional navigation
is introduced. Production adapter code is unchanged.

The maximum is evidence from this one index response. `matches_approval` means only
that its maximum matches the independently approved configuration; even one matching
link never establishes full-novel coverage. Partial indexes with lower maxima fail
the comparison. Explicit `.pagination`/`.pager` containers, an accessible
`Pagination` label, or `next`/`prev` anchor/link relations mark structural uncertainty,
even if the maximum matches. This conservative marker check cannot identify every
possible partial, paginated or dynamically loaded index. Absence of those markers
is not a completeness claim. Discovery still requires its separate approval and
independent per-page observation walker; probe mode cannot certify coverage.

Every evaluated index adds `index_bound` to the exclusive stage receipt, including
when the stage raises `Stop`:

| Field | Meaning |
|---|---|
| `source`, `provenance`, `response_sha256` | Exact configured source, current-index-response provenance and transport response hash |
| `anchor_hrefs_examined` | Number of anchor hrefs actually examined, at most 20,000 |
| `accepted_chapter_links`, `distinct_chapter_slugs` | Accepted links, including duplicates, and distinct numeric candidates |
| `maximum_candidate_slug`, `expected_bound` | Observed maximum or null, and the separately approved configuration |
| `no_recognized_candidates`, `maximum_differs_from_approval` | Zero-candidate flag and comparison; comparison is null when no maximum exists |
| `rejection_counts` | Five fixed categories: invalid href/URL, source mismatch, novel-path mismatch, chapter-path mismatch |
| `analysis_truncated`, `pagination_marker_present` | Explicit uncertainty flags |
| `source_bound_validation` | `no_candidates`, `mismatch`, `matches_approval`, `observed_maximum` (index only), or `insufficient_structure` |
| `full_novel_coverage_established`, `raw_response_retained` | Always false for index-link evidence |
| `href_scan_limit`, `href_length_limit`, `slug_digit_limit` | Structural analysis limits: 20,000, 2,048 characters, ten digits |

If index transport stops before extraction, the outcome is `not_evaluated`; no
candidate counts or maximum are invented. Transport evidence retains attempt count,
outcome, status when available, `response_bytes` and SHA-256. A body over the existing
8 MiB transport limit stops; `sha256_complete_response=false` identifies the hash of
the bounded prefix read (at most 8 MiB + one byte), rather than a full-response hash.
Redirects still stop immediately; only a boolean redirect flag is retained.

Index metadata retains no href/text samples, query values, fragments, response
headers, cookies, credentials, script content or raw HTML. Fixed counters and flags
keep its size independent of page content; synthetic tests check under 4 KiB of
index metadata and under 16 KiB for index-failure receipts. The examination limit
counts only inspected hrefs; hitting it makes the maximum insufficient for validation.
Receipts serialize before opening their checked final path, use exclusive creation,
flush and fsync before `Stop` propagates, and never overwrite previous receipts.
Storage failures surface as errors; there is no write rescue outside the workspace.

Raw-response capture was assessed and is not implemented. It would retain potentially
sensitive source content and requires a separate future retention/privacy decision,
explicit owner authorization, a fresh isolated workspace, an index-only scope and a
finite byte limit. There is no raw-capture switch, automatic chapter-prose capture,
or hidden request. Structural receipts remain until the owner separately disposes
of that isolated evidence; this runner does not delete previous evidence.

All navigations use one fresh stdlib `HTTPSConnection` per GET, an exact URL allowlist,
and `HostRateLimiter` (minimum interval 3 seconds, concurrency one, timeout <=60
seconds). Budget is consumed immediately before `request()`, including failed
attempts. HTTP has no retry adapter and redirects are never followed. Calling
again consumes another slot and must also meet its per-URL limit (index/probes/
discovery: one). Sample's existing pipeline sweep can use a second per-page slot
only within the same total cap. Access failures and transport errors latch Stop
before that sweep. Browser startup/navigation/warm-up and the inherited manager
ladder/session entrypoints are prohibited. This HTTP-only policy may stop at the
first challenge on the live source. Enabling browsers requires new implementation,
offline verification and approval; changing `use_browser` is insufficient.

Resume of incomplete discovery refreshes the index, requires the unchanged approved
bound, and reuses only compatible resolved independent observations. Unresolved
positions remain unresolved; cancelled completion cannot certify coverage. Complete
discovery cannot be revalidated in place: use another fresh approved workspace.
Source-bound/conflict disposition is manual. Samples require exact coverage and at
most 12 selected pages including fractional/duplicate labels; no end clamping.
Unicode TXT fallback and duplicate-body suspicion remain incomplete/reviewable
acceptance outcomes. Automated audit does not establish source authenticity, visual
PDF fidelity, full-novel acceptance or production approval.

Offline tests fake connections below the attempt gate; socket/DNS and stock browser
entrypoints are denied. Synthetic workspaces are marked and cannot later be opened
for a live stage. Run focused tests with contained scratch:

```powershell
$runnerTestTemp = 'files/run-temp/runner-focused-CHOOSE-UNUSED'
if (Test-Path -LiteralPath $runnerTestTemp) { throw 'Choose a fresh unused test folder' }
.venv/Scripts/python.exe -B -m pytest files/tests/test_option_b_runner.py -q -p no:cacheprovider --basetemp=$runnerTestTemp --tb=short
```

Never point tests at owner caches, manifests or historical runs. Offline
verification must not invoke live `--execute` commands. Keep previous stage
receipts and source evidence unchanged; choose a fresh workspace for new work.

## Owner-authorized full publication (2026-10-22)

`--mode full` is an independent developer mode; sample limits remain unchanged.
It requires the certified `sm-full-discovery-20261022-01` manifest, pinned by its
file SHA-256, exact displayed range 1–4552 (4561 identities, including nine
fractions), both TXT/PDF, chunked grouping and 100 chapters per group.
The maximum is 4561 single, paced HTTP attempts; there is no index request,
retry, redirect, browser, rescue, parallel fetch or live resume path.

```powershell
.venv/Scripts/python.exe -B files/option_b_runner.py --root "$((Get-Location).Path)/files/run-temp/sm-full-discovery-20261022-01" --mode full --resume-workspace --cap 4561 --start 1 --end 4552 --formats txt pdf --grouping chunked --chunk-size 100 --interval 3 --timeout 30 --permit-network --execute
```

Execute only once under active supervision after offline checks. An exclusive
`outputs/full-publication-invocation.json` claims that invocation; it must never be
removed to restart live work. New publication runs and `full-evidence-*` siblings
are allocated beneath the existing workspace's outputs directory. The evidence
holds fsynced pre-send HTTP intents/results, individually hashed canonical bodies,
an acceptance ledger, progress, preservation hashes and the final publication audit.
Complete groups publish incrementally through the production journal and builders.
Certification, checkpoints, old evidence and old output files remain untouched.

Source heading identities and exact original titles must agree with certification.
The original title spelling is preserved, including chapter 2475's leading number;
empty titles remain empty in the selection and display as `Untitled`. Duplicate
bodies retain every identity and remain explicit audit concerns. Complete publication
requires every chapter in both formats, exact canonical TXT, readable PDF with all
canonical text in order (modulo layout whitespace), hashes and valid journal records.
Unsupported glyphs, omitted PDF text and incomplete groups fail completion.

`full_publication.recover_offline(cfg, ws, evidence)` is an explicit local-only
repair entry point for this new invocation. It uses production semantic admission
and torn-tail journal repair, verifies saved-body acquisition hashes and prior
fingerprints, preserves complete files and publishes only complete recoverable
groups. It cannot create a request manager or perform any HTTP request. It is
never called automatically after interruption. The regular GUI now supports
chunk publication and verified saved-body Resume independently of this tool.

Focused offline verification: `python -m pytest files/tests/test_full_publication.py
files/tests/test_option_b_runner.py files/tests/test_run_resume.py
files/tests/test_content_output.py files/tests/test_audit_run.py -q`.

The full-publication and offline-repair utilities deliberately pin the historical
Supreme Magus inventory and its SHA-256. They need that local certified workspace;
a fresh clone does not contain it. For another inventory, adapt the configuration
and its tests explicitly rather than removing certification checks. The regular
GUI uses the current catalog/manifests instead of this historical configuration.

`offline_pdf_repair.py --destination <new-contained-folder>` republishes the
verified historical bodies into a fresh run with network access denied. It refuses
an existing or overlapping destination and preserves the source run. Regression:
`files/tests/test_offline_pdf_repair.py`.

`audit_html_cache.py` reads cache/manifests and writes its inventory report to
`files/dev-work/html-cache-audit-2026-10-22.json`. Its `--extraction-only` mode
requires that report and local manifests, including the historical certified
Supreme Magus workspace. Preserve previous evidence separately before intentionally
regenerating this report. It never migrates or repairs the cache itself.
