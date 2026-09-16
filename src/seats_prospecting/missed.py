"""seats-missed: accounts that raised a hand and got no answer.

Kib's framing, 10 September: the point of this system is accounts falling
through the cracks, so ownership is a notification and not a gate. This module
finds them.

The population is not hypothetical. A HubSpot read the same day found 182 US
companies with an intent visit on record and no open deal. Three had never been
contacted at all. Most of the rest are worse: somebody worked them, stopped, and
the prospect came back on their own. Anna Maria College has 37 logged touches
ending November 2024 and a site visit in August 2025.

Two rules this module will not bend.

**A dead trigger is reported, never ranked.** ``seats_last_intent_visit``
stopped being written in April 2026: 90 companies that month, zero since. A
ranking built on it would sort accounts by a staleness that itself stopped
updating, and would look authoritative doing it. ``freshness()`` checks the
newest value across the whole result set and refuses to score when the field
itself has gone quiet.

**Ownership never suppresses a row.** The owner is carried so they can be told.
There is no filter, argument or config that drops an account because a colleague
owns it. Waiting for the owner is what produced the eighteen-month gaps.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

#: A trigger field is only usable if something wrote to it recently.
TRIGGER_STALE_AFTER_DAYS = 45

#: An account touched this recently is a live conversation, not a miss.
ACTIVE_WITHIN_DAYS = 30

#: How long a silence has to run before a return visit counts as a miss.
SILENCE_DAYS = 90

FORM_NO_REPLY = "FORM_NO_REPLY"
NEVER_CONTACTED = "NEVER_CONTACTED"
RETURNED_AFTER_SILENCE = "RETURNED_AFTER_SILENCE"
DORMANT = "DORMANT"
LIVE = "LIVE"
UNKNOWN = "UNKNOWN"

#: Loudest first. A form submission is someone typing their name in.
TIERS = (FORM_NO_REPLY, NEVER_CONTACTED, RETURNED_AFTER_SILENCE, DORMANT)


class TriggerStale(Exception):
    """The intent field stopped updating. Reported rather than ranked."""


@dataclass(frozen=True)
class Miss:
    name: str
    company_id: str
    domain: str
    owner_id: str
    tier: str
    why: str
    last_touch: str
    last_intent: str
    silence_days: int
    forms: int
    touches: int
    #: Which pipe the intent date came from. Printed, because a date from a feed
    #: that died in April means something different from one from this week.
    intent_source: str = "hubspot_field"
    visitor: str = ""

    @property
    def rank(self) -> int:
        return TIERS.index(self.tier) if self.tier in TIERS else len(TIERS)


def _day(value: Any) -> date | None:
    """A HubSpot date, from either an epoch-millis string or an ISO string."""
    if value in (None, "", 0, "0"):
        return None
    text = str(value)
    if text.isdigit():
        try:
            return datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc).date()
        except (ValueError, OSError, OverflowError):
            return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def classify(
    *,
    last_touch: Any,
    last_intent: Any,
    forms: int = 0,
    touches: int = 0,
    today: date | None = None,
) -> tuple[str, str, int]:
    """Which kind of miss this is, why, and how long the silence ran.

    Deliberately pure: no HubSpot, no clock unless one is passed. The tiers are
    about who acted last. If the prospect acted after we did, and long enough
    after, that is a miss whatever the notes say.
    """
    today = today or date.today()
    touch = _day(last_touch)
    intent = _day(last_intent)

    if intent is None:
        return UNKNOWN, "No intent signal on record, so there is nothing to answer.", 0

    if touches == 0 and forms > 0:
        return (
            FORM_NO_REPLY,
            f"{forms} form submission(s) and nobody has ever been contacted here. "
            "They typed their details in and got no reply.",
            (today - intent).days,
        )
    if touches == 0 or touch is None:
        return (
            NEVER_CONTACTED,
            f"Intent visit {intent.isoformat()} and no outbound contact on record, "
            "ever.",
            (today - intent).days,
        )

    if (today - touch).days <= ACTIVE_WITHIN_DAYS:
        return (
            LIVE,
            f"Contacted {touch.isoformat()}, within {ACTIVE_WITHIN_DAYS} days. This "
            "is a live conversation, not a miss.",
            0,
        )

    gap = (intent - touch).days
    if intent > touch and gap >= SILENCE_DAYS:
        return (
            RETURNED_AFTER_SILENCE,
            f"{touches} touch(es), the last on {touch.isoformat()}. They came back "
            f"to the site on {intent.isoformat()}, {gap} days later, and nothing "
            "went out after that.",
            gap,
        )

    return (
        DORMANT,
        f"Last contacted {touch.isoformat()}, {(today - touch).days} days ago. "
        f"Intent visit {intent.isoformat()}. No open deal.",
        (today - touch).days,
    )


def freshness(intent_dates: list[Any], today: date | None = None) -> tuple[int, bool]:
    """Age of the newest intent value in the set, and whether it is usable.

    This is about the field, not the account. If nothing has written to the
    trigger in weeks then every age computed from it is wrong in the same
    direction, and a ranked list would hide that behind confident ordering.
    """
    today = today or date.today()
    days = [d for d in (_day(v) for v in intent_dates) if d is not None]
    if not days:
        return -1, False
    age = (today - max(days)).days
    return age, age <= TRIGGER_STALE_AFTER_DAYS


def missed_from_rows(
    rows: list[dict[str, Any]],
    *,
    today: date | None = None,
    require_fresh_trigger: bool = True,
    live_intent: dict[str, Any] | None = None,
) -> list[Miss]:
    """Turn HubSpot company rows into a ranked list, or refuse.

    Raises TriggerStale rather than returning a list built on a dead field.
    Pass require_fresh_trigger=False only to look at the population knowingly.
    """
    today = today or date.today()
    live_intent = live_intent or {}

    def intent_for(props: dict[str, Any]) -> tuple[Any, str, str]:
        """Apollo first, the HubSpot field only as a fallback.

        Apollo is live; the field stopped in April 2026. Where both exist the
        live one wins, and where only the dead one exists the row still appears
        with its source named rather than being dropped.
        """
        domain = str(props.get("domain") or "").lower().removeprefix("www.")
        hit = live_intent.get(domain)
        if hit is not None:
            return (
                getattr(hit, "last_visit", ""),
                "apollo_live",
                f"{getattr(hit, 'person', '')}, {getattr(hit, 'title', '')}".strip(", "),
            )
        return props.get("seats_last_intent_visit"), "hubspot_field", ""

    intents = [intent_for(r.get("properties") or {})[0] for r in rows]
    age, usable = freshness(intents, today)
    if require_fresh_trigger and not usable:
        raise TriggerStale(
            f"The newest intent value in {len(rows)} companies is {age} days old, "
            f"past the {TRIGGER_STALE_AFTER_DAYS} day limit. The trigger field has "
            "stopped being written, so ranking by it would sort accounts by a "
            "staleness that is itself stale. Fix the feed, or point the trigger at "
            "Apollo, which is live. Re-run with --stale-ok to see the population "
            "anyway."
        )

    out: list[Miss] = []
    for row in rows:
        props = row.get("properties") or {}
        raw_intent, source, who = intent_for(props)
        tier, why, silence = classify(
            last_touch=props.get("notes_last_contacted"),
            last_intent=raw_intent,
            forms=int(props.get("num_conversion_events") or 0),
            touches=int(props.get("num_contacted_notes") or 0),
            today=today,
        )
        if tier in (LIVE, UNKNOWN):
            continue
        touch = _day(props.get("notes_last_contacted"))
        intent = _day(raw_intent)
        out.append(
            Miss(
                name=str(props.get("name") or "unnamed company"),
                company_id=str(row.get("id") or ""),
                domain=str(props.get("domain") or ""),
                # Carried to be told, never to filter on.
                owner_id=str(props.get("hubspot_owner_id") or ""),
                tier=tier,
                why=why,
                last_touch=touch.isoformat() if touch else "never",
                last_intent=intent.isoformat() if intent else "unknown",
                silence_days=silence,
                forms=int(props.get("num_conversion_events") or 0),
                touches=int(props.get("num_contacted_notes") or 0),
                intent_source=source,
                visitor=who,
            )
        )
    out.sort(key=lambda m: (m.rank, -m.silence_days))
    return out


SEARCH_PROPS = (
    "name",
    "domain",
    "country",
    "hubspot_owner_id",
    "notes_last_contacted",
    "num_contacted_notes",
    "num_conversion_events",
    "hs_num_open_deals",
    "seats_last_intent_visit",
)


def search_body(country: str = "United States", limit: int = 100) -> dict[str, Any]:
    """The HubSpot company search for the missed population.

    No owner filter, by design. See the module docstring.
    """
    return {
        "filterGroups": [
            {
                "filters": [
                    {"propertyName": "country", "operator": "EQ", "value": country},
                    {
                        "propertyName": "seats_last_intent_visit",
                        "operator": "HAS_PROPERTY",
                    },
                    {"propertyName": "hs_num_open_deals", "operator": "EQ", "value": "0"},
                ]
            }
        ],
        "properties": list(SEARCH_PROPS),
        "sorts": [{"propertyName": "seats_last_intent_visit", "direction": "DESCENDING"}],
        "limit": min(limit, 100),
    }


def domain_search_body(domains: list[str], limit: int = 100) -> dict[str, Any]:
    """Companies matching a set of domains, whatever their intent field says.

    The first version of this module defined the population as "has the HubSpot
    intent field", which meant an account known to be on the site right now
    could not enter the list at all: zero of 181 rows matched a live Apollo
    visitor, because the live ones were never written to the dead field. The
    population is the union of the two sources, not the intersection.
    """
    return {
        "filterGroups": [
            {
                "filters": [
                    {
                        "propertyName": "domain",
                        "operator": "IN",
                        "values": [d for d in domains if d][:100],
                    }
                ]
            }
        ],
        "properties": list(SEARCH_PROPS),
        "limit": min(limit, 100),
    }


async def fetch_domains(domains: list[str]) -> list[dict[str, Any]]:
    """The CRM rows behind a set of live visitor domains."""
    import httpx

    from .tools.hubspot import API, _headers, _token

    if not domains:
        return []
    token = _token()
    if not token:
        raise TriggerStale("No HubSpot token, so the live domains could not be looked up.")
    async with httpx.AsyncClient(timeout=45) as client:
        resp = await client.post(
            f"{API}/crm/v3/objects/companies/search",
            headers=_headers(token),
            json=domain_search_body(domains),
        )
    if resp.status_code >= 400:
        raise TriggerStale(
            f"HubSpot refused the domain lookup ({resp.status_code}): "
            f"{resp.text[:200]}. The live visitors could not be joined to the CRM."
        )
    return resp.json().get("results") or []


async def fetch(country: str = "United States", pages: int = 3) -> list[dict[str, Any]]:
    """Read the population from HubSpot with the token this build already uses."""
    import httpx

    from .tools.hubspot import API, _headers, _token

    token = _token()
    if not token:
        raise TriggerStale(
            "No HubSpot token, so the missed list could not be read. Reporting "
            "nothing rather than an empty list."
        )
    rows: list[dict[str, Any]] = []
    after: str | None = None
    async with httpx.AsyncClient(timeout=45) as client:
        for _ in range(max(1, pages)):
            body = search_body(country)
            if after:
                body["after"] = after
            resp = await client.post(
                f"{API}/crm/v3/objects/companies/search",
                headers=_headers(token),
                json=body,
            )
            if resp.status_code >= 400:
                raise TriggerStale(
                    f"HubSpot refused the company search ({resp.status_code}): "
                    f"{resp.text[:200]}. No list was produced."
                )
            payload = resp.json()
            rows.extend(payload.get("results") or [])
            after = ((payload.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                break
    return rows
