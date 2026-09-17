# ADR 0005: A fresh contact at an already-decided account still needs a decision

- Status: **Accepted, 2026-09-17. Option B built the same day.**
- Date raised: 2026-09-17
- Affects: `src/seats_prospecting/decisions.py`, `src/seats_prospecting/next_step.py`,
  `scripts/board.py` (`collect_records`), `scripts/board_serve.py` (`_state_json`, `/decide`),
  `prospecting_control_panel_local.html`

## Context

Kib ran two fresh Prospect Briefings today, on two named contacts (Thilla
Sivakumaran, Amanda Nickerson) at Arkansas State University. Neither showed up
anywhere in the dashboard to review. Both records are sitting in
`ledger-outbox/`, correctly filled in, institution and all.

The cause: every decision in this system is keyed by `account_key` alone.
`decisions.current()` returns one standing decision per account, and
`next_step.stage_for()` asks one question â€” has *this account* been decided â€”
not "has this piece of research been decided." Arkansas State was already
Approved, with a COPY job already run, so its computed stage has moved well
past `UNDECIDED`. A brand-new briefing for a brand-new contact at that same
account has nowhere to attach: it is neither blocking nor visible. It is not
dropped by any bug â€” the record and account_key are both fine on disk â€” it is
invisible by design, because the design has one decision slot per account and
this is the second contact.

This is the same shape of gap ADR 0003 closed for SEQUENCE: a real thing (a
COPY artifact, there) had no way to prove which specific piece of content had
been dealt with, only which *account* had. The fix there was content-addressed
matching, not a bigger account-level flag. The same fix applies here.

## Options

### A. One more account-level flag ("has new contacts") (rejected)

Add a boolean or count to the account row â€” "2 new contacts since last
decision" â€” and let Kib re-open the account to sort out which is which by
hand.

- Cheapest to build.
- Solves visibility, not review. Kib still cannot approve or decline Thilla
  specifically without the system telling him which briefing is hers â€” the
  exact problem today, one layer up. Rejected for not actually closing the
  gap, only naming it.

### B. Content-addressed per-contact decisions (recommended)

Give `kib_decision` records an optional `artifact_sha256` â€” the hash of the
`record` field of the specific `prospect_briefing` envelope being decided,
computed the same way `digest.py` already hashes intent records
(`sha256(json.dumps(record, sort_keys=True, separators=(",",":")))`). A
decision with no hash means what it always has â€” the account-level Approved
or Declined that governs COPY/SEQUENCE. A decision *with* a hash governs only
that one artifact.

`board.collect_records` computes this hash for every `prospect_briefing`
entry it attaches to an account. The dashboard can then ask, per account: of
this account's briefing records, which artifact_sha256 values have never been
decided, regardless of what the account's own stage is? Those surface as
their own reviewable rows â€” independent of, and additive to, the account-level
Approved/Declined that already governs automation.

- Reuses a pattern already trusted twice in this codebase (ADR 0003;
  `digest.py`'s own record verification), rather than inventing a third
  scheme.
- Each contact gets a real Approve/Decline, not just a flag. Approving Thilla
  and ignoring Amanda is expressible; today it is not.
- Cost: touches `decisions.record()`'s signature (new optional kwarg,
  backward compatible â€” every existing call keeps working unchanged),
  `board.py`'s entry-building, and needs a second surface in the dashboard,
  since the existing Pending-review table is one row per account and this is
  one row per (account, artifact). Kept as an additive table â€” "New contacts
  awaiting review" â€” rather than merging two different row shapes into one
  table.
- Every `prospect_briefing` written before this ships has no decision
  matching its hash, by definition, because the hash did not exist to record
  against. Same handling as ADR 0002's and 0003's pre-existing records: they
  read as "not yet decided," which for a briefing nobody has looked at is the
  literally correct answer, not a regression.

### C. Key decisions by contact name instead of a content hash (rejected)

Use `(account_key, person_name)` as the matching key instead of a hash.

- Cheaper than hashing, and human-readable in the decisions/ files.
- Wrong tool: a name collision (two "J. Smith"s) or a re-run that changes
  what was actually found about the same person would either merge two
  different decisions or silently treat stale research as already decided.
  ADR 0003 rejected exactly this shape of match (name/recency) for SEQUENCE
  for the same reason. A hash proves the content; a name only labels it.

## Recommendation

**B.** Smallest change that makes a specific piece of research provably
decided or not, and it is additive: nothing about account-level
Approved/Declined, COPY, or SEQUENCE changes. An account can be Approved and
mid-SEQUENCE while still owing a fresh decision on a brand-new contact â€”
that is the whole point, not a side effect to work around.

## Consequences

- `decisions.record()` gains an optional `artifact_sha256: str = ""` keyword
  argument. Blank by default, so every existing caller is unaffected.
- New `decisions.decided_artifact_hashes(account_key, kind=KIB_DECISION)` â€”
  every hash that has ever been decided (Approved or Declined) for an
  account, as a set. Unlike `current()`, there is no "latest wins": each
  hash either has a decision or it doesn't, and a second decision on the same
  hash (a correction) simply both count as "decided."
- `board.collect_records` computes `artifact_sha256` for `prospect_briefing`
  entries only â€” not `account_context` or `review_verdict`, which already
  have their own, differently-scoped hash under ADR 0003 and must not be
  confused with this one.
- New `next_step.PendingContact` and `next_step.undecided_briefings()` â€” a
  small, pure, additive function alongside `stage_for()`, not a change to it.
  `stage_for()`'s account-level stage is untouched.
- `board_serve.py`'s `/api/state` gains `new_contacts`, and `/decide` accepts
  an optional `artifact_sha256` to scope a decision to one contact instead of
  the whole account.
- The dashboard gains a "New contacts awaiting review" panel, separate from
  the existing Pending-review table, with its own Approve/Decline per row.
