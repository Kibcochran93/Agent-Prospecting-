"""Put a named contact into their own draft sequence. Nothing else.

Added 4 September 2026, Kib's decision, after a batch of drafts sat in Apollo
addressed to nobody. A sequence with no contact enrolled cannot be sent, and
Apollo previews it against an unrelated person, which is what it looked like
from the task queue.

This is the capability the design deliberately withheld for months, so what it
holds and what it does not both matter:

``status: "paused"`` is owned by this module.
    Enrolment does not start a cadence. The contact sits in the sequence paused,
    which is a draft waiting for Kib. ``SequenceEnrolment`` has no status field,
    so the model cannot enrol anyone active.

There is still no send.
    Nothing here activates a sequence, approves one, or calls send_now. Every
    step this system writes is a manual email, so even an activated sequence
    produces tasks rather than mail. Enrolment moved the line; it did not remove
    it.

One contact per call, by construction.
    A sequence is one cadence. Enrolling several people in one sequence sends
    each of them every step in it, which is how five different messages to five
    different people become twenty-five wrong emails. The schema takes a single
    contact id.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from agents import RunContextWrapper
from agents.decorators import tool
from pydantic import BaseModel, ConfigDict, Field

from ..context import DispatchContext
from ..enrolment import EnrolRefused
from ..enrolment import enrol as _enrol
from ..enrolment import enrolment_payload as _payload

APOLLO_API = "https://api.apollo.io/api/v1"


class SequenceEnrolment(BaseModel):
    """One person, one sequence. No status field, deliberately."""

    model_config = ConfigDict(extra="forbid")

    sequence_id: str = Field(description="The Apollo sequence id returned when the drafts were written.")
    contact_id: str = Field(description="The Apollo contact id for the one person this sequence is for.")
    who: str = Field(
        max_length=200,
        description="Name and institution, so the run audit reads as something a person can check.",
    )


def live_sequence_block(ctx: RunContextWrapper[DispatchContext]) -> str | None:
    """The refusal text when this person is already in a running sequence.

    Phase 2, 4 September 2026. Apollo will send both cadences to the same
    inbox. Wayne Young Jr. is the case: enrolled in 6a8899a4482aef0010cb668a
    with activity the previous day, and a second sequence was built for him
    anyway because nothing in the system could see the first.

    Kib's call: the same --override that lets a blocked dispatch through lets
    this through too, so a deliberate run can proceed end to end. That makes
    double-sending reachable by one flag, which is why the refusal names the
    sequences and says plainly what proceeding means.

    Extracted from the tool so the gate can be tested without a token, a
    mailbox id, or a tool-invocation context.
    """
    verdict = getattr(ctx.context, "verdict", None)
    live = list(getattr(verdict, "live_sequences", []) or []) if verdict else []
    if not live:
        return None

    override = getattr(ctx.context, "override", None)
    if not override:
        return (
            "NOBODY WAS ENROLLED. Account context says this person is already in "
            f"{len(live)} running sequence(s): {', '.join(live)}.\n"
            "Enrolling them again means Apollo sends both cadences to the same "
            "inbox. Take them out of the other sequence, or re-run with "
            '--override "your reason" if you mean to run both.'
        )

    ctx.context.audit.append(
        f"enrolment proceeding over live sequences {', '.join(live)}; "
        f"override: {override}"
    )
    return None


def enrolment_payload(enrolment: SequenceEnrolment, mailbox_id: str) -> dict[str, Any]:
    """The exact POST body, built by the shared core.

    Kept as a function here because the existing tests call it by this name.
    The body, and the paused flag in it, are defined once in
    ``seats_prospecting.enrolment`` so the agent tool and the queue runner
    cannot drift apart on what enrolment means.
    """
    return _payload(enrolment.sequence_id, enrolment.contact_id, mailbox_id)


@tool
async def add_contact_to_drafts(
    ctx: RunContextWrapper[DispatchContext],
    enrolment: SequenceEnrolment,
) -> str:
    """Enrol one contact into the paused draft sequence written for them.

    The contact must already exist in Apollo. This tool cannot create a contact,
    reveal an email address, activate a sequence, or send anything.

    Args:
        enrolment: The sequence, the one contact, and who they are.
    """
    refusal = live_sequence_block(ctx)
    if refusal:
        return refusal

    try:
        message = _enrol(enrolment.sequence_id, enrolment.contact_id, enrolment.who)
    except EnrolRefused as exc:
        ctx.context.audit.append(f"apollo enrol refused for {enrolment.who}: {exc}")
        return str(exc)

    ctx.context.audit.append(
        f"apollo enrol {enrolment.who} -> {enrolment.sequence_id}: added, paused"
    )
    return message + (
        " The draft is now addressed to them and waiting in Kib's Apollo tasks."
    )
