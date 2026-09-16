"""The account owner is recorded on every record, and no longer blocks one.

Kib's rule until 3 September 2026 was that an account someone else owns is
flagged and held at pending review. His revision that evening: he can send from
several mailboxes, so ownership is a fact he wants in front of him rather than a
gate. The owner field stays required; the forcing came off.

What did not change: Approved and Declined are decisions he records in the
ledger, and an agent that writes either one is a finding. That is still a schema
constraint rather than a prompt rule.
"""

from __future__ import annotations

import pytest

from seats_prospecting.schemas import LedgerRecord, operator_name
from seats_prospecting.tools.notion_ledger import LEDGER_PROPERTIES, page_payload

DB_ID = "35542820-bf93-4267-b96e-17618d8d308b"

# Read off the live Notion property after it was added.
LIVE_DECISIONS = {"Pending owner review", "Approved", "Declined", "Not required"}


def _record(**over) -> LedgerRecord:
    base = dict(
        institution="Lyon College",
        motion="Unclear",
        segment="Private nonprofit college; under 1,000; Arkansas",
        source_agent="Prospect Briefing",
        verified_through="2025-09-09",
        facts_carried_over=["fact, source dated 2025-09-09"],
        not_verified=["nothing else confirmed"],
        what_this_suggests="Route through the account owner first.",
        account_owner="Cal O'Donovan",
    )
    base.update(over)
    return LedgerRecord(**base)


def test_the_default_is_still_pending():
    r = _record(account_owner="Cal O'Donovan")
    assert r.outreach_decision == "Pending owner review"


def test_a_colleagues_account_is_recorded_not_blocked():
    """The revision, 3 September. The owner is named on the record either way."""
    r = _record(account_owner="Cal O'Donovan", outreach_decision="Not required")
    assert r.outreach_decision == "Not required"
    assert r.account_owner == "Cal O'Donovan"


def test_an_agent_cannot_write_approved():
    r = _record(account_owner="Cal O'Donovan", outreach_decision="Approved")
    assert r.outreach_decision == "Pending owner review", (
        "an agent must not be able to record its own outreach approval"
    )


def test_an_agent_cannot_write_declined_either():
    """Declined is also Kib's call: an agent closing an account off is a decision too."""
    r = _record(account_owner="unassigned", outreach_decision="Declined")
    assert r.outreach_decision == "Pending owner review"


def test_an_agent_cannot_approve_even_on_kibs_own_account():
    r = _record(account_owner=operator_name(), outreach_decision="Approved")
    assert r.outreach_decision == "Pending owner review"


@pytest.mark.parametrize("owner", ["unassigned", "not in HubSpot", "None", ""])
def test_unowned_accounts_may_be_marked_not_required(owner):
    r = _record(account_owner=owner, outreach_decision="Not required")
    assert r.outreach_decision == "Not required"


@pytest.mark.parametrize("owner", ["unknown", "UNKNOWN", "Cal O'Donovan", "unassigned"])
def test_the_owner_never_decides_the_record_any_more(owner):
    """Whatever the owner value, it no longer rewrites the decision. It is still
    on the record, which is the whole point of keeping the field."""
    r = _record(account_owner=owner, outreach_decision="Not required")
    assert r.outreach_decision == "Not required"
    assert r.account_owner == owner


def test_kibs_own_account_may_be_marked_not_required():
    r = _record(account_owner=operator_name(), outreach_decision="Not required")
    assert r.outreach_decision == "Not required"


def test_owner_matching_ignores_case():
    r = _record(account_owner=operator_name().lower(), outreach_decision="Not required")
    assert r.outreach_decision == "Not required"


def test_an_agent_still_cannot_record_kibs_decision():
    """The constraint that survived the revision."""
    for value in ("Approved", "Declined"):
        r = _record(account_owner="Miguel Pescador", outreach_decision=value)
        assert r.outreach_decision == "Pending owner review"


def test_the_owner_is_a_required_field():
    import pydantic

    fields = dict(
        institution="X", motion="Unclear", segment="s", source_agent="Prospect Briefing",
        verified_through="2026-01-01", facts_carried_over=["f"], not_verified=["n"],
        what_this_suggests="w",
    )
    with pytest.raises(pydantic.ValidationError):
        LedgerRecord(**fields)


def test_the_default_is_pending_not_cleared():
    r = _record(account_owner="unknown")
    assert r.outreach_decision == "Pending owner review", (
        "the safe default is review, not clearance"
    )


# --- the ledger carries it ------------------------------------------------


def test_both_fields_reach_the_notion_payload():
    props = page_payload(_record(), DB_ID)["properties"]
    assert props["Account owner"]["rich_text"][0]["text"]["content"] == "Cal O'Donovan"
    assert props["Outreach decision"]["select"]["name"] == "Pending owner review"


def test_the_declared_property_list_includes_them():
    assert "Account owner" in LEDGER_PROPERTIES
    assert "Outreach decision" in LEDGER_PROPERTIES


def test_the_decision_value_is_one_the_live_property_accepts():
    sent = page_payload(_record(), DB_ID)["properties"]["Outreach decision"]["select"]["name"]
    assert sent in LIVE_DECISIONS


def test_a_pending_record_says_so_at_the_top_of_the_page():
    blocks = page_payload(_record(account_owner="Cal O'Donovan"), DB_ID)["children"]
    first = blocks[0]
    assert first["type"] == "heading_2"
    assert first["heading_2"]["rich_text"][0]["text"]["content"] == "Owner review required"
    text = " ".join(
        rt["text"]["content"]
        for b in blocks
        for rt in b.get(b["type"], {}).get("rich_text", [])
    )
    assert "Cal O'Donovan" in text
    assert "not cleared to send" in text


def test_a_not_required_record_has_no_review_banner():
    blocks = page_payload(
        _record(account_owner="unassigned", outreach_decision="Not required"), DB_ID
    )["children"]
    assert blocks[0]["heading_2"]["rich_text"][0]["text"]["content"] != "Owner review required"
