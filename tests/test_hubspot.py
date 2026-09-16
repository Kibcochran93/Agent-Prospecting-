"""HubSpot is read only and reaches the Director alone.

The design puts pipeline state in exactly one place. These tests assert the
module has no write path and that no worker can see it, because a worker with
CRM access is how internal deal context ends up in an outbound email.
"""

from __future__ import annotations

import pytest

from seats_prospecting.agents_def import build_agents
from seats_prospecting.tools import hubspot

WRITE_SHAPED = ("create", "update", "delete", "write", "patch", "post_", "archive", "merge", "associate")


@pytest.fixture(scope="module")
def network():
    return build_agents()


def tool_names(agent) -> list[str]:
    return [getattr(t, "name", type(t).__name__) for t in agent.tools]


def test_module_exposes_no_write_function():
    for name in dir(hubspot):
        if name.startswith("_"):
            continue
        assert not any(name.lower().startswith(w) for w in WRITE_SHAPED), (
            f"hubspot module gained a write-shaped function: {name}"
        )


def test_module_calls_no_write_endpoint():
    source = (hubspot.__file__ or "")
    text = open(source, encoding="utf-8").read()
    # The only HTTP verbs used should be get and post-for-search/batch-read.
    assert "client.patch" not in text
    assert "client.delete" not in text
    assert "client.put" not in text
    # Every post must be a search or a batch read, never an object create.
    posts = [line for line in text.splitlines() if "client.post(" in line]
    assert posts, "expected at least one search call"
    for i, line in enumerate(text.splitlines()):
        if "client.post(" in line:
            following = "\n".join(text.splitlines()[i : i + 4])
            assert "/search" in following or "/batch/read" in following, (
                f"post call that is neither a search nor a batch read: {following}"
            )


def test_account_context_holds_the_pipeline_tools(network):
    """These moved off the Director in phase 2, 4 September 2026.

    The argument is not that the Director misused them. It is that reading the
    evidence and deciding what to do about it should not happen in the same
    agent, because the agent that gathers its own evidence is the one that
    talks itself past a stop.
    """
    names = tool_names(network["context"])
    assert "hubspot_find_account" in names
    assert "hubspot_account_notes" in names


def test_the_director_holds_none_of_them(network):
    names = tool_names(network["director"])
    assert not [n for n in names if "hubspot" in n.lower()]


def test_deals_and_owners_follow_the_flag_in_both_directions(monkeypatch):
    """Gated because HubSpot 403s until two separate things are true.

    Granting the API scope is not enough. The private app token inherits the
    permissions of the HubSpot user who created it, so deals also needs the
    deals-read USER permission, and a scope change has to be committed in the
    HubSpot UI before it takes effect. A tool that always 403s costs a turn and
    teaches nothing, so it stays unattached until the flag says otherwise.
    """
    import importlib

    from seats_prospecting import agents_def, connectors

    def context_tool_names() -> list[str]:
        importlib.reload(connectors)
        importlib.reload(agents_def)
        return [getattr(t, "name", "") for t in agents_def.build_agents()["context"].tools]

    monkeypatch.setenv("HUBSPOT_DEALS_ENABLED", "true")
    on = context_tool_names()
    assert "hubspot_account_deals" in on
    assert "hubspot_owner" in on

    monkeypatch.setenv("HUBSPOT_DEALS_ENABLED", "false")
    off = context_tool_names()
    assert "hubspot_account_deals" not in off
    assert "hubspot_owner" not in off
    # The always-available pair is unaffected by the flag.
    assert "hubspot_find_account" in off
    assert "hubspot_account_notes" in off


def test_notes_are_readable_by_account_context_only(network):
    assert "hubspot_account_notes" in tool_names(network["context"])
    for worker in ("director", "briefing", "campaign", "reviewer"):
        assert "hubspot_account_notes" not in tool_names(network[worker]), (
            "note bodies carry internal commentary and must not reach an agent that "
            "writes outbound copy"
        )


def test_note_output_is_labelled_internal():
    text = open(hubspot.__file__, encoding="utf-8").read()
    assert "Never quote this to a prospect and " in text
    assert "never put note text in a work order" in text


def test_html_is_stripped_from_note_bodies():
    dirty = '<div>Spoke to the <b>registrar</b>&nbsp;in March&amp;April</div>'
    clean = hubspot._strip_html(dirty)
    assert "<" not in clean and ">" not in clean
    assert "registrar" in clean
    assert "&nbsp;" not in clean
    assert "March&April" in clean


def test_note_limit_is_bounded():
    # A model asking for 500 notes must not get 500 notes.
    import inspect

    src = inspect.getsource(hubspot.hubspot_account_notes.on_invoke_tool) if False else \
        open(hubspot.__file__, encoding="utf-8").read()
    assert "min(int(limit or 5), 10)" in src


def test_multiple_matches_warn_about_duplicates():
    text = open(hubspot.__file__, encoding="utf-8").read()
    assert "MORE THAN ONE MATCH" in text
    assert "no domain and no recent activity is usually" in text


def test_company_output_surfaces_the_owner(monkeypatch):
    """Defect found live: the owner id was fetched but never printed, so the
    Director reported the account owner as unresolvable.

    Asserts behaviour rather than source text. The first version of this test
    grepped for the exact f-string and broke the moment the wording changed,
    which taught nothing about whether the owner still reaches the Director.
    """
    assert "hubspot_owner_id" in hubspot.COMPANY_PROPS

    monkeypatch.setenv("KIB_HUBSPOT_OWNER_ID", "77758126")
    # "(yours)" not "yours": the colleague warning contains the phrase "not yours".
    assert "(yours)" in hubspot._owner_flag("77758126")
    assert "unassigned" in hubspot._owner_flag(None)

    someone_else = hubspot._owner_flag("1399544781")
    assert "1399544781" in someone_else
    assert "ANOTHER COLLEAGUE OWNS THIS ACCOUNT" in someone_else
    assert "owner review" in someone_else
    assert "Kib's yes or no decision" in someone_else


def test_an_unset_operator_id_does_not_silently_claim_ownership(monkeypatch):
    """With no operator id configured, every owned account must read as someone
    else's rather than defaulting to yours."""
    monkeypatch.delenv("KIB_HUBSPOT_OWNER_ID", raising=False)
    flagged = hubspot._owner_flag("77758126")
    assert "(yours)" not in flagged
    assert "ANOTHER COLLEAGUE OWNS THIS ACCOUNT" in flagged


def test_note_date_discrepancy_is_explained():
    """The company's last-note property can post-date the newest readable note."""
    text = open(hubspot.__file__, encoding="utf-8").read()
    assert "Newest readable note" in text
    assert "not treating the property as the last" in text or "rather than treating the property" in text
