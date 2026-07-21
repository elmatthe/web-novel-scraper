# webnovel-scraper — Decisions (ADR Log)

Append-only record of non-obvious design decisions and their reasoning. Newest on top.
See AI-WORKSPACE.md for how this fits alongside Briefing, Changelog, and Handoff.

---

## 005 — Bounded-window TOC hints threaded via signature introspection, not the adapter contract — 2026-07-20 — Claude (Fable 5)

**Status:** Accepted
**Context:** Phase 2 built `FreeWebNovelAdapter.build_chapter_index`'s optional
`start_hint`/`end_hint` (binary search + padded walk) but deliberately left both
`pipeline.py` call sites unchanged, flagging it as a deviation: without hints, an
offset novel's index build walks the *entire* discovered slug range from 1,
which for a ~4400-chapter novel like Supreme Magus means ~4400 extra fetches
once per output folder. Wiring `job.start`/`job.end` through was the obvious
next step, but `build_chapter_index(self, spec)` is the `BaseAdapter` ABC
contract, and roughly a dozen test fakes across the suite (plus
`WebNovelDynamicAdapter` and the disabled stubs) implement exactly that plain
signature — no `**kwargs`, no `start_hint`/`end_hint`.
**Decision:** Added a small pipeline-level helper, `_build_chapter_index(adapter,
spec, *, fast_path=None, start_hint=None, end_hint=None)`, that uses
`inspect.signature(adapter.build_chapter_index).parameters` to decide which
kwargs the adapter's own method actually declares, and only passes those. Both
`pipeline.py` call sites (the rescue-conductor's `_PrimaryEngine.build_toc` via
`_bootstrap_toc`, and the legacy `_drive` path) now call through this helper
with `start_hint=job.start, end_hint=_end_hint_for(job)`. A large/unbounded
`job.end` (the GUI's blank-end-means-all sentinel, `app.ALL_CHAPTERS = 10**9`)
is folded to `None` by `_end_hint_for` (threshold `10**6`, chosen to sit
comfortably below the real sentinel and far above any real chapter count)
rather than being passed through literally — passing `10**9` as a search target
would still resolve correctly (the binary search degrades gracefully to
"return the last slug"), but only after wasting probe fetches converging on an
unreachable target.
**Alternatives considered:** (a) Add `start_hint`/`end_hint` to the
`BaseAdapter` ABC and every adapter/fake — rejected: touches non-FWN code the
fix drop explicitly scoped out, and is pure churn for adapters that will never
use the hints (WND's TOC already agrees with display numbers; the stubs are
unimplemented). (b) `isinstance(adapter, FreeWebNovelAdapter)` check in
`pipeline.py` — rejected: forces `pipeline.py` (which currently has zero
imports from `adapters/`, operating entirely through injected
factories/fakes) to import a concrete adapter class, a real layering
regression. (c) Broad `try/except TypeError` around the call — rejected: would
silently swallow a genuine `TypeError` raised *inside* a real
`build_chapter_index` call (e.g. a bad URL format bug), masking real bugs as
"unsupported kwarg."
**Consequences:** Every existing adapter and test fake is byte-for-byte
unaffected (proven: the full 285-test Phase 1/2 suite passed unchanged after
this wiring, plus 2 new tests). Only `FreeWebNovelAdapter` — detected by its own
declared parameters, not by name or type — receives the hints. A live run on an
offset novel now resolves near the requested range instead of scanning from
slug 1; proven by `test_pipeline_run_near_high_chapter_stays_bounded_on_offset_novel`
(a 500-slug synthetic novel, chapter 116 requested, fetch count asserted < 60
instead of the ~120+ an unbounded from-slug-1 walk would need). If a future
adapter also wants bounded hints, it just declares the same two parameter
names — no registry/ABC change required, by construction.

---

## 004 — Bounded-window walk defaults to the whole novel when no hint is given — 2026-07-19 — Claude (Fable 5)

**Status:** Accepted (see #005 for the follow-up that actually supplies hints from the pipeline)
**Context:** `build_chapter_index`'s offset-resolution walk needs to know how
much of the novel to reconcile. The drop's own wording — "~12 probes over a
4400-slug space" — confirms a binary search (logarithmic cost) is meant to
anchor the walk, not a full linear scan; but at Phase 2 time no caller yet
supplied `start_hint`/`end_hint` (that wiring was deliberately deferred to
close cleanly — see #005).
**Decision:** When both hints are `None`, the walk covers the whole discovered
slug range (`1..count`) — correct, but potentially the most expensive path.
When either hint is given, binary search anchors near it and the walk is
padded ±8 slugs (`_WALK_PADDING`) around the resolved boundary, absorbing a
local run of insert pages (Supreme Magus has a run of five between slugs 629
and 635) without missing the true boundary by a couple of slugs.
**Alternatives considered:** Always require a hint (raise if omitted) —
rejected: would break direct/test callers of `build_chapter_index` that
reasonably want the full novel (e.g. building a complete catalog view), and
adds a mandatory parameter to a method whose ABC contract has none.
**Consequences:** A direct call with no hints on a genuinely offset novel is
slow (by design, but bounded and one-time per output folder, since
`chapter_index.json` schema v2 persists the resolved result). #005 means real
pipeline runs always supply hints in practice, so this "no hint → full walk"
branch is now mainly exercised by direct/test callers, not live runs.

---

## 003 — Chapter identity/index means the site's DISPLAYED number; slug is a separate field — 2026-07-19 — Claude (Fable 5)

**Status:** Accepted
**Context:** FreeWebNovel's URL slugs (`.../chapter-N`) count every page —
real chapters plus joke-chapter/glossary extras and fractional ".x"
side-chapters — while the page header displays the author's own numbering. On
most novels the two agree; on Supreme Magus they drift apart by a
monotone-non-decreasing, still-growing offset (measured 2 → 15 across the
novel — never constant, ruling out a simple per-novel offset field). Before
this fix, `meta.index` **was** the slug number, which entered filenames,
`chapter_index.json`, the range clamp, and PDF headings — so a user typing a
displayed chapter number (what they read on the site) fetched the wrong page.
**Decision:** `ChapterMeta.index` / `ChapterContent.index` now mean the site's
DISPLAYED chapter number — what a user types and reads — everywhere
downstream (filenames, resume matching, the range clamp, PDF headings,
`RunReport`). The URL slug (what the adapter actually fetches) moved to a new
`ChapterMeta.slug: int | None` field, used only inside the adapter to build
fetch URLs. `index` is `int | float`: fractional side-chapters (e.g. "Chapter
627.2") are kept as their own addressable label — not merged into, or
discarded in favor of, the nearest integer — sorting naturally between their
integer neighbours since Python's numeric ordering already does the right
thing (`627 < 627.2 < 628`).
**Alternatives considered:** (a) Resolve the slug↔displayed mapping once and
bake it into `range()` at index-build time for the whole novel up front —
close to what was chosen, but doesn't handle fractional inserts discovered
only by visiting the page, since they're not knowable from a slug count alone.
(b) A per-novel constant offset field on `SiteSpec`/catalog — rejected outright
by measurement (task 2 of the investigation): the offset is not constant
within Supreme Magus, so a fixed field would silently drift wrong as the
novel accumulates more inserts. (c) Post-fetch reconciliation only (fetch by
slug, relabel from the header after the fact) — rejected as insufficient
alone: it fixes the *label* but not the *address* — a user typing "4207"
would still fetch slug 4207 (the wrong page) and only discover the mislabel
after the fact; the chosen design fetches the *right* page in the first place.
**Consequences:** Every identity novel (slug == displayed for the whole book —
confirmed for Shadow Slave, Reverend Insanity, Renegade Immortal) is
byte-identical to pre-fix output by construction (see #002). Any future
non-FWN adapter that also drifts slug-vs-displayed can reuse the same
`index`/`slug` split without a model change.

---

## 002 — Identity vs. offset is auto-detected for free from the index page, not a dedicated probe — 2026-07-19 — Claude (Fable 5)

**Status:** Accepted
**Context:** The user's decision (#3, "apply to all FWN novels" via
auto-detected probing) requires a hard, testable invariant: an identity novel
(the common case) must cost **zero extra fetches** and produce byte-identical
output to before the fix — not "probably free," provably free, so a
regression can't silently creep back in as fetch count. A naive design (fetch
one extra chapter page up front to check `slug == displayed`) would violate
this: even one wasted fetch on every identity novel's every run is a real,
measurable regression, and several existing offline tests construct a
`FakeRM` registered with *only* the index-page URL (no chapter URLs at all) —
any extra fetch would raise `KeyError` and fail loudly, which is exactly the
tripwire this decision is designed to satisfy rather than dodge.
**Decision:** `build_chapter_index` already fetches the index/TOC page (to
discover the highest slug via `_discover_max_chapter`). That same page's
rendered anchors carry BOTH the URL slug (from `href`) and the site's own
"Chapter N: Title" link text — so a second pass over the same, already-fetched
soup harvests `(slug, displayed)` pairs for free. If every harvested pair
agrees, the novel is identity: return the exact pre-fix generated list, no
further network activity. If any pair disagrees, it's confirmed offset with
no extra fetch either. Only when the index page's anchor text is completely
uninformative (harvests zero pairs — true today only in synthetic tests, not
on any real captured FreeWebNovel TOC page) does the code fall back to one
genuine probe fetch to decide identity vs. offset.
**Alternatives considered:** A dedicated probe fetch on every `build_chapter_index`
call, unconditionally — simpler code, but fails the hard zero-extra-fetch
requirement and the existing `FakeRM`-with-no-chapter-URLs tests outright.
**Consequences:** `test_identity_novel_build_chapter_index_makes_zero_extra_fetches`
proves this directly (asserts `rm.fetched == [index_url]`, nothing else). The
tradeoff is that identity detection depends on the index page's anchors being
informative — true for every FreeWebNovel TOC page captured/observed so far,
but if a future site redesign strips anchor text, the code degrades to the
one-probe fallback rather than silently mis-detecting (never assumes identity
without evidence).

---

## 001 — chapter_index.json schema v2 rebuilds ANY older payload, superseding the 0.2.1 legacy-list-trust allowance — 2026-07-19 — Claude (Fable 5)

**Status:** Accepted (supersedes the 0.2.1 BH-1 "legacy lists remain trusted"
behaviour for the specific case of a pre-v2 index)
**Context:** 0.2.1 added `chapter_index.json` identity (novel/site) so reusing
an output folder for a *different* target rebuilds instead of trusting a
stale TOC — but a pre-identity bare-list file, or a v1 dict with a *matching*
identity, was still trusted and resumed as-is. This fix changes what
`ChapterMeta.index` *means* (site-displayed number, not URL slug — see #003)
and adds the `slug` column. An old index file's rows are slug-space with no
slug column at all — trusting one on an offset novel would silently resume
against the wrong chapter numbers, exactly the bug this fix removes.
**Decision:** Bumped `INDEX_SCHEMA_VERSION` 1 → 2. `_load_index` now rebuilds
(with a logged `NOTE:`, one TOC re-fetch) both the pre-identity bare list AND
any v1-tagged dict, even when its identity matches the current run — schema
staleness is checked independently of identity staleness.
**Alternatives considered:** Leave old indexes trusted and let `fetch_chapter`
silently re-derive correct numbers on the next run — rejected: the whole
point of the persisted index is to avoid re-deriving it, and a resumed run
would keep writing wrongly-addressed content in the meantime.
**Consequences:** Any existing output folder from before this fix pays exactly
one TOC re-fetch on its next resume (logged, not silent), then resumes
correctly from then on. `test_pre_identity_list_only_index_now_rebuilds` and
`test_v1_schema_dict_index_rebuilds_even_with_matching_identity` pin this.
