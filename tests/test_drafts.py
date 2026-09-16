"""The write path, tested, because it was a hand-typed script twice.

Every assertion here is a defect that reached Apollo on 3 or 4 September:

* five people in one sequence, when a sequence is one cadence
* the reviewer's differentiation check inside an email body
* a sign-off left mid-body where the stripper could not see it
* a create-shaped payload sent to the update endpoint
* an update that dropped the step an enrolled contact was attached to

None of them were agent faults. They were a parser and two payload shapes that
nobody had written a test for.
"""

from __future__ import annotations

import json

import httpx
import pytest

from seats_prospecting.drafts import (
    DEFAULT_DAYS,
    DraftError,
    extend_drafts,
    plan_drafts,
    resolve_sequences,
    write_drafts,
)

FIRST = "\n".join(
    [
        "## Copy batch",
        "",
        "### Tulsa Community College",
        "",
        "**To: Dewayne Dickens, Senior Director**",
        "**Subject:** Six coaches, 2,100 students",
        "",
        "Six coaches supporting roughly 2,100 students is meaningful reach.",
        "",
        "Kib Cochran",
        "Solutions Engineer",
        "SEAtS Software",
        "",
        "### Creighton University",
        "",
        "**To: Mary Ann Tietjen, Senior Director**",
        "**Subject:** First-Flight's next cycle",
        "",
        "First-Flight launched this spring inside a wider campus effort.",
        "",
        "## The producing agent's differentiation check",
        "",
        "1. Shared opening structure: no.",
    ]
)

LATER = "\n".join(
    [
        "### Tulsa Community College, Dewayne Dickens, touch 2, day 14",
        "",
        "**To: Dewayne Dickens, Senior Director**",
        "**Subject:** Where coaching judgment meets the queue",
        "",
        "When several students need support at once, who does a coach contact first?",
        "",
        "### Creighton University, Mary Ann Tietjen, touch 2, day 14",
        "",
        "**To: Mary Ann Tietjen, Senior Director**",
        "**Subject:** A handoff worth mapping",
        "",
        "Where does information move late between staff on that journey?",
    ]
)


def test_one_person_is_one_sequence():
    """Five people in one sequence would have sent each of them all five."""
    plans = plan_drafts([FIRST, LATER])
    assert [p.name for p in plans] == [
        "Dewayne Dickens | Tulsa Community College",
        "Mary Ann Tietjen | Creighton University",
    ]
    assert all(len(p.touches) == 2 for p in plans)


def test_the_touch_and_day_come_from_the_heading_when_it_carries_them():
    plans = plan_drafts([FIRST, LATER])
    dickens = plans[0]
    assert [(t.step, t.day_offset) for t in dickens.touches] == [(1, 0), (2, 14)]


def test_days_fall_back_to_the_cadence_when_the_heading_is_silent():
    plans = plan_drafts([FIRST, FIRST.replace("### ", "### ")], days=DEFAULT_DAYS)
    # Same artifact twice: touch 1 then touch 2 by position.
    assert [(t.step, t.day_offset) for t in plans[0].touches] == [(1, 0), (2, 14)]


def test_the_sign_off_never_reaches_a_touch():
    plans = plan_drafts([FIRST])
    assert "Kib Cochran" not in plans[0].touches[0].body


def test_a_body_carrying_the_check_stops_the_write():
    """Belt and braces behind the parser.

    The parser now ends a message at any heading, which is what let the
    reviewer's check into a live draft when it did not. This guard catches the
    same leak if the check arrives without a heading, or if the parser ever
    regresses: the body is inspected, not just the structure, and a hit stops
    every write in the batch rather than only that one.
    """
    leaked = FIRST.replace(
        "First-Flight launched this spring inside a wider campus effort.",
        "First-Flight launched this spring.\n\n"
        "**Batch differentiation check**\n"
        "1. Shared opening structure: no.",
    )
    with pytest.raises(DraftError) as excinfo:
        plan_drafts([leaked])
    assert "differentiation" in str(excinfo.value)
    assert "nothing was written" in str(excinfo.value)


def test_repeated_touch_numbers_are_refused():
    with pytest.raises(DraftError):
        plan_drafts([LATER, LATER])


def test_nothing_to_write_is_an_error_not_an_empty_success():
    with pytest.raises(DraftError):
        plan_drafts(["## Checkpoint 2: Trigger research\n\nNo messages here.\n"])


# --- Apollo calls, against a stub -----------------------------------------


def _token(monkeypatch):
    from seats_prospecting import drafts

    monkeypatch.setattr(drafts, "_headers", lambda: {"Authorization": "Bearer test"})


def test_write_creates_one_paused_sequence_per_person(monkeypatch):
    """Enrollment is best-effort and separate from sequence creation, so this
    test stubs contacts/search to return no match -- the point here is the
    sequence shape, not the enrollment path (see test_resolve_and_enroll.py
    for that)."""
    _token(monkeypatch)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contacts/search"):
            return httpx.Response(200, json={"contacts": []})
        if request.url.path.endswith("/mixed_people/api_search"):
            return httpx.Response(200, json={"people": []})
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(
            200,
            json={
                "emailer_campaign": {"id": f"seq-{len(seen)}", "name": body["name"]},
                "emailer_steps": [{"id": f"s{i}"} for i in range(len(body["emailer_steps"]))],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        results = write_drafts(plan_drafts([FIRST, LATER]), client=client)

    assert [r["status"] for r in results] == [200, 200]
    assert all(body["active"] is False for body in seen)
    assert all(
        step["type"] == "manual_email" for body in seen for step in body["emailer_steps"]
    )
    assert [len(body["emailer_steps"]) for body in seen] == [2, 2]
    assert all(r["enrollment"]["enrolled"] is False for r in results)


def test_extend_sends_the_update_shape_with_the_existing_ids(monkeypatch):
    """Create and update are different bodies. The update needs positions and
    the ids of what is already there, or Apollo answers 422 Missing Step 1."""
    _token(monkeypatch)
    updates = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/emailer_campaigns/search"):
            if json.loads(request.content)["page"] > 1:
                return httpx.Response(200, json={"emailer_campaigns": []})
            return httpx.Response(
                200,
                json={
                    "emailer_campaigns": [
                        {
                            "id": "seq-1",
                            "name": "Dewayne Dickens | Tulsa Community College",
                            "emailer_steps": [
                                {
                                    "id": "step-1",
                                    "position": 1,
                                    "emailer_touches": [{"id": "touch-1"}],
                                }
                            ],
                        }
                    ]
                },
            )
        updates.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"emailer_campaign": {"id": "seq-1"}, "emailer_steps": [1, 2]})

    plans = [p for p in plan_drafts([FIRST, LATER]) if "Dickens" in p.name]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        results = extend_drafts(plans, client=client)

    assert results[0]["status"] == 200
    path, body = updates[0]
    assert path.endswith("/sequences/seq-1")
    assert body["active"] is False
    assert [s["position"] for s in body["emailer_steps"]] == [1, 2]
    assert body["emailer_steps"][0]["id"] == "step-1"
    assert body["emailer_steps"][0]["emailer_touches"][0]["id"] == "touch-1"
    assert "id" not in body["emailer_steps"][1]


def test_extend_reports_a_missing_sequence_instead_of_creating_one(monkeypatch):
    _token(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/emailer_campaigns/search"):
            return httpx.Response(200, json={"emailer_campaigns": []})
        raise AssertionError("extend must not create anything")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        results = extend_drafts(plan_drafts([FIRST]), client=client)

    assert all(r["error"] == "no sequence with that name" for r in results)


def test_resolve_matches_on_the_exact_name(monkeypatch):
    _token(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["page"] > 1:
            return httpx.Response(200, json={"emailer_campaigns": []})
        return httpx.Response(
            200,
            json={
                "emailer_campaigns": [
                    {"id": "a", "name": "Dewayne Dickens | Tulsa Community College", "emailer_steps": []},
                    {"id": "b", "name": "Dewayne Dickens | Tulsa Community College (old)", "emailer_steps": []},
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        found = resolve_sequences(["Dewayne Dickens | Tulsa Community College"], client=client)

    assert list(found) == ["Dewayne Dickens | Tulsa Community College"]
    assert found["Dewayne Dickens | Tulsa Community College"]["id"] == "a"
