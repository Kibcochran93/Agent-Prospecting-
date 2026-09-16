"""Seed decisions/ from the Notion export. Run once, then never again.

The reconcile on 9 September found ten rows carrying Approved, a value only Kib
can write, held nowhere but Notion. This carries them across before Notion is
frozen.

Two of the ten are segment records and one is a batch. They are not accounts
and cannot carry outreach approval, so they seed nothing and are listed instead.
Kib's call, 9 September.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from board import account_key, is_batch  # noqa: E402
from seats_prospecting import decisions  # noqa: E402

EXPORT = ROOT / "ledger-export" / "rows-2026-09-09.jsonl"
SOURCE = "Notion export 2026-09-09, ledger-export/rows-2026-09-09.jsonl"


def main() -> int:
    if not EXPORT.exists():
        print(f"No export at {EXPORT}.", file=sys.stderr)
        return 2
    existing = decisions.current()
    if existing:
        print(
            f"{len(existing)} decisions already recorded. This script seeds once; "
            "refusing to run again so it cannot double-write history.",
            file=sys.stderr,
        )
        return 3

    rows = []
    for line in EXPORT.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))

    # One standing decision per account. Two Notion rows can carry Approved for
    # the same institution: University of Central Arkansas has a 2025-07-01 row
    # and a 2025-09-05 row, both Approved. The later row wins and the earlier is
    # listed as superseded, because seeding both would write two standing
    # decisions for one account and the second would only shadow the first.
    seeded, skipped, superseded = [], [], []
    chosen: dict[str, dict] = {}
    for row in rows:
        decision = row.get("Outreach decision")
        if decision not in decisions.KIB_ONLY:
            continue
        name = row.get("Institution") or row.get("Name") or ""
        if is_batch({"record": {}}, name):
            skipped.append((name, decision))
            continue
        key = account_key(name)
        if not key:
            skipped.append((name, decision))
            continue
        prior = chosen.get(key)
        if prior is None or (row.get("created") or "") >= (prior.get("created") or ""):
            if prior is not None:
                superseded.append((prior.get("Name", ""), prior.get("Outreach decision", "")))
            chosen[key] = row
        else:
            superseded.append((row.get("Name", ""), decision))

    for key, row in chosen.items():
        decision = row["Outreach decision"]
        name = row.get("Institution") or row.get("Name") or ""
        path = decisions.record(
            account=name,
            account_key=key,
            decision=decision,
            decided_by="Kib Cochran",
            source=SOURCE,
            note=(
                "Carried across from the Notion Outreach decision property. The "
                "date is the export date, not the date the decision was made; "
                "Notion recorded no decision date."
            ),
            ledger_url=row.get("url", ""),
        )
        seeded.append((name, decision, path.name))

    print(f"seeded {len(seeded)}:")
    for name, decision, file in seeded:
        print(f"  {decision:<9} {name}  -> {file}")
    print(f"\nnot seeded, not accounts ({len(skipped)}):")
    for name, decision in skipped:
        print(f"  {decision:<9} {name}")
    print(f"\nnot seeded, superseded by a later row for the same account "
          f"({len(superseded)}):")
    for name, decision in superseded:
        print(f"  {decision:<9} {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
