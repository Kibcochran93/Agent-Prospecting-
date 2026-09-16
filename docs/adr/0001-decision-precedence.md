# ADR 0001: Which record wins when the ledger and the decision store disagree

- Status: **Accepted, 2026-09-10.** Option B built the same day.
- Date raised: 2026-09-10
- Affects: `scripts/board_serve.py`, `scripts/next.py`, `src/seats_prospecting/next_step.py`, `src/seats_prospecting/decisions.py`

## Context

An account's outreach decision now exists in two places.

1. `record.outreach_decision` inside a Prospect Briefing envelope in `ledger-outbox/`,
   written by an agent, hash-guarded, and limited to `Pending owner review` or
   `Not required`.
2. A standing decision in `decisions/`, written by a person from the board, and
   able to say `Approved` or `Declined`.

These disagree today. The Saginaw Valley State briefing record says
`Pending owner review`. `decisions/20260909T182600Z-saginaw-valley-approved.json`
says `Approved`. Southern Virginia and Scranton are in the same state.

Nothing declares which one governs. The behaviour that exists is an accident of
branch order in `next_step.stage_for`, which reads the standing decision and
ignores the record. The visible symptom is that the dashboard reports zero
accounts waiting on an account owner while three briefings sit at
`Pending owner review` in the ledger.

This is the same shape as the `Created` property conflict found on 9 September,
where briefing rows relayed on the 8th used `verified_through` and rows relayed
on the 9th used the emitted date. A conflict nobody declared, discovered by
reading rather than by a failure.

## Options

### A. The decision store always wins, silently

What happens now. Simple, and matches the intuition that a person's later
judgement beats an agent's earlier note.

- Cheap: no code change, no new concept.
- The record's own value becomes decoration. Anyone reading a ledger record
  alone gets a stale answer with no hint that it is stale.
- Hides the owner-review step. See ADR 0002; the two are entangled.

### B. The decision store wins, and disagreement is shown

Same precedence, but wherever a decision is displayed the record's value is
shown alongside it when they differ, and `seats-status` reports the count of
disagreements.

- Precedence is written down and tested rather than emergent.
- The reader sees both facts and can tell which is which.
- Costs a display change in three places and one function.
- Does not fix the underlying conflation of two people's decisions.

### C. The record wins until a decision explicitly supersedes it

A decision record must name the outbox file and record hash it supersedes.
A decision with no such reference does not override anything.

- Strongest provenance. A decision is anchored to the evidence it was made on,
  so re-briefing an account invalidates a decision made against the old facts,
  which is arguably correct: an approval given on 2024 data is not an approval
  of 2026 data.
- Most work: the board must resolve the current record for an account before it
  can write a decision, and the six seeded decisions carry no such reference,
  so they need backfilling or grandfathering.
- Introduces a failure mode where an account cannot be approved because no
  record is resolvable.

## Decision

**Option B.** Kib's decision governs, and where it contradicts the value the
agent wrote into the ledger record, both are shown. Accepted and built
2026-09-10.

Implemented as `decisions.resolve()`, one function, with the rule in its
docstring and eleven tests. The dashboard grew a section, "Your decision
overrode the record", which on the day of the decision listed eight accounts:
Creighton, Lyon, Saginaw Valley State, Southern Virginia, Tulsa, University of
Central Arkansas, Scranton and Wiley. All eight had a record reading
`Pending owner review` under a Kib approval. That was already true before the
change; it was just invisible.

Option C stays open. The trigger for revisiting it is the first re-briefing of
an account that already carries an approval, at which point an approval
anchored to no particular evidence becomes actively misleading rather than
merely unprovenanced.

## Recommendation as written when this was raised

**B now, C when re-verification starts to bite.** B removes the current
contradiction for the cost of one function and three display changes. C is the
right end state, and the argument for it gets stronger the moment an account is
re-briefed: two of the three accounts approved from the export are already past
the 30 day re-verification rule by more than a year, so an approval anchored to
nothing is already a live problem rather than a theoretical one.

## Consequences if B is accepted

- A `standing_decision(record, decisions)` function, one place, with tests.
- The dashboard and `seats-next` show the record's value when it differs.
- `seats-status` grows a disagreement count, consistent with its existing habit
  of reporting rather than resolving.
- The six seeded decisions keep working unchanged.
