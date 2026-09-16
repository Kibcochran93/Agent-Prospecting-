"""ADR 0003, 11 September 2026: what a queued SEQUENCE job is allowed to trust.

SEQUENCE was AUTOMATABLE and refused every job it was handed, because nothing
tied a review_verdict back to an account or to the exact text it graded. These
tests are about the glue that closes that gap, not about Apollo's payload
shapes (test_drafts.py already covers those) or the extractor's parsing rules
(test_review_input.py already covers those). Three things matter here:

  * a queued job finds the right COPY run for its account
  * it writes only when a 'ships' verdict names this account AND this exact
    artifact hash
  * every other case refuses with a reason specific enough to act on
"""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from seats_prospecting import drafts, job_queue as q, review_input
from seats_prospecting.next_step import COPY, SEQUENCE
from seats_prospecting.schemas import ReviewVerdict
from seats_prospecting.tools import notion_ledger

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("run_queue", ROOT / "scripts" / "run_queue.py")
run_queue = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_queue)

WHEN = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
ACCOUNT_KEY = "tulsa-community-college"
ACCOUNT_NAME = "Tulsa Community College"

# A minimal but real run log: harness lines above the marker, exactly one
# extractable message below it. review_input strips everything above
# "=== OUTPUT ===", so what's above it is deliberately noise.
LOG_TEXT = (
    "TRAIL: ['START Campaign Builder']\n"
    "AUDIT: []\n"
    "REVEALS: 0\n"
    "LAST AGENT: Campaign Builder\n"
    "RECORDED: not recorded\n"
    "=== OUTPUT ===\n"
    "## Copy batch\n\n"
    "### Tulsa Community College\n\n"
    "**To: Dewayne Dickens, Senior Director**\n"
    "**Subject:** Six coaches, 2,100 students\n\n"
    "Six coaches supporting roughly 2,100 students is meaningful reach. How "
    "does that visibility work at TCC today?\n"
)


def _copy_job(tmp_path: Path, log_path: Path, when=WHEN) -> q.Job:
    """A finished COPY job on record for ACCOUNT_KEY, pointing at log_path."""
    job = q.Job(
        id=f"{when.strftime('%Y%m%dT%H%M%SZ')}-{ACCOUNT_KEY}-copy",
        account=ACCOUNT_NAME,
        account_key=ACCOUNT_KEY,
        stage=COPY,
        state=q.DONE,
        queued_at=when.isoformat(),
        queued_by="Kib Cochran",
        started_at=when.isoformat(),
        finished_at=when.isoformat(),
        exit_code=0,
        log=str(log_path),
        command=[],
    )
    q.finish(job, tmp_path)
    return job


def _sequence_job(when=WHEN) -> q.Job:
    return q.Job(
        id=f"{when.strftime('%Y%m%dT%H%M%SZ')}-{ACCOUNT_KEY}-sequence",
        account=ACCOUNT_NAME,
        account_key=ACCOUNT_KEY,
        stage=SEQUENCE,
        state=q.RUNNING,
        queued_at=when.isoformat(),
        queued_by="Kib Cochran",
        started_at=when.isoformat(),
        command=[],
    )


def _write_verdict(
    tmp_path: Path,
    monkeypatch,
    *,
    status: str = "ships",
    account_key: str = ACCOUNT_KEY,
    artifact_sha256: str,
    relayed: bool = False,
) -> Path:
    monkeypatch.setenv("LEDGER_OUTBOX_DIR", str(tmp_path / "outbox"))
    verdict = ReviewVerdict(
        status=status,
        batch_kind="first_touch",
        findings=[] if status == "ships" else [
            {
                "ground": "unsupported_claim",
                "quote": "placeholder",
                "recipient": "Dewayne Dickens",
                "why": "placeholder",
                "required_fix": "placeholder",
            }
        ],
    )
    path = notion_ledger.write_review_verdict(
        verdict, account_key=account_key, artifact_sha256=artifact_sha256
    )
    if relayed:
        relayed_dir = path.parent / "relayed"
        relayed_dir.mkdir(parents=True, exist_ok=True)
        moved = relayed_dir / path.name
        shutil.move(str(path), str(moved))
        return moved
    return path


def _real_artifact_hash(log_path: Path) -> str:
    artifact = review_input.build_from_path(log_path)
    return hashlib.sha256(artifact.text.encode("utf-8")).hexdigest()


@pytest.fixture(autouse=True)
def _queue_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SEATS_QUEUE_DIR", str(tmp_path))
    return tmp_path


def test_no_copy_job_refuses(tmp_path):
    code, log, outcome = run_queue.run_sequence(_sequence_job(), dry_run=False)
    assert code == 2
    assert log == ""
    assert "No finished COPY run found" in outcome


def test_a_copy_job_for_a_different_account_is_not_found(tmp_path):
    log_path = tmp_path / "other.log"
    log_path.write_text(LOG_TEXT, encoding="utf-8")
    job = q.Job(
        id="20260910T000000Z-some-other-college-copy",
        account="Some Other College",
        account_key="some-other-college",
        stage=COPY,
        state=q.DONE,
        queued_at=WHEN.isoformat(),
        queued_by="Kib Cochran",
        started_at=WHEN.isoformat(),
        finished_at=WHEN.isoformat(),
        log=str(log_path),
        command=[],
    )
    q.finish(job, tmp_path)

    code, _log, outcome = run_queue.run_sequence(_sequence_job(), dry_run=False)
    assert code == 2
    assert "No finished COPY run found" in outcome


def test_missing_log_file_refuses(tmp_path):
    missing = tmp_path / "gone.log"
    _copy_job(tmp_path, missing)
    code, _log, outcome = run_queue.run_sequence(_sequence_job(), dry_run=False)
    assert code == 2
    assert "no longer exists" in outcome


def test_a_log_with_no_copy_batch_refuses(tmp_path):
    log_path = tmp_path / "empty.log"
    log_path.write_text("=== OUTPUT ===\nJust a briefing, no batch heading.\n", encoding="utf-8")
    _copy_job(tmp_path, log_path)
    code, _log, outcome = run_queue.run_sequence(_sequence_job(), dry_run=False)
    assert code == 2
    assert "Could not extract a reviewable artifact" in outcome


def test_no_review_verdict_at_all_refuses(tmp_path, monkeypatch):
    log_path = tmp_path / "copy.log"
    log_path.write_text(LOG_TEXT, encoding="utf-8")
    _copy_job(tmp_path, log_path)
    monkeypatch.setenv("LEDGER_OUTBOX_DIR", str(tmp_path / "outbox"))  # empty outbox

    code, _log, outcome = run_queue.run_sequence(_sequence_job(), dry_run=False)
    assert code == 2
    assert "No review verdict on record" in outcome


def test_a_rewrite_only_verdict_refuses(tmp_path, monkeypatch):
    log_path = tmp_path / "copy.log"
    log_path.write_text(LOG_TEXT, encoding="utf-8")
    _copy_job(tmp_path, log_path)
    _write_verdict(tmp_path, monkeypatch, status="rewrite", artifact_sha256="a" * 64)

    code, _log, outcome = run_queue.run_sequence(_sequence_job(), dry_run=False)
    assert code == 2
    assert "none with status 'ships'" in outcome


def test_a_ships_verdict_for_different_text_refuses(tmp_path, monkeypatch):
    """The copy changed after it shipped: a second COPY run, a rewrite. The old
    approval must not cover new text."""
    log_path = tmp_path / "copy.log"
    log_path.write_text(LOG_TEXT, encoding="utf-8")
    _copy_job(tmp_path, log_path)
    # A ships verdict for this account, but a hash that matches nothing here.
    _write_verdict(tmp_path, monkeypatch, status="ships", artifact_sha256="c" * 64)

    code, _log, outcome = run_queue.run_sequence(_sequence_job(), dry_run=False)
    assert code == 2
    assert "not for this exact copy" in outcome


def test_a_ships_verdict_for_a_different_account_does_not_count(tmp_path, monkeypatch):
    log_path = tmp_path / "copy.log"
    log_path.write_text(LOG_TEXT, encoding="utf-8")
    _copy_job(tmp_path, log_path)
    real_hash = _real_artifact_hash(log_path)
    _write_verdict(
        tmp_path, monkeypatch, status="ships",
        account_key="some-other-college", artifact_sha256=real_hash,
    )

    code, _log, outcome = run_queue.run_sequence(_sequence_job(), dry_run=False)
    assert code == 2
    assert "No review verdict on record" in outcome


def test_a_relayed_ships_verdict_still_counts(tmp_path, monkeypatch):
    """A verdict does not stop counting once Notion has it."""
    log_path = tmp_path / "copy.log"
    log_path.write_text(LOG_TEXT, encoding="utf-8")
    _copy_job(tmp_path, log_path)
    real_hash = _real_artifact_hash(log_path)
    _write_verdict(tmp_path, monkeypatch, status="ships", artifact_sha256=real_hash, relayed=True)

    monkeypatch.setattr(drafts, "write_drafts", lambda plans: pytest.fail("dry run must not write"))
    code, _log, outcome = run_queue.run_sequence(_sequence_job(), dry_run=True)
    assert code == 0
    assert "dry run" in outcome


def test_dry_run_matches_but_writes_nothing(tmp_path, monkeypatch):
    log_path = tmp_path / "copy.log"
    log_path.write_text(LOG_TEXT, encoding="utf-8")
    _copy_job(tmp_path, log_path)
    real_hash = _real_artifact_hash(log_path)
    _write_verdict(tmp_path, monkeypatch, status="ships", artifact_sha256=real_hash)

    def _must_not_write(plans):
        raise AssertionError("dry run must not reach Apollo")

    monkeypatch.setattr(drafts, "write_drafts", _must_not_write)
    code, _log, outcome = run_queue.run_sequence(_sequence_job(), dry_run=True)
    assert code == 0
    assert "dry run" in outcome
    assert "Dewayne Dickens | Tulsa Community College" in outcome


def test_a_matching_ships_verdict_writes_the_sequence(tmp_path, monkeypatch):
    log_path = tmp_path / "copy.log"
    log_path.write_text(LOG_TEXT, encoding="utf-8")
    _copy_job(tmp_path, log_path)
    real_hash = _real_artifact_hash(log_path)
    _write_verdict(tmp_path, monkeypatch, status="ships", artifact_sha256=real_hash)

    captured = {}

    def fake_write_drafts(plans):
        captured["plans"] = plans
        return [
            {"name": p.name, "status": 200, "id": "seq-1", "steps": len(p.touches), "error": None}
            for p in plans
        ]

    monkeypatch.setattr(drafts, "write_drafts", fake_write_drafts)
    job = _sequence_job()
    code, log, outcome = run_queue.run_sequence(job, dry_run=False)

    assert code == 0
    assert len(captured["plans"]) == 1
    assert captured["plans"][0].name == "Dewayne Dickens | Tulsa Community College"
    assert "1 sequence(s) written, paused" in outcome
    assert Path(log).exists()


def test_a_failed_apollo_write_is_reported_not_hidden(tmp_path, monkeypatch):
    log_path = tmp_path / "copy.log"
    log_path.write_text(LOG_TEXT, encoding="utf-8")
    _copy_job(tmp_path, log_path)
    real_hash = _real_artifact_hash(log_path)
    _write_verdict(tmp_path, monkeypatch, status="ships", artifact_sha256=real_hash)

    monkeypatch.setattr(
        drafts, "write_drafts",
        lambda plans: [
            {"name": p.name, "status": 422, "id": None, "steps": 0, "error": "boom"} for p in plans
        ],
    )
    code, _log, outcome = run_queue.run_sequence(_sequence_job(), dry_run=False)
    assert code == 1
    assert "1 of 1 failed" in outcome


def test_the_newest_finished_copy_job_wins(tmp_path):
    """Two COPY runs on record for one account: SEQUENCE reads the newer one,
    not whichever happens to sort first on disk."""
    older_log = tmp_path / "older.log"
    older_log.write_text(LOG_TEXT, encoding="utf-8")
    newer_log = tmp_path / "newer.log"
    newer_log.write_text(LOG_TEXT.replace("Six coaches", "Six coaches, revised"), encoding="utf-8")

    _copy_job(tmp_path, older_log, when=WHEN)
    newer_job = _copy_job(tmp_path, newer_log, when=WHEN + timedelta(hours=1))

    found = run_queue._newest_copy_job(ACCOUNT_KEY, tmp_path)
    assert found is not None
    assert found.id == newer_job.id
    assert found.log == str(newer_log)


def test_a_queued_or_running_copy_job_is_not_a_finished_one(tmp_path):
    """Only a job that actually finished (state DONE) is a source of truth."""
    log_path = tmp_path / "copy.log"
    log_path.write_text(LOG_TEXT, encoding="utf-8")
    job = q.Job(
        id=f"{WHEN.strftime('%Y%m%dT%H%M%SZ')}-{ACCOUNT_KEY}-copy",
        account=ACCOUNT_NAME,
        account_key=ACCOUNT_KEY,
        stage=COPY,
        state=q.RUNNING,
        queued_at=WHEN.isoformat(),
        queued_by="Kib Cochran",
        log=str(log_path),
        command=[],
    )
    q.write(job, tmp_path)

    assert run_queue._newest_copy_job(ACCOUNT_KEY, tmp_path) is None
