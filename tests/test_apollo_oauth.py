"""OAuth exists for one reason: a write that is Kib's rather than the admin's.

Apollo runs every api-key request as the workspace's longest-standing active
admin, which in this workspace is a colleague. That is documented behaviour, not
a misconfiguration, and no key swap changes it. An access token acts as the user
who authorized it, so these tests hold the parts that make that reliable: the
pair is stored before it is used, a rotation that fails does not lose the old
one silently, and the write tool says whose identity it actually used.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from seats_prospecting import apollo_oauth
from seats_prospecting.apollo_oauth import (
    OAuthFailed,
    OAuthNotConfigured,
    Token,
    authorize_url,
    bearer_headers,
    current_token,
    exchange_code,
    load_token,
    refresh,
    save_token,
)

RESPONSE = {
    "access_token": "at-1",
    "refresh_token": "rt-1",
    "expires_in": 2592000,  # 30 days, as Apollo documents
    "scope": "emailer_campaigns_create read_user_profile",
    "token_type": "Bearer",
}


@pytest.fixture
def creds(monkeypatch, tmp_path):
    monkeypatch.setenv("APOLLO_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("APOLLO_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("APOLLO_TOKEN_FILE", str(tmp_path / "token.json"))
    return tmp_path / "token.json"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://app.apollo.io")


def test_the_authorize_url_carries_the_scope_that_creates_sequences():
    url = authorize_url("cid", "http://localhost:8765/callback", "emailer_campaigns_create", "st")
    assert "client_id=cid" in url
    assert "response_type=code" in url
    assert "emailer_campaigns_create" in url
    assert "state=st" in url
    assert url.startswith("https://app.apollo.io/#/oauth/authorize?")


def test_no_client_credentials_is_not_an_error_the_run_dies_on(monkeypatch, tmp_path):
    monkeypatch.delenv("APOLLO_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("APOLLO_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("APOLLO_TOKEN_FILE", str(tmp_path / "none.json"))
    with pytest.raises(OAuthNotConfigured):
        current_token()


def test_a_stored_token_round_trips(creds):
    token = Token.from_response(RESPONSE, now=1000.0)
    save_token(token)
    again = load_token()
    assert again.access_token == "at-1"
    assert again.refresh_token == "rt-1"
    assert again.expires_at == 1000.0 + 2592000


def test_the_new_pair_is_on_disk_before_the_caller_can_use_it(creds):
    """Apollo revokes the old pair the moment a refresh succeeds. A token used
    before it is stored is a token a crash can lose."""
    save_token(Token("at-1", "rt-1", expires_at=time.time() + 60))

    seen_on_disk = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["grant_type"] == "refresh_token"
        assert body["refresh_token"] == "rt-1"
        return httpx.Response(200, json={**RESPONSE, "access_token": "at-2", "refresh_token": "rt-2"})

    with _client(handler) as client:
        fresh = refresh(load_token(), client=client)
        seen_on_disk.update(json.loads(creds.read_text(encoding="utf-8")))

    assert fresh.access_token == "at-2"
    assert seen_on_disk["access_token"] == "at-2", "the caller got a token the disk did not have"
    assert seen_on_disk["refresh_token"] == "rt-2"


def test_a_refused_refresh_says_to_log_in_again_and_keeps_the_old_file(creds):
    save_token(Token("at-1", "rt-1", expires_at=time.time() + 60))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_grant"})

    with _client(handler) as client:
        with pytest.raises(OAuthFailed) as excinfo:
            refresh(load_token(), client=client)

    assert "log" in str(excinfo.value).lower()
    assert load_token().access_token == "at-1", "a failed refresh must not empty the file"


def test_a_token_inside_its_last_day_is_refreshed_before_use(creds, monkeypatch):
    save_token(Token("at-1", "rt-1", expires_at=time.time() + 3600))  # an hour left
    calls = []

    def fake_refresh(token, client=None):
        calls.append(token.access_token)
        return Token("at-2", "rt-2", expires_at=time.time() + 2592000)

    monkeypatch.setattr(apollo_oauth, "refresh", fake_refresh)
    assert current_token().access_token == "at-2"
    assert calls == ["at-1"]


def test_a_fresh_token_is_used_as_is(creds, monkeypatch):
    save_token(Token("at-1", "rt-1", expires_at=time.time() + 20 * 86400))
    monkeypatch.setattr(
        apollo_oauth, "refresh", lambda *a, **k: pytest.fail("refreshed a token with 20 days left")
    )
    assert current_token().access_token == "at-1"


def test_the_exchange_sends_what_apollo_asks_for(creds):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["grant_type"] == "authorization_code"
        assert body["code"] == "the-code"
        assert body["client_id"] == "cid" and body["client_secret"] == "secret"
        return httpx.Response(200, json=RESPONSE)

    with _client(handler) as client:
        token = exchange_code("the-code", "http://localhost:8765/callback", client=client)
    assert token.access_token == "at-1"
    assert "emailer_campaigns_create" in token.scope


def test_the_bearer_header_is_what_the_write_sends():
    headers = bearer_headers(Token("at-9", "rt", expires_at=0))
    assert headers["Authorization"] == "Bearer at-9"
    assert "x-api-key" not in headers


def test_the_write_tool_has_no_fallback_identity():
    """Kib's call, 4 September 2026: there is no second way to write.

    The previous version fell back to the API key and said loudly whose account
    the sequence would land in. That was the honest version of the wrong
    behaviour: five sequences were created under Miguel Pescador's name and
    could not be found by the person who asked for them. Disclosure makes a
    misdirected write legible, it does not make it right, and failing here costs
    a re-login."""
    from pathlib import Path

    source = Path("src/seats_prospecting/tools/apollo_write.py").read_text(encoding="utf-8")
    assert "current_token()" in source
    assert "bearer_headers" in source
    assert "acting_as" in source
    assert "x-api-key" not in source, "the api-key write path came back"
    assert "APOLLO_API_KEY" not in source
    assert "no API-key" in source, "the refusal has to say why there is no fallback"
