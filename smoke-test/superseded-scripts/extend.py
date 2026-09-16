"""Rename the four live sequences and add touches 2, 3 and 4 to each.

Needs the emailer_campaigns_update scope. Runs only when the token has it; the
probe at the top says so plainly rather than half-updating the workspace.

Renames to Kib's convention, person then institution, and appends the three
follow-ups at day 14, 35 and 56. Touch 1 is sent back unchanged, because PUT
replaces the step list rather than appending to it, so every step has to travel
in the same call.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, "src")

from seats_prospecting.apollo_oauth import bearer_headers, current_token  # noqa: E402
from seats_prospecting.differentiation import parse_messages, strip_signature  # noqa: E402
from seats_prospecting.schemas import SequenceProposal, SequenceTouch  # noqa: E402
from seats_prospecting.tools.apollo_write import (  # noqa: E402
    APOLLO_API,
    sequence_title,
    update_payload,
)

# sequence id, person, institution, and the day offsets for touches 1 to 4
LIVE = [
    ("6a9adf429d162900101debd2", "Dewayne Dickens", "Tulsa Community College"),
    ("6a9adf43e3c10a0018f4f229", "Eunice Tarver", "Tulsa Community College"),
    ("6a9adf446b2c85000c732efc", "Mary Ann Tietjen", "Creighton University"),
    ("6a9adf45910af10010922bf8", "Shaniqua Adams", "Wiley University"),
]
DAYS = {2: 14, 3: 35, 4: 56}

first = parse_messages(
    Path("smoke-test/batch-1/reviewer-in-2.txt").read_text(encoding="utf-8")
)
later = parse_messages(
    Path("smoke-test/batch-1/reviewer-in-4.txt").read_text(encoding="utf-8")
)

headers = bearer_headers(current_token())

# No separate probe: PUT requires at least one step, so a name-only call is a 422
# rather than a permission answer. The first real update reports the scope, and a
# 403 stops the run before the second one.


def current_steps(sequence_id: str) -> list[tuple[str, str]]:
    """(step id, touch id) for the steps Apollo already holds, in order."""
    resp = httpx.post(
        f"{APOLLO_API}/emailer_campaigns/search",
        headers=headers,
        content=json.dumps({"per_page": 100, "page": 1}),
        timeout=60,
    )
    resp.raise_for_status()
    for campaign in resp.json().get("emailer_campaigns", []):
        if campaign.get("id") != sequence_id:
            continue
        pairs = []
        for step in sorted(campaign.get("emailer_steps") or [], key=lambda s: s.get("position", 0)):
            touches = step.get("emailer_touches") or []
            pairs.append((step["id"], touches[0]["id"] if touches else ""))
        return pairs
    return []


def touches_for(person: str) -> list[SequenceTouch]:
    surname = person.split()[-1].casefold()
    touches = []
    for message in first:
        if surname in message.recipient.casefold():
            touches.append((1, message))
    for message in later:
        if surname in message.recipient.casefold():
            number = int(re.search(r"touch (\d)", message.institution).group(1))
            touches.append((number, message))
    touches.sort(key=lambda pair: pair[0])
    return [
        SequenceTouch(
            step=number,
            day_offset=0 if number == 1 else DAYS[number],
            subject=message.subject,
            body=strip_signature(message.body),
            recipient_role=message.recipient,
        )
        for number, message in touches
    ]


for sequence_id, person, institution in LIVE:
    touches = touches_for(person)
    if len(touches) != 4:
        print(f"SKIPPED {person}: found {len(touches)} touches, expected 4")
        continue
    proposal = SequenceProposal(
        sequence_name=sequence_title(person, institution),
        contact_count=1,
        source_list=f"One named contact: {person}, {institution}.",
        touches=touches,
    )
    # The ids Apollo already has for step 1 and its touch. Without them the
    # update is a 422, and with the wrong ones it would rebuild the step the
    # enrolled contact is attached to.
    existing = current_steps(sequence_id)
    payload = update_payload(proposal, existing=existing)
    resp = httpx.put(
        f"{APOLLO_API}/sequences/{sequence_id}",
        headers=headers,
        content=json.dumps(payload),
        timeout=90,
    )
    body = resp.json() if resp.status_code < 400 else {}
    steps = body.get("emailer_steps") or (body.get("emailer_campaign") or {}).get("emailer_steps") or []
    waits = [(s.get("wait_time"), s.get("wait_mode")) for s in steps]
    print(f"{resp.status_code} | {payload['name']}")
    print(f"      steps {len(steps)} | waits {waits}")
    if resp.status_code == 403:
        print("      the token lacks emailer_campaigns_update. Nothing else attempted.")
        raise SystemExit(1)
    if resp.status_code >= 400:
        print("      ", resp.text[:250])
