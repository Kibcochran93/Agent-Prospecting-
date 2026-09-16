"""seats-next: what each account is waiting on, and who owes the next move.

    .venv\\Scripts\\python.exe scripts\\next.py
    .venv\\Scripts\\python.exe scripts\\next.py --approved-only
    .venv\\Scripts\\python.exe scripts\\next.py --no-apollo
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import board  # noqa: E402
from seats_prospecting import decisions, next_step  # noqa: E402

ORDER = [
    next_step.SEND,
    next_step.ENROL,
    next_step.SEQUENCE,
    next_step.COPY,
    next_step.WORKING,
    next_step.UNDECIDED,
    next_step.UNKNOWN,
    next_step.DECLINED,
]


def copy_files_for(display: str, key: str, cache: dict[str, str]) -> list[str]:
    """Files in lists/ whose text names this institution.

    Copy is held as markdown, one file per batch, so the only honest test is
    whether the institution appears in one. A file that names it is evidence
    copy exists, not proof the copy is any good; the Reviewer answers that.
    """
    tokens = [t for t in key.split() if len(t) > 4] or [display.lower()]
    hits = []
    for name, text in cache.items():
        low = text.lower()
        if any(t in low for t in tokens):
            hits.append(name)
    return hits


def main(argv: list[str] | None = None) -> int:
    from seats_prospecting import settings  # noqa: F401  loads .env

    parser = argparse.ArgumentParser(
        prog="seats-next",
        description="What each account is waiting on. Reads only; runs nothing.",
    )
    parser.add_argument("--approved-only", action="store_true")
    parser.add_argument("--no-apollo", action="store_true")
    args = parser.parse_args(argv)

    accounts: dict[str, board.Account] = {}
    notes: list[str] = []
    unavailable: list[str] = []
    extras: list[dict] = []
    defects: list[str] = []
    board.collect_records(board._outbox(), accounts, notes, extras)
    # Flags, not prose. Deciding whether Apollo answered by matching the text of
    # a warning is how a naming warning got read as a read failure, which put
    # three live accounts at UNKNOWN.
    flags = {"sequences_read": False, "tasks_read": False}
    if args.no_apollo:
        unavailable.append("Apollo: not checked, --no-apollo was passed.")
    else:
        flags = board.collect_apollo(accounts, notes, unavailable, defects)
    apollo_read = flags["sequences_read"]
    tasks_read = flags["tasks_read"]

    cache = {}
    lists_dir = ROOT / "lists"
    if lists_dir.is_dir():
        for path in sorted(lists_dir.glob("*.md")):
            try:
                cache[path.name] = path.read_text(encoding="utf-8-sig")
            except OSError:
                continue

    standing = decisions.current()
    steps = {}
    for key, acct in accounts.items():
        held = standing.get(key)
        facts = next_step.Facts(
            account=acct.display,
            decision=held.decision if held else None,
            copy_files=copy_files_for(acct.display, key, cache),
            sequences=acct.sequences,
            apollo_read=apollo_read,
            tasks_read=tasks_read,
        )
        steps[key] = (acct, next_step.stage_for(facts))

    shown = 0
    for stage in ORDER:
        group = [(k, a, s) for k, (a, s) in steps.items() if s.stage == stage]
        if not group:
            continue
        if args.approved_only and stage in (next_step.UNDECIDED, next_step.DECLINED):
            continue
        print()
        print(f"{stage} ({len(group)})")
        for _key, acct, step in sorted(group, key=lambda g: g[1].display.lower()):
            shown += 1
            print(f"  {acct.display}")
            print(f"     waiting on: {step.waiting_on}")
            print(f"     {step.blocker}")
            if step.command:
                print(f"     next: {step.command}")
            if step.evidence:
                print(f"     evidence: {step.evidence}")

    print()
    print(f"{shown} accounts shown.")
    automatable = [s for _a, s in steps.values() if s.automatable]
    print(f"{len(automatable)} at a stage a tool could take on. "
          f"{sum(1 for _a, s in steps.values() if s.stage == next_step.SEND)} "
          "waiting on a person to send, which nothing here can do.")
    if defects:
        print()
        print("SEQUENCE FAULTS IN APOLLO")
        for d in defects:
            print(f"  x {d}")
    if unavailable:
        print()
        print("COULD NOT BE READ")
        for reason in unavailable:
            print(f"  ! {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
