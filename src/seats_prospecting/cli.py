"""Entry points. One run per invocation, which mirrors how the system works:
the Director dispatches once and its turn is over, and the Reviewer is a
separate run Kib starts.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from agents import SQLiteSession

from .agents_def import build_agents
from .context import DispatchContext
from .review_input import ReviewInputError, build_from_path
from .runner import run

AGENT_CHOICES = ("director", "briefing", "campaign", "reviewer")

# Windows consoles and redirected pipes default to cp1252, which mangles the
# curly quotes and dashes the models produce. Force UTF-8 on all three streams.
#
# stdin matters most and was missed the first time. The Reviewer's only input is
# a pasted artifact on stdin, and every artifact this system produces contains
# curly quotes. Read under cp1252 with surrogateescape, those bytes become lone
# surrogates, which then fail to encode when the SDK serializes the request:
#
#   UnicodeEncodeError: 'utf-8' codec can't encode character '\udc9d'
#
# So the Reviewer crashed on every real artifact it was ever given, which is why
# it had never produced a review. Decoding stdin as UTF-8 is the fix.
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _read_input(args: argparse.Namespace) -> str:
    if args.from_log:
        # Raises rather than returns on a leak. Caught in _main, where the run
        # is abandoned before the agent is built.
        return build_from_path(args.from_log, kind=args.kind).text
    if args.input:
        return args.input
    if not sys.stdin.isatty():
        return sys.stdin.read()
    prompt = (
        "Paste the artifact to review, then Ctrl-D:\n"
        if args.agent == "reviewer"
        else "What are we working? Ctrl-D when done:\n"
    )
    print(prompt, end="")
    return sys.stdin.read()


async def _main(args: argparse.Namespace) -> int:
    if args.agent == "reviewer" and args.session:
        print(
            "Refusing --session on the reviewer. A reviewer that remembers prior "
            "batches grades against drift instead of against the standard.",
            file=sys.stderr,
        )
        return 2

    if args.from_log and args.agent != "reviewer":
        print(
            "--from-log is the reviewer's input path. It extracts a copy batch "
            "from a run log; no other agent takes one.",
            file=sys.stderr,
        )
        return 2

    try:
        text = _read_input(args)
    except ReviewInputError as exc:
        print(str(exc), file=sys.stderr)
        return 3

    agents = build_agents()
    agent = agents[args.agent]

    session = SQLiteSession(args.session, "./.seats_sessions.db") if args.session else None
    ctx = DispatchContext()

    result = await run(agent, text, context=ctx, session=session)

    print("\n" + result.final_output)

    if ctx.audit:
        print("\n--- run audit ---")
        for line in ctx.audit:
            print(line)
    if ctx.dispatched_to:
        print(f"\ndispatched to: {ctx.dispatched_to} at {ctx.dispatched_at}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="seats-prospect",
        description="SEAtS US Higher Education prospecting agent network.",
    )
    parser.add_argument("agent", choices=AGENT_CHOICES)
    parser.add_argument("-i", "--input", help="Run input. Reads stdin if omitted.")
    parser.add_argument(
        "--from-log",
        metavar="PATH",
        help=(
            "Reviewer only. Build the artifact from a Campaign Builder run log, "
            "keeping the message blocks and the differentiation check and nothing "
            "else. Refuses the run if workflow language survives the extraction."
        ),
    )
    parser.add_argument(
        "--kind",
        choices=("auto", "copy", "briefing"),
        default="auto",
        help="With --from-log: what the log holds. Auto detects a copy batch.",
    )
    parser.add_argument(
        "-s",
        "--session",
        help="Session id, to continue a conversation. Not allowed on the reviewer.",
    )
    args = parser.parse_args()
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
