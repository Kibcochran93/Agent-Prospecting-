"""Reading the Teams and Apollo intent digest.

The records in context-digest/ are internal. They say which institutions and
which named people came to seatsone.com and what they read. That is useful for
deciding who to research and which motion to pursue, and it is not usable in
copy, ever.

This module enforces that split rather than trusting a caller to remember it:

``for_targeting()`` returns the record.
    Full detail, for the board and for a person's judgement.

``for_copy()`` returns nothing. There is no other option.
    Not a redacted version, not a summary. A function that returns an empty
    dict is a control; a note in a docstring is a hope. Telling a prospect we
    watched them read the pricing page is the failure this prevents.

Hashes are verified on read. A record whose hash does not match is dropped and
reported, not used, on the same reasoning as the ledger relay.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

STALE_AFTER_DAYS = 30


@dataclass(frozen=True)
class Intent:
    account_key: str
    institution: str
    signal: str
    source: str
    source_date: str
    age_days: int
    stale: bool
    person: str = ""
    person_title: str = ""
    total_visits: int = 0
    last_visit: str = ""
    apollo_intent: str = ""
    interest_areas: tuple[str, ...] = ()
    named_contacts: tuple[str, ...] = ()
    in_pipeline: bool | None = None
    conflicts_with_ledger: str = ""
    file: str = ""

    @property
    def internal_only(self) -> bool:
        """Always true. Every record in this store is internal."""
        return True


def digest_dir() -> Path:
    return Path(os.environ.get("SEATS_DIGEST_DIR", "./context-digest")).resolve()


def _verify(envelope: dict[str, Any]) -> bool:
    record = envelope.get("record")
    stated = envelope.get("record_sha256")
    if not isinstance(record, dict) or not stated:
        return False
    calc = hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return calc == stated


def load(directory: Path | None = None, today: date | None = None
         ) -> tuple[list[Intent], list[str]]:
    """Every readable intent record, and a note for each one that was not."""
    directory = directory or digest_dir()
    today = today or date.today()
    out: list[Intent] = []
    problems: list[str] = []
    if not directory.is_dir():
        return out, problems
    for path in sorted(directory.glob("*.json")):
        try:
            envelope = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"{path.name}: unreadable ({exc}). Not used.")
            continue
        if not _verify(envelope):
            problems.append(
                f"{path.name}: the record hash does not match its contents, so it "
                "was altered after it was written. Dropped, not used."
            )
            continue
        rec = envelope["record"]
        if rec.get("confidentiality") != "internal_only":
            problems.append(
                f"{path.name}: no internal_only marking. Dropped: every record in "
                "this store is internal and one without the marking is suspect."
            )
            continue
        source_date = str(rec.get("source_date") or "")
        try:
            age = (today - date.fromisoformat(source_date)).days
        except ValueError:
            age = int(rec.get("age_days") or 0)
        out.append(
            Intent(
                account_key=str(rec.get("account_key") or ""),
                institution=str(rec.get("institution") or ""),
                signal=str(rec.get("signal") or ""),
                source=str(rec.get("source") or ""),
                source_date=source_date,
                age_days=age,
                stale=age > STALE_AFTER_DAYS,
                person=str(rec.get("person") or ""),
                person_title=str(rec.get("person_title") or ""),
                total_visits=int(rec.get("total_visits") or 0),
                last_visit=str(rec.get("last_visit") or source_date),
                apollo_intent=str(rec.get("apollo_intent") or ""),
                interest_areas=tuple(rec.get("interest_areas") or ()),
                named_contacts=tuple(rec.get("named_contacts") or ()),
                in_pipeline=rec.get("in_pipeline"),
                conflicts_with_ledger=str(rec.get("conflicts_with_ledger") or ""),
                file=path.name,
            )
        )
    return out, problems


def for_targeting(directory: Path | None = None, today: date | None = None
                  ) -> dict[str, list[Intent]]:
    """Intent per account, newest first. For deciding what to work on."""
    records, _problems = load(directory, today)
    grouped: dict[str, list[Intent]] = {}
    for rec in records:
        if rec.account_key:
            grouped.setdefault(rec.account_key, []).append(rec)
    for items in grouped.values():
        items.sort(key=lambda r: r.last_visit or r.source_date, reverse=True)
    return grouped


def for_copy(*_args: Any, **_kwargs: Any) -> dict[str, str]:
    """Nothing. Intent data never reaches outreach copy.

    Deliberately not a redaction and not a summary. There is no argument that
    makes this return anything: a prospect must never be told, or be able to
    infer, that their browsing was watched. tests/test_digest.py asserts that
    this function has no branch that can return content.
    """
    return {}


def freshest(directory: Path | None = None, today: date | None = None
             ) -> list[Intent]:
    """One record per account, the most recent, newest first."""
    return sorted(
        (items[0] for items in for_targeting(directory, today).values()),
        key=lambda r: r.last_visit or r.source_date,
        reverse=True,
    )
