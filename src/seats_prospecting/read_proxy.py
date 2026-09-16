"""Read-only Apollo/HubSpot proxy for the bridge (board_serve.py).

Exists so the local dashboard -- a plain HTML file with no window.claude
available, because it is not running inside a published Artifact or Cowork
session -- can still reach the four Apollo/HubSpot calls it needs. Translated
here into the OAuth/token credentials this project already has (Kib's own
apollo_oauth.py token, HUBSPOT_ACCESS_TOKEN), not a new credential path and
not the old workspace-wide API key ADR-removed on 4 September.

Deliberately narrow: only the exact tool names the dashboard calls are
mapped, with the same parameter names the dashboard already sends (these
happen to match Apollo/HubSpot's own REST field names closely enough that no
translation layer was needed beyond picking the right URL). Anything else
raises rather than guessing at a shape.

Read-only. Nothing here can create, update, or send anything in Apollo or
HubSpot -- that boundary matters because this runs unauthenticated-to-Apollo
requests on Kib's own OAuth identity, and a proxy that silently grew a write
path here would be very easy to miss in review.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from .tools.apollo import _auth as _apollo_oauth_headers

APOLLO_API = "https://api.apollo.io/api/v1"
HUBSPOT_API = "https://api.hubapi.com"

_APOLLO_TOOLS = {
    "apollo_contacts_search": ("POST", "/contacts/search"),
    "apollo_emailer_campaigns_search": ("POST", "/emailer_campaigns/search"),
}


class ProxyError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _hubspot_headers() -> dict[str, str]:
    token = (os.environ.get("HUBSPOT_ACCESS_TOKEN") or "").strip()
    if not token:
        raise ProxyError(503, "HubSpot is unavailable: HUBSPOT_ACCESS_TOKEN is not set in .env.")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def apollo_call(tool: str, body: dict[str, Any]) -> dict[str, Any]:
    route = _APOLLO_TOOLS.get(tool)
    if route is None:
        raise ProxyError(400, f"Unrecognised Apollo tool for the bridge proxy: {tool!r}")
    headers = _apollo_oauth_headers()
    if headers is None:
        raise ProxyError(503, "Apollo is unavailable: no usable OAuth token on this machine.")
    method, path = route
    resp = httpx.request(method, APOLLO_API + path, headers=headers, json=body, timeout=30)
    if resp.status_code >= 400:
        raise ProxyError(resp.status_code, f"Apollo returned {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def hubspot_call(tool: str, body: dict[str, Any]) -> dict[str, Any]:
    headers = _hubspot_headers()
    if tool == "search_crm_objects":
        object_type = str(body.get("objectType", "CONTACT")).lower()
        payload = {k: v for k, v in body.items() if k not in ("objectType", "chatInsights")}
        resp = httpx.post(f"{HUBSPOT_API}/crm/v3/objects/{object_type}/search",
                           headers=headers, json=payload, timeout=30)
        if resp.status_code >= 400:
            raise ProxyError(resp.status_code, f"HubSpot returned {resp.status_code}: {resp.text[:300]}")
        return resp.json()
    if tool == "search_owners":
        wanted = {int(i) for i in (body.get("ownerIds") or [])}
        resp = httpx.get(f"{HUBSPOT_API}/crm/v3/owners", headers=headers,
                          params={"limit": 100}, timeout=30)
        if resp.status_code >= 400:
            raise ProxyError(resp.status_code, f"HubSpot returned {resp.status_code}: {resp.text[:300]}")
        results = resp.json().get("results", [])
        owners = [
            {"ownerId": o["id"], "name": f'{o.get("firstName", "")} {o.get("lastName", "")}'.strip()}
            for o in results if not wanted or int(o["id"]) in wanted
        ]
        return {"owners": owners}
    raise ProxyError(400, f"Unrecognised HubSpot tool for the bridge proxy: {tool!r}")
