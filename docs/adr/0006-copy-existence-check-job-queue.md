# ADR 0006: The stage check for "does copy exist" never saw job-queue COPY runs

- Status: **Accepted, 2026-09-17. Built the same day.**
- Date raised: 2026-09-17
- Affects: `scripts/board_serve.py` (`_facts`)

## Context

Kib: "No email sequences are being pushed to Apollo." Five real COPY jobs ran
today through `run_queue.py` (Arkansas State, Bellingham Technical, City
Colleges of Chicago, Los Angeles City, Lyon College) â€” real Campaign Builder
output, logged, `queue/done/`, exit 0. `/api/state` shows zero accounts at
SEQUENCE stage. Not one, ever, in this system's history: no SEQUENCE job has
ever appeared in `queue/done/` at all.

Cause: `next_step.stage_for()` only leaves the COPY stage once
`Facts.copy_files` is non-empty. That field is filled by
`next.copy_files_for()`, which does exactly one thing â€” checks whether the
institution's name appears inside a markdown file in `lists/`. That is the
*original* copy store, hand-maintained, one file per batch. The newest file
in `lists/` is dated 4 September. Every real COPY run since has gone through
the job queue instead, which writes to `queue/done/` and `queue/logs/` â€”
neither of which `copy_files_for()` has ever looked at.

So every account that gets copy the way this system actually produces it now
is, and always has been, permanently stuck reporting "no copy exists" back to
the stage computation. SEQUENCE can never become reachable for it. This is
not an Apollo problem, or a Reviewer problem, or anything downstream: the
pipeline cannot get far enough to try.

The fix already half-exists. ADR 0003 built exactly this lookup for
`run_sequence()`'s own use: `_newest_copy_job(account_key, directory)` in
`run_queue.py` finds the newest finished COPY job for an account from the
real queue. It was written for one caller and never connected to the other
place that needed the same answer.

## Decision

Extend `_facts()`'s copy-existence check to also ask `_newest_copy_job()`,
not just `copy_files_for()`. Either source finding something counts.

Deliberately existence-only, matching `copy_files_for()`'s own documented
standard ("evidence copy exists, not proof the copy is any good; the
Reviewer answers that"). A job-queue COPY run that only produced a
checkpoint stub, not real copy, still passes this check the same way a
`lists/` file naming an institution always has â€” and still refuses cleanly
at the step that actually reads content: `run_sequence()`'s own
`review_input.build_from_path()` call raises when there's nothing to
extract, and `_ships_verdict` still requires a real `ships` verdict on the
exact artifact hash before anything reaches Apollo (ADR 0003). Nothing about
that gate changes. This ADR only fixes what makes an account visible as
"ready for a SEQUENCE job to be queued" in the first place â€” it does not
loosen anything about whether that job is then allowed to write.

No new options considered: there is one correct fix, which is wiring an
already-built answer to a second place that needed it, not designing a new
one.

## Consequences

- `board_serve.py` imports `run_queue` alongside its existing `next as
  next_cli` import, same pattern.
- `_facts()`: `copy_files` is `copy_files_for()`'s result, or, if that's
  empty, a one-item list naming the newest finished job-queue COPY log â€”
  falling through to whichever source has something, not merging both.
- `lists/*.md` is not deprecated by this. Either source still counts;
  nothing currently writes there, but nothing stops someone from doing so by
  hand again, and `copy_files_for()` is unchanged.
- No schema change, no test file existed for `board.py`/`board_serve.py`'s
  gathering functions before this (matching ADR 0005's note on the same
  gap) â€” verified live against real data instead: after this change,
  `/api/state` correctly moves today's five COPY'd accounts to SEQUENCE.
