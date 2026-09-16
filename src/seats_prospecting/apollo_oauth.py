r"""Apollo OAuth 2.0, so a write is Kib's rather than the workspace admin's.

The problem this solves, found on 3 September 2026. Apollo's own documentation:

    "A key identifies your workspace, not a person, so Apollo can't tell which
    teammate used it."

    "Every API-key request acts as your workspace's longest-standing active
    admin - the earliest-created user who hasn't been deleted and has admin
    access."

In this workspace that admin is a colleague, so the first live sequence this
system wrote was created as his, with restricted visibility, and Kib could not
see it in his own Apollo. Two key swaps changed nothing, because no key can:
ownership is not settable at creation and the key is not a person.

An OAuth access token is. It acts as the user who authorized it, which makes
``emailer_campaigns_create`` write a sequence Kib owns and can open.

What this module is, and is not. It is the smallest thing that gets a token and
keeps it fresh: an authorize URL, a one-shot loopback listener for the callback,
the code exchange, a token file, and refresh. It is not a general OAuth client,
it holds no scopes beyond what the build asks for, and it never widens them on
its own.

Setup, once:

1. In Apollo, Settings > Integrations > API Keys > OAuth registration.
   Redirect URI: http://localhost:8765/callback
   Scopes: the seven in DEFAULT_SCOPES below. A token carries the scopes the
   authorize request asked for, not the ones the app registration allows, so
   widening the registration alone changes nothing until you log in again.
2. Put the client id and secret in .env as APOLLO_OAUTH_CLIENT_ID and
   APOLLO_OAUTH_CLIENT_SECRET.
3. Run, with the venv python:
   .venv\Scripts\python.exe -m seats_prospecting.apollo_oauth login

After that the write tool uses the token and refreshes it on its own.

Refresh rotates. Apollo revokes the old pair the moment a refresh succeeds, so
the new pair is written to disk before it is returned, and a crash between the
two costs a re-login rather than a silently dead token.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

AUTHORIZE_URL = "https://app.apollo.io/#/oauth/authorize"
TOKEN_URL = "https://app.apollo.io/api/v1/oauth/token"

DEFAULT_REDIRECT = "http://localhost:8765/callback"
DEFAULT_SCOPES = (
    "read_user_profile emailer_campaigns_create emailer_campaigns_update emailer_campaigns_add_contact_ids emailer_campaigns_search contacts_search email_accounts_list"
)

# Refresh this long before the token actually expires, so a run that starts just
# inside the window does not die halfway through.
REFRESH_MARGIN_SECONDS = 24 * 60 * 60


class OAuthNotConfigured(Exception):
    """No client credentials, or no token yet. The caller falls back to the key."""


class OAuthFailed(Exception):
    """Apollo refused the exchange or the refresh."""


def token_path() -> Path:
    return Path(os.environ.get("APOLLO_TOKEN_FILE", "./.apollo_token.json")).resolve()


def _client() -> tuple[str, str]:
    cid = (os.environ.get("APOLLO_OAUTH_CLIENT_ID") or "").strip()
    secret = (os.environ.get("APOLLO_OAUTH_CLIENT_SECRET") or "").strip()
    if not cid or not secret:
        raise OAuthNotConfigured(
            "APOLLO_OAUTH_CLIENT_ID and APOLLO_OAUTH_CLIENT_SECRET are not set. "
            "Register the app in Apollo under Settings > Integrations > API Keys."
        )
    return cid, secret


@dataclass
class Token:
    access_token: str
    refresh_token: str
    expires_at: float
    scope: str = ""

    @classmethod
    def from_response(cls, body: dict, now: float | None = None) -> "Token":
        now = time.time() if now is None else now
        return cls(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token", ""),
            expires_at=now + float(body.get("expires_in", 0)),
            scope=body.get("scope", ""),
        )

    def is_stale(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return now >= self.expires_at - REFRESH_MARGIN_SECONDS

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "scope": self.scope,
        }


def save_token(token: Token, path: Path | None = None) -> Path:
    """Write the pair to disk before anyone uses it.

    Order matters. A refresh revokes the previous pair server-side, so a token
    that is used before it is stored is a token that can be lost by a crash.
    """
    path = path or token_path()
    path.write_text(json.dumps(token.to_dict(), indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # Windows, where the file inherits the folder's permissions
    return path


def load_token(path: Path | None = None) -> Token:
    path = path or token_path()
    if not path.exists():
        raise OAuthNotConfigured(
            f"No Apollo token at {path}. Run: seats-apollo-login login "
            "(or .venv\\Scripts\\python.exe -m seats_prospecting.apollo_oauth login)"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return Token(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", ""),
        expires_at=float(data.get("expires_at", 0)),
        scope=data.get("scope", ""),
    )


def authorize_url(
    client_id: str, redirect_uri: str, scopes: str, state: str
) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scopes,
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def exchange_code(code: str, redirect_uri: str, *, client: httpx.Client | None = None) -> Token:
    cid, secret = _client()
    owned = client is None
    client = client or httpx.Client(timeout=30)
    try:
        resp = client.post(
            TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": cid,
                "client_secret": secret,
                "redirect_uri": redirect_uri,
            },
        )
    finally:
        if owned:
            client.close()
    if resp.status_code >= 400:
        raise OAuthFailed(f"Apollo refused the code exchange ({resp.status_code}): {resp.text[:300]}")
    return Token.from_response(resp.json())


def refresh(token: Token, *, client: httpx.Client | None = None) -> Token:
    """Swap the pair. The old one is dead the moment this returns."""
    cid, secret = _client()
    if not token.refresh_token:
        raise OAuthFailed("This token has no refresh token. Log in again.")
    owned = client is None
    client = client or httpx.Client(timeout=30)
    try:
        resp = client.post(
            TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "refresh_token": token.refresh_token,
                "client_id": cid,
                "client_secret": secret,
            },
        )
    finally:
        if owned:
            client.close()
    if resp.status_code >= 400:
        raise OAuthFailed(
            f"Apollo refused the refresh ({resp.status_code}): {resp.text[:300]}\\n"
            "Run: seats-apollo-login login"
        )
    fresh = Token.from_response(resp.json())
    save_token(fresh)
    return fresh


def current_token(path: Path | None = None) -> Token:
    """The token to send, refreshed if it is near the end of its 30 days."""
    token = load_token(path)
    return refresh(token) if token.is_stale() else token


def bearer_headers(token: Token) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token.access_token}",
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
    }


class _CallbackHandler(BaseHTTPRequestHandler):
    code: str | None = None
    state: str | None = None

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        _CallbackHandler.code = (params.get("code") or [None])[0]
        _CallbackHandler.state = (params.get("state") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        message = "Apollo authorized. Close this tab and go back to the terminal."
        self.wfile.write(message.encode("utf-8"))

    def log_message(self, *args) -> None:  # keep the console clean
        return


def login(redirect_uri: str | None = None, scopes: str | None = None) -> Token:
    """Open Apollo, catch the callback on localhost, store the token."""
    cid, _ = _client()
    redirect_uri = redirect_uri or os.environ.get("APOLLO_OAUTH_REDIRECT", DEFAULT_REDIRECT)
    scopes = scopes or os.environ.get("APOLLO_OAUTH_SCOPES", DEFAULT_SCOPES)
    state = secrets.token_urlsafe(16)

    parsed = urllib.parse.urlparse(redirect_uri)
    server = HTTPServer((parsed.hostname or "localhost", parsed.port or 80), _CallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    url = authorize_url(cid, redirect_uri, scopes, state)
    print("Authorize this build in Apollo:")
    print(f"  {url}")
    webbrowser.open(url)
    print("Waiting for the callback...")
    thread.join(timeout=300)
    server.server_close()

    if not _CallbackHandler.code:
        raise OAuthFailed("No authorization code arrived. Nothing was stored.")
    if _CallbackHandler.state != state:
        # Not a real attack surface on a loopback listener, but a mismatch means
        # the response did not come from the request that was made.
        raise OAuthFailed("State mismatch on the callback. Nothing was stored.")

    token = exchange_code(_CallbackHandler.code, redirect_uri)
    path = save_token(token)
    print(f"Stored the token at {path}. Scope: {token.scope or 'as registered'}")
    return token


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    command = argv[0] if argv else "status"

    try:
        if command == "login":
            login()
            return 0
        if command == "refresh":
            token = refresh(load_token())
            print(f"Refreshed. Expires {time.strftime('%Y-%m-%d', time.localtime(token.expires_at))}")
            return 0
        if command == "status":
            token = load_token()
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(token.expires_at))
            print(f"Token expires {when}. Stale: {token.is_stale()}. Scope: {token.scope or 'unknown'}")
            return 0
    except (OAuthNotConfigured, OAuthFailed) as exc:
        print(str(exc), file=sys.stderr)
        return 3

    print("Usage: python -m seats_prospecting.apollo_oauth [login|refresh|status]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
