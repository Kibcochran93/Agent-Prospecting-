"""seats-bridge: the one door the published Artifact gets into this machine.

Built 15 September 2026, reconciling the "Prospecting Control Panel" Artifact
with the local pipeline after it turned out the two had never been connected.
The Artifact ran entirely on its own: its own storage for decisions, direct
Apollo writes for "push to Apollo," no daily cap, no Reviewer, no append-only
decisions/ audit trail. This server is the fix, and its whole design is that
it does not re-implement any of that -- it is a thin, authenticated proxy in
front of the same board_serve.py endpoints a person clicking the board by hand
already uses. Every rule that already lives in job_queue.py and decisions.py
(the daily ceiling, one-job-per-account, PAUSED, append-only, Approved/Declined
is Kib's alone) applies here unchanged, because the request ends up in the same
place either way.

What this deliberately does NOT expose, and never should:
  - No tool that calls Apollo or HubSpot directly. Every write goes through
    board_serve.py's /queue or /decide, which go through job_queue.py or
    decisions.py. If a future change wants a direct Apollo call from the
    Artifact, that is a new decision, not an addition to this file.
  - No tool that reads or writes SEATS_BRIDGE_TOKEN, or any other secret.
  - No stage other than COPY and SEQUENCE can be queued -- job_queue.py
    enforces that already (next_step.AUTOMATABLE), this just doesn't try to
    widen it.

Auth: a single bearer token, generated once into .env as SEATS_BRIDGE_TOKEN.
Every request to every route needs it, checked in Starlette middleware before
any MCP handling runs. This is a shared secret, not an account -- there is
exactly one legitimate caller (Kib's published Artifact) and one legitimate
operator (Kib), so a token is proportionate. It is not a substitute for
keeping the tunnel URL itself private.

board_serve.py must already be running on 127.0.0.1:8765 (scripts/board_serve.py).
This process does not start it and does not check for it at import time --
each tool call fails individually, with the actual httpx error, if it's down.

Usage:

    .venv\\Scripts\\python.exe scripts\\bridge_server.py
    .venv\\Scripts\\python.exe scripts\\bridge_server.py --port 8766
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mcp.server.fastmcp import FastMCP  # noqa: E402

BOARD_URL = os.environ.get("SEATS_BOARD_URL", "http://127.0.0.1:8765")

ENV_PATH = ROOT / ".env"


def _load_or_create_token() -> str:
    """The token lives in .env, generated once. Never printed after creation
    except to the person running this for the first time -- it goes in the
    Artifact's connector config, not in any log this process writes routinely.
    """
    token = os.environ.get("SEATS_BRIDGE_TOKEN", "").strip()
    if token:
        return token

    token = secrets.token_urlsafe(32)
    line = f"SEATS_BRIDGE_TOKEN={token}\n"
    if ENV_PATH.exists():
        existing = ENV_PATH.read_text(encoding="utf-8")
        if "SEATS_BRIDGE_TOKEN" not in existing:
            with ENV_PATH.open("a", encoding="utf-8") as f:
                if not existing.endswith("\n"):
                    f.write("\n")
                f.write(line)
    else:
        ENV_PATH.write_text(line, encoding="utf-8")

    print("=" * 70)
    print("SEATS_BRIDGE_TOKEN generated and written to .env.")
    print("This is the ONLY time it prints. Copy it into the Artifact's")
    print("connector config now:")
    print()
    print(f"    {token}")
    print()
    print("=" * 70)
    return token


TOKEN = _load_or_create_token()


class BearerAuth(BaseHTTPMiddleware):
    """The one door. Checked before any MCP session handling, on every route."""

    async def dispatch(self, request: Request, call_next):
        header = request.headers.get("authorization", "")
        expected = f"Bearer {TOKEN}"
        if not secrets.compare_digest(header, expected):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


mcp = FastMCP(
    name="seats-prospecting-bridge",
    instructions=(
        "Bridge into Kib's local seats-prospecting pipeline. Every write here "
        "goes through the same job queue and decisions store the local board "
        "uses -- there is no direct Apollo or HubSpot write in this server. "
        "record_decision only accepts Approved or Declined. queue_copy_job and "
        "queue_sequence_job enqueue a job; the daily cap and one-per-account "
        "rule can refuse the request, and the refusal reason is returned "
        "as-is, not summarised."
    ),
)


async def _get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{BOARD_URL}{path}")
        resp.raise_for_status()
        return resp.json()


async def _post(path: str, payload: dict) -> tuple[int, dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{BOARD_URL}{path}", json=payload)
        try:
            body = resp.json()
        except ValueError:
            body = {"error": resp.text}
        return resp.status_code, body


@mcp.tool()
async def board_state() -> dict:
    """Current state of the local pipeline: accounts waiting on a decision
    (Approved/Declined), accounts already approved and waiting on a COPY or
    SEQUENCE job, recent account-context notes, and today's job-queue budget
    (used/max/left -- the daily cap this bridge cannot raise or bypass).

    Read straight from board_serve.py's live state, same data the board itself
    shows. No caching: call this again after any write below to see the effect.
    """
    try:
        return await _get("/api/state")
    except httpx.HTTPError as exc:
        return {"error": f"could not reach the local board at {BOARD_URL}: {exc}"}


@mcp.tool()
async def record_decision(
    account_key: str, institution: str, decision: str, note: str = ""
) -> dict:
    """Record Kib's Approved or Declined for one account. Writes to the local,
    append-only decisions/ store -- the same file a click on the board itself
    would write, with the same source label. Nothing is sent, enrolled, or
    created in Apollo by this call; it only clears (or does not clear) the
    account to move to the next stage.

    account_key and institution both come from a row in board_state()'s
    `pending` list -- do not invent either.
    decision: exactly "Approved" or "Declined". Anything else is refused by
    the decisions store itself, not by this wrapper.
    """
    if decision not in ("Approved", "Declined"):
        return {
            "error": (
                f"{decision!r} is not Approved or Declined. This bridge only "
                "ever forwards one of those two literal strings."
            )
        }
    status, body = await _post(
        "/decide",
        {
            "account": institution,
            "account_key": account_key,
            "decision": decision,
            "note": note,
        },
    )
    if status != 200:
        return {"error": body.get("error", f"HTTP {status}")}
    return {"recorded": True, "file": body.get("file", "")}


@mcp.tool()
async def queue_copy_job(account_key: str, institution: str, note: str = "") -> dict:
    """Queue a Campaign Builder (COPY stage) run for an already-approved
    account that has no copy yet -- check board_state()'s `queueable` list
    first; an account not on it either isn't approved yet or already has a
    job in flight. Subject to job_queue.py's real limits: five jobs a day
    across all accounts, one unrun job per account, refused outright if the
    queue is paused. A refusal is returned as the actual reason, not retried
    or smoothed over.

    This does not write to Apollo. It only starts the same agent run that
    produces a copy file for the Reviewer to grade.
    """
    status, body = await _post(
        "/queue",
        {
            "account": institution,
            "account_key": account_key,
            "stage": "COPY",
            "note": note or "queued from Artifact bridge",
        },
    )
    if status != 200:
        return {"error": body.get("error", f"HTTP {status}")}
    return {"queued": True, "job": body.get("file", "")}


@mcp.tool()
async def queue_sequence_job(account_key: str, institution: str, note: str = "") -> dict:
    """Queue the SEQUENCE stage for an account whose copy has already shipped
    a Reviewer verdict of 'ships'. Same caps as queue_copy_job. This is the
    step that creates a paused Apollo sequence -- still paused, still
    requiring Kib to open Apollo and send, but it is the closest this bridge
    comes to an Apollo write, and it only runs through the queue, never
    directly.
    """
    status, body = await _post(
        "/queue",
        {
            "account": institution,
            "account_key": account_key,
            "stage": "SEQUENCE",
            "note": note or "queued from Artifact bridge",
        },
    )
    if status != 200:
        return {"error": body.get("error", f"HTTP {status}")}
    return {"queued": True, "job": body.get("file", "")}


def build_app() -> Starlette:
    app = mcp.streamable_http_app()
    app.user_middleware.insert(0, Middleware(BearerAuth))
    app.middleware_stack = app.build_middleware_stack()
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seats-bridge")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)

    app = build_app()
    print(f"seats-bridge listening on http://{args.host}:{args.port}/mcp")
    print(f"proxying to board at {BOARD_URL}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
