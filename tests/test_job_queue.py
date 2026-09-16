"""What the queue must refuse. Every test pins a limit, not a feature."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from seats_prospecting import job_queue as q
from seats_prospecting.next_step import COPY, ENROL, SEND, SEQUENCE, UNDECIDED, WORKING

WHEN = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def add(tmp: Path, key="lyon", stage=COPY, when=WHEN, account="Lyon College"):
    return q.enqueue(account, key, stage, queued_by="Kib Cochran",
                     command=["live_run.py", "campaign"], directory=tmp, now=when)


def test_sending_can_never_be_queued(tmp_path: Path):
    for stage in (SEND, ENROL, WORKING, UNDECIDED, "ANYTHING"):
        with pytest.raises(q.QueueRefused) as exc:
            add(tmp_path, stage=stage)
        assert "person" in str(exc.value) or "not a stage" in str(exc.value)
    assert list(tmp_path.glob("*.json")) == []


def test_only_copy_and_sequence_are_queueable(tmp_path: Path):
    assert add(tmp_path, key="a", stage=COPY).state == q.QUEUED
    assert add(tmp_path, key="b", stage=SEQUENCE).state == q.QUEUED


def test_one_job_per_account_per_day(tmp_path: Path):
    """A second click on one account the same day is refused.

    Which limit stops it changed on 10 September: an unrun job now blocks
    first, and the per-day cap catches the case where the first job has
    already run. Both are asserted, because the guarantee is the refusal.
    """
    add(tmp_path)
    with pytest.raises(q.QueueRefused) as exc:
        add(tmp_path, when=WHEN + timedelta(minutes=5))
    assert "already has a job waiting" in str(exc.value)
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_the_per_day_cap_still_catches_an_account_that_already_ran(tmp_path: Path):
    job = add(tmp_path)
    job.state = q.DONE
    job.started_at = WHEN.isoformat()
    q.finish(job, tmp_path)
    with pytest.raises(q.QueueRefused) as exc:
        add(tmp_path, when=WHEN + timedelta(hours=2))
    assert "per account per day" in str(exc.value)


def test_the_same_account_may_be_queued_again_the_next_day(tmp_path: Path):
    """Next day, yes, but only once the earlier job has actually run.

    Until 10 September this passed with the first job still queued, which is
    how Lyon College ended up with two unrun jobs and would have had two runs.
    """
    first = add(tmp_path)
    with pytest.raises(q.QueueRefused):
        add(tmp_path, when=WHEN + timedelta(days=1))

    first.state = q.DONE
    first.started_at = WHEN.isoformat()
    q.finish(first, tmp_path)
    later = add(tmp_path, when=WHEN + timedelta(days=1))
    assert later.state == q.QUEUED


def test_a_daily_ceiling_across_all_accounts(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(q, "MAX_PER_DAY", 2)
    add(tmp_path, key="one")
    add(tmp_path, key="two")
    with pytest.raises(q.QueueRefused) as exc:
        add(tmp_path, key="three")
    assert "daily ceiling" in str(exc.value)


def test_the_kill_switch_stops_enqueueing(tmp_path: Path):
    (tmp_path / "PAUSED").touch()
    with pytest.raises(q.QueueRefused) as exc:
        add(tmp_path)
    assert "paused" in str(exc.value).lower()
    assert q.next_queued(tmp_path) is None


def test_the_kill_switch_stops_the_runner_picking_work_up(tmp_path: Path):
    add(tmp_path)
    assert q.next_queued(tmp_path) is not None
    (tmp_path / "PAUSED").touch()
    assert q.next_queued(tmp_path) is None


def test_a_job_carries_a_reveal_cap(tmp_path: Path):
    job = add(tmp_path)
    assert job.reveal_cap == q.REVEAL_CAP_PER_JOB
    assert job.reveal_cap >= 1


def test_a_finished_job_is_kept_and_still_counts_against_the_cap(tmp_path: Path):
    job = add(tmp_path)
    job.state = q.DONE
    q.finish(job, tmp_path)
    assert (tmp_path / "done" / f"{job.id}.json").exists()
    assert not (tmp_path / f"{job.id}.json").exists()
    with pytest.raises(q.QueueRefused):
        add(tmp_path, when=WHEN + timedelta(minutes=1))


def test_a_refused_job_does_not_consume_the_cap(tmp_path: Path):
    """A refusal is not a spend, so it must not block the retry that follows."""
    job = add(tmp_path)
    job.state = q.REFUSED
    q.write(job, tmp_path)
    again = add(tmp_path, when=WHEN + timedelta(minutes=1))
    assert again.state == q.QUEUED


# --- the cap has to govern work, not only clicking ------------------------


def test_one_unrun_job_per_account_regardless_of_day(tmp_path: Path):
    """Lyon College, 9 and 10 September: queued twice, two runs, two sets of copy."""
    first = add(tmp_path)
    with pytest.raises(q.QueueRefused) as exc:
        add(tmp_path, when=WHEN + timedelta(days=1))
    assert "already has a job waiting" in str(exc.value)
    assert first.id in str(exc.value), "name the job that is already waiting"


def test_a_run_that_finished_frees_the_account(tmp_path: Path):
    job = add(tmp_path)
    job.state = q.DONE
    job.started_at = WHEN.isoformat()
    q.finish(job, tmp_path)
    again = add(tmp_path, when=WHEN + timedelta(days=1))
    assert again.state == q.QUEUED


def _ran(tmp_path: Path, key: str, when=WHEN, state=q.DONE):
    job = add(tmp_path, key=key, when=when)
    job.state = state
    job.started_at = when.isoformat()
    q.finish(job, tmp_path)
    return job


def test_the_runner_stops_at_the_daily_ceiling(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(q, "MAX_PER_DAY", 2)
    _ran(tmp_path, "one")
    allowed, why = q.may_run(tmp_path, WHEN)
    assert allowed is True and "1 of 2" in why
    _ran(tmp_path, "two")
    allowed, why = q.may_run(tmp_path, WHEN)
    assert allowed is False
    assert "ceiling" in why and "stay queued" in why


def test_jobs_queued_yesterday_do_not_get_a_free_run_today(tmp_path: Path, monkeypatch):
    """Four queued on the 9th plus five on the 10th ran nine against a cap of five."""
    monkeypatch.setattr(q, "MAX_PER_DAY", 2)
    yesterday = WHEN - timedelta(days=1)
    add(tmp_path, key="old-one", when=yesterday)
    add(tmp_path, key="old-two", when=yesterday)
    _ran(tmp_path, "today-one", when=WHEN)
    _ran(tmp_path, "today-two", when=WHEN)
    allowed, why = q.may_run(tmp_path, WHEN)
    assert allowed is False, "yesterday's queue must not spend today's budget"
    assert q.next_queued(tmp_path) is not None, "they stay queued, they are not lost"


def test_a_failed_run_still_counts_against_the_ceiling(tmp_path: Path, monkeypatch):
    """A run that failed can still have spent money."""
    monkeypatch.setattr(q, "MAX_PER_DAY", 1)
    _ran(tmp_path, "one", state=q.FAILED)
    allowed, _why = q.may_run(tmp_path, WHEN)
    assert allowed is False


def test_a_queued_job_that_never_ran_does_not_count_as_a_run(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(q, "MAX_PER_DAY", 1)
    add(tmp_path, key="waiting")
    allowed, _why = q.may_run(tmp_path, WHEN)
    assert allowed is True


def test_the_kill_switch_beats_the_budget(tmp_path: Path):
    (tmp_path / "PAUSED").touch()
    allowed, why = q.may_run(tmp_path, WHEN)
    assert allowed is False and "PAUSED" in why
