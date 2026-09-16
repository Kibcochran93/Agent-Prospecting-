"""Apollo read: organizations, role maps, and named-contact reveals.

Three tools, in increasing cost order. The split is deliberate, because two of
the three calls are free and the third spends a credit each time.

- ``apollo_find_org``    free. Does this institution exist, and at what size.
- ``apollo_find_roles``  free. Which titles exist, with first names and an
                         obfuscated surname. Enough to map a buying group.
- ``apollo_reveal_person`` one credit per call. Full name and LinkedIn URL.

**What these tools never return: email addresses and phone numbers.** Not
because Apollo withholds them, but because no agent here can send anything, so
an inbox address has no use in an agent's context and every reason to stay out
of it. ``has_email`` and ``has_direct_phone`` come back as booleans so a
briefing can say Apollo holds contact details without carrying them. Kib takes
the actual address from Apollo's own interface at the point of sending.

The reveal is capped per run purely as a runaway guard, and every reveal is
written to the run audit so the spend is visible after the fact rather than
inferred from an invoice. The cap reserves its slot before the call rather than
counting after it, because a model that asks for six reveals in one turn gets
six concurrent calls and a check-then-spend guard lets all six through.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from agents import RunContextWrapper
from agents.decorators import tool

from ..context import DispatchContext

API = "https://api.apollo.io/api/v1"

# Runaway guard, not an approval gate. Raise or lower with APOLLO_REVEAL_CAP.
DEFAULT_REVEAL_CAP = 25

NOT_CONFIGURED = (
    "Apollo is unavailable on this run: no OAuth token could be used. Run "
    "`python -m seats_prospecting.apollo_oauth login`. Say so in your output and "
    "work from public and official sources only."
)


def _auth() -> dict[str, str] | None:
    """OAuth headers, or None when there is no usable token.

    Kib's call, 4 September 2026: the API key is gone. It was already blank in
    the environment, so every tool in this module had been silently returning
    "Apollo is unavailable" while looking like it was working. Removing the code
    path rather than leaving it as a fallback is the point: a fallback that
    changes who Apollo thinks you are is not a fallback, it is a second
    identity that appears under load.

    What the key bought was workspace-wide visibility, because Apollo runs an
    api-key request as the longest-standing admin. What it cost was writes
    landing in Miguel Pescador's account. Reads are now Kib's view, and where
    that view might be narrower the tool says so rather than reporting a clean
    result.
    """
    try:
        from ..apollo_oauth import bearer_headers, current_token

        return bearer_headers(current_token())
    except Exception:  # noqa: BLE001 - absent, expired, or a refused refresh
        return None


def _reveal_cap() -> int:
    try:
        return int(os.environ.get("APOLLO_REVEAL_CAP", DEFAULT_REVEAL_CAP))
    except ValueError:
        return DEFAULT_REVEAL_CAP


def _explain(status: int, body: str) -> str:
    if status in (401, 403):
        return (
            f"Apollo rejected the request ({status}). The key may be invalid or the plan "
            "may not include this endpoint. Report Apollo as unavailable and continue "
            "from public sources. Do not retry a different endpoint."
        )
    if status == 422:
        return (
            f"Apollo refused the query ({status}): {body[:200]}. Narrow or correct the "
            "filters rather than retrying the same call."
        )
    if status == 429:
        return "Apollo rate limited the request (429). Report as unavailable for this run."
    return f"Apollo returned {status}: {body[:250]}"


@tool
async def apollo_find_org(query: str) -> str:
    """Look up an institution in Apollo. Free, no credit spend.

    Args:
        query: Institution name or domain, for example "asumh.edu" or "Lyon College".
    """
    headers = _auth()
    if headers is None:
        return NOT_CONFIGURED

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{API}/mixed_companies/search",
            headers=headers,
            json={"q_organization_name": query, "page": 1, "per_page": 5},
        )

    if resp.status_code >= 400:
        return _explain(resp.status_code, resp.text)

    orgs = resp.json().get("organizations", []) or []
    if not orgs:
        return f"Apollo has no organization matching {query!r}."

    lines = [f"Apollo organizations matching {query!r}:"]
    for o in orgs:
        lines.append(
            f"- {o.get('name')} | domain {o.get('primary_domain') or 'unknown'} | "
            f"est. employees {o.get('estimated_num_employees') or 'unknown'} | "
            f"{o.get('city') or '?'}, {o.get('state') or '?'}"
        )
    lines.append(
        "Apollo is a commercial database, not the institution. Cite it as such and "
        "verify anything load-bearing against an official source."
    )
    return "\n".join(lines)


@tool
async def apollo_find_roles(domain: str, titles: list[str]) -> str:
    """Map which roles exist at an institution. Free, no credit spend.

    Returns each person's first name, an obfuscated surname, their title, and an
    Apollo id. Use this to map a buying group before deciding whose full name is
    worth a credit.

    Args:
        domain: The institution's email domain, for example "astate.edu".
        titles: Titles to look for, for example ["registrar", "director of financial aid"].
    """
    headers = _auth()
    if headers is None:
        return NOT_CONFIGURED

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{API}/mixed_people/api_search",
            headers=headers,
            json={
                "q_organization_domains_list": [domain],
                "person_titles": titles,
                "page": 1,
                "per_page": 25,
            },
        )

    if resp.status_code >= 400:
        return _explain(resp.status_code, resp.text)

    data = resp.json()
    people = data.get("people", []) or []
    if not people:
        return (
            f"Apollo lists nobody at {domain} under {titles}. That is an absence in a "
            "commercial database, not evidence the role does not exist."
        )

    lines = [f"Apollo lists {data.get('total_entries', len(people))} matching people at {domain}:"]
    for p in people:
        org = (p.get("organization") or {}).get("name") or "?"
        lines.append(
            f"- id {p.get('id')} | {p.get('first_name') or '?'} {p.get('last_name_obfuscated') or ''} | "
            f"{p.get('title') or 'no title'} | {org} | "
            f"has email: {p.get('has_email')} | has direct phone: {p.get('has_direct_phone')} | "
            f"record refreshed {(p.get('last_refreshed_at') or 'unknown')[:10]}"
        )
    lines.append(
        "Surnames are obfuscated until revealed. Call apollo_reveal_person with an id "
        "for a full name. Treat 'record refreshed' as the source date for anything you "
        "carry from here."
    )
    return "\n".join(lines)


def _reserve_reveal(ctx: RunContextWrapper[DispatchContext]) -> int | None:
    """Take a slot under the cap, or None if there is none left.

    Check and increment happen together, with no await between them. That is the
    whole point. The first version checked the count, awaited the HTTP call, and
    incremented afterwards, so when the model emitted six reveals in one turn
    they all read zero before any of them landed and every one of them spent. A
    live run with the cap set to 1 spent two credits and logged "reveal 2/1",
    which is how this was found.
    """
    if ctx.context.apollo_reveals >= _reveal_cap():
        return None
    ctx.context.apollo_reveals += 1
    return ctx.context.apollo_reveals


def _release_reveal(ctx: RunContextWrapper[DispatchContext]) -> None:
    """Give the slot back when Apollo charged nothing."""
    ctx.context.apollo_reveals = max(0, ctx.context.apollo_reveals - 1)


async def _reveal_call(headers: dict[str, str], person_id: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30) as client:
        return await client.post(
            f"{API}/people/match",
            headers=headers,
            json={
                "id": person_id,
                "reveal_personal_emails": False,
                "reveal_phone_number": False,
            },
        )


async def _do_reveal(ctx: RunContextWrapper[DispatchContext], person_id: str) -> str:
    """The reveal, with the reservation around it. Extracted so it can be tested."""
    headers = _auth()
    if headers is None:
        return NOT_CONFIGURED

    cap = _reveal_cap()
    reserved = _reserve_reveal(ctx)
    if reserved is None:
        return (
            f"Reveal cap of {cap} reached for this run, so nothing was spent. This is a "
            "runaway guard, not a judgement about the target. Report which ids you still "
            "wanted and stop."
        )

    try:
        resp = await _reveal_call(headers, person_id)
    except Exception:
        _release_reveal(ctx)
        raise

    if resp.status_code >= 400:
        _release_reveal(ctx)
        ctx.context.audit.append(f"apollo reveal {person_id} -> {resp.status_code}, no credit")
        return _explain(resp.status_code, resp.text)

    person = resp.json().get("person") or {}
    if not person:
        _release_reveal(ctx)
        ctx.context.audit.append(f"apollo reveal {person_id} -> no match")
        return f"Apollo returned no person for id {person_id}."

    ctx.context.audit.append(
        f"apollo reveal {reserved}/{cap}: {person_id} -> "
        f"{person.get('name')} ({person.get('title')})"
    )

    org = (person.get("organization") or {}).get("name") or "?"
    return (
        f"{person.get('name') or 'no name returned'} | {person.get('title') or 'no title'} | {org}\n"
        f"LinkedIn: {person.get('linkedin_url') or 'none on record'}\n"
        f"Apollo holds an email: {bool(person.get('email'))} | "
        f"record refreshed {(person.get('updated_at') or person.get('last_refreshed_at') or 'unknown')[:10]}\n"
        "Cite this as Apollo, a commercial database, with that refresh date. Never state "
        "or imply direct LinkedIn access. Contact details are not returned by design; Kib "
        "takes those from Apollo when he sends."
    )


@tool
async def apollo_reveal_person(
    ctx: RunContextWrapper[DispatchContext], person_id: str
) -> str:
    """Reveal one person's full name and LinkedIn URL. Spends one Apollo credit.

    Email addresses and phone numbers are deliberately not returned. No agent
    here can send anything, so it reports only whether Apollo holds them.

    Args:
        person_id: The Apollo id from apollo_find_roles.
    """
    return await _do_reveal(ctx, person_id)


# --- sequence membership, added for Account Context, 4 September 2026 --------
#
# Wayne Young Jr. was enrolled in 6a8899a4482aef0010cb668a with activity the
# previous day, and a second sequence was built for him anyway. Nothing in the
# system could have known: no tool anywhere answered "is this person already in
# something". This is that tool. It is free and it is read only.
#
# One identity answers this call: Kib, through OAuth. The api-key path was
# removed on 4 September 2026 along with the key itself.
#
# The coverage note travels with every result, because a verdict built on a
# narrower view than it thinks it has is the exact failure this agent exists to
# prevent, and it would be a poor joke to reintroduce it here.

_SEQUENCE_STATES = ("active", "paused", "finished", "not_sent", "bounced", "failed")

# Every Apollo read now runs as Kib. The caveat that goes with that is smaller
# than it was when this module could also speak as a workspace admin, and it is
# smaller than I first wrote it, because the first live run disproved the strong
# version: on 4 September, Kib's own token returned Wayne Young Jr. enrolled in
# a sequence sending from adelarosa@seatsone.com, which is Agustin's mailbox.
# Contact-level membership does surface across colleagues.
#
# What one observation does not establish is that it surfaces for every
# colleague and every sequence state, so absence still gets a hedge and presence
# does not. Delete this caveat when someone checks it properly, not before.
_NARROW_VIEW = (
    "COVERAGE NOTE: this read runs as Kib's own Apollo identity. A colleague's "
    "sequence has been seen through it, so presence is trustworthy; whether every "
    "colleague's sequence surfaces has not been established, so absence is the "
    "weaker half. Report an empty result as unconfirmed rather than clean."
)


def _read_identity() -> tuple[dict[str, str] | None, str, str]:
    """Headers, a label for the query log, and the standing coverage note."""
    headers = _auth()
    if headers is None:
        return None, "nothing", ""
    return headers, "Kib's OAuth token", _NARROW_VIEW


def _contact_search_body(name: str, domain: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {"q_keywords": name, "page": 1, "per_page": 10}
    if domain:
        body["q_organization_domains_list"] = [domain]
    return body


@tool
async def apollo_contact_status(name: str, domain: str | None = None) -> str:
    """Check whether Apollo already holds this person and what we have sent them.

    Free, no credit spend: this reads contact records the workspace already
    owns rather than revealing anything new. Run it before proposing outreach
    to a named person.

    Reports sequence membership and the mailbox each sequence sends from. A
    sequence running out of a colleague's mailbox is still known_active, the
    same as one running out of yours; ownership of the mailbox does not change
    the status, only the fact that it is worth noting before you write to Kib.

    Args:
        name: Person's name, for example "Wayne Young Jr.".
        domain: Their institution's domain, to disambiguate. Optional, and
            dropped automatically if it turns up nothing.
    """
    headers, identity, caveat = _read_identity()
    if headers is None:
        return (
            "Apollo is unavailable on this run: no OAuth token could be used. "
            "Record that in your verdict as an unavailable system. Sequence "
            "membership is unchecked, so this account cannot be called quiet on "
            "Apollo evidence. Fix with `python -m seats_prospecting.apollo_oauth "
            "login`."
        )

    tried: list[str] = []
    contacts: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=30) as client:
        # The domain filter narrows, and a narrowed search that finds nothing
        # looks exactly like a person Apollo has never heard of. On 4 September
        # a lookup for Wayne Young Jr. filtered to the institution the caller
        # believed he worked at returned no record, while Apollo held him under
        # another employer, in a live sequence, sending from a colleague's
        # mailbox. So the filter is an optimisation, never the last word: if it
        # empties the result, the name is asked on its own.
        for use_domain in ([domain, None] if domain else [None]):
            resp = await client.post(
                f"{API}/contacts/search",
                headers=headers,
                json=_contact_search_body(name, use_domain),
            )
            label = f"contacts/search q={name!r}" + (
                f" domain={use_domain}" if use_domain else " (name only)"
            )
            tried.append(label)
            if resp.status_code >= 400:
                return _explain(resp.status_code, resp.text)
            contacts = resp.json().get("contacts", []) or []
            if contacts:
                break

    query = "; ".join(tried) + f" as {identity}"
    if not contacts:
        lines = [
            f"Apollo holds no contact record for {name!r}.",
            f"Searched: {query}",
            "Record those queries in your verdict. No record found and no search "
            "run look the same in a summary and mean opposite things.",
        ]
        if caveat:
            lines.append(caveat)
        return "\n".join(lines)

    lines = [f"Apollo contacts matching {name!r} ({len(contacts)}):", f"Searched: {query}"]
    live: list[str] = []
    mailboxes: set[str] = set()

    for c in contacts:
        org = (c.get("organization") or {}).get("name") or c.get("organization_name")
        lines.append(
            f"- id {c.get('id')} | {c.get('name') or 'unnamed'} | "
            f"{c.get('title') or 'no title'} | {org or 'no organization'}"
        )
        # Apollo returns membership under a few names depending on the record's
        # age. Read all of them rather than the one this workspace happens to
        # use today: a missed enrolment is the failure this tool exists to
        # prevent, and a false negative is silent.
        statuses = c.get("contact_campaign_statuses") or []
        bare_ids = c.get("emailer_campaign_ids") or []
        if not statuses and not bare_ids:
            lines.append("  no sequence membership on this record")
            continue

        for entry in statuses:
            seq_id = str(entry.get("emailer_campaign_id") or entry.get("id") or "unknown")
            status = entry.get("status") or "unknown"
            mailbox = entry.get("send_email_from_email_address") or "unknown mailbox"
            lines.append(
                f"  sequence {seq_id} | status {status} | "
                f"added {entry.get('added_at') or 'unknown'} | "
                f"sends from {mailbox} | "
                f"finished {entry.get('finished_at') or 'no'}"
            )
            if status in ("active", "not_sent", "paused"):
                live.append(seq_id)
                mailboxes.add(mailbox)

        seen = {str(e.get("emailer_campaign_id") or e.get("id")) for e in statuses}
        for raw in bare_ids:
            if str(raw) not in seen:
                live.append(str(raw))
                lines.append(f"  sequence {raw} | status not reported on this record")

    if live:
        lines.append(
            f"LIVE OR PENDING SEQUENCES: {', '.join(sorted(set(live)))}. "
            "This person is already in a running sequence. Do not build a second "
            "one: Apollo will send both."
        )
        if mailboxes:
            lines.append(
                f"Sending mailbox(es): {', '.join(sorted(mailboxes))}. Still "
                "known_active regardless of whose mailbox it sends from; note the "
                "address for context, it does not change the status."
            )
    else:
        lines.append("No live or pending sequence membership found on these records.")

    if caveat:
        lines.append(caveat)
    return "\n".join(lines)
