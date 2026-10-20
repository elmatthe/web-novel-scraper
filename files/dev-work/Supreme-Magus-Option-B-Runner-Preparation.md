# Supreme Magus Option B runner preparation — CP6A

Scope: offline implementation and synthetic verification only. Starting HEAD:
`691013c4efe8150a6f2d4c69db969bfa61df89f1`, branch
`feature/v0.3.0-modernization`. This note replaces an update to the already dirty
shared handoff. Readiness SUMMARY/REPORT are the approved architecture; CP1–CP5
certification and production behavior remain unchanged.

## Design and boundaries

The developer tool is `files/option_b_runner.py`, with configuration instructions
in `files/option_b_runner.md` and offline tests in `files/tests/test_option_b_runner.py`.
The only application seam is optional `FreeWebNovelAdapter.spool_factory` and an
explicit `_PageSpool(directory)`. Existing callers retain their defaults.

Each explicit absolute new root contains only the workspace marker and seven
directories: empty read-only `baseline/`; isolated `runtime/` manifest/lock state;
isolated disabled-persistent `cache/`; temporary `spool/`; fresh allocated
`outputs/`; exclusive evidence receipts and stage lock under `logs/`; independent
read-only audit reports/hashes under `audits/`. Every store and manager is explicitly
injected. Spools close through a runner-owned ExitStack on success/failure/cancel.
There is no process-global path redirection in the runner.

Existing roots require explicit compatible workspace resume. Resumption checks
the marker/version/source, exact top-level structure, empty baseline, all descendants,
symlinks/junctions/reparse attributes/hard links and canonical containment. Parent
markers reject nested workspaces. Owner/default runtime/cache/output/temp/baseline/
spool roots, metadata directories and all Downloads paths are excluded, including
overlapping ancestors. Synthetic/live evidence origins cannot be mixed. This is an
exclusive developer workspace policy, not protection against hostile concurrent
filesystem replacement. Source locks plus a per-root OS stage lock serialize writes.

## Request enforcement and fail-stop policy

Inspection found stock HTTP warm-up, automatic redirects, browser escalation even
with `use_browser=False`, browser warm-up/recreation and internal retry paths.
A counter around that fetch facade would not prove a navigation cap.

The runner's RequestManager subclass initializes the explicitly isolated cache and
limiter, then uses a separate one-GET stdlib HTTPSConnection path. A mutex serializes
the entire operation. Exact approved URL/per-URL/total caps and cancellation are
checked before pacing and again before navigation. The slot is consumed directly
before `connection.request`; failed requests never refund it. The connection has
no redirect, retry or browser machinery. All redirect statuses stop without a
second hop. Public inherited HTTP/browser facades reach a prohibited-ladder gate;
the inherited HTTP session is also refused. Startup is an explicit no-op. No
browser engine, warm-up, proxy, cookie session or rescue lane is started.

Every index/probe/discovery URL has one allowed attempt per invocation. Index plus
12 probes has a total cap of 13, no retry allowance. Sample pipeline sweep calls,
if any, consume actual slots and a maximum second per-page allowance inside the
separate total cap. HTTP/access/challenge/rate-limit/decode/oversize/transport
failures latch Stop/cancellation so no rescue or sweep continues. Parser-level
access detection also stops the discovery walker immediately. The existing
HostRateLimiter enforces >=3-second pacing; socket timeout is explicit and <=60
seconds, response size <=8 MiB. Socket timeouts bound blocking operations, not the
elapsed duration of an entire slowly streamed response. There is no retry ladder.

## Prepared independent modes

Default CLI is read-only dry-run, with no root creation, network, stage advancement
or output. Bytecode caching is disabled before imports so invoking without `-B`
cannot write project import caches during inspection. Any selected stage remains
inspection-only without `--execute`.
Live probe/discovery/sample additionally require separate owner approval and
explicit `--permit-network`; these switches describe intent, not authenticated
authorization. No approval secret/token or run-everything switch exists.

Probe takes 1–12 explicit unique in-bound slugs, obtains one uncached index, requires
its exact approved same-source bound and calls the existing CP1 page parser directly.
It never calls the full index walker. Logs contain URLs, observed exact labels,
headings, body count/hash, response hash and distinct outcomes. Fractions and
independently corroborated composites retain existing meaning; duplicate labels
remain separate source pages. No speculative slug mapping, manifest or completeness
certificate can be created from probes.

Discovery separately requires an independent bound+index cap. The existing CP3
observation walker and atomic schema-2 publisher preserve completed observations,
strict policy, unresolved positions, provenance and exact labels. Interrupted
discovery can resume only with unchanged bounds and compatible partial state;
complete revalidation requires another fresh workspace. Cancellation revokes the
bound-confirmed flag before checkpointing so it cannot certify complete coverage.
No PDF/TXT is published by discovery, no owner cutover occurs.

Sample requires a complete isolated schema-2 manifest and exact requested coverage,
at most 12 selected source pages, explicit PDF/TXT formats/grouping and a separate
cap. It injects the existing CP4/CP5 pipeline with a discovery-refusing adapter and
fresh run allocation. Resume requires a receipt from this workspace, its exact
requested selection and current semantic certificate; old journals are refused.
Audit reads only this run, checks existing accounting/certificates/heading extraction
and artifact hashes, writes only under audits, and never grants production approval.

## Verification and preservation

**PASS for offline CP6A preparation only.** No live stage has been executed, and
offline success does not establish live acceptance readiness. Root `.venv` only,
no dependencies/fonts installed. Original inventory: 41,321 protected files,
28 dirty tracked paths including three deletions, and untracked Windows smoke driver.
The before/after inventory confirms zero changed/deleted/added protected files,
unchanged directories, byte-identical original dirty tracked files/deletions and
byte-identical smoke driver. Owner schema-1 manifest remains SHA-256
`a585f986af9fae55fe446563788eb52ef12d5dbe5b9cffd383e7f47569fef57e`.
Both protected Downloads runs and all owner runtime/cache/baseline/output roots
remain untouched. Local evidence is under `files/run-temp/sm-checkpoint6a/`.

Initial RED collection failed because the tool did not exist. The staged slices
then passed 24, 37 and 65 focused cases; expanded final focused suite: **66 passed**.
An isolated starting-HEAD archive with only this checkpoint's five files was
verified separately. Final combined isolated gate: **1,221 passed, zero failed,
errors, skipped or warnings**, 66.94 seconds. This includes all 66 runner cases
plus relevant discovery/identity/manifest/store/writer-lock/preflight/journal/
Resume/content-integrity/output/audit/cache/source regressions across 21 modules.
`scripts/verify.py` was not rerun: CP5 already passed its full gate and this scoped
preparation does not change application transport, identity or output policies.

The isolated gate installed a Python audit hook denying DNS/socket/network and
writes outside this checkpoint's scratch root. Existing cross-process lock tests
use only their exact reviewed lock-holder command with the same guard injected
into the child. First broad attempt: 1,218 passed, two harness failures because
the guard denied these intentional lock-holder subprocesses, plus one harness
warning from Windows extended-path normalization. Correcting those harness checks
gave 1,220 passes; the final added default-output-fallback refusal brought the
final gate to 1,221. No test or application invariant was weakened or skipped.

The budget mutation check deliberately removed the total cap in the disposable
export. Its initial run exposed a stationary test clock and was stopped; the test
clock now advances. The corrected check produced exactly two expected test
failures, zero errors, proving cap-removal detection; exact source bytes were
restored before the final gate. Only identified CP6A test processes were stopped.
Final review also added early bytecode-cache suppression. A dry-run mechanical
check with bytecode initially enabled denied every filesystem write and network
operation and passed; the final 1,221-case isolated gate includes that exact code.

Reproducible focused command (new scratch basename required for a new run):

```powershell
.venv/Scripts/python.exe -B -m pytest files/tests/test_option_b_runner.py -q -p no:cacheprovider --basetemp=files/run-temp/sm-checkpoint6a/focused-66 --junitxml=files/run-temp/sm-checkpoint6a/focused-66.xml --tb=short
```

Isolated gate command: `.venv/Scripts/python.exe -B files/run-temp/sm-checkpoint6a/gate.py regression importfinal`.
Exact module list/arguments are in `importfinal-command.txt`, JUnit in
`importfinal-final.xml`, output in `importfinal-final.log`. The local guard/export scripts,
snapshots and generated evidence are deliberately untracked. The source/test files
in the final export match the scoped index after Git's normal text conversion;
only this results note is finalized after testing. No unrelated dirty hunk is
included. Remote feature branch was checked at the starting HEAD before commit;
the final terminal report supplies the new commit SHA and push confirmation.

## Later owner approvals and limitations

First review this checkpoint in ChatGPT. Then approve only the source index plus
12 diagnostic pages, a newly named absolute isolated root, exact slug list, cap 13,
HTTP-only/no-browser policy, interval 3 seconds (or slower), timeout 30 seconds and
immediate fail-stop conditions. Proposed slugs:
`1641 1642 1643 1644 1649 1650 1651 1652 1653 1953 1956 1960`.
Historical bound 4557 is only the initial planning hypothesis: mismatch stops after
the index, requiring a new reviewed bound/configuration/approval before more reads.
No probe was executed, and no `1651=1642` or other mapping is asserted.

Full discovery then needs separate approval of the fresh source bound, whole-source
request cap, workspace/resume policy and evidence review. Finite output stages
each need exact ranges/formats/grouping/caps/new run folders; approved new-run
cancellation/Resume and read-only audit remain separate owner decisions. Missing
requested identities, unreviewed duplicates, Unicode PDF fallback, PDF visual
fidelity or source access problems prevent acceptance claims. The restrictive
HTTP transport may be blocked immediately; browser support is deliberately refused
until separately designed, verified and approved. Private adapter seams tie the
tool to this reviewed code snapshot. Owner migration, production approval, full-novel
output, main integration, release and tags remain outside every stage here.
