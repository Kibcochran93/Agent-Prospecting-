r"""Turn a reviewed artifact into Apollo drafts. One code path, tested.

Phase 0 of the architecture rework, and the reason it comes before any new
agent. On 3 and 4 September the drafts were written by scripts typed into a
session, each one splitting the artifact with its own regular expression. Two of
those scripts shipped defects into a live Apollo sequence:

* a message block ended only at the next institution heading, so the last draft
  in a batch carried the reviewer's differentiation check inside the email body,
  which in turn hid the sign-off from the stripper and signed it twice
* five people's messages went into one sequence, and a sequence is one cadence,
  so enrolling all five would have sent each of them every message

Neither was an agent fault. Both were a parser nobody tested. So the parsing,
the grouping, the naming and the payloads live here, with the same
``parse_messages`` the mechanical checker uses, and the scripts are gone.

    seats-drafts plan   smoke-test/batch-1/reviewer-in-2.txt ...   # no network
    seats-drafts write  smoke-test/batch-1/reviewer-in-2.txt ...
    seats-drafts extend smoke-test/batch-1/reviewer-in-*.txt

``plan`` is the habit worth keeping: it prints exactly what would be created,
per recipient, per touch, and touches nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from . import enrollment_queue
from .apollo_oauth import OAuthFailed, OAuthNotConfigured, bearer_headers, current_token
from .differentiation import Message, institution_of, parse_messages, strip_signature
from .schemas import SequenceProposal, SequenceTouch
from .tools.apollo_write import (
    APOLLO_API,
    KIB_APOLLO_USER_ID,
    sequence_payload,
    sequence_title,
    update_payload,
)

# Default spacing for a four-touch cadence over two months, Kib's choice on
# 4 September: day 0, 14, 35 and 56, measured from enrollment.
DEFAULT_DAYS = {1: 0, 2: 14, 3: 35, 4: 56}

_TOUCH_IN_HEADING = re.compile(r"touch\s*(?P<touch>\d+)", re.IGNORECASE)
_DAY_IN_HEADING = re.compile(r"day\s*(?P<day>\d+)", re.IGNORECASE)


class DraftError(Exception):
    """The plan is wrong, so nothing is written."""


@dataclass
class DraftPlan:
    """Every draft for one person, in order. One person, one sequence."""

    person: str
    institution: str
    recipient: str
    touches: list[SequenceTouch] = field(default_factory=list)

    @property
    def name(self) -> str:
        return sequence_title(self.person, self.institution)

    def proposal(self) -> SequenceProposal:
        return SequenceProposal(
            sequence_name=self.name,
            contact_count=1,
            source_list=f"One named contact: {self.recipient}.",
            touches=self.touches,
        )


def person_of(message: Message) -> str:
    """The person a message is for.

    A first-touch heading is the institution alone, so the name comes from the
    recipient line. A follow-up heading carries it: "Tulsa Community College,
    Dewayne Dickens, touch 2, day 14".
    """
    parts = [part.strip() for part in message.institution.split(",")]
    if len(parts) > 1 and not _TOUCH_IN_HEADING.search(parts[1]):
        return parts[1]
    return message.recipient.split(",")[0].strip()


def _touch_number(message: Message, fallback: int) -> int:
    found = _TOUCH_IN_HEADING.search(message.institution)
    return int(found.group("touch")) if found else fallback


def _day_offset(message: Message, touch: int, days: dict[int, int]) -> int:
    found = _DAY_IN_HEADING.search(message.institution)
    if found:
        return int(found.group("day"))
    if touch not in days:
        raise DraftError(
            f"No day offset for touch {touch}. Put it in the heading as "
            f"'touch {touch}, day N' or pass --days."
        )
    return days[touch]


def plan_drafts(
    artifacts: list[str], days: dict[int, int] | None = None
) -> list[DraftPlan]:
    """Group every message across the artifacts into one plan per person.

    Artifacts are given in touch order: the first-touch batch, then the
    follow-ups. A heading that names its touch and day overrides the order.
    """
    days = days or DEFAULT_DAYS
    plans: dict[tuple[str, str], DraftPlan] = {}

    for position, artifact in enumerate(artifacts, start=1):
        for message in parse_messages(artifact):
            person = person_of(message)
            institution = institution_of(message)
            key = (institution.casefold(), person.casefold())
            plan = plans.get(key)
            if plan is None:
                plan = DraftPlan(person=person, institution=institution, recipient=message.recipient)
                plans[key] = plan
            touch = _touch_number(message, position)
            plan.touches.append(
                SequenceTouch(
                    step=touch,
                    day_offset=_day_offset(message, touch, days),
                    subject=message.subject,
                    body=strip_signature(message.body),
                    recipient_role=message.recipient,
                )
            )

    for plan in plans.values():
        plan.touches.sort(key=lambda t: t.step)
        steps = [t.step for t in plan.touches]
        if len(set(steps)) != len(steps):
            raise DraftError(f"{plan.name}: touch numbers repeat, {steps}")
        for touch in plan.touches:
            if "differentiation" in touch.body.casefold():
                raise DraftError(
                    f"{plan.name}: touch {touch.step} carries the differentiation check in "
                    "its body. The artifact was parsed wrong; nothing was written."
                )

    if not plans:
        raise DraftError("No messages found in those artifacts.")
    return list(plans.values())


# --- Apollo ---------------------------------------------------------------


def _client(client: httpx.Client | None) -> tuple[httpx.Client, bool]:
    return (client, False) if client else (httpx.Client(timeout=90), True)


def _headers() -> dict[str, str]:
    try:
        return bearer_headers(current_token())
    except (OAuthNotConfigured, OAuthFailed) as exc:
        raise DraftError(
            f"No Apollo token, so nothing was written ({exc}). Writes run as Kib or "
            "not at all: an api key acts as the workspace's oldest admin."
        ) from exc


def write_drafts(plans: list[DraftPlan], client: httpx.Client | None = None) -> list[dict]:
    """Create one paused sequence per person, then try to enroll them.

    Enrollment is added-on and best-effort, added 15 September 2026 (Kib's
    call): a sequence with no confident contact match is still created and
    still counts as a success. Nothing here can make the sequence any less
    safe than it already is -- every step stays manual_email and the
    sequence stays paused regardless of whether enrollment finds anyone.
    """
    headers = _headers()
    http, owned = _client(client)
    results = []
    try:
        for plan in plans:
            payload = sequence_payload(plan.proposal())
            resp = http.post(f"{APOLLO_API}/sequences", headers=headers, content=json.dumps(payload))
            body = resp.json() if resp.status_code < 400 else {}
            campaign = body.get("emailer_campaign") or {}
            steps = body.get("emailer_steps") or campaign.get("emailer_steps") or []
            result = {
                "name": payload["name"],
                "status": resp.status_code,
                "id": campaign.get("id"),
                "steps": len(steps),
                "error": None if resp.status_code < 400 else resp.text[:200],
            }
            if resp.status_code < 400 and campaign.get("id"):
                result["enrollment"] = resolve_and_enroll(plan, campaign["id"], headers, http)
            results.append(result)
    finally:
        if owned:
            http.close()
    return results


def _org_matches(candidate_org: str, institution: str) -> bool:
    a, b = candidate_org.casefold().strip(), institution.casefold().strip()
    return bool(a) and bool(b) and (a in b or b in a)


def _find_saved_contact(person: str, institution: str, headers, http) -> tuple[dict | None, str]:
    """Free: Apollo's own contacts/search. Many targets already have an
    email on file here from the original list import -- no reveal needed."""
    resp = http.post(
        f"{APOLLO_API}/contacts/search", headers=headers,
        content=json.dumps({"q_keywords": person, "page": 1, "per_page": 10}),
    )
    if resp.status_code >= 400:
        return None, f"contacts/search failed ({resp.status_code})"
    contacts = resp.json().get("contacts", []) or []
    matches = [
        c for c in contacts
        if _org_matches((c.get("organization") or {}).get("name") or c.get("organization_name") or "", institution)
    ]
    if len(matches) == 1:
        return matches[0], "single match in Apollo's saved contacts"
    if len(matches) > 1:
        return None, f"{len(matches)} same-name saved contacts at {institution}, no confident single match"
    return None, ""


def _find_unsaved_person(person: str, institution: str, headers, http) -> tuple[dict | None, str]:
    """Free: Apollo's broader people database, for someone not yet a saved contact."""
    resp = http.post(
        f"{APOLLO_API}/mixed_people/api_search", headers=headers,
        content=json.dumps({"q_keywords": f"{person} {institution}", "page": 1, "per_page": 5}),
    )
    if resp.status_code >= 400:
        return None, f"no saved contact, and mixed_people search failed ({resp.status_code})"
    people = resp.json().get("people", []) or []
    matches = [p for p in people if _org_matches((p.get("organization") or {}).get("name") or "", institution)]
    if len(matches) == 1:
        return matches[0], "single match in Apollo's broader database, needs a reveal"
    if len(matches) > 1:
        return None, f"{len(matches)} candidates at {institution} in Apollo's broader database, no confident single match"
    return None, f"no match for {person!r} at {institution!r} anywhere in Apollo"


def _reveal_email(person_id: str, headers, http) -> tuple[str | None, str]:
    """Spends one enrollment-reveal credit. Caller must reserve first."""
    resp = http.post(
        f"{APOLLO_API}/people/match", headers=headers,
        content=json.dumps({"id": person_id, "reveal_personal_emails": True, "reveal_phone_number": False}),
    )
    if resp.status_code >= 400:
        return None, f"reveal failed ({resp.status_code})"
    email = (resp.json().get("person") or {}).get("email")
    if not email:
        return None, "Apollo revealed no email for this person"
    return email, "revealed"


_mailbox_cache: str | None = None


def _my_mailbox_id(headers, http) -> str | None:
    """Kib's own sending mailbox id, cached for the life of this process.

    Matched by user_id, not the 'default' flag: Apollo marks every
    teammate's own mailbox default:true (a per-user default, not one
    workspace-wide default), confirmed against Kib's account 15 September.
    """
    global _mailbox_cache
    if _mailbox_cache:
        return _mailbox_cache
    resp = http.get(f"{APOLLO_API}/email_accounts", headers=headers)
    if resp.status_code >= 400:
        return None
    for account in resp.json().get("email_accounts", []) or []:
        if account.get("user_id") == KIB_APOLLO_USER_ID:
            _mailbox_cache = account.get("id")
            return _mailbox_cache
    return None


def resolve_and_enroll(plan: DraftPlan, campaign_id: str, headers, http) -> dict:
    """Find this plan's recipient in Apollo and enroll them, paused.

    Never guesses. Zero matches or more than one candidate both end the
    same way: the sequence stays exactly as write_drafts already left it,
    empty and paused, and this returns why. The only paths that end in an
    actual enroll call are a single confident match with an email already
    on file (free), or a single confident match resolved through a
    reveal that the daily cap allowed.
    """
    contact, detail = _find_saved_contact(plan.person, plan.institution, headers, http)
    email = contact.get("email") if contact else None

    if contact and not email:
        # A saved contact exists but Apollo has no email on file for them --
        # same reveal path as someone not saved at all.
        person_id = contact.get("id")
    elif not contact and not detail:
        # No saved-contact result at all (not "ambiguous", genuinely none) --
        # try the broader database.
        candidate, detail = _find_unsaved_person(plan.person, plan.institution, headers, http)
        person_id = candidate.get("id") if candidate else None
    else:
        person_id = None

    if not email and person_id:
        try:
            reserved = enrollment_queue.reserve(plan.person, plan.institution)
        except enrollment_queue.EnrollmentRefused as exc:
            return {"enrolled": False, "detail": str(exc)}
        email, reveal_detail = _reveal_email(person_id, headers, http)
        enrollment_queue.finish(reserved, matched=bool(email), email=email, detail=reveal_detail)
        if not email:
            return {"enrolled": False, "detail": reveal_detail}

    if not email:
        return {"enrolled": False, "detail": detail or "no confident match"}

    mailbox_id = _my_mailbox_id(headers, http)
    if not mailbox_id:
        return {"enrolled": False, "detail": "could not resolve Kib's own Apollo mailbox"}

    name_parts = plan.person.split()
    create_resp = http.post(
        f"{APOLLO_API}/contacts", headers=headers,
        content=json.dumps({
            "first_name": name_parts[0] if name_parts else plan.person,
            "last_name": " ".join(name_parts[1:]) if len(name_parts) > 1 else None,
            "email": email,
            "organization_name": plan.institution,
        }),
    )
    if create_resp.status_code >= 400:
        return {"enrolled": False, "detail": f"contact create/update failed ({create_resp.status_code})"}
    contact_id = (create_resp.json().get("contact") or {}).get("id")
    if not contact_id:
        return {"enrolled": False, "detail": "Apollo did not return a contact id"}

    add_resp = http.post(
        f"{APOLLO_API}/emailer_campaigns/{campaign_id}/add_contact_ids", headers=headers,
        content=json.dumps({
            "contact_ids": [contact_id],
            "send_email_from_email_account_id": mailbox_id,
            "status": "paused",
        }),
    )
    if add_resp.status_code >= 400:
        return {"enrolled": False, "detail": f"enroll call failed ({add_resp.status_code})"}
    return {"enrolled": True, "email": email, "contact_id": contact_id}


def resolve_sequences(names: list[str], client: httpx.Client | None = None) -> dict[str, dict]:
    """Find sequences by exact name, with their step and touch ids."""
    headers = _headers()
    http, owned = _client(client)
    found: dict[str, dict] = {}
    try:
        page = 1
        while page <= 10:
            resp = http.post(
                f"{APOLLO_API}/emailer_campaigns/search",
                headers=headers,
                content=json.dumps({"per_page": 100, "page": page}),
            )
            if resp.status_code >= 400:
                raise DraftError(f"Apollo refused the search ({resp.status_code}): {resp.text[:200]}")
            campaigns = resp.json().get("emailer_campaigns") or []
            if not campaigns:
                break
            for campaign in campaigns:
                if campaign.get("name") in names:
                    steps = sorted(
                        campaign.get("emailer_steps") or [], key=lambda s: s.get("position", 0)
                    )
                    found[campaign["name"]] = {
                        "id": campaign["id"],
                        "existing": [
                            (s["id"], (s.get("emailer_touches") or [{}])[0].get("id", ""))
                            for s in steps
                        ],
                    }
            page += 1
    finally:
        if owned:
            http.close()
    return found


def extend_drafts(plans: list[DraftPlan], client: httpx.Client | None = None) -> list[dict]:
    """Add the later touches to sequences that already exist.

    Apollo's update replaces the step list rather than appending, so every touch
    travels and the existing step and touch ids travel with it. Without the ids
    the update is a 422; with the wrong ids it would rebuild the step an enrolled
    contact is attached to.
    """
    headers = _headers()
    http, owned = _client(client)
    results = []
    try:
        live = resolve_sequences([plan.name for plan in plans], client=http)
        for plan in plans:
            target = live.get(plan.name)
            if not target:
                results.append(
                    {"name": plan.name, "status": None, "error": "no sequence with that name"}
                )
                continue
            payload = update_payload(plan.proposal(), existing=target["existing"])
            resp = http.put(
                f"{APOLLO_API}/sequences/{target['id']}",
                headers=headers,
                content=json.dumps(payload),
            )
            body = resp.json() if resp.status_code < 400 else {}
            campaign = body.get("emailer_campaign") or {}
            steps = body.get("emailer_steps") or campaign.get("emailer_steps") or []
            results.append(
                {
                    "name": plan.name,
                    "status": resp.status_code,
                    "id": target["id"],
                    "steps": len(steps),
                    "error": None if resp.status_code < 400 else resp.text[:200],
                }
            )
    finally:
        if owned:
            http.close()
    return results


# --- command line ---------------------------------------------------------


def _print_plan(plans: list[DraftPlan]) -> None:
    for plan in plans:
        print(f"\n{plan.name}")
        print(f"  to: {plan.recipient}")
        for touch in plan.touches:
            first = touch.body.splitlines()[0] if touch.body else ""
            print(f"  touch {touch.step}, day {touch.day_offset}: {touch.subject}")
            print(f"      {first[:88]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="seats-drafts",
        description="Plan, write or extend Apollo drafts from reviewed artifacts.",
    )
    parser.add_argument("command", choices=("plan", "write", "extend"))
    parser.add_argument("artifacts", nargs="+", help="Extracted artifacts, in touch order.")
    parser.add_argument(
        "--days",
        help="Touch to day offsets, e.g. 1:0,2:14,3:35,4:56. Headings override this.",
    )
    args = parser.parse_args(argv)

    days = dict(DEFAULT_DAYS)
    if args.days:
        days = {int(k): int(v) for k, v in (pair.split(":") for pair in args.days.split(","))}

    try:
        plans = plan_drafts(
            [Path(p).read_text(encoding="utf-8", errors="replace") for p in args.artifacts],
            days=days,
        )
    except DraftError as exc:
        print(str(exc), file=sys.stderr)
        return 3

    print(f"{len(plans)} sequences, {sum(len(p.touches) for p in plans)} touches")
    _print_plan(plans)

    if args.command == "plan":
        print("\nplan only, nothing written")
        return 0

    try:
        results = write_drafts(plans) if args.command == "write" else extend_drafts(plans)
    except DraftError as exc:
        print(str(exc), file=sys.stderr)
        return 3

    print()
    failed = 0
    for result in results:
        print(f"{result.get('status')} | {result['name']} | steps {result.get('steps')}")
        if result.get("error"):
            failed += 1
            print(f"      {result['error']}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
