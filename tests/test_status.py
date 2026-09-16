"""seats-status: outstanding work, read from the systems, never guessed.

The command exists because on 8 September 2026 HANDOFF.md said zero emails had
been sent while Apollo had four delivered. Every test here is about one of two
things: that an outstanding item is surfaced, or that an unreadable source is
reported as unreadable instead of clean. The second kind matters more. This
project has twice had a dead tool return something that read like a finding.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

from seats_prospecting.status import build_report, render


def envelope(record: dict, *, kind: str | None = None, relayed: bool = False) -> dict:
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
    body = {
        "schema_version": 1,
        "written_at": "2026-09-04T15:25:07+00:00",
        "status": "Draft",
        "relay": {"target_database_id": "db", "relayed": relayed},
        "record": record,
        "record_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
    }
    if kind:
        body["kind"] = kind
    return body


def write(outbox: Path, name: str, body: dict, *, relayed: bool = False) -> Path:
    directory = outbox / "relayed" if relayed else outbox
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return path


def ledger_record(**kw) -> dict:
    base = dict(
        institution="Wiley University",
        motion="M3 Student Success / Retention",
        segment="Private university",
        account_owner="Miguel Pescador",
        outreach_decision="Pending owner review",
        source_agent="Prospect Briefing",
        verified_through="2026-09-01",
        facts_carried_over=["A fact."],
        not_verified=["Something."],
        constraints=[],
        what_this_suggests="Qualify first.",
    )
    base.update(kw)
    return base


TODAY = date(2026, 9, 8)


def empty_apollo(_path, _params):
    return {"emailer_campaigns": [], "tasks": []}


# --- outstanding work is surfaced -----------------------------------------


def test_nothing_outstanding_says_so_in_one_line(tmp_path):
    (tmp_path / "relayed").mkdir(parents=True)
    report = build_report(tmp_path, fetch=empty_apollo, today=TODAY)
    assert report.total == 0
    assert render(report).splitlines()[0] == "Nothing outstanding."


def test_a_record_still_in_the_outbox_is_listed_by_filename(tmp_path):
    write(tmp_path, "20260908T195935Z-briefing-wiley.json", envelope(ledger_record()))
    report = build_report(tmp_path, fetch=empty_apollo, today=TODAY)
    listed = report.sections["Pending relay to the ledger"]
    assert len(listed) == 1
    assert "20260908T195935Z-briefing-wiley.json" in listed[0]


def test_a_relayed_record_is_not_pending_but_its_owner_review_still_is(tmp_path):
    write(tmp_path, "briefing.json", envelope(ledger_record(), relayed=True), relayed=True)
    report = build_report(tmp_path, fetch=empty_apollo, today=TODAY)
    assert "Pending relay to the ledger" not in report.sections
    assert any(
        "Wiley University" in line
        for line in report.sections["Pending owner review, as recorded by the agent"]
    )


def test_a_rewrite_verdict_lists_the_recipient_and_the_required_fix(tmp_path):
    verdict = {
        "status": "rewrite",
        "batch_kind": "cadence",
        "findings": [
            {
                "ground": "cadence_additivity",
                "quote": "A concise First-Flight review could cover student touchpoints.",
                "recipient": "Mary Ann Tietjen",
                "why": "Restates touches 2 and 3.",
                "required_fix": "Touch 4 must carry a new reason to reply.",
            }
        ],
        "observations": [],
        "disagreements": [],
        "standard_problem": None,
    }
    write(tmp_path, "review.json", envelope(verdict, kind="review_verdict"))
    report = build_report(tmp_path, fetch=empty_apollo, today=TODAY)
    line = report.sections["Rewrite asked for by the last verdict on record"][0]
    assert "Mary Ann Tietjen" in line
    assert "cadence_additivity" in line
    assert "new reason to reply" in line


def test_a_verdict_that_ships_asks_for_nothing(tmp_path):
    verdict = {"status": "ships", "batch_kind": "cadence", "findings": []}
    write(tmp_path, "review.json", envelope(verdict, kind="review_verdict"))
    report = build_report(tmp_path, fetch=empty_apollo, today=TODAY)
    assert "Rewrite asked for by the last verdict on record" not in report.sections


def test_the_thirty_day_rule_is_the_ledgers_own(tmp_path):
    write(tmp_path, "fresh.json", envelope(ledger_record(verified_through="2026-09-01")))
    write(
        tmp_path,
        "stale.json",
        envelope(ledger_record(institution="Lyon College", verified_through="2025-07-01")),
    )
    report = build_report(tmp_path, fetch=empty_apollo, today=TODAY)
    stale = report.sections["Past the 30-day re-verification rule"]
    assert len(stale) == 1
    assert "Lyon College" in stale[0]


def sequences_fetch(*extra):
    """Two of this build's sequences and one unrelated campaign whose name also
    contains ' | '. The unrelated one is real: UK FE Timetabling, 249 delivered."""

    def fetch(path, _params):
        if path == "/emailer_campaigns/search":
            return {
                "emailer_campaigns": [
                    {
                        "id": "seq1",
                        "name": "Mary Ann Tietjen | Creighton University",
                        "label_ids": ["label-abc"],
                        "active": True,
                        "num_steps": 4,
                        "unique_delivered": 1,
                        "overdue_manual_tasks_count": 0,
                    },
                    {
                        "id": "seq2",
                        "name": "UK FE Timetabling | Outbound Campaign",
                        "label_ids": ["someone-elses-label"],
                        "active": True,
                        "num_steps": 4,
                        "unique_delivered": 249,
                        "overdue_manual_tasks_count": 3,
                    },
                    *extra,
                ]
            }
        return {"tasks": []}

    return fetch


def test_an_active_sequence_is_flagged_because_the_records_say_paused(tmp_path, monkeypatch):
    """Sequences are created paused. Once someone works the first task they are
    not, and nothing in the system re-pauses them, so active is worth surfacing."""
    monkeypatch.setenv("APOLLO_SEQUENCE_LABEL_ID", "label-abc")
    (tmp_path / "relayed").mkdir(parents=True)
    report = build_report(tmp_path, fetch=sequences_fetch(), today=TODAY)
    flagged = report.sections["Apollo sequence is active, not paused"]
    assert len(flagged) == 1
    assert "Creighton" in flagged[0]


def test_the_label_filter_excludes_someone_elses_campaign(tmp_path, monkeypatch):
    """The 8 September finding. Without the label id the filter fell back to the
    ' | ' naming convention and matched a live campaign with 249 delivered
    emails and 3 overdue tasks that has nothing to do with this build."""
    monkeypatch.setenv("APOLLO_SEQUENCE_LABEL_ID", "label-abc")
    (tmp_path / "relayed").mkdir(parents=True)
    report = build_report(tmp_path, fetch=sequences_fetch(), today=TODAY)
    text = render(report)
    assert "UK FE" not in text
    assert "Overdue manual tasks in Apollo" not in report.sections


def test_without_the_label_id_the_wider_filter_is_named_in_coverage(tmp_path, monkeypatch):
    """The fallback is allowed to be wide. It is not allowed to be silent."""
    monkeypatch.delenv("APOLLO_SEQUENCE_LABEL_ID", raising=False)
    (tmp_path / "relayed").mkdir(parents=True)
    report = build_report(tmp_path, fetch=sequences_fetch(), today=TODAY)
    text = render(report)
    assert "naming convention" in text
    assert "UK FE" in text, "the wide filter really does catch it, and says why"


# --- an unreadable source is never a clean one ----------------------------


def test_apollo_failing_is_reported_as_unavailable_not_as_zero(tmp_path):
    def broken(_path, _params):
        raise RuntimeError("403 from Apollo")

    (tmp_path / "relayed").mkdir(parents=True)
    report = build_report(tmp_path, fetch=broken, today=TODAY)
    text = render(report)
    assert "UNAVAILABLE" in text
    assert "403 from Apollo" in text
    assert "Nothing outstanding." not in text
    assert not any("Apollo" in section for section in report.sections)


def test_an_unrecognised_apollo_shape_claims_nothing(tmp_path):
    (tmp_path / "relayed").mkdir(parents=True)
    report = build_report(tmp_path, fetch=lambda p, q: {"unexpected": []}, today=TODAY)
    assert any("shape is not what this tool understands" in r for r in report.unavailable)
    assert report.total == 0
    assert "Nothing outstanding." not in render(report)


def test_skipping_apollo_is_reported_as_unavailable(tmp_path):
    (tmp_path / "relayed").mkdir(parents=True)
    report = build_report(tmp_path, today=TODAY, skip_apollo=True)
    assert any("--no-apollo" in r for r in report.unavailable)


def test_the_notion_gap_is_always_stated(tmp_path):
    (tmp_path / "relayed").mkdir(parents=True)
    text = render(build_report(tmp_path, fetch=empty_apollo, today=TODAY))
    assert "not read: the Notion ledger" in text


def test_nothing_is_invented_when_every_source_is_empty(tmp_path):
    (tmp_path / "relayed").mkdir(parents=True)
    text = render(build_report(tmp_path, fetch=empty_apollo, today=TODAY))
    for word in ("Wiley", "Creighton", "Tulsa", "Arkansas", "Lyon"):
        assert word not in text
