from typing import Any

from tests.conftest import make_client, register


async def test_register_signs_in_and_me_returns_the_user(client: Any) -> None:
    user = await register(client, email="Ana@Example.com")
    assert user["email"] == "ana@example.com"
    assert client.cookies.get("tp_session")
    assert client.cookies.get("tp_csrf")

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["id"] == user["id"]
    assert "password_hash" not in me.json()


async def test_duplicate_email_is_rejected_case_insensitively(client: Any, app: Any) -> None:
    await register(client, email="dup@example.com")
    async with make_client(app) as other:
        response = await other.post(
            "/api/auth/register",
            json={
                "email": "DUP@example.com",
                "password": "another long password",
                "display_name": "Other",
            },
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"


async def test_weak_and_invalid_registrations(client: Any) -> None:
    short = await client.post(
        "/api/auth/register",
        json={"email": "a@example.com", "password": "short", "display_name": "Ana"},
    )
    assert short.status_code == 422
    bad_state = await client.post(
        "/api/auth/register",
        json={
            "email": "b@example.com",
            "password": "long enough password",
            "display_name": "Bia",
            "state": "XX",
        },
    )
    assert bad_state.status_code == 422


async def test_login_errors_do_not_reveal_whether_the_account_exists(client: Any, app: Any) -> None:
    user = await register(client)
    async with make_client(app) as anon:
        wrong = await anon.post(
            "/api/auth/login", json={"email": user["email"], "password": "wrong password!"}
        )
        unknown = await anon.post(
            "/api/auth/login", json={"email": "nobody@example.com", "password": "whatever123"}
        )
        ok = await anon.post(
            "/api/auth/login", json={"email": user["email"], "password": user["password"]}
        )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert ok.status_code == 200


async def test_unsafe_requests_require_the_csrf_token(client: Any) -> None:
    await register(client)
    response = await client.patch(
        "/api/auth/me", json={"display_name": "New name"}, headers={"X-CSRF-Token": "forged"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


async def test_cross_origin_unsafe_requests_are_rejected(client: Any) -> None:
    await register(client)
    response = await client.patch(
        "/api/auth/me", json={"display_name": "x" * 5}, headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "bad_origin"


async def test_logout_revokes_the_session(client: Any) -> None:
    await register(client)
    token = client.cookies.get("tp_session")
    assert (await client.post("/api/auth/logout")).status_code == 204
    # Even if a copy of the cookie survived, the server no longer accepts it.
    client.cookies.set("tp_session", token)
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_password_change_signs_out_other_devices(client: Any, app: Any) -> None:
    user = await register(client)
    async with make_client(app) as other_device:
        login = await other_device.post(
            "/api/auth/login", json={"email": user["email"], "password": user["password"]}
        )
        assert login.status_code == 200

        changed = await client.post(
            "/api/auth/password",
            json={"current_password": user["password"], "new_password": "a brand new password"},
        )
        assert changed.status_code == 204
        assert (await client.get("/api/auth/me")).status_code == 200
        assert (await other_device.get("/api/auth/me")).status_code == 401


async def test_login_is_rate_limited_per_account(client: Any, app: Any) -> None:
    user = await register(client)
    async with make_client(app) as attacker:
        statuses = [
            (
                await attacker.post(
                    "/api/auth/login", json={"email": user["email"], "password": f"guess-{i}xx"}
                )
            ).status_code
            for i in range(11)
        ]
        blocked = await attacker.post(
            "/api/auth/login", json={"email": user["email"], "password": user["password"]}
        )
    assert statuses[:10] == [401] * 10
    assert statuses[10] == 429
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0


async def test_responses_carry_request_id_and_security_headers(client: Any) -> None:
    response = await client.get("/api/categories")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert len(response.headers["X-Request-ID"]) >= 8


async def test_writes_are_committed_before_the_response_is_sent(client: Any, app: Any) -> None:
    """A response must never report success for data another request cannot read yet."""
    from sqlalchemy import text

    await register(client)
    await client.patch("/api/auth/me", json={"display_name": "Committed Name"})
    # Read straight from the database, outside the app, right after the response.
    async with app.state.db.sessionmaker() as db:
        names = (await db.execute(text("SELECT display_name FROM users"))).scalars().all()
    assert "Committed Name" in names
