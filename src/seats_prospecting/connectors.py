"""Hosted MCP tool builders.

Every agent's system access is assembled here so the scope map in the configs
and the scope map in the code are the same object. An agent gets a connector by
being handed the tool; there is no instruction anywhere telling an agent not to
use a system, because a system it should not touch is not in its tool list.
"""

from __future__ import annotations

from agents import HostedMCPTool, WebSearchTool

from . import settings
from .settings import ConnectorSpec


class ConnectorNotConfigured(RuntimeError):
    pass


def build(spec: ConnectorSpec, *, required: bool = False) -> HostedMCPTool | None:
    """Turn a spec into a hosted MCP tool, or None if it has no credential.

    Returning None rather than raising is deliberate: the system should start
    with three connectors wired and seven missing, and say which are missing,
    instead of refusing to start. Pass ``required=True`` where a missing
    connector makes the agent pointless.
    """
    if not spec.configured:
        if required:
            raise ConnectorNotConfigured(
                f"{spec.label} needs {spec.auth_env} set. "
                "Run `python -m seats_prospecting.preflight` to see what is wired."
            )
        return None

    tool_config: dict[str, object] = {
        "type": "mcp",
        "server_label": spec.label,
        "authorization": spec.token,
        "require_approval": spec.require_approval,
    }
    if spec.connector_id:
        tool_config["connector_id"] = spec.connector_id
    else:
        tool_config["server_url"] = spec.server_url
    if spec.allowed_tools:
        # Static tool filtering. This is the read-only boundary.
        tool_config["allowed_tools"] = list(spec.allowed_tools)

    return HostedMCPTool(tool_config=tool_config)


def compact(*tools: object) -> list[object]:
    return [t for t in tools if t is not None]


def web_search() -> WebSearchTool:
    return WebSearchTool()


def hubspot_tools() -> list[object]:
    """Pipeline state. The Director only, and read only.

    The design is explicit that neither worker touches HubSpot, so this is the
    single point where pipeline state enters the system. Company and deal level
    only; no contact records, which keeps personal data out of planning.
    """
    import os

    from .tools.hubspot import (
        hubspot_account_deals,
        hubspot_account_notes,
        hubspot_find_account,
        hubspot_owner,
    )

    tools = [hubspot_find_account, hubspot_account_notes]

    # Deals and owners are off by default, and the reason is worth recording.
    # Granting the app crm.objects.deals.read is not enough: a private app token
    # inherits the permissions of the HubSpot USER who created it, and HubSpot
    # answers "you do not have permissions to view_schema object type DEAL
    # (requires one of [deals-read])" regardless of the scope. deals-read is a
    # user permission, not an API scope. Fix it in Settings, Users & Teams, then
    # set HUBSPOT_DEALS_ENABLED=true. Attaching a tool that always 403s just
    # spends a turn to learn nothing.
    if os.environ.get("HUBSPOT_DEALS_ENABLED", "false").strip().lower() == "true":
        tools.extend([hubspot_account_deals, hubspot_owner])

    return tools


def director_tools() -> list[object]:
    """Orchestration only. No customer relationship management (CRM) at all.

    Phase 2, 4 September 2026, and Kib's decision. The Director used to hold
    every HubSpot read in the system, and the argument for taking them away is
    that reading the evidence and deciding what to do about it should not sit
    in the same agent: an agent that gathers its own evidence is the one that
    talks itself past a stop.

    It does not lose the facts, only the gathering. The account context verdict
    reaches the run as application state stamped before the run starts, and the
    dispatch refuses without one. So the Director now decides on evidence it
    could not have chosen, which is the property that matters.

    No Apollo either, read included: a planner who can size a list starts
    building one.
    """
    return compact(
        build(settings.SHAREPOINT),
        build(settings.NOTION_READ),
        build(settings.TEAMS),
        build(settings.OUTLOOK_CALENDAR),
    )


def knowledge_tools() -> list[object]:
    """Local grounding, standing in for the SharePoint read while it is unwired.

    Read only by construction: the module has no write function. Attached to
    the two workers and to the reviewer, which needs the playbook to check
    motion fit. Not attached to the director, which plans rather than researches.
    """
    from .tools.knowledge import list_knowledge, read_knowledge, search_knowledge

    return [list_knowledge, search_knowledge, read_knowledge]


def apollo_read_tools() -> list[object]:
    """Apollo read for the two workers. Never the Director.

    The design removes Apollo from the Director entirely, read included, on the
    grounds that a planner who can size a list starts building one.
    """
    from .tools.apollo import apollo_find_org, apollo_find_roles, apollo_reveal_person

    return [apollo_find_org, apollo_find_roles, apollo_reveal_person]


def context_tools() -> list[object]:
    """Account Context: read what we already own, and nothing else.

    No web search. That absence is the control: an agent asked "have we been
    here before" that can also search the web will answer from the web, because
    the web always has something and the customer relationship management (CRM)
    often does not. What comes back then is research, produced before anyone
    decided the account was worth researching, which is the spend this agent
    exists to prevent.

    No write tool of any kind, ledger included. The verdict is recorded by the
    caller in phase 1, not by the agent that formed it.

    Contacts are readable here and nowhere else. `tools/hubspot.py` kept contact
    records out of the system entirely while HubSpot belonged to the Director,
    on the grounds that a planner does not need to know who we have emailed.
    This agent's whole job is knowing that, and all four misses on 4 September
    were contact-shaped.
    """
    from .tools.apollo import apollo_contact_status, apollo_find_org
    from .tools.hubspot import (
        hubspot_account_notes,
        hubspot_find_account,
        hubspot_find_contact,
    )

    tools: list[object] = [
        hubspot_find_account,
        hubspot_find_contact,
        hubspot_account_notes,
        apollo_find_org,
        apollo_contact_status,
    ]

    import os

    if os.environ.get("HUBSPOT_DEALS_ENABLED", "false").strip().lower() == "true":
        from .tools.hubspot import hubspot_account_deals, hubspot_owner

        tools.extend([hubspot_account_deals, hubspot_owner])

    return compact(
        *tools,
        build(settings.HUBSPOT_READ),
        # The ledger read, so a prior briefing on this institution is visible.
        # The Reviewer is denied this same connector on purpose and the reasons
        # do not collide: prior acceptance is not evidence about an artifact,
        # and prior work is exactly the evidence about an account.
        build(settings.NOTION_READ),
    )


def briefing_tools() -> list[object]:
    from .tools.notion_ledger import create_ledger_record

    return compact(
        web_search(),
        *knowledge_tools(),
        *apollo_read_tools(),
        build(settings.APOLLO_READ),
        build(settings.SHAREPOINT),
        build(settings.TEAMS),
        build(settings.OUTLOOK_CALENDAR),
        build(settings.OUTLOOK_EMAIL),
        create_ledger_record,
    )


def campaign_tools() -> list[object]:
    from .tools.apollo_enrol import add_contact_to_drafts
    from .tools.apollo_write import create_manual_email_drafts
    from .tools.notion_ledger import create_ledger_record

    tools = compact(
        web_search(),
        *knowledge_tools(),
        *apollo_read_tools(),
        build(settings.APOLLO_READ),
        build(settings.SHAREPOINT),
        build(settings.TEAMS),
        create_ledger_record,
    )
    # The Apollo write scope. On by default since 3 September: a batch lands in
    # Apollo as drafts and Kib reads and approves it there. Set
    # APOLLO_SEQUENCE_WRITE to anything but true and this agent holds neither
    # tool, outputting copy for manual paste instead.
    #
    # Enrolment rides with it, added 4 September. A draft nobody is enrolled in
    # is addressed to nobody, which is what the first batch looked like. Both
    # tools are on the Campaign Builder and nowhere else.
    if settings.APOLLO_WRITE_ENABLED:
        tools.append(create_manual_email_drafts)
        tools.append(add_contact_to_drafts)
    return tools


def reviewer_tools() -> list[object]:
    """SharePoint and web only. No Notion read in particular: ledger records
    would tell the reviewer what was already accepted, and prior acceptance is
    not evidence about the artifact in front of it."""
    return compact(
        build(settings.SHAREPOINT),
        *knowledge_tools(),
        web_search(),
    )
