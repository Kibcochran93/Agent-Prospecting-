"""The ledger write, tested without a Notion token.

The failure this guards against is a 400 on every write because a property
name in the payload does not match a property name in the database. The eight
names below were read off the live SE Prospecting Ledger after it was created
and a schema-validation record was accepted into it.
"""

from __future__ import annotations

import pydantic
import pytest

from seats_prospecting.schemas import LedgerRecord
from seats_prospecting.tools.notion_ledger import (
    LEDGER_PROPERTIES,
    page_payload,
)

# Read off the live database, not copied from the code under test.
LIVE_LEDGER_SCHEMA = {
    "Name": "title",
    "Institution": "rich_text",
    "Motion": "select",
    "Segment": "rich_text",
    "Status": "select",
    "Created": "date",
    "Source agent": "select",
    "Verified through": "date",
    "Account owner": "rich_text",
    "Outreach decision": "select",
}

DB_ID = "35542820-bf93-4267-b96e-17618d8d308b"


@pytest.fixture
def record() -> LedgerRecord:
    return LedgerRecord(
        institution="Ivy Tech Community College",
        motion="Unclear",
        segment="community college, multi-campus, Indiana",
        source_agent="Prospect Briefing",
        verified_through="2026-08-14",
        facts_carried_over=[
            "Registrar named on the institution site, source date 2026-08-14"
        ],
        not_verified=["Which office owns clock-hour attendance day to day"],
        constraints=["No prior outreach"],
        what_this_suggests="Worth a discovery call before any batch.",
        account_owner="unassigned",
        outreach_decision="Not required",
    )


def test_payload_property_names_match_the_live_database(record):
    sent = set(page_payload(record, DB_ID)["properties"])
    assert sent == set(LIVE_LEDGER_SCHEMA), (
        "payload property names drifted from the live ledger schema; "
        f"extra={sent - set(LIVE_LEDGER_SCHEMA)}, "
        f"missing={set(LIVE_LEDGER_SCHEMA) - sent}"
    )


def test_declared_constant_matches_the_live_database():
    assert set(LEDGER_PROPERTIES) == set(LIVE_LEDGER_SCHEMA)


def test_every_property_uses_the_type_the_database_expects(record):
    props = page_payload(record, DB_ID)["properties"]
    for name, expected in LIVE_LEDGER_SCHEMA.items():
        assert expected in props[name], (
            f"{name} is sent as {list(props[name])}, database expects {expected}"
        )


def test_status_is_always_draft_and_never_model_supplied(record):
    # LedgerRecord has no status field, so there is nothing to override.
    assert "status" not in LedgerRecord.model_fields
    assert page_payload(record, DB_ID)["properties"]["Status"]["select"]["name"] == "Draft"


def test_verified_through_drives_both_dates_not_today(record):
    props = page_payload(record, DB_ID)["properties"]
    assert props["Verified through"]["date"]["start"] == "2026-08-14"
    assert props["Created"]["date"]["start"] == "2026-08-14"


def test_title_is_institution_then_date(record):
    title = page_payload(record, DB_ID)["properties"]["Name"]["title"][0]["text"]["content"]
    assert title == "Ivy Tech Community College, 2026-08-14"


def test_body_carries_all_four_sections_and_no_copy(record):
    assert record.motion_hypothesis is None, "fixture should use a real playbook motion"
    blocks = page_payload(record, DB_ID)["children"]
    headings = [
        b["heading_2"]["rich_text"][0]["text"]["content"]
        for b in blocks
        if b["type"] == "heading_2"
    ]
    assert headings == [
        "Facts carried over",
        "Not verified",
        "Constraints",
        "What this suggests",
    ]


def test_empty_constraints_still_produce_a_stated_section(record):
    record.constraints = []
    blocks = page_payload(record, DB_ID)["children"]
    text = " ".join(
        rt["text"]["content"]
        for b in blocks
        for rt in b.get(b["type"], {}).get("rich_text", [])
    )
    assert "None identified." in text


def test_parent_is_the_configured_database(record):
    assert page_payload(record, DB_ID)["parent"] == {"database_id": DB_ID}


# --- outbox mode ----------------------------------------------------------
# Default write path: the worker holds no Notion capability, writes a validated
# JSON envelope, and a relay creates the page later.


def test_outbox_envelope_hash_covers_the_record(record):
    from seats_prospecting.tools.notion_ledger import outbox_envelope, verify_envelope

    envelope = outbox_envelope(record)
    assert verify_envelope(envelope)

    # A paraphrase of any field breaks the hash, which is the whole point.
    envelope["record"]["what_this_suggests"] = "Worth a call soon."
    assert not verify_envelope(envelope)


def test_outbox_envelope_pins_status_to_draft(record):
    from seats_prospecting.tools.notion_ledger import outbox_envelope

    assert outbox_envelope(record)["status"] == "Draft"


def test_outbox_envelope_states_that_its_prose_is_not_instructions(record):
    from seats_prospecting.tools.notion_ledger import outbox_envelope

    policy = outbox_envelope(record)["relay"]["instruction_policy"]
    assert "never directions to the relay" in policy


def test_outbox_write_never_overwrites(record, tmp_path, monkeypatch):
    from seats_prospecting.tools.notion_ledger import write_outbox_record

    monkeypatch.setenv("LEDGER_OUTBOX_DIR", str(tmp_path))
    first = write_outbox_record(record)
    second = write_outbox_record(record)
    assert first != second
    assert first.exists() and second.exists()


def test_outbox_filename_names_the_agent_and_institution(record, tmp_path, monkeypatch):
    from seats_prospecting.tools.notion_ledger import write_outbox_record

    monkeypatch.setenv("LEDGER_OUTBOX_DIR", str(tmp_path))
    name = write_outbox_record(record).name
    assert "prospect-briefing" in name
    assert "ivy-tech-community-college" in name


def test_outbox_record_round_trips_through_the_schema(record, tmp_path, monkeypatch):
    import json

    from seats_prospecting.tools.notion_ledger import write_outbox_record

    monkeypatch.setenv("LEDGER_OUTBOX_DIR", str(tmp_path))
    envelope = json.loads(write_outbox_record(record).read_text(encoding="utf-8"))

    # extra="forbid" means a relay cannot smuggle a field the schema forbids.
    reloaded = LedgerRecord(**envelope["record"])
    assert reloaded == record

    with pytest.raises(pydantic.ValidationError):
        LedgerRecord(**{**envelope["record"], "status": "Open"})


def test_outbox_is_the_default_mode(monkeypatch):
    from seats_prospecting.tools import notion_ledger

    monkeypatch.delenv("LEDGER_WRITE_MODE", raising=False)
    assert notion_ledger._mode() == "outbox"


# --- motion vocabulary ----------------------------------------------------
# Found in the first live relay: Notion 400s on a select value that is not
# already an option on the property, so a free-text motion breaks every write.


def _with_motion(record: LedgerRecord, motion: str, **extra) -> LedgerRecord:
    fields = record.model_dump()
    fields.pop("motion_hypothesis", None)
    return LedgerRecord(**{**fields, "motion": motion, **extra})


def test_unknown_motion_never_reaches_the_select(record):
    record_with_prose = _with_motion(record, "Clarify ownership of attendance data")
    assert record_with_prose.motion == "Unclear"
    assert record_with_prose.motion_hypothesis == "Clarify ownership of attendance data"

    sent = page_payload(record_with_prose, DB_ID)["properties"]["Motion"]["select"]["name"]
    assert sent == "Unclear"


def test_a_known_motion_survives(monkeypatch, record):
    monkeypatch.setenv("LEDGER_MOTIONS", "Unclear,Clock-hour compliance,Retention")
    kept = _with_motion(record, "Clock-hour compliance")
    assert kept.motion == "Clock-hour compliance"
    assert kept.motion_hypothesis is None


def test_hypothesis_is_preserved_in_the_page_body(record):
    prose = _with_motion(record, "Clarify ownership of attendance data")
    blocks = page_payload(prose, DB_ID)["children"]
    headings = [
        b["heading_2"]["rich_text"][0]["text"]["content"]
        for b in blocks
        if b["type"] == "heading_2"
    ]
    assert headings[0] == "Motion hypothesis"
    text = " ".join(
        rt["text"]["content"]
        for b in blocks
        for rt in b.get(b["type"], {}).get("rich_text", [])
    )
    assert "Clarify ownership of attendance data" in text


def test_an_explicit_hypothesis_is_not_overwritten(record):
    both = _with_motion(
        record,
        "Some invented motion",
        motion_hypothesis="The one the agent actually meant",
    )
    assert both.motion == "Unclear"
    assert both.motion_hypothesis == "The one the agent actually meant"
