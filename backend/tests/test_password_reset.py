"""Password reset by e-mail: one answer for every address, single-use short-lived links stored
as hashes, the sign-up password rules, sessions signed out and the owner told."""

import dataclasses
import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from tests.conftest import make_client, register
from tests.test_checkout import sql
from tests.test_password_policy import BREACHED, corpus_transport
from tradepost.config import get_settings
from tradepost.identity.password_reset import purge_expired, send_reset_link
from tradepost.notifications.email import Email, LogEmail, NoEmail
from tradepost.security.passwords import hash_password

NEW_PASSWORD = "a fresh and long passphrase"


@pytest.fixture
def mail(app: Any) -> LogEmail:
    sender = LogEmail()
    app.state.jobs = dataclasses.replace(app.state.jobs, email=sender)
    return sender


def token_in(message: Email) -> str:
    match = re.search(r"/reset-password#token=([A-Za-z0-9_-]+)", message.text)
    assert match, message.text
    return match.group(1)


async def ask_reset(app: Any, email: str) -> httpx.Response:
    async with make_client(app) as anonymous:
        response: httpx.Response = await anonymous.post(
            "/api/auth/password-reset", json={"email": email}
        )
        return response


async def reset(app: Any, token: str, password: str = NEW_PASSWORD) -> httpx.Response:
    async with make_client(app) as anonymous:
        response: httpx.Response = await anonymous.post(
            "/api/auth/password-reset/confirm", json={"token": token, "new_password": password}
        )
        return response


async def login(app: Any, email: str, password: str) -> int:
    async with make_client(app) as c:
        response = await c.post("/api/auth/login", json={"email": email, "password": password})
        return response.status_code


async def test_a_link_sets_a_new_password_once_and_signs_every_session_out(
    client: Any, app: Any, mail: LogEmail
) -> None:
    user = await register(client, email="Rita@Example.com", locale="en")
    async with make_client(app) as phone:
        await phone.post(
            "/api/auth/login", json={"email": user["email"], "password": user["password"]}
        )

        asked = await ask_reset(app, "RITA@example.com")
        assert asked.status_code == 204
        [message] = mail.sent
        assert message.to == "rita@example.com"
        assert message.subject == "Reset your tradepost password"  # the account's language
        assert "http://testserver/reset-password#token=" in message.text
        assert "30 minutes" in message.text
        token = token_in(message)
        assert len(token) >= 43  # 256 random bits

        # Only the token's hash is stored.
        [(stored, expires)] = await sql(
            app, "SELECT token_hash, expires_at FROM password_reset_tokens"
        )
        assert bytes(stored) == hashlib.sha256(token.encode()).digest()
        assert timedelta(minutes=25) < expires - datetime.now(UTC) <= timedelta(minutes=30)

        done = await reset(app, token)
        assert done.status_code == 204
        assert "tp_device=" in done.headers.get("set-cookie", "")

        # Every session ended, on every device.
        assert (await client.get("/api/auth/me")).status_code == 401
        assert (await phone.get("/api/auth/me")).status_code == 401
    assert await sql(app, "SELECT 1 FROM sessions WHERE revoked_at IS NULL") == []

    assert await login(app, user["email"], user["password"]) == 401
    assert await login(app, user["email"], NEW_PASSWORD) == 200

    # The owner is told, with a way to take the account back.
    changed = mail.sent[1]
    assert changed.to == "rita@example.com"
    assert changed.subject == "Your account's password was changed"
    assert "http://testserver/forgot-password" in changed.text

    # The link works once.
    again = await reset(app, token, "yet another long passphrase")
    assert again.status_code == 400
    assert again.json()["error"]["code"] == "reset_link_invalid"
    assert await sql(app, "SELECT 1 FROM password_reset_tokens") == []


async def test_the_answer_is_the_same_whether_or_not_the_account_exists(
    client: Any, app: Any, mail: LogEmail
) -> None:
    await register(client, email="known@example.com")
    known = await ask_reset(app, "known@example.com")
    unknown = await ask_reset(app, "nobody@example.com")
    assert (known.status_code, known.content) == (unknown.status_code, unknown.content)
    assert known.status_code == 204
    assert [m.to for m in mail.sent] == ["known@example.com"]
    assert len(await sql(app, "SELECT 1 FROM password_reset_tokens")) == 1


async def test_accounts_that_cannot_sign_in_get_no_link(app: Any, mail: LogEmail) -> None:
    async with app.state.db.sessionmaker() as db, db.begin():
        for email, password_hash in (
            ("seeded@example.com", "!"),  # no usable password
            ("someone@demo.tradepost.invalid", hash_password("a long demo password")),
        ):
            await db.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, display_name)"
                    " VALUES (gen_random_uuid(), :email, :hash, 'Seeded')"
                ),
                {"email": email, "hash": password_hash},
            )

    seeded = await ask_reset(app, "seeded@example.com")
    assert seeded.status_code == 204  # the same answer as for any other address
    # The demonstration domain is not a deliverable address at all.
    assert (await ask_reset(app, "someone@demo.tradepost.invalid")).status_code == 422
    await send_reset_link(app.state.db, mail, "http://testserver", "someone@demo.tradepost.invalid")
    assert mail.sent == []
    assert await sql(app, "SELECT 1 FROM password_reset_tokens") == []


async def test_an_expired_link_is_refused(client: Any, app: Any, mail: LogEmail) -> None:
    user = await register(client)
    await ask_reset(app, user["email"])
    await sql(app, "UPDATE password_reset_tokens SET expires_at = now() - interval '1 second'")
    expired = await reset(app, token_in(mail.sent[0]))
    assert expired.status_code == 400
    assert expired.json()["error"]["code"] == "reset_link_invalid"
    assert await login(app, user["email"], user["password"]) == 200

    async with app.state.db.sessionmaker() as db, db.begin():
        assert await purge_expired(db) == 1


async def test_a_newer_link_replaces_the_older_one(client: Any, app: Any, mail: LogEmail) -> None:
    user = await register(client)
    await ask_reset(app, user["email"])
    await ask_reset(app, user["email"])
    first, second = (token_in(m) for m in mail.sent)
    assert first != second
    assert (await reset(app, first)).json()["error"]["code"] == "reset_link_invalid"
    assert (await reset(app, second)).status_code == 204


async def test_unknown_or_malformed_tokens_are_refused(app: Any, mail: LogEmail) -> None:
    unknown = await reset(app, "x" * 43)
    assert unknown.status_code == 400
    assert unknown.json()["error"]["code"] == "reset_link_invalid"
    assert (await reset(app, "short")).status_code == 422


async def test_the_new_password_follows_the_sign_up_rules(
    client: Any, app: Any, mail: LogEmail, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await register(client, email="maria.souza@example.com")
    await ask_reset(app, user["email"])
    token = token_in(mail.sent[0])

    short = await reset(app, token, "fourteen chars")
    assert short.status_code == 422
    assert short.json()["error"]["fields"][0]["loc"] == ["body", "new_password"]
    context = await reset(app, token, "maria.souza-and-more-words")
    assert context.json()["error"]["code"] == "weak_password"

    monkeypatch.setattr(get_settings(), "breached_password_check", True)
    app.state.http = httpx.AsyncClient(transport=corpus_transport([]))
    breached = await reset(app, token, BREACHED)
    assert breached.json()["error"]["code"] == "breached_password"

    # A refused password does not use the link up, nor sign anyone out.
    assert (await client.get("/api/auth/me")).status_code == 200
    assert (await reset(app, token)).status_code == 204


async def test_requests_are_limited_per_address_and_per_ip(app: Any, mail: LogEmail) -> None:
    answers = [(await ask_reset(app, "flood@example.com")).status_code for _ in range(4)]
    assert answers == [204, 204, 204, 429]
    # Other addresses go on until the IP's own limit.
    answers = [(await ask_reset(app, f"other{i}@example.com")).status_code for i in range(7)]
    assert answers == [204] * 6 + [429]


async def test_changing_the_password_drops_pending_links_and_tells_the_owner(
    client: Any, app: Any, mail: LogEmail
) -> None:
    user = await register(client)
    await ask_reset(app, user["email"])
    changed = await client.post(
        "/api/auth/password",
        json={"current_password": user["password"], "new_password": NEW_PASSWORD},
    )
    assert changed.status_code == 204
    assert mail.sent[-1].subject == "A senha da sua conta foi alterada"
    assert (await reset(app, token_in(mail.sent[0]))).status_code == 400


async def test_without_e_mail_the_reset_is_not_offered(app: Any) -> None:
    app.state.jobs = dataclasses.replace(app.state.jobs, email=NoEmail())
    response = await ask_reset(app, "someone@example.com")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "email_unavailable"
