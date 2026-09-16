"""seats-missed: US accounts that raised a hand and got no answer.

    .venv\\Scripts\\python.exe scripts\\missed.py
    .venv\\Scripts\\python.exe scripts\\missed.py --stale-ok
    .venv\\Scripts\\python.exe scripts\\missed.py --tier FORM_NO_REPLY

Reads only. Creates nothing, contacts nobody. The owner is printed so they can
be told, which is Kib's decision of 10 September: told, not asked.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from seats_prospecting import apollo_intent, missed  # noqa: E402

OWNERS = {
    "1399544781": "Cal O'Donovan",
    "77501718": "Miguel Pescador",
    "30815754": "munir Choudhry",
    "1421956559": "owner 1421956559",
    "1399545281": "owner 1399545281",
}


def main(argv: list[str] | None = None) -> int:
    from seats_prospecting import settings  # noqa: F401  loads .env

    parser = argparse.ArgumentParser(prog="seats-missed")
    parser.add_argument("--country", default="United States")
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--tier", default="", help="Show one tier only.")
    parser.add_argument("--days", type=int, default=30,
                        help="Apollo visitor window: 7, 15, 30, 60 or 90.")
    parser.add_argument("--no-apollo", action="store_true",
                        help="Skip the live pull and use the HubSpot field only.")
    parser.add_argument("--high-intent", action="store_true",
                        help="Only visitors who touched a high-intent path.")
    parser.add_argument(
        "--stale-ok",
        action="store_true",
        help="Rank anyway when the trigger field has stopped updating. The dates "
        "will be as old as the feed.",
    )
    args = parser.parse_args(argv)

    try:
        rows = asyncio.run(missed.fetch(args.country, args.pages))
    except missed.TriggerStale as exc:
        print(f"NOT READ: {exc}", file=sys.stderr)
        return 2

    live: dict[str, object] = {}
    if args.no_apollo:
        print("Apollo not read (--no-apollo). Using the HubSpot field, which "
              "stopped updating in April 2026.")
    else:
        try:
            if args.high_intent:
                visitors = asyncio.run(apollo_intent.fetch_high_intent(args.days))
                paths = ", ".join(apollo_intent.__dict__ and
                                  __import__("seats_prospecting.reveal_gate",
                                             fromlist=["x"]).high_intent_paths())
                print(f"High intent only: {paths}")
                if not visitors:
                    print("  No identified visitor has touched one of those paths "
                          f"in {args.days} days. That is not evidence nobody did: "
                          "person-level identification is US traffic only.")
            else:
                visitors = asyncio.run(apollo_intent.fetch(args.days))
            live = apollo_intent.by_domain(visitors)
            vage, vusable = apollo_intent.freshness(visitors)
            print(f"Apollo live visitors: {len(visitors)} identified in the last "
                  f"{args.days} days, newest {vage} days old, "
                  f"{'usable' if vusable else 'STALE'}.")
            print(f"  {apollo_intent.COVERAGE_NOTE}")
        except apollo_intent.IntentUnavailable as exc:
            print(f"APOLLO NOT READ: {exc}", file=sys.stderr)
            print("  Falling back to the HubSpot field, which is stale. Any "
                  "ranking below is only as current as that field.")

    print(f"{len(rows)} {args.country} companies with an intent signal and no open deal.")
    # Pull in the CRM rows behind the live visitors, even when the dead field
    # never mentioned them. The population is the union of both sources.
    known = {str(r.get("id")) for r in rows}
    if live:
        try:
            extra = [r for r in asyncio.run(missed.fetch_domains(sorted(live)))
                     if str(r.get("id")) not in known]
        except missed.TriggerStale as exc:
            extra = []
            print(f"LIVE DOMAINS NOT JOINED: {exc}", file=sys.stderr)
        if extra:
            print(f"{len(extra)} account(s) added from live Apollo visitors that the "
                  "HubSpot intent field never recorded.")
        rows = rows + extra
    matched = [
        r for r in rows
        if str((r.get("properties") or {}).get("domain") or "").lower()
        .removeprefix("www.") in live
    ]
    print(f"{len(matched)} of {len(rows)} have a live Apollo visitor.")
    intents = [(r.get("properties") or {}).get("seats_last_intent_visit") for r in rows]
    age, usable = missed.freshness(intents)
    print(f"Newest intent value in the set: {age} days old, "
          f"{'usable' if usable else 'STALE'}.")
    print()

    try:
        items = missed.missed_from_rows(
            rows, require_fresh_trigger=not args.stale_ok, live_intent=live)
    except missed.TriggerStale as exc:
        print(f"REFUSED TO RANK: {exc}", file=sys.stderr)
        return 3

    if args.tier:
        items = [m for m in items if m.tier == args.tier.upper()]

    shown = 0
    for tier in missed.TIERS:
        group = [m for m in items if m.tier == tier]
        if not group:
            continue
        print(f"{tier} ({len(group)})")
        for m in group:
            shown += 1
            owner = OWNERS.get(m.owner_id, m.owner_id or "unassigned")
            print(f"  {m.name}  [{m.domain or 'no domain'}]")
            print(f"     {m.why}")
            if m.intent_source == "apollo_live":
                print(f"     LIVE signal via Apollo: {m.visitor or 'visitor identified'}")
            print(f"     owner to tell: {owner}   company {m.company_id}"
                  f"   intent source: {m.intent_source}")
        print()

    print(f"{shown} accounts. Ownership is printed to be told, never to filter.")
    if not usable:
        print("The dates above are as old as the feed, which stopped in April 2026.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
