"""Account Context, phase 1: read only, advisory, and honest about its searches.

Four misses on 4 September 2026 are the reason this agent exists, and each one
has a test here that would have caught it:

* University of Central Arkansas researched twice for a Vice President who
  retired in 2021, with the retirement in the record.
* Wiley University written up as net new; HubSpot 8675080439, owner Miguel
  Pescador, missed by a domain query that ran and found nothing.
* Wayne Young Jr. given a second sequence while enrolled in a live one.
* Three of five contacts already existing, overwritten by a bulk create.

Like the rest of the suite, most of these assert an absence: no web search, no
write tool, no handoff, no verdict that outruns its evidence.
"""

from __future__ import annotations

import re
from pathlib import Path

import pydantic
import pytest

from seats_prospecting.agents_def import PROMPTS, build_agents
from seats_prospecting.schemas import ContextVerdict
from seats_prospecting.tools import hubspot


@pytest.fixture(scope="module")
def network():
    return build_agents()


def tool_names(agent) -> list[str]:
    return [getattr(t, "name", type(t).__name__) for t in agent.tools]


# --- topology -------------------------------------------------------------


def test_the_agent_exists_and_is_named(network):
    assert "context" in network
    assert network["context"].name == "Account Context"


def test_it_holds_no_web_search(network):
    """The absence is the control.

    An agent asked "have we been here before" that can also search the web
    answers from the web, because the web always has something and the CRM
    often does not. What comes back then is research, done before anyone
    decided the account was worth researching.
    """
    for name in tool_names(network["context"]):
        assert "websearch" not in name.lower().replace("_", "")
        assert "web_search" not in name.lower()


def test_it_holds_no_write_tool_of_any_kind(network):
    for name in tool_names(network["context"]):
        lowered = name.lower()
        for verb in ("create", "update", "delete", "send", "enroll", "activate", "approve", "write"):
            assert verb not in lowered, f"Account Context gained a write tool: {name}"


def test_it_cannot_write_the_ledger_it_informs(network):
    # Phase 1 records the verdict from outside the agent. An agent that writes
    # its own conclusion into the record other agents read is not advisory.
    for name in tool_names(network["context"]):
        assert "ledger" not in name.lower()


def test_it_is_unreachable_and_reaches_nothing(network):
    assert network["context"].handoffs == []
    for other in ("director", "briefing", "campaign", "reviewer"):
        targets = [
            getattr(h, "agent_name", getattr(h, "name", "?"))
            for h in network[other].handoffs
        ]
        assert "Account Context" not in targets, (
            "Phase 1 is advisory. An agent wired into the flow before its verdict "
            "binds gives the flow a way to route around it."
        )


def test_it_returns_the_typed_verdict(network):
    assert network["context"].output_type is ContextVerdict


def test_the_workers_did_not_gain_contact_reads(network):
    """Contacts are readable in exactly one agent.

    `tools/hubspot.py` kept contact records out of the system entirely while
    HubSpot belonged to the Director. Opening them for Account Context must not
    open them anywhere else.
    """
    for other in ("director", "briefing", "campaign", "reviewer"):
        for name in tool_names(network[other]):
            assert "find_contact" not in name.lower(), (
                f"{other} gained a HubSpot contact read"
            )


# --- the verdict ----------------------------------------------------------


def clean(**kw):
    base = dict(
        status="net_new",
        account="Test College",
        searched=["HubSpot companies, domain test.edu, no match"],
    )
    base.update(kw)
    return ContextVerdict(**base)


def test_a_verdict_cannot_be_built_without_a_search():
    """Wiley. A lookup that found nothing and a lookup that never ran read the
    same in a summary and mean opposite things."""
    with pytest.raises(pydantic.ValidationError):
        ContextVerdict(status="net_new", account="Wiley University", searched=[])


def test_a_status_claiming_history_needs_evidence():
    for status in ("known_inactive", "known_active"):
        with pytest.raises(pydantic.ValidationError):
            clean(status=status, evidence=[])


def test_a_status_claiming_history_is_accepted_with_evidence():
    """Owner is recorded for context; it no longer decides the status.
    A colleague-owned account with no activity of its own is still
    known_active only when there is actual activity to point to."""
    v = clean(
        status="known_active",
        evidence=["Apollo sequence 6a8899a4482aef0010cb668a, active"],
        owner="Miguel Pescador",
    )
    assert v.status == "known_active"
    assert v.owner == "Miguel Pescador"


def test_a_person_in_a_live_sequence_cannot_be_called_quiet():
    """Wayne Young Jr., enrolled in 6a8899a4482aef0010cb668a with activity the
    previous day, and given a second sequence anyway."""
    for status in ("net_new", "known_inactive"):
        with pytest.raises(pydantic.ValidationError):
            clean(
                status=status,
                evidence=["Apollo sequence 6a8899a4482aef0010cb668a, added 3 Sep 2026"],
                live_sequences=["6a8899a4482aef0010cb668a"],
            )


def test_a_person_in_a_live_sequence_is_accepted_as_active():
    v = clean(
        status="known_active",
        person="Wayne Young Jr.",
        evidence=["Apollo sequence 6a8899a4482aef0010cb668a, last activity 3 Sep 2026"],
        live_sequences=["6a8899a4482aef0010cb668a"],
    )
    assert v.live_sequences == ["6a8899a4482aef0010cb668a"]


def test_the_verdict_forbids_extra_fields():
    with pytest.raises(pydantic.ValidationError):
        clean(recommendation="go ahead and write to them")


def test_the_note_carries_the_searches_not_only_the_finding():
    note = clean(searched=["HubSpot companies, domain wiley.edu, no match"]).as_note()
    assert "searched:" in note
    assert "wiley.edu" in note
    assert "advisory" in note.lower(), (
        "Phase 1 blocks nothing and the note has to say so, or a reader takes a "
        "verdict for a decision."
    )


# --- the tools ------------------------------------------------------------


_POST_TARGET = re.compile(r'client\.post\(\s*f"\{API\}(?P<path>[^"]+)"')


def test_the_contact_read_has_no_write_path():
    """HubSpot writes with POST, so a POST is checked by where it goes.

    Reads use both verbs here: associations and a single owner are GETs, while
    search and batch-read are POSTs with a body. So the control cannot be "no
    POST", it is that every POST goes to a search or a batch read. Opening
    contacts for Account Context must not open a create alongside them.
    """
    text = Path(hubspot.__file__).read_text(encoding="utf-8")

    for verb in ("client.patch", "client.delete", "client.put"):
        assert verb not in text, f"the HubSpot module gained {verb}"

    targets = [m.group("path") for m in _POST_TARGET.finditer(text)]
    assert targets, "no POST call sites found; the regex has drifted from the code"
    for path in targets:
        assert path.endswith("/search") or path.endswith("/batch/read"), (
            f"the HubSpot module POSTs to {path}, which is not a read"
        )


def test_the_contact_read_reports_the_queries_it_ran():
    text = Path(hubspot.__file__).read_text(encoding="utf-8")
    body = text[text.index("async def hubspot_find_contact") :]
    assert "Searched:" in body, (
        "A contact search that reports only its hits cannot be told apart from "
        "one that never ran, which is how Wiley became net new."
    )


def test_the_contact_read_tries_the_surname_alone():
    assert hubspot._person_variants("Ronnie Williams") == ["Ronnie Williams", "Williams"]
    assert hubspot._person_variants("Cher") == ["Cher"]
    assert hubspot._person_variants("  ") == []


def test_the_contact_read_carries_record_age_beside_the_title():
    text = Path(hubspot.__file__).read_text(encoding="utf-8")
    assert "lastmodifieddate" in text
    body = text[text.index("async def hubspot_find_contact") :]
    assert "record modified" in body, (
        "A title with no record date is what sent two runs at a Vice President "
        "who retired in 2021."
    )


# --- the prompt -----------------------------------------------------------


@pytest.fixture(scope="module")
def prompt() -> str:
    return " ".join((PROMPTS / "context.md").read_text(encoding="utf-8").split()).casefold()


def test_the_prompt_requires_the_searches_that_found_nothing(prompt):
    assert "including the ones that returned nothing" in prompt


def test_the_prompt_forbids_research(prompt):
    assert "never research" in prompt


def test_the_prompt_forbids_softening_the_verdict(prompt):
    assert "never soften a verdict" in prompt
    assert "never state a status you did not search for" in prompt


def test_the_prompt_says_titles_age(prompt):
    assert "a title in hubspot is as old as the record" in prompt


# --- recording ------------------------------------------------------------


def test_the_verdict_is_recorded_as_its_own_kind(tmp_path, monkeypatch):
    """A verdict is not a ledger row and does not become one.

    A LedgerRecord carries a segment, a motion and a verified_through date,
    all of which would have to be invented for a check that researched nothing.
    Same envelope, same hash, different kind.
    """
    from seats_prospecting.tools import notion_ledger

    monkeypatch.setenv("LEDGER_OUTBOX_DIR", str(tmp_path))
    verdict = clean(
        status="known_active",
        account="Wiley University",
        evidence=["Apollo sequence 6a8899a4482aef0010cb668a, active"],
        owner="Miguel Pescador",
    )
    path = notion_ledger.write_context_verdict(verdict)

    import json

    envelope = json.loads(path.read_text(encoding="utf-8"))
    assert envelope["kind"] == "account_context"
    assert envelope["record"]["status"] == "known_active"
    assert notion_ledger.verify_envelope(envelope), "the hash must cover the verdict"
    assert envelope["relay"]["relayed"] is False


def test_recording_never_overwrites(tmp_path, monkeypatch):
    from seats_prospecting.tools import notion_ledger

    monkeypatch.setenv("LEDGER_OUTBOX_DIR", str(tmp_path))
    v = clean()
    first = notion_ledger.write_context_verdict(v)
    second = notion_ledger.write_context_verdict(v)
    assert first != second
    assert first.exists() and second.exists()


def test_a_tampered_verdict_fails_verification(tmp_path, monkeypatch):
    from seats_prospecting.tools import notion_ledger

    monkeypatch.setenv("LEDGER_OUTBOX_DIR", str(tmp_path))
    path = notion_ledger.write_context_verdict(clean(status="net_new"))

    import json

    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["record"]["status"] = "known_active"
    assert not notion_ledger.verify_envelope(envelope)


def test_the_launcher_offers_context_and_refuses_a_session():
    """A session would carry the last account's record into the next verdict."""
    source = Path("scripts/live_run.py").read_text(encoding="utf-8")
    assert '"context", "director"' in source
    assert 'args.agent in ("reviewer", "context") and args.session' in source


def test_the_launcher_records_the_verdict_itself():
    """Account Context holds no ledger write, so the caller does it.

    An agent that writes its own conclusion into the record other agents read
    is not advisory, whatever the prompt says.
    """
    source = Path("scripts/live_run.py").read_text(encoding="utf-8")
    assert "write_context_verdict" in source
    assert "--no-record" in source


# --- Apollo identity ------------------------------------------------------


def test_the_sequence_read_runs_as_kib(monkeypatch):
    """One credential since 4 September. The key is gone, path and all."""
    from seats_prospecting.tools import apollo

    monkeypatch.setattr(
        "seats_prospecting.apollo_oauth.current_token",
        lambda *a, **k: type("T", (), {"access_token": "tok"})(),
    )
    headers, identity, caveat = apollo._read_identity()
    assert headers is not None and headers["Authorization"] == "Bearer tok"
    assert "x-api-key" not in headers
    assert "oauth" in identity.lower()
    assert caveat, "the coverage note travels with every result"


def test_setting_the_old_key_changes_nothing(monkeypatch):
    """A stray APOLLO_API_KEY in someone's environment must not resurrect it."""
    from seats_prospecting.tools import apollo

    monkeypatch.setenv("APOLLO_API_KEY", "abc123")
    monkeypatch.setattr(
        "seats_prospecting.apollo_oauth.current_token",
        lambda *a, **k: type("T", (), {"access_token": "tok"})(),
    )
    headers, identity, _ = apollo._read_identity()
    assert headers["Authorization"] == "Bearer tok"
    assert "x-api-key" not in headers


def test_no_identity_at_all_is_reported_as_unchecked(monkeypatch):
    from seats_prospecting.tools import apollo

    monkeypatch.setattr(
        "seats_prospecting.apollo_oauth.current_token",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no token")),
    )
    headers, identity, caveat = apollo._read_identity()
    assert headers is None


def test_the_coverage_note_hedges_absence_not_presence():
    """The first live run disproved the strong version of this caveat.

    Kib's own token returned Wayne Young Jr. in a sequence sending from
    adelarosa@seatsone.com, so contact-level membership does cross colleagues.
    One observation is not full coverage, so absence stays hedged and presence
    does not.
    """
    from seats_prospecting.tools import apollo

    note = apollo._NARROW_VIEW.lower()
    assert "absence is the weaker half" in note
    assert "presence is trustworthy" in note
    assert "unconfirmed rather than clean" in note


def test_the_domain_filter_is_never_the_last_word():
    """A narrowed search that finds nothing looks like a person Apollo never held.

    Wayne Young Jr. was looked up filtered to the institution the caller
    believed he worked at. Apollo held him under another employer, in an active
    sequence, sending from a colleague's mailbox, and the filtered search
    returned nothing at all.
    """
    from seats_prospecting.tools import apollo

    source = Path(apollo.__file__).read_text(encoding="utf-8")
    body = source[source.index("async def apollo_contact_status") :]
    assert "[domain, None] if domain else [None]" in body, (
        "the domain filter must fall back to a name-only search"
    )
    assert "(name only)" in body, "the fallback query has to appear in the log"


def test_a_miss_still_reports_the_coverage_caveat():
    """The no-match branch is where the caveat matters most.

    A narrow identity that finds nothing is the reading most likely to be
    mistaken for a clean account.
    """
    from seats_prospecting.tools import apollo

    source = Path(apollo.__file__).read_text(encoding="utf-8")
    body = source[source.index("async def apollo_contact_status") :]
    miss = body[body.index("Apollo holds no contact record") : body.index("lines = [f\"Apollo contacts matching")]
    assert "caveat" in miss, "the miss branch dropped the coverage caveat"


def test_the_sending_mailbox_is_surfaced():
    """The mailbox is reported, but does not change known_active either way."""
    from seats_prospecting.tools import apollo

    source = Path(apollo.__file__).read_text(encoding="utf-8")
    body = source[source.index("async def apollo_contact_status") :]
    assert "send_email_from_email_address" in body
    assert "does not change the status" in body
