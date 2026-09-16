"""The work queue. A click enqueues; a separate process runs.

Kib authorised spend and Apollo writes from a click, 9 September. This is where
the limits live, and they are in code rather than in a prompt or a habit,
because the reveal cap taught that lesson already: a rule that a model or a
person can reason around is not a cap.

Why a queue at all, rather than running inside the HTTP handler:

  - A Campaign Builder run takes minutes. A browser POST would time out and the
    person would learn nothing about what happened.
  - Two clicks would start two runs at once. The reveal cap was defeated once by
    exactly that shape, six concurrent calls each reading the count before any
    of them incremented it.
  - A run launched from a handler produces no log anyone keeps. The queue runner
    calls the same launcher every other run uses, so a queued run leaves the
    same trail.

What is enforced here:

  1. **Only stages that cannot send.** ``next_step.AUTOMATABLE`` is COPY and
     SEQUENCE. Enqueueing SEND is refused, and no caller can widen the set by
     passing a different stage string.
  2. **One job per account per day**, so a double click or an impatient reload
     cannot spend twice on one account.
  3. **A daily ceiling across all accounts**, so a bad afternoon costs a known
     amount.
  4. **A kill switch.** A file named PAUSED in the queue directory stops the
     runner picking anything up. Nothing needs to be killed mid-flight.
  5. **Append-only history.** A job file is updated in place as it runs, and
     completed jobs move to done/ rather than being deleted.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .next_step import AUTOMATABLE

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
REFUSED = "refused"

MAX_PER_ACCOUNT_PER_DAY = int(os.environ.get("SEATS_MAX_JOBS_PER_ACCOUNT_PER_DAY", "1"))
MAX_PER_DAY = int(os.environ.get("SEATS_MAX_JOBS_PER_DAY", "5"))
#: Per job, not per session. The 4 September run spent four credits under a cap
#: of one because the cap was read as a session budget.
REVEAL_CAP_PER_JOB = int(os.environ.get("SEATS_REVEAL_CAP_PER_JOB", "1"))


class QueueRefused(Exception):
    """Raised instead of enqueueing something the caps or the stage set forbid."""


@dataclass
class Job:
    id: str
    account: str
    account_key: str
    stage: str
    state: str = QUEUED
    queued_at: str = ""
    queued_by: str = ""
    started_at: str = ""
    finished_at: str = ""
    exit_code: int | None = None
    log: str = ""
    command: list[str] = field(default_factory=list)
    reveal_cap: int = REVEAL_CAP_PER_JOB
    note: str = ""
    outcome: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "account": self.account,
            "account_key": self.account_key,
            "stage": self.stage,
            "state": self.state,
            "queued_at": self.queued_at,
            "queued_by": self.queued_by,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "log": self.log,
            "command": self.command,
            "reveal_cap": self.reveal_cap,
            "note": self.note,
            "outcome": self.outcome,
        }


def queue_dir() -> Path:
    return Path(os.environ.get("SEATS_QUEUE_DIR", "./queue")).resolve()


def paused(directory: Path | None = None) -> bool:
    return ((directory or queue_dir()) / "PAUSED").exists()


def slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")[:50]


def _load(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def all_jobs(directory: Path | None = None) -> list[Job]:
    directory = directory or queue_dir()
    out: list[Job] = []
    for sub in (directory, directory / "done"):
        if not sub.is_dir():
            continue
        for path in sorted(sub.glob("*.json")):
            raw = _load(path)
            if raw:
                out.append(Job(**{k: v for k, v in raw.items() if k in Job.__annotations__}))
    return out


def write(job: Job, directory: Path | None = None) -> Path:
    directory = directory or queue_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{job.id}.json"
    path.write_text(json.dumps(job.as_dict(), indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


def enqueue(
    account: str,
    account_key: str,
    stage: str,
    *,
    queued_by: str,
    command: list[str],
    note: str = "",
    directory: Path | None = None,
    now: datetime | None = None,
) -> Job:
    """Add one job, or refuse and say which limit stopped it."""
    directory = directory or queue_dir()
    stamp = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    today = stamp.date().isoformat()

    if stage not in AUTOMATABLE:
        raise QueueRefused(
            f"{stage} is not a stage a tool may take on. Only "
            f"{', '.join(sorted(AUTOMATABLE))} can be queued; sending is a "
            "person's job and nothing here can do it."
        )
    if paused(directory):
        raise QueueRefused(
            "The queue is paused. Delete the PAUSED file in the queue directory "
            "to start it again."
        )

    existing = all_jobs(directory)

    # One unrun job per account, regardless of when it was queued. The per-day
    # cap alone let Lyon College be queued on the 9th and again on the 10th,
    # which would have run the Campaign Builder twice and produced two sets of
    # copy for one account.
    pending = [
        j for j in existing
        if j.account_key == account_key and j.state in (QUEUED, RUNNING)
    ]
    if pending:
        raise QueueRefused(
            f"{account} already has a job waiting to run ({pending[0].id}, queued "
            f"{pending[0].queued_at[:16]}). One unrun job per account: running two "
            "would produce two sets of copy for one account."
        )

    same_account_today = [
        j for j in existing
        if j.account_key == account_key
        and j.queued_at[:10] == today
        and j.state in (QUEUED, RUNNING, DONE)
    ]
    if len(same_account_today) >= MAX_PER_ACCOUNT_PER_DAY:
        raise QueueRefused(
            f"{account} already has {len(same_account_today)} job(s) today and the "
            f"cap is {MAX_PER_ACCOUNT_PER_DAY} per account per day. This is the "
            "double-click guard; it spends nothing."
        )
    today_all = [
        j for j in existing
        if j.queued_at[:10] == today and j.state in (QUEUED, RUNNING, DONE)
    ]
    if len(today_all) >= MAX_PER_DAY:
        raise QueueRefused(
            f"{len(today_all)} jobs today already and the daily ceiling is "
            f"{MAX_PER_DAY}. Raise SEATS_MAX_JOBS_PER_DAY deliberately if you "
            "mean to spend more."
        )

    job = Job(
        id=f"{stamp.strftime('%Y%m%dT%H%M%SZ')}-{slug(account_key)}-{slug(stage)}",
        account=account,
        account_key=account_key,
        stage=stage,
        queued_at=stamp.isoformat(),
        queued_by=queued_by,
        command=list(command),
        note=note,
    )
    path = directory / f"{job.id}.json"
    if path.exists():
        raise QueueRefused(f"{job.id} already exists. Wait a second and retry.")
    write(job, directory)
    return job


def runs_today(directory: Path | None = None, now: datetime | None = None) -> int:
    """Jobs that actually started today, whatever became of them.

    A failed run can still have spent money, so it counts. This is deliberately
    not the same figure as the enqueue cap: that one counts clicks.
    """
    today = (now or datetime.now(timezone.utc)).date().isoformat()
    return len([
        j for j in all_jobs(directory)
        if j.started_at[:10] == today and j.state in (RUNNING, DONE, FAILED)
    ])


def may_run(directory: Path | None = None, now: datetime | None = None) -> tuple[bool, str]:
    """Whether the runner may start another job. Checked before every job.

    The ceiling used to govern clicking only, so four jobs queued yesterday and
    five today meant nine runs against a budget of five. A cap that limits
    clicks but not work is not a cap on spend.
    """
    directory = directory or queue_dir()
    if paused(directory):
        return False, "PAUSED file present. Nothing will be picked up."
    started = runs_today(directory, now)
    if started >= MAX_PER_DAY:
        return False, (
            f"{started} job(s) have already run today and the ceiling is "
            f"{MAX_PER_DAY}. The rest stay queued. Raise SEATS_MAX_JOBS_PER_DAY "
            "deliberately if you mean to spend more today."
        )
    return True, f"{MAX_PER_DAY - started} of {MAX_PER_DAY} runs left today."


def next_queued(directory: Path | None = None) -> Job | None:
    directory = directory or queue_dir()
    if paused(directory):
        return None
    for path in sorted(directory.glob("*.json")):
        raw = _load(path)
        if raw and raw.get("state") == QUEUED:
            return Job(**{k: v for k, v in raw.items() if k in Job.__annotations__})
    return None


def finish(job: Job, directory: Path | None = None) -> Path:
    """Move a settled job to done/, keeping the record."""
    directory = directory or queue_dir()
    done = directory / "done"
    done.mkdir(parents=True, exist_ok=True)
    path = done / f"{job.id}.json"
    path.write_text(json.dumps(job.as_dict(), indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    live = directory / f"{job.id}.json"
    if live.exists():
        live.unlink()
    return path
