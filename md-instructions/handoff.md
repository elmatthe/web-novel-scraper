# webnovel-scraper - Handoff

## Snapshot — v0.3.0 Windows release candidate, awaiting owner acceptance
**2026-10-18 · HOME-PC (Windows 11) · Claude Code (Opus 5.5) · branch `feature/v0.3.0-modernization`**

- **State:** Checkpoints 01–07 plus owner-acceptance round-1 fixes implemented, verified and pushed.
  Not merged, tagged or released; `main` untouched. macOS deferred (ADR 013).
- **Owner results so far:** nine small ranges (29 PDFs, no failures); .net LOTM 1301–1305 all five
  after the 1303 fix; Reverend Insanity 1–13 and LOTM (.com) 1430–99999 (clamped to 1432) audited;
  Start 1433 refused; Reverend Insanity 14–43 Stop then Resume audited (table in the acceptance doc).
  **No whole-novel run exists yet**; the earlier automated full-novel batch was BLOCKED by a local
  network denial (not a pass, not an app fault).
- **Last verification:** 1,476 tests pass on Python 3.13.12 (`.venv`) and 3.12.10; `scripts/verify.py`
  PASS. Live: .net LOTM 1301–1305 saves all five (scratch copy of the owner's list self-corrected).
- **Release blockers:** whole-novel runs (item 11, at least one pairing per website) and the unreported
  checks listed in the acceptance doc ("Owner results so far").
- **Ready:** the independent GO/NO-GO review prompt (Part 2 of `v0.3.0-Acceptance-and-Review.md`),
  draft notes in `md-instructions/v0.3.0-Release-Notes-DRAFT.md`. Review must not merge, tag or release.
- **Next exact action:** the owner starts the independent review and/or runs a whole-novel pairing.
- **Do not stage:** the deleted `md-instructions/0.2.1_bug-hunt.md`, `0.2.1_release_plan.md`,
  `Decisions-template.md`, or the untracked `scripts/scripts-to-use-from-my-other-repo's-GUIs/`.
  Never `git add -A`.
- **Owner data (ignored by Git, never touch destructively):** `.venv`, `files/cache` (includes the
  1.2 GB pre-0.3.0 cache, copied on demand, never changed), `files/runtime-data`, `files/bin`.

## Checkpoint commits (approved start `df08070` = 03.2a)
| Checkpoint | Commit | What |
|---|---|---|
| 03.2b + overrides A/B | `12e83be` | End clamp (ADR 011), validated self-updating HTML cache (ADR 012) |
| 03.3–03.4 | `8926a30` | Numbered run folders, checksummed journal, real Resume (ADR 014) |
| 05 | `7ce33ff` | Canonical chapter text, TXT output, faithful PDFs (ADR 015) |
| 04 | `c1b03af` | LOTM 1/2 on .com and .net, headless-first browser (ADR 016) |
| 06 | `fb7dfe9` | Stop during builds, offline open-ended runs, memory |
| 07 | `3fd7b99` | Windows setup and repair (ADR 017), review fixes, cache-hit pacing, `files/audit_run.py` |
| Acceptance round 1 | `89ce3da` | .net irregular chapter addresses (LOTM 1303), smooth preparing bar, ETA ignores cached chapters |

The round-1 commit is the last code commit; the commit after it only records its SHA here.

## Novel × website (Windows)
| Website | Novel | Enabled | Evidence so far |
|---|---|---|---|
| Free Web Novel (.com) | Shadow Slave | yes | offline suite; live small run; owner round 1 small run |
| Free Web Novel (.com) | Reverend Insanity | yes | offline suite; live small run; owner round 1 small run |
| Free Web Novel (.com) | Renegade Immortal | yes | offline suite; live small run; owner round 1 small run |
| Free Web Novel (.com) | Supreme Magus | yes | offline suite (offset numbering); live small run 4–5; owner round 1 small run |
| Free Web Novel (.com) | Lord of the Mysteries | yes | live list 1–1,432; sampled chapters; small run; cached repeat; owner round 1 small run |
| Free Web Novel (.com) | Lord of Mysteries 2 | yes | live list 1–1,181; sampled chapters; small run; owner round 1 small run |
| Free Web Novel (.net) | Lord of the Mysteries | yes | live list (1,432 entries; 1303 at a misspelt address); 1301–1305 all five; owner round 1 (before the fix: 4 of 5) |
| Free Web Novel (.net) | Lord of Mysteries 2 | yes | live list 1–1,181; small run; End 5000 clamped to 1181 live; owner round 1 small run |
| WebNovel (Dynamic) | The Noble Queen | yes | offline suite; live small run; owner round 1 small run |
| Empire Novel / Telegraph | Shadow Slave | no ("coming soon") | stubs |
| Novel Bin | The Noble Queen | no ("coming soon") | stub |

Whole-novel runs: **none yet** (owner checklist item 11).

## Open issues
- Whole-novel runs unproven against the live sites (owner item 11).
- Not exercised on this PC: Python installation on a PC without Python (download and verification
  were), the window at 125%/150% scaling (this PC is at 100%).
- Site-side: the .net LOTM list gives 1303 a misspelt address (now read as listed); untitled .net list
  entries LOTM2 888, 1069 and LOTM 1180; a stray quote in .net LOTM 1301's title ("New Life“"), kept.
- Older deferred minors #3–#6 from `files/test-logs/v0.1.0_pre-release.md` remain for owner review.

## Log (newest first; Git has the full record)
- 2026-10-18 · release preparation: owner results recorded, review prompt corrected, draft release notes,
  regenerable caches (`__pycache__`, `.pytest_cache`) removed; everything else kept (inventory in the
  final report).
- 2026-10-18 · acceptance round 1 `89ce3da`: .net list reader accepts a misspelt chapter address only when it is a
  single page of the novel whose number matches (LOTM 1303); indeterminate bar on a fixed scale;
  `phase_cb` carries `from_cache` so cached chapters stay out of the ETA (plan §9.18).
- 2026-10-18 · 07 `3fd7b99`: launcher rewrite (`setup_check.py`, `install_python.ps1`), start-up engine warning,
  DPI awareness, version 0.3.0, README; independent review (5 findings) fixed with tests; cache hits no
  longer wait the inter-fetch delay; `files/audit_run.py`; ADR 017; plan §9.17.
- 2026-10-18 · 06 `fb7dfe9`, 04 `c1b03af`, 05 `7ce33ff`, 03.3–03.4 `8926a30`, 03.2b `12e83be` (plan §9.12–9.16).
- Earlier v0.3.0: 03.2a `df08070`, 03.1 `f5394ea`, 03.0 `20db263`, 02.3 `1da9eac`, 02.2 `3269aa7`,
  02.1 `d7116db`, 01.1 `e93c21a`, 01.0 `f169565`, 00 `1ab1747`; base `577b5db` (FWN offset fix).
