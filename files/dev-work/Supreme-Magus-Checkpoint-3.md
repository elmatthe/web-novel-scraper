# Supreme Magus remediation: Checkpoint 3

2026-10-18, Codex, HOME-PC, `feature/v0.3.0-modernization`.
Starting HEAD: `e47c4566cd4714cb557e6f4fc7afb1cb2ff0a7f9`.
Origin: `https://github.com/elmatthe/web-novel-scraper.git`.

Scope: verified manifest persistence, compatibility, refresh and failure recovery.
No live requests or owner migration. Final commit and push confirmation belong in
the terminal report. This note substitutes for editing the already dirty shared handoff.

**PASS for Checkpoint 3. Owner migration remains blocked pending separate approval.**
This is not whole-application release readiness or owner acceptance.

## Contract and architecture

- `ChapterManifest` retains schema 1, including its existing checksum, for other
  sources. Schema 2 adds `Verification`, policy `sm-com-page-identity-v1`, scoped
  strictly to Supreme Magus / freewebnovel / freewebnovel.com. Old parsers reject
  its version and extra key. No checksum-valid schema 1 gets promoted.
- Schema 2 integrity covers exact chapter rows, absences/spans, source, layer,
  policy, per-page evidence, provenance, timestamps and completeness flags.
  A checksum protects integrity, not authenticity against someone fabricating an
  entire certificate. Application producers must independently observe pages.
- Each page records its URL, exact labels, outcome, acquisition time, provenance
  and detail. Observed identities, recognized extras, unresolved/inaccessible/
  unreadable pages, conflicts, candidates and saved unknown metadata stay distinct.
  Pending revalidation retains prior exact identities and their acquisition times
  across restarts, so an interrupted check cannot erase conflict comparison.
- Every selected row must match its observed page certificate. Fractions such as
  `627.1` and `627.10`, duplicate labels on different URLs, and reverse numbering
  survive. No source absence is certified by this policy; 404/410 and omitted
  displayed numbers remain insufficient. `toc_complete` is never persisted true.
- `chapter_verification.py` contains the scoped evidence contract;
  `observed_preflight.py` implements its lifecycle using the existing store,
  writer lock, atomic JSON publisher and common selection logic. This is one
  manifest system, with no persistent HTML or spool pointers in its records.
- Schema 2 runtime is a full evidence snapshot. It suppresses baseline entries
  during invalidation instead of merging them back. Contradictory verified
  baseline/runtime observations remain inspectable conflicts and refuse selection.
  Incompatible baselines block reuse even beside a valid runtime snapshot.

## Reuse and freshness

- Clean discovery reads every slug within the freshly observed same-source index
  bound; only current-page positive identities become chapter rows. Checkpoints
  atomically persist completed observations every 16 reads and on interruption.
- Finite covered requests reuse compatible historical observations without TOC
  traffic. There is no time-based claim of fresh website validation. Logs disclose
  historical reuse. Actual content still passes CP1 independently on every fetch.
- Open-ended or uncovered requests read the index again and reobserve the last
  64 saved source positions plus new/unresolved positions. Their pending tail is
  invalidated atomically before fetching. No saved last label establishes a
  current end. Only this request's reliable fresh bound supports end clamping.
- Partial observations survive and are reused on retry, but any unresolved source
  position prevents covered spans and selection: that page could contain a
  duplicate or backward label in the requested range. Once all positions resolve,
  coverage includes only actually observed whole labels. Displayed gaps remain
  unresolved and never become source absence; unrelated covered ranges can work.

## Refresh, conflicts and rollback

- Chapter-list refresh stays separate from `refresh_html_cache`. For verified
  runtime state it exclusively copies exact previous bytes to a unique backup,
  then atomically invalidates old authority before independent reobservation.
  No old guessed seed can suppress a read. Repeated callbacks are idempotent.
- Conflicting old/new observations withhold the page, persist both labels and
  acquisition context, remove coverage and refuse selection. An explicit full
  refresh is the defined resolution path. No timestamp winner is selected.
- Interruption retains completed new observations and unresolved pending records.
  A publication failure cannot expose torn JSON. Failure after backup but before
  invalidation leaves original bytes and backup intact, before any source read.
  Failure publishing a later conflict leaves the previously committed pending
  invalidation in force. Automatic backup restoration is disabled for this policy.
- A decreased source bound persists a conflict and blocks reuse. A repeated
  smaller bound stays blocked, including explicit refresh; a separately reviewed
  source-bound disposition is needed. Omitted URLs cannot prove source absence.
- Store guards reject incompatible state before locking, quarantine, refresh,
  legacy import, publication or restoration. Direct metadata-only discovery
  publication is refused. Invalid Supreme Magus overlays stay in place.
- A narrow shared primary/rescue Resume gate closes the removed-entry bypass:
  only a complete current-policy snapshot with matching exact selected identities
  and the same schema 2 evidence digest may resume. Pre-CP3 metadata digests or
  missing/invalidated entries cannot approve old runs. CP5's broader semantic
  output/run certificate redesign remains outstanding.

## Owner migration preview — not executed

Source: `https://freewebnovel.com/novel/supreme-magus-novel`.
Observed protected runtime path:
`files/runtime-data/chapter-lists/freewebnovel.com/supreme-magus.json`.
Its schema is **1**, policy absent, byte SHA-256:
`a585f986af9fae55fe446563788eb52ef12d5dbe5b9cffd383e7f47569fef57e`.
It validates structurally but has no independent current-policy page certificates.
No Supreme Magus baseline was found in the default baseline store.

Proposed unique backup beside that file:
`supreme-magus.json.migration-a585f986af9fae55fe446563788eb52ef12d5dbe5b9cffd383e7f47569fef57e.bak`.
The preview chooses a non-overwriting suffix if occupied. A future separately
authorized migration must exclusively copy exact bytes, verify their digest,
independently scan uncached source pages, then atomically publish validated schema 2
under the existing writer lock. Before publication preserve originals; afterward
keep unresolved observations and backups without restoring obsolete authority.
Restoring schema 1 bytes never grants current-policy trust. Legacy output indexes,
if explicitly offered later, receive their own exact-path preview and refuse import.
Existing caches, rejected HTML, selections, journals, backups and outputs stay out
of the proposed mapping evidence. No migration executor is shipped in CP3.

The complete read-only exact-absolute-path preview is captured locally in
`files/run-temp/sm-checkpoint3/owner-migration-preview.json`. It includes the source,
paths, schema/policy, reasons, backup destination, transitions, failure policy and
expected full-scan cost. Owner-state runs remain **blocked pending separate
migration approval and implementation**. No approval is requested by this note.

## Offline measurements

All sources and stores below are synthetic. Counts exclude the index request.
Times are local fixture/parser/storage timings, not network predictions.

| Scenario | Chapter-page discovery requests | Reused verified | Persisted observed | Unresolved positions | Extra content requests |
|---|---:|---:|---:|---:|---:|
| Clean full 500 | 500 | 0 | 500 | 0 | 0 for 500 selected |
| Second finite 10–14 | 0 | 500 | 500 | 0 | 5 for 5 selected |
| Tail 500 → 510 | 74 | 436 | 510 | 0 | 0 for 6 new selected |
| Explicit refresh 510 | 510 | 0 | 510 | 0 | 0 for 5 selected |
| Interrupt after 16/500 | 16 | 0 | 16 | 484 | No selection |
| Independent restart | 484 | 16 | 500 | 0 | 16 for historical selected |
| One unreadable / 500 | 500 | 0 | 499 | 1 | No selection |
| Retry unresolved only | 1 | 499 | 500 | 0 | 0 for repaired selected |

Clean discovery: 1.649 s; finite reuse: 0.088 s; extension: 0.553 s;
refresh: 2.245 s; restart: 1.633 s; unresolved retry: 0.169 s.
Fresh selected pages share one temporary spool and need no duplicate content
fetch; historical selected pages need actual content reads and CP1 validation.
No spool survives serialization. Raw measurements and driver are local under
`files/run-temp/sm-checkpoint3/`.

## Verification and preservation

Root `.venv`, Python 3.13; offline deterministic fixtures only. Initial lifecycle
RED was 9 failed / 2 passed. Review reproduced and fixed interrupted-comparison
and Resume missing-entry bypasses before the green gates. Contract fault tests
cover prefirst/partial interruption, refresh backup/invalidation failure, failed
atomic replacement, conflict publication failure, restoration refusal, contention,
tampered evidence/provenance/policy/source, and baseline/legacy reentry.

Final working-tree expanded gate: **940 passed**, zero failed/skipped, across the
18 modules below. Starting HEAD plus the 14 staged CP3 files, excluding all
pre-existing dirty work: **920 passed**, zero failed/skipped. The 20-case difference
comes from pre-existing uncommitted test additions, not skipped cases.

Additional store/writer/legacy-import/UI gate: **174 passed** in the working tree.
Isolated export: **164 passed** plus ten UI cases initially skipped because the
sandbox could not initialize installed Tcl; those exact ten passed on the permitted
native rerun. Combined final totals: **1,114 working-tree / 1,094 isolated passes**.
No unresolved failures or skips. Disabling the schema 2 integrity comparison in the
disposable export made the provenance-tamper regression fail; restoring exact
original bytes passed it. The implementation was not mutated for that check.

```powershell
$tests = @(
 'test_verified_manifest_contract','test_verified_manifest_lifecycle',
 'test_discovery_confidence','test_chapter_number_offset','test_chapter_preflight',
 'test_freewebnovel_positive_identity','test_supreme_magus_identity',
 'test_composite_chapter_headings','test_chapter_manifest','test_source_identity',
 'test_html_cache','test_bug_hunt_bh2_cache','test_phase1','test_phase2',
 'test_phase2_rescue_pool','test_freewebnovel_net','test_content_output','test_run_resume'
) | ForEach-Object { "files/tests/$_.py" }
.venv/Scripts/python.exe -B -m pytest @tests -q -p no:cacheprovider --basetemp=files/run-temp/sm-checkpoint3/final --junitxml=files/run-temp/sm-checkpoint3/final.xml --tb=short
.venv/Scripts/python.exe -B -m pytest files/tests/test_chapter_store.py files/tests/test_chapter_store_writer.py files/tests/test_chapter_legacy_import.py files/tests/test_ui_chapter_list.py -q -p no:cacheprovider --basetemp=files/run-temp/sm-checkpoint3/store-ui --junitxml=files/run-temp/sm-checkpoint3/store-ui.xml --tb=short
```

The isolated gate uses the same root interpreter from the export's working
directory, with `../isolated-testtmp` / `../isolated.xml` and
`../isolated-store-ui` / `../isolated-store-ui.xml`. The native UI rerun uses
`test_ui_chapter_list.py`, `../isolated-ui-native` and `../isolated-ui-native.xml`.

CP2's temporary no-persistence assertions now assert verified persistence or
retained incomplete observations; their identity/output/refusal checks remain.
CP1's synthetic Resume fixture now supplies observations for the full source bound.
Other supported .com novels and .net pairings retain schema 1 behavior.
No full `scripts/verify.py`, live scraping, dependency changes or release gate ran.

The initial 28 dirty tracked paths (three deletions) and untracked Windows smoke
driver are preserved. Only CP3 hunks in the already dirty pipeline are staged;
the shared handoff/changelog are untouched. A read-only hash inventory covers
41,320 existing protected runtime/cache/baseline/output files. Final comparison
confirmed byte-for-byte preservation, including reconstruction of the original
dirty pipeline by reversing only CP3 hunks. No new protected files were created.

Next exact action after CP3: independent owner/ChatGPT review of this note and the
committed diff. Obtain separate authorization for any owner migration or CP4.
Do not start CP4/CP5, owner acceptance scraping, release preparation or main merge.
