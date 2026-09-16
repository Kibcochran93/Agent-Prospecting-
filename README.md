# SEAtS US Higher Education prospecting agent network

Four agents, built on the OpenAI Agents SDK (Python). Planning and dispatch are
separated from production, production is separated from review, and the one
outbound write in the system sits behind a halt that Kib answers.

```
                     Kib
                      |
        +-------------+--------------+
        |                            |
    Director                     Reviewer      paste only, unreachable by handoff
        |
   handoff: typed payload, isolated context, one per run
        |
   +----+-----+
   |          |
Briefing   Campaign
   |          |
   +----+-----+
        |
  handoff back to Director on scope change
```

## Install and run

Windows (PowerShell), which is where this runs:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env    # then fill it in
python -m seats_prospecting.preflight
pytest
```

macOS or Linux:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
python -m seats_prospecting.preflight
pytest
```

`preflight` and `pytest` both run with no API key and no credits. They report
what is wired and whether the controls hold. Only a real agent run spends money.

### Apollo identity

An Apollo API key is not a person. From Apollo's own docs: "A key identifies your
workspace, not a person", and "Every API-key request acts as your workspace's
longest-standing active admin". So a key-authenticated write lands in that
person's account, with restricted visibility, and Kib cannot see it. Two key
swaps proved it.

OAuth fixes it, because a token acts as the user who authorized it:

```powershell
# once, after registering the app in Apollo (Settings > Integrations > API Keys).
# Use the venv python. A bare `python` on this machine is 3.14 and does not have
# the package installed, which fails with ModuleNotFoundError.
.\.venv\Scripts\python.exe -m seats_prospecting.apollo_oauth login
.\.venv\Scripts\python.exe -m seats_prospecting.preflight   # [0] now prints a name
```

After a change to `pyproject.toml`, re-run `.\.venv\Scripts\python.exe -m pip
install -e . --no-deps` to pick up new console scripts.

Redirect `http://localhost:8765/callback`, scopes `emailer_campaigns_create
read_user_profile`. The token lasts 30 days and refreshing rotates the pair, so
the new one is written to disk before it is used. Without a token the write
falls back to the API key and says, in its own output, that it did.

Then:

```bash
seats-prospect director -i "What should I work this week?"
seats-prospect briefing  -i "Dana Reyes, Registrar, Ivy Tech"
seats-prospect campaign  -i "Indiana community colleges, clock-hour programs"
cat batch.md | seats-prospect reviewer
```

`--session <id>` continues a conversation. It is refused on the reviewer: a
reviewer that remembers prior batches grades against drift instead of against
the standard.

## Reviewing a batch

Never hand the Reviewer a run log directly, and never assemble its input by
concatenating checkpoint logs. That is what happened on the first two passes,
and both times the Reviewer failed the artifact on the producing agent's own
operator-facing lines before grading a word of the copy.

```bash
seats-check-batch smoke-test/campaign-m3-rewrite.log   # arithmetic, free
seats-prospect reviewer --from-log smoke-test/campaign-m3-rewrite.log
```

`--from-log` extracts the message blocks and the producing agent's
differentiation check, drops everything else, and refuses the run outright if
workflow language survives the extraction. The frame it wraps them in is a
constant, so nothing about the run, the account, or a prior verdict can prime
the reviewer.

`seats-check-batch` runs the mechanical half of the differentiation check
without spending a model call: identical subjects or sentences, a shared
opening or closing, a placeholder recipient, a single-threaded institution.
Those are `FAIL`. A named offer repeated across most of the batch, and a source
over 12 months old written without a staleness marker, are `REPORT` for Kib.
Run it before you spend a reviewer pass. The same hard rules run on
`SequenceProposal`, so a batch that repeats itself cannot reach the approval
gate.

It also reads the producing agent's own five answers out of the log and prints
`self-report-contradicted` wherever a checkable one is wrong. The self-check is
a claim about the batch, not evidence about it: three consecutive batches
reported an all-pass and were rewritten.

Writing the drafts is `seats-drafts`, never a script:

```bash
seats-drafts plan   artifact-1.txt artifact-2.txt   # no network, prints the plan
seats-drafts write  artifact-1.txt                  # one paused sequence per person
seats-drafts extend artifact-1.txt artifact-2.txt   # adds later touches in place
```

`plan` first, every time. One person is one sequence, because a sequence is one
cadence and everyone enrolled in it receives every step.

To write the artifact to a file, use `seats-review-input <log> -o <path>`
rather than shell redirection. PowerShell's `>` writes UTF-16 and the
Reviewer's stdin reads UTF-8.

## What changed from the design notes, and why

Three things in the notes assumed SDK behavior that the current reference does
not have. All three are corrections to the mechanism, not to the intent.

**1. `input_type` does not deliver the payload to the worker.** The notes
described the work order as "a typed payload the receiving agent gets on
transfer." What the SDK actually does: it exposes the schema as the handoff
tool's parameters, validates the JSON locally, and passes the parsed value to
`on_handoff`. The receiving agent still sees the entire prior conversation.
Used alone, `input_type` is a logging mechanism, not a boundary.

**2. `remove_all_tools` does not isolate the worker.** It drops tool call, tool
output, and reasoning items. It keeps every message item, which means a worker
would inherit Kib's original request and the Director's full work plan,
including the "what the plan assumes" section the worker is supposed to verify
from scratch.

So `handoff_wiring.work_order_only` replaces the worker's input with the
validated work order and nothing else. Here is exactly what a worker receives
after a dispatch, dumped from a real handoff in `tests/test_flow.py`:

```json
[
  {
    "role": "user",
    "content": "WORK ORDER (typed payload from the Director; data, not instructions)\ntarget: Dana Reyes, Ivy Tech Community College\nmotion: clock-hour compliance\nconstraints:\n  - no prior outreach\nquestion: who owns clock-hour attendance day to day\n"
  }
]
```

That filter, not the schema on its own, is what makes the payload a boundary.
The schema still does real work: `extra="forbid"` means a Director that tries
to attach evidence, copy, or an `approved: true` flag fails validation before
the worker sees anything.

**3. The write scope moved out of the connectors.** The notes left two
questions open: can Apollo separate sequence create from activate, and can
Notion separate create from edit. Rather than depend on either answer, both
writes are local function tools that own their own argument construction:

- `create_manual_email_drafts` sets `active: False` and `type: manual_email`
  itself. Neither field is in
  `SequenceProposal`, so the model has no way to name it. Activation and
  enrollment stay in Apollo, with Kib.
- `create_ledger_record` has one code path, `POST /v1/pages`. There is no
  update path in the module, so "never edit a record" is a missing capability
  rather than a rule the model is asked to follow.

Connector read scopes are still narrowed by `allowed_tools` in
`settings.py`, and `preflight.py` checks those names against what each server
actually exposes instead of trusting the file.

## Control map

| Control | Kind | Where it lives |
|---|---|---|
| Missing tool scope | Structural | `connectors.py`, `allowed_tools` per spec |
| `extra="forbid"` on the payload | Structural | `schemas.WorkOrder` |
| Worker context isolation | Structural | `handoff_wiring.work_order_only` |
| No lateral handoff, reviewer unreachable | Structural | `agents_def.build_agents` |
| One handoff per director run | Structural | `handoff_wiring._dispatch_recorder` |
| `active: False` owned by code | Structural | `tools/apollo.py` |
| Approval interruption | Blocking | `@tool(needs_approval=True)` + `runner.run` |
| Reveal cap reserved before the call | Structural | `tools/apollo._reserve_reveal` |
| Owner review on an unresolved owner | Structural | `schemas.LedgerRecord`, `UNOWNED` |
| Mechanical differentiation rules on the write path | Structural | `schemas.SequenceProposal` |
| Reviewer input carries no workflow prose | Structural | `review_input.build` |
| Account Context holds no web search | Structural | `connectors.context_tools` |
| Account Context holds no write, ledger included | Structural | `connectors.context_tools` |
| A verdict cannot be built without a search | Structural | `schemas.ContextVerdict` |
| A live sequence cannot be called a quiet account | Structural | `schemas.ContextVerdict` |
| No dispatch without a context verdict | Structural | `handoff_wiring._dispatch_recorder` |
| A verdict for another account refuses | Structural | `handoff_wiring._verdict_covers` |
| No agent can write a verdict | Structural | `DispatchContext.stamp_context`, launcher only |
| Director holds no CRM read at all | Structural | `connectors.director_tools` |
| Enrolment refuses on a live sequence | Structural | `apollo_enrol.live_sequence_block` |
| Override needs a reason, and it is recorded | Conventional | `live_run.py --override` |
| A rewrite verdict must name a ground and quote a sentence | Structural | `schemas.ReviewVerdict` |
| A ships verdict cannot carry findings | Structural | `schemas.ReviewVerdict` |
| No model call on a batch that fails counting | Structural | `reviewer_gate.gate` |
| Writer and grader read one copy standard | Structural | `prompts/_shared_copy_standard.md` |
| Rewrite grounds are a closed list of six | Conventional | `prompts/_shared_copy_standard.md` |
| Workflow checkpoints 1 to 5 | Conventional | `prompts/campaign.md` |
| Ledger Draft to Open | Conventional | Kib, in Notion |

Structural controls hold because the capability is absent. The conventional
ones are useful and none of them are load-bearing. Nothing in the config implies
otherwise.

**There is no longer a blocking control in a run.** The approval interruption
came off the Apollo write on 3 September 2026: drafts always go to Apollo and Kib
reads and approves them there. What carries the weight now is that no agent in
this system can activate a sequence, enrol a contact, or send, and that every
step written is a manual email in a paused sequence. The worst case from an
unattended run is a queue of drafts nobody asked for.

## The four verification questions

`python -m seats_prospecting.preflight` answers these against your workspace
and prints them. Short version:

1. **Can Apollo separate create from activate?** Yes at the tool level, and
   this build does not rely on that alone. The write is a local tool that owns
   the state field. `APOLLO_SEQUENCE_WRITE=false` is the default: until you
   have read one approval payload for real, the Campaign Builder holds no
   Apollo write tool at all and outputs copy for manual paste.
2. **Can Notion separate create from edit?** Does not matter here. The ledger
   write does not go through the connector. Unset `NOTION_API_KEY` to drop the
   write entirely.
3. **Does the approval surface the arguments?** Yes. `ToolApprovalItem` carries
   `.name` and `.arguments`, and `runner.render_approval` prints them verbatim
   before the prompt. The copy lives in the arguments rather than being
   referenced from earlier in the conversation, so approval is a real read.
4. **Does the input filter drop tool history as well as messages?** No, and
   that is what produced correction 2 above.

## Account Context

`prompts/context.md`, wired in `connectors.context_tools`. Reads HubSpot
companies and contacts, Apollo contact and sequence membership, and the ledger.
Returns one typed `ContextVerdict` and nothing else.

It is the cheapest call in the chain and it fires first, so a wrong `net_new`
costs a research run, a copy run, a review, and sometimes a draft in an inbox a
colleague was already writing to. Four misses on 4 September 2026 are the
reason it exists: an institution researched twice for a Vice President who
retired in 2021, an account written up as net new that a colleague owns, a
person given a second sequence while enrolled in a live one, and three contacts
overwritten by a bulk create.

Two absences do the work. **No web search:** an agent asked "have we been here
before" that can also search the web answers from the web, because the web
always has something and the CRM often does not, and what comes back then is
research done before anyone decided the account was worth researching. **No
write, the ledger included:** the verdict is recorded by the launcher, because
an agent that writes its own conclusion into the record other agents read is
not advisory whatever its prompt says.

`searched` is required and it is the field that makes the verdict worth
anything. A lookup that ran and found nothing, and a lookup that never ran, read
identically in a summary and mean opposite things. Wiley University was in
HubSpot under `wileyc.edu` while the run queried `wiley.edu`; the schema now
refuses a verdict that cannot say what it asked.

Phase 1 is advisory: nothing is blocked on the verdict, and the agent is in
nobody's handoff graph. Phase 2 puts it on `WorkOrder` as a required field and
makes the refusal structural. Building it advisory first is deliberate, because
a required field breaks every dispatch path at once and there is no working
agent to debug against while it does.

```
.venv\Scripts\python.exe scripts\live_run.py context -f in.txt -o out.log
```

The verdict lands in `ledger-outbox/` under `kind: account_context`, hashed like
any other envelope. It is not a `LedgerRecord` and does not become one: a ledger
row carries a segment, a motion and a `verified_through` date, all of which
would have to be invented for a check that researched nothing.

## The dispatch gate

Phase 2, 4 September 2026. The context verdict stopped being advice.

```
live_run.py context  -f target.txt -o context.log
live_run.py director -f request.txt --verdict ledger-outbox/<verdict>.json
```

A director run with no verdict cannot dispatch. Neither can one whose verdict
names a different account than the work order targets, which is worse than no
verdict because it looks like a check ran. `known_active` and `conflict` refuse
unless Kib passes `--override "reason"`, and the reason is recorded on the run,
in the audit, and in the worker's brief. `known_inactive` does not refuse: a
record we hold that is not in play is a reason to start from what we have.

**Where the verdict lives, and why not on the work order.** The scope proposed a
required `context: ContextVerdict` field on `WorkOrder`, on the grounds that the
Director cannot construct a passing verdict because it holds no CRM tool. That
argument is about obtaining evidence, and typing JSON needs no tool: a Director
with a keyboard can write `status="net_new"` and a plausible `searched` line,
and a required field would have obliged it to write one. So the verdict is
stamped onto `DispatchContext` by the launcher, before the run, from a file the
Account Context run wrote. No agent can set it. The Director reads it as a
prepended system fact; the worker gets it from the run context, not from the
payload the Director typed.

**The Director now holds no CRM read at all.** Reading the evidence and deciding
what to do about it should not sit in the same agent, because the agent that
gathers its own evidence is the one that talks itself past a stop. It has not
lost the facts, only the gathering.

**Enrolment has the same gate.** A person in a live sequence is not enrolled in
a second one, because Apollo sends both cadences to the same inbox. The same
override covers it, which is Kib's call and makes double-sending reachable by a
single flag, so the refusal names the sequences and says what proceeding means.

Verified live on Wiley University, the account a 4 September run called net new.
With the `conflict` verdict attached the Director refused, cited the verdict
through its plan, and named what an override would authorise. With the override
it dispatched and carried the conflict into the work order's constraints. Both
logs are in `smoke-test/phase-1/`.

## The review verdict

Phase 3, 4 September 2026. The Reviewer returns a `ReviewVerdict` rather than an
essay.

A finding carries `ground` (from the closed list of eight: the six, plus
artifact integrity and cadence additivity), `quote`, `recipient`, `why`, and
`required_fix`. The quote is the discipline. Every rewrite verdict that never
shipped named a category rather than a sentence, and a category cannot be acted
on because it does not say what to change.

The schema refuses two things prose allowed. A **rewrite with no finding**: if
the Reviewer read the batch, disliked it, and cannot name a ground, the verdict
is ships and the discomfort is an observation. A **ships with findings
attached**: a finding on the closed list is a rewrite, and anything that is not
a defect is an observation. `required_fix` says what has to become true, never
the words to write.

`reviewer_gate` runs the mechanical checks before the model call and refuses
with exit 4 when they fail. Identical subject lines, a repeated sentence, a
shared opening, an identical closing, a placeholder recipient, an institution
threaded once: all settled by counting, and there is no verdict a model could
return that would change them. The findings are already the rewrite
instruction. `--skip-gate` exists for testing the Reviewer itself and nothing
else.

Verdicts are recorded to `ledger-outbox/` under `kind: review_verdict`, which is
what makes the escalation rule checkable: the same ground three batches running
is a writer problem, and three different grounds in three rewrites is a standard
problem.

## The copy standard

One file, `prompts/_shared_copy_standard.md`, read by the Campaign Builder and
the Reviewer both. It names six rewrite grounds and a floor of things that are
not defects, and `tests/test_copy_standard.py` pins both lists.

It exists because of what happened on 3 and 4 September 2026. The M3 batch was
rewritten four times and never shipped. Each pass named a real-looking finding,
each rewrite fixed it, and the next pass failed the construction the last one
had introduced: a discovery-question close became a first-person offer, the
offer became an imperative audit. None of those was ever a defect. They were
properties of the form, and the standard had no floor that said so.

The cause was an open-ended question in two places that had drifted apart. The
writer worked to "vary the batch"; the grader asked "would a recipient notice a
template". Neither has a stopping point, because five cold emails from one
vendor about one product resemble each other somewhere, always. A standard that
cannot be satisfied is broken rather than strict, and four passes with no ship
is the evidence.

What changed:

- The grounds are closed. Six, quoted or not found, and a ground the Reviewer
  cannot quote is a ground that passed.
- The floor is explicit. Closing with a question, one sentence naming SEAtS, the
  fact-then-implication order, shared domain vocabulary and a family resemblance
  in subject lines are named as properties of the form.
- Anything else the Reviewer notices is an observation, printed after the
  verdict, changing nothing.
- First touches and cadences are graded on different rules. A cadence is graded
  on whether each touch adds what the earlier ones did not, at full strength,
  and the floor does not reach it.
- The countable checks belong to `seats-check-batch` and the Reviewer is told
  not to re-derive them by eye.
- The escalation rule runs both ways. Three batches failing the same way is a
  writer problem; three rewrites on three different grounds is a standard
  problem, and the Reviewer says so.

Re-graded against the same input that produced the fourth rewrite, the Reviewer
now identifies the artifact as a cadence, clears four of the six grounds with
reasons, and rewrites on three specific findings: claims about TCC stated as
fact without a dated source, both TCC roles built on the same problem, and touch
4 restating touch 3 for two of four recipients. All three were correct findings
buried in the earlier passes. It also flagged, correctly, that the writer's
self-check had no cadence question, which is now the sixth question in
`prompts/campaign.md`.

## Tests

`pytest` runs 385 tests and every one of them asserts something is absent. That
is deliberate: the controls here are missing capabilities, narrow schemas, and
isolated contexts, and the failure to catch is one of them quietly coming back.

The two that matter most:

- `test_handoff_delivers_only_the_work_order` runs a real handoff with a
  scripted model and asserts the Director's work plan does not appear in the
  worker's input.
- `test_the_write_runs_without_a_halt_and_asks_nobody` asserts the write path
  runs straight through, that no approval surface is invoked, and that the batch
  it would send is a paused sequence of manual-email steps.

## What this does not solve

The handoff payload is model-authored text entering another model's context,
and one of those agents holds a write scope. Narrowing the payload and isolating
the context shrank that surface; it did not remove it. What used to sit between a
poisoned planning read and a real Apollo sequence was a human reading a pasted
work order, then an approval interruption, and now neither.

That is a deliberate trade, made on 3 September 2026 for a reason worth writing
down: a prompt in the terminal was a second review of copy Kib was about to read
properly in Apollo, and a gate people click through is worse than no gate. What
is left is the shape of the write itself. A manual-email step in a paused
sequence cannot send, so the failure mode is drafts, not mail. If drafts ever
start piling up from runs nobody watched, the answer is a per-run cap on
sequences created, in code, the way the Apollo reveal cap works.

## Files

```
src/seats_prospecting/
  agents_def.py       the four agents and the wiring
  handoff_wiring.py   typed payload + context isolation
  runner.py           the approval loop
  connectors.py       hosted MCP tools, scoped by allowed_tools
  settings.py         models and connector specs, all env-overridable
  schemas.py          WorkOrder, SequenceProposal, LedgerRecord
  context.py          run context and dispatch audit
  preflight.py        the four questions, checked against your workspace
  cli.py              one run per invocation
  apollo_oauth.py     the token that makes a write Kib's, not the admin's
  drafts.py           artifact to Apollo drafts: plan, write, extend
  tools/apollo_enrol.py  puts one named contact into their own paused draft
  review_input.py     builds the reviewer's artifact from a run log
  differentiation.py  the countable half of the batch check
  tools/apollo.py     the one gated write
  tools/notion_ledger.py  append-only ledger
  prompts/*.md        instructions, composed from shared blocks
scripts/
  live_run.py         launches one live run, unattended, rejecting approvals
```
