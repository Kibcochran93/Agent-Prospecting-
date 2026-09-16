"""The Apollo intent pipe: what it must not return."""

from __future__ import annotations

import ast
import inspect
from datetime import date

import pytest

from seats_prospecting import apollo_intent as ai

TODAY = date(2026, 9, 10)

PAYLOAD = [
    {
        "id": "c1",
        "name": "Robin Rockey",
        "title": "Interim Director of Admissions",
        "email": "rrockey@lhup.edu",
        "contact_emails": [{"email": "rrockey@lhup.edu"}],
        "sanitized_phone": "+15704842750",
        "organization_name": "Lock Haven University",
        "organization": {"primary_domain": "lockhaven.edu", "name": "Lock Haven"},
        "website_visitor": {"website_last_visit": "2026-09-01",
                            "website_total_visits": 37, "website_intent": "low"},
    },
    {"id": "c2", "name": "No Visits", "organization": {"primary_domain": "x.edu"}},
]


def test_no_address_survives_parsing():
    visitors = ai._parse(PAYLOAD)
    assert len(visitors) == 1
    blob = repr(visitors[0]).lower()
    for leak in ("@", "rrockey", "lhup.edu", "+1570", "phone"):
        assert leak not in blob, leak
    assert not any("email" in f for f in ai.Visitor.__dataclass_fields__)


def test_a_contact_with_no_visit_is_not_a_visitor():
    assert [v.person for v in ai._parse(PAYLOAD)] == ["Robin Rockey"]


def test_nothing_in_this_module_reveals_or_enriches():
    """A reveal costs a credit. Reading the visitor summary does not."""
    tree = ast.parse(inspect.getsource(ai))
    literals = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    joined = " ".join(literals).lower()
    for forbidden in ("people/match", "reveal_personal_emails", "/enrich",
                      "bulk_match"):
        assert forbidden not in joined, forbidden


def test_an_unsupported_window_is_refused_before_any_request():
    with pytest.raises(ai.IntentUnavailable) as exc:
        import asyncio

        asyncio.run(ai.fetch(days=45))
    assert "7, 15, 30, 60 or 90" in str(exc.value)


def test_absence_is_never_reported_as_disinterest():
    assert "has not been identified" in ai.COVERAGE_NOTE
    assert "not been shown to be uninterested" in ai.COVERAGE_NOTE


def test_freshness_uses_the_newest_visit():
    visitors = ai._parse(PAYLOAD)
    age, usable = ai.freshness(visitors, TODAY)
    assert age == 9 and usable is True


def test_no_visitors_is_unusable_not_fresh():
    """An empty set must never read as a fresh 'nobody visited'."""
    age, usable = ai.freshness([], TODAY)
    assert usable is False and age == -1


def test_newest_visit_per_domain_wins():
    payload = PAYLOAD + [{
        "id": "c3", "name": "Later Visitor",
        "organization": {"primary_domain": "www.lockhaven.edu"},
        "website_visitor": {"website_last_visit": "2026-09-08",
                            "website_total_visits": 2, "website_intent": "high"},
    }]
    best = ai.by_domain(ai._parse(payload))
    assert best["lockhaven.edu"].person == "Later Visitor"


def test_live_intent_beats_the_dead_field():
    from seats_prospecting import missed

    class Live:
        last_visit = "2026-09-01"
        person = "Robin Rockey"
        title = "Interim Director of Admissions"

    rows = [{"id": "1", "properties": {
        "name": "Lock Haven University", "domain": "lockhaven.edu",
        "seats_last_intent_visit": "2026-04-09", "num_contacted_notes": "0"}}]
    out = missed.missed_from_rows(rows, today=TODAY,
                                 live_intent={"lockhaven.edu": Live()})
    assert out[0].intent_source == "apollo_live"
    assert out[0].last_intent == "2026-09-01"
    assert "Robin Rockey" in out[0].visitor


def test_a_row_with_no_live_match_keeps_its_source_named():
    from seats_prospecting import missed

    rows = [{"id": "1", "properties": {
        "name": "Old Field Only", "domain": "old.edu",
        "seats_last_intent_visit": "2026-04-09", "num_contacted_notes": "0"}}]
    out = missed.missed_from_rows(rows, today=TODAY, require_fresh_trigger=False)
    assert out[0].intent_source == "hubspot_field"


# --- the high-intent read (10 September) ----------------------------------


def test_the_page_filter_only_appears_when_asked_for():
    assert "website_visitors_people_pages" not in ai.search_body()
    body = ai.search_body(pages_viewed=["/demo", "/pricing"])
    assert body["website_visitors_people_pages"] == ["/demo", "/pricing"]


def test_high_intent_paths_come_from_the_gate_not_from_here():
    """One definition of high intent, in the tracker config."""
    src = inspect.getsource(ai.fetch_high_intent)
    assert "high_intent_paths" in src
    for hardcoded in ("/demo", "/pricing", "/pilot", "/contact-us"):
        assert hardcoded not in src, hardcoded


def test_a_stale_gate_config_blocks_the_high_intent_search(tmp_path, monkeypatch):
    """A search that claims to find high-intent visitors using out-of-date path
    levels is worse than no search."""
    import asyncio
    import json as _json

    cfg = tmp_path / "paths.json"
    cfg.write_text(_json.dumps({
        "read_at": "2026-01-01",
        "paths": [{"path": "/demo", "label": "Demo", "level": "high"}]}),
        encoding="utf-8")
    monkeypatch.setenv("SEATS_INTENT_PATHS", str(cfg))
    with pytest.raises(ai.IntentUnavailable) as exc:
        asyncio.run(ai.fetch_high_intent())
    assert "not usable" in str(exc.value)
