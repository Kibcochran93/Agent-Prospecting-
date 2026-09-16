"""Outreach decisions, append-only, held in files.

Replaces the Outreach decision property in Notion. The reconcile on 9 September
found ten rows carrying Approved with no local trace, so every run and every
status read had been blind to Kib's actual decisions. This is where they live
now.

Three rules, and they are the whole design:

1. **Append-only.** A decision is never edited and never deleted. Changing your
   mind writes a second record, and the later one wins on read. The history is
   the point: "approved on the 9th, declined on the 11th" is information.
2. **A decision is an intent, not an action.** Writing Approved here sends
   nothing, enrols nobody and creates no sequence. Nothing in this module
   imports Apollo, Notion or httpx, and ``tests/test_decisions.py`` asserts
   that. Approval means a person cleared the account; the sending is still done
   by a person in Apollo.
3. **Approved and Declined are Kib's.** An agent may write Pending owner review
   or Not required, the same limit the Notion property carried.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Two different people decide two different things. Cal or Miguel decide
#: whether prospecting into their account is acceptable; Kib decides whether to
#: proceed. Until 10 September both went in one field and the second erased the
#: first, so six accounts read Approved with no record of whether an owner had
#: ever been asked. ADR 0002.
OWNER_REVIEW = "owner_review"
KIB_DECISION = "kib_decision"
KINDS = frozenset({OWNER_REVIEW, KIB_DECISION})

KIB_ONLY = frozenset({"Approved", "Declined"})
AGENT_ALLOWED = frozenset({"Pending owner review", "Not required"})
VALID = KIB_ONLY | AGENT_ALLOWED


class DecisionRefused(Exception):
    """Raised rather than writing something the ledger's own rules forbid."""


@dataclass(frozen=True)
class Decision:
    account: str
    account_key: str
    decision: str
    decided_at: str
    decided_by: str
    source: str
    kind: str = KIB_DECISION
    recorded_by: str = ""
    note: str = ""
    ledger_url: str = ""
    file: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "account": self.account,
            "account_key": self.account_key,
            "kind": self.kind,
            "decision": self.decision,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
            "recorded_by": self.recorded_by,
            "source": self.source,
            "note": self.note,
            "ledger_url": self.ledger_url,
        }

    @property
    def secondhand(self) -> bool:
        """Someone else decided and Kib wrote it down.

        Miguel and Agustin have no Notion licence and no login here, and are
        not expected to get one, so every owner review will arrive verbally.
        That is a real fact and worth recording as one, but it must not look
        like the owner used the board.
        """
        return bool(self.recorded_by) and self.recorded_by != self.decided_by


@dataclass(frozen=True)
class Resolution:
    """What governs, and what it overrode.

    Kib's decision wins. That was already the behaviour, but it was an accident
    of branch order rather than a rule, and the value it overrode was invisible.
    ADR 0001.
    """

    effective: str | None
    waiting_on: str
    kib: Decision | None = None
    owner: Decision | None = None
    record_said: str | None = None
    disagreement: str | None = None


def decisions_dir() -> Path:
    return Path(os.environ.get("SEATS_DECISIONS_DIR", "./decisions")).resolve()


def slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")[:60]


def record(
    account: str,
    account_key: str,
    decision: str,
    *,
    decided_by: str,
    source: str,
    kind: str = KIB_DECISION,
    recorded_by: str = "",
    note: str = "",
    ledger_url: str = "",
    directory: Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Append one decision. Refuses rather than overwrites.

    ``decided_by`` is recorded, not checked: this runs on one laptop and there
    is no identity to verify. What is enforced is that an agent-authored source
    cannot write Approved or Declined.
    """
    if kind not in KINDS:
        raise DecisionRefused(
            f"{kind!r} is not a kind of decision this ledger recognises. "
            f"One of: {', '.join(sorted(KINDS))}."
        )
    if decision not in VALID:
        raise DecisionRefused(
            f"{decision!r} is not a decision this ledger recognises. "
            f"One of: {', '.join(sorted(VALID))}."
        )
    if kind == KIB_DECISION and decision in KIB_ONLY and decided_by.strip().lower() in {
        "", "agent", "system"
    }:
        raise DecisionRefused(
            f"{decision} is Kib's to write. An agent may only write: "
            f"{', '.join(sorted(AGENT_ALLOWED))}."
        )
    directory = directory or decisions_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    entry = Decision(
        account=account,
        account_key=account_key,
        kind=kind,
        decision=decision,
        decided_at=stamp.isoformat(),
        decided_by=decided_by,
        recorded_by=recorded_by or decided_by,
        source=source,
        note=note,
        ledger_url=ledger_url,
    )
    suffix = "owner" if kind == OWNER_REVIEW else "kib"
    name = (
        f"{stamp.strftime('%Y%m%dT%H%M%SZ')}-{slug(account_key)}-{suffix}-"
        f"{slug(decision)}.json"
    )
    path = directory / name
    if path.exists():
        raise DecisionRefused(
            f"{name} already exists. Decisions are append-only and are never "
            "overwritten; wait a second and write a new one."
        )
    path.write_text(
        json.dumps(entry.as_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def load_all(directory: Path | None = None) -> list[Decision]:
    directory = directory or decisions_dir()
    if not directory.is_dir():
        return []
    out: list[Decision] = []
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append(
            Decision(
                account=raw.get("account", ""),
                account_key=raw.get("account_key", ""),
                # Records written before 10 September carry no kind. They came
                # from a Notion property only Kib could write, so kib_decision
                # is the accurate default rather than a convenient one.
                kind=raw.get("kind") or KIB_DECISION,
                decision=raw.get("decision", ""),
                decided_at=raw.get("decided_at", ""),
                decided_by=raw.get("decided_by", ""),
                recorded_by=raw.get("recorded_by") or raw.get("decided_by", ""),
                source=raw.get("source", ""),
                note=raw.get("note", ""),
                ledger_url=raw.get("ledger_url", ""),
                file=path.name,
            )
        )
    return out


def current(
    directory: Path | None = None, kind: str = KIB_DECISION
) -> dict[str, Decision]:
    """The standing decision of one kind per account. History is kept."""
    latest: dict[str, Decision] = {}
    for entry in sorted(load_all(directory), key=lambda d: (d.decided_at, d.file)):
        if entry.account_key and entry.kind == kind:
            latest[entry.account_key] = entry
    return latest


def owner_reviews(directory: Path | None = None) -> dict[str, Decision]:
    return current(directory, kind=OWNER_REVIEW)


def resolve(
    record_said: str | None = None,
    kib: Decision | None = None,
    owner: Decision | None = None,
    *,
    account_owner: str = "",
) -> Resolution:
    """The rule, in one place, instead of an accident of branch order.

    Kib's decision governs. Where it contradicts the value the agent wrote into
    the ledger record, both are reported, because a reader of the record alone
    would otherwise get a stale answer with no hint that it is stale.

    Where Kib has not decided, what the account is waiting on depends on whether
    an owner has been asked. Nobody but Kib can record that, so an unasked
    colleague-owned account is a conversation Kib owes, not a queue someone else
    is sitting in.
    """
    disagreement = None
    if kib and record_said and kib.decision != record_said:
        disagreement = (
            f"The ledger record says {record_said!r}; Kib recorded "
            f"{kib.decision!r} on {kib.decided_at[:10]}. Kib's decision governs."
        )
    if kib:
        return Resolution(
            effective=kib.decision,
            waiting_on="nobody" if kib.decision == "Declined" else "the next step",
            kib=kib,
            owner=owner,
            record_said=record_said,
            disagreement=disagreement,
        )
    if owner and owner.decision == "Declined":
        return Resolution(
            effective="Declined",
            waiting_on="nobody",
            owner=owner,
            record_said=record_said,
            disagreement=disagreement,
        )
    if owner:
        return Resolution(
            effective=None,
            waiting_on="Kib, now that the owner has cleared it",
            owner=owner,
            record_said=record_said,
        )
    if account_owner and account_owner.strip().lower() not in {"", "unknown", "kib cochran"}:
        return Resolution(
            effective=None,
            waiting_on=f"Kib, to ask {account_owner}",
            record_said=record_said,
        )
    return Resolution(effective=None, waiting_on="Kib", record_said=record_said)


def history(account_key: str, directory: Path | None = None) -> list[Decision]:
    return [d for d in load_all(directory) if d.account_key == account_key]
