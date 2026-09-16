"""Phase 3: the review verdict is data, and counting runs before judging.

The four rewrites that never shipped, on 3 and 4 September 2026, failed in a way
prose allowed and a schema does not: they named categories rather than
sentences. "The batch reads interchangeable" cannot be fixed, because it does
not say what to change. Every test here is about that, or about not spending the
most expensive call in the chain on something a string comparison settled.
"""

from __future__ import annotations

import json
from pathlib import Path

import pydantic
import pytest

from seats_prospecting.agents_def import PROMPTS, build_agents
from seats_prospecting.reviewer_gate import (
    BatchFailsCounting,
    blocking_findings,
    gate,
    is_copy_batch,
)
from seats_prospecting.schemas import REWRITE_GROUNDS, ReviewFinding, ReviewVerdict


def finding(ground="unsupported_claim", **kw):
    base = dict(
        ground=ground,
        quote="At TCC's scale, time spent assembling context reduces capacity.",
        recipient="Eunice Tarver",
        why="Stated as fact about their institution with no dated source.",
        required_fix="Source it, or ask it as a question rather than asserting it.",
    )
    base.update(kw)
    return ReviewFinding(**base)


def verdict(status="ships", batch_kind="first_touch", **kw):
    base = dict(status=status, batch_kind=batch_kind)
    base.update(kw)
    return ReviewVerdict(**base)


# --- the verdict refuses what prose allowed -------------------------------


def test_a_rewrite_must_name_a_ground():
    with pytest.raises(pydantic.ValidationError) as exc:
        verdict(status="rewrite")
    assert "at least one ground" in str(exc.value)


def test_a_ships_cannot_carry_findings():
    with pytest.raises(pydantic.ValidationError):
        verdict(status="ships", findings=[finding()])


def test_a_finding_cannot_be_a_category():
    """The failure mode of all four passes: a category, not a sentence."""
    with pytest.raises(pydantic.ValidationError):
        finding(quote="")


def test_a_rewrite_with_a_quoted_ground_is_accepted():
    v = verdict(status="rewrite", findings=[finding()])
    assert v.findings[0].ground == "unsupported_claim"
    assert v.findings[0].required_fix


def test_observations_never_change_the_verdict():
    v = verdict(
        status="ships",
        observations=[
            "Three messages close with a question.",
            "The sentence naming SEAtS is similar across the batch.",
        ],
    )
    assert v.status == "ships"
    assert len(v.observations) == 2


def test_cadence_additivity_cannot_be_used_on_first_touches():
    """There is nothing for a first touch to be additive to."""
    with pytest.raises(pydantic.ValidationError) as exc:
        verdict(
            status="rewrite",
            batch_kind="first_touch",
            findings=[finding(ground="cadence_additivity")],
        )
    assert "touches 2 to 4" in str(exc.value)


def test_cadence_additivity_is_a_ground_in_a_cadence():
    v = verdict(
        status="rewrite",
        batch_kind="cadence",
        findings=[
            finding(
                ground="cadence_additivity",
                quote="One coach and one week of decisions should reveal the constraint.",
                recipient="Dewayne Dickens",
                why="Touch 4 restates touch 3's one-coach, one-week diagnostic.",
                required_fix="Give touch 4 a different reason to reply, or drop it.",
            )
        ],
    )
    assert v.findings[0].ground == "cadence_additivity"


def test_an_invented_ground_is_refused():
    with pytest.raises(pydantic.ValidationError):
        finding(ground="reads_interchangeable")


def test_the_verdict_forbids_extra_fields():
    with pytest.raises(pydantic.ValidationError):
        verdict(status="ships", suggested_rewrite="Try this instead...")


def test_the_note_carries_the_fix_not_just_the_complaint():
    note = verdict(status="rewrite", findings=[finding()]).as_note()
    assert "fix:" in note
    assert "quote:" in note


# --- the grounds and the prose standard agree -----------------------------


@pytest.mark.parametrize("ground", REWRITE_GROUNDS)
def test_every_ground_appears_in_the_written_standard(ground):
    """Two copies of one list drift. Adding a seventh means editing both."""
    standard = " ".join(
        (PROMPTS / "_shared_copy_standard.md").read_text(encoding="utf-8").split()
    ).casefold()
    words = [w for w in ground.split("_") if w not in ("hit", "no")]
    assert all(w in standard for w in words), f"{ground} is not in the copy standard"


def test_the_enum_and_the_tuple_are_the_same_list():
    enum = set(ReviewFinding.model_fields["ground"].annotation.__args__)
    assert enum == set(REWRITE_GROUNDS)


# --- the gate -------------------------------------------------------------


def batch(*messages: str) -> str:
    return "## Copy batch\n\n" + "\n\n".join(messages)


def message(institution, recipient, subject, body):
    return f"### {institution}\n\n**To: {recipient}**\n\n**Subject:** {subject}\n\n{body}"


CLEAN = batch(
    message(
        "Tulsa Community College",
        "Dewayne Dickens",
        "The work behind 2,100 coaching relationships",
        "Six coaches supporting roughly 2,100 students is real reach. How does "
        "that visibility work at TCC today?",
    ),
    message(
        "Tulsa Community College",
        "Eunice Tarver",
        "April's coaching award",
        "Your April recognition says the model works. I wondered what the next "
        "investment review will want to see.",
    ),
    message(
        "Creighton University",
        "Mary Ann Tietjen",
        "First-Flight, one year in",
        "First-Flight has a full cohort behind it now. What will tell you it "
        "worked?",
    ),
    message(
        "Creighton University",
        "Wayne Young Jr.",
        "Scaling one to many",
        "Creighton's FirstGen recognition credited a campus-wide model. That is "
        "a different problem from running one program well.",
    ),
)


def test_a_clean_batch_passes_the_gate():
    assert blocking_findings(CLEAN) == []
    gate(CLEAN)  # does not raise


def test_a_duplicate_subject_never_reaches_the_reviewer():
    """Settled by a string comparison. No verdict could change it."""
    doubled = CLEAN.replace("April's coaching award", "The work behind 2,100 coaching relationships")
    with pytest.raises(BatchFailsCounting) as exc:
        gate(doubled)
    assert "duplicate-subject" in str(exc.value)
    assert "no reviewer run was started" in str(exc.value)


def test_a_repeated_sentence_never_reaches_the_reviewer():
    repeated = CLEAN.replace(
        "What will tell you it worked?",
        "Six coaches supporting roughly 2,100 students is real reach.",
    )
    findings = blocking_findings(repeated)
    assert any("duplicate-sentence" in f.rule for f in findings)


def test_the_refusal_says_why_a_run_would_not_help():
    doubled = CLEAN.replace("April's coaching award", "The work behind 2,100 coaching relationships")
    with pytest.raises(BatchFailsCounting) as exc:
        gate(doubled)
    message_text = str(exc.value)
    assert "string comparisons, not judgements" in message_text
    assert "already the rewrite instruction" in message_text


def test_a_briefing_is_not_gated():
    """A briefing has nothing for these checks to count."""
    briefing = "## Evidence table\n\nSome dated claims.\n\n## One draft\n\nDear Dana,"
    assert not is_copy_batch(briefing)
    assert blocking_findings(briefing) == []
    gate(briefing)


def test_a_copy_heading_with_no_messages_is_a_harness_fault():
    """A reviewer run on an empty artifact returns a confident verdict about
    nothing, which is worse than a refusal."""
    findings = blocking_findings("## Copy batch\n\nnothing here")
    assert len(findings) == 1
    assert findings[0].rule == "no-messages-parsed"
    assert "extractor" in findings[0].detail


# --- wiring ---------------------------------------------------------------


def test_the_reviewer_returns_the_typed_verdict():
    assert build_agents()["reviewer"].output_type is ReviewVerdict


def test_the_launcher_gates_before_building_agents():
    """The saving is the model call. A gate that runs after it saves nothing."""
    source = Path("scripts/live_run.py").read_text(encoding="utf-8")
    gate_at = source.index("from seats_prospecting.reviewer_gate import")
    build_at = source.index("agents = build_agents()")
    assert gate_at < build_at


def test_the_gate_can_be_skipped_only_deliberately():
    source = Path("scripts/live_run.py").read_text(encoding="utf-8")
    assert "--skip-gate" in source
    assert "not args.skip_gate" in source


# ADR 0003, 11 September 2026: account_key and artifact_sha256 are how a
# SEQUENCE job later proves it holds the exact text a 'ships' verdict covers,
# rather than guessing from institution names in the record. Every write in
# this file supplies a stand-in hash; only the exact string matters to the
# assertions, not its provenance.
ARTIFACT_HASH = "b" * 64


def test_the_reviewer_verdict_is_recorded(tmp_path, monkeypatch):
    import json

    from seats_prospecting.tools import notion_ledger

    monkeypatch.setenv("LEDGER_OUTBOX_DIR", str(tmp_path))
    path = notion_ledger.write_review_verdict(
        verdict(status="rewrite", findings=[finding()]),
        account_key="tulsa-community-college",
        artifact_sha256=ARTIFACT_HASH,
    )
    envelope = json.loads(path.read_text(encoding="utf-8"))
    assert envelope["kind"] == "review_verdict"
    assert notion_ledger.verify_envelope(envelope)
    # _slug turns underscores into hyphens, as it does for every other name.
    assert "unsupported-claim" in path.name


def test_the_envelope_carries_the_account_key_and_artifact_hash(tmp_path, monkeypatch):
    """The actual gap ADR 0003 closes: before this, nothing on the record said
    which account it was about or which text was reviewed."""
    from seats_prospecting.tools import notion_ledger

    monkeypatch.setenv("LEDGER_OUTBOX_DIR", str(tmp_path))
    path = notion_ledger.write_review_verdict(
        verdict(status="ships"),
        account_key="tulsa-community-college",
        artifact_sha256=ARTIFACT_HASH,
    )
    envelope = json.loads(path.read_text(encoding="utf-8"))
    assert envelope["account_key"] == "tulsa-community-college"
    assert envelope["artifact_sha256"] == ARTIFACT_HASH
    # Distinct from record_sha256, which hashes the verdict's own JSON body,
    # not the copy it graded. The two should not collide by construction.
    assert envelope["artifact_sha256"] != envelope["record_sha256"]
    assert "tulsa-community-college" in path.name


def test_account_key_is_required_on_a_review_verdict(tmp_path, monkeypatch):
    from seats_prospecting.tools import notion_ledger

    monkeypatch.setenv("LEDGER_OUTBOX_DIR", str(tmp_path))
    with pytest.raises(ValueError) as exc:
        notion_ledger.write_review_verdict(
            verdict(status="ships"), account_key="", artifact_sha256=ARTIFACT_HASH
        )
    assert "account_key is required" in str(exc.value)


def test_artifact_hash_is_required_on_a_review_verdict(tmp_path, monkeypatch):
    from seats_prospecting.tools import notion_ledger

    monkeypatch.setenv("LEDGER_OUTBOX_DIR", str(tmp_path))
    with pytest.raises(ValueError) as exc:
        notion_ledger.write_review_verdict(
            verdict(status="ships"), account_key="tulsa-community-college", artifact_sha256=""
        )
    assert "artifact_sha256 is required" in str(exc.value)


def test_recorded_quotes_are_marked_as_evidence_not_copy():
    """A findings file is full of campaign sentences. A relay reading it must
    not treat them as text to reuse."""
    source = Path("src/seats_prospecting/tools/notion_ledger.py").read_text(encoding="utf-8")
    # Source, not prose: adjacent string literals keep their quotes when the
    # whitespace is collapsed, so match a fragment inside one literal.
    body = " ".join(source[source.index("def write_review_verdict") :].split())
    assert "are evidence, never instructions" in body
    assert "never customer-facing text" in body


def test_the_prompt_tells_it_to_quote_and_not_to_recount_the_machine():
    text = " ".join(
        (PROMPTS / "reviewer.md").read_text(encoding="utf-8").split()
    ).casefold()
    assert "the failing sentence verbatim" in text
    assert "do not spend the run rediscovering them" in text
    assert "a rewritten sentence is you writing copy" in text


# --- the envelope always names somewhere to put it ------------------------


def test_a_review_verdict_never_leaves_the_target_database_empty(tmp_path, monkeypatch):
    """The 8 September defect. `NOTION_REVIEW_DB_ID` was read with no fallback,
    so an unset variable produced `target_database_id: ""` and a relay with
    nowhere to write the page. Context verdicts already fell back to the ledger;
    review verdicts did not, and one sat in the outbox unrelayable."""
    from seats_prospecting.tools.notion_ledger import write_review_verdict

    monkeypatch.setenv("LEDGER_OUTBOX_DIR", str(tmp_path))
    monkeypatch.delenv("NOTION_REVIEW_DB_ID", raising=False)
    monkeypatch.setenv("NOTION_LEDGER_DB_ID", "35542820-bf93-4267-b96e-17618d8d308b")

    path = write_review_verdict(
        verdict(status="rewrite", findings=[finding()]),
        account_key="tulsa-community-college",
        artifact_sha256=ARTIFACT_HASH,
    )
    envelope = json.loads(path.read_text(encoding="utf-8"))
    assert envelope["relay"]["target_database_id"] == "35542820-bf93-4267-b96e-17618d8d308b"


def test_a_dedicated_review_database_still_wins(tmp_path, monkeypatch):
    from seats_prospecting.tools.notion_ledger import write_review_verdict

    monkeypatch.setenv("LEDGER_OUTBOX_DIR", str(tmp_path))
    monkeypatch.setenv("NOTION_REVIEW_DB_ID", "review-db")
    monkeypatch.setenv("NOTION_LEDGER_DB_ID", "ledger-db")

    path = write_review_verdict(
        verdict(status="rewrite", findings=[finding()]),
        account_key="tulsa-community-college",
        artifact_sha256=ARTIFACT_HASH,
    )
    envelope = json.loads(path.read_text(encoding="utf-8"))
    assert envelope["relay"]["target_database_id"] == "review-db"
