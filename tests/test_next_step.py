"""What seats-next must never conclude.

Every test here pins an absence: a stage the system cannot take on, or a
conclusion it is not entitled to draw from a source it could not read.
"""

from __future__ import annotations

import pytest

from seats_prospecting.next_step import (
    ASK_OWNER,
    COPY,
    DECLINED,
    ENROL,
    SEND,
    SEQUENCE,
    UNDECIDED,
    UNKNOWN,
    WORKING,
    Facts,
    delivered_count,
    stage_for,
)

DELIVERED = {"id": "seq1", "delivered": 1, "num_steps": 4}
RAW_DELIVERED = {"id": "seq1", "unique_delivered": 1, "num_steps": 4}
EMPTY = {"id": "seq1", "delivered": 0, "num_steps": 4}
SHAPELESS = {"id": "seq1", "num_steps": 4}


def approved(**kw) -> Facts:
    return Facts(account="Test College", decision="Approved", **kw)


def test_send_is_never_automatable():
    facts = approved(copy_files=["copy.md"], sequences=[dict(DELIVERED, overdue_manual_tasks_count=2)])
    step = stage_for(facts)
    assert step.stage == SEND
    assert step.automatable is False
    assert step.waiting_on == "Kib"


def test_no_stage_that_sends_is_ever_automatable():
    """The only automatable stages are the two that cannot put mail in the world."""
    for stage in (COPY, SEQUENCE):
        pass
    facts = approved(copy_files=[], sequences=[])
    assert stage_for(facts).automatable is True
    facts = approved(copy_files=["copy.md"], sequences=[])
    assert stage_for(facts).automatable is True
    for sequences in ([EMPTY], [DELIVERED]):
        assert stage_for(approved(copy_files=["c.md"], sequences=sequences)).automatable is False


def test_a_missing_delivered_field_is_unknown_not_zero():
    """The fault of 9 September: a key read under the wrong name summed to zero.

    Three accounts with touch 1 delivered were reported as nobody enrolled.
    """
    assert delivered_count([SHAPELESS]) is None
    step = stage_for(approved(copy_files=["c.md"], sequences=[SHAPELESS]))
    assert step.stage == UNKNOWN
    assert "not reported as zero" in step.blocker.lower()


def test_both_field_names_are_read():
    assert delivered_count([DELIVERED]) == 1
    assert delivered_count([RAW_DELIVERED]) == 1
    assert delivered_count([DELIVERED, RAW_DELIVERED]) == 2
    assert stage_for(approved(copy_files=["c.md"], sequences=[RAW_DELIVERED])).stage == WORKING


def test_a_real_zero_is_still_a_zero():
    assert delivered_count([EMPTY]) == 0
    assert stage_for(approved(copy_files=["c.md"], sequences=[EMPTY])).stage == ENROL


def test_unread_apollo_never_produces_a_missing_sequence():
    step = stage_for(approved(copy_files=["c.md"], sequences=[], apollo_read=False))
    assert step.stage == UNKNOWN
    assert step.automatable is False


def test_unapproved_accounts_are_never_given_work():
    for decision in (None, "Pending owner review", "Not required"):
        step = stage_for(Facts(account="X", decision=decision))
        assert step.stage == UNDECIDED
        assert step.automatable is False


def test_declined_produces_no_work():
    step = stage_for(Facts(account="X", decision="Declined"))
    assert step.stage == DECLINED
    assert step.waiting_on == "nobody"
    assert step.automatable is False


def test_an_unread_task_queue_is_named_in_the_blocker():
    step = stage_for(approved(copy_files=["c.md"], sequences=[DELIVERED], tasks_read=False))
    assert step.stage == WORKING
    assert "task scope" in step.blocker


def test_approval_alone_does_not_reach_a_sending_stage():
    """Approve an account with nothing else done and the next step is copy."""
    assert stage_for(approved()).stage == COPY


# --- owner review is a conversation Kib owes (ADR 0002) -------------------


def test_a_colleague_owned_account_with_no_recorded_answer_says_go_ask():
    step = stage_for(Facts(account="Lyon College", account_owner="Cal O'Donovan"))
    assert step.stage == ASK_OWNER
    assert "ask Cal O'Donovan" in step.waiting_on
    assert step.automatable is False


def test_going_to_ask_is_never_something_a_tool_can_do():
    """No licence, no login, so nothing can resolve this but Kib."""
    step = stage_for(Facts(account="X", account_owner="Miguel Pescador"))
    assert step.stage == ASK_OWNER
    assert step.automatable is False
    assert "will not resolve itself" in step.blocker


def test_an_owner_yes_moves_it_to_kib():
    step = stage_for(Facts(account="Lyon College", account_owner="Cal O'Donovan",
                           owner_review="Approved"))
    assert step.stage == UNDECIDED
    assert step.waiting_on == "Kib"
    assert "cleared it" in step.blocker


def test_an_owner_no_stops_it():
    step = stage_for(Facts(account="Lyon College", account_owner="Cal O'Donovan",
                           owner_review="Declined"))
    assert step.stage == DECLINED
    assert step.automatable is False


def test_kibs_approval_still_wins_over_an_owner_no():
    step = stage_for(Facts(account="Lyon College", decision="Approved",
                           account_owner="Cal O'Donovan", owner_review="Declined"))
    assert step.stage == COPY


def test_an_account_kib_owns_does_not_wait_on_a_conversation():
    for owner in ("Kib Cochran", "unknown", ""):
        step = stage_for(Facts(account="X", account_owner=owner))
        assert step.stage == UNDECIDED, owner
