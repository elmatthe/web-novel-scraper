# Isolated Option B acceptance runner (developer only)

`option_b_runner.py` is separate from the installed application. Use the existing
repository `.venv`. The CP6A checkpoint permits offline tests and inspection only.
The switches described here configure future owner-approved stages; they are not
authorization and do not authenticate approval. No stage implies another stage.

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

Configure the proposed diagnostic list for **inspection only**:

```powershell
# 4557 is a historical planning hypothesis, not a currently verified bound.
.venv/Scripts/python.exe -B files/option_b_runner.py --root $acceptanceRoot --mode probe --bound 4557 --cap 13 --slugs 1641 1642 1643 1644 1649 1650 1651 1652 1653 1953 1956 1960 --interval 3 --timeout 30
```

The future index request must show exactly the approved bound. Any difference or
missing same-source bound stops before requesting chapters; obtain a new reviewed
configuration and approval. A fresh bound larger than the cap never expands the
budget. No URL/displayed-number mapping is asserted by this list.

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
.venv/Scripts/python.exe -B -m pytest files/tests/test_option_b_runner.py -q -p no:cacheprovider --basetemp=files/run-temp/option-b-offline-tests
```

Never point this at owner caches, manifests or historical runs. Do not invoke any
live `--execute` command as part of CP6A. See the checkpoint note for the isolated
starting-HEAD verification, protected-file preservation and remaining approvals.
