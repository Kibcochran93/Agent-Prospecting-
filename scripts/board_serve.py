"""seats-board-serve: the board, with the decision buttons live.

Why a server and not the static file. A file:// page has nothing to POST to and
a browser will not launch a local script from a link, so a clickable approval
needs something listening. This binds to 127.0.0.1 only.

What a click does, and does not do:

    does      append one JSON file to decisions/
    does not  edit or delete any earlier decision
    does not  touch Apollo: no send, no enrol, no activate, no sequence write
    does not  touch Notion
    does not  run an agent

So the button records that a person cleared an account. The sending is still a
person's job in Apollo, which is the same guarantee the rest of the system
keeps: nothing here can send, and that is a missing capability rather than a
rule. This module imports no HTTP client and no connector, and
tests/test_board_serve.py asserts that.

The run-time approval gate in runner.py is deliberately NOT exposed here. That
halt is the one control sitting outside the model's reach, the process has
already exited by the time this page is open, and a second approval surface in
front of it would weaken it rather than help.

    .venv\\Scripts\\python.exe scripts\\board_serve.py
    http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import board  # noqa: E402
import next as next_cli  # noqa: E402
import run_queue  # noqa: E402
from seats_prospecting import briefing_queue as briefings  # noqa: E402
from seats_prospecting import decisions  # noqa: E402
from seats_prospecting import job_queue as jobs  # noqa: E402
from seats_prospecting import next_step  # noqa: E402
from seats_prospecting import read_proxy  # noqa: E402

OPERATOR = "Kib Cochran"

# Guards /api/state, /queue and /decide. Empty means those three routes are
# refused outright rather than silently left open -- an unset token is not
# the same as "no auth needed", and the difference matters the moment this
# server sits behind a tunnel instead of only 127.0.0.1. Set from .env via
# `settings`, loaded in main() before the server starts.
BRIDGE_TOKEN = os.environ.get("SEATS_BRIDGE_TOKEN", "").strip()


DASH_CSS = """
.wrap { max-width: 1100px; }
.counts { display: flex; gap: 10px; flex-wrap: wrap; margin: 4px 0 22px; }
.count { flex: 1 1 150px; padding: 12px 14px; border-radius: 6px; background: #fff;
  border: 1px solid #e4e4e2; text-decoration: none; color: inherit; }
.count b { display: block; font-size: 26px; line-height: 1.1; }
.count span { font-size: 12px; color: #666; }
.count.you { border-left: 3px solid #b3761a; }
.count.ready { border-left: 3px solid #2f6b3f; }
.count.them { border-left: 3px solid #55606f; }
.count.fault { border-left: 3px solid #a33; }
section { margin: 0 0 26px; }
section > h2 { font-size: 15px; margin: 0 0 4px; }
section > p.why { margin: 0 0 12px; color: #666; font-size: 13px; }
.card { background: #fff; border: 1px solid #e4e4e2; border-radius: 6px;
  padding: 12px 14px; margin-bottom: 8px; display: flex; gap: 14px;
  align-items: flex-start; }
.card .who { flex: 1 1 auto; }
.card .name { font-weight: 600; }
.card .ask { color: #444; font-size: 13px; margin-top: 3px; }
.card .meta { color: #888; font-size: 12px; margin-top: 4px; }
.card .act { flex: 0 0 auto; display: flex; gap: 6px; align-items: center; }
.btn { font: inherit; padding: 5px 12px; border-radius: 4px; border: 1px solid #cfcfcb;
  background: #fff; cursor: pointer; }
.btn.ok:hover { background: #e8f3ea; border-color: #7fae8c; }
.btn.no:hover { background: #f6e7e7; border-color: #c49a9a; }
.btn.run { border-color: #7fae8c; background: #f2f8f3; }
.btn.run:hover { background: #e2f0e6; }
.btn[disabled] { opacity: .5; cursor: default; }
.muted { color: #777; font-size: 12px; }
details.grid { margin-top: 30px; }
details.grid summary { cursor: pointer; font-size: 13px; color: #555; }
a.out { font-size: 12px; }
"""



SCRIPT = """
<script>
document.addEventListener('click', async function (event) {
  const btn = event.target.closest('.btn');
  if (!btn) return;
  const isRun = btn.classList.contains('run');
  const isOwner = btn.dataset.kind === 'owner_review';
  const note = isRun
    ? (prompt('Note, recorded with the job. Optional.') || '')
    : isOwner
      ? (prompt('How did ' + (btn.dataset.owner || 'the owner')
          + ' tell you? Recorded with their answer.') || '')
      : (prompt('Reason, recorded with the decision. Optional.') || '');
  btn.disabled = true;
  const said = document.getElementById('said');
  said.textContent = isRun ? 'Queueing...' : 'Recording...';
  try {
    const resp = await fetch(isRun ? '/queue' : '/decide', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        account_key: btn.dataset.key,
        account: btn.dataset.account,
        decision: btn.dataset.decision,
        kind: btn.dataset.kind,
        owner: btn.dataset.owner,
        stage: btn.dataset.stage,
        note: note
      })
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || 'refused');
    said.textContent = (isRun ? 'Queued: ' : 'Recorded: ') + data.file + '. Reloading.';
    setTimeout(function () { location.reload(); }, 700);
  } catch (err) {
    btn.disabled = false;
    said.textContent = 'Not done: ' + err.message;
  }
});
</script>
"""


def _record_said(acct) -> str | None:
    """What the agent wrote into the newest briefing record for this account."""
    said = [e["decision"] for e in acct.briefing if e.get("decision")]
    return said[-1] if said else None


def _facts(accounts, standing, flags, copy_cache, owners=None):
    from seats_prospecting import next_step as ns

    owners = owners or {}
    out = {}
    for key, acct in accounts.items():
        held = standing.get(key)
        review = owners.get(key)
        copy_files = next_cli.copy_files_for(acct.display, key, copy_cache)
        if not copy_files:
            # lists/*.md is the original, hand-maintained copy store -- its
            # newest file is dated 4 September. Every real COPY run since has
            # gone through run_queue.py instead, which copy_files_for() never
            # looked at, so an account could finish a real COPY job and still
            # report "no copy exists" forever. ADR 0006. Existence only, same
            # standard copy_files_for() already applied ("evidence copy
            # exists, not proof the copy is any good"): a checkpoint-only log
            # still refuses cleanly at SEQUENCE's own extraction step
            # (ADR 0003), so nothing unsafe slips through by counting it here.
            queued_copy = run_queue._newest_copy_job(key, jobs.queue_dir())
            if queued_copy:
                copy_files = [f"queue: {queued_copy.log}"]
        out[key] = ns.stage_for(
            ns.Facts(
                account=acct.display,
                decision=held.decision if held else None,
                owner_review=review.decision if review else None,
                account_owner=acct.owner or "",
                inbound_person=acct.intent.person if acct.intent else "",
                inbound_last_visit=acct.intent.last_visit if acct.intent else "",
                inbound_visits=acct.intent.total_visits if acct.intent else 0,
                has_records=bool(acct.briefing or acct.context or acct.review),
                copy_files=copy_files,
                sequences=acct.sequences,
                apollo_read=flags["sequences_read"],
                tasks_read=flags["tasks_read"],
            )
        )
    return out


def _card(acct, key, ask, meta, actions) -> str:
    return (
        '<div class="card"><div class="who">'
        f'<div class="name">{html.escape(acct.display)}</div>'
        f'<div class="ask">{ask}</div>'
        f'<div class="meta">{meta}</div></div>'
        f'<div class="act">{actions}</div></div>'
    )


def _decide_buttons(acct, key, kind="kib_decision") -> str:
    labels = (("Approved", "ok"), ("Declined", "no"))
    prefix = "Owner said " if kind == "owner_review" else ""
    return "".join(
        f'<button class="btn {cls}" data-key="{html.escape(key, quote=True)}" '
        f'data-account="{html.escape(acct.display, quote=True)}" '
        f'data-kind="{kind}" data-owner="{html.escape(acct.owner or "", quote=True)}" '
        f'data-decision="{label}">{prefix}{"yes" if cls == "ok" else "no"}'
        f'{"" if prefix else " (" + label + ")"}</button>'
        for label, cls in labels
    )


def build_page(host: str) -> str:
    """Gather the facts, sort them by who owes the next move, hand to dashboard."""
    from seats_prospecting import next_step as ns

    import dashboard

    generated = datetime.now(timezone.utc)
    accounts: dict[str, board.Account] = {}
    notes: list[str] = []
    unavailable: list[str] = []
    defects: list[str] = []
    extras: list[dict] = []
    board.collect_records(board._outbox(), accounts, notes, extras)
    intent = board.collect_intent(accounts, notes, defects)
    board.collect_logs(ROOT, accounts, notes)
    flags = board.collect_apollo(accounts, notes, unavailable, defects)
    notes.append(f"standing decisions in {decisions.decisions_dir()}")

    copy_cache = {}
    if (ROOT / "lists").is_dir():
        for path in sorted((ROOT / "lists").glob("*.md")):
            try:
                copy_cache[path.name] = path.read_text(encoding="utf-8-sig")
            except OSError:
                continue

    standing = decisions.current()
    owners = decisions.owner_reviews()
    steps = _facts(accounts, standing, flags, copy_cache, owners)

    today = generated.date().isoformat()
    all_jobs = jobs.all_jobs()
    jobs_today = {j.account_key: j for j in all_jobs if j.queued_at[:10] == today}
    spent_today = len([
        j for j in all_jobs
        if j.queued_at[:10] == today and j.state in (jobs.QUEUED, jobs.RUNNING, jobs.DONE)
    ])
    budget_left = max(0, jobs.MAX_PER_DAY - spent_today)
    waiting = len([j for j in all_jobs if j.state == jobs.QUEUED])

    buckets = {"yours": [], "ready": [], "ask": [], "cleared": [], "running": [],
               "resting": [], "inbound": []}
    resolutions = {}
    disagreements = []
    for key, acct in sorted(accounts.items(), key=lambda kv: kv[1].display.lower()):
        step = steps[key]
        held = standing.get(key)
        job = jobs_today.get(key)
        res = decisions.resolve(_record_said(acct), kib=held, owner=owners.get(key),
                                account_owner=acct.owner or "")
        resolutions[key] = res
        if res.disagreement:
            disagreements.append((acct.display, res.disagreement))
        item = (key, acct, step, job if job else held)
        if job and job.state in (jobs.QUEUED, jobs.RUNNING, jobs.FAILED, jobs.REFUSED):
            buckets["running"].append((key, acct, step, job))
        elif step.stage == ns.INBOUND:
            buckets["inbound"].append((key, acct, step, held))
        elif step.stage == ns.INBOUND:
            inbound.append((key, acct, step, held))
        elif step.stage == ns.ASK_OWNER:
            buckets["ask"].append((key, acct, step, held))
        elif step.stage in (ns.SEND, ns.ENROL):
            buckets["yours"].append((key, acct, step, held))
        elif step.stage == ns.UNDECIDED and owners.get(key):
            buckets["cleared"].append((key, acct, step, held))
        elif step.stage == ns.UNDECIDED:
            buckets["yours"].append((key, acct, step, held))
        elif step.automatable and not job:
            buckets["ready"].append((key, acct, step, held))
        else:
            buckets["resting"].append((key, acct, step, job))

    grid = board.render(accounts, extras, notes, defects, unavailable, generated,
                        generated.date())
    appendix = grid[grid.index('<p class="key">'):grid.index("</body>")]

    return dashboard.page(
        generated=generated, accounts=accounts, steps=steps, standing=standing,
        owners=owners, resolutions=resolutions, jobs_mod=jobs, all_jobs=all_jobs,
        budget_left=budget_left, waiting=waiting, defects=defects,
        unavailable=unavailable, disagreements=disagreements, buckets=buckets,
        appendix=appendix, bridge_token=BRIDGE_TOKEN,
    )


def _state_json() -> dict:
    """Read-only JSON snapshot of the same facts build_page renders, shaped as
    data instead of HTML. No write, no side effect. Built for the bridge (and
    anything else that wants the board's state without parsing HTML) rather
    than for a human, so it stays a plain dict of primitives.

    Deliberately narrower than build_page's own gathering: it skips the
    engagement charts and the raw grid appendix, which are display concerns,
    and keeps only what a caller needs to decide what to do next or queue a
    job. If the two ever need to share more, factor build_page's gathering out
    into this function rather than widen this one to match build_page's shape.
    """
    from seats_prospecting import next_step as ns

    accounts: dict[str, board.Account] = {}
    notes: list[str] = []
    unavailable: list[str] = []
    defects: list[str] = []
    extras: list[dict] = []
    board.collect_records(board._outbox(), accounts, notes, extras)
    board.collect_intent(accounts, notes, defects)
    board.collect_logs(ROOT, accounts, notes)
    flags = board.collect_apollo(accounts, notes, unavailable, defects)

    copy_cache = {}
    if (ROOT / "lists").is_dir():
        for path in sorted((ROOT / "lists").glob("*.md")):
            try:
                copy_cache[path.name] = path.read_text(encoding="utf-8-sig")
            except OSError:
                continue

    standing = decisions.current()
    owners = decisions.owner_reviews()
    steps = _facts(accounts, standing, flags, copy_cache, owners)

    # Declined kib_decisions, by account_key. Exposed so an Overlooked lead --
    # read fresh from Apollo/HubSpot on every load, with no record in
    # `accounts` at all -- can be permanently dismissed and stay dismissed.
    # The write already goes through /decide into decisions/, same as every
    # other Declined; this is only the read side, so a dismissed lead does not
    # need to be re-declined by hand on every refresh.
    declined_keys = sorted(k for k, d in standing.items() if d.decision == "Declined")

    today = datetime.now(timezone.utc).date().isoformat()
    all_jobs = jobs.all_jobs()
    jobs_today = {j.account_key: j for j in all_jobs if j.queued_at[:10] == today}

    # Any job still queued or running for an account, from any day -- not just
    # today. job_queue.enqueue()'s own "one unrun job per account" guard looks
    # exactly this way; the dashboard has to agree with it, or an account
    # queued days ago and never run keeps showing as "waiting on a run" with a
    # Queue button that only fails. This is the read side of that same guard.
    unrun_by_account = {j.account_key: j for j in all_jobs if j.state in (jobs.QUEUED, jobs.RUNNING)}
    spent_today = len([
        j for j in all_jobs
        if j.queued_at[:10] == today and j.state in (jobs.QUEUED, jobs.RUNNING, jobs.DONE)
    ])
    budget_left = max(0, jobs.MAX_PER_DAY - spent_today)

    pending = []
    queueable = []
    context_notes = []
    new_contacts = []
    for key, acct in sorted(accounts.items(), key=lambda kv: kv[1].display.lower()):
        step = steps[key]
        # A DONE job only ever blocks the stage it was actually queued for.
        # jobs_today alone used to treat any job from today as still
        # blocking, done or not -- so a finished COPY job kept hiding the
        # very next stage's row (SEQUENCE) once the account correctly moved
        # past COPY. Only a queued or running job is still in the way;
        # jobs_today is display-only now, for job_state.
        blocking_job = unrun_by_account.get(key)
        display_job = blocking_job or jobs_today.get(key)
        row = {
            "account_key": key,
            "institution": acct.display,
            "motion": acct.motion or "",
            "account_owner": acct.owner or "",
            "stage": step.stage,
            "waiting_on": step.waiting_on,
            "blocker": step.blocker,
            "automatable": step.automatable,
            "job_state": display_job.state if display_job else None,
        }
        if step.stage in (ns.UNDECIDED, ns.ASK_OWNER):
            pending.append(row)
        elif step.automatable and not blocking_job:
            # Approved, and waiting on a COPY or SEQUENCE run. Separate from
            # `pending` deliberately: these need a job queued, not a decision.
            queueable.append(row)
        for e in acct.context[-3:]:
            context_notes.append({
                "account_key": key,
                "institution": acct.display,
                "written_at": e.get("written_at", ""),
                "file": e.get("file", ""),
                "relayed": e.get("relayed", False),
            })

        # A fresh contact's briefing has no relation to this account's own
        # stage (above) -- an Approved, already-sequenced account can still
        # owe a decision on someone new. ADR 0005.
        decided_hashes = decisions.decided_artifact_hashes(key)
        for contact in ns.undecided_briefings(acct.briefing, decided_hashes):
            new_contacts.append({
                "account_key": key,
                "institution": acct.display,
                "motion": acct.motion or "",
                "person": contact.person,
                "what_this_suggests": next(
                    (b.get("what_this_suggests", "") for b in acct.briefing
                     if b.get("artifact_sha256") == contact.artifact_sha256),
                    "",
                ),
                "artifact_sha256": contact.artifact_sha256,
                "file": contact.file,
                "written_at": contact.written_at,
            })

    context_notes.sort(key=lambda r: r["written_at"], reverse=True)
    new_contacts.sort(key=lambda r: r["written_at"], reverse=True)

    briefings_used = briefings.today_count()
    briefings_left = max(0, briefings.MAX_PER_DAY - briefings_used)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pending": pending,
        "queueable": queueable,
        "declined_keys": declined_keys,
        "new_contacts": new_contacts,
        "context_notes": context_notes[:30],
        "budget": {
            "used": spent_today, "max": jobs.MAX_PER_DAY, "left": budget_left,
        },
        "briefing_budget": {
            "used": briefings_used, "max": briefings.MAX_PER_DAY, "left": briefings_left,
        },
        "queue_waiting": len([j for j in all_jobs if j.state == jobs.QUEUED]),
        "unavailable": unavailable,
        "defects": defects,
        "note": (
            "account_context verdicts no longer carry a blocking 'conflict' "
            "status as of ADR 0004 (15 Sep 2026); context_notes is informational."
        ),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "seats-board"

    def log_message(self, fmt, *args):  # quieter console
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, content_type: str, cors: bool = False) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if cors:
                self._cors_headers()
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            # The browser went away mid-write, which is what a reload after a
            # POST looks like. The request already succeeded; a stack trace here
            # would say otherwise.
            pass

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin", "*")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Vary", "Origin")

    def do_OPTIONS(self) -> None:
        """Preflight for the three bridge routes. No auth check here -- a
        preflight carries no Authorization header by design, only the actual
        request does, and that request still hits _authorized() on its own
        do_GET/do_POST branch. Answering every OPTIONS the same way (allowed,
        no data) rather than only for known paths keeps this branch from
        needing to track the route list twice.
        """
        self.send_response(204)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _authorized(self) -> bool:
        """Bearer token check for /api/state, /queue, /decide.

        An empty BRIDGE_TOKEN refuses everything on these three routes rather
        than waving requests through: the page at / still works with no token
        set (it always has), but nothing that can queue a job or record a
        decision responds until SEATS_BRIDGE_TOKEN is actually configured.
        """
        if not BRIDGE_TOKEN:
            return False
        got = self.headers.get("Authorization", "")
        return got == f"Bearer {BRIDGE_TOKEN}"

    def _refuse_unauthorized(self) -> None:
        self._send(401, json.dumps({"error": "missing or wrong bearer token"}).encode(),
                   "application/json", cors=True)

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path in ("/api/state",) and not self._authorized():
            self._refuse_unauthorized()
            return
        if path == "/api/state":
            body = json.dumps(_state_json()).encode("utf-8")
            self._send(200, body, "application/json", cors=True)
            return
        if path not in ("/", "/index.html"):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        page = build_page(self.headers.get("Host", "127.0.0.1"))
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

    def _queue(self, payload: dict) -> None:
        """Enqueue one job. The caps and the stage set are enforced in job_queue."""
        try:
            job = jobs.enqueue(
                account=str(payload.get("account", "")),
                account_key=str(payload.get("account_key", "")),
                stage=str(payload.get("stage", "")),
                queued_by=OPERATOR,
                command=["run_queue.py"],
                note=str(payload.get("note", "")),
            )
        except jobs.QueueRefused as exc:
            self._send(409, json.dumps({"error": str(exc)}).encode(), "application/json", cors=True)
            return
        self._send(200, json.dumps({"file": job.id}).encode(), "application/json", cors=True)

    def _briefing(self, payload: dict) -> None:
        """Run one Prospect Briefing synchronously and hand back its output.

        Blocks for as long as the underlying agent run takes -- minutes, the
        same as every other run in this system -- which is why this matters
        on ThreadingHTTPServer specifically: one slow briefing must not stall
        /api/state or a second briefing for anyone else looking at the board
        at the same time. The cap is checked and the run is reserved together
        in briefing_queue.reserve(), under its own lock, before any money is
        spent; the subprocess only starts once that reservation succeeds.
        """
        target = str(payload.get("target", "")).strip()
        if not target:
            self._send(400, json.dumps({"error": "target is required"}).encode(),
                       "application/json", cors=True)
            return
        try:
            record_path = briefings.reserve(target, requested_by=OPERATOR)
        except briefings.BriefingRefused as exc:
            self._send(409, json.dumps({"error": str(exc)}).encode(), "application/json", cors=True)
            return
        log_path = ROOT / "queue" / "logs" / f"{record_path.stem}.log"
        cmd = [
            str(ROOT / ".venv" / "Scripts" / "python.exe"),
            "scripts/live_run.py", "briefing", "-i", target, "-o", str(log_path),
        ]
        try:
            result = subprocess.run(
                cmd, cwd=str(ROOT), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=900,
            )
            exit_code = result.returncode
            output = result.stdout
            if exit_code != 0 and result.stderr:
                output = output + "\n\nSTDERR:\n" + result.stderr
        except subprocess.TimeoutExpired:
            exit_code = -1
            output = "Timed out after 15 minutes. Check queue/logs/ for a partial log."
        briefings.finish(record_path, exit_code=exit_code, output=output)
        self._send(
            200,
            json.dumps({
                "file": record_path.name,
                "exit_code": exit_code,
                "output": output,
            }).encode("utf-8"),
            "application/json", cors=True,
        )

    def _run_queue(self, payload: dict) -> None:
        """Run one queued job synchronously through run_queue.py, and hand back
        what happened.

        Same shape as /briefing: blocks for as long as the run takes (minutes),
        which matters on ThreadingHTTPServer for the same reason it does there.
        This exists because queueing a job (POST /queue) only ever writes a job
        file -- nothing has ever automatically run the queue, which is exactly
        what made an account look "waiting on a run" with a Queue button that
        would only fail, days after it was actually queued. A click here is
        exactly `run_queue.py`, run once, from the same launcher and cwd as
        every other run in this system, so it leaves the same log and moves
        the same job from queue/ to queue/done/. One job per click, matching
        run_queue.py's own default (no --loop): the person watching decides
        whether to click again, rather than the whole day's cap draining
        unattended behind one button press.
        """
        cmd = [
            str(ROOT / ".venv" / "Scripts" / "python.exe"),
            "scripts/run_queue.py",
        ]
        try:
            result = subprocess.run(
                cmd, cwd=str(ROOT), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=900,
            )
            exit_code = result.returncode
            output = result.stdout
            if exit_code != 0 and result.stderr:
                output = output + "\n\nSTDERR:\n" + result.stderr
        except subprocess.TimeoutExpired:
            exit_code = -1
            output = "Timed out after 15 minutes. Check queue/logs/ for a partial log."
        self._send(
            200,
            json.dumps({"exit_code": exit_code, "output": output}).encode("utf-8"),
            "application/json", cors=True,
        )

    def _read_proxy(self, path: str, payload: dict) -> None:
        """Forward one named Apollo/HubSpot read call to the real API.

        This exists only so the local dashboard -- a plain file with no
        window.claude -- can reach the same handful of read calls the
        published Artifact makes through the MCP connector. See
        read_proxy.py for exactly which tool names are recognised; anything
        else is a 400, not a guess.
        """
        tool = str(payload.get("tool", ""))
        body = payload.get("input") or {}
        caller = read_proxy.apollo_call if path == "/api/apollo" else read_proxy.hubspot_call
        try:
            result = caller(tool, body)
        except read_proxy.ProxyError as exc:
            self._send(exc.status, json.dumps({"error": str(exc)}).encode(), "application/json", cors=True)
            return
        except Exception as exc:  # noqa: BLE001 - surface it to the panel, don't 500 silently
            self._send(502, json.dumps({"error": str(exc)}).encode(), "application/json", cors=True)
            return
        self._send(200, json.dumps(result).encode("utf-8"), "application/json", cors=True)

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path in ("/queue", "/decide", "/briefing", "/run-queue", "/api/apollo", "/api/hubspot") \
                and not self._authorized():
            self._refuse_unauthorized()
            return
        if path in ("/api/apollo", "/api/hubspot"):
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError) as exc:
                self._send(400, json.dumps({"error": str(exc)}).encode(), "application/json", cors=True)
                return
            self._read_proxy(path, payload)
            return
        if path == "/briefing":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError) as exc:
                self._send(400, json.dumps({"error": str(exc)}).encode(), "application/json", cors=True)
                return
            self._briefing(payload)
            return
        if path == "/run-queue":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError) as exc:
                self._send(400, json.dumps({"error": str(exc)}).encode(), "application/json", cors=True)
                return
            self._run_queue(payload)
            return
        if path == "/queue":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError) as exc:
                self._send(400, json.dumps({"error": str(exc)}).encode(), "application/json", cors=True)
                return
            self._queue(payload)
            return
        if path != "/decide":
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(400, json.dumps({"error": str(exc)}).encode(), "application/json", cors=True)
            return
        try:
            kind = str(payload.get("kind") or decisions.KIB_DECISION)
            owner = str(payload.get("owner") or "").strip()
            if kind == decisions.OWNER_REVIEW and not owner:
                raise decisions.DecisionRefused(
                    "An owner review needs a named owner. Nothing recorded."
                )
            path = decisions.record(
                account=str(payload.get("account", "")),
                account_key=str(payload.get("account_key", "")),
                decision=str(payload.get("decision", "")),
                kind=kind,
                decided_by=owner if kind == decisions.OWNER_REVIEW else OPERATOR,
                recorded_by=OPERATOR,
                source=(
                    f"reported by {owner}, recorded by {OPERATOR} on the board"
                    if kind == decisions.OWNER_REVIEW
                    else "board button at 127.0.0.1"
                ),
                note=str(payload.get("note", "")),
                # ADR 0005: blank means this decision is the account-level
                # Approved/Declined as before. Non-blank scopes it to one
                # contact's briefing, independent of the account's own stage.
                artifact_sha256=str(payload.get("artifact_sha256", "")),
            )
        except decisions.DecisionRefused as exc:
            self._send(409, json.dumps({"error": str(exc)}).encode(), "application/json", cors=True)
            return
        self._send(200, json.dumps({"file": path.name}).encode(), "application/json", cors=True)


def main(argv: list[str] | None = None) -> int:
    from seats_prospecting import settings  # noqa: F401  loads .env

    parser = argparse.ArgumentParser(prog="seats-board-serve")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"http://127.0.0.1:{args.port}   (local only, Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
