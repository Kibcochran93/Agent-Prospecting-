"""Run loop with the approval interruption.

The approval is the only human gate left in the run. Everything else in this
system is either structural (a missing tool, a narrow schema, an isolated
context) or conventional (a workflow checkpoint in conversation). This is the
one control that halts execution outside the model's reach, so the printing
here is deliberate: the full arguments, verbatim, before the prompt.
"""

from __future__ import annotations

import json
import textwrap
from typing import Any

from agents import Agent, RunResult, Runner, RunState, SQLiteSession

from . import settings
from .context import DispatchContext

RULE = "=" * 78


def _pretty_arguments(raw: str | None) -> str:
    if not raw:
        return "(no arguments returned by the model)"
    try:
        parsed: Any = json.loads(raw)
    except (TypeError, ValueError):
        return raw
    return json.dumps(parsed, indent=2, ensure_ascii=False)


def render_approval(interruption: Any, agent_name: str | None = None) -> str:
    """Everything Kib reads before deciding. Nothing elided, nothing summarized."""
    name = getattr(interruption, "name", None) or getattr(
        interruption, "tool_name", "unknown_tool"
    )
    who = agent_name or getattr(getattr(interruption, "agent", None), "name", "unknown agent")
    return "\n".join(
        [
            "",
            RULE,
            "APPROVAL REQUIRED — the run is halted and nothing has executed.",
            RULE,
            f"agent: {who}",
            f"tool:  {name}",
            "",
            "arguments as they will be sent:",
            textwrap.indent(_pretty_arguments(getattr(interruption, "arguments", None)), "  "),
            "",
            "Read the copy above, not your memory of what was proposed earlier.",
            "Rejecting ends the write path for this run. It is not a request for a",
            "revised proposal.",
            RULE,
        ]
    )


def _console_decision(interruption: Any, agent_name: str | None) -> bool:
    print(render_approval(interruption, agent_name))
    answer = input("Approve this call? [y/N]: ").strip().lower()
    return answer in {"y", "yes"}


async def run(
    agent: Agent[DispatchContext],
    user_input: str | list[dict[str, Any]],
    *,
    context: DispatchContext | None = None,
    session: SQLiteSession | None = None,
    decide=_console_decision,
    max_turns: int = settings.MAX_TURNS,
) -> RunResult:
    """Run to completion, pausing on every approval.

    ``decide`` takes (interruption, agent_name) and returns True to approve.
    Swapped out in tests, and swappable for a Teams or web approval surface
    without touching anything else.
    """
    ctx = context or DispatchContext()
    result = await Runner.run(
        agent, user_input, context=ctx, session=session, max_turns=max_turns
    )

    while result.interruptions:
        state: RunState = result.to_state()
        for interruption in result.interruptions:
            agent_name = getattr(
                getattr(interruption, "agent", None), "name", None
            )
            if decide(interruption, agent_name):
                # Never sticky. Every sequence write is its own read.
                state.approve(interruption, always_approve=False)
                ctx.audit.append(f"approved: {getattr(interruption, 'name', '?')}")
            else:
                state.reject(
                    interruption,
                    rejection_message=(
                        "Kib rejected this call. The write path for this run is closed. "
                        "State what was rejected and stop. Do not propose a revised "
                        "version of the same call."
                    ),
                )
                ctx.audit.append(f"rejected: {getattr(interruption, 'name', '?')}")

        result = await Runner.run(
            agent, state, context=ctx, session=session, max_turns=max_turns
        )

    return result
