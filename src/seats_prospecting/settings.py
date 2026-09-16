"""Models and connector configuration.

Model IDs and connector IDs both move. Everything here is overridable by
environment variable, and ``preflight.py`` checks the connector claims against
what the connector actually exposes rather than trusting this file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


# Reasoning work (planning, research, review) versus conversational turns.
# Start on the flagship model, drop to a lighter one only where latency matters.
MODEL_PLANNING = _env("SEATS_MODEL_PLANNING", "gpt-5.6-sol")
MODEL_RESEARCH = _env("SEATS_MODEL_RESEARCH", "gpt-5.6-sol")
MODEL_REVIEW = _env("SEATS_MODEL_REVIEW", "gpt-5.6-sol")

MAX_TURNS = int(_env("SEATS_MAX_TURNS", "25"))


@dataclass(frozen=True)
class ConnectorSpec:
    """One hosted-MCP connector, scoped by allowed_tools.

    ``allowed_tools`` is the structural control. A tool that is not on the list
    is not in the model's tool list at all, so no instruction is needed to keep
    the agent off it. Leave it empty ONLY if you have verified the connector has
    no write tools, which is rare.
    """

    label: str
    connector_id: str | None
    server_url: str | None
    auth_env: str
    allowed_tools: tuple[str, ...] = ()
    require_approval: str = "never"

    @property
    def token(self) -> str | None:
        return os.environ.get(self.auth_env)

    @property
    def configured(self) -> bool:
        return bool(self.token)


# OpenAI-hosted connectors. Confirm each connector_id against the current
# connectors list in the platform docs before first run; they are renamed
# occasionally and a wrong id fails loudly at request time.
SHAREPOINT = ConnectorSpec(
    label="sharepoint",
    connector_id="connector_sharepoint",
    server_url=None,
    auth_env="SHAREPOINT_AUTHORIZATION",
    allowed_tools=("search", "fetch"),
)

TEAMS = ConnectorSpec(
    label="microsoft_teams",
    connector_id="connector_microsoftteams",
    server_url=None,
    auth_env="TEAMS_AUTHORIZATION",
    allowed_tools=("search", "fetch"),
)

OUTLOOK_EMAIL = ConnectorSpec(
    label="outlook_email",
    connector_id="connector_outlookemail",
    server_url=None,
    auth_env="OUTLOOK_EMAIL_AUTHORIZATION",
    allowed_tools=("search", "fetch"),
)

OUTLOOK_CALENDAR = ConnectorSpec(
    label="outlook_calendar",
    connector_id="connector_outlookcalendar",
    server_url=None,
    auth_env="OUTLOOK_CALENDAR_AUTHORIZATION",
    allowed_tools=("search", "fetch"),
)

# Third-party remote MCP servers, addressed by URL.
NOTION_READ = ConnectorSpec(
    label="notion_read",
    connector_id=None,
    server_url=_env("NOTION_MCP_URL", "https://mcp.notion.com/mcp"),
    auth_env="NOTION_AUTHORIZATION",
    allowed_tools=("notion-search", "notion-fetch", "notion-query-data-sources"),
)

# Create-only. If preflight shows create and update cannot be separated on this
# connector, this spec must not be attached to any agent: see README, question 2.
NOTION_LEDGER_CREATE = ConnectorSpec(
    label="notion_ledger_create",
    connector_id=None,
    server_url=_env("NOTION_MCP_URL", "https://mcp.notion.com/mcp"),
    auth_env="NOTION_AUTHORIZATION",
    allowed_tools=("notion-create-pages",),
)

HUBSPOT_READ = ConnectorSpec(
    label="hubspot_read",
    connector_id=None,
    server_url=_env("HUBSPOT_MCP_URL", "https://mcp.hubspot.com/anthropic"),
    auth_env="HUBSPOT_AUTHORIZATION",
    allowed_tools=("search_crm_objects", "get_crm_objects", "query_crm_data"),
)

APOLLO_READ = ConnectorSpec(
    label="apollo_read",
    connector_id=None,
    server_url=_env("APOLLO_MCP_URL", "https://mcp.apollo.io/mcp"),
    auth_env="APOLLO_AUTHORIZATION",
    allowed_tools=(
        "apollo_mixed_people_api_search",
        "apollo_mixed_companies_search",
        "apollo_organizations_enrich",
        "apollo_people_match",
        "apollo_organizations_job_postings",
    ),
)

# Apollo write. Intentionally NOT a hosted connector: see tools/apollo.py for
# why the sequence write goes through a local function tool instead.
#
# On by default since 3 September 2026. Kib's rule is that a batch always lands
# in Apollo as drafts and he reads and approves it there, so a build where the
# tool is absent produces copy he has to paste by hand. Set the variable to
# anything but true to take the capability away again.
APOLLO_WRITE_ENABLED = _env("APOLLO_SEQUENCE_WRITE", "true").lower() == "true"
