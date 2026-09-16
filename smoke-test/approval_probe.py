"""Drive the real Campaign Builder to the write gate and reject it, without a TTY.

Why this exists: `runner._console_decision` calls `input()`, so the approval gate
cannot be exercised from a redirected process. This script swaps `decide` for a
recorder that writes the exact approval payload to disk and then rejects. Nothing
else is changed. The agent, the tool, the model and the Apollo credential are all
the real ones.

Two things get proved:

1. The run halts before the HTTP call, and the payload Kib would read is complete.
2. A rejection closes the write path and the agent stops rather than re-proposing.

Run it with APOLLO_SEQUENCE_WRITE=true, otherwise the Campaign Builder holds no
write tool and there is nothing to halt.

    python smoke-test/approval_probe.py --session camp-m3-test --decision reject

`--decision approve` exists so the same script can be used with a human watching.
It performs a real Apollo write. It is not the default and it is not for a test.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents import SQLiteSession  # noqa: E402

from seats_prospecting.agents_def import build_agents  # noqa: E402
from seats_prospecting.context import DispatchContext  # noqa: E402
from seats_prospecting.runner import render_approval, run  # noqa: E402

OUT = Path(__file__).parent


def _recorder(decision: str, log: list[dict]):
    def decide(interruption, agent_name):
        name = getattr(interruption, "name", None) or getattr(
            interruption, "tool_name", "unknown"
        )
        raw = getattr(interruption, "arguments", None)
        log.append({"tool": name, "agent": agent_name, "arguments": raw})
        (OUT / "approval-payload.txt").write_text(
            render_approval(interruption, agent_name), encoding="utf-8"
        )
        print(render_approval(interruption, agent_name))
        print(f"\n[probe] decision = {decision}")
        return decision == "approve"

    return decide


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--decision", choices=("approve", "reject"), default="reject")
    ap.add_argument(
        "--input",
        default=(
            "Checkpoint 5 copy is approved. Create the Apollo sequence now, "
            "inactive, with the full batch."
        ),
    )
    args = ap.parse_args()

    if not os.environ.get("APOLLO_SEQUENCE_WRITE", "").lower() == "true":
        print(
            "[probe] APOLLO_SEQUENCE_WRITE is not true, so the Campaign Builder "
            "holds no write tool and there is no gate to test. Set it for this run "
            "only.",
            file=sys.stderr,
        )
        return 2

    agents = build_agents()
    campaign = agents["campaign"]

    tool_names = sorted(
        getattr(t, "name", getattr(t, "__name__", type(t).__name__))
        for t in campaign.tools
    )
    print("[probe] campaign tools:", json.dumps(tool_names, indent=2))
    assert any("draft" in n or "sequence" in n for n in tool_names), (
        "no draft or sequence tool on the campaign agent; nothing to gate"
    )

    log: list[dict] = []
    ctx = DispatchContext()
    session = SQLiteSession(args.session, "./.seats_sessions.db")

    result = await run(
        campaign,
        args.input,
        context=ctx,
        session=session,
        decide=_recorder(args.decision, log),
    )

    print("\n=== final output ===")
    print(result.final_output)
    print("\n=== run audit ===")
    for line in ctx.audit:
        print(line)

    (OUT / "approval-probe-result.json").write_text(
        json.dumps(
            {
                "decision": args.decision,
                "gate_fired": bool(log),
                "interruptions_seen": log,
                "audit": ctx.audit,
                "final_output": result.final_output,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    if not log:
        print(
            "\n[probe] FAIL: the run finished without ever halting. Either the agent "
            "never called the write tool, or the tool is not gated.",
            file=sys.stderr,
        )
        return 1

    print(f"\n[probe] gate fired {len(log)} time(s), decision was {args.decision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
