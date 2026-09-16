"""Write batch 1 to Apollo as manual-email drafts, through the tool's own code.

Copy is taken from the extracted artifact rather than retyped, so what lands in
Apollo is what the Reviewer read. Five messages, one sequence, paused.
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

blocks = re.split(r"^### ", body, flags=re.MULTILINE)[1:]
touches = []
step = 0
for block in blocks:
    lines = block.splitlines()
    institution = lines[0].strip()
    to = next((l for l in lines if l.startswith("**To:")), None)
    subject_line = next((l for l in lines if l.startswith("**Subject:**")), None)
    if not to or not subject_line:
        continue
    step += 1
    recipient = to.replace("**To:", "").rstrip("*").strip()
    subject = subject_line.replace("**Subject:**", "").strip()
    start = lines.index(subject_line) + 1
    text = "\n".join(lines[start:]).strip()
    touches.append(
        SequenceTouch(
            step=step,
            day_offset=0,  # five separate first touches, no cadence between them
            subject=f"{institution}: {subject}",
            body=text,
            recipient_role=recipient,
        )
    )

print(f"{len(touches)} messages read from the artifact")
for t in touches:
    print(f"  {t.step}. {t.recipient_role[:60]} | {t.subject[:60]}")

proposal = SequenceProposal(
    sequence_name="Batch 1, M3 student success, 4 Sep 2026",
    contact_count=0,
    source_list=(
        "Hand built from Kib's saved LinkedIn prospects. Employers verified against "
        "public sources on 4 September. Three institutions, none with an open deal."
    ),
    touches=touches,
)

payload = sequence_payload(proposal)
print("\nsequence name:", payload["name"])

resp = httpx.post(
    f"{APOLLO_API}/sequences",
    headers=bearer_headers(current_token()),
    content=json.dumps(payload),
    timeout=90,
)
print("status", resp.status_code)
if resp.status_code >= 400:
    print(resp.text[:500])
    raise SystemExit(1)

data = resp.json()
campaign = data.get("emailer_campaign") or {}
steps = data.get("emailer_steps") or campaign.get("emailer_steps") or []
print("id:", campaign.get("id"))
print("owner user_id:", campaign.get("user_id"))
print("active:", campaign.get("active"))
print("steps:", len(steps), [s.get("type") for s in steps])
