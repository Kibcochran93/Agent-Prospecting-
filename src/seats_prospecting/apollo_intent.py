"""Live website-visitor intent from Apollo, replacing a dead HubSpot field.

Why this module exists. The trigger the missed-opportunity work was built on,
``seats_last_intent_visit`` in HubSpot, stopped being written after April 2026:
90 companies that month and none since. The weekly Teams report that fed the
same purpose stopped in August 2025. Apollo is the third pipe and the only one
still flowing: the tracker on seatsone.com received data twenty minutes before
this module was written, and eleven intent paths were configured on 10 September
so a visit to /demo or /pricing now scores differently from a visit to a blog.

Two rules.

**No addresses come home.** Apollo returns each visitor's email. This module
drops it and keeps the person's name, title, organisation, domain and visit
stats. Holding prospect addresses in this system was declined on 4 September and
that decision stands; ``tests/test_apollo_intent.py`` asserts no email field
survives parsing.

**Absence is never evidence.** Apollo identifies visitors at person level for
United States traffic only, and attaches a confidence tier. An institution
missing from this list has not been shown to be uninterested, and
``coverage_note`` says so on every result set.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

APOLLO_API = "https://api.apollo.io/api/v1"

COVERAGE_NOTE = (
    "Apollo identifies website visitors at person level for United States "
    "traffic only, with a confidence tier. An institution absent from this set "
    "has not been shown to be uninterested; it has not been identified."
)


class IntentUnavailable(Exception):
    """Raised instead of returning an empty list that reads as 'nobody visited'."""


@dataclass(frozen=True)
class Visitor:
    person: str
    title: str
    organisation: str
    domain: str
    last_visit: str
    total_visits: int
    intent: str
    contact_id: str

    @property
    def account_key(self) -> str:
        from .digest import STALE_AFTER_DAYS  # noqa: F401  (kept import local)

        return self.domain.lower().removeprefix("www.")


def tracked_domain() -> str:
    return (os.environ.get("SEATS_TRACKED_DOMAIN") or "seatsone.com").strip()


def _parse(contacts: list[dict[str, Any]]) -> list[Visitor]:
    """Visitors from a contacts/search payload, with the addresses dropped.

    Only these fields are read. Nothing constructs a dict from the raw contact,
    so a future Apollo field cannot arrive here by accident.
    """
    out: list[Visitor] = []
    for row in contacts:
        visit = row.get("website_visitor") or {}
        if not visit:
            continue
        org = row.get("organization") or row.get("account") or {}
        out.append(
            Visitor(
                person=str(row.get("name") or "").strip(),
                title=str(row.get("title") or "").strip(),
                organisation=str(row.get("organization_name")
                                 or org.get("name") or "").strip(),
                domain=str(org.get("primary_domain") or org.get("domain") or "").strip(),
                last_visit=str(visit.get("website_last_visit") or "").strip(),
                total_visits=int(visit.get("website_total_visits") or 0),
                intent=str(visit.get("website_intent") or "").strip(),
                contact_id=str(row.get("id") or "").strip(),
            )
        )
    out.sort(key=lambda v: v.last_visit, reverse=True)
    return out


def freshness(visitors: list[Visitor], today: date | None = None) -> tuple[int, bool]:
    """Age of the newest visit. The same check the HubSpot field failed."""
    today = today or date.today()
    days = []
    for v in visitors:
        try:
            days.append(date.fromisoformat(v.last_visit[:10]))
        except ValueError:
            continue
    if not days:
        return -1, False
    age = (today - max(days)).days
    return age, age <= 45


def search_body(
    days: int = 30,
    per_page: int = 100,
    page: int = 1,
    pages_viewed: list[str] | None = None,
) -> dict[str, Any]:
    """The Apollo query. Costs no enrichment credits: the visitor summary rides
    along on a contacts search and is not a reveal.

    ``pages_viewed`` narrows to visitors who touched those paths. Apollo has no
    public endpoint for listing visiting companies, so this contact-level filter
    is the whole of the high-intent read that is reachable from here. Verified
    in scope on 10 September: the filter is accepted and returns 0, meaning
    nobody identified has been on a high-intent page in 90 days.
    """
    body: dict[str, Any] = {
        "website_visitors_people_from_domains": [tracked_domain()],
        "website_visitors_people_from_past": days,
        "sort_by_field": "last_visited_at",
        "sort_ascending": False,
        "per_page": per_page,
        "page": page,
    }
    if pages_viewed:
        body["website_visitors_people_pages"] = list(pages_viewed)
    return body


async def fetch_high_intent(days: int = 90, pages: int = 2) -> list[Visitor]:
    """Visitors who touched a path Apollo has configured as high intent.

    The paths come from the reveal gate's config, not from a list here, so the
    definition of high intent is in one place. If that config has gone stale the
    gate refuses, and so does this: a query that claims to find high-intent
    visitors while using out-of-date path levels would be worse than no query.
    """
    from .reveal_gate import RevealRefused, high_intent_paths

    try:
        paths = high_intent_paths()
    except RevealRefused as exc:
        raise IntentUnavailable(
            f"The high intent path config is not usable ({exc}), so no "
            "high-intent search was run."
        ) from exc
    if not paths:
        raise IntentUnavailable(
            "No paths are configured as high intent, so a high-intent search "
            "would match everything or nothing. Set levels in Apollo first."
        )
    return await fetch(days=days, pages=pages, pages_viewed=paths)


async def fetch(
    days: int = 30, pages: int = 2, pages_viewed: list[str] | None = None
) -> list[Visitor]:
    """Read live visitors with the OAuth token, as Kib, not as an api key."""
    import httpx

    from .apollo_oauth import (
        OAuthFailed,
        OAuthNotConfigured,
        bearer_headers,
        current_token,
    )

    if days not in (7, 15, 30, 60, 90):
        raise IntentUnavailable(
            f"Apollo only accepts a window of 7, 15, 30, 60 or 90 days, not {days}."
        )
    try:
        headers = bearer_headers(current_token())
    except (OAuthNotConfigured, OAuthFailed) as exc:
        raise IntentUnavailable(
            f"No Apollo token, so no intent was read ({exc}). Reporting nothing "
            "rather than an empty list."
        ) from exc

    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=45) as client:
        for page in range(1, max(1, pages) + 1):
            resp = await client.post(
                f"{APOLLO_API}/contacts/search",
                headers=headers,
                content=json.dumps(
                    search_body(days, page=page, pages_viewed=pages_viewed)
                ),
            )
            if resp.status_code >= 400:
                raise IntentUnavailable(
                    f"Apollo refused the visitor search ({resp.status_code}): "
                    f"{resp.text[:200]}. No intent was read."
                )
            payload = resp.json()
            batch = payload.get("contacts") or []
            rows.extend(batch)
            paging = payload.get("pagination") or {}
            if page >= int(paging.get("total_pages") or 1) or not batch:
                break
    return _parse(rows)


def by_domain(visitors: list[Visitor]) -> dict[str, Visitor]:
    """Newest visit per domain, for joining onto a CRM row."""
    best: dict[str, Visitor] = {}
    for v in visitors:
        if not v.domain:
            continue
        key = v.domain.lower().removeprefix("www.")
        if key not in best or v.last_visit > best[key].last_visit:
            best[key] = v
    return best


def as_of() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
