"""resolve_and_enroll: what gets a real enroll call, and what refuses.

The rule under test throughout: zero or multiple candidates never enrolls,
regardless of which search stage found them. Only a single confident match
-- free or revealed -- reaches the actual add_contact_ids call.
"""

from __future__ import annotations

import json

import httpx
import pytest

from seats_prospecting import drafts, enrollment_queue
from seats_prospecting.drafts import DraftPlan
from seats_prospecting.schemas import SequenceTouch


def _token(monkeypatch):
    monkeypatch.setattr(drafts, "_headers", lambda: {"Authorization": "Bearer test"})


def _plan(person="Dewayne Dickens", institution="Tulsa Community College") -> DraftPlan:
    return DraftPlan(
        person=person, institution=institution, recipient=person,
        touches=[SequenceTouch(step=1, day_offset=0, subject="s", body="b", recipient_role="x")],
    )


@pytest.fixture(autouse=True)
def _isolated_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("SEATS_ENROLLMENT_DIR", str(tmp_path / "reveals"))
    monkeypatch.setenv("SEATS_QUEUE_DIR", str(tmp_path / "queue"))
    # _my_mailbox_id caches process-wide across calls (by design, in
    # production); reset it per test so one test's mailbox lookup can't
    # leak into another's.
    monkeypatch.setattr(drafts, "_mailbox_cache", None)
    yield


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_single_saved_contact_with_email_enrolls_with_no_reveal(monkeypatch):
    _token(monkeypatch)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/contacts/search"):
            return httpx.Response(200, json={"contacts": [
                {"id": "c1", "name": "Dewayne Dickens", "email": "dd@tulsacc.edu",
                 "organization": {"name": "Tulsa Community College"}},
            ]})
        if request.url.path.endswith("/email_accounts"):
            return httpx.Response(200, json={"email_accounts": [
                {"id": "mbx1", "user_id": drafts.KIB_APOLLO_USER_ID},
            ]})
        if request.url.path.endswith("/contacts"):
            return httpx.Response(200, json={"contact": {"id": "created-1"}})
        if request.url.path.endswith("/add_contact_ids"):
            return httpx.Response(200, json={"ok": True})
        raise AssertionError(f"unexpected call: {request.url.path}")

    with _client(handler) as http:
        result = drafts.resolve_and_enroll(_plan(), "seq-1", {"Authorization": "Bearer test"}, http)

    assert result == {"enrolled": True, "email": "dd@tulsacc.edu", "contact_id": "created-1"}
    assert "/people/match" not in calls  # no reveal spent, email was already on file
    assert enrollment_queue.today_count() == 0  # cap untouched


def test_two_same_name_saved_contacts_refuses_without_a_reveal(monkeypatch):
    _token(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contacts/search"):
            return httpx.Response(200, json={"contacts": [
                {"id": "c1", "name": "Dewayne Dickens", "email": "a@x.edu",
                 "organization": {"name": "Tulsa Community College"}},
                {"id": "c2", "name": "Dewayne Dickens", "email": "b@x.edu",
                 "organization": {"name": "Tulsa Community College Foundation"}},
            ]})
        raise AssertionError(f"unexpected call: {request.url.path}")

    with _client(handler) as http:
        result = drafts.resolve_and_enroll(_plan(), "seq-1", {"Authorization": "Bearer test"}, http)

    assert result["enrolled"] is False
    assert "no confident single match" in result["detail"]
    assert enrollment_queue.today_count() == 0


def test_no_saved_contact_falls_through_to_the_broader_database_and_reveals(monkeypatch):
    _token(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contacts/search"):
            return httpx.Response(200, json={"contacts": []})
        if request.url.path.endswith("/mixed_people/api_search"):
            return httpx.Response(200, json={"people": [
                {"id": "p1", "organization": {"name": "Tulsa Community College"}},
            ]})
        if request.url.path.endswith("/people/match"):
            body = json.loads(request.content)
            assert body["reveal_personal_emails"] is True
            return httpx.Response(200, json={"person": {"email": "revealed@tulsacc.edu"}})
        if request.url.path.endswith("/email_accounts"):
            return httpx.Response(200, json={"email_accounts": [
                {"id": "mbx1", "user_id": drafts.KIB_APOLLO_USER_ID},
            ]})
        if request.url.path.endswith("/contacts"):
            return httpx.Response(200, json={"contact": {"id": "created-2"}})
        if request.url.path.endswith("/add_contact_ids"):
            return httpx.Response(200, json={"ok": True})
        raise AssertionError(f"unexpected call: {request.url.path}")

    with _client(handler) as http:
        result = drafts.resolve_and_enroll(_plan(), "seq-1", {"Authorization": "Bearer test"}, http)

    assert result["enrolled"] is True
    assert result["email"] == "revealed@tulsacc.edu"
    assert enrollment_queue.today_count() == 1  # one real credit spent


def test_zero_matches_anywhere_does_not_enroll_and_spends_nothing(monkeypatch):
    _token(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contacts/search"):
            return httpx.Response(200, json={"contacts": []})
        if request.url.path.endswith("/mixed_people/api_search"):
            return httpx.Response(200, json={"people": []})
        raise AssertionError(f"unexpected call: {request.url.path}")

    with _client(handler) as http:
        result = drafts.resolve_and_enroll(_plan(), "seq-1", {"Authorization": "Bearer test"}, http)

    assert result["enrolled"] is False
    assert "no match" in result["detail"]
    assert enrollment_queue.today_count() == 0


def test_cap_exhausted_refuses_the_reveal_but_does_not_error(monkeypatch):
    _token(monkeypatch)
    monkeypatch.setattr(enrollment_queue, "MAX_PER_DAY", 0)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contacts/search"):
            return httpx.Response(200, json={"contacts": []})
        if request.url.path.endswith("/mixed_people/api_search"):
            return httpx.Response(200, json={"people": [
                {"id": "p1", "organization": {"name": "Tulsa Community College"}},
            ]})
        raise AssertionError(f"unexpected call: {request.url.path} -- reveal must not fire past the cap")

    with _client(handler) as http:
        result = drafts.resolve_and_enroll(_plan(), "seq-1", {"Authorization": "Bearer test"}, http)

    assert result["enrolled"] is False
    assert "ceiling" in result["detail"]


def test_missing_mailbox_refuses_cleanly_after_a_confident_match(monkeypatch):
    _token(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contacts/search"):
            return httpx.Response(200, json={"contacts": [
                {"id": "c1", "name": "Dewayne Dickens", "email": "dd@tulsacc.edu",
                 "organization": {"name": "Tulsa Community College"}},
            ]})
        if request.url.path.endswith("/email_accounts"):
            return httpx.Response(200, json={"email_accounts": [
                {"id": "mbx1", "user_id": "someone-else"},
            ]})
        raise AssertionError(f"unexpected call: {request.url.path}")

    with _client(handler) as http:
        result = drafts.resolve_and_enroll(_plan(), "seq-1", {"Authorization": "Bearer test"}, http)

    assert result["enrolled"] is False
    assert "mailbox" in result["detail"]
