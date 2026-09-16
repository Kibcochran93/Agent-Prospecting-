"""The mechanical half of the differentiation check is arithmetic, not opinion.

Each test here is a failure the M3 batch actually shipped with, or the shape of
one. The producing agent reported that batch as passing all five questions; the
Reviewer disagreed on four. These assertions are what stop that disagreement
from costing a review pass again.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from seats_prospecting.differentiation import (  # noqa: I001
    FAIL,
    REPORT,
    parse_self_report,
    self_report_conflicts,
    Message,
    Finding,
    check,
    check_artifact,
    hard_checks,
    parse_messages,
    soft_checks,
)
from seats_prospecting.schemas import SequenceProposal, SequenceTouch

ARTIFACT = """## Rewritten batch

### University of Tennessee at Chattanooga

**To: Vice Chancellor for Enrollment Management**
**Subject:** The 80% retention goal

UTC set a target of 80% first-year retention by 2030. Worth a look at how
progress gets measured?

**To: Retention workgroup lead / Student Success leader**
**Subject:** Eight groups, one student journey

Eight workgroups now own pieces of the same student journey.

### Murray State University

**To: Provost**
**Subject:** Reading two different indicators

Murray State reported 78% retention while spring headcount fell 4.3%.

**To: Student Affairs leader**
**Subject:** Before recapture becomes necessary

The recapture effort helped 1,191 undergraduates return.
"""


def _msg(institution, recipient, subject, body):
    return Message(institution=institution, recipient=recipient, subject=subject, body=body)


def test_the_parser_finds_every_message_with_its_institution():
    messages = parse_messages(ARTIFACT)
    assert len(messages) == 4
    assert messages[0].institution == "University of Tennessee at Chattanooga"
    assert messages[0].subject == "The 80% retention goal"
    assert "80% first-year retention" in messages[0].body
    assert messages[3].institution == "Murray State University"


def test_a_slashed_role_is_a_slot_not_a_recipient():
    """"Retention workgroup lead / Student Success leader" was in the live batch.
    The sources cited in that same run named the workgroup leads."""
    findings = check_artifact(ARTIFACT)
    rules = [f.rule for f in findings]
    assert "placeholder-recipient" in rules
    assert all(f.severity == FAIL for f in findings if f.rule == "placeholder-recipient")


@pytest.mark.parametrize(
    "recipient",
    ["[Name], Provost", "{{first_name}}", "TBD", "the relevant Student Success lead"],
)
def test_placeholder_recipients_are_caught(recipient):
    messages = [
        _msg("A", recipient, "One", "A sentence that is quite long and specific here."),
        _msg("A", "Provost", "Two", "Another sentence entirely, also long and specific."),
    ]
    assert any(f.rule == "placeholder-recipient" for f in hard_checks(messages))


def test_two_messages_with_the_same_subject_fail():
    messages = [
        _msg("A", "Provost", "The retention goal", "One body of text right here now."),
        _msg("B", "Provost", "The Retention Goal", "A different body of text entirely."),
    ]
    assert any(f.rule == "duplicate-subject" for f in hard_checks(messages))


def test_a_shared_opening_fails_even_when_the_rest_diverges():
    """Eight identical words, then two entirely different messages. The rule is
    exact match on the opening; structure that rhymes without repeating is the
    Reviewer's call, not arithmetic."""
    messages = [
        _msg("A", "Provost", "One", "Your strategic plan sets a target for retention. First year, 80% by 2030."),
        _msg("B", "Provost", "Two", "Your strategic plan sets a target for retention, and spring headcount fell 4.3%."),
    ]
    assert any(f.rule == "shared-opening" for f in hard_checks(messages))


def test_a_shared_closing_move_fails():
    """Every executive in the live batch closed on the same offer question."""
    close = "Would an Engagement Readiness session help establish the measures?"
    messages = [
        _msg("A", "Provost", "One", f"Retention rose to 73.4% this year. {close}"),
        _msg("B", "Chief Financial Officer", "Two", f"Headcount fell 4.3% in spring. {close}"),
    ]
    rules = [f.rule for f in hard_checks(messages)]
    assert "shared-closing" in rules
    assert "duplicate-sentence" in rules


def test_an_institution_with_one_role_is_single_threaded():
    messages = [_msg("Lyon College", "Provost", "One", "A single thread into one office here.")]
    assert any(f.rule == "single-threaded" for f in hard_checks(messages))


def test_a_two_lane_template_is_reported_not_refused():
    """Every executive got Engagement Readiness, every operator got Progress Check
    Pilot. No two messages were identical, so only counting catches it."""
    messages = [
        _msg("A", "Provost", "One", "Retention rose here. An Engagement Readiness session sets the measures."),
        _msg("B", "Provost", "Two", "Headcount fell there. An Engagement Readiness session frames the case."),
        _msg("A", "Registrar", "Three", "Advising is spread across eight groups on this campus."),
        _msg("B", "Registrar", "Four", "Alerts arrive after registration closes on that campus."),
    ]
    findings = soft_checks(messages)
    repeated = [f for f in findings if f.rule == "repeated-offer"]
    assert repeated and repeated[0].severity == REPORT
    assert "Engagement Readiness" in repeated[0].detail


def test_a_two_year_old_source_stated_as_current_is_reported():
    """UA Little Rock's plan was approved 6 June 2024, flagged stale upstream and
    then written as current fact anyway."""
    messages = [
        _msg("UA Little Rock", "Provost", "One", "Your strategic plan, approved 6 June 2024, embeds analytics in departmental reporting."),
        _msg("UA Little Rock", "Registrar", "Two", "Departmental reporting now carries the intervention data."),
    ]
    findings = soft_checks(messages, as_of=date(2026, 9, 3))
    stale = [f for f in findings if f.rule == "stale-date-stated-as-current"]
    assert stale and "2024-06-06" in stale[0].detail


def test_a_stale_date_that_says_so_is_left_alone():
    messages = [
        _msg("UA Little Rock", "Provost", "One", "Your plan from 6 June 2024 is older than a year, so the priorities may have moved on."),
        _msg("UA Little Rock", "Registrar", "Two", "Departmental reporting now carries the intervention data."),
    ]
    assert not [f for f in soft_checks(messages, as_of=date(2026, 9, 3)) if f.rule == "stale-date-stated-as-current"]


def test_a_recent_date_is_not_reported():
    messages = [
        _msg("UTC", "Provost", "One", "The plan you launched on January 13, 2026 targets 80% retention."),
        _msg("UTC", "Registrar", "Two", "Eight workgroups own pieces of the same journey now."),
    ]
    assert not [f for f in soft_checks(messages, as_of=date(2026, 9, 3)) if f.rule == "stale-date-stated-as-current"]


def test_a_clean_batch_produces_nothing():
    messages = [
        _msg("UTC", "Provost", "The 80% goal", "UTC set a target of 80% first-year retention by 2030."),
        _msg("UTC", "Registrar", "Eight groups", "Advising now runs across eight separate workgroups."),
        _msg("Murray State", "Chief Financial Officer", "Two indicators", "Retention held at 78% while spring headcount fell."),
        _msg("Murray State", "Student Affairs lead", "Before recapture", "The recapture effort brought 1,191 undergraduates back."),
    ]
    assert check(messages, as_of=date(2026, 9, 3)) == []


def test_the_write_path_refuses_a_repeated_batch():
    """The gate never gets built, so Kib is never asked to approve a template."""
    close = "Would an Engagement Readiness session help establish the measures?"
    with pytest.raises(ValidationError) as excinfo:
        SequenceProposal(
            sequence_name="M3 regional publics",
            contact_count=8,
            source_list="SACSCOC publics, 5k to 15k headcount",
            touches=[
                SequenceTouch(step=1, day_offset=0, subject="The goal", body=f"Retention rose to 73.4%. {close}", recipient_role="economic buyer"),
                SequenceTouch(step=2, day_offset=6, subject="The goal", body=f"Headcount fell 4.3%. {close}", recipient_role="economic buyer"),
            ],
        )
    text = str(excinfo.value)
    assert "duplicate-subject" in text
    assert "shared-closing" in text


def test_the_write_path_accepts_a_differentiated_batch():
    proposal = SequenceProposal(
        sequence_name="M3 regional publics",
        contact_count=8,
        source_list="SACSCOC publics, 5k to 15k headcount",
        touches=[
            SequenceTouch(step=1, day_offset=0, subject="The 80% goal", body="UTC set a target of 80% first-year retention by 2030. Worth a look?", recipient_role="economic buyer"),
            SequenceTouch(step=2, day_offset=6, subject="Eight workgroups", body="Advising now runs across eight separate groups on one journey.", recipient_role="operational owner"),
        ],
    )
    assert len(proposal.touches) == 2


def test_both_live_recipient_header_shapes_parse():
    """Caught on the first live M1 batch. The Campaign Builder wrote
    "**To Rachel Broussard, Coordinator of Events Management**" with no colon,
    the parser found zero messages, and the check reported nothing at all. A
    silent pass is the one failure mode this module must not have."""
    artifact = (
        "## Copy\n\n"
        "### Arkansas Tech University\n\n"
        "**To Rachel Broussard, Coordinator of Events Management**\n"
        "**Subject:** Ad Astra and event scheduling\n\n"
        "Arkansas Tech routes space requests through your centralized office.\n\n"
        "### UA Little Rock\n\n"
        "**To: Malissa Mathis, Registrar**\n"
        "**Subject:** One system, several owners\n\n"
        "Mazevo gives one enterprise scheduling environment across three teams.\n"
    )
    messages = parse_messages(artifact)
    assert len(messages) == 2
    assert messages[0].recipient.startswith("Rachel Broussard")
    assert messages[1].recipient.startswith("Malissa Mathis")


def test_the_signature_line_is_not_the_closing():
    """Every message here ends "Kib". Reading that as the closing made three
    pairs in the first live M1 batch look identical and would have blocked a
    write on nothing."""
    messages = [
        _msg("A", "Rachel Broussard, Coordinator", "One", "Where does your team step outside the platform?\n\nKib"),
        _msg("B", "Malissa Mathis, Registrar", "Two", "Is platform scope something you expect to revisit?\n\nKib"),
    ]
    assert not [f for f in hard_checks(messages) if f.rule == "shared-closing"]


def test_a_real_title_containing_a_slash_is_not_a_placeholder():
    """"Chelsea Ward, Scheduling/NCAA Coordinator" is one person's actual job.
    "Provost / Vice Chancellor for Academic Affairs" is two roles nobody chose
    between. The spaces are the difference."""
    real = [
        _msg("A", "Chelsea Ward, Scheduling/NCAA Coordinator", "One", "Mazevo spans academic spaces and events here."),
        _msg("A", "Malissa Mathis, Registrar", "Two", "Responsibility still crosses Records and college schedulers."),
    ]
    assert not [f for f in hard_checks(real) if f.rule == "placeholder-recipient"]

    slot = [
        _msg("A", "Provost / Vice Chancellor for Academic Affairs", "One", "Mazevo spans academic spaces and events here."),
        _msg("A", "Malissa Mathis, Registrar", "Two", "Responsibility still crosses Records and college schedulers."),
    ]
    assert [f for f in hard_checks(slot) if f.rule == "placeholder-recipient"]


# --- the self-check is a claim, not evidence -------------------------------

SELF_REPORT = """## The producing agent's differentiation check

1. Shared opening structure: **No**
2. Shared closing move: **No**
3. Details demonstrably recipient-specific: **Yes**
4. Obvious common template: **No**
5. Sentence forms and lengths varied: **Yes**
"""


def test_the_self_report_parses_into_its_five_answers():
    answers = parse_self_report(SELF_REPORT)
    assert set(answers) == {1, 2, 3, 4, 5}
    assert answers[2].startswith("Shared closing move")


def test_a_reported_pass_is_printed_against_the_count_that_contradicts_it():
    """Kib's rule, 3 September: the self-check stays and stops being evidence.
    Where an answer is checkable and wrong, the contradiction is named at the
    point of the claim rather than left for a reviewer pass to find."""
    findings = [Finding(FAIL, "shared-closing", "two messages close the same way")]
    conflicts = self_report_conflicts(SELF_REPORT, findings)
    assert len(conflicts) == 1
    assert conflicts[0].rule == "self-report-contradicted"
    assert "question 2" in conflicts[0].detail
    assert "shared-closing" in conflicts[0].detail


def test_the_judgement_questions_are_not_contradicted_by_arithmetic():
    """Nothing computable answers questions 3 and 5, and the checker does not
    pretend otherwise."""
    findings = [Finding(FAIL, "shared-closing", "x"), Finding(FAIL, "duplicate-subject", "y")]
    questions = {c.detail.split(",")[0] for c in self_report_conflicts(SELF_REPORT, findings)}
    assert questions == {"question 2", "question 4"}


def test_an_agent_that_admits_the_failure_is_not_flagged_for_it():
    admitted = SELF_REPORT.replace(
        "2. Shared closing move: **No**", "2. Shared closing move: **Yes**, two of them"
    )
    findings = [Finding(FAIL, "shared-closing", "x")]
    assert not self_report_conflicts(admitted, findings)


def test_no_self_report_means_nothing_to_contradict():
    assert self_report_conflicts("## Copy batch\n\nsome copy.", [Finding(FAIL, "shared-closing", "x")]) == []


def test_the_sign_off_block_is_not_the_message():
    """4 September, five clean messages, nine failures, all of them the
    signature. "Kib Cochran / Solutions Engineer / SEAtS Software" flattens to
    six words, so the word-count floor that caught a bare "Kib" let it through.
    A checker that fails good copy stops being read."""
    from seats_prospecting.differentiation import strip_signature

    body = (
        "Six coaches supporting roughly 2,100 students is meaningful reach.\n\n"
        "How does that visibility work at TCC today?\n\n"
        "Kib Cochran  \nSolutions Engineer  \nSEAtS Software"
    )
    assert strip_signature(body).endswith("work at TCC today?")

    messages = [
        _msg("Tulsa Community College", "Dewayne Dickens, Senior Director", "Six coaches", body),
        _msg(
            "Creighton University",
            "Mary Ann Tietjen, Senior Director",
            "First-Flight",
            "First-Flight launched this spring inside a wider campus effort.\n\n"
            "What evidence will matter most when Creighton reviews the pilot?\n\n"
            "Kib Cochran  \nSolutions Engineer  \nSEAtS Software",
        ),
    ]
    rules = {f.rule for f in check(messages, as_of=date(2026, 9, 4))}
    assert "duplicate-sentence" not in rules
    assert "shared-closing" not in rules
    assert "repeated-offer" not in rules


def test_a_repeated_closing_is_still_caught_under_the_signature():
    close = "Would an Engagement Readiness session help establish the measures?"
    sign = "\n\nKib Cochran  \nSolutions Engineer  \nSEAtS Software"
    messages = [
        _msg("A", "Provost", "One", f"Retention rose to 73.4% this year. {close}{sign}"),
        _msg("B", "Chief Financial Officer", "Two", f"Headcount fell 4.3% in spring. {close}{sign}"),
    ]
    rules = {f.rule for f in hard_checks(messages)}
    assert "shared-closing" in rules


def test_the_last_message_does_not_swallow_what_follows_it():
    """4 September, seen in Apollo. The final draft in the batch carried the
    producing agent's differentiation check inside the email body, because a
    message block ended only at the next institution and the last one has none.

    It also broke the sign-off stripper: with the check block at the end, the
    trailing lines no longer looked like a signature, so "Kib Cochran /
    Solutions Engineer / SEAtS Software" stayed in the middle of the draft.
    """
    from seats_prospecting.differentiation import strip_signature

    artifact = "\n".join(
        [
            "## Copy batch",
            "",
            "### Wiley University",
            "",
            "**To: Shaniqua Adams, Executive Director**",
            "**Subject:** The workload behind Wiley's retention growth",
            "",
            "Wiley reported that 117 of this fall's 144 additional students were returning.",
            "",
            "Kib Cochran",
            "Solutions Engineer",
            "SEAtS Software",
            "",
            "## The producing agent's differentiation check",
            "",
            "1. Shared opening structure: **No.**",
            "2. Shared closing move: **No.**",
        ]
    )
    messages = parse_messages(artifact)
    assert len(messages) == 1
    body = messages[0].body
    assert "returning" in body
    assert "differentiation check" not in body
    assert "Shared opening structure" not in body
    assert "Kib Cochran" not in strip_signature(body)


def test_a_cadence_is_not_a_single_threaded_account():
    """4 September: twelve follow-up touches, twelve single-threaded failures.

    The rule asks whether a batch of first touches reached more than one role at
    an account. A cadence is the same person several times, deliberately, so the
    question does not apply and firing it on every message buries the findings
    that do.
    """
    messages = [
        _msg("Tulsa Community College, Dewayne Dickens, touch 2, day 14", "Dewayne Dickens, Senior Director", "Queue", "What determines who a coach contacts first when several need support?"),
        _msg("Tulsa Community College, Dewayne Dickens, touch 3, day 35", "Dewayne Dickens, Senior Director", "A test", "Take one coach's caseload and mark where context was assembled by hand."),
        _msg("Wiley University, Shaniqua Adams, touch 2, day 14", "Shaniqua Adams, Executive Director", "Advising load", "Where does a growing cohort make a change in pattern harder to see?"),
    ]
    assert not [f for f in hard_checks(messages) if f.rule == "single-threaded"]


def test_a_first_touch_batch_still_reports_single_threading():
    messages = [
        _msg("Wiley University", "Shaniqua Adams, Executive Director", "One", "Returning students drove most of the growth this fall here."),
        _msg("Creighton University", "Mary Ann Tietjen, Senior Director", "Two", "First-Flight launched this spring inside a wider effort."),
    ]
    rules = [f.detail for f in hard_checks(messages) if f.rule == "single-threaded"]
    assert len(rules) == 2


def test_the_institution_is_read_from_a_compound_heading():
    from seats_prospecting.differentiation import institution_of

    message = _msg("Tulsa Community College, Dewayne Dickens, touch 2, day 14", "x, y", "s", "b")
    assert institution_of(message) == "Tulsa Community College"
