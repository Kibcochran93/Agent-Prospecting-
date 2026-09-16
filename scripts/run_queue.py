"""seats-run-queue: pick up one queued job, run it, record what happened.

Separate from the board on purpose. A run takes minutes, a browser POST does
not, and a run launched from an HTTP handler leaves no log anyone keeps. This
calls the same launcher every other run uses, so a queued run produces the same
TRAIL, AUDIT and OUTPUT as one Kib starts by hand.

What it will not do:

  - It never passes --approve-interactively, so a queued run rejects every
    approval interruption. A job cannot approve an Apollo write on Kib's behalf.
  - It runs one job at a time. Concurrency is what defeated the reveal cap.
  - It sets APOLLO_REVEAL_CAP to the job's own cap, so the .env value of 25,
    which is a session ceiling, cannot become a per-job budget.
  - It stops if PAUSED exists, checked before every job rather than once.
  - It enforces the daily ceiling on runs, not just on clicks. Nine jobs
    queued against a budget of five run five today and stay queued after.

    .venv\\Scripts\\python.exe scripts\\run_queue.py            # one job
    .venv\\Scripts\\python.exe scripts\\run_queue.py --loop     # until empty
    .venv\\Scripts\\python.exe scripts\\run_queue.py --dry-run  # print, run nothing
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from seats_prospecting import job_queue as q  # noqa: E402
from seats_prospecting.next_step import COPY, INBOUND, SEQUENCE  # noqa: E402

LOGS = ROOT / "queue" / "logs"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def briefing_for(account_key: str) -> dict | None:
    """The most recent briefing record for this account, as the run's input.

    Read from the relayed outbox rather than from Notion, and from the record
    rather than from a log, so the input to a queued run is the same object the
    ledger holds.
    """
    import board

    accounts: dict[str, board.Account] = {}
    board.collect_records(board._outbox(), accounts, [], [])
    acct = accounts.get(account_key)
    if not acct or not acct.briefing:
        return None
    newest = sorted(acct.briefing, key=lambda e: e["written_at"])[-1]
    for directory in (board._outbox() / "relayed", board._outbox()):
        path = directory / newest["file"]
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8-sig"))
    return None


def copy_input(job: q.Job) -> tuple[str | None, str]:
    """Build the Campaign Builder's input from the briefing record.

    Returns (text, why_not). The facts travel verbatim; nothing is summarised
    into the prompt, because a paraphrase of an evidence line is how an
    unsourced claim gets into copy.
    """
    envelope = briefing_for(job.account_key)
    if envelope is None:
        return None, (
            f"No briefing record for {job.account}, so there is nothing to write "
            "copy from. Run a briefing first; a campaign run with no record would "
            "invent the account."
        )
    rec = envelope.get("record", {})
    lines = [
        f"Write cold first-touch copy for one account: {rec.get('institution')}.",
        f"Motion: {rec.get('motion')}",
        f"Segment: {rec.get('segment')}",
        f"Account owner: {rec.get('account_owner')}",
        "",
        "FACTS ON RECORD, verbatim from the ledger record. Use only these:",
    ]
    lines += [f"- {f}" for f in rec.get("facts_carried_over", [])]
    lines += ["", "NOT VERIFIED. Do not state any of these as fact:"]
    lines += [f"- {f}" for f in rec.get("not_verified", [])]
    lines += ["", "CONSTRAINTS ON THIS RUN:"]
    lines += [f"- {c}" for c in rec.get("constraints", [])]
    lines += [
        "",
        f"What the briefing concluded: {rec.get('what_this_suggests')}",
        "",
        "This run was queued from the board by Kib's standing Approved decision. "
        "It is a draft run: nothing you produce is cleared to send.",
    ]
    return "\n".join(str(line) for line in lines), ""


def run_copy(job: q.Job, dry_run: bool) -> tuple[int, str, str]:
    text, why_not = copy_input(job)
    if text is None:
        return 2, "", why_not
    LOGS.mkdir(parents=True, exist_ok=True)
    infile = LOGS / f"{job.id}.input.txt"
    logfile = LOGS / f"{job.id}.log"
    infile.write_text(text, encoding="utf-8")
    cmd = [
        str(ROOT / ".venv/Scripts/python.exe"),
        "scripts/live_run.py",
        "campaign",
        "-f",
        str(infile),
        "-o",
        str(logfile),
    ]
    if dry_run:
        return 0, str(logfile), "dry run: " + " ".join(cmd)
    env = dict(os.environ, APOLLO_REVEAL_CAP=str(job.reveal_cap))
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True)
    tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-3:]
    return proc.returncode, str(logfile), " | ".join(tail)[:400]


def _newest_copy_job(account_key: str, directory: Path) -> q.Job | None:
    """The most recent finished COPY job for this account, or None.

    SEQUENCE reads its input from a COPY run's log, not from the ledger record,
    so it needs to know which run that was. Jobs carry no explicit forward link
    to whichever SEQUENCE job later consumes them, so this looks backward: among
    every job on record for this account_key, the newest COPY job that actually
    finished and left a log behind.
    """
    candidates = [
        j
        for j in q.all_jobs(directory)
        if j.account_key == account_key and j.stage == COPY and j.state == q.DONE and j.log
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda j: j.finished_at)


def _ships_verdict(account_key: str, artifact_sha256: str) -> tuple[bool, str]:
    """Whether this exact artifact has a 'ships' review verdict on record.

    ADR 0003, 11 September 2026. Scans both pending and relayed outbox
    envelopes for a review_verdict naming this account_key: a verdict does not
    stop counting once Notion has it. Three outcomes, and the caller needs to
    tell them apart rather than getting one flat "no":

      - no review_verdict at all for this account: nobody has reviewed anything
      - a 'ships' verdict exists, but for a different artifact hash: the copy
        changed since it shipped (a later COPY run, a rewrite) and the
        approval on record no longer covers what's about to be written
      - a 'ships' verdict exists for this exact hash: clear to write

    Returns (ok, detail). detail is empty when ok is True.
    """
    from seats_prospecting.status import _envelopes, _outbox

    outbox = _outbox()
    any_verdict = False
    any_ships = False
    for relayed in (False, True):
        for _path, envelope in _envelopes(outbox, relayed=relayed):
            if envelope.get("kind") != "review_verdict":
                continue
            if envelope.get("account_key") != account_key:
                continue
            any_verdict = True
            record = envelope.get("record") or {}
            if record.get("status") != "ships":
                continue
            any_ships = True
            if envelope.get("artifact_sha256") == artifact_sha256:
                return True, ""

    if any_ships:
        return False, (
            f"{account_key} has a 'ships' review verdict on record, but not for "
            "this exact copy. The copy has changed since it was reviewed -- "
            "another COPY run happened after the verdict, or the verdict covers "
            "an earlier draft. Nothing was written. Run the Reviewer again on "
            "the current artifact before this can be sequenced."
        )
    if any_verdict:
        return False, (
            f"{account_key} has review verdict(s) on record, none with status "
            "'ships'. Nothing was written. The last verdict asked for a rewrite; "
            "fix it and get a ships verdict before this can be sequenced."
        )
    return False, (
        f"No review verdict on record for {account_key}. Nothing was written. "
        "Extract the artifact from the COPY run's log (`review_input`), run "
        "`live_run.py reviewer --account " + account_key + "` on it, and get a "
        "ships verdict before this can be sequenced."
    )


def run_sequence(job: q.Job, dry_run: bool) -> tuple[int, str, str]:
    """Extract the reviewed copy, verify it shipped, write it to Apollo.

    ADR 0003, 11 September 2026. Three steps, each of which can refuse on its
    own and say why, rather than one function guessing its way through:

      1. Find the account's most recent finished COPY run and extract the
         reviewable artifact from its log, the same way a person would by hand
         (``review_input.build_from_path`` -- this is exactly what produces a
         ``reviewer-in-N.txt`` file).
      2. Hash that artifact and check for a relayed review_verdict naming this
         account and this exact hash with status 'ships'. No match, no write --
         see ``_ships_verdict`` for the three ways this can fail.
      3. Plan and write the sequence via ``seats_prospecting.drafts``, the same
         one code path ``seats-drafts`` uses by hand. Still just the first
         write: touches 2-4 (the cadence extend) are a separate question,
         explicitly out of scope here (ADR 0003).
    """
    from seats_prospecting import drafts, review_input

    copy_job = _newest_copy_job(job.account_key, q.queue_dir())
    if copy_job is None or not copy_job.log:
        return 2, "", (
            f"No finished COPY run found for {job.account}. SEQUENCE reads its "
            "input from a COPY run's log; queue and run COPY first. Nothing was "
            "written."
        )
    log_path = Path(copy_job.log)
    if not log_path.exists():
        return 2, "", (
            f"The COPY job on record for {job.account} ({copy_job.id}) points to "
            f"{log_path}, which no longer exists. Nothing was written."
        )

    try:
        artifact = review_input.build_from_path(log_path)
    except review_input.ReviewInputError as exc:
        return 2, "", (
            f"Could not extract a reviewable artifact from {log_path}: {exc} "
            "Nothing was written."
        )

    artifact_sha256 = hashlib.sha256(artifact.text.encode("utf-8")).hexdigest()
    ok, why_not = _ships_verdict(job.account_key, artifact_sha256)
    if not ok:
        return 2, "", why_not

    try:
        plans = drafts.plan_drafts([artifact.text])
    except drafts.DraftError as exc:
        return 2, "", (
            f"The shipped artifact did not plan cleanly: {exc} Nothing was "
            "written. A verdict of 'ships' does not skip this check."
        )

    LOGS.mkdir(parents=True, exist_ok=True)
    logfile = LOGS / f"{job.id}.log"
    if dry_run:
        names = ", ".join(p.name for p in plans)
        return 0, str(logfile), f"dry run: would write {len(plans)} sequence(s): {names}"

    try:
        results = drafts.write_drafts(plans)
    except drafts.DraftError as exc:
        return 2, "", f"Apollo write refused: {exc} Nothing was written."

    logfile.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    summary = "; ".join(f"{r['name']} -> {r.get('status')}" for r in results)
    failed = [r for r in results if r.get("error")]
    if failed:
        return 1, str(logfile), f"{len(failed)} of {len(results)} failed: {summary}"
    return 0, str(logfile), f"{len(results)} sequence(s) written, paused: {summary}"


def run_inbound(job: q.Job, dry_run: bool) -> tuple[int, str, str]:
    """Research an institution that came to us: the Account Context check.

    The input names the institution and the person only. The visit detail stays
    out of it: an agent that knows how many times someone read the pricing page
    can leak that into a draft, and there is no reason a context check needs to
    know. Targeting used the signal; the run does not.
    """
    from seats_prospecting import digest

    intent = digest.for_targeting().get(job.account_key)
    if not intent:
        return 2, "", (
            f"No intent record for {job.account} in context-digest, so there is "
            "nothing to research from. The record may have been removed since the "
            "job was queued."
        )
    newest = intent[0]
    if not newest.person:
        return 2, "", (
            f"The intent record for {job.account} names no person, so a context "
            "check has nobody to check. Nothing was run."
        )
    LOGS.mkdir(parents=True, exist_ok=True)
    infile = LOGS / f"{job.id}.input.txt"
    logfile = LOGS / f"{job.id}.log"
    infile.write_text(
        f"Run an account context check.\n\n"
        f"Institution: {newest.institution}\n"
        f"Person: {newest.person}\n"
        f"Their title, unverified: {newest.person_title}\n\n"
        "This account reached us through inbound interest. That is why it is "
        "being researched and is not a fact about the account: do not treat it "
        "as evidence, do not record it as a finding, and it must not appear in "
        "any draft.\n",
        encoding="utf-8",
    )
    cmd = [
        str(ROOT / ".venv/Scripts/python.exe"),
        "scripts/live_run.py", "context", "-f", str(infile), "-o", str(logfile),
    ]
    if dry_run:
        return 0, str(logfile), "dry run: " + " ".join(cmd)
    env = dict(os.environ, APOLLO_REVEAL_CAP=str(job.reveal_cap))
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True)
    tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-3:]
    return proc.returncode, str(logfile), " | ".join(tail)[:400]


RUNNERS = {INBOUND: run_inbound, COPY: run_copy, SEQUENCE: run_sequence}


def run_one(job: q.Job, directory: Path, dry_run: bool) -> q.Job:
    job.state = q.RUNNING
    job.started_at = now()
    q.write(job, directory)
    runner = RUNNERS.get(job.stage)
    if runner is None:
        job.state = q.REFUSED
        job.outcome = f"No runner for stage {job.stage}."
    else:
        code, log, outcome = runner(job, dry_run)
        job.exit_code = code
        job.log = log
        job.outcome = outcome
        job.state = q.DONE if code == 0 else q.FAILED
    job.finished_at = now()
    q.finish(job, directory)
    return job


def main(argv: list[str] | None = None) -> int:
    from seats_prospecting import settings  # noqa: F401  loads .env

    parser = argparse.ArgumentParser(prog="seats-run-queue")
    parser.add_argument("--loop", action="store_true", help="Run until the queue is empty.")
    parser.add_argument("--dry-run", action="store_true", help="Print the command, run nothing.")
    args = parser.parse_args(argv)

    directory = q.queue_dir()
    ran = 0
    while True:
        allowed, why = q.may_run(directory)
        if not allowed:
            print(why)
            break
        if ran == 0:
            print(why)
        job = q.next_queued(directory)
        if job is None:
            print("Nothing queued." if ran == 0 else f"Queue empty after {ran} job(s).")
            break
        print(f"-> {job.id}  {job.stage}  {job.account}")
        job = run_one(job, directory, args.dry_run)
        ran += 1
        print(f"   {job.state}  exit={job.exit_code}  log={job.log or '-'}")
        if job.outcome:
            print(f"   {job.outcome}")
        if not args.loop:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
