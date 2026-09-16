"""One live write, through the real code path, to prove drafts land as Kib's.

What is being tested, in order of what has already failed:

1. Identity. An api key acts as the workspace's longest-standing admin. The
   OAuth token acts as Kib. The created sequence's owner is the assertion.
2. The endpoint. POST /emailer_campaigns returned 200 and created a sequence
   with zero steps. The documented create is POST /sequences.
3. The step shape. Copy lives in a touch's emailer_template as subject and
   body_html, not in an emailer_templates array on the step.

Uses seats_prospecting's own sequence_payload and its own auth, so a pass here
is a pass for the tool rather than for this script. Writes one paused sequence
of manual-email drafts, named so it is obvious it can be deleted.
"""

from __future__ import annotations

import json
import sys

import httpx

sys.path.insert(0, "src")

from seats_prospecting.apollo_oauth import bearer_headers, current_token  # noqa: E402
from seats_prospecting.schemas import SequenceProposal, SequenceTouch  # noqa: E402
from seats_prospecting.tools.apollo_write import APOLLO_API, sequence_payload  # noqa: E402

proposal = SequenceProposal(
    sequence_name="TEST - write path check 4 Sep - safe to delete",
    contact_count=0,
    source_list="No contacts. This sequence exists to prove the write path.",
    touches=[
        SequenceTouch(
            step=1,
            day_offset=0,
            subject="Write path check, first touch",
            body=(
                "This is a test draft written by the prospecting build.\n\n"
                "If you are reading it in Apollo as a task, the endpoint and the "
                "step shape are both right."
            ),
            recipient_role="operational owner",
        ),
        SequenceTouch(
            step=2,
            day_offset=4,
            subject="Write path check, second touch",
            body="Second step, four days later, to prove the wait maths.",
            recipient_role="economic buyer",
        ),
    ],
)

token = current_token()
payload = sequence_payload(proposal)
print("posting to", f"{APOLLO_API}/sequences")
print(json.dumps(payload, indent=1)[:700])

resp = httpx.post(
    f"{APOLLO_API}/sequences",
    headers=bearer_headers(token),
    content=json.dumps(payload),
    timeout=60,
)
print("\nstatus", resp.status_code)
if resp.status_code >= 400:
    print(resp.text[:600])
    raise SystemExit(1)

body = resp.json()
campaign = body.get("emailer_campaign") or {}
steps = body.get("emailer_steps") or campaign.get("emailer_steps") or []
print("sequence id:", campaign.get("id"))
print("name:", campaign.get("name"))
print("active:", campaign.get("active"))
print("owner user_id:", campaign.get("user_id"))
print("steps created:", len(steps))
print("step types:", [s.get("type") for s in steps])
print("waits:", [(s.get("wait_time"), s.get("wait_mode")) for s in steps])
templates = body.get("emailer_templates") or []
print("templates:", [(t.get("subject"), (t.get("body_html") or "")[:40]) for t in templates])
