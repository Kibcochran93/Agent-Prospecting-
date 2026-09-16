"""Phase 2: the context verdict stops a dispatch, structurally.

Phase 1 established the verdict and blocked nothing. This is the phase where it
binds. Everything here asserts a refusal, which is the same shape as the rest of
the suite: the control is that a path is missing, not that a prompt asks nicely.

One deviation from the scope is tested here on purpose. The scope proposed a
required `context: ContextVerdict` field on `WorkOrder`, reasoning that the
Director "cannot construct a passing verdict itself because it holds no CRM
tool". That is an argument about obtaining evidence, and typing JSON needs no
tool: a Director with a keyboard can write `status="net_new"` and a plausible
`searched` line. A required field would have made that mandatory rather than
impossible. So the verdict is carried on the run context, stamped by the
launcher, and `test_the_work_order_carries_no_verdict_field` pins that choice.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pydantic
import pytest
from agents import RunContextWrapper

from seats_prospecting.agents_def import build_agents
from seats_prospecting.context import DispatchContext
from seats_prospecting.handoff_wiring import (
    BLOCKING,
    _dispatch_recorder,
    _verdict_covers,
    work_order_only,
)
from seats_prospecting.schemas import ContextVerdict, WorkOrder


def order(target="Wiley University", motion="unclear", question="what next?"):
    return WorkOrder(target=target, motion=motion, question=question)


def verdict(status="net_new", account="Wiley University", **kw):
    base = dict(
        status=status,
        account=account,
        searched=["HubSpot companies, domain wiley.edu, no match"],
    )
    if status != "net_new":
        base["evidence"] = ["HubSpot company 8675080439, owner Miguel Pescador"]
    base.update(kw)
    return ContextVerdict(**base)


def dispatch(ctx: DispatchContext, work_order: WorkOrder, target="Prospect Briefing"):
    recorder = _dispatch_recorder(target)
    return asyncio.run(recorder(RunContextWrapper(ctx), work_order))


# --- the gate -------------------------------------------------------------


def test_a_run_with_no_verdict_cannot_dispatch():
    ctx = DispatchContext()
    with pytest.raises(RuntimeError) as exc:
        dispatch(ctx, order())
    assert "no account context verdict" in str(exc.value).lower()
    assert ctx.handoff_count == 0, "a refused dispatch must not record one"


def test_net_new_dispatches():
    ctx = DispatchContext()
    ctx.stamp_context(verdict("net_new"))
    dispatch(ctx, order())
    assert ctx.handoff_count == 1


def test_known_inactive_dispatches():
    """A record we hold that is not in play is a reason to start from what we
    have, not a reason to stop."""
    ctx = DispatchContext()
    ctx.stamp_context(verdict("known_inactive"))
    dispatch(ctx, order())
    assert ctx.handoff_count == 1


@pytest.mark.parametrize("status", BLOCKING)
def test_a_blocking_verdict_refuses(status):
    ctx = DispatchContext()
    ctx.stamp_context(verdict(status))
    with pytest.raises(RuntimeError) as exc:
        dispatch(ctx, order())
    message = str(exc.value)
    assert status in message
    assert "--override" in message, "a refusal has to say how Kib proceeds"
    assert "8675080439" in message, "the evidence goes in the refusal, not just the status"
    assert ctx.handoff_count == 0


@pytest.mark.parametrize("status", BLOCKING)
def test_an_override_lets_it_through_and_is_recorded(status):
    ctx = DispatchContext()
    ctx.stamp_context(verdict(status), "Miguel asked me to take this one")
    dispatch(ctx, order())
    assert ctx.handoff_count == 1
    trail = " ".join(ctx.audit)
    assert "Miguel asked me to take this one" in trail
    assert status in trail


def test_an_empty_override_is_no_override():
    ctx = DispatchContext()
    ctx.stamp_context(verdict("known_active"), "   ")
    with pytest.raises(RuntimeError):
        dispatch(ctx, order())


def test_one_handoff_per_run_still_holds():
    ctx = DispatchContext()
    ctx.stamp_context(verdict("net_new"))
    dispatch(ctx, order())
    with pytest.raises(RuntimeError) as exc:
        dispatch(ctx, order())
    assert "one handoff per director run" in str(exc.value).lower()


# --- the verdict has to be about this account -----------------------------


def test_a_verdict_for_another_account_refuses():
    """Worse than no verdict, because it looks like a check ran."""
    ctx = DispatchContext()
    ctx.stamp_context(verdict("net_new", account="Wiley University"))
    with pytest.raises(RuntimeError) as exc:
        dispatch(ctx, order(target="Creighton University"))
    assert "wrong account" in str(exc.value).lower() or "targets" in str(exc.value)


def test_matching_tolerates_the_way_people_write_targets():
    v = verdict("net_new", account="Tulsa Community College")
    assert _verdict_covers(v, order(target="Tulsa Community College"))
    assert _verdict_covers(v, order(target="Dewayne Dickens, Tulsa Community College"))
    assert not _verdict_covers(v, order(target="Creighton University"))


def test_matching_finds_the_person_when_the_target_names_them():
    v = verdict("known_active", account="Creighton University", person="Wayne Young Jr.")
    assert _verdict_covers(v, order(target="Wayne Young Jr."))


# --- what the worker sees -------------------------------------------------


class _Data:
    """Minimal stand-in for HandoffInputData."""

    def __init__(self, ctx):
        self.run_context = RunContextWrapper(ctx)
        self.cloned = None

    def clone(self, **kw):
        self.cloned = kw
        return kw


def test_the_worker_is_given_the_verdict_from_the_run_not_the_payload():
    ctx = DispatchContext()
    ctx.stamp_context(verdict("known_inactive", account="Wiley University"))
    ctx.work_order = order()
    payload = work_order_only(_Data(ctx))["input_history"]
    assert "ACCOUNT CONTEXT" in payload
    assert "known_inactive" in payload
    assert "wiley.edu" in payload, "the searches travel with the verdict"


def test_the_worker_is_told_when_kib_overrode_and_that_it_changes_nothing():
    ctx = DispatchContext()
    ctx.stamp_context(verdict("known_active"), "Miguel handed it over")
    ctx.work_order = order()
    payload = work_order_only(_Data(ctx))["input_history"]
    assert "Miguel handed it over" in payload
    assert "not an instruction" in payload, (
        "an override is context for the worker, never a widened permission"
    )


# --- the shape of the control --------------------------------------------


def test_the_work_order_carries_no_verdict_field():
    """A field the model types is a field the model can fabricate.

    The Director holds no CRM tool, so it cannot obtain evidence. It can still
    type `status="net_new"`, and a required field would have obliged it to.
    """
    assert "context" not in WorkOrder.model_fields
    assert "verdict" not in WorkOrder.model_fields
    with pytest.raises(pydantic.ValidationError):
        WorkOrder(target="X", motion="unclear", question="?", context={"status": "net_new"})


def test_no_agent_can_stamp_a_verdict():
    """Only the launcher calls stamp_context, and no agent runs the launcher."""
    source = Path("src/seats_prospecting/handoff_wiring.py").read_text(encoding="utf-8")
    assert "stamp_context" not in source, (
        "the dispatch gate reads the verdict; anything that writes one belongs "
        "outside the agent runtime"
    )
    for module in ("agents_def", "connectors", "runner"):
        text = Path(f"src/seats_prospecting/{module}.py").read_text(encoding="utf-8")
        assert "stamp_context" not in text


def test_the_launcher_stamps_it_and_refuses_a_bare_override():
    source = Path("scripts/live_run.py").read_text(encoding="utf-8")
    assert "stamp_context" in source
    assert "--verdict" in source
    assert "overrides nothing" in source, (
        "--override with no verdict has to fail loudly; the dispatch would "
        "refuse for want of a verdict and the flag would look like it did nothing"
    )


# --- the Director's reads -------------------------------------------------


def test_the_director_holds_no_crm_read_at_all():
    network = build_agents()
    for tool in network["director"].tools:
        name = getattr(tool, "name", type(tool).__name__).lower()
        assert "hubspot" not in name, f"the Director kept a CRM read: {name}"
        assert "apollo" not in name


def test_the_director_prompt_no_longer_tells_it_to_read_the_crm():
    text = (Path("src/seats_prospecting/prompts/director.md")).read_text(encoding="utf-8")
    assert "hubspot_find_account" not in text
    assert "hubspot_account_deals" not in text
    flat = " ".join(text.split()).casefold()
    assert "you no longer read hubspot" in flat
    assert "you cannot dispatch without one" in flat


def test_the_director_is_given_the_verdict_to_read():
    """Found on the first live phase-2 run, not by reading the code.

    The gate held and the audit showed the verdict, and the Director still
    reported that none was provided. It was right: stamping put the verdict
    where the handoff filter reads it, and the handoff filter builds the
    WORKER's input. An agent asked to reason about a verdict has to be shown it.
    """
    source = Path("scripts/live_run.py").read_text(encoding="utf-8")
    assert "ACCOUNT CONTEXT VERDICT (system fact" in source
    assert "stamped.as_note()" in source
    # Prepended, not offered as a tool: a tool is something the Director
    # chooses to call, and this is a fact of the run.
    assert 'text = "\\n\\n".join(preamble)' in source


def test_an_override_is_explained_to_the_director_too():
    source = Path("scripts/live_run.py").read_text(encoding="utf-8")
    assert "has overridden this verdict for this run" in source
    assert "proceeded over the verdict and why" in source
