"""Answer the four verify-before-building questions against reality.

The design notes list four things where a failure changes the design rather
than the wording. Two of them are answered by reading the SDK, and this file
states those answers. The other two depend on what a connector actually exposes
to your workspace, so this file goes and looks.

Run it before the first real run, and again after any connector upgrade.

    python -m seats_prospecting.preflight
"""

from __future__ import annotations

import asyncio
import os

import httpx

from . import settings
from .settings import ConnectorSpec

APOLLO_API = "https://api.apollo.io/api/v1"
RULE = "-" * 78

# Tools whose presence on a server breaks a create-only or read-only claim.
WRITE_MARKERS = (
    "create",
    "update",
    "delete",
    "send",
    "activate",
    "approve",
    "enroll",
    "move",
    "duplicate",
    "add_",
    "remove",
    "complete",
    "skip",
    "start",
)


def _is_write(name: str) -> bool:
    lowered = name.lower()
    return any(marker in lowered for marker in WRITE_MARKERS)


async def _list_tools(spec: ConnectorSpec) -> list[str] | str:
    """Ask a remote MCP server what it exposes. Returns names or an error string."""
    if not spec.server_url:
        return "hosted connector: cannot introspect without a live Responses call"
    if not spec.configured:
        return f"not configured ({spec.auth_env} unset)"

    try:
        from agents.mcp import MCPServerStreamableHttp
    except ImportError as exc:  # pragma: no cover
        return f"agents.mcp unavailable: {exc}"

    try:
        async with MCPServerStreamableHttp(
            name=spec.label,
            params={
                "url": spec.server_url,
                "headers": {"Authorization": f"Bearer {spec.token}"},
            },
            client_session_timeout_seconds=20,
        ) as server:
            tools = await server.list_tools()
            return [t.name for t in tools]
    except Exception as exc:  # noqa: BLE001 - surface anything, do not mask
        return f"{type(exc).__name__}: {exc}"


def _report_scope(spec: ConnectorSpec, tools: list[str] | str) -> None:
    print(f"\n{spec.label}")
    if isinstance(tools, str):
        print(f"  UNKNOWN  {tools}")
        return

    claimed = set(spec.allowed_tools)
    available = set(tools)
    missing = sorted(claimed - available)
    writes = sorted(t for t in available if _is_write(t))
    claimed_writes = sorted(t for t in claimed if _is_write(t))

    print(f"  server exposes {len(available)} tools, {len(writes)} of them writes")
    if missing:
        print(f"  BROKEN   allowed_tools names that do not exist: {missing}")
        print("           A wrong name silently narrows the scope or fails at call time.")
    if claimed_writes:
        print(f"  NOTE     write tools you are deliberately allowing: {claimed_writes}")
    unclaimed_writes = [t for t in writes if t not in claimed]
    if unclaimed_writes:
        print(f"  OK       {len(unclaimed_writes)} write tools excluded by allowed_tools")
    if not missing and not claimed_writes:
        print("  OK       read-only scope, every claimed tool exists")


def whose_key(profile: dict) -> tuple[str, str]:
    """Name and email on an Apollo api_profile response."""
    user = profile.get("user") or profile
    return (user.get("name") or "no name on the profile", user.get("email") or "unknown")


def identity_verdict(email: str, expected: str | None) -> str:
    """Whether the Apollo key is the person this build is meant to act as.

    Written on 3 September 2026, after the first live Apollo write landed in a
    colleague's account. The key in .env belonged to Miguel Pescador, so the
    sequence was created as his, with restricted visibility, and Kib could not
    find it in his own Apollo. Every reveal credit had been coming off that user
    too. Nothing in the system said so, because nothing ever asked whose key it
    was holding.
    """
    if not expected:
        return (
            f"  key identity: {email}\n"
            "  Set APOLLO_EXPECTED_USER to the address this build should act as, and "
            "this check becomes an assertion rather than a note."
        )
    if email.strip().casefold() == expected.strip().casefold():
        return f"  OK       the Apollo key is {email}, as expected"
    return (
        f"  WRONG    the Apollo key belongs to {email}, not {expected}.\n"
        "  Everything this build writes will be created as that person, and on a\n"
        "  restricted sequence you will not be able to see it. Reveal credits come\n"
        "  off their seat too. Sign in again with\n"
        "  `python -m seats_prospecting.apollo_oauth login` under your own Apollo\n"
        "  account."
    )


def owner_ids_from_sequences(payload: dict) -> set[str]:
    """The user ids this key owns sequences for.

    Apollo marks ``sharing_permission.is_owner`` relative to whoever is asking,
    so a key that reports True on somebody's sequences is that somebody. This is
    the identity check that works on a scoped key: ``users/api_profile`` needs a
    scope a restricted key does not have, and returns 403 instead of an answer.
    """
    ids: set[str] = set()
    for campaign in payload.get("emailer_campaigns") or []:
        sharing = campaign.get("sharing_permission") or {}
        if sharing.get("is_owner"):
            owner = sharing.get("owner_id") or campaign.get("user_id")
            if owner:
                ids.add(owner)
    return ids


def email_for_user_id(payload: dict, user_id: str) -> str | None:
    """Map an Apollo user id to a mailbox address via the team's email accounts."""
    for account in payload.get("email_accounts") or []:
        if account.get("user_id") == user_id:
            return account.get("email")
    return None


async def _apollo_identity_by_ownership(client: httpx.AsyncClient, headers: dict) -> None:
    seqs = await client.get(
        f"{APOLLO_API}/emailer_campaigns/search?per_page=25&page=1", headers=headers
    )
    if seqs.status_code >= 400:
        print(f"  could not check by ownership either: Apollo returned {seqs.status_code}")
        return
    owners = owner_ids_from_sequences(seqs.json())
    if not owners:
        print("  this key owns none of the sequences on the first page, so identity is")
        print("  unresolved. Create one sequence and re-run this check.")
        return

    mailboxes = await client.get(f"{APOLLO_API}/email_accounts", headers=headers)
    accounts = mailboxes.json() if mailboxes.status_code < 400 else {}
    expected = (os.environ.get("APOLLO_EXPECTED_USER") or "").strip() or None

    for owner in sorted(owners):
        email = email_for_user_id(accounts, owner) or f"user id {owner}"
        print(f"  Apollo key acts as: {email}")
        print(identity_verdict(email, expected))


async def _apollo_identity_by_token() -> bool:
    """Ask Apollo who the OAuth token belongs to. True if it answered.

    The read_user_profile scope makes this work where a scoped api key gets a
    403, which is the same check finally being able to say a name.
    """
    from .apollo_oauth import OAuthFailed, OAuthNotConfigured, bearer_headers, current_token

    try:
        token = current_token()
    except (OAuthNotConfigured, OAuthFailed) as exc:
        print(f"  no OAuth token in use ({exc})")
        return False

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(f"{APOLLO_API}/users/api_profile", headers=bearer_headers(token))
    if resp.status_code >= 400:
        print(f"  an OAuth token is present but Apollo returned {resp.status_code} for the profile")
        return False

    name, email = whose_key(resp.json())
    print(f"  OAuth token acts as: {name} <{email}>")
    print(identity_verdict(email, (os.environ.get("APOLLO_EXPECTED_USER") or "").strip() or None))
    return True


async def _apollo_identity() -> None:
    """Who Apollo thinks we are. OAuth only since 4 September 2026.

    The api-key branch is gone with the key. It used to check whether the key
    belonged to Kib and print WRONG when it did not, which it always did:
    Apollo runs every api-key request as the workspace's longest-standing
    admin, so the answer was a property of the workspace rather than of the
    key, and no amount of regenerating it under the right login changed the
    result. Checking a credential that no longer exists would only report a
    reassuring absence.
    """
    if await _apollo_identity_by_token():
        return
    print("  no Apollo OAuth token, so identity cannot be checked")
    print("  run: python -m seats_prospecting.apollo_oauth login")


async def main() -> None:
    print(RULE)
    print("SEAtS prospecting preflight")
    print(RULE)

    print("\n[0] Whose Apollo account is this build acting as?")
    await _apollo_identity()

    print("\n[1] Can Apollo separate sequence create from sequence activate?")
    apollo_tools = await _list_tools(settings.APOLLO_READ)
    if isinstance(apollo_tools, list):
        creates = [t for t in apollo_tools if "sequences_create" in t or "campaigns" in t]
        activates = [
            t
            for t in apollo_tools
            if any(k in t for k in ("approve", "send_now", "add_contact_ids"))
        ]
        print(f"  create-ish tools:   {creates or 'none found'}")
        print(f"  activate-ish tools: {activates or 'none found'}")
        print(
            "  ANSWER: separable at the tool level. This build does not rely on that\n"
            "          alone. The sequence write is a local function tool that owns the\n"
            "          active=False argument, so the model cannot set a sequence active\n"
            "          even if an activate tool were reachable."
        )
    else:
        print(f"  could not check: {apollo_tools}")
        print(
            "  ANSWER: unverified from here. The control that matters does not depend\n"
            "          on this connector: the write is a local tool that owns active=False\n"
            "          and manual_email, so a step cannot send itself. Set\n"
            "          APOLLO_SEQUENCE_WRITE to anything but true to remove the\n"
            "          capability entirely and fall back to manual paste."
        )
    print(
        f"  current setting: APOLLO_SEQUENCE_WRITE="
        f"{'true' if settings.APOLLO_WRITE_ENABLED else 'false'}"
    )

    print("\n[2] Can Notion separate create from edit and delete?")
    notion_tools = await _list_tools(settings.NOTION_LEDGER_CREATE)
    if isinstance(notion_tools, list):
        print(
            "  ANSWER: this build does not depend on the connector separating them.\n"
            "          The ledger write is a local function tool with one code path:\n"
            "          POST /v1/pages. There is no update path in the module, so\n"
            "          'never edit a record' is a missing capability, not a rule."
        )
    else:
        print(f"  connector check: {notion_tools}")
        print(
            "  ANSWER: unaffected. The ledger write does not go through the connector.\n"
            "          Unset NOTION_API_KEY to drop the write entirely and fall back to\n"
            "          manual paste."
        )
    print(
        f"  ledger write configured: "
        f"{bool(os.environ.get('NOTION_API_KEY') and os.environ.get('NOTION_LEDGER_DB_ID'))}"
    )

    print("\n[3] Does the approval interruption surface the tool arguments to Kib?")
    print(
        "  ANSWER: yes. ToolApprovalItem carries .name and .arguments, and\n"
        "          runner.render_approval prints the arguments verbatim before the\n"
        "          prompt. Because the copy lives in the arguments rather than being\n"
        "          referenced from earlier in the conversation, approval is a real read."
    )

    print("\n[4] Does the handoff input filter drop tool history as well as messages?")
    print(
        "  ANSWER: no, and this changed the build. handoff_filters.remove_all_tools\n"
        "          drops tool and reasoning items but KEEPS every message item, so a\n"
        "          worker using it would inherit Kib's request and the Director's full\n"
        "          work plan. Separately, input_type does not become the worker's input\n"
        "          at all: the SDK validates it and passes it to on_handoff, and the\n"
        "          worker still sees prior history.\n"
        "          So handoff_wiring.work_order_only replaces the worker's input with\n"
        "          the validated work order and nothing else. That filter, not the\n"
        "          schema alone, is what makes the payload a boundary."
    )

    print("\n" + RULE)
    print("Connector scopes")
    print(RULE)
    specs = [
        settings.SHAREPOINT,
        settings.TEAMS,
        settings.OUTLOOK_EMAIL,
        settings.OUTLOOK_CALENDAR,
        settings.NOTION_READ,
        settings.HUBSPOT_READ,
        settings.APOLLO_READ,
    ]
    results = await asyncio.gather(*(_list_tools(s) for s in specs))
    for spec, tools in zip(specs, results):
        _report_scope(spec, tools)

    print("\n" + RULE)
    print("Grounding")
    print(RULE)
    from .schemas import ledger_motions
    from .tools.knowledge import knowledge_dir, _documents

    docs = _documents()
    print(f"\nknowledge dir: {knowledge_dir()}")
    if docs:
        for doc in docs:
            print(f"  OK       {doc.name} ({doc.stat().st_size // 1024} KB)")
    else:
        print("  MISSING  no documents. Motions cannot be checked against the")
        print("           battlecards and copy has no vocabulary grounding.")
    motions = ledger_motions()
    print(f"\nmotion vocabulary: {len(motions)} names")
    if motions == ("Unclear",):
        print("  NOTE     only 'Unclear' configured. Set LEDGER_MOTIONS from the")
        print("           battlecards, and add the same options to the Notion property.")
    else:
        print("  OK       " + ", ".join(motions[1:]))
        print("  Confirm these exactly match the Notion Motion select, or writes 400.")

    print("\n" + RULE)
    print("Agent scopes as built")
    print(RULE)
    from .agents_def import build_agents

    for name, agent in build_agents().items():
        tool_names = [getattr(t, "name", type(t).__name__) for t in agent.tools]
        handoffs = [getattr(h, "agent_name", getattr(h, "name", "?")) for h in agent.handoffs]
        print(f"\n{agent.name}")
        print(f"  tools:    {tool_names or 'none'}")
        print(f"  handoffs: {handoffs or 'none'}")


if __name__ == "__main__":
    asyncio.run(main())
