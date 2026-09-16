"""End-to-end flow with a scripted model.

Two things get exercised here that unit tests cannot reach: what the worker
actually receives after a real handoff, and what the write path does now that
Kib has taken the halt off it. The old test asserted the run stopped and a
rejection closed the write. Since 3 September the batch goes straight to Apollo
as drafts and Kib reviews it there, so what this asserts instead is that the run
does not stop, no approval surface is invoked, and the thing that keeps it safe
is the step type rather than a prompt.

This file used to open with "No network, no API key". That was true when it was
written and false from 4 September, when the API-key path was removed and the
write began using the OAuth token on disk. It then created a real paused
sequence on every run for six days. What keeps this offline now is
tests/conftest.py, not anything in this file.
"""

from __future__ import annotations

import json

import pytest
from agents import Runner
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call

from seats_prospecting.agents_def import build_agents
from seats_prospecting.context import DispatchContext
from seats_prospecting.runner import run
from seats_prospecting.schemas import ContextVerdict

WORK_ORDER = {
    "target": "Dana Reyes, Ivy Tech Community College",
    "motion": "clock-hour compliance",
    "constraints": ["no prior outreach"],
    "question": "who owns clock-hour attendance day to day",
}

# Phase 2: no dispatch happens without a verdict on the run. Stamped here the
# way the launcher stamps it, which is the only way it can be set.
CLEAN_VERDICT = ContextVerdict(
    status="net_new",
    account="Ivy Tech Community College",
    person="Dana Reyes",
    searched=["HubSpot companies, domain ivytech.edu, no match"],
)


@pytest.fixture
def network():
    return build_agents()


async def test_handoff_delivers_only_the_work_order(network):
    """The Director's plan must not reach the worker."""
    director = network["director"]
    briefing = network["briefing"]

    director.model = ScriptedModel(
        [
            ModelStep(
                output=[
                    assistant_message(
                        "WORK PLAN. Dispatch to Briefing: Dana Reyes, because the "
                        "December audit window is close. Not this week: everything "
                        "in Ohio. Assumes the Registrar still owns attendance."
                    ),
                    function_call(
                        "transfer_to_prospect_briefing",
                        json.dumps(WORK_ORDER),
                        call_id="call_handoff_1",
                    ),
                ]
            )
        ]
    )

    seen: list[str] = []

    briefing_script = ScriptedModel(
        [ModelStep(output=[assistant_message("Stating the payload back: ...")])]
    )
    original_get_response = briefing_script.get_response

    async def spy(*args, **kwargs):
        payload = kwargs.get("input", args[1] if len(args) > 1 else None)
        seen.append(json.dumps(payload, default=str))
        return await original_get_response(*args, **kwargs)

    briefing_script.get_response = spy  # type: ignore[method-assign]
    briefing.model = briefing_script

    ctx = DispatchContext()
    ctx.stamp_context(CLEAN_VERDICT)
    await Runner.run(
        director,
        "Kib here. Everything I know about Ivy Tech is in the shared doc.",
        context=ctx,
        max_turns=6,
    )

    assert seen, "the briefing agent was never called"
    worker_input = seen[-1]

    # What must be there.
    assert "Dana Reyes" in worker_input
    assert "clock-hour compliance" in worker_input
    assert "no prior outreach" in worker_input

    # What must not.
    assert "WORK PLAN" not in worker_input
    assert "December audit window" not in worker_input
    assert "Not this week" not in worker_input
    assert "shared doc" not in worker_input

    assert ctx.handoff_count == 1
    assert ctx.dispatched_to == "Prospect Briefing"


async def test_the_write_runs_without_a_halt_and_asks_nobody(monkeypatch):
    """No interruption, no approval surface, and no send-shaped call."""
    monkeypatch.setenv("APOLLO_SEQUENCE_WRITE", "true")
    # Nothing here disables the write. APOLLO_API_KEY used to, and stopped
    # meaning anything on 4 September when the fallback identity was removed.
    # The autouse fixtures in tests/conftest.py are what hold this offline.

    import importlib

    from seats_prospecting import agents_def, connectors, settings

    importlib.reload(settings)
    importlib.reload(connectors)
    importlib.reload(agents_def)

    campaign = agents_def.build_agents()["campaign"]
    assert any(
        getattr(t, "name", "") == "create_manual_email_drafts" for t in campaign.tools
    ), "write tool did not attach with APOLLO_SEQUENCE_WRITE=true"

    proposal = {
        "proposal": {
            "sequence_name": "IN clock-hour Q4",
            "contact_count": 34,
            "source_list": "community college, IN, 10k-30k, clock-hour programs",
            "touches": [
                {
                    "step": 1,
                    "day_offset": 0,
                    "subject": "R2T4 timing",
                    "body": "Your December audit window overlaps census.",
                    "recipient_role": "compliance stakeholder",
                }
            ],
        }
    }

    campaign.model = ScriptedModel(
        [
            ModelStep(
                output=[
                    function_call(
                        "create_manual_email_drafts",
                        json.dumps(proposal),
                        call_id="call_seq_1",
                    )
                ]
            ),
            ModelStep(
                output=[
                    assistant_message(
                        "Apollo is not configured on this run, so the copy is above "
                        "for manual paste."
                    )
                ]
            ),
        ]
    )

    asked: list[str] = []

    def should_never_be_called(interruption, agent_name):
        asked.append(getattr(interruption, "name", "?"))
        return False

    ctx = DispatchContext()
    result = await run(
        campaign,
        "Build the Indiana clock-hour batch.",
        context=ctx,
        decide=should_never_be_called,
        max_turns=8,
    )

    assert asked == [], "the write asked for approval; Kib took that off on 3 Sep"
    assert not any("rejected" in line for line in ctx.audit)
    assert result.final_output

    # apollo_write records an audit line only after it has posted. An empty
    # audit is the proof this test got to Apollo's door and not through it.
    assert not any(
        "create_manual_email_drafts" in line for line in ctx.audit
    ), f"a test reached live Apollo: {ctx.audit}"


def test_the_batch_that_would_have_been_written_is_paused_and_manual():
    """What replaced the halt. Asserted here as well as in test_manual_drafts,
    because this is the file that describes the flow."""
    from seats_prospecting.schemas import SequenceProposal, SequenceTouch
    from seats_prospecting.tools.apollo_write import sequence_payload

    payload = sequence_payload(
        SequenceProposal(
            sequence_name="IN clock-hour Q4",
            contact_count=34,
            source_list="community college, IN, clock-hour programs",
            touches=[
                SequenceTouch(
                    step=1,
                    day_offset=0,
                    subject="R2T4 timing",
                    body="Your December audit window overlaps census.",
                    recipient_role="compliance stakeholder",
                )
            ],
        )
    )
    assert payload["active"] is False
    assert payload["emailer_steps"][0]["type"] == "manual_email"
