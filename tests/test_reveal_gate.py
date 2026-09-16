"""When a page view may cost a credit, and the many cases where it may not."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from seats_prospecting import reveal_gate as gate

TODAY = date(2026, 9, 10)


def write_config(tmp: Path, read_at="2026-09-10", paths=None) -> Path:
    body = {
        "read_at": read_at,
        "paths": paths if paths is not None else [
            {"path": "/demo", "label": "Demo request", "level": "high"},
            {"path": "/pricing", "label": "Pricing", "level": "high"},
            {"path": "/platform", "label": "Platform overview", "level": "medium"},
            {"path": "/case-studies", "label": "Case studies", "level": "medium"},
        ],
    }
    path = tmp / "paths.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_a_demo_view_authorises_one_reveal(tmp_path: Path):
    ok, why = gate.may_reveal(["/demo"], today=TODAY, config=write_config(tmp_path))
    assert ok is True
    assert "One reveal authorised" in why


def test_pricing_counts_too(tmp_path: Path):
    ok, _why = gate.may_reveal(["/pricing/"], today=TODAY,
                               config=write_config(tmp_path))
    assert ok is True


def test_a_blog_view_spends_nothing(tmp_path: Path):
    ok, why = gate.may_reveal(["/blog/attendance-tips"], today=TODAY,
                              config=write_config(tmp_path))
    assert ok is False
    assert "Nothing was spent" in why


def test_medium_is_not_high_enough(tmp_path: Path):
    """There is no 'high enough'. Only high."""
    for page in ("/platform", "/case-studies"):
        ok, why = gate.may_reveal([page], today=TODAY, config=write_config(tmp_path))
        assert ok is False, page
        assert "No high intent path" in why


def test_an_unknown_path_is_never_probably_fine(tmp_path: Path):
    ok, why = gate.may_reveal(["/new-page-nobody-configured"], today=TODAY,
                              config=write_config(tmp_path))
    assert ok is False
    assert "not in the config" in why


def test_a_stale_config_authorises_nothing(tmp_path: Path):
    """Path levels are editable in Apollo. An old copy must not license spend."""
    cfg = write_config(tmp_path, read_at="2026-06-01")
    ok, why = gate.may_reveal(["/demo"], today=TODAY, config=cfg)
    assert ok is False
    assert "past the" in why and "day limit" in why


def test_a_config_with_no_date_is_refused(tmp_path: Path):
    path = tmp_path / "paths.json"
    path.write_text(json.dumps({"paths": [{"path": "/demo", "level": "high"}]}),
                    encoding="utf-8")
    with pytest.raises(gate.RevealRefused) as exc:
        gate.may_reveal(["/demo"], today=TODAY, config=path)
    assert "read_at" in str(exc.value)


def test_a_missing_config_is_refused_not_defaulted(tmp_path: Path):
    with pytest.raises(gate.RevealRefused):
        gate.may_reveal(["/demo"], today=TODAY, config=tmp_path / "absent.json")


def test_an_empty_path_list_is_refused(tmp_path: Path):
    with pytest.raises(gate.RevealRefused) as exc:
        gate.may_reveal(["/demo"], today=TODAY,
                        config=write_config(tmp_path, paths=[]))
    assert "no paths" in str(exc.value)


def test_the_daily_cap_is_checked_before_the_spend(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(gate, "DISCOVERY_REVEAL_CAP_PER_DAY", 2)
    cfg = write_config(tmp_path)
    assert gate.may_reveal(["/demo"], spent_today=1, today=TODAY, config=cfg)[0] is True
    ok, why = gate.may_reveal(["/demo"], spent_today=2, today=TODAY, config=cfg)
    assert ok is False
    assert "cap is 2" in why


def test_a_visit_with_no_pages_is_not_high_intent_by_default(tmp_path: Path):
    ok, why = gate.may_reveal([], today=TODAY, config=write_config(tmp_path))
    assert ok is False
    assert "not high intent by default" in why


def test_prefix_matching_does_not_overreach(tmp_path: Path):
    """/platform-comparison is not /platform."""
    cfg = write_config(tmp_path, paths=[
        {"path": "/platform", "label": "Platform", "level": "high"}])
    assert gate.may_reveal(["/platform/pricing"], today=TODAY, config=cfg)[0] is True
    ok, why = gate.may_reveal(["/platform-comparison"], today=TODAY, config=cfg)
    assert ok is False
    assert "not in the config" in why


def test_a_full_url_with_query_string_still_matches(tmp_path: Path):
    ok, _why = gate.may_reveal(
        ["https://seatsone.com/demo/?utm_source=x#form"], today=TODAY,
        config=write_config(tmp_path))
    assert ok is True


def test_one_high_page_among_many_is_enough_and_reveals_once(tmp_path: Path):
    ok, why = gate.may_reveal(["/blog/x", "/case-studies", "/demo"], today=TODAY,
                              config=write_config(tmp_path))
    assert ok is True
    assert why.count("reveal authorised") == 1


def test_the_shipped_config_has_the_paths_we_set_in_apollo():
    """The real config, not a fixture: /demo and /pricing are high, blog is not."""
    high = gate.high_intent_paths()
    assert "/demo" in high and "/pricing" in high
    assert "/platform" not in high and "/case-studies" not in high


def test_the_path_list_itself_refuses_when_the_config_is_stale(tmp_path: Path):
    """Until 10 September this check lived only in may_reveal, so a caller
    asking for the high-intent paths got a happy answer from a stale config."""
    cfg = write_config(tmp_path, read_at="2026-06-01")
    with pytest.raises(gate.RevealRefused) as exc:
        gate.high_intent_paths(cfg, today=TODAY)
    assert "not allowed to define high intent" in str(exc.value)


def test_a_fresh_config_still_yields_its_paths(tmp_path: Path):
    assert gate.high_intent_paths(write_config(tmp_path), today=TODAY) == [
        "/demo", "/pricing"]
