"""What seats-missed must refuse to claim."""

from __future__ import annotations

import inspect
from datetime import date

import pytest

from seats_prospecting import missed

TODAY = date(2026, 9, 10)


def row(**props):
    return {"id": "1", "properties": props}


def test_a_dead_trigger_is_refused_not_ranked():
    """The field stopped being written in April 2026. A ranking built on it
    would sort accounts by a staleness that is itself stale."""
    rows = [row(name="Anna Maria College", seats_last_intent_visit="2026-04-22",
                notes_last_contacted="2024-11-27", num_contacted_notes="37")]
    with pytest.raises(missed.TriggerStale) as exc:
        missed.missed_from_rows(rows, today=TODAY)
    assert "stopped being written" in str(exc.value)
    assert "--stale-ok" in str(exc.value)


def test_the_population_can_still_be_seen_deliberately():
    rows = [row(name="Anna Maria College", seats_last_intent_visit="2026-04-22",
                notes_last_contacted="2024-11-27", num_contacted_notes="37")]
    out = missed.missed_from_rows(rows, today=TODAY, require_fresh_trigger=False)
    assert len(out) == 1


def test_a_fresh_trigger_ranks_normally():
    rows = [row(name="Live Signal University", seats_last_intent_visit="2026-09-05",
                notes_last_contacted="2025-01-01", num_contacted_notes="4")]
    out = missed.missed_from_rows(rows, today=TODAY)
    assert out[0].tier == missed.RETURNED_AFTER_SILENCE


def test_freshness_is_measured_on_the_newest_value_not_the_average():
    age, usable = missed.freshness(
        ["2026-09-01", "2024-01-01", "2023-05-05"], TODAY)
    assert age == 9 and usable is True


def test_no_intent_values_is_unusable_rather_than_fresh():
    age, usable = missed.freshness([], TODAY)
    assert usable is False and age == -1


def test_a_form_with_no_reply_outranks_everything():
    tier, why, _ = missed.classify(last_touch=None, last_intent="2026-09-01",
                                   forms=5, touches=0, today=TODAY)
    assert tier == missed.FORM_NO_REPLY
    assert "got no reply" in why
    assert missed.TIERS.index(tier) == 0


def test_a_live_conversation_is_not_a_miss():
    tier, why, _ = missed.classify(last_touch="2026-09-05", last_intent="2026-09-01",
                                   touches=3, today=TODAY)
    assert tier == missed.LIVE
    assert "not a miss" in why


def test_a_live_conversation_never_reaches_the_list():
    rows = [row(name="Talking To Them Now", seats_last_intent_visit="2026-09-01",
                notes_last_contacted="2026-09-05", num_contacted_notes="3")]
    assert missed.missed_from_rows(rows, today=TODAY) == []


def test_a_return_visit_needs_a_real_silence():
    """Coming back two days after a call is a conversation, not a miss."""
    tier, _why, _ = missed.classify(last_touch="2026-03-01", last_intent="2026-03-03",
                                    touches=2, today=TODAY)
    assert tier == missed.DORMANT


def test_ownership_can_never_filter_a_row_out():
    """Waiting on the owner is what produced the gaps this module exists to find."""
    src = inspect.getsource(missed)
    body = "\n".join(
        line for line in src.splitlines()
        if not line.strip().startswith("#") and '"""' not in line
    )
    assert "hubspot_owner_id" in body, "the owner is carried"
    for pattern in ("owner_id ==", "owner_id !=", "owner_id in", "owner ==",
                    "if owner", "operator\": \"EQ\", \"value\": owner"):
        assert pattern not in body, pattern
    assert "hubspot_owner_id" not in missed.search_body()["filterGroups"][0]["filters"][0].values()


def test_epoch_millis_and_iso_dates_both_read():
    """HubSpot returns epoch millis from search and ISO from the reporting API."""
    assert missed._day("1732733240088") == date(2024, 11, 27)
    assert missed._day("2024-11-27T18:47:20.088Z") == date(2024, 11, 27)
    assert missed._day("") is None
    assert missed._day(None) is None


def test_the_loudest_signal_sorts_first():
    rows = [
        row(name="Dormant", seats_last_intent_visit="2026-09-01",
            notes_last_contacted="2025-06-01", num_contacted_notes="9"),
        row(name="Form no reply", seats_last_intent_visit="2026-09-02",
            num_conversion_events="5", num_contacted_notes="0"),
        row(name="Never contacted", seats_last_intent_visit="2026-09-03",
            num_contacted_notes="0"),
    ]
    out = missed.missed_from_rows(rows, today=TODAY)
    assert [m.tier for m in out] == [
        missed.FORM_NO_REPLY, missed.NEVER_CONTACTED, missed.RETURNED_AFTER_SILENCE]
