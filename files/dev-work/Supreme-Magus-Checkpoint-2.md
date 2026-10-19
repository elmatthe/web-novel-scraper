# Supreme Magus remediation: Checkpoint 2

2026-10-18, Codex, HOME-PC, `feature/v0.3.0-modernization`.
Starting HEAD: `f90b2e135ed82f209c843f97b5a7632dd13f28de`.
Origin: `https://github.com/elmatthe/web-novel-scraper.git`.

**PASS for Checkpoint 2, with the temporary fresh-state restriction below.**
This is not saved-state, Resume, live-source or whole-novel acceptance.
The final commit SHA and remote confirmation belong in the terminal report.

## Behavior and boundaries

- `DiscoveryEvidence` distinguishes observed/current-page identities, candidates,
  saved unknown metadata, conflicts, unreadable pages, inaccessible responses,
  recognized extras and unknown identities. Explicit source absence is a separate
  concept; discovery never emits it from HTTP 404/410 or a missing label.
- Supreme Magus .com always scans every slug from 1 through the fresh index's
  source-scoped URL bound, even when the visible index slice has equal offsets.
  Displayed selection hints cannot exclude a nonmonotone source suffix. Catalog
  bound disagreements cannot certify a complete discovery. Binary search remains
  a candidate locator only and no longer determines the authoritative scan window.
- Every emitted chapter row has an independent current-page observation and an
  exact `ChapterLabel`. There is no offset interpolation. Short excursions,
  fractions, repeated labels and backward sequences survive. Known observations
  survive interior reconciliation; saved seeds cannot avoid a page read.
- Seeds contradicted by actual pages produce a visible diagnostic. Contradictory
  prior HTML observations remain conflicts, not an implicit choice of generation.
  Conflicting numbered headings fail closed using Checkpoint 1's resolver.
- Discovery passes `use_cache=False`: historical HTML/checksums never certify
  current identity and neither cache generation is promoted, rewritten or merged.
  Unexpected cached responses remain `saved_unknown`. Recognized opening joke /
  Volume 2 glossary headings are extras; arbitrary unnumbered pages are unknown.
  Missing body, access documents and typed fetch failures stay distinguishable.
- One temporary disk spool retains fetched HTML for extraction. Metadata holds
  page references rather than whole-novel HTML strings. Reads are locked, files
  close when the last reference is released, and extraction applies the existing
  positive identity guard again. Temporary files stay under `files/run-temp/`.
- Batches publish every 16 completed slugs, with one final prefix on cancellation
  or budget failure when necessary. Repeated prefix notifications are suppressed.
  Partial `ChapterIndex` callbacks never claim completeness/tail confirmation.
- Preflight rejects tagged candidates before identity conversion/coverage.
  **Supreme Magus persistence is deliberately disabled in this checkpoint.**
  Fresh complete observations form an in-memory projection for this run only.
  Existing baseline/runtime/legacy paths are refused before refresh, import,
  quarantine, locking or discovery, even with Refresh requested. No checkpoint
  can certify old-schema state. Unknown pages and unresolved requested labels
  cause refusal rather than a listed-gap/absence certificate.
- Other supported source pairings retain their existing selection policy. Normal
  .com generated addressing remains unchanged outside this source; .net was not
  modified. No float-based identity comparison was added.

F01 is blocked by independent seed revalidation and the saved-state refusal.
F02 is removed by observing every slug. F03 is isolated by uncached reads and
explicit historical uncertainty/conflict. F13 no longer supplies a completeness
proof through bisection, eight-slug padding or inferred progress rows.

## Verification

Root `.venv`: Python 3.13.12. All tests use fake managers and contained state.
No live website request or full project verification gate was run.

- Initial regression run: 56 failed / 29 passed, including one newly written
  fixture construction error; 55 failures demonstrated production defects. The
  earlier invocation had 85 setup errors because the temporary parent was absent;
  no test bodies ran in that invocation. The fixture and contained directory were
  corrected before the green runs.
- Final working-tree focused gate: **831 passed, 0 failed, 0 skipped**.
- Starting-HEAD export plus only these nine code/test/fixture files:
  **817 passed, 0 failed, 0 skipped**. Fourteen pre-existing test additions are
  excluded from this isolated result; no unrelated implementation is required.
- Mutation in a separate disposable export: allowing the equal-offset Supreme
  Magus fast path caused the identity-slice regression to fail (1 failed).
  Restoring the exact original bytes passed that case (1 passed).
- Fresh preflight, checksum-valid stale HTML, fully wrong seeds, saved-state
  refusal, cancelled preflight/checkpoint, incomplete restart, exact fractions,
  contradictory constraints, budget exhaustion and callback failure are covered.
  Snapshot fixtures keep legacy L, active C and rejected C evidence separate;
  missing snapshots remain unknown. No live-correct 1642/1643 map is invented.
- Checkpoint 1's typo, 1953/1956, composite-heading, exact-label and safe
  cache/prefetch rejection regressions pass. .com and .net compatibility pass.
- A separate read-only review was attempted but stopped at a provider usage
  limit. Local diff review, isolated verification and mutation testing completed;
  an independent reviewer approval is not claimed.

Exact focused command (PowerShell, repository root):

```powershell
$tests = @(
  'test_discovery_confidence', 'test_chapter_number_offset', 'test_chapter_preflight',
  'test_freewebnovel_positive_identity', 'test_supreme_magus_identity',
  'test_composite_chapter_headings', 'test_chapter_manifest', 'test_source_identity',
  'test_html_cache', 'test_bug_hunt_bh2_cache', 'test_phase1', 'test_phase2',
  'test_phase2_rescue_pool', 'test_freewebnovel_net', 'test_content_output'
) | ForEach-Object { "files/tests/$_.py" }
.venv/Scripts/python.exe -B -m pytest @tests -q -p no:cacheprovider --basetemp=files/run-temp/sm-checkpoint2/review-final --junitxml=files/run-temp/sm-checkpoint2/review-final.xml --tb=short
```

The isolated gate uses the same module list from the exported repository, the
root interpreter, and separate `isolated-testtmp` / `isolated.xml` paths.
Raw logs, XML, disposable exports and the offline measurement driver remain in
`files/run-temp/sm-checkpoint2/`; they are not runtime or committed owner data.

## Offline request comparison

Counts exclude the one index-page request. "Content" extracts every returned
row; the hinted case therefore compares different result sizes (17 versus 497).

| Synthetic scenario | Old discovery | New discovery | Old additional content | New additional content | Old / new total |
|---|---:|---:|---:|---:|---:|
| 500 slugs, hinted | 20 | 500 | 11 | 0 | 31 / 500 |
| 500 slugs, full | 52 | 500 | 461 | 0 | 513 / 500 |
| 600 slugs, long excursion | 67 | 600 | 548 | 0 | 615 / 600 |
| 4,417 slugs, structural scale | 364 | 4,417 | 4,073 | 0 | 4,437 / 4,417 |

The 4,417-page scan emitted 4,414 positively observed rows, two recognized extras
and one inaccessible URL. Its completeness flag remains false. One spool held
1,049,442 synthetic HTML bytes; no full HTML strings ride on the returned rows.
New discovery took about 5.1 seconds offline versus 1.9 seconds for old discovery.
These are local parser/fixture timings, not network throughput predictions.
Small selections cost more discovery because no trustworthy complete TOC or
monotonicity certificate permits excluding distant aliases. Full selections do
not require a duplicate whole-novel fetch for content. Interruption intentionally
requires reobservation on restart until Checkpoint 3 can persist safe generations.

## Replaced invalid test assumptions

No identity/prose/publication assertion was weakened. The following old tests
needed new evidence expectations (names abbreviated to their distinguishing part):

| Module / test | Replacement |
|---|---|
| offset / binary_search_and_bounded_walk | Full source scan, exact 116/116.5 mapping, unique requests |
| offset / full_range_compressed_walk | One observation per slug, every returned row verified |
| offset / real_size_full_range | 4,417 unique reads; missing URL prevents completeness |
| offset / pipeline_near_high | Same output, discovery HTML reused without another fetch |
| positive_identity / seeded_exact_label | Actual page read required; 627.10 still exact |
| supreme_identity / run_and_resume | Synthetic discovery supplies actual page observations; output assertions retained |
| supreme_identity / equal_gaps_span | Same exact map; 600 independent page reads |
| supreme_identity / plain_offset_few_fetches | Same exact map; 600 unique reads |
| preflight / persisted_then_no_TOC | Fresh in-memory selection; exact selected labels/slugs retained; next run reobserves |
| preflight / extension_only_new_window | Wider selection stays correct; unsafe cross-run reuse unavailable |
| preflight / duplicate_number | Both observed URLs and distinct publications retained; no old-schema manifest written |

The synthetic slug manager now names its intended extras positively instead of
calling arbitrary `-9` headings extras. Separate negative regressions require
unknown/access/error pages to remain unresolved.

## Preservation and next action

The original 28 tracked dirty paths (including three deletions) and untracked
Windows smoke driver are excluded from this checkpoint. Shared handoff/changelog
edits remain untouched; this note is the separate checkpoint handoff.
No protected output, selection, journal, PDF/TXT, personal cache, saved manifest,
backup or download was migrated, refreshed, repaired, resumed, deleted or rewritten.

Checkpoint 3 must define persisted evidence/policy generations, safe reuse,
overlay/baseline/legacy handling, authorized backup-preserving migration and
rollback. This checkpoint cannot resume discovery from old persisted metadata.
Existing-run Resume bypasses chapter-list preflight and remains an explicitly
unremediated Checkpoint 5 dependency; **do not treat it as production-safe**.
The current live 1642/1643 identities and exact tail remain unresolved.

Next: independently review this checkpoint and separately authorize Checkpoint 3.
No Checkpoint 3-5 work, owner acceptance crawl, release, tag or main merge started.
