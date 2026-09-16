"""Enrolment exists, and it still cannot send.

Kib's decision, 4 September 2026. Until then no agent could put a person into a
sequence, which meant a batch of drafts sat in Apollo addressed to nobody. The
capability moved the line between an agent and a named person's inbox, so what
holds it in place is asserted here rather than described in a prompt.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from seats_prospecting.tools.apollo_enrol import SequenceEnrolment, enrolment_payload

MODULE = Path("src/seats_prospecting/tools/apollo_enrol.py")


def _code_only(source: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _enrolment() -> SequenceEnrolment:
    return SequenceEnrolment(
        sequence_id="6a9adf429d162900101debd2",
        contact_id="6a9adfcc92e73b0018934a25",
        who="Dewayne Dickens, Tulsa Community College",
    )


def test_enrolment_is_always_paused():
    """A draft waiting for Kib, not a cadence that started itself."""
    payload = enrolment_payload(_enrolment(), "686d22278ceaa3000d33d445")
    assert payload["status"] == "paused"


def test_the_model_cannot_name_the_status():
    assert "status" not in SequenceEnrolment.model_fields
    with pytest.raises(ValidationError):
        SequenceEnrolment(
            sequence_id="a", contact_id="b", who="c", status="active"
        )


def test_one_contact_per_call():
    """A sequence is one cadence. Several people in one sequence means each of
    them receives every step in it."""
    payload = enrolment_payload(_enrolment(), "mailbox")
    assert payload["contact_ids"] == ["6a9adfcc92e73b0018934a25"]
    assert isinstance(SequenceEnrolment.model_fields["contact_id"].annotation, type)


def test_the_module_cannot_send_activate_or_reveal():
    code = _code_only(MODULE.read_text(encoding="utf-8"))
    for forbidden in ("send_now", "approve", "activate", "people/match", "reveal", "emailer_messages"):
        assert forbidden not in code, f"the enrolment module reaches {forbidden}"
    assert '"active"' not in code


def test_enrolment_requires_the_token_not_the_api_key():
    """An api key acts as the workspace admin, so a draft queued under it would
    be queued under someone else's name.

    The HTTP call moved to seats_prospecting.enrolment on 9 September so the
    queue runner could enrol without an agent run. The guarantee is unchanged
    and is now asserted where the call lives, plus here, so the tool cannot
    reintroduce a key of its own.
    """
    from pathlib import Path as _P

    core = _code_only(
        _P("src/seats_prospecting/enrolment.py").read_text(encoding="utf-8")
    )
    assert "current_token" in core
    assert "APOLLO_API_KEY" not in core

    tool = _code_only(MODULE.read_text(encoding="utf-8"))
    assert "APOLLO_API_KEY" not in tool


def test_only_the_campaign_builder_holds_it(monkeypatch):
    import importlib

    from seats_prospecting import agents_def, connectors, settings

    monkeypatch.setenv("APOLLO_SEQUENCE_WRITE", "true")
    importlib.reload(settings)
    importlib.reload(connectors)
    importlib.reload(agents_def)

    agents = agents_def.build_agents()

    def names(agent):
        return [getattr(t, "name", "") for t in agent.tools]

    assert "add_contact_to_drafts" in names(agents["campaign"])
    for other in ("director", "briefing", "reviewer"):
        assert "add_contact_to_drafts" not in names(agents[other])


# --- phase 2: a live sequence blocks a second enrolment --------------------


def _enrolment_ctx(live=(), override=None):
    from seats_prospecting.context import DispatchContext
    from seats_prospecting.schemas import ContextVerdict

    ctx = DispatchContext()
    kw = dict(
        status="known_active" if live else "net_new",
        account="Creighton University",
        person="Wayne Young Jr.",
        searched=["Apollo contacts/search, name only"],
        live_sequences=list(live),
    )
    if live:
        kw["evidence"] = ["Apollo sequence 6a8899a4482aef0010cb668a, active 3 Sep 2026"]
    ctx.stamp_context(ContextVerdict(**kw), override)
    return ctx


class _Ctx:
    """Stands in for RunContextWrapper. Only .context is used, as elsewhere."""

    def __init__(self, context):
        self.context = context


def _enrol(ctx):
    from seats_prospecting.tools.apollo_enrol import live_sequence_block

    return live_sequence_block(_Ctx(ctx)) or ""


def test_a_live_sequence_stops_a_second_enrolment():
    """Wayne Young Jr. Apollo sends both cadences to the same inbox."""
    out = _enrol(_enrolment_ctx(live=["6a8899a4482aef0010cb668a"]))
    assert "NOBODY WAS ENROLLED" in out
    assert "6a8899a4482aef0010cb668a" in out, "name the sequence, not just the problem"
    assert "--override" in out


def test_the_override_carries_through_to_enrolment_and_is_recorded():
    """Kib's call: one override covers dispatch and enrolment both.

    That makes double-sending reachable by a single flag, which is why the
    refusal above spells out what proceeding means and why the audit records it.
    """
    ctx = _enrolment_ctx(live=["6a8899a4482aef0010cb668a"], override="running both on purpose")
    out = _enrol(ctx)
    assert "NOBODY WAS ENROLLED" not in out
    trail = " ".join(ctx.audit)
    assert "running both on purpose" in trail
    assert "6a8899a4482aef0010cb668a" in trail


def test_a_clean_verdict_does_not_block_enrolment():
    out = _enrol(_enrolment_ctx())
    assert "NOBODY WAS ENROLLED" not in out


def test_a_run_with_no_verdict_does_not_block_enrolment():
    """The enrolment gate reads a verdict when there is one and does not invent
    a refusal when there is not. The dispatch gate is what makes a verdict
    mandatory, and it runs first."""
    from seats_prospecting.context import DispatchContext

    out = _enrol(DispatchContext())
    assert "NOBODY WAS ENROLLED" not in out


def test_the_tool_actually_calls_the_gate():
    """The gate is only a control if the write path runs it."""
    from pathlib import Path as _P

    source = _P("src/seats_prospecting/tools/apollo_enrol.py").read_text(encoding="utf-8")
    body = source[source.index("async def add_contact_to_drafts") :]
    call = body.index("live_sequence_block(ctx)")
    # The anchor was APOLLO_MAILBOX_ID until the HTTP call moved into
    # seats_prospecting.enrolment. The thing that must not precede the gate is
    # now the enrolment call itself, which is a closer test of the same rule.
    write = body.index("_enrol(")
    assert call < write, (
        "the gate has to run before the enrolment call, so a refusal cannot be "
        "preempted by an unrelated configuration failure"
    )
    assert "APOLLO_MAILBOX_ID" not in body, (
        "the mailbox lookup lives in the core now; two copies would drift"
    )
