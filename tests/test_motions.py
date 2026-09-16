"""The motion vocabulary matches the Notion select, exactly."""

from __future__ import annotations

import os

from seats_prospecting.schemas import LedgerRecord, ledger_motions

# Read off the live Notion Motion property after it was updated.
LIVE_MOTION_OPTIONS = {
    "Unclear",
    "M1 Schedule Replacement",
    "M2 Attendance & Evidence",
    "M3 Student Success / Retention",
    "M4 Student Care / Coordinated Care",
    "M5 Compliance & Audit Evidence",
    "M6 Smart Campus / Space Utilization",
    "M7 Education AI / Data Engine",
    "M9 US Health Sciences clock-hour attendance and Title IV evidence",
}


def _record(**over):
    base = dict(
        institution="Florida Atlantic University",
        motion="Unclear",
        segment="Public research university; 30,000-39,999; Southeast",
        source_agent="Prospect Briefing",
        verified_through="2026-08-01",
        facts_carried_over=["fact, source dated 2026-08-01"],
        not_verified=["nothing else confirmed"],
        what_this_suggests="Confirm ownership before positioning.",
        account_owner="unassigned",
        outreach_decision="Not required",
    )
    base.update(over)
    return LedgerRecord(**base)


def test_configured_vocabulary_matches_the_notion_property():
    configured = set(ledger_motions())
    assert configured == LIVE_MOTION_OPTIONS, (
        "LEDGER_MOTIONS and the Notion Motion select have drifted; "
        f"only in env={configured - LIVE_MOTION_OPTIONS}, "
        f"only in Notion={LIVE_MOTION_OPTIONS - configured}"
    )


def test_every_configured_motion_is_writable():
    for motion in ledger_motions():
        assert _record(motion=motion).motion == motion


def test_a_composed_phrase_still_falls_back():
    r = _record(motion="Clarify ownership of attendance and engagement data")
    assert r.motion == "Unclear"
    assert r.motion_hypothesis == "Clarify ownership of attendance and engagement data"


def test_env_is_the_source_of_truth(monkeypatch):
    monkeypatch.setenv("LEDGER_MOTIONS", "Unclear,M1 Schedule Replacement")
    assert set(ledger_motions()) == {"Unclear", "M1 Schedule Replacement"}
    assert _record(motion="M2 Attendance & Evidence").motion == "Unclear"


def test_env_actually_carries_the_battlecard_motions():
    raw = os.environ.get("LEDGER_MOTIONS", "")
    assert "M2 Attendance & Evidence" in raw, ".env is missing the playbook motions"
    assert "M8" not in raw, "there is no M8 in the Sales_Motions sheet"
