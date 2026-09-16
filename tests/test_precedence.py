"""The canonical layer outranks the local playbook, and the agents must be told so.

These tests guard a governance fact, not a code path. SEAtS keeps a canonical GTM
layer on SharePoint that the agents cannot read. Two of its documents name a thing
the local US playbook forbids. The rule for that situation is written down in the
canonical governance file: raise a review item, do not silently fork the truth.

Each test below asserts that something stayed put. The failure mode they exist to
catch is someone resolving an open conflict quietly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PRECEDENCE = ROOT / "knowledge" / "00-canonical-precedence.md"
LANGUAGE = ROOT / "src" / "seats_prospecting" / "prompts" / "_shared_us_language.md"


@pytest.fixture(scope="module")
def precedence() -> str:
    if not PRECEDENCE.exists():
        pytest.fail(
            "knowledge/00-canonical-precedence.md is gone. It is the only record of "
            "which documents outrank the local playbook and which conflicts are open."
        )
    return PRECEDENCE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def language() -> str:
    return LANGUAGE.read_text(encoding="utf-8")


def test_the_playbook_no_longer_claims_to_outrank_everything(language):
    """It is authoritative for US execution, not over the canonical layer."""
    assert "The authoritative source." not in language, (
        "the US playbook is claiming top precedence again; the canonical GTM layer "
        "sits above it"
    )


def test_the_prompt_points_at_the_precedence_file(language):
    assert "precedence" in language.lower(), (
        "the grounding section no longer tells the agent that a precedence file exists"
    )


def test_the_m9_placement_guardrail_is_still_in_force(language):
    """Unresolved means unresolved. The canonical layer disagrees; Kib decides."""
    assert "never pitch clinical placement or rotation management" in language, (
        "the M9 placement guardrail was removed. The canonical layer does name "
        "placement and rotation visibility, but review item 1 is still open and the "
        "guardrail holds until Kib decides."
    )


def test_the_m9_conflict_is_declared_rather_than_hidden(language):
    assert "under review" in language, (
        "the M9 guardrail is in force but no longer says the canonical layer "
        "disagrees with it; an agent would present a contested rule as settled"
    )


def test_precedence_file_records_the_open_items(precedence):
    for item in (
        "Open review item 1",
        "Open review item 2",
        "Open review item 3",
    ):
        assert item in precedence, f"{item} is missing from the precedence file"


def test_precedence_file_does_not_claim_sharepoint_is_wired(precedence):
    assert "SHAREPOINT_AUTHORIZATION` is unset" in precedence, (
        "the precedence file no longer states that the agents cannot read the "
        "canonical layer. If that changed, the claim should change with it."
    )


def test_motion_vocabulary_confirmation_is_recorded(precedence):
    assert "No change required" in precedence, (
        "the note confirming M1-M7 match the canonical motions is gone; without it "
        "the vocabulary looks unverified again"
    )


def test_precedence_file_is_reachable_through_the_knowledge_tool():
    """The agents read this folder through one tool. A file it cannot enumerate is a
    file the agents will never see."""
    from seats_prospecting.tools import knowledge

    names = [p.name for p in knowledge._documents()]
    assert "00-canonical-precedence.md" in names, (
        f"the knowledge tool does not enumerate the precedence file; it sees {names}"
    )


def test_precedence_file_sorts_ahead_of_the_playbook():
    """It says read this first. The listing should not bury it."""
    from seats_prospecting.tools import knowledge

    names = [p.name for p in knowledge._documents()]
    assert names[0] == "00-canonical-precedence.md", (
        f"the precedence file is not first in the listing; order is {names}"
    )
