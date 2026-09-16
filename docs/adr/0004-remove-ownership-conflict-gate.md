# ADR 0004: Ownership stops being a blocking fact on dispatch

- Status: **Accepted, 2026-09-15.** Built the same day.
- Date raised: 2026-09-15
- Affects: `src/seats_prospecting/schemas.py` (`ContextVerdict`),
  `src/seats_prospecting/handoff_wiring.py` (`BLOCKING`),
  `src/seats_prospecting/tools/apollo.py` (`apollo_contact_status`),
  `scripts/live_run.py` (`--override` help text),
  `tests/test_context_agent.py`, `tests/test_dispatch_gate.py`

## Context

`ContextVerdict.status` has carried four values since 4 September:
`net_new`, `known_inactive`, `known_active`, `conflict`. `conflict` meant
either a colleague's account or an open deal, and it sat in
`handoff_wiring.BLOCKING` alongside `known_active`, which meant a Director
dispatch refused outright — no briefing, no spend — the moment the Account
Context check found a HubSpot owner who wasn't Kib.

That is a stricter rule than anything else in the system holds today.
`LedgerRecord.outreach_decision` stopped treating ownership as blocking on
3 September (Kib's call then: he sends from several mailboxes, so an agent
records the owner but never writes an approval or decline). ADR 0002 built
`ASK_OWNER` as a notify-not-gate stage for the board. `missed.py` says
outright: "ownership is a notification and not a gate." The `conflict`
status on `ContextVerdict` was the one place left where ownership alone,
with no activity behind it, stopped a run before it started.

That is what surfaced running the new "USA Community College Contacts -
True" list on 15 September: 16 of 84 institutions carried Cal O'Donovan's
owner ID with zero other signal, and every one of them would have hard-
stopped a briefing regardless of whether anything was actually happening on
the account. Kib's framing: this system exists to find accounts falling
through the cracks of ownership, and a hard stop keyed on ownership alone
works against that on every dormant Cal or Miguel account, not just the
live ones.

## Decision

**Ownership is no longer a blocking fact anywhere in the dispatch path.**
`conflict` is removed as a status. Three remain: `net_new`,
`known_inactive`, `known_active`. `known_active` now covers what `conflict`
used to split off — a live sequence, a recent touch, or a colleague's
mailbox already sending — because activity is the actual signal, whoever's
name is on the account. An account with a colleague's name on it and no
activity is `net_new` and proceeds. `owner` stays on the verdict, recorded
for context, and decides nothing.

This is scoped to the Director's dispatch gate specifically — the check
that stopped Arkansas State–Mountain Home on 11 September when it hit Cal's
ownership with no activity behind it. It does **not** touch:

- `ASK_OWNER` in `next_step.py`, or the `owner_review` / `kib_decision`
  split from ADR 0002. That is a board-level notify step, already
  non-blocking, already separate from this gate. Whether Kib wants that
  gone too is a separate call, not decided here.
- `LedgerRecord.outreach_decision`, already non-blocking on ownership since
  3 September.

If it turns out the ASK_OWNER notify step should go too, that is ADR 0005,
not a quiet edit to this one.

## What was built

- `ContextVerdict.status`: `Literal["net_new", "known_inactive", "known_active"]`.
  `conflict` removed. Docstring rewritten to say why, and to say `owner` is
  context, not a decision.
- `handoff_wiring.BLOCKING`: `("known_active",)`. `conflict` removed.
- `apollo_contact_status`: a sequence sending from a colleague's mailbox is
  reported as `known_active`, same as one from Kib's own; the mailbox
  address is still surfaced, but it no longer reads as a distinct
  "conflict" state.
- `live_run.py --override` help text: no longer mentions `conflict`.
- Tests updated to match: the `conflict`-specific cases in
  `test_context_agent.py` and `test_dispatch_gate.py` are gone or rewritten
  against `known_active`; the parametrized blocking-status tests now run
  against one status instead of two. 534 passing (down from 536: the two
  removed were the `conflict` legs of the parametrized dispatch-refusal
  tests, not a coverage loss).

## What this means for the 15 September community-college batch

The 16 Cal-owned institutions pre-filtered out of the first 10-account run
were excluded by a manual check against Apollo's `crm_owner_id` field, done
in chat, not by this gate — this ADR postdates that filtering. Going
forward, those 16 would proceed through Account Context as `net_new` unless
something in HubSpot shows actual activity on them.
