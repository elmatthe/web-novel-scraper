<!--
HOW TO USE THIS FILE (read me first, AI)
========================================
Decisions.md answers ONE question: "WHY is it built this way?"

It is the append-only architecture-decision record for this project. Where the other three
permanent docs cover what/when/now, this one captures the REASONING behind non-obvious choices
so a future session (or a bug hunt) does not "fix" something that was deliberate.

- Briefing.md  = what the project IS (features, architecture, stack).
- Changelog.md = what CHANGED over time (versioned release history).
- Handoff.md   = what's happening RIGHT NOW (live state, open bugs, sync log).
- Decisions.md = WHY a choice was made (this file).

WHEN TO ADD AN ENTRY:
- A library/tool was chosen over an obvious alternative (why this one, not that one).
- A structural or data-flow decision that isn't self-evident from the code.
- A deliberate trade-off (e.g. "slower but simpler", "no async because the user base is small").
- A constraint that shaped the design (no admin rights on CSPW-PC, 256 GB SSD, no CUDA, etc.).
- Anything you'd otherwise have to re-explain, or that an agent might undo without context.

WHEN NOT TO ADD AN ENTRY:
- Routine bug fixes (that's Changelog/Handoff).
- Obvious choices with no real alternative.
- In-flight work (that's Handoff).

RULES:
- APPEND-ONLY. Newest entry on top. Never rewrite history.
- If a decision is later reversed, DO NOT delete it — add a new entry that supersedes it and
  note "Supersedes #N" so the reasoning trail stays intact.
- Sign and date every entry (— Agent/Person, YYYY-MM-DD), like Handoff.md.
- Keep each entry short: the decision, the alternatives, and the reason. A few sentences each.
- Replace [bracketed] placeholders. Delete this comment block once the project is real.
-->

# [Project Name] — Decisions (ADR Log)

Append-only record of non-obvious design decisions and their reasoning. Newest on top.
See AI-WORKSPACE.md for how this fits alongside Briefing, Changelog, and Handoff.

---

<!--
TEMPLATE FOR A NEW ENTRY (copy this block above the previous entry, newest on top):

## [NNN] — [Short decision title] — YYYY-MM-DD — [Agent/Person]

**Status:** Accepted   <!-- Accepted | Superseded by #NNN | Deprecated -->
**Context:** [What situation or problem forced a decision? What constraints applied?]
**Decision:** [What was chosen.]
**Alternatives considered:** [What else was on the table, and why it lost.]
**Consequences:** [What this makes easier, and what it makes harder or rules out.]
-->

## 001 — [First decision title] — [YYYY-MM-DD] — [Agent/Person]

**Status:** Accepted
**Context:** [Why a decision was needed here.]
**Decision:** [What was chosen.]
**Alternatives considered:** [What else, and why not.]
**Consequences:** [Trade-offs and follow-on effects.]
