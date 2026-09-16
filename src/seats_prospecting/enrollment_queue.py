"""Daily cap and audit trail for email reveals spent enrolling a contact.

Separate from the per-run APOLLO_REVEAL_CAP in tools/apollo.py on purpose: that
cap lives on a DispatchContext, which only exists inside an agent run (a
research/briefing subprocess). The SEQUENCE stage that calls resolve_and_enroll
in drafts.py is NOT an agent run -- it is plain synchronous Python inside
run_queue.py, with no DispatchContext to attach a counter to. This is the same
answer this project already gave that problem twice (job_queue.py,
briefing_queue.py): a file-backed daily count, checked and reserved under one
lock, because a synchronous check-then-spend across two steps is exactly what
let six reveals through a cap of one on 4 September.

Kib's call, 15 September 2026: resolve the email at the point of the Apollo
write only (ADR-equivalent decision, ADR 0004 territory but not yet numbered
as its own ADR). Briefing, drafting, and review never see an email address --
only run_sequence(), already gated behind a 'ships' verdict, ever calls this.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

MAX_PER_DAY = int(os.environ.get("SEATS_MAX_ENROLLMENT_REVEALS_PER_DAY", "10"))

_LOCK = threading.Lock()


class EnrollmentRefused(Exception):
    """Raised instead of spending a reveal the daily cap or PAUSED forbids."""


def reveals_dir() -> Path:
    return Path(os.environ.get("SEATS_ENROLLMENT_DIR", "./queue/enrollment-reveals")).resolve()


def _queue_root() -> Path:
    return Path(os.environ.get("SEATS_QUEUE_DIR", "./queue")).resolve()


def paused() -> bool:
    """The same PAUSED file the job queue and briefing queue honour."""
    return (_queue_root() / "PAUSED").exists()


def _slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")[:50]


def today_count(directory: Path | None = None) -> int:
    directory = directory or reveals_dir()
    if not directory.is_dir():
        return 0
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return sum(1 for _ in directory.glob(f"{today}T*.json"))


def reserve(
    person: str,
    institution: str,
    directory: Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Check the cap and, if it holds, immediately write a 'reserved' record.

    Same shape as briefing_queue.reserve(): check-and-write under one lock, so
    two SEQUENCE jobs finishing at the same moment cannot both observe "under
    the cap" before either has recorded a spend.
    """
    directory = directory or reveals_dir()
    with _LOCK:
        if paused():
            raise EnrollmentRefused(
                "The queue is paused (a PAUSED file sits in queue/). Enrollment "
                "reveals are stopped along with everything else until it's removed."
            )
        used = today_count(directory)
        if used >= MAX_PER_DAY:
            raise EnrollmentRefused(
                f"{used} enrollment reveal(s) already spent today and the ceiling "
                f"is {MAX_PER_DAY}. The sequence is still created, just not "
                "enrolled -- raise SEATS_MAX_ENROLLMENT_REVEALS_PER_DAY "
                "deliberately to spend more today."
            )
        directory.mkdir(parents=True, exist_ok=True)
        stamp = (now or datetime.now(timezone.utc)).replace(microsecond=0)
        file_id = f"{stamp.strftime('%Y%m%dT%H%M%SZ')}-{_slug(person)}-{_slug(institution)}"
        path = directory / f"{file_id}.json"
        path.write_text(
            json.dumps(
                {
                    "person": person,
                    "institution": institution,
                    "started_at": stamp.isoformat(),
                    "state": "reserved",
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return path


def finish(path: Path, *, matched: bool, email: str | None, detail: str) -> None:
    """Record what the reveal actually found, win or lose.

    The email itself is written here -- this file is the one place in the
    whole project a real inbox address is allowed to live, and only because
    it is the terminal record of a spend already made, not context an agent
    reads back in on a later run.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    data["state"] = "matched" if matched else "no_match"
    data["finished_at"] = (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    )
    data["email"] = email
    data["detail"] = detail
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
