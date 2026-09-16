"""What the enrollment-reveal cap must refuse, and that it survives a race.

Mirrors test_briefing_queue.py: the same lock-before-write shape, because the
same failure mode applies here -- run_sequence() calls resolve_and_enroll()
with no serial stage downstream to catch a race, so the lock itself is what's
under test.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from seats_prospecting import enrollment_queue as eq

WHEN = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


def test_a_reveal_can_be_reserved(tmp_path: Path):
    path = eq.reserve("Dewayne Dickens", "Tulsa Community College", directory=tmp_path, now=WHEN)
    assert path.exists()
    assert eq.today_count(tmp_path) == 1


def test_the_daily_ceiling_refuses_the_next_one(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(eq, "MAX_PER_DAY", 2)
    eq.reserve("first", "inst", directory=tmp_path, now=WHEN)
    eq.reserve("second", "inst", directory=tmp_path, now=WHEN)
    with pytest.raises(eq.EnrollmentRefused) as exc:
        eq.reserve("third", "inst", directory=tmp_path, now=WHEN)
    assert "ceiling" in str(exc.value)
    assert eq.today_count(tmp_path) == 2


def test_a_refusal_writes_nothing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(eq, "MAX_PER_DAY", 0)
    with pytest.raises(eq.EnrollmentRefused):
        eq.reserve("anyone", "inst", directory=tmp_path, now=WHEN)
    assert list(tmp_path.glob("*.json")) == []


def test_the_kill_switch_stops_reservation(tmp_path: Path, monkeypatch):
    queue_root = tmp_path / "queue"
    reveals_dir = queue_root / "enrollment-reveals"
    queue_root.mkdir()
    (queue_root / "PAUSED").touch()
    monkeypatch.setenv("SEATS_QUEUE_DIR", str(queue_root))
    with pytest.raises(eq.EnrollmentRefused) as exc:
        eq.reserve("anyone", "inst", directory=reveals_dir, now=WHEN)
    assert "paused" in str(exc.value).lower()
    assert not reveals_dir.exists() or list(reveals_dir.glob("*.json")) == []


def test_finish_records_the_email_only_in_the_terminal_record(tmp_path: Path):
    path = eq.reserve("Dewayne Dickens", "Tulsa Community College", directory=tmp_path, now=WHEN)
    eq.finish(path, matched=True, email="dewayne.dickens@tulsacc.edu", detail="single confident match")
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["state"] == "matched"
    assert data["email"] == "dewayne.dickens@tulsacc.edu"
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_no_match_is_recorded_without_an_email(tmp_path: Path):
    path = eq.reserve("Ambiguous Name", "Big State University", directory=tmp_path, now=WHEN)
    eq.finish(path, matched=False, email=None, detail="3 same-name contacts, no confident single match")
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["state"] == "no_match"
    assert data["email"] is None
    # Still counts against today's cap -- the credit is spent whether or not it resolved.
    assert eq.today_count(tmp_path) == 1


def test_concurrent_reservations_cannot_exceed_the_ceiling(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(eq, "MAX_PER_DAY", 5)
    attempts = 20
    results: list[bool] = []
    lock = threading.Lock()

    def attempt(i: int) -> None:
        try:
            eq.reserve(f"person-{i}", "inst", directory=tmp_path, now=WHEN)
            ok = True
        except eq.EnrollmentRefused:
            ok = False
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(attempts)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(results) == 5
    assert eq.today_count(tmp_path) == 5
