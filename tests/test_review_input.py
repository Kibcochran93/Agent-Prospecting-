"""The Reviewer's input is built, not assembled by hand.

Every test here asserts something is absent from the artifact the Reviewer sees.
The two live reviewer passes both failed on lines that were never copy: the
Campaign Builder telling Kib which slice to confirm and what still needed doing
by hand. Concatenating checkpoint logs is what put them there, so the harness is
what these tests hold in place.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seats_prospecting import review_input
from seats_prospecting.review_input import (
    DirectiveLeak,
    NoCopyFound,
    ReviewInputError,
    build,
    scan_for_directives,
)

ROOT = Path(__file__).resolve().parents[1]

# The shape of a real run log: checkpoint prose, then the batch, then the CLI's
# own trailer. Lines marked here are the ones that must not reach the Reviewer.
LOG = """
## Checkpoint 2: Trigger research

Confirm the tighter, recommended slice before checkpoint 4 buying-group mapping.

1. **University of Tennessee at Chattanooga**
   - Retention rose to 73.4%

## Checkpoint 3: List-build logic

Include an institution only when all are true. Searched Apollo, 41 matches.

## Rewritten batch

**Structure:** two touches per institution. Names require manual personalization.
CRM ownership remains unverified.

### University of Tennessee at Chattanooga

**To: Vice Chancellor for Enrollment Management**
**Subject:** The 80% retention goal

You set a target of 80% first-year retention by 2030 back in January.

### Murray State University

**To: Provost**
**Subject:** Reading two different indicators

Retention held at 78% while spring headcount fell 4.3%.

## Differentiation check

1. Shared opening structure: no.
2. Shared closing move: no.

--- run audit ---
knowledge.read us-abm-playbook-v3-1.md
dispatched to: campaign at 2026-09-03T19:00:22Z
"""


def test_checkpoint_prose_cannot_reach_the_reviewer():
    artifact = build(LOG)
    assert "checkpoint" not in artifact.text.casefold()
    assert "Trigger research" not in artifact.text
    assert "List-build logic" not in artifact.text
    assert "Checkpoint 2: Trigger research" in artifact.dropped[0]


def test_the_producing_agents_note_to_kib_is_dropped_with_the_preamble():
    """It sits inside the batch section, above the first institution, so the
    section heading alone is not enough to keep it out."""
    artifact = build(LOG)
    assert "manual personalization" not in artifact.text.casefold()
    assert "CRM ownership remains unverified" not in artifact.text


def test_the_messages_and_the_self_check_survive():
    artifact = build(LOG)
    assert "80% first-year retention by 2030" in artifact.text
    assert "Reading two different indicators" in artifact.text
    assert review_input.CHECK_HEADING in artifact.text
    assert "Shared closing move: no." in artifact.text


def test_the_run_signoff_under_the_check_heading_is_dropped():
    """The producing agent signs off under the differentiation-check heading with
    what it did about a sequence and which ledger file it wrote. Bookkeeping for
    Kib, and the exact shape that made the first real build refuse."""
    log = LOG.replace(
        "2. Shared closing move: no.",
        "2. Shared closing move: no.\n\nNo sequence was created.\n\n"
        "Ledger draft: `20260903T190022Z-campaign-builder.json`",
    )
    artifact = build(log)
    assert "Shared closing move: no." in artifact.text
    assert "ledger" not in artifact.text.casefold()
    assert "No sequence was created" not in artifact.text


def test_the_cli_trailer_is_not_artifact():
    artifact = build(LOG)
    assert "run audit" not in artifact.text.casefold()
    assert "dispatched to" not in artifact.text.casefold()
    assert "us-abm-playbook" not in artifact.text


def test_the_frame_carries_no_run_context():
    """The second pass was told a prior review had returned a rewrite. A reviewer
    holding that grades against the last verdict instead of against the standard,
    so the frame is a constant and nothing per-run can be attached to it."""
    artifact = build(LOG)
    assert artifact.text.startswith(review_input.FRAME_OPEN)
    head = artifact.text.split(review_input.ARTIFACT_BEGINS)[0]
    for primer in ("second pass", "prior review", "rewrite", "verdict", "M3", "SACSCOC"):
        assert primer.casefold() not in head.casefold()


def test_workflow_language_inside_the_copy_is_refused_not_shipped():
    leaky = LOG.replace(
        "You set a target of 80% first-year retention by 2030 back in January.",
        "Confirm at checkpoint 4 before we log this to the ledger.",
    )
    with pytest.raises(DirectiveLeak) as excinfo:
        build(leaky)
    assert "checkpoint" in str(excinfo.value)
    assert "line " in str(excinfo.value), "the refusal has to name the line"


@pytest.mark.parametrize(
    "term",
    ["Apollo", "HubSpot", "the ledger", "our playbook", "the battlecard", "work order"],
)
def test_system_names_never_travel_inside_copy(term):
    leaky = LOG.replace("**Subject:** The 80% retention goal", f"**Subject:** {term} note")
    with pytest.raises(DirectiveLeak):
        build(leaky)


def test_a_log_with_no_batch_section_is_refused():
    """Two ways to refuse the same log, and both of them refuse: asked for copy
    there is none, and read as a briefing it is checkpoint prose."""
    log = "## Checkpoint 1: Segment definition\n\nRegional publics, 5k to 15k.\n"
    with pytest.raises(NoCopyFound):
        build(log, kind="copy")
    with pytest.raises(ReviewInputError):
        build(log)


def test_the_historical_leak_would_have_been_caught():
    """The exact input that burned reviewer pass two."""
    path = ROOT / "smoke-test" / "reviewer-in-2.txt"
    if not path.exists():
        pytest.skip("smoke-test artifact not present")
    hits = scan_for_directives(path.read_text(encoding="utf-8", errors="replace"))
    assert hits, "the scanner misses the leak it was written for"
    assert any(term == "checkpoint" for _, term, _ in hits)


def test_the_builder_has_no_write_or_network_shape():
    forbidden = ("write", "post", "send", "create", "update", "delete")
    for name in dir(review_input):
        if name.startswith("_"):
            continue
        assert not any(name.lower().startswith(f) for f in forbidden), (
            f"review_input gained a write-shaped function: {name}"
        )


# --- briefings ------------------------------------------------------------

BRIEFING_LOG = """TRAIL: ['START Director', 'HANDOFF -> Prospect Briefing', 'TOOL hubspot_find_account']
AUDIT: ['2026-09-03T19:49:21+00:00 handoff -> Prospect Briefing: {"target":"Ronnie Williams",\
"motion":"unclear","question":"Is this account worth working?"}']
LAST AGENT: Prospect Briefing
=== OUTPUT ===
## 1. Contact and institution facts

Ronnie Williams is not currently verifiable as a UCA employee.

## 3. Internal context

- HubSpot owner: Cal O'Donovan. No associated deals as checked 3 September 2026.

## 8. Review notes

Account owner: Cal O'Donovan. This draft is not cleared to send.

Ledger draft written to `20260903T195021Z-prospect-briefing.json`.
It is awaiting the separate Notion relay.
"""


def test_the_launcher_header_never_reaches_the_reviewer():
    """The AUDIT line carries the Director's work order. Handing a raw run log to
    the Reviewer would put the dispatch inside the object under review."""
    artifact = build(BRIEFING_LOG)
    assert "work order" not in artifact.text.casefold()
    assert "Is this account worth working?" not in artifact.text
    assert "TRAIL" not in artifact.text
    assert "LAST AGENT" not in artifact.text


def test_a_briefing_is_reviewed_whole():
    """Every section is under the Reviewer's briefing checklist, so nothing is
    filtered by heading the way a copy batch is."""
    artifact = build(BRIEFING_LOG)
    assert "Contact and institution facts" in artifact.text
    assert "Internal context" in artifact.text
    assert "Review notes" in artifact.text
    assert artifact.dropped == []


def test_a_briefing_may_name_the_systems_a_copy_batch_may_not():
    """HubSpot in an email to a Provost is a leak. HubSpot in a briefing's
    internal context section is the section doing its job."""
    artifact = build(BRIEFING_LOG)
    assert "HubSpot owner" in artifact.text
    with pytest.raises(NoCopyFound):
        build(BRIEFING_LOG, kind="copy")


def test_the_run_signoff_is_dropped_from_a_briefing_too():
    artifact = build(BRIEFING_LOG)
    assert "Ledger draft written to" not in artifact.text
    assert "awaiting the separate Notion relay" not in artifact.text


def test_workflow_prose_in_a_briefing_still_refuses():
    leaky = BRIEFING_LOG.replace(
        "Ronnie Williams is not currently verifiable as a UCA employee.",
        "Confirm at checkpoint 2 before the Reviewer sees this.",
    )
    with pytest.raises(DirectiveLeak):
        build(leaky)


def test_the_briefing_frame_carries_no_run_context():
    artifact = build(BRIEFING_LOG)
    head = artifact.text.split(review_input.ARTIFACT_BEGINS)[0]
    assert head.strip() == review_input.FRAME_OPEN_BRIEFING


def test_a_briefings_one_draft_section_is_not_mistaken_for_a_batch():
    """Caught on the first live briefing. Section 7 of every briefing is called
    "One draft", the heading matched, and the extractor kept that section and
    threw the other seven away. A copy batch addresses its messages; a briefing's
    draft is a letter with a salutation."""
    log = BRIEFING_LOG.replace(
        "## 8. Review notes",
        "## 7. One draft\n\n**Subject:** Where EMS stops\n\nLisa,\n\nOne question.\n\nKib\n\n## 8. Review notes",
    )
    artifact = build(log)
    assert "Contact and institution facts" in artifact.text
    assert "Review notes" in artifact.text
    assert artifact.dropped == []


def test_the_producing_agents_heading_is_replaced_not_carried():
    """The first live M1 batch came in under "## Checkpoint 5: Manual-paste
    copy". The copy was clean; the heading was workflow prose, and carrying it
    refused the whole batch."""
    log = LOG.replace("## Rewritten batch", "## Checkpoint 5: Manual-paste copy")
    artifact = build(log)
    assert review_input.COPY_HEADING in artifact.text
    assert "checkpoint" not in artifact.text.casefold()
    assert "80% first-year retention by 2030" in artifact.text


def test_the_frame_labels_the_self_check_without_grading_it():
    """It stays in the artifact because a disagreement between the two readings
    is worth Kib seeing. It is labelled because it is a claim about the batch."""
    artifact = build(LOG)
    head = artifact.text.split(review_input.ARTIFACT_BEGINS)[0]
    assert review_input.FRAME_SELF_CHECK in head
    assert "claim, not evidence" in head
    for primer in ("prior review", "second pass", "M3", "SACSCOC"):
        assert primer.casefold() not in head.casefold()


def test_a_batch_with_no_self_check_gets_the_bare_frame():
    log = LOG.split("## Differentiation check")[0]
    artifact = build(log)
    head = artifact.text.split(review_input.ARTIFACT_BEGINS)[0]
    assert head.strip() == review_input.FRAME_OPEN


def test_messages_above_the_first_heading_are_still_the_batch():
    """The 4 September live batch. The Campaign Builder wrote five messages into
    H3 institution sections with no H2 above them, so the only H2 in the log was
    the differentiation check. The extractor kept the check, dropped every
    message, and the mechanical checker reported zero messages parsed and
    nothing to report. A silent pass is the one outcome that must not happen."""
    log = """=== OUTPUT ===
### Tulsa Community College
**To: Dewayne Dickens, Senior Director**
**Subject:** Six coaches, 2,100 students

Six coaches supporting roughly 2,100 students is meaningful reach.

### Creighton University
**To: Mary Ann Tietjen, Senior Director**
**Subject:** First-Flight's next learning cycle

First-Flight launched this spring inside a broader campus effort.

## Batch differentiation check

1. Shared opening structure: no.
2. Shared closing move: no.
"""
    artifact = build(log)
    assert "Dewayne Dickens" in artifact.text
    assert "Mary Ann Tietjen" in artifact.text
    assert review_input.COPY_HEADING in artifact.text
    assert review_input.CHECK_HEADING in artifact.text

    from seats_prospecting.differentiation import parse_messages

    assert len(parse_messages(artifact.text)) == 2


def test_staff_handoffs_are_copy_and_hand_back_is_workflow():
    """4 September: a clean batch was refused for the sentence "student
    touchpoints, staff handoffs and the evidence to review". Handoffs between
    staff are what the copy is about; handing back to the Director is workflow.
    """
    from seats_prospecting.review_input import WORKFLOW_TERMS, scan_for_directives

    copy = "I can sketch student touchpoints, staff handoffs and the evidence to review."
    assert not scan_for_directives(copy, terms=WORKFLOW_TERMS)

    for workflow in (
        "Hand back to the Director with the institution named.",
        "This run should handoff to the Prospect Briefing agent.",
        "Handback is for scope changes only.",
    ):
        assert scan_for_directives(workflow, terms=WORKFLOW_TERMS), workflow


# --- a headerless batch's sign-off (11 September 2026, Arkansas Mountain Home) --


def test_a_headerless_batchs_signoff_is_dropped():
    """The real defect: no '## Copy batch' heading anywhere -- campaign.md never
    tells the producing agent to write one -- so the fallback path took the
    whole preamble verbatim, including the producing agent's own closing note.
    That note landed inside the same block as the email body, which is the
    exact artifact-integrity failure this module exists to catch."""
    log = """=== OUTPUT ===
### Arkansas State University-Mountain Home
**To: Jessica Camp, Dean of Technology and Health Sciences**
**Subject:** A question on ASUMH's health sciences attendance record

Jessica,

With ASUMH's Certified Nursing Assistant and Practical Nursing programs
combining coursework and clinical hours, is either program federally defined
as clock-hour?

If so, I'd also be interested in whether attendance evidence is consolidated
in Banner, Canvas, or another system. SEAtS helps health sciences teams
maintain that record without adding work for faculty.

**Motion remains Unclear:** M9 is the closest fit, but clock-hour status,
system of record, and ownership are not verified.

**Status:** Draft only, not cleared to send. Step 1 awaits confirmation.
"""
    artifact = build(log)
    assert "without adding work for faculty" in artifact.text
    assert "Motion remains Unclear" not in artifact.text
    assert "not cleared to send" not in artifact.text
    assert "Step 1 awaits confirmation" not in artifact.text


def test_a_headerless_batch_with_no_signoff_is_untouched():
    """The common, clean case must not lose anything: no bold-led trailing
    paragraph means nothing is cut."""
    log = """=== OUTPUT ===
### Arkansas State University-Mountain Home
**To: Jessica Camp, Dean of Technology and Health Sciences**
**Subject:** A question on ASUMH's health sciences attendance record

Jessica,

With ASUMH's Certified Nursing Assistant and Practical Nursing programs
combining coursework and clinical hours, is either program federally defined
as clock-hour?

If so, I'd also be interested in whether attendance evidence is consolidated
in Banner, Canvas, or another system.
"""
    artifact = build(log)
    assert "clinical hours" in artifact.text
    assert "consolidated" in artifact.text


def test_drop_trailing_notes_keeps_everything_when_nothing_is_bold_led():
    text = "### Institution\n**To: A**\n**Subject:** B\n\nFirst paragraph.\n\nSecond paragraph."
    assert review_input._drop_trailing_notes(text) == text.strip()


def test_drop_trailing_notes_cuts_at_the_first_non_header_bold_paragraph():
    text = (
        "**To: A**\n**Subject:** B\n\n"
        "Real body text here.\n\n"
        "**Motion remains Unclear:** a note.\n\n"
        "**Status:** another note."
    )
    result = review_input._drop_trailing_notes(text)
    assert result == "**To: A**\n**Subject:** B\n\nReal body text here."


def test_drop_trailing_notes_on_an_all_bold_input_returns_it_unchanged():
    """No content paragraph at all: nothing to anchor a cut to, so the text is
    returned as-is rather than emptied. An empty artifact fails NoCopyFound
    downstream instead of silently vanishing here."""
    text = "**Motion remains Unclear:** a note.\n\n**Status:** another note."
    assert review_input._drop_trailing_notes(text) == text.strip()
