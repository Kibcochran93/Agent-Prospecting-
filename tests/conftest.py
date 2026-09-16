"""Guards that keep the suite off live accounts.

These exist because of a real incident. On 3 September the write path learned to
post to Apollo. On 4 September the API-key fallback was removed and OAuth became
the only identity, but tests/test_flow.py was still neutering the write by
setting APOLLO_API_KEY="" and that variable no longer controlled anything. The
test kept passing, its docstring kept claiming "no network", and every run of the
suite created a real paused sequence named "IN clock-hour Q4" in the live Apollo
workspace. Ten of them before anyone noticed.

The lesson is that a guard living inside the test it protects is not a guard.
These are autouse and suite-wide, so a new test cannot forget them and a
refactor of the production code cannot quietly disarm them.

no_live_network patches httpx's real transports. It is the layer that actually
stops a write, and it holds even when a test reloads project modules.
httpx.MockTransport is a separate class and is untouched, so tests that stub
responses on purpose keep working.

no_live_apollo_token points APOLLO_TOKEN_FILE at a path that does not exist, so
token lookup raises OAuthNotConfigured before a request is ever built. Its job is
to land the failure on the production code's own "nothing was created" branch
with a readable message rather than as a raw connection error. A test that needs
a token writes its own, the way tests/test_apollo_oauth.py does.

Mark a test @pytest.mark.allow_network if it genuinely must reach out. Nothing in
the suite does today.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

# Deliberately absent. Named so that an accidental commit of it is obvious.
NO_TOKEN = Path(__file__).parent / "_no_apollo_token_during_tests.json"


class LiveNetworkAttempt(RuntimeError):
    """A test tried to open a real connection."""


def _refuse(request: httpx.Request) -> LiveNetworkAttempt:
    return LiveNetworkAttempt(
        f"A test tried to reach {request.method} {request.url}. The suite may not "
        "touch live services. Use httpx.MockTransport, or mark the test "
        "@pytest.mark.allow_network if it truly needs the network."
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "allow_network: permit real connections in this test (nothing needs it today)",
    )


@pytest.fixture(autouse=True)
def no_live_network(request, monkeypatch):
    if request.node.get_closest_marker("allow_network"):
        return

    def handle_request(self, req):
        raise _refuse(req)

    async def handle_async_request(self, req):
        raise _refuse(req)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", handle_async_request
    )


@pytest.fixture(autouse=True)
def no_live_apollo_token(monkeypatch):
    assert not NO_TOKEN.exists(), (
        f"{NO_TOKEN} is a sentinel path and must never exist. Delete it."
    )
    monkeypatch.setenv("APOLLO_TOKEN_FILE", str(NO_TOKEN))


@pytest.fixture
def apollo_token(monkeypatch, tmp_path):
    """A well-formed, non-stale token on disk, for tests that must get past
    the identity check.

    It is fake and the network is blocked, so it can authorize nothing. Ask for
    it explicitly. Four reveal tests in test_apollo.py used to clear this bar by
    reading the operator's real token off disk, which is how a unit test ends up
    depending on somebody being logged in.
    """
    path = tmp_path / "token.json"
    path.write_text(
        json.dumps(
            {
                "access_token": "test-access-token",
                "refresh_token": "test-refresh-token",
                "expires_at": time.time() + 30 * 24 * 3600,
                "scope": "read_user_profile emailer_campaigns_create",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("APOLLO_TOKEN_FILE", str(path))
    return path
