"""Daily cap and audit trail for briefing runs triggered from the bridge.

Mirrors job_queue.py's cap philosophy for a different kind of run. A queued
COPY or SEQUENCE job is written to disk and picked up later, one at a time,
by a separate run_queue.py process -- that serial pickup is what actually
enforces the cap under concurrency, per its own docstring: "concurrency is
what defeated the reveal cap."

A briefing has no equivalent second stage. It runs synchronously inside the
HTTP request that asked for it, on board_serve.py's ThreadingHTTPServer,
because the panel that asks for one wants the drafted text back, not a job
id to poll. Two requests can genuinely arrive at once. So the check and the
reservation happen together, under one lock, in `reserve()`: nothing here
trusts a caller to check the count and then write in two separate steps,
because "two things that raced past a read of the same count" is exactly
the failure this project already had once, on 4 September, with the reveal
cap.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

MAX_PER_DAY = int(os.environ.get("SEATS_MAX_BRIEFINGS_PER_DAY", "5"))

_LOCK = threading.Lock()


class BriefingRefused(Exception):
    """Raised instead of starting a briefing the daily cap or PAUSED forbids."""


def briefings_dir() -> Path:
    return Path(os.environ.get("SEATS_BRIEFINGS_DIR", "./queue/briefings")).resolve()


def _queue_root() -> Path:
    return Path(os.environ.get("SEATS_QUEUE_DIR", "./queue")).resolve()


def paused() -> bool:
    """The same PAUSED file the job queue honours, in the same queue/ tree,
    so one kill switch stops both kinds of spend rather than needing two."""
    return (_queue_root() / "PAUSED").exists()


def _slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")[:50]


def today_count(directory: Path | None = None) -> int:
    """Records started today, whatever their current state.

    Counting starts rather than completions on purpose: a briefing that is
    still running counts against today's cap the moment it starts, not only
    once it finishes, or two slow briefings could both be in flight while a
    third slips past the check that should have refused it.
    """
    directory = directory or briefings_dir()
    if not directory.is_dir():
        return 0
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return sum(1 for _ in directory.glob(f"{today}T*.json"))


def reserve(
    target: str,
    requested_by: str,
    directory: Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Check the cap and, if it holds, immediately write a 'running' record.

    Check-and-write happen inside one lock so two requests that arrive at the
    same instant cannot both observe "under the cap" before either has
    written its own record. Raises BriefingRefused and writes nothing if the
    queue is paused or the daily ceiling is already spent.
    """
    directory = directory or briefings_dir()
    with _LOCK:
        if paused():
            raise BriefingRefused(
                "The queue is paused (a PAUSED file sits in queue/). Briefings "
                "are stopped along with everything else until it's removed."
            )
        used = today_count(directory)
        if used >= MAX_PER_DAY:
            raise BriefingRefused(
                f"{used} briefing(s) already run today and the ceiling is "
                f"{MAX_PER_DAY}. Raise SEATS_MAX_BRIEFINGS_PER_DAY deliberately "
                "to spend more today."
            )
        directory.mkdir(parents=True, exist_ok=True)
        stamp = (now or datetime.now(timezone.utc)).replace(microsecond=0)
        file_id = f"{stamp.strftime('%Y%m%dT%H%M%SZ')}-{_slug(target)}"
        path = directory / f"{file_id}.json"
        path.write_text(
            json.dumps(
                {
                    "target": target,
                    "requested_by": requested_by,
                    "started_at": stamp.isoformat(),
                    "state": "running",
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return path


def finish(path: Path, *, exit_code: int | None, output: str) -> None:
    """Update the reserved record with what the run actually produced.

    Updated in place, same as a job_queue.Job: the record that reserved the
    spend and the record of what it bought are one file, not two that could
    drift apart.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    data["state"] = "done" if exit_code == 0 else "failed"
    data["finished_at"] = (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    )
    data["exit_code"] = exit_code
    data["output"] = output
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
