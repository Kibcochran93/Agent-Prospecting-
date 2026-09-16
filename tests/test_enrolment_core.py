"""Tests for the extracted enrolment core. Each pins something it cannot do."""

from __future__ import annotations

import inspect

import pytest

from seats_prospecting import enrolment


def test_paused_is_owned_here_and_has_no_argument():
    payload = enrolment.enrolment_payload("seq1", "c1", "mb1")
    assert payload["status"] == "paused"
    assert "status" not in inspect.signature(enrolment.enrolment_payload).parameters
    assert "status" not in inspect.signature(enrolment.enrol).parameters


def test_one_contact_per_call():
    payload = enrolment.enrolment_payload("seq1", "c1", "mb1")
    assert payload["contact_ids"] == ["c1"]
    params = inspect.signature(enrolment.enrol).parameters
    assert "contact_ids" not in params
    assert "contacts" not in params


def test_nothing_here_can_send_or_activate():
    """Checked against the parsed code, not the text.

    A first version of this test grepped the source and failed on the module
    docstring, which names send_now in order to promise it is not called. A
    guarantee that a comment can break is not a guarantee.
    """
    import ast

    tree = ast.parse(inspect.getsource(enrolment))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)

    literals = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and n.value not in docstrings
    ]
    names = [
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    ] + [n.id for n in ast.walk(tree) if isinstance(n, ast.Name)]

    haystack = " ".join(literals + names).lower()
    for forbidden in ("send_now", "activate", "approve", "emailer_messages_create"):
        assert forbidden not in haystack, forbidden

    # Prose may say "the sequence is not active". What must not exist is a
    # literal "active" used as a key or a value, and the only status this
    # module knows is paused.
    assert "active" not in [lit.strip() for lit in literals]
    assert "paused" in literals
    statuses = [
        lit for lit in literals
        if lit in ("paused", "active", "started", "enabled")
    ]
    assert set(statuses) == {"paused"}, statuses


def test_an_already_sequenced_person_is_refused_without_an_override():
    refusal = enrolment.live_sequence_refusal(["6a8899a4482aef0010cb668a"], None)
    assert refusal is not None
    assert "NOBODY WAS ENROLLED" in refusal
    assert "6a8899a4482aef0010cb668a" in refusal


def test_an_override_lets_it_through_and_a_queue_job_passes_none():
    assert enrolment.live_sequence_refusal(["seq"], "Kib override, deliberate") is None
    assert enrolment.live_sequence_refusal([], None) is None


def test_enrichment_refuses_at_the_cap_before_spending(monkeypatch):
    """The slot is checked before the call, not after. A cap of one cost two."""
    called = []
    monkeypatch.setattr(enrolment, "_headers", lambda: called.append("headers") or {})
    with pytest.raises(enrolment.EnrolRefused) as exc:
        enrolment.ensure_email("c1", reveal_cap=1, spent=1)
    assert "cap reached" in str(exc.value)
    assert called == [], "no request may be prepared once the cap is reached"


def test_enrichment_never_returns_an_address():
    fields = enrolment.Enriched.__dataclass_fields__
    for name in fields:
        assert "email" not in name or name == "has_email", name
    assert "email" not in [f for f in fields if f != "has_email"]


def test_mailbox_is_required(monkeypatch):
    monkeypatch.delenv("APOLLO_MAILBOX_ID", raising=False)
    with pytest.raises(enrolment.EnrolRefused) as exc:
        enrolment._mailbox()
    assert "APOLLO_MAILBOX_ID" in str(exc.value)
