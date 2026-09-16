"""Move relayed outbox records into relayed/, per LEDGER-RELAY.md step 5.

Keeps a .pre-relay.json copy of the original bytes, matching the convention
already in relayed/. Run from the project root with the Windows interpreter:
device_bash cannot delete files on the mount.
"""
import json, pathlib, shutil, sys

RELAYED_AT = "2026-09-08T20:15:00+00:00"
PAGES = {
 "20260903T190022Z-campaign-builder-sacscoc-regional-public-universities-5-000-15-000-total-head.json": "3d553f704a4581169fefe79820ce6ed1",
 "20260903T195021Z-prospect-briefing-university-of-central-arkansas.json": "3d553f704a4581158244cb2ea477e6ef",
 "20260903T201343Z-campaign-builder-arkansas-public-universities-scheduling-platform-segment.json": "3d553f704a45819dad2ff14dd3232cdc",
 "20260903T203243Z-prospect-briefing-university-of-central-arkansas.json": "3d553f704a4581719593fd9ce9218014",
 "20260904T152507Z-campaign-builder-multi-account-batch-tulsa-community-college-creighton-univer.json": "3d553f704a4581d39cd1c8647f176e3e",
 "20260904T193136Z-context-conflict-shaniqua-adams.json": "3d553f704a4581368ccde86582750250",
 "20260904T193521Z-context-conflict-wayne-young-jr.json": "3d553f704a458133bc5bdbac6567f5ec",
 "20260904T193910Z-context-conflict-wayne-young-jr.json": "3d553f704a45817a86c9fb6af2b722d7",
 "20260904T200957Z-prospect-briefing-wiley-university.json": "3d553f704a45812cbd64c34cb6291ee2",
 "20260908T195935Z-review-rewrite-cadence-additivity.json": "3d553f704a458119bf09eb5ce86e0e8c",
}

out = pathlib.Path("ledger-outbox")
dest = out / "relayed"
dest.mkdir(parents=True, exist_ok=True)
fail = 0
for name, page in PAGES.items():
    src = out / name
    if not src.exists():
        print("SKIP missing:", name); fail += 1; continue
    pre = dest / (name[:-5] + ".pre-relay.json")
    if not pre.exists():
        shutil.copy2(src, pre)
    o = json.loads(src.read_text(encoding="utf-8"))
    o["relay"]["relayed"] = True
    o["relay"]["relayed_at"] = RELAYED_AT
    o["relay"]["notion_page_url"] = f"https://app.notion.com/p/{page}"
    (dest / name).write_text(json.dumps(o, indent=2, ensure_ascii=False), encoding="utf-8")
    src.unlink()
    print("MOVED", name)
left = sorted(p.name for p in out.glob("*.json"))
print("REMAINING IN OUTBOX:", left if left else "none")
sys.exit(1 if fail else 0)
