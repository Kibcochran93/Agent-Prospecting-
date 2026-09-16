"""The one outbound write in the system. Kept in its own module, apart from reads.

**No approval halt, by Kib's decision on 3 September 2026.** Drafts always go to
Apollo and Apollo is where he reads and approves them. A terminal prompt in front
of that was a second review of the same copy, one screen worse.

What holds instead is the same thing that always did the real work, and it is
structural rather than procedural: the sequence is created paused and every step
is a manual email. Nothing here can activate, enrol, or send. The worst case from
an unattended run is a queue of drafts nobody asked for, which is clutter, not
mail leaving the building. A per-run cap on sequences created is the obvious next
guard if that clutter ever shows up; it does not exist yet.

This lived in ``tools/apollo.py`` until the read tools were added there and
overwrote it. The test suite caught the deletion, and the fix is the better
structure: the read module now physically cannot contain a write, and this
module contains nothing else.

Why it is a local function tool rather than a hosted Apollo MCP tool:

The Apollo MCP surface exposes ``apollo_sequences_create`` and
``apollo_emailer_campaigns_approve`` (plus send-now and enrollment tools). An
``allowed_tools`` list can keep activation out of the model's reach, which is
the right control. What it cannot do is guarantee that a create call carries an
inactive state, because that lives in the call's arguments rather than in the
tool identity.

So the create goes through a local tool that owns the argument construction.
The model supplies the proposal; this function decides the state fields. The
model cannot set a sequence active because it never names that field.

Two state fields are owned here, not by the model:

``active: False``
    The sequence is created paused. Kib activates it in Apollo or not at all.

``type: "manual_email"``
    This is the stronger of the two and it is why the tool is named for drafts.
    An ``auto_email`` step sends by itself the moment the sequence is activated
    and a contact is enrolled. A ``manual_email`` step cannot: it produces a
    task in Apollo with the copy pre-filled, which a person reads and sends by
    hand. So the worst case if this sequence is activated by accident is a queue
    of drafts waiting for someone, not mail leaving the building.

    Verified against Kib's own Apollo account rather than assumed: ``manual_email``
    appears on 15 existing steps across the team's sequences, so it is a real
    step type in this workspace and not a guess at the API.

What is deliberately absent from this module: any way to activate a sequence,
enrol a contact, create a draft against a mailbox, or send. Apollo exposes all
four. None of them is imported here, and ``tests/test_apollo.py`` asserts that.
The Apollo draft endpoint in particular is one call away from ``send_now``, and
its own documentation encourages chaining the two, which is exactly why this
system does not hold it.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from agents import RunContextWrapper
from agents.decorators import tool

from ..apollo_oauth import OAuthFailed, OAuthNotConfigured, bearer_headers, current_token
from ..differentiation import strip_signature
from ..context import DispatchContext
from ..schemas import SequenceProposal

APOLLO_API = "https://api.apollo.io/api/v1"

# Filing, Kib's instruction on 4 September and revised the same day. Everything
# this system writes belongs together in Apollo, but a sequence here is one
# person, so the title is theirs: "Dewayne Dickens | Tulsa Community College".
# The grouping moved to an Apollo label, which filters without occupying the
# name. Set APOLLO_SEQUENCE_PREFIX to bring a name prefix back.
#
# A sequence is one cadence, so every contact enrolled in it receives every step.
# That is why the unit is one recipient and not one batch. Apollo has no folder
# API, only a folder_id on create, so set APOLLO_SEQUENCE_FOLDER_ID from the
# folder URL if you want these in a real folder too.
SEQUENCE_PREFIX = os.environ.get("APOLLO_SEQUENCE_PREFIX", "")
SEQUENCE_LABEL = os.environ.get("APOLLO_SEQUENCE_LABEL", "AI Agent Prospecting Ops")

# Kib's Apollo user id, for resolving his own sending mailbox at enrollment
# time (drafts.py). Confirmed 15 September 2026 via GET /email_accounts:
KIB_APOLLO_USER_ID = os.environ.get("KIB_APOLLO_USER_ID", "69d808314b6a820015875600")


def sequence_title(person: str, institution: str) -> str:
    """The label for a one-person sequence: who, then where.

    Kib's convention, 4 September. A sequence here is one recipient, so the
    person is what he is looking for in a list of them, and the institution is
    the disambiguator rather than the heading. ``filed_name`` still puts the
    filing prefix in front.
    """
    person = " ".join(person.split()).strip(" |")
    institution = " ".join(institution.split()).strip(" |")
    if not person:
        return institution
    if not institution:
        return person
    return f"{person} | {institution}"


def filed_name(label: str, prefix: str | None = None) -> str:
    """The name Apollo sees. Prefixed once, never twice."""
    prefix = (prefix if prefix is not None else SEQUENCE_PREFIX).strip()
    label = label.strip()
    if not prefix:
        return label
    if label.casefold().startswith(prefix.casefold()):
        return label
    return f"{prefix} | {label}"


def _as_html(body: str) -> str:
    """Plain copy to the HTML Apollo stores, without inventing formatting.

    The typed sign-off comes off here. Apollo appends Kib's own signature block,
    with his title and booking link, to every step it renders. A model that also
    types "Kib Cochran / Solutions Engineer / SEAtS Software" produces a draft
    signed twice, which is what the first batch looked like in the task queue on
    4 September.
    """
    from html import escape

    body = strip_signature(body)
    paragraphs = [escape(block.strip()) for block in body.split("\n\n") if block.strip()]
    return "".join(f"<p>{para.replace(chr(10), '<br>')}</p>" for para in paragraphs)


def _relative_waits(touches: list) -> list[int]:
    """Apollo waits are measured from the previous step; ours are from enrollment.

    ``SequenceTouch.day_offset`` says how many days after enrollment a touch
    goes, which is how a person thinks about a cadence. Apollo's ``wait_time``
    is the gap since the step before it. Sending our numbers straight through
    would stretch a day-0/day-6/day-13 cadence into day 0, 6, 19.
    """
    waits: list[int] = []
    previous = 0
    for touch in touches:
        waits.append(max(0, touch.day_offset - previous))
        previous = touch.day_offset
    return waits


def sequence_payload(proposal: SequenceProposal) -> dict[str, Any]:
    """Build the exact POST /sequences body. Pure, so it can be tested.

    Two rewrites are baked in here, both found by writing to Apollo for real on
    3 and 4 September 2026.

    **The endpoint.** This posted to ``/emailer_campaigns``, which returned 200
    and created a sequence with zero steps. The documented create endpoint is
    ``/sequences``, and the steps only land there.

    **The step shape.** Each email step carries ``emailer_touches``, and the copy
    lives in a touch's ``emailer_template`` as ``subject`` and ``body_html``.
    This module was sending ``emailer_templates`` on the step, which Apollo
    ignored. Both shapes are taken from Apollo's own OpenAPI specification
    rather than guessed.

    The model supplies ``proposal``. It does not supply ``active`` or ``type``,
    and its schema has no field for either.
    """
    touches = sorted(proposal.touches, key=lambda t: t.step)
    waits = _relative_waits(touches)
    payload: dict[str, Any] = {
        # Owned here, never by the model. Kib's filing, not the run's.
        "name": filed_name(proposal.sequence_name),
        # Owned here, never by the model. Created paused.
        "active": False,
        "emailer_steps": [
            {
                # Owned here, never by the model. A manual step cannot send
                # itself; it queues a draft with the copy pre-filled for a
                # person to read and send.
                "type": "manual_email",
                "wait_time": wait,
                "wait_mode": "day",
                "emailer_touches": [
                    {
                        "type": "new_thread",
                        # The template is usable rather than half-written. It
                        # still cannot send: the step is manual.
                        "status": "approved",
                        "include_signature": True,
                        "emailer_template": {
                            "subject": touch.subject,
                            "body_html": _as_html(touch.body),
                        },
                    }
                ],
            }
            for touch, wait in zip(touches, waits)
        ],
    }
    label = SEQUENCE_LABEL.strip()
    if label:
        # Owned here, never by the model. This is how every sequence the system
        # writes stays findable in one place now that the name is the person's.
        payload["label_names"] = [label]
    folder = (os.environ.get("APOLLO_SEQUENCE_FOLDER_ID") or "").strip()
    if folder:
        payload["folder_id"] = folder
    return payload


def update_payload(
    proposal: SequenceProposal,
    existing: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """The PUT /sequences/{id} body, which is not the POST body.

    Two differences, both found the hard way on 4 September when a create-shaped
    payload came back 422 "Missing Step 1 for this sequence":

    * every step carries a ``position``
    * a step that already exists carries its ``id``, and so does its touch

    PUT replaces the step list rather than appending to it, so touch 1 has to
    travel with the new ones or it is deleted. ``existing`` is the (step id,
    touch id) pairs already in Apollo, in order; anything beyond them is new.
    """
    touches = sorted(proposal.touches, key=lambda t: t.step)
    waits = _relative_waits(touches)
    existing = existing or []

    steps: list[dict[str, Any]] = []
    for index, (touch, wait) in enumerate(zip(touches, waits)):
        touch_body: dict[str, Any] = {
            "status": "approved",
            "emailer_template": {
                "subject": touch.subject,
                "body_html": _as_html(touch.body),
            },
        }
        step: dict[str, Any] = {
            "position": index + 1,
            # Owned here, never by the model, on update as on create.
            "type": "manual_email",
            "wait_time": wait,
            "wait_mode": "day",
        }
        if index < len(existing):
            step_id, touch_id = existing[index]
            step["id"] = step_id
            touch_body["id"] = touch_id
        step["emailer_touches"] = [touch_body]
        steps.append(step)

    payload: dict[str, Any] = {
        "name": proposal.sequence_name,
        # The sequence stays paused on update too. An update that quietly
        # activated a sequence would be the worst possible bug in this module.
        "active": False,
        "emailer_steps": steps,
    }
    label = SEQUENCE_LABEL.strip()
    if label:
        payload["label_names"] = [label]
    return payload


@tool
async def create_manual_email_drafts(
    ctx: RunContextWrapper[DispatchContext],
    proposal: SequenceProposal,
) -> str:
    """Save the batch into Apollo as manual-email drafts, in a paused sequence.

    Every touch becomes a task in Apollo with its subject and body pre-filled,
    for Kib to read and send by hand. This tool cannot activate the sequence,
    cannot enrol a contact, and cannot send. Those tools are not in this module.

    Call this at the end of workflow step 5. This is where a batch lands: Kib
    reads and approves the drafts in Apollo, not in this conversation.

    Args:
        proposal: The full sequence. Name, every touch with its exact subject
            and body, the contact count, and the filter logic that built the
            list. All of it appears in the approval payload Kib reads, so
            nothing may be summarized or referenced from elsewhere.
    """
    try:
        headers = bearer_headers(current_token())
        acting_as = "your Apollo user, through OAuth"
    except (OAuthNotConfigured, OAuthFailed) as exc:
        # There is no fallback, and that is Kib's decision of 4 September 2026.
        #
        # This used to fall back to the API key and say loudly whose account the
        # sequence would land in. That was the honest version of the wrong
        # behaviour: Apollo runs every api-key request as the workspace's
        # longest-standing admin, so five sequences were created under Miguel
        # Pescador's name and could not be found by the person who asked for
        # them. A disclosure does not fix a write that goes to the wrong
        # account; it only makes it legible afterwards.
        #
        # Failing here costs a re-login. The fallback cost an afternoon.
        return (
            "No Apollo OAuth token, so nothing was created. The copy is unchanged "
            "above; run `python -m seats_prospecting.apollo_oauth login` and try "
            "again, or paste it into Apollo manually. There is no API-key "
            f"fallback: it would create the sequence under another user. ({exc})"
        )

    payload = sequence_payload(proposal)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            # /sequences, not /emailer_campaigns. The latter takes the request,
            # returns 200, and drops every step.
            f"{APOLLO_API}/sequences",
            headers=headers,
            content=json.dumps(payload),
        )

    ctx.context.audit.append(
        f"apollo create_manual_email_drafts '{proposal.sequence_name}' -> "
        f"{resp.status_code}, as {acting_as}"
    )

    if resp.status_code >= 400:
        return (
            f"Apollo refused the create ({resp.status_code}): {resp.text[:400]}\n"
            "Nothing was created and nothing was activated. Report this and stop; "
            "do not retry with different arguments."
        )

    body = resp.json()
    campaign = body.get("emailer_campaign") or {}
    seq_id = campaign.get("id", "unknown")
    # The create returns steps as a sibling of the campaign, not nested in it.
    steps_created = len(body.get("emailer_steps") or campaign.get("emailer_steps") or [])
    wanted = len(proposal.touches)

    ctx.context.audit.append(
        f"apollo sequence {seq_id}: {steps_created} of {wanted} steps recorded"
    )

    if steps_created != wanted:
        # Found live on 3 September 2026. The create returned 200 and Apollo
        # made an empty paused sequence: the nested emailer_steps in the request
        # body were ignored. The old code counted the proposal rather than the
        # response and reported four drafts that were not there.
        #
        # Never describe copy as being in Apollo unless Apollo says it is.
        return (
            f"Apollo created the sequence '{proposal.sequence_name}' (id {seq_id}) but "
            f"recorded {steps_created} of {wanted} steps, so THE COPY IS NOT IN APOLLO. "
            "The sequence is empty and paused. Steps are not created by the sequence "
            "endpoint in this workspace; they need their own call, which this tool does "
            "not make yet.\n"
            "Report this plainly, output the copy for manual paste, and do not retry."
        )

    return (
        f"Saved '{proposal.sequence_name}' (id {seq_id}) to Apollo as {steps_created} "
        f"manual-email drafts in a paused sequence, created as {acting_as}, confirmed "
        "against Apollo's response. "
        "Every touch is a task with the copy pre-filled, waiting to be read and sent by "
        "hand. Nothing is active, no contacts are enrolled, and no step can send "
        "itself. Enrolling contacts, activating the sequence and sending are Kib's, "
        "in Apollo."
    )
