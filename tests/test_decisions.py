"""What the decision path must not be able to do.

Every assertion here is about an absent capability, which is the pattern the
rest of this suite keeps: a guarantee held by a rule can be argued away, one
held by a missing import cannot.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from seats_prospecting import decisions

FORBIDDEN = ("httpx", "requests", "apollo", "notion", "send_now", "approve", "enrol")


def test_decisions_module_reaches_no_connector():
    src = Path(decisions.__file__).read_text(encoding="utf-8").lower()
    code = "\n".join(
        line for line in src.splitlines()
        if line.strip().startswith(("import ", "from ")) or "(" in line
    )
    for name in ("httpx", "requests", "apollo", "notion", "send_now"):
        assert f"import {name}" not in code
        assert f"from {name}" not in code


def test_the_decide_handler_calls_only_the_recorder():
    import sys

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "scripts"))
    import board_serve

    names = board_serve.Handler.do_POST.__code__.co_names
    assert "record" in names
    for name in FORBIDDEN:
        assert not any(name in n.lower() for n in names), names


def test_an_agent_cannot_write_approved(tmp_path: Path):
    for who in ("agent", "system", "", "   "):
        with pytest.raises(decisions.DecisionRefused):
            decisions.record(
                "Lyon College", "lyon", "Approved",
                decided_by=who, source="test", directory=tmp_path,
            )
    assert list(tmp_path.glob("*.json")) == []


def test_an_agent_may_write_pending(tmp_path: Path):
    path = decisions.record(
        "Lyon College", "lyon", "Pending owner review",
        decided_by="agent", source="test", directory=tmp_path,
    )
    assert path.exists()


def test_an_unknown_decision_is_refused(tmp_path: Path):
    with pytest.raises(decisions.DecisionRefused):
        decisions.record(
            "Lyon College", "lyon", "Open", decided_by="Kib Cochran",
            source="test", directory=tmp_path,
        )


def test_recording_never_overwrites(tmp_path: Path):
    when = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    decisions.record(
        "Lyon College", "lyon", "Approved", decided_by="Kib Cochran",
        source="test", directory=tmp_path, now=when,
    )
    with pytest.raises(decisions.DecisionRefused):
        decisions.record(
            "Lyon College", "lyon", "Approved", decided_by="Kib Cochran",
            source="test", directory=tmp_path, now=when,
        )
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_a_reversal_is_a_second_record_and_the_first_survives(tmp_path: Path):
    first = decisions.record(
        "Lyon College", "lyon", "Approved", decided_by="Kib Cochran",
        source="test", directory=tmp_path,
        now=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc),
    )
    decisions.record(
        "Lyon College", "lyon", "Declined", decided_by="Kib Cochran",
        source="test", directory=tmp_path,
        now=datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc),
    )
    assert first.exists()
    assert json.loads(first.read_text(encoding="utf-8"))["decision"] == "Approved"
    assert decisions.current(tmp_path)["lyon"].decision == "Declined"
    assert len(decisions.history("lyon", tmp_path)) == 2


def test_a_decision_records_no_action_fields(tmp_path: Path):
    """A decision is an intent. It must not carry anything that looks like a send."""
    path = decisions.record(
        "Lyon College", "lyon", "Approved", decided_by="Kib Cochran",
        source="test", directory=tmp_path,
    )
    body = json.loads(path.read_text(encoding="utf-8"))
    for key in ("sequence_id", "contact_id", "sent", "send_at", "enrolled", "mailbox"):
        assert key not in body


# --- two people, two decisions (ADR 0002) ---------------------------------


def _dec(kind, decision, who="Kib Cochran", recorded_by="", at="2026-09-10T10:00:00+00:00"):
    return decisions.Decision(
        account="Lyon College", account_key="lyon", kind=kind, decision=decision,
        decided_at=at, decided_by=who, recorded_by=recorded_by or who, source="test",
    )


def test_an_owner_review_and_a_kib_decision_coexist(tmp_path):
    decisions.record("Lyon College", "lyon", "Approved", decided_by="Cal O'Donovan",
                     recorded_by="Kib Cochran", source="Teams, 10 Sep",
                     kind=decisions.OWNER_REVIEW, directory=tmp_path)
    decisions.record("Lyon College", "lyon", "Approved", decided_by="Kib Cochran",
                     source="board button", directory=tmp_path)
    assert decisions.owner_reviews(tmp_path)["lyon"].decided_by == "Cal O'Donovan"
    assert decisions.current(tmp_path)["lyon"].decided_by == "Kib Cochran"
    assert len(decisions.history("lyon", tmp_path)) == 2


def test_a_secondhand_review_is_marked_as_such(tmp_path):
    path = decisions.record(
        "Lyon College", "lyon", "Approved", decided_by="Cal O'Donovan",
        recorded_by="Kib Cochran", source="Teams", kind=decisions.OWNER_REVIEW,
        directory=tmp_path)
    entry = [d for d in decisions.load_all(tmp_path) if d.file == path.name][0]
    assert entry.secondhand is True
    assert decisions.current(tmp_path).get("lyon") is None, "not a Kib decision"


def test_a_firsthand_decision_is_not_secondhand(tmp_path):
    decisions.record("Lyon College", "lyon", "Approved", decided_by="Kib Cochran",
                     source="board", directory=tmp_path)
    assert decisions.current(tmp_path)["lyon"].secondhand is False


def test_an_unknown_kind_is_refused(tmp_path):
    with pytest.raises(decisions.DecisionRefused):
        decisions.record("Lyon College", "lyon", "Approved", decided_by="Kib Cochran",
                         source="test", kind="whoever", directory=tmp_path)


def test_records_written_before_kinds_existed_read_as_kib_decisions(tmp_path):
    """The six seeded from Notion came from a property only Kib could write."""
    (tmp_path / "20260909T182600Z-lyon-approved.json").write_text(
        json.dumps({"account": "Lyon College", "account_key": "lyon",
                    "decision": "Approved", "decided_at": "2026-09-09T18:26:00+00:00",
                    "decided_by": "Kib Cochran", "source": "Notion export"}),
        encoding="utf-8")
    entry = decisions.current(tmp_path)["lyon"]
    assert entry.kind == decisions.KIB_DECISION
    assert entry.recorded_by == "Kib Cochran"


# --- precedence (ADR 0001) ------------------------------------------------


def test_kibs_decision_governs_and_names_what_it_overrode():
    res = decisions.resolve("Pending owner review", kib=_dec(decisions.KIB_DECISION, "Approved"))
    assert res.effective == "Approved"
    assert res.disagreement is not None
    assert "Pending owner review" in res.disagreement
    assert "governs" in res.disagreement


def test_no_disagreement_is_reported_when_they_agree():
    res = decisions.resolve("Approved", kib=_dec(decisions.KIB_DECISION, "Approved"))
    assert res.disagreement is None


def test_an_unasked_colleague_owned_account_is_a_conversation_kib_owes():
    res = decisions.resolve("Pending owner review", account_owner="Cal O'Donovan")
    assert res.effective is None
    assert "ask Cal O'Donovan" in res.waiting_on


def test_an_owner_yes_hands_it_back_to_kib():
    res = decisions.resolve("Pending owner review",
                            owner=_dec(decisions.OWNER_REVIEW, "Approved", "Cal O'Donovan"))
    assert res.effective is None
    assert "Kib" in res.waiting_on


def test_an_owner_no_settles_it_without_kib():
    res = decisions.resolve("Pending owner review",
                            owner=_dec(decisions.OWNER_REVIEW, "Declined", "Cal O'Donovan"))
    assert res.effective == "Declined"
    assert res.waiting_on == "nobody"


def test_kib_can_overrule_an_owner_and_it_is_visible():
    """Recorded rather than prevented: Kib owns the call, and the record shows it."""
    res = decisions.resolve(
        "Pending owner review",
        kib=_dec(decisions.KIB_DECISION, "Approved"),
        owner=_dec(decisions.OWNER_REVIEW, "Declined", "Cal O'Donovan"),
    )
    assert res.effective == "Approved"
    assert res.owner is not None and res.owner.decision == "Declined"
