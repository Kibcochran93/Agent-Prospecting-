"""Can this token update a sequence, or only create one?

Tested against Wayne Young's sequence, which is the only one of the five with
nobody enrolled. The call sends the name it already has, so a success changes
nothing.
"""

from __future__ import annotations

import json
import sys

import httpx

sys.path.insert(0, "src")

from seats_prospecting.apollo_oauth import bearer_headers, current_token  # noqa: E402

SEQUENCE = "6a9adf457c1bc30010142b15"  # Wayne Young Jr. (v2), unenrolled
NAME = "AI Agent Prospecting Ops | Creighton University | W. Wayne Young Jr. (v2)"

resp = httpx.put(
    f"https://api.apollo.io/api/v1/sequences/{SEQUENCE}",
    headers=bearer_headers(current_token()),
    content=json.dumps({"name": NAME}),
    timeout=45,
)
print("PUT /sequences/{id} ->", resp.status_code)
print(resp.text[:400])

spec = json.load(open("smoke-test/apollo-openapi.json", encoding="utf-8"))
desc = spec["paths"]["/sequences/{id}"]["put"].get("description", "")
for line in desc.splitlines():
    if "scope" in line.lower() or "API key access" in line:
        print("SPEC:", line.strip())
