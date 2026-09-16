# ADR 0003: What a queued SEQUENCE job is allowed to trust

- Status: **Accepted, 2026-09-11. Option B built the same day.**
- Date raised: 2026-09-11
- Affects: `scripts/run_queue.py` (`run_sequence`), `src/seats_prospecting/tools/notion_ledger.py`
  (`write_review_verdict`), `scripts/live_run.py` (the `reviewer` branch), `src/seats_prospecting/schemas.py`
  (`ReviewVerdict`)

## Context

`next_step.AUTOMATABLE` already includes SEQUENCE alongside COPY, and
`job_queue.enqueue` will happily accept one. `run_sequence()` in
`scripts/run_queue.py` refuses every job handed to it:

> "The SEQUENCE stage is not wired. seats-drafts needs extracted artifacts in
> touch order, and which artifacts a queued job should use has not been
> decided."

That refusal is correct, and the reason is more specific than the docstring
says. COPY's input is built fresh every run, straight from the ledger record
(`copy_input()`). SEQUENCE's input has to be something else: reviewed,
gated copy, not whatever the Campaign Builder produced. Getting from a COPY
run to something safe to hand `seats-drafts write` has three links, and today
all three are manual and none of them are keyed by account:

1. **Extraction.** `review_input.build_from_path(log)` turns a Campaign
   Builder run log into a clean artifact — `## Copy batch` heading, messages
   only, the differentiation check stripped out. This already exists, is
   already tested (it's what produced every `reviewer-in-N.txt` in
   `smoke-test/batch-1/`), and no queued path calls it.
2. **The gate.** `reviewer_gate` plus a `live_run.py reviewer` run produces a
   `ReviewVerdict` (`status: ships` or `rewrite`), and `write_review_verdict`
   relays it to `ledger-outbox/` as `kind: review_verdict`. This part is
   wired — it's the Reviewer column on the board today.
3. **Matching a verdict back to an account and an artifact.** This is the
   part that isn't wired, and it's the actual reason SEQUENCE is stubbed.

On point 3: `ReviewVerdict` carries `status`, `batch_kind`, and `findings`.
No account field. `board.py` shows a Reviewer cell per account anyway, by
running `named_institutions()` over the record's title text and guessing
which account(s) it's about — the same string-matching that its own comment
admits misfired once already, on a leading "The" in "The Scranton briefing."
That's fine for a cell a person glances at. It is not fine for a write path:
a queued job deciding whether to hand a person's name and inbox to Apollo
needs to know it's holding the artifact a `ships` verdict actually covers,
not "a verdict mentioning this institution exists somewhere in the outbox."

The gap has a second edge. Even a correctly-matched verdict can go stale: if
a second COPY run happens for the same account after the verdict was
recorded — a rewrite, a re-run, anything — "most recent ships verdict for
this account" and "the text that most recent verdict actually read" can
silently diverge. Nothing today would notice.

## Options

### A. Match by account name and recency

`run_sequence()` finds the account's newest `review_verdict` record (same
`named_institutions()` matching board.py already uses) with `status: ships`,
checks it's newer than the account's most recent COPY job, and if so
extracts that COPY job's log and writes it.

- No schema change. Ships fastest.
- "Newer than" is a timing assumption, not a content guarantee — the same
  shape of bug as the reveal-cap race on 4 September, where six concurrent
  reads each saw a count before any of them had incremented it. A COPY run
  queued between the verdict and this check would make the newer, *unreviewed*
  log the one that looks current.
- Inherits `named_institutions()`'s known fuzziness on a path that now writes
  to Apollo instead of rendering a table cell.

### B. Content-addressed match (recommended)

Give `ReviewVerdict` relay records an explicit `account_key` and an
`artifact_sha256` — the hash of the extracted artifact text the Reviewer
actually read, not the hash already taken of the verdict's own JSON body.
`run_sequence()` then:

1. Finds the account's newest finished COPY job, reads its log.
2. Runs `review_input.build_from_path` on it — the same extraction a human
   would run by hand.
3. Hashes the result and looks for a relayed `review_verdict` with that exact
   `account_key` and matching `artifact_sha256`, `status: ships`.
4. Match found: `drafts.plan_drafts` + `drafts.write_drafts`, same pattern as
   `run_copy`. No match: refuse and say which — no ships verdict for this
   account, or one exists but for different text (something changed since it
   shipped).

This is the same integrity-check habit already in use elsewhere in this
system (append-only `decisions/` records, `record_sha256` on the verdict
envelope, SHA-256 checks before ledger transcription). It extends a pattern
that's already trusted rather than inventing a new one.

- Closes the actual failure mode: a queued job can prove the text it's about
  to hand Apollo is the text that shipped, not "something shipped recently
  for an account with a similar name."
- `account_key` on `review_verdict` also fixes board.py's fuzzy matching for
  this record kind going forward, which is a second win, not required to
  land this ADR.
- Cost: touches `write_review_verdict()`'s signature, and `live_run.py`'s
  `reviewer` branch needs to pass an account key in. Cleanest way to get one:
  require `--account <key>` on a `reviewer` run. A reviewer run is already
  scoped to one account's batch; Kib already knows which account when he
  starts it. Inferring it from the text is exactly the fuzziness this ADR
  exists to remove.

### C. A companion store, `review-artifacts/` (rejected)

Write every extracted artifact to disk when `review_input` runs, keyed by
account and timestamp, and have the verdict reference the filename.

- Rejected for the same reason ADR 0002 rejected a second owner-review store:
  it's a new store whose only job is making another record resolvable, when
  the fix belongs on the record that already exists. `review_verdict` should
  just say what it verdicted.

## Recommendation

**B.** It's the smallest change that makes SEQUENCE safe to run unattended,
and `review_verdict` missing an account key is a gap that predates this ADR —
board.py's guess-by-name matching was always a display convenience, never
something a write path should have inherited by default.

Scope explicitly excluded from this ADR: `extend_drafts` (touches 2 through
4, the `cadence` batch kind). SEQUENCE as defined in `next_step.py` is the
first `seats-drafts write` only — creating the sequence, paused, with
whatever's in the first-touch artifact. When and how later touches get
reviewed and appended is a separate question with its own artifact-matching
shape; it doesn't block this one.

## Consequences if B is accepted

- `write_review_verdict(verdict, *, account_key, artifact_sha256)` — a
  required keyword change, so every caller has to supply both rather than
  the field silently defaulting to empty.
- `live_run.py`'s `reviewer` branch gains `--account`, computes the hash of
  the text it's about to send to the agent (the extracted artifact, before
  any preamble is prepended), and passes both through.
- `run_sequence()` in `run_queue.py` goes from an unconditional refusal to:
  find the newest COPY job for the account, extract, hash, match against
  `review_verdict` records, write or refuse with a specific reason.
- Every `review_verdict` written before this change has no `account_key` and
  no `artifact_sha256`. Same handling as ADR 0002's six pre-existing
  decisions: they don't retroactively gain the field, and a queued SEQUENCE
  job correctly finds no matching verdict for any account whose last review
  happened before this ships. That reads as "nothing's approved yet" on
  accounts that were actually reviewed under the old, unkeyed scheme — not a
  regression, just an honest reflection of what the old records don't say.

## What was built, 2026-09-11

- `notion_ledger.write_review_verdict` takes `account_key` and
  `artifact_sha256` as required keyword-only arguments and raises `ValueError`
  on either being blank, rather than silently writing an unmatchable record.
  Both land on the envelope itself (alongside `kind`, `written_at`), not
  inside `record` — `ReviewVerdict` still validates with `extra="forbid"` and
  stays exactly the agent's typed output.
- `live_run.py` gained `--account`, required on a `reviewer` run unless
  `--no-record` is passed (checked in `main()`, before the run starts, so an
  expensive reviewer call is never spent only to fail recording afterward).
  It hashes the exact text handed to the agent, right before the run, and
  passes both through to `write_review_verdict`.
- `run_queue.run_sequence()` is no longer an unconditional refusal:
  `_newest_copy_job` finds the account's most recent finished COPY job,
  `review_input.build_from_path` extracts the artifact from its log, the
  result is hashed, and `_ships_verdict` checks both pending and relayed
  outbox envelopes for a `ships` verdict naming this exact account and hash.
  Three distinct refusal messages depending on what's missing (no verdict at
  all, a verdict but not `ships`, a `ships` verdict but for different text) —
  see `_ships_verdict`'s docstring. A match runs `drafts.plan_drafts` and
  `drafts.write_drafts`, same code path `seats-drafts` uses by hand.
- Tests: `tests/test_review_verdict.py` updated for the new required
  arguments, plus new tests asserting the fields round-trip through the
  envelope and that blank values are refused. `tests/test_sequence_stage.py`
  is new — covers job lookup (newest finished COPY job wins, a running or
  queued one doesn't count), every refusal path in `_ships_verdict`, the
  relayed-verdict case, dry-run (matches but writes nothing), the happy path,
  and a failed Apollo write being reported rather than swallowed. 531 tests
  pass suite-wide.
- Not built, per the excluded scope above: `extend_drafts` / touches 2-4.
  `run_sequence` still only ever performs the first write.
