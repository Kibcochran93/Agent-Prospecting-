"""seats-board: the whole loop, one row per account, as a single HTML file.

Read-only. It creates nothing, edits nothing and sends nothing. It reads what
is already readable and renders it, then says at the bottom what it could not
read.

Three rules carried over from seats-status:

1. An unreachable source is never rendered as an empty or clean cell. It gets
   its own state, UNAVAILABLE, with the exception text in the tooltip.
2. The Notion ledger is not read. This build holds no Notion scope, so the
   ledger column is built from the notion_page_url the relay wrote into each
   outbox file. A row created directly in Notion does not appear here, and the
   coverage block says so.
3. The generated-at stamp is rendered prominently and turns amber when the page
   is over a day old, because a dashboard that looks live and is not is worse
   than no dashboard.

Usage, from the project root on Windows:

    .venv\\Scripts\\python.exe scripts\\board.py -o board.html
    .venv\\Scripts\\python.exe scripts\\board.py -o board.html --no-apollo
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from seats_prospecting.status import (  # noqa: E402
    STALE_AFTER_DAYS,
    _envelopes,
    _label_filter,
    _live_fetch,
    _outbox,
)

DONE = "done"
WAITING = "waiting"
MISSING = "missing"
UNAVAILABLE = "unavailable"

STOPWORDS = {"the", "of", "university", "college", "state", "community", "at"}


@dataclass
class Cell:
    state: str
    label: str
    href: str | None = None
    tip: str = ""


@dataclass
class Account:
    display: str
    owner: str = ""
    motion: str = ""
    verified_through: str = ""
    context: list[dict[str, Any]] = field(default_factory=list)
    briefing: list[dict[str, Any]] = field(default_factory=list)
    review: list[dict[str, Any]] = field(default_factory=list)
    relayed: list[dict[str, Any]] = field(default_factory=list)
    pending: list[dict[str, Any]] = field(default_factory=list)
    sequences: list[dict[str, Any]] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    intent: Any = None


def account_key(name: str) -> str:
    """One key per institution, tolerant of the shapes the records actually use.

    'The University of Scranton', 'University of Scranton' and
    'University of Scranton, Scranton, Pennsylvania' are one account. The
    Scranton briefing and its context verdict disagreed on the leading 'The',
    which would otherwise split that row in two.
    """
    head = name.split(",")[0]
    head = re.sub(r"^the\s+", "", head.strip(), flags=re.IGNORECASE)
    words = re.findall(r"[a-z0-9]+", head.lower())
    kept = [w for w in words if w not in STOPWORDS]
    return " ".join(kept or words)


def subject(record: dict[str, Any]) -> str:
    for key in ("institution", "account", "batch_kind"):
        if record.get(key):
            return str(record[key])
    return "unnamed record"


def kind_of(envelope: dict[str, Any]) -> str:
    kind = envelope.get("kind")
    if kind:
        return str(kind)
    record = envelope.get("record", {})
    if "findings" in record:
        return "review_verdict"
    if "searched" in record:
        return "account_context"
    return "prospect_briefing"


# --- collection -----------------------------------------------------------


def is_batch(envelope: dict, name: str) -> bool:
    """A record about several accounts at once, or about a segment.

    These are real records and they must not be dropped, but they are not
    accounts. Given a row of their own they read as a fifth institution, which
    is how 'Batch 1: Tulsa Community College, Creighton University, Wiley
    University' became a row sitting next to Tulsa Community College.
    """
    record = envelope.get("record", {})
    if record.get("batch_kind"):
        return True
    lowered = name.lower()
    if re.match(r"^(batch\b|multi-account)", lowered):
        return True
    return " segment" in lowered or lowered.endswith("segment")


INSTITUTION_WORDS = ("university", "college", "institute", "school", "academy")


def named_institutions(name: str) -> list[str]:
    """Institutions named inside a batch title, or nothing.

    Splits on both separators the titles actually use and keeps only fragments
    that name an institution, so 'SACSCOC Regional Public Universities,
    5,000-15,000 Total Headcount' yields nothing rather than a row called
    '5,000-15,000 Total Headcount'.
    """
    out = []
    for frag in re.split(r"[;,]", name):
        frag = frag.strip()
        if not frag or len(frag) < 8:
            continue
        low = frag.lower()
        if low.endswith("universities") or low.endswith("colleges"):
            continue
        if any(w in low for w in INSTITUTION_WORDS):
            out.append(re.sub(r"^(batch \d+:|multi-account batch:)\s*", "", frag,
                              flags=re.IGNORECASE))
    return out


def collect_records(
    outbox: Path,
    accounts: dict[str, Account],
    notes: list[str],
    extras: list[dict[str, Any]],
) -> None:
    """Every envelope the agents produced, pending or relayed, grouped by account.

    A batch or segment record is attached to each institution it names and gets
    no row of its own. One that names none is listed at the foot of the page
    rather than dropped.
    """
    for relayed in (False, True):
        for path, envelope in _envelopes(outbox, relayed=relayed):
            record = envelope.get("record", {})
            name = subject(record)
            kind = kind_of(envelope)
            relay = envelope.get("relay", {}) or {}
            batch = is_batch(envelope, name)
            entry = {
                "file": path.name,
                "kind": kind,
                "batch": batch,
                "title": name,
                "written_at": envelope.get("written_at", ""),
                "status": record.get("status", ""),
                "person": record.get("person", ""),
                "url": relay.get("notion_page_url") or "",
                "relayed": bool(relay.get("relayed")),
                "relayed_at": relay.get("relayed_at") or "",
                "verified_through": record.get("verified_through") or "",
                "owner": record.get("account_owner") or record.get("owner") or "",
                "motion": record.get("motion") or "",
                "decision": record.get("outreach_decision") or "",
                "findings": len(record.get("findings") or []),
                # ADR 0005: read straight off the envelope, not recomputed --
                # every prospect_briefing envelope already carries this from
                # write time (the same hash-of-record pattern digest.py uses
                # for its own records). Blank for any other kind:
                # review_verdict already has its own, differently-scoped
                # artifact_sha256 under ADR 0003 -- hashing extracted COPY
                # text, not a briefing's record dict -- and mixing the two
                # would let two different things collide on one field name.
                "artifact_sha256": (
                    envelope.get("record_sha256", "") if kind == "prospect_briefing" else ""
                ),
                "what_this_suggests": (
                    record.get("what_this_suggests", "") if kind == "prospect_briefing" else ""
                ),
            }
            targets = named_institutions(name) if batch else [name]
            if not targets:
                extras.append(entry)
                continue
            for target in targets:
                key = account_key(target)
                if not key:
                    continue
                acct = accounts.setdefault(key, Account(display=target))
                if not batch and len(target) < len(acct.display):
                    acct.display = target
                if kind == "account_context":
                    acct.context.append(entry)
                elif kind == "review_verdict":
                    acct.review.append(entry)
                else:
                    acct.briefing.append(entry)
                if entry["relayed"]:
                    acct.relayed.append(entry)
                else:
                    acct.pending.append(entry)
                if not batch:
                    acct.owner = acct.owner or entry["owner"]
                    acct.motion = acct.motion or entry["motion"]
                if entry["verified_through"]:
                    if (not acct.verified_through
                            or entry["verified_through"] < acct.verified_through):
                        acct.verified_through = entry["verified_through"]
    for acct in accounts.values():
        if not acct.owner:
            for e in acct.briefing + acct.context:
                if e["owner"]:
                    acct.owner = e["owner"]
                    break
        if not acct.motion:
            for e in acct.briefing:
                if e["motion"]:
                    acct.motion = e["motion"]
                    break
    notes.append(f"ledger outbox and relayed records at {outbox}")


def collect_intent(accounts: dict[str, Account], notes: list[str],
                   defects: list[str]) -> dict[str, Any]:
    """Institutions with live website-visitor intent, whether or not they have
    a ledger record. An inbound institution with no record still needs a row,
    or it stays invisible exactly when it is most worth seeing."""
    from seats_prospecting import digest

    records, problems = digest.load()
    for problem in problems:
        defects.append(f"context-digest: {problem}")
    grouped = digest.for_targeting()
    for key, items in grouped.items():
        newest = items[0]
        acct = accounts.setdefault(key, Account(display=newest.institution))
        acct.intent = newest
    if grouped:
        notes.append(
            f"website-visitor intent from {digest.digest_dir()}, "
            f"{len(records)} record(s), internal only and never used in copy"
        )
    return grouped


def collect_logs(root: Path, accounts: dict[str, Account], notes: list[str]) -> None:
    """Run logs, matched on the filename. Approximate, and labelled as such."""
    smoke = root / "smoke-test"
    if not smoke.is_dir():
        return
    paths = [p for p in smoke.rglob("*.log")]
    for key, acct in accounts.items():
        tokens = [t for t in key.split() if len(t) > 4]
        for path in paths:
            hay = str(path.relative_to(root)).lower().replace("_", "-")
            if any(t in hay for t in tokens):
                acct.logs.append(str(path.relative_to(root)))
    notes.append(f"run logs under {smoke.name}, matched on filename only")


def collect_apollo(
    accounts: dict[str, Account],
    notes: list[str],
    unavailable: list[str],
    defects: list[str] | None = None,
) -> dict[str, bool]:
    """Sequences from this build, and what became of their touches.

    Sequence names are '<person> | <institution>', so the institution is read
    off the name. A sequence whose name does not carry one is attached to no
    account and reported at the foot of the page instead of being dropped.
    """
    defects = defects if defects is not None else []
    description, matches = _label_filter()
    try:
        payload = _live_fetch("/emailer_campaigns/search", {"per_page": "100"})
    except Exception as exc:  # noqa: BLE001 - any failure means no claim
        unavailable.append(
            f"Apollo sequences: {type(exc).__name__}: {exc}. No sequence or touch "
            "state was read, so every Apollo cell on this page is unavailable, "
            "not empty."
        )
        return {"sequences_read": False, "tasks_read": False}
    found = payload.get("emailer_campaigns")
    if not isinstance(found, list):
        unavailable.append(
            "Apollo sequences: the response carried no 'emailer_campaigns' list. "
            "Reporting nothing rather than zero."
        )
        return {"sequences_read": False, "tasks_read": False}
    notes.append(f"Apollo sequences, filtered by {description}")
    orphans: list[str] = []
    for seq in [s for s in found if matches(s)]:
        name = str(seq.get("name", "unnamed sequence"))
        entry = {
            "id": seq.get("id"),
            "name": name,
            "active": bool(seq.get("active")),
            "steps": seq.get("num_steps"),
            "delivered": seq.get("unique_delivered") or 0,
            "opened": seq.get("unique_opened") or 0,
            "overdue": seq.get("overdue_manual_tasks_count") or 0,
        }
        institution = name.split("|", 1)[1].strip() if "|" in name else ""
        key = account_key(institution) if institution else ""
        if key and key in accounts:
            accounts[key].sequences.append(entry)
        elif key:
            accounts.setdefault(key, Account(display=institution)).sequences.append(entry)
        else:
            orphans.append(name)
    # One entry per distinct problem, not one per sequence. Eight sequences
    # sharing a name produced the same defect line eight times plus a ninth
    # about the duplication, which buries the actual finding: something is
    # creating them repeatedly.
    labelled = [x for x in found if matches(x)]
    by_name: dict[str, list[dict[str, Any]]] = {}
    for seq in labelled:
        by_name.setdefault(str(seq.get("name", "")).strip(), []).append(seq)

    orphan_names = {str(n).strip() for n in orphans}
    for name, group in sorted(by_name.items()):
        ids = [str(s.get("id")) for s in group]
        unnamed = name in orphan_names
        # creation_type says which surface made it, and that is the whole
        # diagnosis when duplicates appear. "api" and "mcp" are this build and
        # Claude sessions; "new" is a person or a browser agent clicking through
        # Apollo's sequence builder. On 10 September eight sequences appeared as
        # "new" in two hours, which is how the Chrome extension driving an
        # Apollo tab was identified as the source.
        made_by = sorted({str(s.get("creation_type") or "unknown") for s in group})
        surface = {
            "new": "the Apollo web UI, by a person or a browser agent",
            "mcp": "an AI session over MCP",
            "api": "this build",
            "clone": "a clone in the Apollo UI",
            "ai_assistant": "Apollo's own AI assistant",
            "ai_generated": "Apollo's AI generation",
            "prebuilt": "an Apollo template",
        }
        origin = "; ".join(surface.get(m, m) for m in made_by)
        if len(group) > 1:
            created = sorted(str(s.get("created_at") or "")[:16] for s in group)
            contacts = sum(int(s.get("unique_delivered") or 0) for s in group)
            defects.append(
                f"{name}: {len(group)} sequences share this name, created "
                f"{created[0]} to {created[-1]}, {contacts} delivered between "
                f"them. Their figures are summed onto one row, so a duplicate "
                f"reads as activity rather than as a duplicate. Created by: "
                f"{origin}. "
                + ("It also carries no ' | institution', so the group sits on no "
                   "account row. " if unnamed else "")
                + f"ids: {', '.join(ids)}"
            )
        elif unnamed:
            defects.append(
                f"{name}: carries the label but no ' | institution', so it sits "
                "on no account row and nothing tracks it. Sequences this build "
                "writes are named 'person | institution'. Created by: "
                f"{origin}. Rename it or take the label off. id: {ids[0]}"
            )

    try:
        _live_fetch("/tasks/search", {"per_page": "1"})
    except Exception as exc:  # noqa: BLE001
        unavailable.append(
            f"Apollo tasks: {type(exc).__name__}: {exc}. Due and overdue manual "
            "tasks were not read, so the later-touches column reports what the "
            "sequence shape implies and not what is actually queued."
        )
        return {"sequences_read": True, "tasks_read": False}
    notes.append("Apollo tasks")
    return {"sequences_read": True, "tasks_read": True}


# --- cells ----------------------------------------------------------------


def cell_records(entries: list[dict[str, Any]], label_missing: str) -> Cell:
    if not entries:
        return Cell(MISSING, "none", tip=label_missing)
    newest = sorted(entries, key=lambda e: e["written_at"])[-1]
    extra = f", {len(entries)} records" if len(entries) > 1 else ""
    status = newest.get("status") or ""
    label = (status or "written") + extra
    tip = f"{newest['file']}  written {newest['written_at']}"
    return Cell(DONE, label, tip=tip)


def cell_review(entries: list[dict[str, Any]]) -> Cell:
    if not entries:
        return Cell(MISSING, "none", tip="No reviewer verdict on record for this account.")
    newest = sorted(entries, key=lambda e: e["written_at"])[-1]
    if newest.get("status") == "rewrite":
        return Cell(
            WAITING,
            f"rewrite, {newest['findings']} findings",
            tip=f"{newest['file']}. Nothing records whether a finding was fixed; "
            "this is what the last verdict asked for.",
        )
    return Cell(DONE, newest.get("status") or "verdict", tip=newest["file"])


def cell_ledger(acct: Account) -> Cell:
    if acct.pending:
        newest = sorted(acct.pending, key=lambda e: e["written_at"])[-1]
        return Cell(
            WAITING,
            f"{len(acct.pending)} awaiting relay",
            tip=f"{newest['file']} is in the outbox and not in the ledger.",
        )
    if acct.relayed:
        newest = sorted(acct.relayed, key=lambda e: e["relayed_at"] or "")[-1]
        return Cell(
            DONE,
            f"{len(acct.relayed)} relayed",
            href=newest.get("url") or None,
            tip=f"Relayed {newest.get('relayed_at') or 'date not recorded'}. Link "
            "opens the newest row. Notion itself was not read.",
        )
    return Cell(MISSING, "none", tip="No record has reached the ledger for this account.")


def cell_decision(acct: Account) -> Cell:
    decisions = [e["decision"] for e in acct.briefing if e["decision"]]
    if not decisions:
        return Cell(MISSING, "none", tip="No briefing recorded an outreach decision.")
    latest = decisions[-1]
    if latest == "Pending owner review":
        return Cell(
            WAITING,
            latest,
            tip=f"Waiting on {acct.owner or 'the account owner'}, then on Kib. As "
            "recorded by the agent, not read from Notion.",
        )
    return Cell(DONE, latest, tip="As recorded by the agent, not read from Notion.")


def cell_sequence(acct: Account, apollo_down: bool) -> Cell:
    if apollo_down:
        return Cell(UNAVAILABLE, "not read", tip="Apollo could not be read this run.")
    if not acct.sequences:
        return Cell(MISSING, "none", tip="No sequence from this build carries this institution.")
    parts = []
    for seq in acct.sequences:
        parts.append(f"{seq['name']} (id {seq['id']}, {seq['steps']} steps, "
                     f"{'active' if seq['active'] else 'paused'})")
    label = f"{len(acct.sequences)} sequence" + ("s" if len(acct.sequences) > 1 else "")
    state = WAITING if any(s["active"] for s in acct.sequences) else DONE
    return Cell(state, label, tip="; ".join(parts))


def cell_touch_one(acct: Account, apollo_down: bool) -> Cell:
    if apollo_down:
        return Cell(UNAVAILABLE, "not read", tip="Apollo could not be read this run.")
    if not acct.sequences:
        return Cell(MISSING, "none", tip="No sequence to have a first touch.")
    delivered = sum(s["delivered"] for s in acct.sequences)
    opened = sum(s["opened"] for s in acct.sequences)
    if delivered:
        return Cell(DONE, f"{delivered} delivered, {opened} opened",
                    tip="Sent by Kib by hand in Apollo. No agent here can send.")
    return Cell(WAITING, "queued, not delivered",
                tip="Touch 1 is a manual task waiting on a person.")


def cell_later_touches(acct: Account, apollo_down: bool, tasks_down: bool) -> Cell:
    if apollo_down:
        return Cell(UNAVAILABLE, "not read", tip="Apollo could not be read this run.")
    if not acct.sequences:
        return Cell(MISSING, "none", tip="No sequence to have later touches.")
    if tasks_down:
        steps = sum((s["steps"] or 0) for s in acct.sequences)
        return Cell(
            UNAVAILABLE,
            f"{steps} steps exist, queue not read",
            tip="The Apollo token has no task scope, so what is actually queued or "
            "overdue could not be read. This is the step count, not the queue.",
        )
    overdue = sum(s["overdue"] for s in acct.sequences)
    if overdue:
        return Cell(WAITING, f"{overdue} overdue", tip="Overdue manual tasks in Apollo.")
    return Cell(DONE, "nothing overdue", tip="Read from Apollo.")


def cell_verification(acct: Account, today: date) -> Cell:
    if not acct.verified_through:
        return Cell(MISSING, "none", tip="No record carries a verified_through date.")
    try:
        through = date.fromisoformat(acct.verified_through)
    except ValueError:
        return Cell(MISSING, str(acct.verified_through), tip="Date not readable.")
    age = (today - through).days
    state = WAITING if age > STALE_AFTER_DAYS else DONE
    return Cell(state, f"{through} ({age}d)",
                tip=f"The ledger's own rule is re-verify past {STALE_AFTER_DAYS} days.")


def cell_logs(acct: Account) -> Cell:
    if not acct.logs:
        return Cell(MISSING, "none", tip="No log filename matched this account.")
    return Cell(DONE, f"{len(acct.logs)} logs", tip="; ".join(sorted(set(acct.logs))[:8]))


# --- render ---------------------------------------------------------------

COLUMNS = [
    ("Account", "The institution. One row per account, not per record."),
    ("Owner", "HubSpot account owner as the agent recorded it."),
    ("Motion", "Motion the briefing recommended."),
    ("Context", "Account Context verdict: is this account already someone's?"),
    ("Briefing", "Prospect Briefing record."),
    ("Reviewer", "Last reviewer verdict on record."),
    ("Ledger", "Has a record reached the Notion ledger."),
    ("Decision", "Outreach decision, as recorded by the agent."),
    ("Apollo", "Sequences from this build."),
    ("Touch 1", "First touch, sent by a person."),
    ("Later touches", "Touches 2 and up."),
    ("Verified", "Oldest source date, and its age."),
    ("Logs", "Run logs, matched on filename."),
]

CSS = """
:root { color-scheme: light dark; }
body { font: 15px/1.5 -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; padding: 24px;
  background: #fbfbfa; color: #1f1f1f; }
h1 { font-size: 20px; margin: 0 0 4px; }
.sub { color: #666; margin: 0 0 18px; font-size: 13px; }
.stamp { display: inline-block; padding: 4px 10px; border-radius: 4px; font-size: 13px;
  background: #e8f3ea; color: #1c5c2e; }
.stamp.stale { background: #fdf1dc; color: #7a4c05; }
table { border-collapse: collapse; width: 100%; background: #fff; font-size: 13px; }
th, td { border: 1px solid #e4e4e2; padding: 7px 9px; text-align: left; vertical-align: top; }
th { background: #f4f4f2; font-weight: 600; position: sticky; top: 0; }
td.acct { font-weight: 600; white-space: nowrap; }
.pill { display: inline-block; padding: 2px 7px; border-radius: 3px; font-size: 12px;
  white-space: nowrap; }
.done { background: #e8f3ea; color: #1c5c2e; }
.waiting { background: #fdf1dc; color: #7a4c05; }
.missing { background: #f1f1ef; color: #6b6b6b; }
.unavailable { background: #eaeef7; color: #2f4577; border: 1px dashed #94a5cc; }
a.pill { text-decoration: none; }
h2 { font-size: 15px; margin: 28px 0 8px; }
ul { margin: 0 0 12px; padding-left: 20px; color: #444; font-size: 13px; }
li { margin-bottom: 3px; }
.key { margin: 14px 0 18px; font-size: 13px; color: #555; }
footer { margin-top: 26px; font-size: 12px; color: #777; }
"""


def pill(cell: Cell) -> str:
    tip = html.escape(cell.tip, quote=True)
    label = html.escape(cell.label)
    if cell.href:
        return (f'<a class="pill {cell.state}" href="{html.escape(cell.href, quote=True)}" '
                f'title="{tip}" target="_blank" rel="noopener">{label}</a>')
    return f'<span class="pill {cell.state}" title="{tip}">{label}</span>'


def render(
    accounts: dict[str, Account],
    extras: list[dict[str, Any]],
    notes: list[str],
    defects: list[str],
    unavailable: list[str],
    generated: datetime,
    today: date,
) -> str:
    apollo_down = any(u.startswith("Apollo sequences") for u in unavailable)
    tasks_down = any(u.startswith("Apollo tasks") for u in unavailable)
    stale = "" if generated.date() == today else " stale"

    rows = []
    for _key, acct in sorted(accounts.items(), key=lambda kv: kv[1].display.lower()):
        cells = [
            f'<td class="acct">{html.escape(acct.display)}</td>',
            f"<td>{html.escape(acct.owner or '-')}</td>",
            f"<td>{html.escape(acct.motion or '-')}</td>",
            f"<td>{pill(cell_records(acct.context, 'No context verdict for this account.'))}</td>",
            f"<td>{pill(cell_records(acct.briefing, 'No briefing record for this account.'))}</td>",
            f"<td>{pill(cell_review(acct.review))}</td>",
            f"<td>{pill(cell_ledger(acct))}</td>",
            f"<td>{pill(cell_decision(acct))}</td>",
            f"<td>{pill(cell_sequence(acct, apollo_down))}</td>",
            f"<td>{pill(cell_touch_one(acct, apollo_down))}</td>",
            f"<td>{pill(cell_later_touches(acct, apollo_down, tasks_down))}</td>",
            f"<td>{pill(cell_verification(acct, today))}</td>",
            f"<td>{pill(cell_logs(acct))}</td>",
        ]
        rows.append("<tr>" + "".join(cells) + "</tr>")

    head = "".join(
        f'<th title="{html.escape(tip, quote=True)}">{html.escape(name)}</th>'
        for name, tip in COLUMNS
    )

    parts = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<title>SE Prospecting Board</title>",
        f"<style>{CSS}</style></head><body>",
        "<h1>SE Prospecting Board</h1>",
        "<p class=\"sub\">One row per account. Every cell is read from a system, "
        "not from a run's account of itself. Hover any cell for the source.</p>",
        f'<p><span class="stamp{stale}">Generated '
        f'{generated.strftime("%Y-%m-%d %H:%M UTC")}</span></p>',
        '<p class="key">'
        '<span class="pill done">done</span> '
        '<span class="pill waiting">waiting on a person</span> '
        '<span class="pill missing">missing</span> '
        '<span class="pill unavailable">could not be read</span>'
        "</p>",
        f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>",
    ]

    if extras:
        parts.append(
            f"<h2>Batch and segment records ({len(extras)})</h2>"
            "<p class=\"sub\">Records about a segment or a cadence rather than one "
            "institution. They have no row because they are not accounts.</p><ul>"
        )
        for e in extras:
            where = "relayed" if e["relayed"] else "awaiting relay"
            link = (f' <a href="{html.escape(e["url"], quote=True)}" target="_blank" '
                    f'rel="noopener">ledger row</a>' if e["url"] else "")
            parts.append(
                f"<li>{html.escape(e['title'])} [{html.escape(e['kind'])}, "
                f"{where}]{link}</li>"
            )
        parts.append("</ul>")

    if defects:
        parts.append(
            f"<h2>Sequence faults in Apollo ({len(defects)})</h2>"
            "<p class=\"sub\">Apollo read fine. These are problems with what is in "
            "it.</p><ul>"
        )
        parts += [f"<li>{html.escape(d)}</li>" for d in defects]
        parts.append("</ul>")

    if unavailable:
        parts.append(f"<h2>Could not be read ({len(unavailable)})</h2><ul>")
        parts += [f"<li>{html.escape(u)}</li>" for u in unavailable]
        parts.append("</ul>")

    parts.append("<h2>What this page read</h2><ul>")
    parts += [f"<li>{html.escape(n)}</li>" for n in notes]
    parts.append(
        "<li><strong>Not read: the Notion ledger.</strong> This build holds no "
        "Notion scope. The Ledger column is built from the page URL the relay "
        "wrote into each outbox file, so a row created directly in Notion does "
        "not appear on this page, and a Status or Outreach decision changed in "
        "Notion is not reflected here.</li>"
    )
    parts.append(
        "<li><strong>Not read: whether a reviewer finding was fixed.</strong> "
        "Nothing in the system records that. The Reviewer column says what the "
        "last verdict asked for.</li>"
    )
    parts.append("</ul>")
    parts.append(
        "<footer>Read-only. This page creates nothing, edits nothing and sends "
        "nothing. Regenerate with <code>scripts\\board.py</code>. Contains "
        "prospect names and CRM record ids.</footer></body></html>"
    )
    return "".join(parts)


def main(argv: list[str] | None = None) -> int:
    from seats_prospecting import settings  # noqa: F401  loads .env

    parser = argparse.ArgumentParser(
        prog="seats-board",
        description="Render the whole loop, one row per account, as one HTML file.",
    )
    parser.add_argument("-o", "--out", default="board.html")
    parser.add_argument(
        "--no-apollo",
        action="store_true",
        help="Skip the Apollo reads. Rendered as unavailable, never as clean.",
    )
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    generated = datetime.now(timezone.utc)
    today = generated.date()
    accounts: dict[str, Account] = {}
    notes: list[str] = []
    unavailable: list[str] = []

    extras: list[dict[str, Any]] = []
    defects: list[str] = []
    collect_records(_outbox(), accounts, notes, extras)
    collect_logs(root, accounts, notes)
    if args.no_apollo:
        unavailable.append(
            "Apollo sequences: not checked, --no-apollo was passed. Every Apollo "
            "cell is unavailable rather than empty."
        )
        unavailable.append("Apollo tasks: not checked, --no-apollo was passed.")
    else:
        collect_apollo(accounts, notes, unavailable, defects)

    out = Path(args.out)
    out.write_text(render(accounts, extras, notes, defects, unavailable, generated, today), encoding="utf-8")
    print(f"{out.resolve()}  ({len(accounts)} accounts, {len(extras)} batch records, "
          f"{len(defects)} sequence faults, {len(unavailable)} unreadable)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
