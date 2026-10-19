# Supreme Magus remediation: Checkpoint 1

2026-10-18, Codex, HOME-PC, `feature/v0.3.0-modernization`.
Starting commit: `b008a44f574e5ed8f52074d8975f540fa038b837`.
Scope: positive extraction identity and exact fractional labels only.

The working tree began with 25 modified tracked files, three deletions and one
untracked file. The shared handoff/changelog already contain another work stream;
leave them intact. Their eventual integration needs this checkpoint's result and
the next authorized action, without reviving their older release instructions.

## Behavior and decisions

- Extraction resolves observed page identity independently of selection metadata.
  Missing identity and conflicting numbered evidence raise `ChapterMismatchError`,
  retaining existing cache invalidation, bounded refetch and prefetch validation.
  Opening story headings participate even when the outer heading matches the request.
  Ordinary story mentions cannot establish identity. Existing confirmed composite
  pairs are the sole reconciliation of different numbered headings.
- Known `Chpater` spellings, recovered titles, verified untitled chapters and
  canonical heading removal remain supported. A body or an unnumbered document
  title alone cannot establish a chapter's number.
- Fractions are parsed into exact `Decimal` values, then bridged through existing
  `ChapterLabel` / `manifest_label` conventions before numeric metadata conversion.
  `ChapterMeta.index` remains `int | float` for legacy range consumers; its value
  alone is not an exact identity certificate. Exact labels drive comparisons,
  deterministic ordering and content headings/filenames, including `627.1`/`627.10`.
- The existing pure metadata-label bridge now lives on `ChapterLabel`; preflight,
  the .com adapter and `ChapterIdentity.from_meta` share it. No persisted schema
  changes. This tightly coupled preflight edit only shares metadata conversion.
- `_extract_title` retains its legacy numeric return type for compatibility with
  existing callers/tests; production probes and extraction use the exact observed
  resolver. Its expected-number fallback is removed.
- The existing untitled-output regression now supplies a bare numbered heading.
  New negative tests require its original unnumbered shape to fail. The unrelated
  pending Unicode/PDF additions in that test file belong to the previous work stream.

## Verification

All tests are offline, using the root `.venv`, with disposable state/logs contained
under `files/run-temp/sm-checkpoint1/`. New fixtures contain synthetic diagnostic
prose, not downloaded chapters.

- Initial reproduction: 47 failed, 9 passed on the original production code.
- Identity/fraction slice: 206 passed across the new test module and the four
  requested regression modules.
- Expanded dirty-working-tree gate: 645 passed across 13 focused modules:
  `test_freewebnovel_positive_identity`, `test_supreme_magus_identity`,
  `test_composite_chapter_headings`, `test_chapter_number_offset`,
  `test_content_output`, `test_chapter_preflight`, `test_chapter_manifest`,
  `test_source_identity`, `test_html_cache`, `test_bug_hunt_bh2_cache`,
  `test_phase1`, `test_phase2`, `test_freewebnovel_net`.
- Rescue-default-method coverage plus worker lifecycle regressions: 81 passed.
- Isolated staged implementation: 647 passed across the same 13 modules plus
  `test_phase2_rescue_pool`, without any pre-existing uncommitted edits. The
  first export attempt had a missing pytest temporary-parent directory and ran
  no test bodies; the corrected contained path passed all 647.
- Mutation check in the disposable snapshot: inverting the exact-label mismatch
  guard caused both fractional mismatch regressions to fail; original snapshot
  bytes were restored afterward. No working source was mutated for this check.
- Commit/push details are reported in the implementation agent's terminal report;
  these focused results must not be represented as a full release gate.

The complete project verification gate is deferred to the approved final checkpoint.
No live website access, owner output operations, cache regeneration or manifest
migration is part of this work.

## Limits and next action

Numbered evidence establishes the displayed chapter label; it cannot prove that
the source supplied unique or correct story prose beneath a matching number.
The recognized misspelling list remains deliberately closed. Newly conflicting
page headings fail conservatively rather than selecting the first candidate.
Old metadata already missing trailing digits cannot reconstruct them; pages must
match the exact expected label, so stale selections can fail pending later review.
Mismatch/unknown/conflict failures retain the existing extraction-error hierarchy;
pipeline/UI failure classification is a later checkpoint.

Discovery inference, seed trust, offset reconciliation, manifest generations,
resume policy and duplicate-body reporting remain unchanged. The next recommended
step is independent review of Checkpoint 1, then separately authorized Checkpoint 2
(discovery confidence and bounded reconciliation). Do not start it automatically.
