"""Session lifetimes (idle and absolute, by role) and the __Host- cookie names."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tests.conftest import make_client, register
from tests.test_checkout import sql
from tradepost.config import get_settings


async def age_session(app: Any, email: str, *, idle: timedelta, age: timedelta) -> None:
    now = datetime.now(UTC)
    await sql(
        app,
        "UPDATE sessions SET last_seen_at = :seen, created_at = :created"
        " WHERE user_id = (SELECT id FROM users WHERE email = :email)",
        seen=now - idle,
        created=now - age,
        email=email,
    )


async def make_admin(app: Any, email: str) -> None:
    await sql(app, "UPDATE users SET is_admin = true WHERE email = :email", email=email)


async def test_a_session_left_unused_ends(client: Any, app: Any) -> None:
    user = await register(client)
    await age_session(app, user["email"], idle=timedelta(days=6), age=timedelta(days=6))
    assert (await client.get("/api/auth/me")).status_code == 200
    await age_session(app, user["email"], idle=timedelta(days=8), age=timedelta(days=8))
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_administrator_sessions_are_short(client: Any, app: Any) -> None:
    user = await register(client)
    await make_admin(app, user["email"])
    # An hour unused is enough for an administrator, not for a regular account.
    await age_session(app, user["email"], idle=timedelta(minutes=61), age=timedelta(hours=2))
    assert (await client.get("/api/auth/me")).status_code == 401

    other = await register(client)
    await age_session(app, other["email"], idle=timedelta(minutes=61), age=timedelta(hours=13))
    assert (await client.get("/api/auth/me")).status_code == 200
    # The role applies to sessions opened before it was granted.
    await make_admin(app, other["email"])
    await age_session(app, other["email"], idle=timedelta(minutes=1), age=timedelta(hours=13))
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_administrators_sign_in_for_twelve_hours(client: Any, app: Any) -> None:
    user = await register(client)
    await make_admin(app, user["email"])
    await client.post("/api/auth/logout")
    response = await client.post(
        "/api/auth/login", json={"email": user["email"], "password": user["password"]}
    )
    assert response.status_code == 200
    session_cookie = next(
        c for c in response.headers.get_list("set-cookie") if c.startswith("tp_session=")
    )
    assert "Max-Age=43200" in session_cookie
    [(hours,)] = await sql(
        app,
        "SELECT extract(epoch FROM expires_at - created_at) / 3600 FROM sessions"
        " WHERE revoked_at IS NULL",
    )
    assert round(float(hours)) == 12


async def test_over_https_the_cookies_carry_the_host_prefix(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "cookie_secure", True)
    async with make_client(app) as browser:
        response = await browser.post(
            "/api/auth/register",
            json={
                "email": "host-prefix@example.com",
                "password": "correct horse battery",
                "display_name": "Host",
            },
        )
    cookies = response.headers.get_list("set-cookie")
    session = next(c for c in cookies if c.startswith("__Host-tp_session="))
    csrf = next(c for c in cookies if c.startswith("__Host-tp_csrf="))
    for cookie in (session, csrf):
        assert "Secure" in cookie
        assert "Path=/" in cookie
        assert "Domain" not in cookie
    assert "HttpOnly" in session
    assert "HttpOnly" not in csrf
    # Bare-name cookies from before the prefix are removed.
    assert any(c.startswith("tp_session=") and "Max-Age=0" in c for c in cookies)

    token = session.split(";", 1)[0].split("=", 1)[1]
    async with make_client(app) as browser:
        # The browser sends prefixed cookies only over HTTPS; the header is set by hand here.
        prefixed = await browser.get(
            "/api/auth/me", headers={"Cookie": f"__Host-tp_session={token}"}
        )
        legacy = await browser.get("/api/auth/me", headers={"Cookie": f"tp_session={token}"})
    assert prefixed.status_code == 200
    # Sessions issued before the prefix keep working until they expire.
    assert legacy.status_code == 200


async def test_sign_out_clears_both_names(client: Any) -> None:
    await register(client)
    response = await client.post("/api/auth/logout")
    cleared = [c.split("=", 1)[0] for c in response.headers.get_list("set-cookie")]
    assert {"tp_session", "tp_csrf", "__Host-tp_session", "__Host-tp_csrf"} <= set(cleared)
    host = [c for c in response.headers.get_list("set-cookie") if c.startswith("__Host-")]
    assert all("Secure" in c for c in host)
