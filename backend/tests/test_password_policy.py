"""Rules for new passwords: length, context words, tiny alphabets and the breach corpus."""

import hashlib
from typing import Any

import httpx
import pytest

from tests.conftest import register
from tradepost.config import get_settings
from tradepost.security.password_policy import is_breached

BREACHED = "a password everybody uses"


def corpus_transport(seen: list[httpx.Request]) -> httpx.MockTransport:
    """The range API: suffixes for the requested prefix, padded with zero-count entries."""
    digest = hashlib.sha1(BREACHED.encode(), usedforsecurity=False).hexdigest().upper()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        lines = ["0000000000000000000000000000000000A:0"]
        if request.url.path.endswith(digest[:5]):
            lines.append(f"{digest[5:]}:3861493")
        return httpx.Response(200, text="\r\n".join(lines))

    return httpx.MockTransport(handler)


async def test_short_passwords_are_refused(client: Any) -> None:
    response = await client.post(
        "/api/auth/register",
        json={"email": "s@example.com", "password": "fourteen chars", "display_name": "Sam"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["fields"][0]["loc"] == ["body", "password"]


@pytest.mark.parametrize(
    "password",
    [
        "maria.souza-and-more-words",  # the e-mail's local part
        "my tradepost password!",  # the site's name
        "abababababababab",  # two characters repeated
    ],
)
async def test_passwords_built_from_context_are_refused(client: Any, password: str) -> None:
    response = await client.post(
        "/api/auth/register",
        json={"email": "maria.souza@example.com", "password": password, "display_name": "Maria"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "weak_password"


async def test_breached_passwords_are_refused_on_sign_up_and_change(
    client: Any, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[httpx.Request] = []
    monkeypatch.setattr(get_settings(), "breached_password_check", True)
    app.state.http = httpx.AsyncClient(transport=corpus_transport(seen))

    refused = await client.post(
        "/api/auth/register",
        json={"email": "b@example.com", "password": BREACHED, "display_name": "Bea"},
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "breached_password"
    # Only the first five characters of the hash leave the server, with padding asked for.
    assert all(len(r.url.path.rsplit("/", 1)[-1]) == 5 for r in seen)
    assert BREACHED not in str(seen[0].url)
    assert seen[0].headers["add-padding"] == "true"

    user = await register(client)
    changed = await client.post(
        "/api/auth/password",
        json={"current_password": user["password"], "new_password": BREACHED},
    )
    assert changed.status_code == 422
    assert changed.json()["error"]["code"] == "breached_password"


async def test_the_check_fails_open_when_the_service_is_down() -> None:
    def down(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as http:
        assert await is_breached(http, BREACHED) is None
    async with httpx.AsyncClient(transport=corpus_transport([])) as http:
        assert await is_breached(http, BREACHED) is True
        assert await is_breached(http, "a passphrase nobody has used before") is False
