# Web Novel Scraper — handoff

2026-10-23 · Codex · HOME-PC · `feature/v0.3.0-modernization` → `main`.
Owner acceptance is complete: live scraping and HTML-cache reuse across all nine
enabled novel/site pairs. No new live scrape or historical-output repair is needed.

The 20 failures from the previous complete gate are remediated in tests only:
- Direct preflight tests now supply the explicit `auto` list policy.
- Rescue tests use an initialized pool; prefetched-page tests use a metadata-capable
  manager and verify zero fetching. Identity/cache rejection checks remain intact.
- Progress/Stop/Resume tests distinguish incremental chunks, final whole-range
  output and verified saved-body reuse. Legacy no-body file-preservation coverage
  remains, with a separate fatal incremental-render regression. Refresh/saved-list
  expectations match the current historical-coverage warnings and update policy.

Verification: original 20 failing cases pass; 448 neighboring regressions pass.
Final complete offline gate: **2,289 passed in 392.58s, zero failures/skips**.
Dependency and permanent-doc checks pass; the suite includes real Tk/adapter and
launcher regressions. No network attempts occurred. All 230,476 protected files
match original hashes, sizes and timestamps. The tested source/fixture tree is unchanged.

Pre-commit checkpoint: verified implementation and retained cleanup are approved
for normal feature push and ancestry-checked fast-forward into main. Resolve the
actual delivery SHA from Git references; this commit cannot record its own SHA.
No remaining implementation or owner-acceptance blocker is recorded.
Evidence is ignored under `files/run-temp/final-remediation-20261023/`; the previous
failed gate and original protection baseline remain under `final-cleanup-20261023/`.

Cleanup is retained: 12 tracked obsolete documents removed (three were already
absent at task start), all meaningful tests, 20 fixtures, launchers and reusable
scraping/audit/publication/repair tools retained. New production modules
`chapter_bodies.py` and `run_performance.py`, installer smoke utility and all other
approved untracked implementation/tests belong in the final commit. No obsolete
reports are reintroduced. README/Briefing/Decisions/Changelog carry lasting guidance.

Protect all existing cache, runtime manifests/preferences, browser/environment
files, publications, renamed Supreme Magus PDFs/journals and the original LOTM
1099 publication. Its repair remains outside the repository and unchanged. The
previous baseline contains 230,476 protected files. About 5 GB of historical
workspaces and useful local adapter references remain for separate archival review.
Do not delete the feature branch, rewrite history, create a tag or GitHub Release.
