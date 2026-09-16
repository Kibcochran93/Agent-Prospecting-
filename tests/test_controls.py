"""Tests for the controls, not for the copy.

Every test here asserts something is ABSENT. That is the point: the controls in
this system are missing capabilities, narrow schemas, and isolated contexts, and
the failure mode to catch is one of them quietly coming back.
"""

from __future__ import annotations

import pydantic
import pytest
from agents import RunContextWrapper
from agents.handoffs import HandoffInputData
from agents.items import MessageOutputItem

from seats_prospecting.agents_def import build_agents
from seats_prospecting.context import DispatchContext
from seats_prospecting.handoff_wiring import work_order_only
from seats_prospecting.schemas import LedgerRecord, WorkOrder

WRITE_WORDS = ("create", "update", "delete", "send", "activate", "approve", "enroll")


@pytest.fixture(scope="module")
def network():
    return build_agents()


def tool_names(agent) -> list[str]:
    return [getattr(t, "name", type(t).__name__) for t in agent.tools]


def handoff_targets(agent) -> list[str]:
    return [getattr(h, "agent_name", getattr(h, "name", "?")) for h in agent.handoffs]


# --- topology -------------------------------------------------------------


def test_director_holds_no_write_tool(network):
    for name in tool_names(network["director"]):
        assert not any(w in name.lower() for w in WRITE_WORDS), (
            f"Director gained a write tool: {name}"
        )


def test_director_holds_no_apollo_access_at_all(network):
    assert not any("apollo" in n.lower() for n in tool_names(network["director"]))


def test_workers_cannot_reach_each_other(network):
    assert handoff_targets(network["briefing"]) == ["Director"]
    assert handoff_targets(network["campaign"]) == ["Director"]


def test_reviewer_is_unreachable_and_reaches_nothing(network):
    assert network["reviewer"].handoffs == []
    for agent in network.values():
        assert "Reviewer" not in handoff_targets(agent), (
            f"{agent.name} can summon the Reviewer, which ends its independence"
        )


def test_reviewer_holds_no_notion_read(network):
    # Ledger records would tell the reviewer what was already accepted.
    assert not any("notion" in n.lower() or "ledger" in n.lower() for n in tool_names(network["reviewer"]))


def test_reviewer_holds_no_write_tool(network):
    for name in tool_names(network["reviewer"]):
        assert not any(w in name.lower() for w in WRITE_WORDS)


def test_briefing_holds_no_apollo_write(network):
    names = [n.lower() for n in tool_names(network["briefing"])]
    assert not any("sequence" in n for n in names)


def test_campaign_holds_no_outlook(network):
    # Mail access on the agent that drafts outbound copy is how internal
    # framing ends up in external messages.
    assert not any("outlook" in n.lower() for n in tool_names(network["campaign"]))


def test_no_agent_holds_a_send_tool(network):
    for agent in network.values():
        for name in tool_names(agent):
            assert "send" not in name.lower(), f"{agent.name} can send: {name}"


# --- the payload schema ---------------------------------------------------


def test_work_order_rejects_evidence_and_copy():
    with pytest.raises(pydantic.ValidationError):
        WorkOrder(
            target="Ivy Tech Community College",
            motion="compliance",
            question="who owns clock-hour attendance",
            evidence=["enrollment 90k, source: IPEDS 2024"],
        )
    with pytest.raises(pydantic.ValidationError):
        WorkOrder(
            target="Ivy Tech Community College",
            motion="compliance",
            question="q",
            copy="Hi Dana, saw the new SIS rollout",
        )


def test_work_order_rejects_a_pre_cleared_gate():
    with pytest.raises(pydantic.ValidationError):
        WorkOrder(
            target="segment: Indiana community colleges",
            motion="compliance",
            question="q",
            approved=True,
        )


def test_work_order_free_text_is_bounded():
    with pytest.raises(pydantic.ValidationError):
        WorkOrder(target="x" * 500, motion="m", question="q")
    with pytest.raises(pydantic.ValidationError):
        WorkOrder(target="t", motion="m", question="q", constraints=["c"] * 20)


def test_ledger_record_cannot_leave_not_verified_empty():
    with pytest.raises(pydantic.ValidationError):
        LedgerRecord(
            institution="Ivy Tech Community College",
            motion="compliance",
            segment="community college, 50k+, Midwest",
            source_agent="Prospect Briefing",
            verified_through="2026-03-01",
            facts_carried_over=["Registrar named, source: institution site 2026-02-11"],
            not_verified=[],
            what_this_suggests="Worth a discovery call before any batch.",
        )


# --- context isolation ----------------------------------------------------


def _handoff_data(ctx: DispatchContext, *, with_history: bool) -> HandoffInputData:
    wrapper = RunContextWrapper(ctx)
    history = (
        (
            {"role": "user", "content": "here is everything I know about Ivy Tech"},
            {"role": "assistant", "content": "WORK PLAN: dispatch to Briefing because..."},
        )
        if with_history
        else ()
    )
    fake_item = object.__new__(MessageOutputItem)
    return HandoffInputData(
        input_history=history,
        pre_handoff_items=(fake_item,),
        new_items=(fake_item,),
        run_context=wrapper,
    )


def test_worker_inherits_only_the_work_order():
    ctx = DispatchContext()
    order = WorkOrder(
        target="Dana Reyes, Ivy Tech Community College",
        motion="clock-hour compliance",
        constraints=["no prior outreach"],
        question="who owns clock-hour attendance day to day",
    )
    ctx.record_dispatch("Prospect Briefing", order)

    filtered = work_order_only(_handoff_data(ctx, with_history=True))

    assert isinstance(filtered.input_history, str)
    assert "Dana Reyes" in filtered.input_history
    assert "WORK PLAN" not in filtered.input_history
    assert "everything I know" not in filtered.input_history
    assert filtered.pre_handoff_items == ()
    assert filtered.input_items == ()


def test_filter_fails_closed_without_a_payload():
    filtered = work_order_only(_handoff_data(DispatchContext(), with_history=True))
    assert "WORK ORDER MISSING" in filtered.input_history
    assert "WORK PLAN" not in filtered.input_history


def test_one_handoff_per_director_run():
    from seats_prospecting.handoff_wiring import _dispatch_recorder

    from seats_prospecting.schemas import ContextVerdict

    ctx = DispatchContext()
    # Phase 2: a dispatch needs a verdict covering the target, so this test
    # supplies one rather than testing the verdict gate, which has its own file.
    ctx.stamp_context(
        ContextVerdict(status="net_new", account="t", searched=["HubSpot, no match"])
    )
    order = WorkOrder(target="t", motion="m", question="q")
    recorder = _dispatch_recorder("Prospect Briefing")
    wrapper = RunContextWrapper(ctx)

    import asyncio

    asyncio.run(recorder(wrapper, order))
    assert ctx.handoff_count == 1

    with pytest.raises(RuntimeError, match="One handoff per director run"):
        asyncio.run(recorder(wrapper, order))


# --- what replaced the approval gate --------------------------------------


def test_the_sequence_write_is_not_gated_and_does_not_need_to_be():
    """Kib's decision, 3 September 2026: drafts always go to Apollo and he reads
    and approves them there. A terminal halt in front of that was a second review
    of the same copy on a worse screen.

    What carries the weight is the step type, asserted right below and in
    tests/test_manual_drafts.py. A manual-email step in a paused sequence cannot
    send itself, so an unattended run produces drafts nobody asked for rather
    than mail nobody approved.
    """
    from seats_prospecting.tools.apollo_write import create_manual_email_drafts

    assert create_manual_email_drafts.needs_approval is False


def test_nothing_in_the_system_can_send_or_activate():
    """The control that outlived the gate."""
    from seats_prospecting.schemas import SequenceProposal, SequenceTouch
    from seats_prospecting.tools.apollo_write import sequence_payload

    payload = sequence_payload(
        SequenceProposal(
            sequence_name="x",
            contact_count=1,
            source_list="y",
            touches=[
                SequenceTouch(
                    step=1,
                    day_offset=0,
                    subject="s",
                    body="a body long enough to be a sentence here",
                    recipient_role="operational owner",
                )
            ],
        )
    )
    assert payload["active"] is False
    assert {step["type"] for step in payload["emailer_steps"]} == {"manual_email"}


def test_ledger_write_is_not_gated():
    # An agent that asks constantly trains the user to approve without reading.
    from seats_prospecting.tools.notion_ledger import create_ledger_record

    assert create_ledger_record.needs_approval is False


def test_approval_render_shows_the_full_arguments():
    from seats_prospecting.runner import render_approval

    class FakeInterruption:
        name = "create_manual_email_drafts"
        arguments = (
            '{"proposal": {"sequence_name": "IN clock-hour Q4", "contact_count": 34, '
            '"source_list": "community college, IN, 10k-30k, clock-hour programs", '
            '"touches": [{"step": 1, "day_offset": 0, "subject": "R2T4 timing", '
            '"body": "Your December audit window overlaps census.", '
            '"recipient_role": "compliance stakeholder"}]}}'
        )

    text = render_approval(FakeInterruption(), "Campaign Builder")
    assert "December audit window overlaps census" in text
    assert "IN clock-hour Q4" in text
    assert "34" in text
    assert "community college, IN, 10k-30k" in text
    assert "nothing has executed" in text


def test_apollo_write_is_on_by_default_and_only_the_campaign_builder_holds_it():
    """Inverted on 3 September 2026: a batch always lands in Apollo as drafts.

    The assertion worth keeping is not that the capability is absent but that it
    is in exactly one place. A planner that can write a sequence starts writing
    them; a reviewer that can write one is grading its own work by the next pass.
    """
    import importlib

    from seats_prospecting import agents_def, connectors, settings

    importlib.reload(settings)
    importlib.reload(connectors)
    importlib.reload(agents_def)

    assert settings.APOLLO_WRITE_ENABLED is True
    agents = agents_def.build_agents()
    assert "create_manual_email_drafts" in tool_names(agents["campaign"])
    for other in ("director", "briefing", "reviewer"):
        assert "create_manual_email_drafts" not in tool_names(agents[other])


def test_model_cannot_name_the_active_field():
    from seats_prospecting.schemas import SequenceProposal

    fields = set(SequenceProposal.model_fields)
    assert "active" not in fields
    with pytest.raises(pydantic.ValidationError):
        SequenceProposal(
            sequence_name="x",
            touches=[
                {
                    "step": 1,
                    "day_offset": 0,
                    "subject": "s",
                    "body": "b",
                    "recipient_role": "r",
                }
            ],
            contact_count=1,
            source_list="l",
            active=True,
        )


# --- the handback path -----------------------------------------------------
# Regression tests for a defect found in the first live run: after a worker
# handed control back, the Director reported "no handoff made" while the audit
# trail showed the transfer had happened.


def test_handback_carries_the_dispatch_record():
    from seats_prospecting.handoff_wiring import dispatch_aware_handback

    ctx = DispatchContext()
    order = WorkOrder(
        target="Ivy Tech Community College, Indiana",
        motion="unclear",
        constraints=["no prior outreach"],
        question="who owns clock-hour attendance day to day",
    )
    ctx.record_dispatch("Prospect Briefing", order)

    filtered = dispatch_aware_handback(_handoff_data(ctx, with_history=True))
    header = filtered.input_history[0]["content"]

    assert "DISPATCH RECORD" in header
    assert "Prospect Briefing" in header
    assert "Ivy Tech Community College" in header
    assert ctx.dispatched_at in header
    assert "Do not state that no handoff was made" in header


def test_handback_keeps_kibs_original_request():
    from seats_prospecting.handoff_wiring import dispatch_aware_handback

    ctx = DispatchContext()
    ctx.record_dispatch(
        "Prospect Briefing", WorkOrder(target="t", motion="m", question="q")
    )

    filtered = dispatch_aware_handback(_handoff_data(ctx, with_history=True))
    joined = " ".join(item["content"] for item in filtered.input_history)

    # The Director needs Kib's ask back to answer him, unlike a worker.
    assert "everything I know about Ivy Tech" in joined


def test_handback_without_a_dispatch_record_is_left_alone():
    from seats_prospecting.handoff_wiring import dispatch_aware_handback

    filtered = dispatch_aware_handback(_handoff_data(DispatchContext(), with_history=True))
    assert all("DISPATCH RECORD" not in i["content"] for i in filtered.input_history)
