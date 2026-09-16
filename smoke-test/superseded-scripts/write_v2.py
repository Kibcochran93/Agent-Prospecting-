"""Rewrite batch 1 again, this time parsing the artifact with the package.

The first per-recipient write split the artifact with an ad hoc regular
expression in this script. It cut each message at the next institution heading,
so the last message ran on into the differentiation check, and the sign-off
stripper then could not see a sign-off at the end. The draft that reached Apollo
carried both.

Now it uses seats_prospecting.differentiation.parse_messages, the same parser
the mechanical check uses, which ends a message at any heading. One parser, one
behaviour, one test holding it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, "src")

from seats_prospecting.apollo_oauth import bearer_headers, current_token  # noqa: E402
from seats_prospecting.differentiation import parse_messages, strip_signature  # noqa: E402
from seats_prospecting.schemas import SequenceProposal, SequenceTouch  # noqa: E402
from seats_prospecting.tools.apollo_write import APOLLO_API, sequence_payload  # noqa: E402

art = Path("smoke-test/batch-1/reviewer-in-2.txt").read_text(encoding="utf-8")
messages = parse_messages(art)
print(f"{len(messages)} messages parsed by the package parser\n")

headers = bearer_headers(current_token())
for message in messages:
    clean = strip_signature(message.body)
    assert "Kib Cochran" not in clean, f"sign-off survived in {message.recipient}"
    assert "differentiation" not in clean.lower(), f"check leaked into {message.recipient}"
    person = message.recipient.split(",")[0].strip()

    proposal = SequenceProposal(
        sequence_name=f"{message.institution} | {person} (v2)",
        contact_count=1,
        source_list=f"One named contact: {message.recipient}. Verified 4 Sep 2026.",
        touches=[
            SequenceTouch(
                step=1,
                day_offset=0,
                subject=message.subject,
                body=clean,
                recipient_role=message.recipient,
            )
        ],
    )
    payload = sequence_payload(proposal)
    html = payload["emailer_steps"][0]["emailer_touches"][0]["emailer_template"]["body_html"]
    resp = httpx.post(
        f"{APOLLO_API}/sequences", headers=headers, content=json.dumps(payload), timeout=90
    )
    campaign = (resp.json().get("emailer_campaign") or {}) if resp.status_code < 400 else {}
    print(f"{resp.status_code} | {campaign.get('id')} | {payload['name']}")
    print(f"      body ends: ...{html[-90:]}\n")
