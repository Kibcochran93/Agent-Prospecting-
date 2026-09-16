"""Reconcile the Notion export against the local records. Read-only.

Runs both directions, because each direction finds a different fault:

  rows with no record   -> state that exists only in Notion. Lost if Notion goes.
  records with no row   -> a relay that did not land, or was never run.
  row disagrees         -> a decision made by hand in Notion after the relay.

The point of the third check is Outreach decision. Agents may only write
"Pending owner review" or "Not required", so "Approved" or "Declined" on a row
is Kib's own judgement, made in Notion, recorded nowhere else.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXPORT = ROOT / "ledger-export" / "rows-2026-09-09.jsonl"
OUTBOX = ROOT / "ledger-outbox"

KIB_ONLY_DECISIONS = {"Approved", "Declined"}


def page_id(url: str) -> str:
    return re.sub(r"[^0-9a-f]", "", (url or "").lower())[-32:]


def load_rows() -> list[dict]:
    rows = []
    for line in EXPORT.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def load_records() -> list[dict]:
    out = []
    for directory, relayed in ((OUTBOX, False), (OUTBOX / "relayed", True)):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            if path.name.endswith(".pre-relay.json"):
                continue
            try:
                env = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                print(f"UNREADABLE  {path.name}")
                continue
            relay = env.get("relay", {}) or {}
            rec = env.get("record", {})
            out.append(
                {
                    "file": path.name,
                    "in_relayed_dir": relayed,
                    "relayed_flag": bool(relay.get("relayed")),
                    "page": page_id(relay.get("notion_page_url", "")),
                    "url": relay.get("notion_page_url", ""),
                    "subject": rec.get("institution") or rec.get("account") or rec.get("batch_kind") or "?",
                    "decision": rec.get("outreach_decision"),
                }
            )
    return out


def main() -> int:
    rows = load_rows()
    records = load_records()
    by_page = {}
    for rec in records:
        if rec["page"]:
            by_page.setdefault(rec["page"], []).append(rec)

    print(f"{len(rows)} exported rows, {len(records)} local records\n")

    print("=" * 72)
    print("A. NOTION-ONLY: rows no local record points at")
    print("=" * 72)
    orphans = [r for r in rows if page_id(r["url"]) not in by_page]
    for row in orphans:
        print(f"  {row['Name']}")
        print(f"     status={row['Status']}  decision={row['Outreach decision']}  "
              f"agent={row['Source agent']}")
        print(f"     {row['url']}")
    if not orphans:
        print("  none")

    print()
    print("=" * 72)
    print("B. UNLANDED: local records whose page is not in the export")
    print("=" * 72)
    exported = {page_id(r["url"]) for r in rows}
    missing = [rec for rec in records if rec["page"] and rec["page"] not in exported]
    for rec in missing:
        print(f"  {rec['file']}")
        print(f"     subject={rec['subject']}  url={rec['url']}")
    unrelayed = [rec for rec in records if not rec["page"]]
    for rec in unrelayed:
        print(f"  {rec['file']}  (never relayed, no page url)")
    if not missing and not unrelayed:
        print("  none")

    print()
    print("=" * 72)
    print("C. KIB'S OWN DECISIONS, made in Notion and held nowhere else")
    print("=" * 72)
    kib = [r for r in rows if r["Outreach decision"] in KIB_ONLY_DECISIONS]
    for row in kib:
        recs = by_page.get(page_id(row["url"]), [])
        was = recs[0]["decision"] if recs else "no local record"
        print(f"  {row['Outreach decision']:<9} {row['Name']}")
        print(f"     the agent recorded: {was}")
    print(f"\n  {len(kib)} rows carry a decision only Kib can write.")

    print()
    print("=" * 72)
    print("D. STATUS moved off Draft in Notion")
    print("=" * 72)
    moved = [r for r in rows if r["Status"] != "Draft"]
    for row in moved:
        print(f"  {row['Status']:<6} {row['Name']}")
    if not moved:
        print("  none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
