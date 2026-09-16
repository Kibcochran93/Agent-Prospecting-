"""seats-status: what is outstanding, read from the systems rather than the run.

Written 8 September 2026, the day ``HANDOFF.md`` said zero emails had been sent
while four had been delivered four days earlier. Nothing was broken. The state
of the world simply lived in three places that nobody joined: a log file for
what a run did, Apollo for what happened to the drafts, and a folder of JSON for
what reached the ledger.

So this command does not ask a run how it went. A run's account of itself is a
self-report, and self-reports here have a record: the differentiation check
passed itself on every question four batches running while the Reviewer
disagreed on every question. This asks the systems instead. Local files for what
the agents produced, Apollo for what actually became of it.

Two rules it keeps:

1. **It prints only what is waiting on a person.** Not a dashboard. If nothing
   is outstanding it says so in one line.
2. **It never reports a clean result for a source it could not reach.** Twice in
   this project a dead tool returned something that read like a finding: the
   Apollo reads were mute for a day because they spoke only API key, and a
   narrowed identity search that found nothing looked like a clear account. An
   unreachable source is reported as UNAVAILABLE, in its own section, and no
   count is claimed for it.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

APOLLO_API = "https://api.apollo.io/api/v1"

# The ledger's own rule, from the Verified through property description:
# "Oldest source date in the evidence section. Re-verify past 30 days."
STALE_AFTER_DAYS = 30


@dataclass
class Report:
    """Outstanding items by section, plus what could not be checked.

    ``unavailable`` is not a warning list. A section named here has no count and
    no clean result, which is the whole point of keeping it separate.
    """

    sections: dict[str, list[str]] = field(default_factory=dict)
    unavailable: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    def add(self, section: str, line: str) -> None:
        self.sections.setdefault(section, []).append(line)

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.sections.values())


def _outbox() -> Path:
    return Path(os.environ.get("LEDGER_OUTBOX_DIR", "./ledger-outbox")).resolve()


def _load(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _envelopes(outbox: Path, *, relayed: bool) -> Iterable[tuple[Path, dict[str, Any]]]:
    directory = outbox / "relayed" if relayed else outbox
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json")):
        if path.name.endswith(".pre-relay.json"):
            continue
        envelope = _load(path)
        if envelope is not None:
            yield path, envelope


def _subject(record: dict[str, Any]) -> str:
    for key in ("institution", "account", "batch_kind"):
        if record.get(key):
            return str(record[key])
    return "unnamed record"


# --- the local half: what the agents produced -----------------------------


def pending_relays(outbox: Path, report: Report) -> None:
    """Records written to the outbox and not yet in the ledger.

    On 8 September ten of these had accumulated while this file's own notes
    claimed one. A count that is only ever written by hand goes stale silently.
    """
    report.checked.append(f"ledger outbox at {outbox}")
    for path, envelope in _envelopes(outbox, relayed=False):
        kind = envelope.get("kind", "ledger_record")
        report.add(
            "Pending relay to the ledger",
            f"{path.name}  [{kind}]  {_subject(envelope.get('record', {}))}",
        )


def owner_review(outbox: Path, report: Report) -> None:
    """Records whose outreach decision is still Pending owner review.

    Read from the records rather than from Notion, because the Notion read is
    not wired. So this is what the agents recorded, not what the ledger says
    today: a decision made in Notion is invisible here, and that is stated in
    the coverage section rather than left for the reader to discover.
    """
    for _path, envelope in list(_envelopes(outbox, relayed=False)) + list(
        _envelopes(outbox, relayed=True)
    ):
        record = envelope.get("record", {})
        if record.get("outreach_decision") == "Pending owner review":
            owner = record.get("account_owner", "owner not recorded")
            report.add(
                "Pending owner review, as recorded by the agent",
                f"{_subject(record)}  (owner: {owner})",
            )


def open_rewrites(outbox: Path, report: Report) -> None:
    """The most recent review verdict per batch, where it asked for a rewrite.

    This cannot know whether a finding was fixed; nothing records that. It says
    what the last verdict on record asked for, which is the honest version.
    """
    for _path, envelope in list(_envelopes(outbox, relayed=False)) + list(
        _envelopes(outbox, relayed=True)
    ):
        if envelope.get("kind") != "review_verdict":
            continue
        record = envelope.get("record", {})
        if record.get("status") != "rewrite":
            continue
        for finding in record.get("findings", []):
            report.add(
                "Rewrite asked for by the last verdict on record",
                f"{finding.get('recipient', 'recipient not named')}  "
                f"[{finding.get('ground', 'ground not named')}]  "
                f"{finding.get('required_fix', 'no fix recorded')}",
            )


def stale_verification(outbox: Path, report: Report, today: date) -> None:
    """Records whose oldest source is past the ledger's own 30-day rule."""
    cutoff = today - timedelta(days=STALE_AFTER_DAYS)
    for _path, envelope in list(_envelopes(outbox, relayed=False)) + list(
        _envelopes(outbox, relayed=True)
    ):
        record = envelope.get("record", {})
        raw = record.get("verified_through")
        if not raw:
            continue
        try:
            through = date.fromisoformat(str(raw))
        except ValueError:
            report.add(
                "Verification date not readable",
                f"{_subject(record)}  (verified_through: {raw!r})",
            )
            continue
        if through < cutoff:
            report.add(
                "Past the 30-day re-verification rule",
                f"{_subject(record)}  (verified through {through}, "
                f"{(today - through).days} days)",
            )


# --- the Apollo half: what became of the drafts ---------------------------

Fetch = Callable[[str, dict[str, str]], dict[str, Any]]


def _live_fetch(path: str, params: dict[str, str]) -> dict[str, Any]:
    import httpx

    from .apollo_oauth import bearer_headers, current_token

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{APOLLO_API}{path}", headers=bearer_headers(current_token()), params=params
        )
    resp.raise_for_status()
    return resp.json()


def _label_filter() -> tuple[str, Callable[[dict[str, Any]], bool]]:
    """How sequences from this build are recognised, and it says which way.

    A filter that silently excludes the thing you are looking for is the defect
    that made an account with a live sequence read as clean, so the filter in
    use is printed rather than assumed.
    """
    label_id = os.environ.get("APOLLO_SEQUENCE_LABEL_ID", "").strip()
    if label_id:
        return (
            f"Apollo label id {label_id}",
            lambda seq: label_id in (seq.get("label_ids") or []),
        )
    return (
        "sequence names containing ' | ', this build's naming convention "
        "(set APOLLO_SEQUENCE_LABEL_ID for the exact label)",
        lambda seq: " | " in str(seq.get("name", "")),
    )


def apollo_outstanding(report: Report, fetch: Fetch | None = None, today: date | None = None) -> None:
    fetch = fetch or _live_fetch
    today = today or datetime.now(timezone.utc).date()
    description, matches = _label_filter()

    try:
        sequences = fetch("/emailer_campaigns/search", {"per_page": "100"})
    except Exception as exc:  # noqa: BLE001 - any failure means no claim
        report.unavailable.append(
            f"Apollo sequences: {type(exc).__name__}: {exc}. No sequence, touch or "
            "task state was read, so nothing below reflects Apollo."
        )
        return

    found = sequences.get("emailer_campaigns")
    if not isinstance(found, list):
        report.unavailable.append(
            "Apollo sequences: the response had no 'emailer_campaigns' list, so the "
            "shape is not what this tool understands. Reporting nothing rather than zero."
        )
        return

    report.checked.append(f"Apollo sequences, filtered by {description}")
    mine = [s for s in found if matches(s)]
    for seq in mine:
        name = seq.get("name", "unnamed sequence")
        if seq.get("active"):
            report.add(
                "Apollo sequence is active, not paused",
                f"{name}  (id {seq.get('id')}, {seq.get('num_steps')} steps, "
                f"{seq.get('unique_delivered', 0)} delivered)",
            )
        overdue = seq.get("overdue_manual_tasks_count") or 0
        if overdue:
            report.add(
                "Overdue manual tasks in Apollo",
                f"{name}  ({overdue} overdue)",
            )

    try:
        tasks = fetch("/tasks/search", {"per_page": "100", "sort_by_field": "task_due_at"})
    except Exception as exc:  # noqa: BLE001
        report.unavailable.append(
            f"Apollo tasks: {type(exc).__name__}: {exc}. Due and overdue tasks were "
            "not read."
        )
        return

    rows = tasks.get("tasks")
    if not isinstance(rows, list):
        report.unavailable.append(
            "Apollo tasks: the response had no 'tasks' list, so the shape is not what "
            "this tool understands. Reporting nothing rather than zero."
        )
        return

    report.checked.append("Apollo tasks due on or before today")
    for task in rows:
        raw = task.get("due_at") or task.get("task_due_at")
        if not raw:
            continue
        try:
            due = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if due <= today:
            report.add(
                "Apollo task due",
                f"{due}  {task.get('type', 'task')}  {task.get('name') or task.get('id')}",
            )


# --- output ---------------------------------------------------------------


def render(report: Report) -> str:
    lines: list[str] = []
    if report.total == 0 and not report.unavailable:
        lines.append("Nothing outstanding.")
    elif report.total == 0:
        lines.append("Nothing outstanding in the sources that could be read.")
    else:
        lines.append(f"{report.total} outstanding.")
    for section, items in report.sections.items():
        lines.append("")
        lines.append(f"{section} ({len(items)})")
        lines.extend(f"  - {item}" for item in items)
    if report.unavailable:
        lines.append("")
        lines.append(f"UNAVAILABLE ({len(report.unavailable)})")
        lines.extend(f"  ! {reason}" for reason in report.unavailable)
    lines.append("")
    lines.append("COVERAGE")
    for entry in report.checked:
        lines.append(f"  read: {entry}")
    lines.append(
        "  not read: the Notion ledger. NOTION_AUTHORIZATION is unset, so a decision "
        "you made in Notion does not appear here."
    )
    return "\n".join(lines)


def build_report(
    outbox: Path | None = None,
    *,
    fetch: Fetch | None = None,
    today: date | None = None,
    skip_apollo: bool = False,
) -> Report:
    outbox = outbox or _outbox()
    today = today or datetime.now(timezone.utc).date()
    report = Report()
    pending_relays(outbox, report)
    owner_review(outbox, report)
    open_rewrites(outbox, report)
    stale_verification(outbox, report, today)
    if skip_apollo:
        report.unavailable.append("Apollo: not checked, --no-apollo was passed.")
    else:
        apollo_outstanding(report, fetch=fetch, today=today)
    return report


def main(argv: list[str] | None = None) -> int:
    # Importing settings runs load_dotenv, so .env reaches os.environ. Without it
    # APOLLO_SEQUENCE_LABEL_ID is invisible and the filter silently widens to the
    # naming convention, which on 8 September also matched an unrelated campaign
    # with 249 delivered emails in it.
    from . import settings  # noqa: F401

    parser = argparse.ArgumentParser(
        prog="seats-status",
        description=(
            "What is waiting on a person, read from the outbox and Apollo rather "
            "than from a run's account of itself."
        ),
    )
    parser.add_argument(
        "--no-apollo",
        action="store_true",
        help="Skip the Apollo reads. Reported as unavailable, never as clean.",
    )
    parser.add_argument(
        "--exit-code",
        action="store_true",
        help="Exit 1 when anything is outstanding or unreadable, for use in a script.",
    )
    args = parser.parse_args(argv)

    report = build_report(skip_apollo=args.no_apollo)
    print(render(report))
    if args.exit_code and (report.total or report.unavailable):
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
