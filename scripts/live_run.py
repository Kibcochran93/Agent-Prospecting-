"""Run one agent live, unattended, and write a log that says what happened.

Why this is a file and not an inline `python -c`. Every live run so far was
launched from a command line that no longer exists, so the logs in
`smoke-test/` cannot be reproduced or compared. This script is the launcher,
kept next to the code that changes underneath it.

What it prints, in order:

    TRAIL       every agent start, handoff and tool call, in sequence
    AUDIT       the run context's own audit lines
    LAST AGENT  who produced the output, which is not always who was invoked
    OUTPUT      the final output verbatim

Approvals are **rejected** by default. An unattended run must not be able to
approve an Apollo write on Kib's behalf, and a rejection is the safe outcome:
it closes the write path for that run and leaves Apollo untouched. Pass
`--approve-interactively` only when Kib is at the keyboard.

Usage, from the project root on Windows:

    .venv\\Scripts\\python.exe scripts\\live_run.py briefing -f in.txt -o out.log
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents import SQLiteSession  # noqa: E402

from seats_prospecting.agents_def import build_agents  # noqa: E402
from seats_prospecting.context import DispatchContext  # noqa: E402
from seats_prospecting.runner import render_approval, run  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _tool_name(raw) -> str:
    """Hosted MCP calls do not carry .name, so a raw attribute read prints '?'.

    Their raw item is a dict-shaped payload whose tool name sits under `name`,
    `tool_name`, or `server_label`. Worth resolving: the trail is how a later
    session sees which connector a run actually reached.
    """
    for attr in ("name", "tool_name", "server_label"):
        value = getattr(raw, attr, None)
        if value:
            return str(value)
    if isinstance(raw, dict):
        for key in ("name", "tool_name", "server_label", "type"):
            if raw.get(key):
                return str(raw[key])
    return type(raw).__name__ if raw is not None else "?"


def _trail(result) -> list[str]:
    steps: list[str] = []
    for item in getattr(result, "new_items", []):
        kind = type(item).__name__
        agent = getattr(getattr(item, "agent", None), "name", None)
        if kind == "HandoffOutputItem":
            steps.append(f"HANDOFF -> {agent}")
        elif kind == "ToolCallItem":
            steps.append(f"TOOL {_tool_name(getattr(item, 'raw_item', None))}")
        elif kind == "MessageOutputItem" and agent:
            steps.append(f"MESSAGE {agent}")
    return steps


def _reject(interruption, agent_name):
    print(render_approval(interruption, agent_name))
    print("\nUnattended run: rejecting. Nothing was written.")
    return False


def _ask(interruption, agent_name):
    print(render_approval(interruption, agent_name))
    return input("Approve this call? [y/N]: ").strip().lower() in {"y", "yes"}


async def _main(args) -> int:
    text = Path(args.file).read_text(encoding="utf-8") if args.file else args.input
    if not text:
        print("No input. Pass -f or -i.", file=sys.stderr)
        return 2

    # Phase 3: the counting runs before the judging. The Reviewer is the most
    # expensive call in the chain, and a batch with two identical subject lines
    # is settled by a string comparison.
    if args.agent == "reviewer" and not args.skip_gate:
        from seats_prospecting.reviewer_gate import BatchFailsCounting, gate

        try:
            gate(text)
        except BatchFailsCounting as exc:
            print(str(exc), file=sys.stderr)
            return 4

    agents = build_agents()
    ctx = DispatchContext()

    # Phase 2: the verdict is stamped onto the run before it starts, by Kib,
    # from a file the Account Context run wrote. No agent can set it and no
    # agent can fake it, which is why it lives here rather than on the work
    # order the Director types.
    if args.verdict:
        import json

        from seats_prospecting.schemas import ContextVerdict

        raw = json.loads(Path(args.verdict).read_text(encoding="utf-8"))
        # Accept the outbox envelope or a bare verdict, because both are things
        # a person reasonably has to hand.
        body = raw.get("record", raw)
        stamped = ContextVerdict.model_validate(body)
        ctx.stamp_context(stamped, args.override)

        # The Director has to SEE the verdict, not merely run with one attached.
        # Found on the first live phase-2 run: the dispatch gate held, the audit
        # showed the verdict, and the Director still reported that no verdict was
        # provided. It was right. Stamping put the verdict where the handoff
        # filter reads it, and the handoff filter builds the WORKER's input.
        #
        # This is prepended rather than given as a tool, because a tool is
        # something the Director chooses to call and this is a fact of the run.
        # It is application state written by Kib before the run started, which
        # is why it can be trusted as an input and could not be trusted as a
        # required field on the payload the Director types.
        preamble = [
            "ACCOUNT CONTEXT VERDICT (system fact, established before this run "
            "by an agent that reads the CRM and does nothing else; you cannot "
            "edit it and you did not produce it)",
            stamped.as_note(),
        ]
        if args.override:
            preamble.append(
                f"Kib has overridden this verdict for this run. His reason: "
                f"{args.override}\nThe dispatch will proceed. Say in your plan "
                "that it proceeded over the verdict and why."
            )
        text = "\n\n".join(preamble) + "\n\n---\n\n" + text
    elif args.override:
        print(
            "--override without --verdict overrides nothing: the dispatch "
            "refuses for want of a verdict, not because of one.",
            file=sys.stderr,
        )
        return 2
    session = SQLiteSession(args.session, "./.seats_sessions.db") if args.session else None

    # ADR 0003, 11 September 2026: hashed here, on the exact text handed to the
    # agent, not re-derived later from the file on disk. If --verdict ever grew
    # a legitimate use on a reviewer run, the hash would still cover what the
    # Reviewer actually read rather than what the input file happened to say.
    artifact_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()

    result = await run(
        agents[args.agent],
        text,
        context=ctx,
        session=session,
        decide=_ask if args.approve_interactively else _reject,
    )

    # Phase 1: the context verdict is recorded by the caller, not by the agent
    # that formed it. Account Context holds no ledger write on purpose, so the
    # write happens here, after the run, from the typed output. Advisory: the
    # file records what was searched and found and nothing is blocked on it.
    recorded: Path | None = None
    if args.agent in ("context", "reviewer") and not args.no_record:
        from seats_prospecting.tools.notion_ledger import (
            write_context_verdict,
            write_review_verdict,
        )

        try:
            if args.agent == "context":
                recorded = write_context_verdict(result.final_output)
            else:
                # account_key is required and validated in main(), before the
                # run started, so a reviewer run never spends its expensive
                # call only to fail recording afterward for want of one.
                recorded = write_review_verdict(
                    result.final_output,
                    account_key=args.account,
                    artifact_sha256=artifact_sha256,
                )
        except Exception as exc:  # noqa: BLE001 - a failed record must not lose the run
            print(f"could not record the verdict: {exc}", file=sys.stderr)

    out = [
        f"TRAIL: {['START ' + agents[args.agent].name] + _trail(result)}",
        f"AUDIT: {ctx.audit}",
        f"REVEALS: {ctx.apollo_reveals}",
        f"LAST AGENT: {getattr(result.last_agent, 'name', '?')}",
        f"RECORDED: {recorded or 'not recorded'}",
        "=== OUTPUT ===",
        result.final_output,
    ]
    body = "\n".join(str(line) for line in out)
    print(body)
    if args.out:
        Path(args.out).write_text(body, encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="live_run")
    parser.add_argument(
        "agent", choices=("context", "director", "briefing", "campaign", "reviewer")
    )
    parser.add_argument("-f", "--file", help="Input file, read as UTF-8.")
    parser.add_argument("-i", "--input")
    parser.add_argument("-o", "--out", help="Write the log here as UTF-8.")
    parser.add_argument("-s", "--session")
    parser.add_argument(
        "--verdict",
        metavar="PATH",
        help=(
            "Account context verdict to stamp on this run, as written by a "
            "context run into ledger-outbox/. Required before a director run can "
            "dispatch."
        ),
    )
    parser.add_argument(
        "--override",
        metavar="REASON",
        help=(
            "Proceed despite a known_active verdict, and enrol a person who is "
            "already in a live sequence. The reason is recorded on the run and "
            "in the ledger. Requires --verdict."
        ),
    )
    parser.add_argument(
        "--account",
        metavar="KEY",
        help=(
            "Reviewer runs only, required unless --no-record: the account_key "
            "this batch is about, in the same form used everywhere else "
            "(decisions/, the board, the job queue). ADR 0003: a review_verdict "
            "with no account key cannot be matched to a SEQUENCE job later, so "
            "this is how a 'ships' verdict becomes something a queued job can "
            "trust rather than guess at from institution names in the text."
        ),
    )
    parser.add_argument(
        "--no-record",
        action="store_true",
        help=(
            "Context and reviewer runs: skip writing the verdict to the ledger "
            "outbox."
        ),
    )
    parser.add_argument(
        "--skip-gate",
        action="store_true",
        help=(
            "Reviewer runs only: start the run even though the batch fails the "
            "mechanical checks. Only useful for testing the reviewer itself; the "
            "findings it would spend the run rediscovering are already printed."
        ),
    )
    parser.add_argument(
        "--approve-interactively",
        action="store_true",
        help="Prompt on approvals instead of rejecting. Only with Kib watching.",
    )
    args = parser.parse_args()
    if args.agent in ("reviewer", "context") and args.session:
        # Both are single-shot judgements on one object. A session would carry
        # the last account's record into the next account's verdict, which is
        # the failure mode this agent exists to catch.
        print(f"The {args.agent} takes no session.", file=sys.stderr)
        return 2
    if args.agent == "reviewer" and not args.account and not args.no_record:
        print(
            "Reviewer runs need --account so the verdict can be matched to an "
            "account later (ADR 0003), or --no-record if this run is not meant "
            "to produce a verdict anyone will act on.",
            file=sys.stderr,
        )
        return 2
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
