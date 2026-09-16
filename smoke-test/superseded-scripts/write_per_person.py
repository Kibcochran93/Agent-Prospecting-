"""Rewrite batch 1 as one sequence per recipient.

Why per person. A sequence is one cadence for whoever is enrolled in it, so five
different people's messages as five steps of one sequence means every enrolled
contact receives all five. It also cannot be addressed: with nobody enrolled,
Apollo previews the template against an unrelated contact, which is why every
draft in the first attempt showed the same stranger in the To line.

One recipient, one sequence, one step. Enrol that person and the draft is theirs.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, "src")

from seats_prospecting.apollo_oauth import bearer_headers, current_token  # noqa: E402
from seats_prospecting.schemas import SequenceProposal, SequenceTouch  # noqa: E402
from seats_prospecting.tools.apollo_write import APOLLO_API, sequence_payload  # noqa: E402

art = Path("smoke-test/batch-1/reviewer-in-2.txt").read_text(encoding="utf-8")
body = art.split("--- ARTIFACT BEGINS ---", 1)[1].split("--- ARTIFACT ENDS ---")[0]

headers = bearer_headers(current_token())
created = []

for block in re.split(r"^### ", body, flags=re.MULTILINE)[1:]:
    lines = block.splitlines()
    institution = lines[0].strip()
    to = next((l for l in lines if l.startswith("**To:")), None)
    subj = next((l for l in lines if l.startswith("**Subject:**")), None)
    if not to or not subj:
        continue
    recipient = to.replace("**To:", "").rstrip("*").strip()
    person = recipient.split(",")[0].strip()
    subject = subj.replace("**Subject:**", "").strip()
    text = "\n".join(lines[lines.index(subj) + 1 :]).strip()

    proposal = SequenceProposal(
        sequence_name=f"{institution} | {person}",
        contact_count=1,
        source_list=f"One named contact: {recipient}. Verified against a public source 4 Sep 2026.",
        touches=[
            SequenceTouch(
                step=1,
                day_offset=0,
                subject=subject,
                body=text,
                recipient_role=recipient,
            )
        ],
    )
    payload = sequence_payload(proposal)
    resp = httpx.post(
        f"{APOLLO_API}/sequences", headers=headers, content=json.dumps(payload), timeout=90
    )
    campaign = (resp.json().get("emailer_campaign") or {}) if resp.status_code < 400 else {}
    steps = (resp.json().get("emailer_steps") or []) if resp.status_code < 400 else []
    created.append((payload["name"], resp.status_code, campaign.get("id"), len(steps)))
    print(f"{resp.status_code} | {len(steps)} step | {campaign.get('id')} | {payload['name']}")

print("\ncreated", sum(1 for c in created if c[1] == 200), "of", len(created))
