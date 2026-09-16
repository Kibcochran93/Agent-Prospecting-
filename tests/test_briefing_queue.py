"""What the briefing cap must refuse, and that it survives a real race.

Mirrors test_job_queue.py's style: every test pins a limit, not a feature.
The one thing this suite covers that job_queue's doesn't is concurrency --
briefing.reserve() has no later serial stage to catch a race the way
run_queue.py's one-at-a-time pickup does, so the lock itself is the control
and has to be tested as one.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from seats_prospecting import briefing_queue as bq

WHEN = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


def test_a_briefing_can_be_reserved(tmp_path: Path):
    path = bq.reserve("Sherry Strickland, TSTC", "Kib Cochran", directory=tmp_path, now=WHEN)
    assert path.exists()
    assert path.parent == tmp_path
    assert bq.today_count(tmp_path) == 1


def test_the_daily_ceiling_refuses_the_next_one(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bq, "MAX_PER_DAY", 2)
    bq.reserve("first", "Kib Cochran", directory=tmp_path, now=WHEN)
    bq.reserve("second", "Kib Cochran", directory=tmp_path, now=WHEN)
    with pytest.raises(bq.BriefingRefused) as exc:
        bq.reserve("third", "Kib Cochran", directory=tmp_path, now=WHEN)
    assert "ceiling" in str(exc.value)
    assert bq.today_count(tmp_path) == 2, "a refusal must not itself count"


def test_a_refusal_writes_nothing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bq, "MAX_PER_DAY", 0)
    with pytest.raises(bq.BriefingRefused):
        bq.reserve("anything", "Kib Cochran", directory=tmp_path, now=WHEN)
    assert list(tmp_path.glob("*.json")) == []


def test_the_kill_switch_stops_reservation(tmp_path: Path, monkeypatch):
    queue_root = tmp_path / "queue"
    briefings_dir = queue_root / "briefings"
    (queue_root).mkdir()
    (queue_root / "PAUSED").touch()
    monkeypatch.setenv("SEATS_QUEUE_DIR", str(queue_root))
    with pytest.raises(bq.BriefingRefused) as exc:
        bq.reserve("anything", "Kib Cochran", directory=briefings_dir, now=WHEN)
    assert "paused" in str(exc.value).lower()
    assert not briefings_dir.exists() or list(briefings_dir.glob("*.json")) == []


def test_finish_updates_the_same_record_in_place(tmp_path: Path):
    path = bq.reserve("Sherry Strickland, TSTC", "Kib Cochran", directory=tmp_path, now=WHEN)
    bq.finish(path, exit_code=0, output="## Briefing\n...")
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["state"] == "done"
    assert data["exit_code"] == 0
    assert "Briefing" in data["output"]
    assert data["target"] == "Sherry Strickland, TSTC"
    # Still the one file, not a second record.
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_a_failed_run_is_recorded_as_failed_not_dropped(tmp_path: Path):
    path = bq.reserve("bad target", "Kib Cochran", directory=tmp_path, now=WHEN)
    bq.finish(path, exit_code=1, output="traceback...")
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["state"] == "failed"
    # And it still counts against today's cap -- a failure can still have spent money.
    assert bq.today_count(tmp_path) == 1


def test_concurrent_reservations_cannot_exceed_the_ceiling(tmp_path: Path, monkeypatch):
    """The exact shape that defeated the reveal cap on 4 September: several
    callers reading the same count before any of them had written theirs.
    Fired as real threads, not sequential calls, because the lock is the
    thing under test."""
    monkeypatch.setattr(bq, "MAX_PER_DAY", 5)
    attempts = 20
    results: list[bool] = []
    lock = threading.Lock()

    def attempt(i: int) -> None:
        try:
            bq.reserve(f"target-{i}", "Kib Cochran", directory=tmp_path, now=WHEN)
            ok = True
        except bq.BriefingRefused:
            ok = False
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(attempts)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(results) == 5, "exactly the ceiling should have succeeded, not fewer or more"
    assert bq.today_count(tmp_path) == 5
