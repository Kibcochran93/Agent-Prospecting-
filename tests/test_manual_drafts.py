"""The batch lands in Apollo as drafts a person sends, never as auto-send steps.

Kib asked for drafts in Apollo, not a sequence that mails on activation. The
control that delivers that is the step type. An `auto_email` step sends by itself
as soon as the sequence is activated and a contact is enrolled. A `manual_email`
step cannot: it produces a task with the copy pre-filled for a person to read and
send.

So the step type is structural, not cosmetic. `active: False` on its own is not
enough, because one accidental activation would turn twenty auto steps into
twenty sent emails. These tests assert the step type and the paused flag are
owned by the code, that the model's schema cannot name either, and that no
send-shaped capability has crept into the module.

`manual_email` was checked against Kib's own Apollo workspace before being
adopted, where it appears on 15 existing steps across the team's sequences. It is
not a guess at the API.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from seats_prospecting.schemas import SequenceProposal, SequenceTouch
from seats_prospecting.tools.apollo_write import sequence_payload

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "seats_prospecting"
    / "tools"
    / "apollo_write.py"
)


def _code_only(source: str) -> str:
    """The module's executable code, with comments and docstrings removed.

    The first version of these tests scanned the raw file and failed, because the
    module's docstring explains at length why ``auto_email`` and ``send_now`` are
    absent. Naming a capability in prose is the opposite of holding it, so a test
    that cannot tell those apart is measuring the wrong thing.

    ``ast.unparse`` drops comments for free; docstrings are stripped explicitly.
    String literals that matter, like ``"manual_email"``, survive because they are
    values in the code rather than documentation about it.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


MODULE_CODE = _code_only(MODULE_PATH.read_text(encoding="utf-8"))


def _touch(step: int, day: int) -> SequenceTouch:
    return SequenceTouch(
        step=step,
        day_offset=day,
        subject=f"Subject {step}",
        body=f"Body {step}.",
        recipient_role="operational owner",
    )


def _proposal(n: int = 3) -> SequenceProposal:
    return SequenceProposal(
        sequence_name="M3 test batch",
        contact_count=4,
        source_list="Public four-year, SACSCOC, 5,000 to 15,000 headcount.",
        touches=[_touch(i, (i - 1) * 4) for i in range(1, n + 1)],
    )


def test_every_step_is_a_manual_email():
    """The requirement, expressed as an assertion."""
    payload = sequence_payload(_proposal(5))
    types = {step["type"] for step in payload["emailer_steps"]}
    assert types == {"manual_email"}, (
        f"expected every step to be manual_email, got {types}. An auto_email step "
        "sends itself the moment the sequence is activated."
    )


def test_the_sequence_is_created_paused():
    assert sequence_payload(_proposal())["active"] is False


def _template(step: dict) -> dict:
    return step["emailer_touches"][0]["emailer_template"]


def test_the_copy_reaches_apollo_intact():
    """Drafts are useless if the body is summarized on the way in.

    The shape here is Apollo's, read off its OpenAPI specification after the
    first live write created a sequence with zero steps: the copy lives in a
    touch's emailer_template, not in an emailer_templates array on the step.
    """
    payload = sequence_payload(_proposal(2))
    steps = payload["emailer_steps"]
    assert [_template(s)["subject"] for s in steps] == ["Subject 1", "Subject 2"]
    assert [_template(s)["body_html"] for s in steps] == ["<p>Body 1.</p>", "<p>Body 2.</p>"]
    assert all(t["type"] == "new_thread" for s in steps for t in s["emailer_touches"])


def test_the_body_is_escaped_not_injected():
    proposal = SequenceProposal(
        sequence_name="x",
        contact_count=1,
        source_list="y",
        touches=[
            SequenceTouch(
                step=1,
                day_offset=0,
                subject="s",
                body="Retention & scheduling <are> linked.\n\nWorth a look?",
                recipient_role="operational owner",
            )
        ],
    )
    html = _template(sequence_payload(proposal)["emailer_steps"][0])["body_html"]
    assert "&amp;" in html and "&lt;are&gt;" in html
    assert html.count("<p>") == 2, "a blank line should start a new paragraph"


def test_waits_are_gaps_between_steps_not_days_since_enrollment():
    """Apollo measures wait_time from the previous step. day_offset is measured
    from enrollment, which is how a cadence is written. Passing ours through
    unchanged would turn day 0, 4, 8 into day 0, 4, 12."""
    payload = sequence_payload(_proposal(3))  # offsets 0, 4, 8
    assert [s["wait_time"] for s in payload["emailer_steps"]] == [0, 4, 4]
    assert {s["wait_mode"] for s in payload["emailer_steps"]} == {"day"}


def test_touches_are_ordered_by_step_not_by_arrival():
    proposal = SequenceProposal(
        sequence_name="out of order",
        contact_count=1,
        source_list="x",
        touches=[_touch(3, 8), _touch(1, 0), _touch(2, 4)],
    )
    # /sequences takes order from the array, so the order is the assertion.
    subjects = [
        s["emailer_touches"][0]["emailer_template"]["subject"]
        for s in sequence_payload(proposal)["emailer_steps"]
    ]
    assert subjects == ["Subject 1", "Subject 2", "Subject 3"], (
        f"steps reached Apollo in the order {subjects}; a batch would arrive with "
        "its touches shuffled"
    )


def test_the_code_pins_manual_email_and_never_builds_an_auto_email_step():
    """Source guard, so the control survives a refactor of the call path.

    Checked against code with docstrings and comments stripped, so the module can
    explain why auto_email is wrong without the explanation tripping the test.
    """
    assert "manual_email" in MODULE_CODE, (
        "the step type is no longer pinned to manual_email in the code"
    )
    assert "auto_email" not in MODULE_CODE, (
        "auto_email has reappeared in the write module's code. An auto step sends "
        "itself on activation, which is the thing this design refuses."
    )


def test_the_model_cannot_name_the_step_type_or_the_paused_flag():
    for forbidden in ("active", "type", "step_type", "emailer_steps", "permissions"):
        assert forbidden not in SequenceProposal.model_fields, (
            f"SequenceProposal accepts '{forbidden}', so the model can set a field "
            "the function is supposed to own"
        )
    for forbidden in ("type", "step_type", "active", "send_at"):
        assert forbidden not in SequenceTouch.model_fields, (
            f"SequenceTouch accepts '{forbidden}'; a touch must not carry its own "
            "send behaviour"
        )


def test_an_extra_field_is_rejected_rather_than_ignored():
    with pytest.raises(Exception):
        SequenceProposal(
            sequence_name="x",
            contact_count=0,
            source_list="y",
            touches=[_touch(1, 0)],
            active=True,
        )


def test_the_module_holds_no_send_activate_or_enrol_capability():
    """Apollo exposes all of these. None of them may live here.

    The mailbox-draft endpoint is the sharpest case. It sits one call from
    send_now and Apollo's own documentation encourages chaining the two, which is
    why this system queues manual steps in a sequence instead of writing drafts
    against a mailbox.
    """
    for token in (
        "send_now",
        "emailer_messages",
        "campaigns_approve",
        "add_contact_ids",
        "sequences_update",
        "email_account_id",
        "contacts_create",
    ):
        assert token not in MODULE_CODE, (
            f"'{token}' is called from the write module's code. Sending, "
            "activating, enrolling and mailbox drafts are absent by design, not by "
            "instruction. Discussing them in a comment is fine; calling them is not."
        )


def test_the_module_still_documents_why_those_are_absent():
    """The reasoning is load-bearing for the next person who reads this.

    Stripping comments made the checks above correct. It also means nothing was
    verifying that the explanation still exists, so this asserts the prose too.
    """
    raw = MODULE_PATH.read_text(encoding="utf-8")
    assert "send_now" in raw, (
        "the module no longer explains that Apollo's draft endpoint sits one call "
        "from send_now. That reasoning is why this tool builds sequence steps "
        "instead of mailbox drafts, and it should not be deleted silently."
    )
    assert "manual_email" in raw


def test_the_module_exports_nothing_send_shaped():
    from seats_prospecting.tools import apollo_write

    for name in dir(apollo_write):
        if name.startswith("_"):
            continue
        low = name.lower()
        assert not any(
            word in low for word in ("send", "activate", "approve", "enrol", "enroll")
        ), f"apollo_write exports a send-shaped name: {name}"


# --- the tool reports what Apollo recorded, not what it sent ---------------


def test_the_success_message_is_built_from_the_response_not_the_proposal():
    """Found live on 3 September 2026.

    The first real write returned 200 and Apollo created an empty paused
    sequence: the nested ``emailer_steps`` in the request body were ignored, so
    "Arkansas Public Universities | M1 Schedule Replacement | September 2026"
    holds zero steps. The tool counted ``proposal.touches`` and reported four
    manual-email drafts that do not exist, and the agent repeated that to Kib.

    A write tool may never describe copy as being in Apollo unless Apollo's own
    response says it is.
    """
    source = MODULE_PATH.read_text(encoding="utf-8")
    code = _code_only(source)

    assert "steps_created" in code, "the module does not read the steps back"
    # ast.unparse normalizes quotes, so match on the shape rather than the source.
    assert "emailer_steps" in code and ".get(" in code
    assert "/sequences" in code, (
        "the write must post to /sequences. /emailer_campaigns returns 200 and "
        "silently drops every step, which is how the first live write produced an "
        "empty sequence."
    )
    assert "THE COPY IS NOT IN APOLLO" in code, (
        "a partial write must say so in words the agent cannot soften"
    )
    # The old bug in one line: reporting a count taken from the request.
    assert "{len(proposal.touches)} manual-email drafts" not in code


# --- every sequence files under one name -----------------------------------


def test_the_filing_is_a_label_and_the_name_belongs_to_the_recipient():
    """Kib's instruction, 4 September, revised the same day.

    Everything this system writes has to stay findable in one place in Apollo.
    That started as a name prefix and became a label, because a sequence here is
    one person and the title is theirs. Both are owned by the code: a run cannot
    file its work somewhere else by naming it something else.
    """
    payload = sequence_payload(_proposal())
    assert payload["name"] == "M3 test batch"
    assert payload["label_names"] == ["AI Agent Prospecting Ops"]


def test_the_label_is_configurable_and_can_be_turned_off(monkeypatch):
    import importlib

    from seats_prospecting.tools import apollo_write

    monkeypatch.setenv("APOLLO_SEQUENCE_LABEL", "")
    importlib.reload(apollo_write)
    assert "label_names" not in apollo_write.sequence_payload(_proposal())

    monkeypatch.setenv("APOLLO_SEQUENCE_LABEL", "US Higher Ed")
    importlib.reload(apollo_write)
    assert apollo_write.sequence_payload(_proposal())["label_names"] == ["US Higher Ed"]

    monkeypatch.delenv("APOLLO_SEQUENCE_LABEL", raising=False)
    importlib.reload(apollo_write)


def test_the_prefix_is_not_applied_twice():
    from seats_prospecting.tools.apollo_write import filed_name

    once = filed_name("Arkansas publics")
    assert filed_name(once) == once


def test_the_prefix_is_configurable_but_present_by_default():
    from seats_prospecting.tools.apollo_write import filed_name

    assert filed_name("x", prefix="SEAtS US") == "SEAtS US | x"
    assert filed_name("x", prefix="") == "x"


def test_the_folder_id_is_sent_only_when_set(monkeypatch):
    """Apollo has no folder API, only a folder_id on create. Absent by default so
    a create never references a folder that does not exist."""
    monkeypatch.delenv("APOLLO_SEQUENCE_FOLDER_ID", raising=False)
    assert "folder_id" not in sequence_payload(_proposal())

    monkeypatch.setenv("APOLLO_SEQUENCE_FOLDER_ID", "6a99f00d")
    assert sequence_payload(_proposal())["folder_id"] == "6a99f00d"


def test_the_model_still_cannot_name_the_folder_or_the_filing():
    for forbidden in ("folder_id", "name_prefix"):
        assert forbidden not in SequenceProposal.model_fields


def test_the_typed_sign_off_is_stripped_before_apollo_sees_it():
    """4 September: the first real batch arrived in Apollo signed twice.

    Apollo appends Kib's own signature block, with his title and booking link,
    to every step it renders. The model had also typed one at the bottom of each
    message, so every draft in the task queue carried both.
    """
    proposal = SequenceProposal(
        sequence_name="signature check",
        contact_count=1,
        source_list="x",
        touches=[
            SequenceTouch(
                step=1,
                day_offset=0,
                subject="s",
                body=(
                    "Six coaches supporting roughly 2,100 students is meaningful reach.\n\n"
                    "How does that visibility work at TCC today?\n\n"
                    "Kib Cochran\nSolutions Engineer\nSEAtS Software"
                ),
                recipient_role="operational owner",
            )
        ],
    )
    html = _template(sequence_payload(proposal)["emailer_steps"][0])["body_html"]
    assert "TCC today?" in html
    assert "Kib Cochran" not in html
    assert "Solutions Engineer" not in html


def test_a_sequence_is_titled_for_the_person_then_the_institution():
    """Kib's convention, 4 September. One sequence is one recipient, so his eye
    goes to the name first and the institution disambiguates it."""
    from seats_prospecting.tools.apollo_write import filed_name, sequence_title

    title = sequence_title("Dewayne Dickens", "Tulsa Community College")
    assert title == "Dewayne Dickens | Tulsa Community College"
    # No prefix by default any more: the grouping is a label. filed_name still
    # exists for anyone who sets APOLLO_SEQUENCE_PREFIX.
    assert filed_name(title) == title
    assert filed_name(title, prefix="US Higher Ed") == f"US Higher Ed | {title}"


def test_the_title_survives_a_missing_half_and_stray_spacing():
    from seats_prospecting.tools.apollo_write import sequence_title

    assert sequence_title("  Mary Ann  Tietjen ", "Creighton University") == (
        "Mary Ann Tietjen | Creighton University"
    )
    assert sequence_title("", "Wiley University") == "Wiley University"
    assert sequence_title("Shaniqua Adams", "") == "Shaniqua Adams"


def test_the_update_payload_positions_every_step_and_keeps_existing_ids():
    """4 September: extending the live sequences returned 422, "Missing Step 1".

    The create body and the update body are different shapes. Update wants a
    position on every step and the id of anything that already exists, and it
    replaces the step list rather than appending, so touch 1 has to travel with
    the new ones or Apollo deletes it.
    """
    from seats_prospecting.tools.apollo_write import update_payload

    proposal = SequenceProposal(
        sequence_name="Dewayne Dickens | Tulsa Community College",
        contact_count=1,
        source_list="one contact",
        touches=[_touch(i, (i - 1) * 7) for i in range(1, 4)],
    )
    payload = update_payload(proposal, existing=[("step-1", "touch-1")])
    steps = payload["emailer_steps"]

    assert [s["position"] for s in steps] == [1, 2, 3]
    assert steps[0]["id"] == "step-1"
    assert steps[0]["emailer_touches"][0]["id"] == "touch-1"
    assert "id" not in steps[1] and "id" not in steps[2]
    assert {s["type"] for s in steps} == {"manual_email"}
    assert payload["active"] is False
    assert [s["wait_time"] for s in steps] == [0, 7, 7]


def test_an_update_cannot_activate_a_sequence():
    from seats_prospecting.tools.apollo_write import update_payload

    payload = update_payload(_proposal(2))
    assert payload["active"] is False
    assert "status" not in payload
