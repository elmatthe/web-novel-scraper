# Supreme Magus — Checkpoint 4

Scope: F06/F08/F09/F10 content integrity, typed failures, publication accounting and cancellation. Starting commit `a19b84f42bdb084e9d8f5e7d6ca1b17863e8cd48`, branch `feature/v0.3.0-modernization`. No discovery, manifest-policy, migration or semantic Resume redesign.

## Behavior and evidence contract

- Canonical `ChapterContent.paragraphs` are fingerprinted with SHA-256 under `sha256-paragraphs-v1`: exact UTF-8 bytes, length-framed paragraphs, no title/layout/filename and no Unicode normalization. A same-body observation records both immutable selection positions, exact labels and URLs. Activity and audits warn; every chapter is retained, including short repeated prose, repeated labels and fractional labels. No deduplication filter is enabled. Duplicate source URLs are refused with their positions because selection identities must be unique by URL.
- Identity errors retain their existing extraction exception ancestry for cache/retry compatibility, but expose distinct `IDENTITY_MISMATCH`, `IDENTITY_UNKNOWN` and `IDENTITY_CONFLICT` reasons. Empty content remains `EMPTY_BODY`. Primary and rescue carry structured reasons, observed labels, original message, stage, format and HTTP status. Access/rate-limit documents and URL 404/410 outcomes do not certify chapter absence. Identity failures neither trigger network escalation nor bypass CP1 validation.
- Rendering reports `PDF_GLYPH_UNSUPPORTED` or `RENDER_ERROR`, separately from fetch/extraction. The established glyph-coverage test preserves original Unicode through TXT fallback. Requested PDF remains failed; an unrequested fallback is an actual TXT publication with `fallback_for=pdf`. Independently requested TXT is a requested-format success, not a fallback. Filesystem errors remain fatal after recording their known rendering outcome.
- New runs declare `outcome_policy=page-outcomes-v1`. Journal schema remains 1 with optional header metadata, version-1 `attempt` and `content` events, typed failure fields, and optional publication fallback metadata. Existing append-only sequence/CRC/fsync and atomic output publication remain. Replay validates event versions, policy/hash, selection position/URL/exact label, duplicate references and format/extension relationships. No story prose enters journal events.
- New readers accept previously valid legacy journals. Missing old attempt events mean `legacy-inferred`/`unrecorded`, not proof of never-attempted pages. Older readers refuse new unknown event types rather than silently discarding the evidence. The CP3 Supreme Magus Resume gate remains unchanged; this extension grants no owner-run Resume approval.
- Known terminal failures are appended immediately and idempotently by position, stage and format. A known primary failure awaiting recovery is retained until success or flushed on Stop/abort. Stop does not render pending groups or manufacture failures for unattempted pages. Completed files remain untouched. Retry success resolves current outcomes through later events while preserving failure history.
- Shared accounting distinguishes selected, attempted, processed, requested-format success/failure, pending, fallback-preserved, not-attempted and integrity suspicions. Requested page success requires all requested formats. Fallback is a subset of those pages, never an extra page. Actual artifact counts use published formats; audits enumerate fallback filenames and hashes even when TXT was not requested. Audit availability requires an intact recorded checksum. An intact earlier artifact survives a later failed fetch as an artifact success, with the failed fetch still inspectable; no new story-authenticity certification is implied.

## Changed files and dirty-tree isolation

Runtime: `models.py`, new `content_integrity.py`, new `run_accounting.py`, `adapters/freewebnovel.py`, `pipeline.py`, `rescue_pool.py`, `run_journal.py`, `run_output.py`, `ui/run_activity.py`, and the minimal coverage helpers in `pdf_builder.py` (all under `scripts/webnovel_scraper/`). Developer audit: `files/audit_run.py`. Tests: new `test_content_integrity_accounting.py` and `test_cp4_journal_contract.py`, plus focused expectation/corruption-target updates in `test_run_resume.py` and `test_ui_run_activity.py`. This note is the checkpoint handoff; shared dirty handoff/changelog files are intentionally preserved.

The starting tree contained 28 dirty tracked paths (three deletions) plus the untracked Windows smoke driver. Original bytes/diff and 41,320 protected-file hashes were captured under ignored `files/run-temp/sm-checkpoint4/`. Overlapping pipeline, output, journal, audit and Resume-test hunks were separated in memory against starting HEAD; working files retain the original work. Only the existing `UnsupportedGlyphError`, `unsupported_characters` and `require_glyph_coverage` helpers are adopted as the necessary fallback dependency. Unrelated font inventory, Unicode font-map changes, builder guards, path hardening, orphan-temp handling, source-list-gap metadata, launcher/setup changes and documentation remain outside this checkpoint commit. No fonts or dependencies were added.

`isolate.py` exports starting HEAD plus the approved hunks into a fresh repository-contained tree, without replacing working files. Both exports use the existing root Python 3.13 environment. The difference in gate counts reflects additional pre-existing tests in the actual working tree.

## Verification

All fixtures are synthetic and offline. Disposable stores, selections, journals, output files, logs and exports stay under `files/run-temp/sm-checkpoint4/`. No full `scripts/verify.py` or live scraping was run.

- Red regression demonstration: 42 new content/accounting cases failed against starting behavior. A deliberate isolated mutation that counted TXT fallback as PDF success was rejected by the fallback regression; original isolated bytes were restored exactly.
- Focused contract/output/cancellation/UI/Resume gate: **184 passed**. Command: `.venv/Scripts/python.exe -B -m pytest files/tests/test_cp4_journal_contract.py files/tests/test_content_integrity_accounting.py files/tests/test_run_resume.py files/tests/test_ui_run_activity.py -q -p no:cacheprovider --basetemp=files/run-temp/sm-checkpoint4/contract-final --tb=short`.
- Combined 33-module CP1–CP4 gate includes positive/fractional/composite identity, discovery, schema-2 manifest persistence, source isolation, .net compatibility, cache rejection, primary/rescue, journal integrity, publication, Activity and audit tests. Final native working tree: **1,453 passed**, zero failures/skips, 71.72 s. Isolated starting HEAD plus only approved checkpoint hunks: **1,433 passed**, zero failures/skips, 69.58 s. Commands: `.venv/Scripts/python.exe -B files/run-temp/sm-checkpoint4/gate.py` and the same command with `--isolated`. This disposable harness records the exact underlying pytest argument arrays in `working-final-command.json` and `isolated-final-command.json`, with `*-final.log` and `*-final.xml` alongside.
- New cases cover unknown/conflicting/wrong headings with valid bodies, empty bodies, Cloudflare/rate limits, HTTP 404/410, unexpected exceptions, repeated and nonconsecutive bodies, exact fractional labels, duplicate URLs, grouped output, Unicode fallback, actual format inventory/hashes, cancellation before first/after final page and during grouping/recovery, multiple same-label failures, immutable selection, legacy replay, malformed typed events, and repeated finalization.

Synthetic measurements (not a live scraping benchmark):

| Work | Result |
| --- | --- |
| 10,000 unique fingerprints, 4 KB bodies | 4,104,736 retained bytes, 4,113,404 peak, 0.110 s |
| 10,000 unique fingerprints, 100 KB bodies | 4,104,728 retained bytes, 4,305,204 peak, 0.524 s |
| 200 individual TXT publications | 2.128 s; 200 attempts, 200 content events, 200 publishes, one end |
| 200 pages, two per TXT group | 1.390 s; 200 attempts, 200 content events, 100 publishes, one end |

Fingerprint tracking stores only hashes and positions, about 411 bytes per unique chapter in this measurement; storage is O(selected pages), independent of prior body length. Grouped output retains the existing pending canonical-content behavior; this checkpoint does not claim constant memory for whole-range rendering. Metadata events introduce no chapter fetch requests. Final audit adds file checksum verification, not network work.

The forensic-equivalent accounting test reconciles 4,555 selected pages as 2,279 requested PDF successes + 282 identity failures + two PDF glyph failures + 1,992 not attempted. Two preserved TXT fallback artifacts belong to the glyph-failed pages. Actual publications total 2,281 artifacts, not 2,281 PDFs.

## Limits and next action

Duplicate fingerprints are conservative diagnostics, including potentially legitimate repeated prose. They prove byte equality under the declared canonical policy, not absence or which source page is correct. No exclusion, substitution or automatic repair policy is implemented. Old journals cannot reconstruct attempts they never recorded. Stop is supported; abrupt process termination during an in-flight recovery may leave only its durable attempt event, not a terminal failure. Journal writes and checksum validation can still fail on disk errors; existing append recovery and fail-closed replay remain.

Owner manifests, HTML caches, rejected pages, previous journals/audits/selections and protected output folders are read-only evidence and were not migrated, resumed, repaired or regenerated. CP3's non-destructive revalidation requirement still blocks incompatible owner state. CP5 semantic Resume certification and the final full verification/manual owner acceptance remain outstanding. This checkpoint does not establish release readiness.

Final disposition: **PASS for Checkpoint 4**, with protected-file hashes unchanged and unrelated dirty work retained. Commit/push identifiers belong in the terminal report because a commit cannot contain its own identifier.

Next exact action: return this note and the checkpoint terminal report for independent review. Begin CP5 only after a separate owner instruction; obtain separate approval for any owner-data migration.
