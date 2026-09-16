"""Apollo is read only, keeps contact details out of agent context, and caps spend."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from seats_prospecting.agents_def import build_agents
from seats_prospecting.context import DispatchContext
from seats_prospecting.tools import apollo

WRITE_SHAPED = ("create", "update", "delete", "send", "enroll", "activate", "approve", "sequence")


@pytest.fixture(scope="module")
def network():
    return build_agents()


def tool_names(agent) -> list[str]:
    return [getattr(t, "name", type(t).__name__) for t in agent.tools]


def test_module_exposes_no_write_or_sequence_function():
    for name in dir(apollo):
        if name.startswith("_"):
            continue
        assert not any(name.lower().startswith(w) for w in WRITE_SHAPED), (
            f"apollo read module gained a write-shaped function: {name}"
        )


# Every Apollo path this module is allowed to call. An addition here is a
# deliberate widening of the read surface and should be argued for in review.
READ_ENDPOINTS = (
    "mixed_companies/search",
    "mixed_people/api_search",
    "people/match",
    "contacts/search",
)

_CALL = re.compile(r'f"\{API\}/(?P<path>[^"]+)"')


def test_only_read_endpoints_are_called():
    """Check the call sites, not the prose.

    This used to ban the substrings "sequences" and "emailer_campaigns"
    anywhere in the file, which caught the endpoints and also caught any
    sentence mentioning them. On 4 September a read tool that reports which
    sequences a contact is already enrolled in could not be documented in its
    own module. The control is that no write endpoint is called; an allowlist
    of the paths actually constructed says that directly and says it more
    tightly, because a read endpoint nobody approved now fails too.
    """
    text = Path(apollo.__file__).read_text(encoding="utf-8")

    called = {m.group("path") for m in _CALL.finditer(text)}
    unexpected = called - set(READ_ENDPOINTS)
    assert not unexpected, f"apollo read module calls unapproved endpoints: {sorted(unexpected)}"

    for verb in ("client.patch", "client.delete", "client.put"):
        assert verb not in text, f"apollo read module gained {verb}"

    # The write endpoints, checked as endpoints. These are the paths that
    # create, modify or fire a sequence, and none of them may be constructed.
    for path in ("emailer_campaigns", "sequences", "send_now", "add_contact_ids"):
        assert not any(path in c for c in called), (
            f"apollo read module calls a write endpoint containing {path!r}"
        )


def test_contact_details_are_never_requested():
    text = open(apollo.__file__, encoding="utf-8").read()
    assert '"reveal_personal_emails": False' in text
    assert '"reveal_phone_number": False' in text


def test_the_director_holds_no_apollo_tool(network):
    for name in tool_names(network["director"]):
        assert "apollo" not in name.lower(), (
            "a planner that can size a list starts building one"
        )


@pytest.mark.parametrize("worker", ["briefing", "campaign"])
def test_both_workers_hold_the_read_tools(network, worker):
    names = tool_names(network[worker])
    assert "apollo_find_org" in names
    assert "apollo_find_roles" in names
    assert "apollo_reveal_person" in names


def test_the_reviewer_holds_no_apollo_tool(network):
    for name in tool_names(network["reviewer"]):
        assert "apollo" not in name.lower()


def test_no_agent_holds_the_sequence_write_by_default(network):
    for agent in network.values():
        for name in tool_names(agent):
            assert "create_sequence" not in name


def test_reveal_cap_defaults_and_is_overridable(monkeypatch):
    monkeypatch.delenv("APOLLO_REVEAL_CAP", raising=False)
    assert apollo._reveal_cap() == apollo.DEFAULT_REVEAL_CAP
    monkeypatch.setenv("APOLLO_REVEAL_CAP", "3")
    assert apollo._reveal_cap() == 3
    monkeypatch.setenv("APOLLO_REVEAL_CAP", "not a number")
    assert apollo._reveal_cap() == apollo.DEFAULT_REVEAL_CAP


def test_cap_is_described_as_a_guard_not_a_gate(monkeypatch):
    monkeypatch.setenv("APOLLO_REVEAL_CAP", "0")
    ctx = DispatchContext()
    # At the cap from the start, the tool must refuse without spending.
    assert ctx.apollo_reveals == 0
    assert apollo._reveal_cap() == 0


def test_missing_token_degrades_instead_of_failing(monkeypatch):
    """The API key was removed on 4 September 2026, key and code path together.

    It had been blank in the environment for a day, which meant every read in
    this module returned "Apollo is unavailable" while looking wired. Now there
    is one credential, so an absent one is a single obvious failure rather than
    a silent degrade behind a fallback.
    """
    monkeypatch.setattr(
        "seats_prospecting.apollo_oauth.current_token",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no token")),
    )
    assert apollo._auth() is None
    assert "unavailable on this run" in apollo.NOT_CONFIGURED
    assert "apollo_oauth login" in apollo.NOT_CONFIGURED


def test_the_api_key_path_is_gone_from_the_module():
    text = Path(apollo.__file__).read_text(encoding="utf-8")
    assert "x-api-key" not in text, (
        "an api-key request runs as the workspace's longest-standing admin; "
        "reintroducing it here brings back a second identity that appears under load"
    )
    assert "APOLLO_API_KEY" not in text


def test_auth_failure_tells_the_agent_not_to_retry_elsewhere():
    msg = apollo._explain(403, "forbidden")
    assert "Do not retry a different endpoint" in msg


def test_the_context_tracks_spend():
    ctx = DispatchContext()
    assert ctx.apollo_reveals == 0
    ctx.apollo_reveals += 1
    assert ctx.apollo_reveals == 1


def test_the_write_tool_still_exists_and_still_writes_drafts_only():
    """Regression: the read tools were once written over the write tool.

    The suite caught it because these assertions exist. Keep them. The approval
    flag came off on 3 September; the draft-only guarantee did not.
    """
    from seats_prospecting.tools.apollo_write import create_manual_email_drafts

    assert create_manual_email_drafts.name == "create_manual_email_drafts"
    assert create_manual_email_drafts.needs_approval is False


def test_the_read_module_and_the_write_module_are_separate():
    from seats_prospecting.tools import apollo, apollo_write

    assert apollo.__file__ != apollo_write.__file__
    assert not hasattr(apollo, "create_manual_email_drafts"), (
        "the write tool must not live in the read module"
    )


def test_the_write_module_owns_the_inactive_flag():
    from seats_prospecting.tools import apollo_write

    text = open(apollo_write.__file__, encoding="utf-8").read()
    assert '"active": False' in text
    from seats_prospecting.schemas import SequenceProposal

    assert "active" not in SequenceProposal.model_fields


# --- the cap holds under parallel calls -----------------------------------


class _FakeResponse:
    """Enough of httpx.Response for the reveal path."""

    status_code = 200
    text = ""

    def __init__(self, person: dict | None):
        self._person = person

    def json(self):
        return {"person": self._person}


class _Ctx:
    """Stands in for RunContextWrapper. Only .context is used."""

    def __init__(self, context):
        self.context = context


PERSON = {"name": "Tony Sitz", "title": "University Registrar", "organization": {"name": "UCA"}}


async def test_six_reveals_in_one_turn_cannot_all_spend(monkeypatch, apollo_token):
    """The live failure, reproduced.

    A run with APOLLO_REVEAL_CAP=1 spent two credits and logged "reveal 2/1".
    The model had emitted six reveal calls in a single turn; they ran
    concurrently, and every one of them read the count before any of them
    incremented it. The guard now reserves its slot before awaiting.
    """
    import asyncio

    monkeypatch.setenv("APOLLO_API_KEY", "test-key")
    monkeypatch.setenv("APOLLO_REVEAL_CAP", "1")

    calls = 0

    async def fake_call(key, person_id):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)  # force a real interleave point
        return _FakeResponse(PERSON)

    monkeypatch.setattr(apollo, "_reveal_call", fake_call)

    ctx = _Ctx(DispatchContext())
    results = await asyncio.gather(
        *(apollo._do_reveal(ctx, f"id-{n}") for n in range(6))
    )

    assert calls == 1, f"{calls} credits spent against a cap of 1"
    assert ctx.context.apollo_reveals == 1
    assert sum("cap of 1 reached" in r for r in results) == 5


async def test_a_no_match_gives_the_slot_back(monkeypatch, apollo_token):
    """Apollo charges nothing for a miss, so the run should not lose a slot."""
    monkeypatch.setenv("APOLLO_API_KEY", "test-key")
    monkeypatch.setenv("APOLLO_REVEAL_CAP", "2")

    async def no_match(key, person_id):
        return _FakeResponse(None)

    monkeypatch.setattr(apollo, "_reveal_call", no_match)

    ctx = _Ctx(DispatchContext())
    out = await apollo._do_reveal(ctx, "id-1")
    assert "no person" in out
    assert ctx.context.apollo_reveals == 0


async def test_an_error_gives_the_slot_back(monkeypatch, apollo_token):
    monkeypatch.setenv("APOLLO_API_KEY", "test-key")
    monkeypatch.setenv("APOLLO_REVEAL_CAP", "2")

    class _Err(_FakeResponse):
        status_code = 429
        text = "rate limited"

    async def rate_limited(key, person_id):
        return _Err(None)

    monkeypatch.setattr(apollo, "_reveal_call", rate_limited)

    ctx = _Ctx(DispatchContext())
    out = await apollo._do_reveal(ctx, "id-1")
    assert "rate limited" in out.lower()
    assert ctx.context.apollo_reveals == 0


async def test_the_audit_line_matches_the_slot_that_was_reserved(monkeypatch, apollo_token):
    monkeypatch.setenv("APOLLO_API_KEY", "test-key")
    monkeypatch.setenv("APOLLO_REVEAL_CAP", "3")

    async def ok(key, person_id):
        return _FakeResponse(PERSON)

    monkeypatch.setattr(apollo, "_reveal_call", ok)

    ctx = _Ctx(DispatchContext())
    await apollo._do_reveal(ctx, "id-1")
    await apollo._do_reveal(ctx, "id-2")
    assert "apollo reveal 1/3" in ctx.context.audit[0]
    assert "apollo reveal 2/3" in ctx.context.audit[1]


# --- whose Apollo account is this ------------------------------------------


def test_the_key_identity_verdict_names_the_mismatch():
    """3 September 2026: the first live write was created as Miguel Pescador.

    The key in .env was his, so the sequence was owned by him, its visibility was
    restricted, and Kib could not find it in his own Apollo. The reveal credits
    had been coming off that seat all along. Nothing in the system said whose key
    it held, so preflight now asks.
    """
    from seats_prospecting.preflight import identity_verdict, whose_key

    name, email = whose_key({"user": {"name": "Miguel Pescador", "email": "mpescador@seatsone.com"}})
    assert (name, email) == ("Miguel Pescador", "mpescador@seatsone.com")

    wrong = identity_verdict(email, "kcochran@seatsone.com")
    assert "WRONG" in wrong
    assert "mpescador@seatsone.com" in wrong
    assert "you will not be able to see it" in wrong

    right = identity_verdict("kcochran@seatsone.com", "kcochran@seatsone.com")
    assert right.strip().startswith("OK")

    unset = identity_verdict("anyone@example.com", None)
    assert "APOLLO_EXPECTED_USER" in unset


def test_the_identity_check_is_case_and_space_insensitive():
    from seats_prospecting.preflight import identity_verdict

    assert "OK" in identity_verdict(" Kcochran@SeatsOne.com ", "kcochran@seatsone.com")


def test_ownership_identifies_a_key_that_cannot_read_its_own_profile():
    """The second identity check, added when the first key swap did not take.

    A key scoped without ``users/api_profile`` returns 403 there, so the profile
    endpoint cannot answer whose key it is. Apollo does mark
    ``sharing_permission.is_owner`` relative to the caller, so the sequences a
    key claims to own name the user behind it. On the evening of 3 September a
    replacement key still came back owning only Miguel Pescador's sequences,
    which is how we knew the swap had not changed the identity.
    """
    from seats_prospecting.preflight import email_for_user_id, owner_ids_from_sequences

    MIGUEL = "69d5220afb60c4001d2603e1"
    KIB = "69d808314b6a820015875600"

    page = {
        "emailer_campaigns": [
            {
                "id": "6a99df0f30072e001cd336c7",
                "user_id": MIGUEL,
                "sharing_permission": {"owner_id": MIGUEL, "is_owner": True},
            },
            {
                "id": "other",
                "user_id": KIB,
                "sharing_permission": {"owner_id": KIB, "is_owner": False},
            },
        ]
    }
    assert owner_ids_from_sequences(page) == {MIGUEL}

    accounts = {
        "email_accounts": [
            {"email": "kcochran@seatsone.com", "user_id": KIB},
            {"email": "mpescador@seatsone.com", "user_id": MIGUEL},
        ]
    }
    assert email_for_user_id(accounts, MIGUEL) == "mpescador@seatsone.com"
    assert email_for_user_id(accounts, "nobody") is None


def test_a_key_that_owns_nothing_yet_is_not_reported_as_anyone():
    from seats_prospecting.preflight import owner_ids_from_sequences

    assert owner_ids_from_sequences({"emailer_campaigns": []}) == set()
    assert owner_ids_from_sequences({}) == set()
