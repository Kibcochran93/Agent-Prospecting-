# Handoff — state as of 15 September 2026

Written for a fresh session picking this up with no prior context. Read
`README.md` for the architecture and `knowledge/00-canonical-precedence.md` for
document authority and open conflicts. This file is only the current state.
Everything below the 15 September section is prior history, kept because most
of it is still true; nothing in it has been re-verified today except what this
section explicitly touches.

## 15 September: the dashboard reconciled, a fourth cap added, no git anywhere

**554 tests passing**, up from 225 on 8 September (`.venv\Scripts\python.exe -m
pytest -q`). Four new modules today: `briefing_queue.py`, `enrollment_queue.py`,
`read_proxy.py`, and additions to `drafts.py` and `tools/apollo_write.py`.

**The single biggest finding today: there were two disconnected systems.** A
separate "Prospecting Control Panel" had been built as a Claude Artifact inside
a Cowork session, entirely independent of this codebase — its own Firestore-
style storage, its own direct Apollo writes, no daily cap, no Reviewer gate, no
`decisions/` audit trail. It duplicated everything this project's safety
architecture exists to enforce, from a different door. Kib's call: reconcile
into one system, and make the result independent of Cowork entirely rather than
live inside it.

**ADR 0004, `docs/adr/0004-remove-ownership-conflict-gate.md`.** Ownership alone
no longer blocks a dispatch. `ContextVerdict.status` lost `conflict`; only
`net_new`, `known_inactive`, `known_active` remain. An account owned by Cal or
Miguel with no activity on it is `net_new` and proceeds — the point of this
system is finding what falls through the cracks of ownership, not gating on it.
Activity (a live sequence, a recent touch, a colleague's mailbox already
sending) is `known_active` regardless of whose name is on the account, and that
is still what blocks. This directly supersedes the "Account ownership is
recorded, not enforced" section further down this file for the *dispatch* gate
specifically — it does not touch `ASK_OWNER` in `next_step.py`, which is
unchanged and still notify-not-gate.

**The bridge: `scripts/board_serve.py`, port 8765, localhost only.** A plain
`http.server` already existed for the local board; today it grew into the one
place both the local dashboard and the (new, independent) file-based dashboard
talk to. Endpoints, all bearer-token-gated (`SEATS_BRIDGE_TOKEN` in `.env`) and
CORS-enabled (including the `Origin: null` case a `file://` page sends):

| Route | Does | Guarded by |
|---|---|---|
| `GET /api/state` | pending/queueable/context_notes + both budgets, read fresh off disk every call | token |
| `POST /decide` | writes `decisions/`, same as the local board's own buttons | token, `decisions.py` |
| `POST /queue` | writes `queue/`, picked up later by `run_queue.py` | token, `job_queue.py`'s daily cap |
| `POST /briefing` | **new today** — runs `live_run.py briefing` synchronously, returns the output | token, `briefing_queue.py`'s daily cap (10), lock-before-write |
| `POST /api/apollo`, `/api/hubspot` | **new today** — `read_proxy.py` forwards a named tool+input to the real API, using the same OAuth/token creds this project already had | token, read-only, narrow allow-list of tool names |

**Three daily caps now, all the same lock-before-write shape** (the exact bug
that let six reveals through a cap of one on 4 September, solved the same way
each time rather than trusted to not recur):
- `job_queue.py`, `SEATS_MAX_JOBS_PER_DAY`, bumped 5→10 in `.env` today.
- `briefing_queue.py`, `SEATS_MAX_BRIEFINGS_PER_DAY`, new today, set to 10.
- `enrollment_queue.py`, `SEATS_MAX_ENROLLMENT_REVEALS_PER_DAY`, new today,
  default 10, not yet added to `.env` explicitly (code default holds).

**The dashboard is now a single independent file:**
`prospecting_control_panel_local.html`, repo root. No `window.claude` anywhere
in it — not `mcp`, not `db`, not `sample`. Everything goes through `fetch()` to
the bridge or through `localStorage` (bridge URL/token, the dashboard's own
activity log, marked-reviewed state). It was built by porting the Cowork
Artifact's JS to call the bridge instead of Apollo/HubSpot/Firestore directly,
then a second pass stripped every remaining Claude-platform dependency so it
would work as a plain file. **The old Cowork Artifact itself was never deleted**
— I have no tool that reaches a Cowork session from a chat; Kib was told to
clear or stop using it himself, and I don't know whether that's been done.

**A real bug shipped and was caught live, not in testing:** the independence
rewrite deleted the `connectorState` variable declaration but left three
references to it, which threw inside `loadCampaigns()`'s own catch block —
meaning the exception escaped uncaught and `renderEngagementChart()`/
`renderDraftsTable()` silently never ran, while `renderStats()` (called
separately, from `pollState()`) looked fine. Kib found it via the browser
console; fixed same day. Worth remembering: this file has no automated test
coverage at all, unlike everything in `src/`. Any future JS change should get
at least a manual reload-and-look before being called done.

**Full end-to-end test passed for real, by the best measure available:** Kib
used the live dashboard himself — approved six real pending accounts, declined
one, queued two real COPY jobs — before I'd even finished asking whether it
worked. `/api/apollo` and `/api/hubspot` were separately confirmed with real
calls (Apollo campaigns search, Apollo contact search, HubSpot object search and
owner lookup all returned correct real data).

**Email resolution at enrollment time, built today, NOT yet fired live.**
Previously `write_drafts()` created a sequence with no contact ever attached —
by design, per the existing `tools/apollo.py` doctrine that no agent holds an
email address. Kib's call, 15 September: resolve the email **only** at the one
point that already writes to Apollo (`run_sequence()`, already gated behind a
matching-hash "ships" verdict), and only there — briefing, drafting, and review
still never see an email. `drafts.resolve_and_enroll()`:
1. Free search first (`contacts/search`) — often already has the email, since
   most current targets came from the original Apollo list import.
2. If not saved, free search of Apollo's broader database
   (`mixed_people/api_search`).
3. Only a **single confident match** at either stage proceeds; zero or multiple
   candidates refuse without spending anything, on purpose (identity-match risk
   was the explicit reason option 1 was chosen over auto-enrolling everything).
4. A real reveal (`people/match` with `reveal_personal_emails: true`) only fires
   for a single confident match with no email yet, spends one
   `enrollment_queue` credit.
5. Creates/updates the Apollo contact, calls `add_contact_ids` with
   `status: "paused"` against Kib's own mailbox
   (`GET /email_accounts`, matched by `user_id == KIB_APOLLO_USER_ID`, new
   constant in `tools/apollo_write.py`).

Verified: `GET /email_accounts` for real (found `kcochran@seatsone.com`,
mailbox id `686d22278ceaa3000d33d445`). `contacts/search` for real on Sherry
Strickland / Texas State Technical College — single match, email already on
file, zero reveal spent. Seven new unit tests in `tests/test_resolve_and_enroll.py`
cover every branch with a mocked transport. **The two calls that actually
create/attach a contact (`POST /contacts`, `POST .../add_contact_ids`) have
never fired for real.** The next real SEQUENCE job through `run_queue.py` is
the first live proof. If it fails partway, the sequence itself is unaffected —
`resolve_and_enroll` runs after `write_drafts` already succeeded, and a failed
enrollment just leaves the sequence exactly as empty as it always used to be.

**A real, unexplained reliability problem: `board_serve.py` died silently at
least four times in one session,** no trace in its own error log each time.
Correlated (not proven causal) with entries in Windows' System event log —
`WdDevFlt` (Windows Defender's device filter) unloading and the hypervisor
restarting, both times within a couple minutes of when the server was found
down. Possibly tied to a VPN reconnect or a periodic security-tool reset; not
pinned down. **Not fixed** — Kib was offered an auto-restart/service wrapper and
the session ended before building it. Restart command:

    cd "C:\Agent Prospecting Ops\seats-prospecting"
    Start-Process -FilePath ".\.venv\Scripts\python.exe" -ArgumentList "scripts\board_serve.py" -WindowStyle Hidden -RedirectStandardOutput "queue\logs\board_serve.out.log" -RedirectStandardError "queue\logs\board_serve.err.log"

Check it's actually listening after, don't trust the launch call's own success —
`Start-Process` through the bridge has reported a 4-minute timeout on this exact
command every single time today despite the process starting fine each time.
`Get-NetTCPConnection -LocalPort 8765 | Where-Object State -eq Listen` is the
real check.

**No git repository exists in this folder.** `git status` returns "not a git
repository." Everything from today — the entire bridge, three new modules, the
independent dashboard, ADR 0004, the enrollment feature — exists only as raw
files on disk, no version history, no commit boundary between today's session
and anything before it. This was true before today too (the 4 September section
below already says "no version control, no backup"), but today added enough
volume that it's worth flagging prominently rather than repeating as a footnote.
Initializing git and making a first commit would be a good use of five minutes
before building anything else.

**On driving the desktop directly (Windows-MCP click/type/screenshot):** it
works, but Kib is often actively using the same machine at the same time, which
means an action that looks like a malfunction (Outlook showing "Deleted/Undo",
a bulk of real approvals appearing in the activity log) may just be him using
his computer. Twice today I flagged something as broken that turned out to be
his own real-time activity. Confirm with him before assuming an anomaly is a
bug, especially anything involving Cowork's own UI — the chat transcript's own
tool-call log ("Used Windows-MCP integration") is easy to mistake for a second
agent when viewed out of context; it isn't one unless proven otherwise.

## New open items for the next session

1. **Fire the first real live SEQUENCE run and confirm `resolve_and_enroll`
   actually creates/attaches a contact**, not just the mocked tests.
2. **Board server reliability** — build the auto-restart Kib was offered, or at
   minimum keep investigating the WdDevFlt/hypervisor correlation.
3. **Initialize git.** Nothing today has any version history.
4. **Confirm the old Cowork Artifact has actually been cleared or abandoned** —
   unverified as of end of session.
5. **`SEATS_MAX_ENROLLMENT_REVEALS_PER_DAY`** isn't in `.env` yet; only the
   code default (10) is in effect. Add it explicitly if 10 isn't the right
   number.

## Where things stand

- **225 tests passing.** `.venv\Scripts\python.exe -m pytest -q` on Windows.
  Every test asserts something is absent.
- **Four emails sent, all four by Kib's own hand in Apollo on 4 September.**
  No agent in this system can send. The capability is absent, not forbidden.
  Touch 1 of each batch 1 cadence was delivered by Kib working the manual tasks;
  confirmed against Apollo on 8 September. Everything after touch 1 is still a
  queued task waiting on a person.
- **The write path works, proven live on 4 September.** Sequence
  `6a9ad06e47812c0014788c89`, owner `69d808314b6a820015875600` which is Kib,
  paused, two manual-email steps with the copy in them. Probe:
  `smoke-test/write_probe.py`.
- **Both halves have now run live.** Research and outreach.

## Wired

| System | Access | Notes |
|---|---|---|
| HubSpot | read only, Director only | companies, deals, owners, notes. No contacts. `HUBSPOT_DEALS_ENABLED=true` |
| Apollo | read, both workers | org lookup and roles free, reveal costs a credit. 18 spent, cap 25 |
| Apollo sequence write | **on** | `APOLLO_SEQUENCE_WRITE=true` since 3 Sep. `create_manual_email_drafts` writes manual-email steps into a paused sequence: every touch becomes a task with the copy pre-filled and no step can send itself. No approval halt in front of it |
| Notion ledger | outbox mode | `LEDGER_WRITE_MODE=outbox`, relayed by hand |
| Knowledge | 5 local docs | `knowledge/`. Read the precedence file first |
| SharePoint / Teams / Outlook | not wired | needs credentials Kib pastes himself. Never create these |

## Proven live

- Campaign Builder produced a 20-email batch across 4 institutions (M3).
  Log: `smoke-test/campaign-m3-step{1,2,3}.log`.
- Reviewer ran and returned **rewrite**, disagreeing with the Campaign
  Builder's own self-check on 4 of 5 questions. Log: `smoke-test/reviewer-2.log`.
- Approval gate halted before the Apollo write with all 20 messages in the
  payload; rejection closed the write path. Apollo unchanged, 232 sequences
  before and after. Logs: `smoke-test/gate-reject.log`,
  `smoke-test/approval-payload.txt`, `smoke-test/approval-probe-result.json`.
- M9 placement guardrail held on a live Lyon College run and declared the
  canonical conflict open. Log: `smoke-test/lyon-m9.log`.

## Drafts in Apollo

Kib's requirement is drafts he reviews and sends, not a sequence that mails on
activation. Delivered by the step type: `manual_email` rather than `auto_email`.
An auto step sends itself the moment the sequence is activated and a contact is
enrolled; a manual step queues a task instead. So the worst case from an
accidental activation is a queue of drafts, not mail leaving the building.

Both `active: False` and `type: manual_email` are set in `sequence_payload()` and
are absent from `SequenceProposal`, so the model cannot name either.
`manual_email` was verified against Kib's own Apollo workspace, where it appears
on 15 existing steps, rather than assumed from the API docs.

Mailbox drafts via `emailer_messages_create` were considered and rejected for
now. They need a named Apollo contact with a business email per recipient, which
would mean the system starts holding addresses, and the endpoint sits one call
from `send_now` with Apollo's own docs encouraging the chain. Revisit only as a
deliberate decision. `tests/test_manual_drafts.py` asserts none of that
capability is in the module.

## What changed this session

Three things, all of them behind the copy problem rather than in it.

**1. The reviewer harness was the bug in passes one and two.** Both inputs were
assembled by concatenating checkpoint logs, which carried the Campaign Builder's
lines to Kib into the review object: "Confirm the tighter slice before
checkpoint 4", "Names require manual personalization", "The copy below must not
be used until CRM suppression is complete". The Reviewer failed the artifact on
those, correctly, before grading the copy. Two passes spent on a harness fault.

`seats-prospect reviewer --from-log <log>` now builds the input structurally:
message blocks plus the producing agent's differentiation check, nothing else,
wrapped in a constant frame so no run context or prior verdict can prime the
review. If workflow language survives the extraction it refuses the run and
names the line. `seats-review-input <log> -o <path>` writes the artifact to a
file as UTF-8; do not use PowerShell's `>`, which writes UTF-16.

Never assemble reviewer input by hand again. It is the one step in this system
that has cost more than it has caught.

**2. The differentiation check is now counted, not self-reported.**
`seats-check-batch <log>` runs the mechanical half with no model call. On the
live M3 rewrite it produces, in about a second:

```
[FAIL]   shared-closing: two UA Little Rock messages close the same way
[FAIL]   placeholder-recipient: 'Retention workgroup lead / Student Success leader'
[FAIL]   placeholder-recipient: 'Provost / Vice Chancellor for Academic Affairs'
[REPORT] repeated-offer: 'Engagement Readiness' in 4 of 8 across 4 institutions
[REPORT] repeated-offer: 'Progress Check Pilot' in 4 of 8 across 4 institutions
[REPORT] stale-date-stated-as-current: 2025-02-17, 563 days old, no marker
```

That is most of reviewer pass three, for free. The same hard rules run on
`SequenceProposal`, so a batch that repeats itself now raises before the
approval payload is built and Kib is never asked to approve a template.

`prompts/campaign.md` gained the rules behind the rest: name the person when the
cited source names them, one role per message rather than two titles slashed
together, an incumbent is a sourced claim or it is unverified, one document is
one date, and a source over 12 months old is never restated as current.

**3. An unresolved account owner no longer clears itself.** `account_owner:
"unknown"` counted as unowned, so a record could write `outreach_decision: "Not
required"` with the owner never looked up. The 18:12 segment record did exactly
that. `unknown` is now held at Pending owner review; genuinely unowned values
("unassigned", "not in HubSpot", "none") still clear. Kib's call, this evening.

## The loop ran end to end on 3 September, twice

Target: Ronnie Williams, Vice President for Student Services and Institutional
Diversity, University of Central Arkansas, from an Apollo contact record.
Launcher: `scripts/live_run.py`, which is now a file rather than a command line
nobody kept. Logs in `smoke-test/uca/`.

**Leg one, research.** Director read HubSpot, found the account owned by Cal
O'Donovan with no deals, and dispatched. The Briefing came back with the finding
that matters: **Ronnie Williams is not a current UCA employee.** He retired,
the student center is named after him, and he later went to Hendrix College.
Apollo still carries the record Kib was working from. The briefing recommended
Lisa Pfrenger and named the real buying group instead. Ledger record written,
owner review pending. Reveals: 4.

**Leg two, copy.** Campaign Builder on Arkansas public universities, 5,000 to
15,000 headcount, M1. It refused to invent a segment under an EMS-only filter,
which was correct; widened to any documented scheduling platform it found two
accounts, mapped named people to roles with sources, and wrote four messages.

**Both reviewer passes ran clean.** No artifact-integrity finding on either,
for the first time. Both verdicts were rewrite, on substance:

- The briefing: M1 rationale contradicts its own evidence, buying-group roles
  asserted beyond what the sources establish, several claims undated.
- The copy: same problem restated per institution, a visible four-part
  architecture, and Chelsea Ward's Scheduling/NCAA role not reflected in a word
  of the message written to her.

**Decided.** Three consecutive batches now: the Campaign Builder reported its
own differentiation check as an all-pass and the Reviewer disagreed on every
question. Kib's call, 3 September: **the self-check stays in the output and
stops being treated as evidence.** It still makes the agent read its batch
together, which is what it is for. What it no longer buys is standing.

In force in four places:

- `campaign.md` says a pass of its own clears nothing and is never an argument
  against a rewrite.
- `reviewer.md` says the answers are a claim about the batch, not a second
  opinion to weigh.
- The reviewer's frame labels the section as a claim, and the section still
  travels, because the disagreement between the two readings is worth reading.
- `seats-check-batch` prints `self-report-contradicted` next to the claim
  wherever a checkable answer is wrong. On the M3 batch that is questions 2 and
  4, quoted against the count. Questions 3 and 5 are judgement and nothing
  pretends to answer them.

**What the live run fixed in the tooling.** Four faults, all found by running it:

1. The launcher header printed the work order above the output, so a raw run log
   handed to the Reviewer contained the dispatch. `--from-log` now cuts at
   `=== OUTPUT ===`.
2. A briefing has no batch section, so the extractor had nothing for the research
   half of the loop. It now reads briefings whole, keeping the system names a
   briefing is supposed to carry and refusing only workflow prose.
3. Section 7 of every briefing is called "One draft", which the copy detector
   read as a batch and kept while dropping the other seven sections. Detection
   now needs an addressed message as well as a heading.
4. The live batch wrote `**To Rachel Broussard, ...**` with no colon, which the
   checker parsed as zero messages and reported clean. Both header shapes parse
   now, and `campaign.md` pins the format.

Plus two false positives the checker raised on real copy and no longer does: the
sign-off "Kib" read as a shared closing, and "Scheduling/NCAA Coordinator" read
as two roles slashed together.

**Reveal budget.** `APOLLO_REVEAL_CAP` is per run, not per session. Kib said one
reveal on this run; the Director run spent four before the cap was set on the
command line. Set `$env:APOLLO_REVEAL_CAP` before launching if a run is meant to
be tight.

## The re-run, same contact, current build

Second pass on Ronnie Williams / UCA, logs in `smoke-test/uca-2/`. Same input
file as the first run, so the two are comparable.

- Same core finding, better sourced: Williams **retired in 2021**, with UCA News
  of 3 December 2021 behind it rather than an inference from a building name.
- Richer account: UCA 2035 approved 27 May 2026, Banner on premises, Brightspace
  planned for Fall 2026, Vicky Summers as Registrar, Kevin Thomas over Enrollment
  Services and Student Success. Motion moved to M3 leading, M1 second.
- Ledger record written, owner review pending. Reviewer ran clean on the harness
  and returned rewrite on ten substantive findings.

**The reveal cap did not hold, and now does.** The run was launched with
`APOLLO_REVEAL_CAP=1` and spent two credits, logging "apollo reveal 2/1". The
model had emitted six reveal calls in one turn; they ran concurrently and every
one of them read the count before any of them incremented it. Check-then-spend.
The guard now reserves its slot before the await and gives it back when Apollo
charged nothing, and `tests/test_apollo.py` runs six concurrent reveals against
a cap of 1 and asserts one credit. Total spend today: 6.

**Decided from the re-run.** The Reviewer flagged the briefing's own
recommendations as directives:

> "Hold for Cal's review and send only to a verified current contact."
> "First verify Ronnie's status, resolve the CRM date discrepancy..."

Those are the Prospect Briefing writing to Kib in its Review notes, which is
what that section is for. Kib's call: the artifact-is-the-object rule stays at
full strength for copy and is scoped for briefings, where it now fires only on
text addressed to the reviewer, workflow language, or plainly retrieved
instruction. A recommendation to Kib is content, judged on its evidence.

## Copy quality: settled 4 September, the standard was the problem

Kib's decision, 4 September 2026: recalibrate the Reviewer for cold first
touches rather than change the copy strategy. Done. The reasoning and the
before/after are in the README under "The copy standard"; the short version is
that the M3 batch was rewritten four times because the standard had no floor,
so every pass found the construction the previous rewrite had introduced.

`prompts/_shared_copy_standard.md` now holds one closed list of six rewrite
grounds and an explicit floor of things that are not defects, and both the
Campaign Builder and the Reviewer read that file. `tests/test_copy_standard.py`
pins both lists and asserts the two agents read the same bytes, because the
drift between two descriptions of one standard is what caused this.

Re-graded on the same input as the fourth rewrite
(`smoke-test/batch-1/reviewer-5-recalibrated.log`), the Reviewer identifies the
artifact as a cadence, clears four grounds with reasons, and rewrites on three
findings that are all real and all fixable: TCC claims stated as fact with no
dated source, both TCC roles built on the same problem, and touch 4 restating
touch 3 for Dewayne Dickens and Mary Ann Tietjen. Fix those three and this
cadence ships. That is the difference from the four earlier passes, where
nothing named was ever fixable in a way that would have produced a ship.

The Reviewer also caught a gap in the recalibration itself: the writer's
self-check was entirely batch-level, so every question could pass while a
cadence repeated itself. There is now a sixth question in `prompts/campaign.md`
asking, per recipient, whether each touch carries a different reason to reply.

**Do not loosen the six to get a ship.** The floor exists so the Reviewer stops
failing copy for having the shape of cold outreach. It does not exist to pass
copy that asserts things about a stranger's institution without a source, and
ground 2 is the one most likely to be argued away.

### What the four failed passes said, for the record

The M3 batch has been through the Reviewer twice and failed twice. This is the
main open thread.

Pass 1 (`smoke-test/reviewer-2.log`): templated structures, all four accounts
single-threaded, unsourced claims, a line implying a competitor lacked a
capability.

Pass 2 (`smoke-test/reviewer-3.log`), after a rewrite that added sources and a
second role per institution, found deeper problems and researched the accounts
itself:

- **Murray State has an EdSights retention platform contract** in its board
  materials. The Campaign Builder had recorded the incumbent as "unverified,
  older EAB reference". Worth acting on regardless of the copy.
- UTC's 2025-2030 Strategic Plan was board-approved 2 March 2026, not the
  "Fall 2025 edition" cited. Two documents were conflated.
- UA Little Rock's strategic plan was approved 6 June 2024, over two years old,
  and was flagged as stale but then stated as current fact anyway.
- Named people were available in the very sources cited (UTC's January 2026
  announcement names the workgroup leads) but the copy used role placeholders.
- A visible two-lane template: every executive got Engagement Readiness, every
  operational owner got Progress Check Pilot.

Do not keep looping rewrites without Kib deciding. Each pass costs a run and the
failures are getting more substantive, not less, which is information about the
Campaign Builder rather than noise.

**Harness note:** fixed this session, see above. Use `--from-log`. The open
question is not how to run a third pass, it is whether to run one at all: the
mechanical failures are now visible for free, and what remains for a reviewer is
register, specificity and whether each touch earns its place.

## Two decisions from the evening of 3 September

**1. Drafts always go to Apollo, and the halt came off.** Kib reads and approves
in Apollo, which is where the copy is legible and where sending happens. A
terminal prompt in front of that was a second review of the same copy on a worse
screen. `APOLLO_SEQUENCE_WRITE` now defaults to true and
`create_manual_email_drafts` is no longer `needs_approval`.

So there is no blocking control left inside a run. What holds is unchanged and
structural: nothing here can activate, enrol or send, and every step written is
a manual email in a paused sequence. The failure mode is a pile of drafts, not
mail. If that ever happens, add a per-run cap on sequences created, in code,
the way the reveal cap now works. Kib was offered that today and did not take it.

**2. Account ownership is recorded, not enforced.** He sends from several
mailboxes, so an account Cal or Miguel owns is a fact he wants in front of him
rather than a gate. Every ledger record still names the owner. The forced
"Pending owner review" came off, including the tightening made earlier the same
day. Approved and Declined are still his to write, in the ledger, and an agent
that writes either is still overruled.

## Apollo identity: an API key cannot act as Kib

Settled against Apollo's own documentation on the evening of 3 September, after
two key swaps did not change anything.

> "A key identifies your workspace, not a person, so Apollo can't tell which
> teammate used it."
>
> "Every API-key request acts as your workspace's longest-standing active admin
> — the earliest-created user who hasn't been deleted and has admin access."

In this workspace that is **Miguel Pescador** (`69d5220afb60c4001d2603e1`, the
earliest user id on the team). So:

- Being an admin and generating the key under his own login changes nothing.
  Kib's second key behaved exactly like the first.
- Everything the local write tool creates is owned by Miguel, and new sequences
  are created with restricted visibility, so Kib cannot see them in his Apollo.
- Reveal credits come off that seat.

Proved without the profile endpoint, which a scoped key cannot read: Apollo sets
`sharing_permission.is_owner` relative to the caller, and across all 72
sequences the key reports owner on exactly the 8 that are Miguel's, including
the four that are Kib's own. `preflight` now runs that probe and says WRONG.

**The path to drafts that show as Kib's is OAuth**, built on 4 September:
`src/seats_prospecting/apollo_oauth.py`, plus `seats-apollo-login`. Apollo's
`create-sequence` accepts a Bearer token and names the scope
`emailer_campaigns_create`; a token acts as the user who authorized it. The
write tool takes the token when there is one and falls back to the API key
otherwise, saying in its own output which identity it used. Preflight asks the
token whose it is, which the `read_user_profile` scope makes possible where a
scoped key gets a 403.

**Kib's next step, and it is his:** register the app in Apollo under Settings >
Integrations > API Keys > OAuth registration, redirect
`http://localhost:8765/callback`, scopes `emailer_campaigns_create
read_user_profile`, then put the client id and secret in `.env` and run
`python -m seats_prospecting.apollo_oauth login`. Registration goes through an
approval step, so this is not instant.

The hosted Apollo MCP connector is the other route to the same thing. The evidence is in the workspace already: sequences with
`creation_type: "mcp"` carry each person's own `user_id`, while the one this
build wrote carries `creation_type: "api"` and Miguel's. `settings.APOLLO_READ`
already has the connector slot and `APOLLO_AUTHORIZATION` is unset. Wiring it
means narrowing `allowed_tools` to the sequence create, because that server also
exposes approve, send_now and contact enrolment.

**The steps: solved, and the touches theory was only half of it.** The create
endpoint is `POST /sequences`. This build was posting to `/emailer_campaigns`,
which accepts the request, returns 200, and drops every step. Two faults in one
call, both now fixed from Apollo's OpenAPI specification rather than guessed:

- endpoint `/sequences`
- each step carries `emailer_touches`, and the copy lives in a touch's
  `emailer_template` as `subject` and `body_html`

A third fault was found on the way: Apollo's `wait_time` is the gap since the
previous step, while `SequenceTouch.day_offset` is days since enrollment. Passed
through unchanged, a day 0/6/13 cadence would have gone out on day 0, 6 and 19.
`_relative_waits` converts, and a test pins it.

The spec is worth keeping: `smoke-test/apollo-openapi.json`, 1.1 MB, downloaded
4 September. The container cannot reach docs.apollo.io; Kib's machine can.

## Not proven

- Nothing has been **sent by the system**. Four first touches were approved and
  sent by Kib in Apollo on 4 September, which is the design working rather than
  an exception to it. No agent has sent anything, and none can.
- Two test sequences are sitting in Apollo and can be deleted:
  `6a99df0f30072e001cd336c7` (empty, owned by Miguel, from the broken endpoint)
  and `6a9ad06e47812c0014788c89` (Kib's, two test drafts).
- One laptop, one operator, no version control, no backup.

## The rework, scoped 4 September

`docs/agent-architecture-rework.md`. One new agent, Account Context, with the
authority to stop a run, and the stop expressed as a required field on the
typed payload rather than a prompt rule. The Director loses its CRM reads. No
Fit Scoring, Briefing or Message agents: they share tools and stop conditions
with agents that exist, so they are checkpoints.

Phase 0 is plumbing, not agents, because six of the defects on 3 and 4 September
were tool contracts and parsers rather than topology, and two came from untested
scripts written by hand in a session.

**Phase 0 is done, 4 September.** `seats-drafts` replaces every hand-typed
script: `plan` prints what would be created and touches nothing, `write` creates
one paused sequence per person, `extend` adds later touches to sequences that
already exist. It uses the same parser as the mechanical checker, refuses a body
that carries the reviewer's check, and refuses to write without an OAuth token.
The old scripts are in `smoke-test/superseded-scripts/` with a note not to run
them. `tests/test_drafts.py` holds one assertion per defect that reached Apollo.

**Phase 1 is done, 4 September.** Account Context is built, advisory, and in
nobody's handoff graph. It holds no web search and no write of any kind, the
ledger included, and returns a typed `ContextVerdict` whose `searched` field is
required. Run it with `scripts/live_run.py context -f in.txt`; the launcher
writes the verdict to the outbox because the agent cannot.

Both live checks came back `conflict` on accounts the 4 September runs treated
as open ground, and the logs are in `smoke-test/phase-1/`. Wiley was found only
after the agent tried `wileyc.edu` and "Wiley College", which is the miss the
`searched` field exists to make visible. Wayne Young Jr. came back with his live
sequence and, more usefully, the mailbox it sends from: adelarosa@seatsone.com.
That mailbox is the whole reason it is a conflict rather than an active account,
because the HubSpot record is Kib's own.

Running the tools found three defects that reading them did not. The Apollo
read spoke only API key, and `APOLLO_API_KEY` has been blank since the OAuth
move, so the tool was dead on arrival while reporting something that looked like
a finding. The domain filter turned a hit into a silent miss. The no-match
branch dropped the coverage caveat, in the branch where it matters most. All
three are fixed and tested. **Run a new tool against a real account before
believing it**; two of these three are invisible to a unit test that mocks the
transport.

Note on coverage: the Apollo read prefers the workspace API key because it sees
colleagues' sequences, and falls back to Kib's OAuth token, which may not. When
the token answers, the output carries a caveat saying absence of a sequence is
weaker evidence than presence of one. Setting `APOLLO_API_KEY` would remove that
caveat, at the cost of reintroducing the identity that made writes land under
Miguel's name. It is safe for reads and only for reads.

**Phase 2 is done, 4 September.** The verdict binds. A director run refuses to
dispatch with no verdict, with a verdict for a different account, and on
`known_active` or `conflict` without `--override "reason"`. Enrolment refuses
when the person is in a live sequence, under the same override. The Director
holds no CRM read at all any more.

Kib settled three open decisions to get here: the Director loses every CRM read;
`conflict` refuses but is overridable per run with a recorded reason; the same
override covers enrolment. That last one makes double-sending reachable by a
single flag, which is deliberate and is why the enrolment refusal names the
sequences and spells out what proceeding means. Decisions 3 and 4 in the scope
are still open.

**The verdict is not on `WorkOrder`, and the scope said it should be.** The
scope's reasoning was that the Director cannot construct a passing verdict
because it holds no CRM tool. That is about obtaining evidence; typing JSON
needs no tool. A required field would have obliged the Director to write a
verdict rather than made it impossible. So it is stamped on `DispatchContext` by
the launcher, before the run. If someone later wants it on the payload, that is
a weakening, not a tidy-up.

Verified live on Wiley University, both directions, logs in
`smoke-test/phase-1/`. Two defects surfaced only by running it. The verdict was
stamped where the handoff filter reads it, and that filter builds the *worker's*
input, so the Director had a verdict and correctly said it had none. And the
three Apollo read tools had been dead since the OAuth move because they spoke
only API key; removing the key path fixed them, which is worth knowing if
anything in the logs before 4 September looks like Apollo was consulted.

**The Apollo API key is gone**, key and code path, on Kib's instruction. There
is no fallback identity anywhere now: the write tool used to fall back to the
key and announce whose account the sequence would land in, which was the honest
version of the wrong behaviour. Failing costs a re-login; the fallback cost an
afternoon and five sequences under Miguel Pescador's name.

**Phase 3 is done. Verified live 8 September.** `ReviewVerdict` replaces
prose: a finding must name a ground from the closed list and quote the failing
sentence, a rewrite with no finding is a validation error, and a ships with
findings is too. `reviewer_gate` refuses before the model call on anything the
counting settles.

The gate is confirmed live through the CLI. A batch with a duplicated subject
exits 4 with the finding printed and no model call, and the real 4 September
artifact passes cleanly, so it does not false-positive on genuine copy.

**The typed verdict ran live on 8 September and the schema held.** The
4 September attempt died on `organization_spend_limit_exceeded` before producing
anything; the block was the organisation's enforced spend limit, not the code.
Kib raised the limit from $15 to $30 on 8 September and the re-run returned a
valid `ReviewVerdict` first time. Log: `smoke-test/batch-1/reviewer-6.log`.

Verdict: `rewrite`, `batch_kind='cadence'`, one finding, ground
`cadence_additivity`, against Mary Ann Tietjen's touch 4, with the failing
sentence quoted and a required fix that names what has to become true. It
cleared the other three cadences by name and recorded a disagreement worth
keeping: the Campaign Builder had run a first-touch differentiation check on an
artifact that is a cadence, so its self-report never examined additivity at all.
`standard_problem` was null, so the Reviewer is no longer arguing with the
standard. Fix that one touch and the cadence ships.

`smoke-test/batch-1/gate-demo.txt` is a synthetic artifact, the real one with a
subject line duplicated, kept as the gate's evidence. Do not read it as copy
anyone wrote.

## Open decisions that are Kib's or leadership's, not yours

1. **M9 clinical placement.** The canonical layer names placement and rotation
   visibility; the US playbook forbids it. Guardrail stays in force. Do not
   resolve this by reading either document. See review item 1.
2. **The A-State three-year incumbent contract.** Heard of in 2025, never
   logged as a deal, no vendor named.
3. **The M9 segment call.** Community college allied health vs standalone
   nursing vs academic health centers. Lyon is a fourth shape none of them cover.
4. **Ratifying the owner-review rule** and who authorizes a re-approach after a
   closed-lost deal. Florida Atlantic is the live case.
5. **A governed home for the four US documents**, and a US regional account plan.
   Neither exists. See review items 2 and 3.
6. **OpenAI trace retention**, now that CRM notes flow through prompts.

## Enrolment, added 4 September

The agents could write a draft and not address it, so batch 1 sat in Apollo
enrolled to nobody and Apollo previewed each draft against an unrelated contact.
Kib's call: give the Campaign Builder enrolment. `tools/apollo_enrol.py`,
`add_contact_to_drafts`, on the Campaign Builder and nowhere else.

What holds it: enrolment is always `status: paused` and the schema has no status
field, one contact per call because a sequence is one cadence, OAuth only so a
draft is never queued under the workspace admin's name, and still no send,
approve or activate anywhere in the system. `tests/test_enrolment.py`.

## Apollo scopes, and the two shapes of a sequence

The token now carries seven scopes: `read_user_profile`,
`emailer_campaigns_create`, `emailer_campaigns_update`,
`emailer_campaigns_add_contact_ids`, `emailer_campaigns_search`,
`contacts_search`, `email_accounts_list`. Deliberately absent, and they should
stay absent: `emailer_messages_send_now`, `emailer_campaigns_approve`,
`emailer_messages_create`, and both purchase scopes, which spend money.

**A token carries the scopes the authorize request asked for, not the ones the
registration allows.** Widening the app in Apollo changed nothing until
`APOLLO_OAUTH_SCOPES` was widened too and the login run again. That cost a round
trip on 4 September.

**Create and update are different payloads.** `POST /sequences` takes steps with
no position and no ids. `PUT /sequences/{id}` wants a `position` on every step,
the `id` of any step and touch that already exists, and it replaces the step
list rather than appending, so touch 1 travels with the new ones or Apollo
deletes it. `update_payload` builds it; `sequence_payload` builds the create.
A create-shaped body sent to the update endpoint returns 422 "Missing Step 1",
which reads like a permissions problem and is not one.

## Batch 1, written to Apollo 4 September

`AI Agent Prospecting Ops | Batch 1, M3 student success, 4 Sep 2026`, id
`6a9adbb08bdf010020d0acc0`, owner Kib, paused, five manual-email steps.

Built from Kib's saved LinkedIn list. Six contacts in, three dropped at
checkpoints 1 and 2 for want of a qualifying trigger, five messages out across
Tulsa Community College, Creighton and Wiley. Logs in `smoke-test/batch-1/`,
copy in `lists/batch-1-copy-2026-09-04.md`.

- Mechanical check clean, except Wiley single threaded: Erinne Weber has no
  dated source.
- **Reviewer returned rewrite twice.** The rewrite fixed role differentiation;
  what remains is batch-wide architecture, every message running institutional
  news into an abstract transition into a generalized measurement problem.
- Kib's call: write them anyway and edit in Apollo. The Reviewer's verdict is a
  note on the batch, not a gate.
- Rewritten twice on 4 September. v1 put five people in one sequence, which
  would have sent each of them all five messages, and the last draft carried the
  differentiation check and a duplicate sign-off in its body. Both faults were
  in an ad hoc parser in a throwaway script; the write now uses
  `differentiation.parse_messages`, the same parser the checker uses.
- **Live as of 4 September, and checked again on 8 September:** four sequences
  of four manual-email touches at day 0, 14, 35 and 56, named for the recipient
  (`Dewayne Dickens | Tulsa Community College`) and grouped by the Apollo label
  `AI Agent Prospecting Ops` rather than a name prefix. Each has its one contact
  enrolled from kcochran@seatsone.com; the update preserved step 1 by id, so
  enrolments and their current step survived.
- **They read `active: true`, not paused, and touch 1 is delivered on all four.**
  Kib sent those four himself on the afternoon of 4 September. Opens recorded on
  Dickens, Tarver and Adams; Tietjen delivered, not opened. So the claim
  elsewhere that sequences sit paused describes how they are created, not how
  they stand. Every step is still `manual_email`, so touches 2 to 4 queue as
  tasks around 18 September and wait for a person; `overdue_manual_tasks_count`
  is 0 on all four.
- **Worth knowing for the next build:** a sequence does not stay paused once
  someone works its first task. If "paused" is meant to be a durable property
  rather than an initial state, nothing in this system enforces it, and the
  thing that actually holds is the step type.
- Touches 2 to 4 went through two more rewrite verdicts, which is four across
  the two batches. Squeezing one repeated construction produced another: the
  offer move became an imperative audit opening. Copy in
  `lists/batch-1-touches-2-to-4-2026-09-04.md`.
- Five v2 sequences were created, four of them extended. Wayne Young Jr. is held back:
  he is already in sequence `6a8899a4482aef0010cb668a` with activity on 3
  September.
- Contacts: two created, three already existed from CRM imports. The bulk create
  overwrote their titles, which is what that endpoint does.
- Wiley University is **not** net new. Apollo ties it to HubSpot company
  8675080439, owner Miguel Pescador.
- The deeper finding, worth acting on before batch 2: three of these triggers
  were awards and enrollment wins. An achievement is the wrong shape for a
  problem-led message, and no rewrite converts one into evidence of pain.
  Restricting batches to problem-shaped triggers is the fix the Reviewer keeps
  pointing at.

## Account state

All four accounts are owned by colleagues and sit at **Pending owner review**.
None is cleared to work. Nothing has been sent to any of them.

| Account | HubSpot id | Owner | Motion |
|---|---|---|---|
| Florida Atlantic | 8674812621 | Miguel Pescador | M3 |
| Arkansas State Jonesboro | 8675051709 | Cal O'Donovan | M2 |
| Lyon College | 8675146717 | Cal O'Donovan | M9, contested |
| ASU Mountain Home | 8675116475 | Cal O'Donovan | M9 pending segment call |

Stored as "ASUMH", not as any spelling of its full name. HubSpot matches company
names from the start of the string, which is why it read as absent three times.

## Operational notes that will save you an hour

- Tests and agent runs need the **Windows** venv (`.venv\Scripts\`). The Linux
  side of the bridge has no pytest and cannot reach the OpenAI API.
- PowerShell calls through the bridge time out around 60 seconds. Launch long
  runs with `Start-Process ... -WindowStyle Hidden` and redirect to a file, then
  poll. **Do not** use `-NoNewWindow`, which blocks, and do not pass multi-line
  strings through `-ArgumentList`, which splits on newlines. Write the input to a
  file and use `-RedirectStandardInput`.
- Two records from the 3 September loop test are pending relay: the UCA
  briefing (`...195021Z-prospect-briefing-university-of-central-arkansas.json`)
  and the Arkansas M1 segment
  (`...201343Z-campaign-builder-arkansas-public-universities-...json`). Both are
  from a test run. Decide before relaying.
- The outbox is otherwise clear except one held record. Cleared this evening:
  the 18:18 Lyon briefing was relayed as a third row
  (`https://app.notion.com/p/3d053f704a4581928008cc7724d571a3`) because it
  carries two facts the v2 row does not, the 2025-04-03 work-order note on prior
  satisfaction with Canvas and Jenzabar, and Kristi Price's appointment as Chief
  of Staff. The 18:12 campaign record moved to `superseded/`. The 19:00 campaign
  record is **held**, with the reason written into its `relay` block: its
  `what_this_suggests` is the two-lane play that failed review. Relay or
  supersede it once the copy question is settled.
- `device_bash` through the bridge cannot delete files. Moving an outbox record
  needs the Windows side (`.venv\Scripts\python.exe -c ...` through PowerShell).

## 8 September, and the outbox is further behind than this file said

**The OpenAI spend limit is an organisation setting and it was the whole
blocker.** Adding credits does not move it. Auto-reload is separately capped at
$10 a month, so topping up the balance cannot clear a reached limit either. The
limits page is the only place: platform.openai.com/settings/organization/limits,
the monthly budget field rather than the spend alerts, which are notifications
and gate nothing. Raised from $15 to $30 against $15.15 already spent.

`smoke-test/spend_probe.py` sends one tiny request and prints accepted or
refused with the reason. Run it before a long agent run rather than discovering
a 429 at the end of one.

**The OpenAI project is `proj_vRe6tfLNhApE4lkVaXnQZrO2`.** The key in `.env` is
project-scoped, and a project key cannot name its own organisation: OpenAI no
longer returns the `openai-organization` header for one. The limits page reports
the organisation as "Personal", usage tier 1, which is worth reconciling against
where this work is meant to be billed and where its traces are meant to live.
Open decision 6 on trace retention sits in whichever organisation that is.

**The outbox is empty. All ten records were relayed on 8 September.** The
operational note further up this file said one record was pending; it was
written on 3 September and had been stale ever since. Every hash verified
against `record_sha256` before transcription, and every page carries the relay
provenance section: outbox filename, hash, and that the hash verified.

| Record | Ledger page |
|---|---|
| 20260903T190022Z campaign SACSCOC | 3d553f704a4581169fefe79820ce6ed1 |
| 20260903T195021Z briefing UCA, first pass | 3d553f704a4581158244cb2ea477e6ef |
| 20260903T201343Z campaign Arkansas M1 | 3d553f704a45819dad2ff14dd3232cdc |
| 20260903T203243Z briefing UCA, re-run | 3d553f704a4581719593fd9ce9218014 |
| 20260904T152507Z campaign batch 1 | 3d553f704a4581d39cd1c8647f176e3e |
| 20260904T193136Z context, Shaniqua Adams | 3d553f704a4581368ccde86582750250 |
| 20260904T193521Z context, Wayne Young Jr., NTCC | 3d553f704a458133bc5bdbac6567f5ec |
| 20260904T193910Z context, Wayne Young Jr., Creighton | 3d553f704a45817a86c9fb6af2b722d7 |
| 20260904T200957Z briefing Wiley University | 3d553f704a45812cbd64c34cb6291ee2 |
| 20260908T195935Z review verdict | 3d553f704a458119bf09eb5ce86e0e8c |

Kib's call: relay all of them rather than supersede the two that were arguably
spent. The ledger is append-only at Draft, so a row read once and ignored costs
less than a gap in the record. Two rows carry a relay note saying what to make
of them: the SACSCOC record is history rather than a recommendation, because its
`what_this_suggests` is the two-lane play the recalibration did not adopt, and
the batch 1 record's `account_owner` says Wiley is not in HubSpot, which the
Account Context check later contradicted.

Files moved to `ledger-outbox/relayed/` with `relayed`, `relayed_at` and
`notion_page_url` filled in, and a `.pre-relay.json` copy of the original bytes
beside each, matching the convention already there. The mover is
`smoke-test/relay_move_2026-09-08.py`, written for this run and kept as the
record of what moved where. It is not tested tooling; if the relay becomes
routine it belongs in the package as a command, the way `seats-drafts` replaced
the hand-written write scripts.

**The empty `target_database_id` is fixed.** `write_review_verdict` read
`NOTION_REVIEW_DB_ID` with no fallback, so an unset variable produced `""` and a
relay with nowhere to write. Context verdicts already fell back to the ledger
database; review verdicts now do too. `tests/test_review_verdict.py` pins both
the fallback and the precedence of a dedicated review database when one is set.
Kib's call, 8 September: review verdicts are ledger rows.

**Two things the relay changed outside the outbox, both worth knowing.** The
`Source agent` select held only Prospect Briefing and Campaign Builder, and
Notion refuses a select value that is not an option, so `Account Context` and
`Reviewer` were added to it. That is a schema change to the ledger, made on
Kib's instruction. The Notion DDL that adds options replaces the whole property
definition, which cleared that property's description; it read "Which agent
emitted this record" and needs retyping in Notion, because the update tool
cannot set a property description.

Context and review verdicts have no Motion, Segment, Outreach decision or
Verified through, so those properties are empty on their five rows rather than
invented. If the ledger should distinguish record kinds structurally rather than
by an empty column, that is a schema decision and it is open.

## `seats-status`, added 8 September

Kib's ask, after the four-sent-emails discrepancy: something that says which
steps of the process are done, during and after a run.

Built as a read of the systems rather than a run manifest, and that was his
call. A manifest is a self-report, and the self-report has a record here: the
differentiation check passed itself on every question for four batches running
while the Reviewer disagreed on every question. So `seats-status` asks the
outbox and Apollo what is true now.

    .venv\Scripts\python.exe -m seats_prospecting.status
    seats-status --no-apollo      # local sources only, Apollo reported unavailable
    seats-status --exit-code      # exit 1 when anything is outstanding, for a script

It prints only what is waiting on a person: records still in the outbox,
records at Pending owner review, the rewrite the last verdict on record asked
for, records past the ledger's own 30-day re-verification rule, sequences that
are active when the records say paused, and overdue manual tasks. Then a
COVERAGE block saying what it read and what it could not.

**It never reports a clean result for a source it could not reach.** That is the
one rule worth keeping if the rest is rewritten. An unreachable source goes in
its own UNAVAILABLE section with the exception text, and no count is claimed for
it. `tests/test_status.py` has more tests about that than about the reporting.

**Two things running it found immediately, which is the argument for it.**

1. The name-convention filter matched `UK FE - Timetabling, Scheduling &
   Attendance | Outbound Campaign`, an unrelated sequence with 249 delivered
   emails, because that name also contains " | ". `APOLLO_SEQUENCE_LABEL_ID` is
   now set in `.env` to 6a9ae82e07992400102bce22 and the filter is exact. The
   fallback still exists and still says which filter it used, because a filter
   that silently excludes what you are looking for is how Wayne Young Jr.'s live
   sequence read as a clean account.
2. **Apollo tasks return 403.** The token has no task scope, so due and overdue
   manual tasks cannot be read and are reported unavailable rather than zero.
   Fixing it means widening the app registration in Apollo, widening
   `APOLLO_OAUTH_SCOPES` to match, and running `seats-apollo-login` again: a
   token carries the scopes the authorize request asked for, not the ones the
   registration allows. Until then the task half of this command is blind and
   says so.

Current output, 8 September: 27 outstanding. Seven records at Pending owner
review, one rewrite finding (Mary Ann Tietjen's touch 4), fifteen records past
the 30-day rule, four sequences active with touch 1 delivered.

**What it deliberately does not do.** It does not read Notion, so a decision you
make in the ledger is invisible to it and every run says so in the coverage
block. It does not know whether a rewrite finding was fixed, because nothing
records that; it reports what the last verdict asked for. And it does not track
what happened inside a run. If that is wanted later, the useful shape is a
manifest plus this command flagging where the two disagree, which is the option
Kib did not take today.


## Batch 2, colleague-owned M3 accounts overridden for research only, 9 September

Batch 2 hit the same wall phase 2 was built for: of 40 fresh LinkedIn saves
screened against HubSpot (`lists/linkedin-saved-2026-09-09.csv`), 20 belong to
a colleague, 14 have unresolved employers, 3 are already in batch 1, and only
2 are genuinely net new, neither M3. Kib's call: proceed on four colleague-owned
M3 accounts anyway, override scoped to research only, matching the Wiley
precedent from 4 September exactly. No outreach, no Apollo write, no enrolment.

The four: Veronica D. Wilson (Saginaw Valley State, owner Cal O'Donovan),
Michael Frye (Southern Virginia University, owner Miguel Pescador), Franklin
Ard (Kennesaw State, owner Miguel Pescador), Jason Schwass (University of
Scranton, owner Cal O'Donovan). All lead stage, zero open deals. Live
`context` then `director --verdict ... --override "Kib override, prospecting
research only"` for each. Logs in `smoke-test/batch-2-override/`.

All four context checks came back `conflict`, correctly. All four director
runs dispatched to Prospect Briefing only, correctly refused to draft
anything cleared to send, and named the account owner as the recommended
first reviewer. Every briefing recommends M3 as the best-supported motion;
none is a qualified opportunity yet, all still need internal buy-in from the
account owner before any outreach.

Two things worth knowing before you touch this batch again:

- The first Scranton `context` run landed in the middle of a HubSpot 429 and
  an Apollo 403 and came back `net_new` — wrong, and it would have skipped
  the override path entirely had it been used. The retry a few minutes later,
  clean of rate limiting, correctly returned `conflict`. The bad record is
  still sitting in the outbox as
  `20260909T164313Z-context-net-new-jason-schwass.json`. Do not relay it;
  it should be deleted or clearly marked superseded before the next relay
  pass. This is the same class of problem `seats-status` exists to catch on
  the output side; it doesn't cover a transient bad verdict on the input
  side, so this one had to be caught by rerunning and comparing.
- Three of the four briefings wrote a ledger record (Saginaw, Southern
  Virginia, Scranton). Kennesaw's did not call `create_ledger_record` at
  all; nothing in the trail explains why, and the run otherwise looks
  identical in shape to the other three. Worth a look if it happens again.

None of these seven ledger records (4 context verdicts, 3 briefings, minus
the one bad net_new) have been relayed to Notion yet. That's the next step
if this batch is worth taking further.
