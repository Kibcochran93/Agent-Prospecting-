"""The copy standard is one file and it is closed.

Four consecutive rewrites of the M3 batch on 3 and 4 September 2026 named four
different constructions, each one introduced by the rewrite that fixed the last.
The cause was not strictness. It was an open-ended standard held in two places:
the Campaign Builder wrote against "vary the batch" and the Reviewer graded
against "would a recipient notice a template", and neither has a stopping point.

Every test here asserts a boundary. The grounds are a closed list, the floor
names what is not a defect, and the two agents read the same bytes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seats_prospecting.agents_def import PROMPTS, build_agents

STANDARD = PROMPTS / "_shared_copy_standard.md"


def flat(text: str) -> str:
    """Casefolded, with runs of whitespace collapsed.

    The prompts are hard-wrapped at 79 columns, so a phrase these tests pin
    lands with a newline in the middle of it. Matching the wrapped form would
    make every test here break on a reflow that changed nothing.
    """
    return " ".join(text.split()).casefold()

# The six. A ground that leaves this file stops being a rewrite ground, so the
# list is pinned here rather than counted from the prose.
GROUNDS = (
    "never-say hit",
    "unsupported claim about the recipient",
    "no recipient-specific anchor",
    "same institution, same problem",
    "register mismatch",
    "an invented motion",
)

# The floor. Each of these was the sole basis of at least one rewrite verdict.
FLOOR = (
    "closing with a question",
    "one sentence naming seats",
    "fact, then implication, then ask",
    "shared domain vocabulary",
    "family resemblance",
)


@pytest.fixture(scope="module")
def network():
    return build_agents()


@pytest.fixture(scope="module")
def standard() -> str:
    return STANDARD.read_text(encoding="utf-8")


def test_the_standard_exists_as_one_file():
    assert STANDARD.is_file(), (
        "The standard was split across two prompts until 4 September and they "
        "drifted. It lives in one file so that cannot happen again."
    )


def test_both_agents_read_the_same_bytes(network, standard):
    writer = network["campaign"].instructions
    grader = network["reviewer"].instructions
    body = standard.strip()
    assert body in writer, "The Campaign Builder no longer reads the copy standard."
    assert body in grader, "The Reviewer no longer reads the copy standard."


@pytest.mark.parametrize("ground", GROUNDS)
def test_every_rewrite_ground_is_named(standard, ground):
    assert ground in flat(standard), f"Rewrite ground went missing: {ground}"


@pytest.mark.parametrize("item", FLOOR)
def test_the_floor_still_names_what_is_not_a_defect(standard, item):
    assert item in flat(standard), (
        f"The floor lost {item!r}. Each item on it was the sole basis of a real "
        "rewrite verdict, and losing one reopens that verdict."
    )


def test_the_grounds_are_stated_as_closed(standard):
    lowered = flat(standard)
    assert "and only when" in lowered, (
        "The grounds have to read as exhaustive. 'Rewritten when one of these is "
        "true' invites a seventh ground; 'when and only when' does not."
    )


def test_a_finding_must_quote_a_sentence(standard):
    lowered = flat(standard)
    assert "quote the sentence" in lowered
    assert "without the sentence is not a finding" in lowered


def test_the_reviewer_cannot_rewrite_on_a_ground_it_cannot_quote(network):
    grader = flat(network["reviewer"].instructions)
    assert "a ground you cannot quote is a ground that passed" in grader
    assert "the verdict is ships" in grader


def test_the_reviewer_separates_observations_from_the_verdict(network):
    """Phase 3 moved this from a heading in prose to a field in the schema.

    The separation is the same and it is now enforced rather than requested: a
    ships verdict carrying findings is rejected, and so is a rewrite with none.
    """
    grader = flat(network["reviewer"].instructions)
    assert "goes in `observations`" in grader
    assert "does not change the verdict" in grader
    assert "it changes nothing" in grader


def test_cadence_touches_keep_the_full_strength_rule(standard):
    lowered = flat(standard)
    assert "cadence touches are graded differently" in lowered
    assert "the floor above does not soften it" in lowered, (
        "Touch 4 repeating touch 2 was a correct finding on 4 September. The "
        "floor is for first touches and must not reach the cadence rule."
    )


def test_the_machine_owns_the_countable_checks(standard):
    lowered = flat(standard)
    assert "seats-check-batch" in lowered
    assert "do not spend a review pass re-deriving them" in lowered


def test_the_ratchet_is_named_as_a_standard_problem(network):
    grader = flat(network["reviewer"].instructions)
    assert "the standard is chasing itself" in grader, (
        "The escalation rule ran one way only: three failures meant the writer "
        "was misconfigured. Three rewrites on three different grounds means the "
        "standard is, and the Reviewer has to be able to say so."
    )


def test_the_writer_is_told_not_to_swap_one_uniform_move_for_another(network):
    writer = flat(network["campaign"].instructions)
    assert "uniform construction" in writer, (
        "Each rewrite replaced a shared close with a shared offer, then a "
        "shared offer with a shared imperative. That is the failure to name."
    )


def test_the_unbounded_questions_are_gone_from_both_prompts(network):
    # "Would they notice a template?" has no stopping point: any two messages
    # from one vendor about one product resemble each other somewhere.
    for role in ("campaign", "reviewer"):
        text = flat(network[role].instructions)
        assert "notice a template?" not in text, (
            f"The unbounded question is posed again in the {role} prompt. The "
            "standard may recount that it was retired; it may not ask it."
        )
        assert "do any two share a closing move" not in text
        assert "sentence lengths and shapes varied" not in text


def test_the_reviewer_still_writes_no_copy(network):
    grader = flat(network["reviewer"].instructions)
    assert "never write copy" in grader, (
        "Recalibrating the threshold must not loosen the prohibition that keeps "
        "the Reviewer from grading its own work."
    )


def test_the_self_report_still_carries_no_standing(network):
    grader = flat(network["reviewer"].instructions)
    assert "a claim about the batch, not evidence about it" in grader


def test_the_writer_asks_a_cadence_question_too(network):
    """The Reviewer caught this on the first recalibrated pass.

    The five self-check questions are all batch-level: they ask whether five
    messages to five people differ from each other. A cadence is one person
    reading four messages in order, and every batch-level answer can pass while
    touch 4 restates touch 3. That is exactly what it did for two of four
    recipients on 4 September.
    """
    writer = flat(network["campaign"].instructions)
    assert "when the batch is a cadence" in writer
    assert "a different reason to reply" in writer


def test_the_grader_states_which_object_it_is_grading(network):
    grader = flat(network["reviewer"].instructions)
    assert "first touches or a cadence?" in grader, (
        "The two are graded on different rules, so the verdict has to say which "
        "one was applied before it says ships or rewrite."
    )
