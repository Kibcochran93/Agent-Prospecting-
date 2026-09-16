"""Pipeline state for the Director. Read only, by construction.

The design gives HubSpot to the Director alone: "neither worker touches it, so
this is the only place pipeline state enters the system." That rule is enforced
in ``connectors.py`` by never handing these tools to a worker.

Why a local tool rather than the hosted HubSpot connector: the same reason the
ledger write is local. A connector's scope claim is only as good as the
``allowed_tools`` list, and the Notion work showed that a scope you cannot
verify is a control you do not have. This module contains no create, update,
delete, or association call. There is no write path to disable.

What it deliberately does not read: contact records. The Director needs to know
whether an account is already in play, not who we have emailed. Company and
deal level only, which also keeps personal data out of the planning context.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx
from agents.decorators import tool

API = "https://api.hubapi.com"

# Company properties worth carrying into a plan. Anything not listed never
# leaves HubSpot, which keeps the planning context small and predictable.
COMPANY_PROPS = (
    "name",
    "domain",
    "hubspot_owner_id",
    "lifecyclestage",
    "type",
    "industry",
    "state",
    "country",
    "createdate",
    "notes_last_updated",
    "num_associated_deals",
)

DEAL_PROPS = (
    "dealname",
    "dealstage",
    "pipeline",
    "closedate",
    "createdate",
    "hs_lastmodifieddate",
    "hubspot_owner_id",
    "dealtype",
    "hs_is_closed",
    "hs_is_closed_won",
)

NOT_CONFIGURED = (
    "HUBSPOT_ACCESS_TOKEN is not set, so pipeline state is unavailable on this run. "
    "Say so in the plan: report open ledger items as unknown rather than zero, and "
    "state that prior relationship and active-opportunity status are unverified."
)


def _token() -> str | None:
    return (os.environ.get("HUBSPOT_ACCESS_TOKEN") or "").strip() or None


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# --- name variants ---------------------------------------------------------
# Found the hard way: searching "ASU Mountain Home" and "Mountain Home" both
# returned nothing, and the record was there all along under the name "ASUMH".
# HubSpot's free-text company search matches tokens by prefix, so a record whose
# name shares no leading token with the query is invisible. Institutions are
# recorded under initialisms, hyphenated campus names, and former names, so one
# query is not a search. A false "not in HubSpot" is the expensive answer here:
# it is what put "no prior outreach" into three work orders that were wrong.

_PUNCT = re.compile(r"[^\w\s.-]")
_WS = re.compile(r"\s+")

# Words that carry no distinguishing weight in a US higher education name.
_STOP = {
    "the", "of", "at", "and", "for", "a", "an",
    "university", "college", "colleges", "school", "schools", "institute",
    "community", "technical", "state", "campus", "system", "district",
}

_EXPANSIONS = {
    "asu": "arkansas state university",
    "u": "university",
    "univ": "university",
    "cc": "community college",
    "st": "state",
    "&": "and",
}


def _normalise(text: str) -> str:
    return _WS.sub(" ", _PUNCT.sub(" ", (text or "")).replace("-", " ")).strip()


def _acronym(text: str) -> str:
    """Arkansas State University Mountain Home -> ASUMH."""
    words = [w for w in _normalise(text).split() if w.lower() not in {"of", "the", "and", "at"}]
    return "".join(w[0] for w in words if w).upper()


def name_variants(query: str, domain: str | None = None) -> tuple[list[str], list[str]]:
    """Return (precise, fallback) query spellings.

    Precise variants are tried first and are specific enough to trust. Fallback
    variants are single distinctive words, tried only when nothing precise
    matched: "Mountain" finds the right record eventually, and also finds every
    other mountain college in the portal, so it is a last resort rather than a
    default.
    """
    precise: list[str] = []
    fallback: list[str] = []

    def add(target: list[str], candidate: str | None) -> None:
        c = (candidate or "").strip()
        if not c:
            return
        known = {x.lower() for x in precise + fallback}
        if c.lower() not in known:
            target.append(c)

    add(precise, query)
    if domain:
        add(precise, domain)

    plain = _normalise(query)
    add(precise, plain)

    tokens = plain.split()
    expanded = " ".join(_EXPANSIONS.get(t.lower(), t) for t in tokens)
    add(precise, expanded)

    # Initialisms, but only four characters or more. Three-letter acronyms are
    # too generic: "AMH" from "ASU Mountain Home" matches UMass Amherst, a
    # Norwegian college on amh.no, and a high school in Israel.
    for source in (plain, expanded):
        acr = _acronym(source)
        if 4 <= len(acr) <= 8:
            add(precise, acr)

    distinctive = sorted(
        (t for t in tokens if t.lower() not in _STOP and len(t) > 3),
        key=len,
        reverse=True,
    )
    for token in distinctive[:2]:
        add(fallback, token)

    return precise[:5], fallback[:2]


def _score(row: dict[str, Any], query: str, domain: str | None) -> int:
    """Rank a match against what was asked for. Higher is better.

    Scored against the same variant set the search uses, rather than against a
    separately computed acronym. The first version compared the record name to
    the acronym of the raw query, which for "ASU Mountain Home" is AMH rather
    than ASUMH, so the correct record scored 3 and Rocky Mountain College scored
    13. One source of truth for what counts as this institution's name.
    """
    props = row.get("properties", {})
    raw_name = props.get("name") or ""
    name = _normalise(raw_name).lower()
    row_domain = (props.get("domain") or "").lower()

    precise, _ = name_variants(query, domain)
    known = {_normalise(v).lower() for v in precise}
    known.add(_acronym(query).lower())

    wanted = _normalise(query).lower()
    wanted_tokens = {t for t in wanted.split() if t not in _STOP and len(t) > 2}
    name_tokens = {t for t in name.split() if len(t) > 2}

    score = 0
    if domain and domain.lower() in row_domain:
        score += 100
    if name in known:
        # Covers both the full name and the initialism the record is filed under.
        score += 60
    if name == wanted:
        score += 20
    score += 10 * len(wanted_tokens & name_tokens)
    if row_domain:
        score += 3
    if props.get("notes_last_updated"):
        score += 2
    return score


def _operator_owner_id() -> str:
    return (os.environ.get("KIB_HUBSPOT_OWNER_ID") or "").strip()


def _owner_flag(owner_id: str | None) -> str:
    """Say whose account this is, in the words the plan needs to use."""
    if not owner_id:
        return "unassigned"
    if owner_id == _operator_owner_id():
        return f"{owner_id} (yours)"
    return (
        f"{owner_id} (ANOTHER COLLEAGUE OWNS THIS ACCOUNT. Flag it and mark the record "
        "for owner review. Proceeding to outreach is Kib's yes or no decision, not "
        "yours, and not the account owner's by default.)"
    )


def _explain(status: int, body: str) -> str:
    if status == 401:
        return (
            "HubSpot rejected the token (401). It may be expired or revoked. "
            "Report pipeline state as unavailable and continue."
        )
    if status == 403:
        return (
            "HubSpot refused the scope (403). The private app is missing a read scope "
            "for this object. Report pipeline state as unavailable and continue; do "
            "not retry a different endpoint."
        )
    if status == 429:
        return "HubSpot rate limited the request (429). Report as unavailable for this run."
    return f"HubSpot returned {status}: {body[:300]}"


@tool
async def hubspot_find_account(name: str, domain: str | None = None) -> str:
    """Search HubSpot companies for an institution, trying several spellings.

    Use this before putting an institution in a plan. An account already in
    HubSpot is a constraint on the work order, not a fresh target.

    Pass the domain whenever you know it. It is an exact identifier and it finds
    records that no spelling of the name will match: ASU Mountain Home is stored
    as "ASUMH", so only asumh.edu or that initialism finds it.

    Args:
        name: Institution name as you have it, for example "ASU Mountain Home".
        domain: The institution's web domain if known, for example "asumh.edu".
    """
    token = _token()
    if not token:
        return NOT_CONFIGURED

    precise, fallback = name_variants(name, domain)
    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    tried: list[str] = []

    async def run_query(client: httpx.AsyncClient, body: dict[str, Any], label: str) -> str | None:
        resp = await client.post(
            f"{API}/crm/v3/objects/companies/search",
            headers=_headers(token),
            json=body,
        )
        tried.append(label)
        if resp.status_code >= 400:
            return _explain(resp.status_code, resp.text)
        for row in resp.json().get("results", []):
            if row["id"] not in seen_ids:
                seen_ids.add(row["id"])
                results.append(row)
        return None

    async with httpx.AsyncClient(timeout=30) as client:
        if domain:
            err = await run_query(
                client,
                {
                    "filterGroups": [
                        {
                            "filters": [
                                {
                                    "propertyName": "domain",
                                    "operator": "CONTAINS_TOKEN",
                                    "value": domain,
                                }
                            ]
                        }
                    ],
                    "properties": list(COMPANY_PROPS),
                    "limit": 10,
                },
                f"domain={domain}",
            )
            if err:
                return err

        for variant in precise:
            err = await run_query(
                client,
                {"query": variant, "limit": 10, "properties": list(COMPANY_PROPS)},
                repr(variant),
            )
            if err:
                return err

        # Loose single-word queries only when nothing precise matched.
        if not results:
            for variant in fallback:
                err = await run_query(
                    client,
                    {"query": variant, "limit": 10, "properties": list(COMPANY_PROPS)},
                    f"{variant!r} (broad fallback)",
                )
                if err:
                    return err

    results.sort(key=lambda row: _score(row, name, domain), reverse=True)
    suppressed = max(0, len(results) - 8)
    shown = results[:8]

    attempts = ", ".join(tried)
    if not results:
        return (
            f"No HubSpot company matched any of: {attempts}.\n"
            "This is an absence of a match, not confirmation of no relationship. Say it "
            "that way, and list the spellings tried so the negative can be checked. "
            "If the institution has a former name, an initialism, or a campus suffix "
            "you know of, search that too before concluding it is not on record."
        )

    lines = [
        f"{len(results)} HubSpot company match(es), best first. Spellings tried: {attempts}.",
        "Records are often stored under an initialism or a differently formatted "
        "campus name, so check the top match is really the institution you meant.",
    ]
    for row in shown:
        p = row.get("properties", {})
        lines.append(
            f"- id {row.get('id')} | {p.get('name') or '(no name)'} | "
            f"domain {p.get('domain') or 'unknown'} | "
            f"lifecycle {p.get('lifecyclestage') or 'unset'} | "
            f"type {p.get('type') or 'unset'} | "
            f"state {p.get('state') or 'unset'} | "
            f"deals {p.get('num_associated_deals') or '0'} | "
            f"created {(p.get('createdate') or '')[:10]} | "
            f"last note {(p.get('notes_last_updated') or 'never')[:10]} | "
            f"owner {_owner_flag(p.get('hubspot_owner_id'))}"
        )
    if suppressed:
        lines.append(
            f"({suppressed} weaker matches not shown. They came from broad spellings "
            "and are probably other institutions.)"
        )
    if len(shown) > 1:
        lines.append("")
        lines.append(
            "MORE THAN ONE MATCH. These are often duplicates. Prefer the record that has "
            "a domain and the most recent 'last note' date, and say in the plan that "
            "duplicates exist. A record with no domain and no recent activity is usually "
            "the stale copy, and planning from it will understate the relationship."
        )
    lines.append(
        "Call hubspot_account_deals with an id to see open opportunities before planning."
    )
    return "\n".join(lines)


@tool
async def hubspot_account_deals(company_id: str) -> str:
    """List the deals associated with one HubSpot company.

    An open deal means the institution comes out of prospecting work, or the run
    is scoped around the existing opportunity. Say which in the plan.

    Args:
        company_id: HubSpot company id from hubspot_find_account.
    """
    token = _token()
    if not token:
        return NOT_CONFIGURED

    async with httpx.AsyncClient(timeout=30) as client:
        assoc = await client.get(
            f"{API}/crm/v4/objects/companies/{company_id}/associations/deals",
            headers=_headers(token),
        )
        if assoc.status_code >= 400:
            return _explain(assoc.status_code, assoc.text)

        ids = [r["toObjectId"] for r in assoc.json().get("results", [])][:25]
        if not ids:
            return (
                f"Company {company_id} has no associated deals. No active opportunity "
                "on record. That is an absence of a record, not proof of no conversation."
            )

        batch = await client.post(
            f"{API}/crm/v3/objects/deals/batch/read",
            headers=_headers(token),
            json={
                "properties": list(DEAL_PROPS),
                "inputs": [{"id": str(i)} for i in ids],
            },
        )

    if batch.status_code >= 400:
        return _explain(batch.status_code, batch.text)

    rows = batch.json().get("results", [])
    open_rows = [r for r in rows if (r.get("properties", {}).get("hs_is_closed") in (None, "false", False))]
    closed_rows = [r for r in rows if r not in open_rows]

    lines = [f"Company {company_id}: {len(open_rows)} open, {len(closed_rows)} closed."]
    for label, group in (("OPEN", open_rows), ("CLOSED", closed_rows)):
        for row in group:
            p = row.get("properties", {})
            won = p.get("hs_is_closed_won")
            lines.append(
                f"- [{label}] {p.get('dealname') or '(unnamed)'} | stage {p.get('dealstage') or 'unset'} | "
                f"close {(p.get('closedate') or 'unset')[:10]} | "
                f"type {p.get('dealtype') or 'unset'} | "
                f"modified {(p.get('hs_lastmodifieddate') or '')[:10]}"
                + (f" | won={won}" if label == "CLOSED" else "")
            )
    if open_rows:
        lines.append(
            "An open deal is a constraint. Either drop the institution from prospecting "
            "work or state explicitly that the run is scoped around the open opportunity."
        )
    return "\n".join(lines)


@tool
async def hubspot_owner(owner_id: str) -> str:
    """Resolve a HubSpot owner id to a name, so a plan can say who holds an account.

    Args:
        owner_id: The hubspot_owner_id value from a company or deal.
    """
    token = _token()
    if not token:
        return NOT_CONFIGURED

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{API}/crm/v3/owners/{owner_id}", headers=_headers(token)
        )

    if resp.status_code == 404:
        return f"No HubSpot owner with id {owner_id}."
    if resp.status_code >= 400:
        return _explain(resp.status_code, resp.text)

    o = resp.json()
    name = " ".join(x for x in (o.get("firstName"), o.get("lastName")) if x) or "(no name)"
    return f"Owner {owner_id}: {name}, {o.get('email') or 'no email listed'}"


# --- account history -------------------------------------------------------
# The notes scope arrived unrequested and Kib chose to use it, Director only.
# Note bodies are internal commentary: colleague opinion, deal speculation,
# pricing talk. The Director writes nothing outbound and the work order has no
# field for prose, so the only way this can travel to a worker is as a short
# constraint phrase the Director composes. That is the wall working by shape.

_TAG = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _TAG.sub(" ", text or "").replace("&nbsp;", " ").replace("&amp;", "&").strip()


@tool
async def hubspot_account_notes(company_id: str, limit: int = 5) -> str:
    """Read the most recent notes logged against one company. Director only.

    This is the account history: what colleagues actually recorded. Use it to
    turn "prior outreach unverified" into a stated fact.

    Never carry note text into a work order or anywhere a prospect could see it.
    Summarise it into a constraint phrase, for example "spoke to the registrar in
    March about census reporting, no follow-up logged since".

    Args:
        company_id: HubSpot company id from hubspot_find_account.
        limit: How many recent notes to return, at most 10.
    """
    token = _token()
    if not token:
        return NOT_CONFIGURED

    limit = max(1, min(int(limit or 5), 10))

    async with httpx.AsyncClient(timeout=30) as client:
        assoc = await client.get(
            f"{API}/crm/v4/objects/companies/{company_id}/associations/notes",
            headers=_headers(token),
        )
        if assoc.status_code >= 400:
            return _explain(assoc.status_code, assoc.text)

        ids = [r["toObjectId"] for r in assoc.json().get("results", [])]
        if not ids:
            return (
                f"Company {company_id} has no notes logged. Nothing was recorded, which "
                "is not the same as nothing having happened."
            )

        batch = await client.post(
            f"{API}/crm/v3/objects/notes/batch/read",
            headers=_headers(token),
            json={
                "properties": ["hs_note_body", "hs_timestamp", "hs_createdate"],
                "inputs": [{"id": str(i)} for i in ids[-40:]],
            },
        )

    if batch.status_code >= 400:
        return _explain(batch.status_code, batch.text)

    rows = batch.json().get("results", [])
    rows.sort(
        key=lambda r: (r.get("properties", {}).get("hs_timestamp") or ""), reverse=True
    )

    lines = [
        f"{len(rows)} note(s) on company {company_id}. Most recent {min(limit, len(rows))}:",
        "[INTERNAL. Summarise into a constraint. Never quote this to a prospect and "
        "never put note text in a work order.]",
    ]
    for row in rows[:limit]:
        p = row.get("properties", {})
        body = _strip_html(p.get("hs_note_body") or "")
        lines.append(
            f"- {(p.get('hs_timestamp') or '')[:10]}: {body[:400]}"
            + ("..." if len(body) > 400 else "")
        )
    if len(rows) > limit:
        lines.append(f"({len(rows) - limit} older notes not shown.)")
    newest = (rows[0].get("properties", {}).get("hs_timestamp") or "")[:10] if rows else ""
    if newest:
        lines.append(
            f"Newest readable note: {newest}. If the company's 'last note' property is "
            "later than this, something touched the record that is not a note you can "
            "read. Report both dates rather than treating the property as the last "
            "human contact."
        )
    return "\n".join(lines)


# --- contact level, added for Account Context, 4 September 2026 --------------
#
# The module docstring above says contacts are deliberately absent, and that was
# right while HubSpot belonged to the Director: a planner does not need to know
# who we have emailed, and leaving contacts out kept personal data out of the
# planning context. Account Context is a different agent with a different job,
# and the four misses on 4 September were all contact-shaped. Ronnie Williams
# retired in 2021 and the record said so. So contacts are readable here, by that
# agent only, and `connectors.py` is where that stays true.
#
# Still no write path. There is no create, update, delete or association call in
# this file and adding one is the thing to refuse.

CONTACT_PROPS = (
    "firstname",
    "lastname",
    "email",
    "jobtitle",
    "company",
    "hubspot_owner_id",
    "lifecyclestage",
    "hs_lead_status",
    "lastmodifieddate",
    "notes_last_contacted",
    "num_notes",
    "hs_email_last_send_date",
    "hs_email_last_reply_date",
)


def _person_variants(name: str) -> list[str]:
    """Search strings worth trying for a person.

    HubSpot's contact search matches on name fragments, so the full string
    finds a record stored the same way and nothing else. "Ronnie Williams"
    stored as "Ron Williams" needs the surname on its own, and a surname on its
    own at a large institution returns a page of people, which is fine: the
    caller reads them and decides.
    """
    cleaned = _WS.sub(" ", _PUNCT.sub(" ", name)).strip()
    if not cleaned:
        return []
    parts = cleaned.split()
    variants = [cleaned]
    if len(parts) > 1 and parts[-1] not in variants:
        variants.append(parts[-1])
    return variants


@tool
async def hubspot_find_contact(name: str, company: str | None = None) -> str:
    """Search HubSpot contacts for a named person before researching them.

    Use this first, before any web research on a person. A contact we already
    hold carries the two facts that cost the most to learn late: whether they
    still work there, and when we last spoke to them.

    Reports every query it ran, including the ones that found nothing. A search
    that missed is not the same as an account we have never touched, and the
    difference is the whole value of the check.

    Args:
        name: Person's name as you have it, for example "Ronnie Williams".
        company: Their institution, to narrow a common surname. Optional.
    """
    token = _token()
    if not token:
        return NOT_CONFIGURED

    variants = _person_variants(name)
    if not variants:
        return "No searchable name given."

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    tried: list[str] = []

    async with httpx.AsyncClient(timeout=30) as client:
        for variant in variants:
            body: dict[str, Any] = {
                "query": variant,
                "limit": 10,
                "properties": list(CONTACT_PROPS),
            }
            resp = await client.post(
                f"{API}/crm/v3/objects/contacts/search",
                headers=_headers(token),
                json=body,
            )
            tried.append(f"contacts query={variant!r}")
            if resp.status_code >= 400:
                return _explain(resp.status_code, resp.text)
            for row in resp.json().get("results", []):
                if row["id"] not in seen:
                    seen.add(row["id"])
                    results.append(row)
            if results:
                break

    searched = "; ".join(tried)
    if not results:
        return (
            f"HubSpot has no contact matching {name!r}.\n"
            f"Searched: {searched}\n"
            "Record the searches in your verdict. A miss on a name we store "
            "differently reads identically to a person we have never held, and "
            "only the query list tells them apart."
        )

    if company:
        wanted = _normalise(company)
        ranked = sorted(
            results,
            key=lambda r: wanted not in _normalise(str(r["properties"].get("company") or "")),
        )
    else:
        ranked = results

    lines = [f"HubSpot contacts matching {name!r} ({len(ranked)}):", f"Searched: {searched}"]
    for row in ranked[:10]:
        p = row["properties"]
        full = " ".join(x for x in (p.get("firstname"), p.get("lastname")) if x) or "unnamed"
        lines.append(
            f"- id {row['id']} | {full} | {p.get('jobtitle') or 'no title'} | "
            f"{p.get('company') or 'no company'} | {p.get('email') or 'no email'}"
        )
        lines.append(
            f"  owner {_owner_flag(p.get('hubspot_owner_id'))} | "
            f"lifecycle {p.get('lifecyclestage') or 'unset'} | "
            f"lead status {p.get('hs_lead_status') or 'unset'}"
        )
        lines.append(
            f"  last contacted {p.get('notes_last_contacted') or 'never'} | "
            f"last email sent {p.get('hs_email_last_send_date') or 'never'} | "
            f"last reply {p.get('hs_email_last_reply_date') or 'never'} | "
            f"record modified {p.get('lastmodifieddate') or 'unknown'}"
        )
    lines.append(
        "A title in HubSpot is as old as the record. Check it against a current "
        "source before writing to the person: University of Central Arkansas was "
        "researched twice in one day for a Vice President who retired in 2021."
    )
    return "\n".join(lines)
