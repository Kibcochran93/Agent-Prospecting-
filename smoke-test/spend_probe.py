"""Cheapest possible check that the OpenAI org will accept a request.

Written 8 September 2026 after the reviewer run died on
organization_spend_limit_exceeded. Sends one tiny request and prints the
status. Costs a fraction of a cent. Safe to delete.
"""
import json
import pathlib
import urllib.error
import urllib.request

env = pathlib.Path(__file__).resolve().parent.parent / ".env"
key = ""
model = "gpt-5.6-sol"
for line in env.read_text(encoding="utf-8").splitlines():
    if line.startswith("OPENAI_API_KEY="):
        key = line.split("=", 1)[1].strip().strip('"')
    if line.startswith("SEATS_MODEL_REVIEW="):
        model = line.split("=", 1)[1].strip().strip('"')

body = json.dumps({"model": model, "input": "Reply with the single word ok.", "max_output_tokens": 16}).encode()
req = urllib.request.Request(
    "https://api.openai.com/v1/responses",
    data=body,
    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        print("HTTP", r.status, "model", model)
        print("ACCEPTED. The organisation took the request.")
except urllib.error.HTTPError as e:
    detail = e.read().decode("utf-8", "replace")[:600]
    print("HTTP", e.code, "model", model)
    print("REFUSED:", detail)
except Exception as e:
    print("ERROR:", type(e).__name__, e)
