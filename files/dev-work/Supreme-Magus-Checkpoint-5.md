# Supreme Magus remediation — Checkpoint 5

Scope: semantic Resume admission and final automated verification. Starting HEAD
`6f62ba5ba1bf9a5d501b7cf411c899edcfc5ca12`, branch
`feature/v0.3.0-modernization`. This note substitutes for the already dirty shared
handoff/changelog. No migration, scraping, GUI acceptance or release integration.

## Architecture and explicit transitions

Journal schema remains 1, with optional `resume_policy=semantic-resume-v1` and
`extraction_policy=chapter-extraction-identity-v1` in new-run headers. They require
CP4's `page-outcomes-v1` and `sha256-paragraphs-v1`. Missing/unsupported policies
never grant Resume eligibility. These identifiers describe approved application
extraction/identity validation and publication behavior; incompatible future
implementations must change identifiers and refuse previous certificates.

An explicit versioned digest of immutable header fields binds the complete SourceIdentity
(host, novel, adapter, index URL, identifiers), selection SHA/count/exact endpoints,
manifest digest, requested formats and grouping. Selection SHA covers ordered
ChapterIdentity rows, including exact URL, slug and fractional label. Content
events add extraction policy and exact accepted observed identity after the
adapter's CP1 validation. Confirmed composite headings retain their final whole
identity while exact fractions retain trailing zeroes.

Each publication references the position, sequence and SHA-256 of each exact
accepted content event in page order. Its certificate also binds header context,
actual format, fallback relationship, complete/missing membership, path, size
and actual file SHA-256. Replay validates every historical relationship before
updating state, rejects unknown events and contradictory associations, and keeps
sequence/CRC conventions. No copyrighted body text is recorded in journals.

TXT publishing checks deterministic UTF-8 serialization of the accepted canonical
content against the actual file before certification. Both formats check renderer
inputs still match accepted body fingerprints and exact identities. PDF certificates
attest approved renderer invocation with those inputs and actual published bytes;
they do not claim independent text extraction or infallible PDF authentication.

## Admission, legacy runs and proof limits

F11 previously let a recorded size/SHA and empty `missing` list authorize a skip.
A wrong chapter's historically recorded PDF could therefore be byte-valid. Now
the application refuses missing/incompatible semantic policy or publication linkage
before lock creation, journal repair/appends, cleanup or rewriting. It rechecks
admission under the run lock. Artifact hash checks and planning precede journal
repair/appends and cleanup. Only complete current-policy publications with intact
bytes can skip a group.

Legacy journals remain readable for historical analysis but are LEGACY UNVERIFIED,
even with CP4 body hashes and valid output bytes. No filename, output hash, legacy
selection or inferred manifest can promote them. The remedy is a new isolated run.
This small general compatibility break applies to all novels because checksum-only
Resume is unsafe generally. Other novels retain their existing manifest/discovery
policies; no Supreme Magus schema-2 requirement is imposed on them. Selected mappings
must still exist and agree, while unrelated chapter-list growth can coexist with
their immutable historical selection. Supreme Magus additionally retains CP3's exact
complete schema-2 policy/evidence digest gate, including source-wide trust generation.

Checksums are integrity/provenance evidence, not signatures or adversarial authenticity.
Someone fabricating an entire coherent journal can fabricate its checksums. The
application cannot prove the external website supplied correct story prose, nor
claim old cached observations are fresh. No live-correct 1642/1643 mapping is asserted.

## Grouped outputs, failures and cancellation

Separate, chunked and whole-range groups keep immutable boundaries and order.
An incomplete group fetches the pages needed to render its missing formats; already
complete certified groups fetch nothing. Missing/modified current-policy files are
recovered only from matching accepted canonical bodies. Pages already accepted but
not yet published retain the same conservative body pin on restart.

A changed fetched body appends `content_conflict` with old content-event reference
and candidate body hash, plus typed CONTENT_CONFLICT failure. The old accepted
content remains authoritative; that group publishes nothing further. Existing
publications remain untouched and subsequent Resume refuses with a new-run remedy.
Warnings about identical bodies remain suspicions, preserving both pages without
asserting which story is correct. No candidate conflict poisons duplicate tracking.

TXT glyph fallback is certified as actual TXT, with `fallback_for=pdf` and earlier
requested-PDF glyph failure evidence. It never certifies a PDF. Partial outputs,
missing formats, cancellation and unattempted pages retain CP4 accounting. Torn
tails are trimmed only after semantic admission and read-only artifact planning.
Publication/append crashes leave unrecorded files uncertified and recoverable from
newly fetched matching bodies; historical publication/attempt/failure events remain.

Audit remains read-only. It separately reports semantic eligibility/reason and
CERTIFIED, INCOMPLETE, INCOMPATIBLE, LEGACY UNVERIFIED or SUSPICIOUS status, keeping
byte accounting, actual artifacts and duplicate warnings distinct.

## Changed files and isolation

- `scripts/webnovel_scraper/semantic_resume.py`
- `scripts/webnovel_scraper/pipeline.py`
- `scripts/webnovel_scraper/run_journal.py`
- `scripts/webnovel_scraper/run_output.py`
- `scripts/webnovel_scraper/content_integrity.py`
- `scripts/webnovel_scraper/observed_preflight.py`
- `files/audit_run.py`
- `files/tests/test_semantic_resume.py`
- `files/tests/test_cp4_journal_contract.py`
- `files/tests/test_verified_manifest_lifecycle.py`
- `files/tests/test_chapter_preflight.py`
- `files/tests/test_brotli_extraction_fix.py`
- `files/tests/test_reliability.py`
- `files/tests/test_ui_activity_panel.py`
- This note.

Pre-edit snapshots captured 28 dirty tracked paths (three intentional deletions)
and the untracked Windows smoke driver. Overlap is limited to pipeline, journal,
output and audit; only CP5 deltas are staged. Synthetic fixtures and logs live
under `files/run-temp/sm-checkpoint5/`. A HEAD-only export plus CP5 deltas passed
independently. Residual diffs were reviewed to exclude all pre-existing edits.
Staging contains exactly the 15 listed files. All **159 tracked export files**
match the staged tree after Git's normal clean conversion; 141 unchanged archive
files require only CRLF/LF conversion, independently checked with path-specific
`git hash-object`. No material difference is waived. Only this results note differs
from the raw full-tested snapshot; all runtime and test bytes remain as tested.
The pre-existing dirty paths, deletions and smoke driver remain unstaged.

## Verification and owner preservation

RED: all three initial semantic regressions failed as expected: wrong legacy output
accepted, no policy recorded, independent content alteration accepted. Intermediate
focused implementation gate: 144 passed. Expanded initial focused gate: 886 passed,
one synthetic adapter fixture lost exact `627.10`; it now preserves ChapterLabel
from the immutable meta rather than returning a lossy floating-point label.

Approved runtime focused gate: **889 working-tree / 869 isolated passes**, followed
by the expanded **66-case semantic module passing in each**. Five newly added cases
bring unique focused coverage to **894 working-tree / 874 isolated**, zero failed or
skipped. The 20-case difference is pre-existing uncommitted tests excluded from the
export. Mutation checks in the disposable export deliberately omitted policy
admission and publication/content comparison; their respective regressions failed.
Exact original bytes were restored before the final isolated semantic gate.

Full gate attempt 1: **1,983 collected, 1,979 passed, 4 failed, 0 skipped**, exit 1;
dependency and documentation gates passed. All four failures reproduced in an
unchanged `6f62ba5` export (four failed). Two asserted pre-CP4 extraction-log wording;
two incorrectly expected real failure history to disappear after recovery or
preservation in an earlier publication. Three test files now assert typed EMPTY_BODY
events, preserved historical failures, later recovery sequences and exact current
success/failure accounting. No runtime workaround, suppressed check or deleted test.
The three corrected modules passed **35 isolated native tests**. Root focused
confirmation also passed **35 native tests**. Combined unique focused coverage is
**929 working-tree / 909 isolated passes**, including the corrections; no skips.

Final full rerun: **1,983 collected / 1,983 passed / 0 failed / 0 skipped**, **exit 0**,
116.99 seconds. Requirements: PASS, all dependencies exactly pinned. Documentation:
PASS, required CHANGELOG and Briefing content valid. No remaining environmental
limitation; native Windows/Tk access was used for synthetic GUI unit tests because
prior sandbox gates could not initialize installed Tcl. No GUI scraping ran.

Interpreter: repository-root `.venv/Scripts/python.exe`, Python **3.13.12** (64-bit).
Full-gate working directory: `files/run-temp/sm-checkpoint5/isolated/` under this
repository. Exact absolute directory/interpreter and all 159 exported tracked-file
hashes are recorded in the local `full-tested-snapshot.json`. The tested state is
starting HEAD plus only the 15 approved CP5 files, excluding unrelated dirty work.
Its snapshot SHA-256 before this final results-only note update is
`e4220cd75ea6576364759062409ba35e09164d740009b1f77df0b190055d4f3c`.
No runtime/test file changed after this full gate. Only this note is finalized
afterward, with an exact staged/export comparison before commit.

Full rerun command, from that isolated directory:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTEST_ADDOPTS='-p no:cacheprovider --basetemp=../full-final-testtmp --junitxml=../full-final.xml'
& ../../../../.venv/Scripts/python.exe -B scripts/verify.py > ../verify-full-final.log 2>&1
```

Attempt 1 used the same interpreter/command with `../full-testtmp`, `../full.xml`
and `../verify-full.log`. Both process exit codes are saved alongside the logs.
The single rerun is justified solely by the four reproduced stale assertions.
No full gate was run against unrelated dirty documentation to manufacture a pass.

Focused commands use `-B -m pytest`, `-q -p no:cacheprovider --tb=short`, isolated
basetemp and JUnit XML destinations. The 16-module gate covers semantic Resume,
run Resume, CP4 journal contracts, content integrity/accounting, verified manifest
contracts/lifecycle, Supreme Magus identity, positive identity, composite headings,
discovery confidence, chapter offsets/preflight, output rendering, .net support,
audit and source identity. Logs: `focused-approved.log` (889), `isolated-focused.log`
(869), `semantic-final.log` and `isolated-semantic-final.log` (66 each, 61 already
included), `working-gate-corrections.log` and `gate-corrections.log` (35 each).
JUnit XML records every result; mutation logs and `starting-head-red.xml` retain
the negative checks. All synthetic artifacts remain under the checkpoint scratch
directory; no generated logs, archives or fixtures are staged.

Read-only pre-edit inventory captured 41,321 existing protected files, covering
owner runs and runtime/cache roots. Byte/path comparison confirmed **zero changed,
deleted or newly created protected files**. Tracked baseline files are additionally
covered by repository state/snapshot comparisons.
No owner migration/refresh/Resume/repair, GUI scrape, personal-output write, font
or dependency change is authorized or executed by this checkpoint.

## Remaining owner decisions and next workflow

After automated verification, stop for independent review of this scoped commit
and note. Owner-manifest migration and any new-run GUI acceptance require separate
approval. Existing schema-1 owner state remains ineligible. Source identity around
1642/1643 still needs independent evidence, with no inferred correction claimed.
Subsequent acceptance should use a separately approved new isolated run and audit;
release integration, main merge, tags and publication remain out of scope.

Checkpoint status: **PASS for CP5 automated implementation**, subject to the final
commit/push confirmation in the terminal report. No release-readiness claim.
