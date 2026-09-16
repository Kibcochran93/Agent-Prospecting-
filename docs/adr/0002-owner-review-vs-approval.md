# ADR 0002: One field is carrying two people's decisions

- Status: **Accepted, 2026-09-10.** Option B built the same day. Option C rejected, not deferred.
- Date raised: 2026-09-10
- Affects: `src/seats_prospecting/decisions.py`, `scripts/board_serve.py`, `src/seats_prospecting/schemas.py`, the Notion export

## Context

Two different people decide two different things about a colleague-owned account.

1. The account owner, Cal O'Donovan or Miguel Pescador, decides whether
   prospecting into their account is acceptable at all.
2. Kib decides whether to proceed with outreach.

Both are recorded in one field. In Notion it was the `Outreach decision`
property; locally it is `decision` in a `decisions/` record. Whichever was
written last is the only one visible.

The design intent is on record in the briefing pages relayed on 9 September:
"This account is owned by Cal O'Donovan. Outreach does not proceed until Kib
records a yes or no on this record." That sentence describes a sequence of two
approvals, and the data model holds one.

Two consequences are already live.

- Six accounts were seeded as `Approved` from the Notion export on 9 September.
  Nothing records whether Cal or Miguel ever reviewed any of them. The owner
  review step has been bypassed for all six, invisibly.
- The dashboard reports zero accounts waiting on an account owner while three
  briefings sit at `Pending owner review`, because a Kib-level approval
  overwrote the owner-level state. See ADR 0001; the precedence question only
  looks confusing because two facts are sharing one slot.

Miguel and Agustin have no Notion access and no account on the local board, so
there is currently no surface on which an owner could record a review even if
the model allowed it. That is a separate problem, but it shapes the choice:
whatever is built must tolerate an owner review that arrives verbally and is
recorded by Kib on their behalf.

## Options

### A. Leave it as one field

- No work.
- The distinction between "the owner cleared it" and "Kib cleared it" is
  permanently unrecoverable, including for the six already seeded.
- If an owner later disputes an approach, there is no record either way.

### B. Add a `kind` to the decision record

`kind` is `owner_review` or `kib_decision`. Both live in `decisions/`, both
append-only, and `current()` returns the latest of each.

- Small change: one field, one branch in `current()`, no new store.
- An account's state becomes readable as a pair, which is what the briefing
  text already describes.
- `recorded_by` needs to be distinguishable from `decided_by`, so that "Miguel
  said yes on a call, Kib wrote it down" is honest rather than looking like
  Miguel used the board.
- The six seeded decisions have no `kind`. They would default to
  `kib_decision`, which is accurate: they came from a property only Kib could
  write.

### C. A separate owner-review store and gate (REJECTED)

`owner-reviews/`, and a decision is refused unless a matching owner review
exists for a colleague-owned account.

- Strongest guarantee: the sequence cannot be skipped.
- Blocks the six seeded accounts immediately, and blocks any account whose
  owner review happened verbally and was never written down, which is all of
  them today.
- Adds a store whose only job is to gate another store.

## Decision

**Option B, and option C is rejected rather than deferred.** Accepted and built
2026-09-10.

Kib confirmed on 2026-09-10 that Notion is licensed to him alone and that
Miguel and Agustin are not expected to be licensed. That kills option C
outright. A gate requiring an owner to record their own review would wait on a
click from someone who has no account and never will, so it could only ever
block Kib. The earlier framing of C as "the right control once an owner has a
way to record a review" was wrong: that day is not coming.

The consequence to accept deliberately: **every owner review will be
secondhand, permanently.** Cal or Miguel tell Kib on Teams or on a call, and Kib
records it. That is a real fact and worth holding, but the record must never
imply the owner used a system they do not have. So a decision carries
`decided_by` and `recorded_by` separately, and any record where those differ is
marked `secondhand`.

What was built:

- `kind` on every decision record, `owner_review` or `kib_decision`. Records
  written before 10 September default to `kib_decision`, which is accurate:
  they came from a Notion property only Kib could write.
- `decisions.owner_reviews()` alongside `decisions.current()`.
- A new stage, `ASK_OWNER`, for a colleague-owned account with no recorded
  answer. It is not automatable and says so: nothing can resolve it but Kib
  asking. The dashboard counter reads "owners to ask", which is a task for Kib
  rather than a queue someone else is sitting in.
- An owner review recorded from the board requires a named owner and asks how
  they said it, storing that as the source.

Six accounts seeded from the Notion export on 9 September are
`kib_decision` with no owner review, so those accounts now show that no owner
has been recorded as reviewing them. That reads as a regression on the
dashboard and is not one.

## Recommendation as written when this was raised

**B, with the gate deferred.** The pair of facts is what the system already
describes in prose, and `kind` records it for the cost of one field. The gate in
C is the right control once an owner has a way to record a review, but
introducing it today would block every account in flight and the only way
through would be Kib recording owner reviews on their colleagues' behalf, which
is the exact ambiguity the ADR is trying to remove.

## Consequences if B is accepted

- `decisions.record` takes `kind`, defaulting to `kib_decision` so nothing
  breaks, and `recorded_by` separate from `decided_by`.
- `current()` returns both kinds; `next_step` reads the owner review to
  decide whether an account is waiting on a colleague or on Kib.
- The dashboard's "waiting on an account owner" section becomes truthful.
- The six seeded decisions are `kib_decision` with no owner review, so those
  accounts will correctly show that no owner has been recorded as reviewing
  them. That will look like a regression on the dashboard. It is not.
