"""What the intent digest and the INBOUND stage must not do."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
from datetime import date
from pathlib import Path

from seats_prospecting import digest
from seats_prospecting.next_step import COPY, INBOUND, UNDECIDED, Facts, stage_for

TODAY = date(2026, 9, 10)


def write(tmp: Path, name="rec.json", **over) -> Path:
    record = {
        "kind": "live_intent_signal",
        "institution": "Lock Haven University",
        "account_key": "lock haven",
        "signal": "Robin Rockey read the site 37 times.",
        "person": "Robin Rockey",
        "person_title": "Interim Director of Admissions",
        "total_visits": 37,
        "last_visit": "2026-09-01",
        "source": "Apollo website visitors",
        "source_date": "2026-09-01",
        "confidentiality": "internal_only",
    }
    record.update(over)
    digest_hex = hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    path = tmp / name
    path.write_text(json.dumps(
        {"record": record, "record_sha256": digest_hex}), encoding="utf-8")
    return path


def test_intent_never_reaches_copy():
    """for_copy has no branch that can return content."""
    assert digest.for_copy() == {}
    assert digest.for_copy("lock haven", force=True) == {}
    tree = ast.parse(inspect.getsource(digest.for_copy))
    returns = [n for n in ast.walk(tree) if isinstance(n, ast.Return)]
    assert len(returns) == 1
    assert isinstance(returns[0].value, ast.Dict)
    assert returns[0].value.keys == [], "must return an empty dict, not a redaction"


def test_a_tampered_record_is_dropped_and_reported(tmp_path: Path):
    path = write(tmp_path)
    body = json.loads(path.read_text())
    body["record"]["total_visits"] = 999
    path.write_text(json.dumps(body), encoding="utf-8")
    records, problems = digest.load(tmp_path, TODAY)
    assert records == []
    assert any("altered" in p for p in problems)


def test_a_record_without_the_internal_marking_is_dropped(tmp_path: Path):
    write(tmp_path, confidentiality="public")
    records, problems = digest.load(tmp_path, TODAY)
    assert records == []
    assert any("internal_only" in p for p in problems)


def test_every_loaded_record_is_internal(tmp_path: Path):
    write(tmp_path)
    records, _ = digest.load(tmp_path, TODAY)
    assert records and all(r.internal_only for r in records)


def test_age_is_computed_not_trusted(tmp_path: Path):
    """A record claiming to be fresh does not get to say so."""
    write(tmp_path, source_date="2025-03-26", age_days=1, stale=False)
    records, _ = digest.load(tmp_path, TODAY)
    assert records[0].age_days == 533
    assert records[0].stale is True


def test_inbound_is_queueable_but_reaches_no_sending_stage(tmp_path: Path):
    step = stage_for(Facts(account="Lock Haven University", inbound_person="Robin Rockey",
                           inbound_visits=37, inbound_last_visit="2026-09-01",
                           has_records=False))
    assert step.stage == INBOUND
    assert step.automatable is True
    assert step.waiting_on == "Account Context"


def test_an_account_with_records_is_not_inbound():
    """Inbound means nobody has researched it. Once researched, it is not."""
    step = stage_for(Facts(account="X", inbound_person="Someone", has_records=True))
    assert step.stage == UNDECIDED


def test_a_decided_account_is_never_pulled_back_to_inbound():
    step = stage_for(Facts(account="X", decision="Approved", inbound_person="Someone",
                           has_records=False))
    assert step.stage == COPY


def test_the_inbound_blocker_warns_against_quoting_the_signal():
    step = stage_for(Facts(account="X", inbound_person="Someone", has_records=False))
    assert "never quote" in step.evidence.lower()


def test_the_inbound_runner_keeps_visit_detail_out_of_the_run():
    """An agent that knows the visit count can leak it into a draft."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import run_queue

    src = inspect.getsource(run_queue.run_inbound)
    # The visit count is never handed to the run. Targeting used the signal;
    # the research does not need it and an agent that has it can leak it.
    assert "total_visits" not in src
    assert "apollo_intent" not in src
    # Phrases, not one sentence: the prompt text wraps across source lines.
    for phrase in ("do not treat it", "must not appear in", "not a fact about the account"):
        assert phrase in src, phrase
