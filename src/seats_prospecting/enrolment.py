"""Enrolment and enrichment as plain functions, callable without an agent run.

Extracted 9 September 2026 so the queue runner can enrol without inventing a
RunContextWrapper. The agent tool in tools/apollo_enrol.py keeps its own
docstring and its own refusals and now calls these.

The rules travel with the functions, not with the caller:

``status: "paused"`` is set here and nowhere else.
    There is no status argument. A caller cannot enrol anyone active.

Enrichment is mandatory before enrolment, and the address never comes home.
    ``ensure_email`` spends a credit to get a verified address into Apollo and
    returns whether one exists. It returns no address, and nothing here writes
    one to disk. That is what kept emailer_messages_create off the table on
    4 September: holding addresses per recipient. Enrolment needs a contact id,
    so Apollo keeps the address and this system keeps the id.

Still no send.
    Nothing here activates, approves, or calls send_now, and no argument can
    make it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx

from .apollo_oauth import OAuthFailed, OAuthNotConfigured, bearer_headers, current_token

APOLLO_API = "https://api.apollo.io/api/v1"


class EnrolRefused(Exception):
    """Raised rather than enrolling. The message is what a person should read."""


@dataclass(frozen=True)
class Enriched:
    contact_id: str
    has_email: bool
    credit_spent: bool
    detail: str


def enrolment_payload(sequence_id: str, contact_id: str, mailbox_id: str) -> dict[str, Any]:
    """The exact POST body. Pure, so the paused flag is testable without a token."""
    return {
        "emailer_campaign_id": sequence_id,
        "contact_ids": [contact_id],
        "send_email_from_email_account_id": mailbox_id,
        # Owned here, never by a caller. Enrolment queues a draft; it does not
        # start a cadence.
        "status": "paused",
    }


def _headers() -> dict[str, str]:
    try:
        return bearer_headers(current_token())
    except (OAuthNotConfigured, OAuthFailed) as exc:
        raise EnrolRefused(
            f"No Apollo token, so nobody was enrolled ({exc}). Enrolment runs as Kib "
            "or not at all: an api key acts as the workspace admin, and a draft "
            "queued under someone else's name is worse than no draft."
        ) from exc


def _mailbox() -> str:
    mailbox = (os.environ.get("APOLLO_MAILBOX_ID") or "").strip()
    if not mailbox:
        raise EnrolRefused(
            "APOLLO_MAILBOX_ID is not set, so nobody was enrolled. It is the id of "
            "the mailbox the drafts would be sent from."
        )
    return mailbox


def live_sequence_refusal(live_sequences: list[str], override: str | None) -> str | None:
    """Why an already-sequenced person is not enrolled again.

    Wayne Young Jr., 4 September: already in a running sequence, and a second
    was built for him because nothing could see the first. Apollo would have
    sent both cadences to one inbox.

    A queue job passes no override, by Kib's decision of 9 September, so an
    unattended job refuses here rather than making double-sending reachable by
    a click.
    """
    if not live_sequences:
        return None
    if override:
        return None
    return (
        "NOBODY WAS ENROLLED. This person is already in "
        f"{len(live_sequences)} running sequence(s): {', '.join(live_sequences)}.\n"
        "Enrolling them again means Apollo sends both cadences to the same inbox. "
        "Take them out of the other sequence, or run it yourself with an override "
        "if you mean to run both."
    )


def ensure_email(contact_id: str, *, reveal_cap: int, spent: int = 0) -> Enriched:
    """Make sure Apollo holds a verified email for this contact.

    Returns whether one exists and whether a credit was spent. Never returns the
    address, and never writes it anywhere.

    The cap is checked and the slot reserved before the call, not after. Six
    concurrent reveals each read the count before any of them incremented it on
    4 September and a cap of one cost two credits.
    """
    if spent >= reveal_cap:
        raise EnrolRefused(
            f"Enrichment cap reached for this job ({spent}/{reveal_cap}), so no "
            "credit was spent and nobody was enrolled."
        )
    headers = _headers()
    with httpx.Client(timeout=45) as client:
        resp = client.post(
            f"{APOLLO_API}/people/match",
            headers=headers,
            content=json.dumps({"id": contact_id, "reveal_personal_emails": False}),
        )
    if resp.status_code >= 400:
        raise EnrolRefused(
            f"Apollo refused the enrichment ({resp.status_code}): {resp.text[:200]}. "
            "Nobody was enrolled."
        )
    person = (resp.json() or {}).get("person") or {}
    has_email = bool(person.get("email"))
    return Enriched(
        contact_id=contact_id,
        has_email=has_email,
        credit_spent=True,
        detail=(
            "Apollo holds a verified email for this contact."
            if has_email
            else "Apollo returned no email for this contact, so nobody was enrolled."
        ),
    )


def enrol(sequence_id: str, contact_id: str, who: str) -> str:
    """Put one contact into one paused sequence. One contact per call, by design.

    A sequence is one cadence. Enrolling several people in one sequence sends
    each of them every step in it, which is how five messages to five people
    become twenty-five wrong emails.
    """
    payload = enrolment_payload(sequence_id, contact_id, _mailbox())
    with httpx.Client(timeout=45) as client:
        resp = client.post(
            f"{APOLLO_API}/emailer_campaigns/{sequence_id}/add_contact_ids",
            headers=_headers(),
            content=json.dumps(payload),
        )
    if resp.status_code >= 400:
        raise EnrolRefused(
            f"Apollo refused the enrolment ({resp.status_code}): {resp.text[:300]}. "
            "Nobody was added. Do not retry with different arguments."
        )
    body = resp.json()
    skipped = body.get("skipped_contact_ids") or {}
    added = len(body.get("contacts") or [])
    if skipped or not added:
        raise EnrolRefused(
            f"Apollo accepted the call but enrolled {added} contacts and skipped "
            f"{skipped or 'none'}. Reporting this rather than a success."
        )
    return (
        f"{who} is enrolled in sequence {sequence_id}, paused. Nothing was sent, the "
        "sequence is not active, and no step in it can send itself."
    )
