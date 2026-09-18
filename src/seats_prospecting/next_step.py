"""What an approved account is waiting on. A read, never an action.

Kib's ask, 9 September: approval should move an account to the next step. This
module answers what the next step is. It runs nothing.

The stages, and who each one waits on:

    ASK_OWNER    Kib, to go and ask the account owner
    INBOUND      the Account Context check, on an institution that came to us
    UNDECIDED    a person, to approve or decline
    DECLINED     nobody
    COPY         the Campaign Builder, no copy exists yet
    SEQUENCE     seats-drafts write, copy exists but no Apollo sequence
    ENROL        a person or a tool, sequence exists with nobody in it
    SEND         **a person, always.** Touch 1 is a manual email in Apollo
    WORKING      a person, on the later touches when they come due
    UNKNOWN      nothing, because a source could not be read

Two rules this module will not bend:

1. **SEND is never automatable.** Nothing in this system can send, and that is a
   missing capability rather than a policy. So SEND is reported as Kib's, and
   ``automatable`` is False on it. A caller that queues work reads that flag.
2. **An unreadable source produces UNKNOWN, not a stage.** Apollo's sequence
   endpoint exposes no enrolment count and the tasks endpoint returns 403, so
   "nobody is enrolled" is not a thing this code may conclude. Delivery proves
   enrolment; the absence of delivery proves nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: A named person from this institution has been reading seatsone.com and
#: nobody has researched them. The Apollo website-visitor pull on 10 September
#: found four such institutions, none of them in the pipeline, while the queue
#: held eight accounts whose only evidence of interest was a strategic plan page.
INBOUND = "INBOUND"
UNDECIDED = "UNDECIDED"
#: A colleague owns the account and nobody has recorded their answer. Miguel and
#: Agustin have no licence for Notion and no login to the board, and are not
#: expected to get either, so this will never resolve itself: it is a
#: conversation Kib owes, not a queue someone else is sitting in. ADR 0002.
ASK_OWNER = "ASK_OWNER"
DECLINED = "DECLINED"
COPY = "COPY"
SEQUENCE = "SEQUENCE"
ENROL = "ENROL"
SEND = "SEND"
WORKING = "WORKING"
UNKNOWN = "UNKNOWN"

#: Stages a tool could take on. SEND is deliberately absent.
AUTOMATABLE = frozenset({INBOUND, COPY, SEQUENCE})


@dataclass(frozen=True)
class Step:
    stage: str
    waiting_on: str
    blocker: str
    command: str = ""
    evidence: str = ""

    @property
    def automatable(self) -> bool:
        return self.stage in AUTOMATABLE


@dataclass
class Facts:
    """Everything known about one account, gathered by the caller.

    ``apollo_read`` is required and separate from ``sequences`` being empty. An
    empty list means Apollo said there are none; False means Apollo was not
    asked or did not answer, and those are different answers.
    """

    account: str
    decision: str | None = None
    owner_review: str | None = None
    #: Live website-visitor intent, from context-digest. Never used in copy.
    inbound_person: str = ""
    inbound_last_visit: str = ""
    inbound_visits: int = 0
    has_records: bool = True
    account_owner: str = ""
    copy_files: list[str] = field(default_factory=list)
    sequences: list[dict[str, Any]] = field(default_factory=list)
    apollo_read: bool = True
    tasks_read: bool = False


#: The delivered count arrives under one name from Apollo and another from the
#: board's normalizer. Reading only one of them is how three accounts with touch
#: 1 delivered were reported as having nobody enrolled: the key was absent,
#: ``.get`` returned None, and the sum read zero. A missing key is now unknown.
DELIVERED_KEYS = ("delivered", "unique_delivered")


def delivered_count(sequences: list[dict[str, Any]]) -> int | None:
    """Total delivered, or None when any sequence does not carry the figure.

    None is not zero. Zero means Apollo said nothing has been delivered; None
    means this code was handed a shape it cannot read, and it must say so
    rather than imply an empty sequence.
    """
    total = 0
    for seq in sequences:
        for key in DELIVERED_KEYS:
            if key in seq and seq[key] is not None:
                total += int(seq[key])
                break
        else:
            return None
    return total


def stage_for(facts: Facts) -> Step:
    # Inbound comes first, before the decision checks, because an institution
    # that came to us has no decision to have made yet and would otherwise fall
    # through to UNDECIDED and look like something Kib had failed to action.
    # An active Apollo sequence is the strongest evidence an account has
    # already been handled, regardless of whether the local research record
    # for it still exists -- found live 18 September: North Dakota's INBOUND
    # signal kept firing for a contact who already had a running sequence,
    # because the local briefing record for her had been deleted separately
    # and this check never looked at facts.sequences at all, even though it
    # was already available at this point. Same category of gap as ADR 0006.
    if (
        facts.inbound_person
        and not facts.has_records
        and facts.decision is None
        and not facts.sequences
    ):
        visits = f"{facts.inbound_visits} visits" if facts.inbound_visits else "visits"
        return Step(
            INBOUND,
            "Account Context",
            f"{facts.inbound_person} has been reading the site ({visits}, last "
            f"{facts.inbound_last_visit}) and nothing has been researched. Run "
            "the context check.",
            command="live_run.py context",
            evidence=(
                "Apollo website visitors, person level. Internal only: never "
                "quote it and never imply their browsing was observed."
            ),
        )
    if facts.decision == "Declined":
        return Step(DECLINED, "nobody", "Declined. Nothing further happens.")
    if facts.decision != "Approved":
        if facts.owner_review == "Declined":
            return Step(
                DECLINED,
                "nobody",
                f"{facts.account_owner or 'The account owner'} declined. Nothing "
                "further happens unless Kib overrules it.",
            )
        held = facts.decision or "no decision recorded"
        colleague = facts.account_owner.strip()
        if not facts.owner_review and colleague and colleague.lower() not in {
            "unknown", "kib cochran"
        }:
            return Step(
                ASK_OWNER,
                f"Kib, to ask {colleague}",
                f"{colleague} owns this account and nothing records their answer. "
                "They have no licence for the ledger and no login here, so this "
                "will not resolve itself: ask them, then record it.",
            )
        cleared = (
            f" {colleague} has cleared it." if facts.owner_review == "Approved" else ""
        )
        return Step(
            UNDECIDED,
            "Kib",
            f"Not approved ({held}).{cleared} Approve or decline on the board.",
        )

    if not facts.copy_files:
        return Step(
            COPY,
            "Campaign Builder",
            "Approved, and no copy exists for this account.",
            command="live_run.py campaign",
            evidence="no file in lists/ names this institution",
        )

    if not facts.apollo_read:
        return Step(
            UNKNOWN,
            "nothing",
            "Copy exists, but Apollo could not be read, so what happened to it "
            "is unknown. Not reported as missing.",
            evidence="; ".join(facts.copy_files),
        )

    if not facts.sequences:
        return Step(
            SEQUENCE,
            "seats-drafts",
            "Copy exists and no Apollo sequence carries this account.",
            command="seats-drafts plan, then seats-drafts write",
            evidence="; ".join(facts.copy_files),
        )

    ids = ", ".join(str(s.get("id")) for s in facts.sequences)
    delivered = delivered_count(facts.sequences)

    if delivered is None:
        return Step(
            UNKNOWN,
            "nothing",
            "A sequence exists but the delivered count could not be read from "
            "the data handed to this check, so whether touch 1 went out is "
            "unknown. Not reported as zero.",
            evidence=f"sequence {ids}",
        )

    if delivered == 0:
        return Step(
            ENROL,
            "Kib, and enrolment is not readable",
            "A sequence exists and nothing has been delivered. Whether a contact "
            "is enrolled cannot be read: the sequence endpoint exposes no "
            "enrolment count and the tasks endpoint returns 403. Open the "
            "sequence in Apollo and look.",
            evidence=f"sequence {ids}, 0 delivered",
        )

    overdue = sum(int(s.get("overdue_manual_tasks_count") or 0) for s in facts.sequences)
    steps = max(int(s.get("num_steps") or 0) for s in facts.sequences)

    if overdue:
        return Step(
            SEND,
            "Kib",
            f"{overdue} manual task(s) overdue in Apollo. Send or reschedule them.",
            evidence=f"sequence {ids}, {delivered} delivered",
        )

    if not facts.tasks_read:
        return Step(
            WORKING,
            "Kib, when the next touch comes due",
            f"Touch 1 delivered. {steps} steps exist. What is queued cannot be "
            "read, because the Apollo token has no task scope.",
            evidence=f"sequence {ids}, {delivered} delivered",
        )

    return Step(
        WORKING,
        "Kib, when the next touch comes due",
        f"Touch 1 delivered, {steps} steps, nothing overdue.",
        evidence=f"sequence {ids}, {delivered} delivered",
    )


@dataclass(frozen=True)
class PendingContact:
    """One briefing whose own content has not itself been decided, ADR 0005.

    Independent of the account's stage_for() stage: an Approved account
    already mid-SEQUENCE can still owe a fresh decision on a brand-new
    contact nobody has looked at yet. This is additive to stage_for(), not a
    replacement for any branch of it.
    """

    person: str
    title: str
    artifact_sha256: str
    file: str = ""
    written_at: str = ""


def undecided_briefings(
    briefings: list[dict[str, Any]],
    decided_hashes: frozenset[str],
) -> list[PendingContact]:
    """Which of this account's briefing records have no matching decision
    yet, newest first.

    A briefing with no artifact_sha256 at all predates ADR 0005 and is
    skipped rather than treated as either decided or newly pending -- there
    is nothing to match a decision against. See board.collect_records for
    where the hash comes from.
    """
    out = [
        PendingContact(
            person=str(b.get("person") or "") or "(unnamed contact)",
            title=str(b.get("title") or ""),
            artifact_sha256=b["artifact_sha256"],
            file=str(b.get("file") or ""),
            written_at=str(b.get("written_at") or ""),
        )
        for b in briefings
        if b.get("artifact_sha256") and b["artifact_sha256"] not in decided_hashes
    ]
    out.sort(key=lambda p: p.written_at, reverse=True)
    return out
