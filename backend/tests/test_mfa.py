"""Second factor (TOTP and recovery codes): setup, two-step sign-in, administrators."""

import base64
import time
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from tests.conftest import make_client, register
from tests.test_checkout import sql
from tradepost.identity import mfa

RFC_SECRET = b"12345678901234567890"


def test_codes_match_the_rfc_6238_vectors() -> None:
    # RFC 6238 appendix B (SHA-1), last six digits of the eight-digit values.
    assert mfa.code_at(RFC_SECRET, 59 // 30) == "287082"
    assert mfa.code_at(RFC_SECRET, 1111111109 // 30) == "081804"
    assert mfa.code_at(RFC_SECRET, 2000000000 // 30) == "279037"


def test_secrets_are_stored_encrypted() -> None:
    blob = mfa.encrypt_secret(RFC_SECRET)
    assert RFC_SECRET not in blob
    assert mfa.encrypt_secret(RFC_SECRET) != blob
    assert mfa.decrypt_secret(blob) == RFC_SECRET


def test_a_code_is_accepted_for_its_step_and_the_previous_one_only() -> None:
    now = 1_800_000_000.0
    step = mfa.current_step(now)
    current = mfa.code_at(RFC_SECRET, step)
    previous = mfa.code_at(RFC_SECRET, step - 1)
    older = mfa.code_at(RFC_SECRET, step - 2)
    assert mfa.matching_step(RFC_SECRET, current, after=0, now=now) == step
    assert mfa.matching_step(RFC_SECRET, previous, after=0, now=now) == step - 1
    assert mfa.matching_step(RFC_SECRET, older, after=0, now=now) is None
    # A step already used is never accepted again.
    assert mfa.matching_step(RFC_SECRET, current, after=step, now=now) is None


class Clock:
    """Moves the second-factor clock in whole steps, so each test code is fresh."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now = float(int(time.time()) // 30 * 30)
        monkeypatch.setattr(mfa, "time", SimpleNamespace(time=lambda: self.now))

    def code(self, secret: bytes) -> str:
        self.now += 30
        return mfa.code_at(secret, mfa.current_step(self.now))


async def enable(client: Any, password: str, clock: Clock) -> tuple[bytes, list[str]]:
    setup = await client.post("/api/auth/mfa/setup", json={"password": password})
    assert setup.status_code == 200, setup.text
    body = setup.json()
    assert body["otpauth_uri"].startswith("otpauth://totp/tradepost:")
    assert body["qr_code"].startswith("data:image/svg+xml")
    secret = base64.b32decode(body["secret"] + "=" * (-len(body["secret"]) % 8))
    confirmed = await client.post("/api/auth/mfa/confirm", json={"code": clock.code(secret)})
    assert confirmed.status_code == 200, confirmed.text
    return secret, confirmed.json()["recovery_codes"]


async def test_sign_in_asks_for_the_second_factor(
    client: Any, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock(monkeypatch)
    user = await register(client)
    secret, codes = await enable(client, user["password"], clock)
    assert len(codes) == 10
    assert (await client.get("/api/auth/mfa")).json() == {
        "enabled": True,
        "recovery_codes_left": 10,
    }
    credentials = {"email": user["email"], "password": user["password"]}

    async with make_client(app) as browser:
        first = await browser.post("/api/auth/login", json=credentials)
        assert first.json() == {"mfa_required": True}
        # The password alone is not a sign-in.
        assert (await browser.get("/api/auth/me")).status_code == 401
        wrong = await browser.post("/api/auth/login/mfa", json={"code": "000000"})
        assert wrong.json()["error"]["code"] == "invalid_code"
        code = clock.code(secret)
        signed_in = await browser.post("/api/auth/login/mfa", json={"code": code})
        assert signed_in.status_code == 200
        assert signed_in.json()["email"] == user["email"]
        assert (await browser.get("/api/auth/me")).status_code == 200

    async with make_client(app) as replay:
        await replay.post("/api/auth/login", json=credentials)
        # The same code, still within its time window, does not work twice.
        again = await replay.post("/api/auth/login/mfa", json={"code": code})
        assert again.status_code == 400
        recovered = await replay.post("/api/auth/login/mfa", json={"recovery_code": codes[0]})
        assert recovered.status_code == 200

    async with make_client(app) as reuse:
        await reuse.post("/api/auth/login", json=credentials)
        used = await reuse.post("/api/auth/login/mfa", json={"recovery_code": codes[0]})
        assert used.status_code == 400
    assert (await client.get("/api/auth/mfa")).json()["recovery_codes_left"] == 9


async def test_the_pending_step_is_short_and_needs_the_password_first(
    client: Any, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock(monkeypatch)
    user = await register(client)
    await enable(client, user["password"], clock)
    async with make_client(app) as browser:
        no_password = await browser.post("/api/auth/login/mfa", json={"code": "123456"})
        assert no_password.status_code == 401
        assert no_password.json()["error"]["code"] == "mfa_session_expired"
        await browser.post(
            "/api/auth/login", json={"email": user["email"], "password": user["password"]}
        )
    [(minutes,)] = await sql(
        app,
        "SELECT extract(epoch FROM expires_at - created_at) / 60 FROM sessions"
        " WHERE auth_level = 0",
    )
    assert round(float(minutes)) == 5


async def test_wrong_codes_are_limited_per_account(
    client: Any, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock(monkeypatch)
    user = await register(client)
    secret, _ = await enable(client, user["password"], clock)
    async with make_client(app) as browser:
        await browser.post(
            "/api/auth/login", json={"email": user["email"], "password": user["password"]}
        )
        statuses = [
            (await browser.post("/api/auth/login/mfa", json={"code": "000000"})).status_code
            for _ in range(10)
        ]
        assert statuses == [400] * 10
        # Past the limit even the right code is refused, so guessing learns nothing.
        right = await browser.post("/api/auth/login/mfa", json={"code": clock.code(secret)})
        assert right.status_code == 429


async def test_administration_needs_the_second_factor(
    client: Any, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock(monkeypatch)
    user = await register(client)
    await sql(app, "UPDATE users SET is_admin = true WHERE email = :e", e=user["email"])
    refused = await client.get("/api/admin/ops")
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "mfa_required"
    # Turning it on upgrades this browser's session.
    await enable(client, user["password"], clock)
    assert (await client.get("/api/admin/ops")).status_code == 200


async def test_turning_it_off_needs_the_password_and_a_code(
    client: Any, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock(monkeypatch)
    user = await register(client)
    secret, codes = await enable(client, user["password"], clock)
    assert (await client.post("/api/auth/mfa/setup", json={"password": user["password"]})).json()[
        "error"
    ]["code"] == "mfa_already_enabled"

    wrong_password = await client.post(
        "/api/auth/mfa/disable", json={"password": "not the password at all", "code": "1"}
    )
    assert wrong_password.status_code == 401
    no_code = await client.post("/api/auth/mfa/disable", json={"password": user["password"]})
    assert no_code.status_code == 400
    off = await client.post(
        "/api/auth/mfa/disable", json={"password": user["password"], "code": clock.code(secret)}
    )
    assert off.status_code == 204
    assert (await client.get("/api/auth/mfa")).json() == {
        "enabled": False,
        "recovery_codes_left": 0,
    }
    [(left,)] = await sql(
        app,
        "SELECT count(*) FROM recovery_codes WHERE user_id ="
        " (SELECT id FROM users WHERE email = :e)",
        e=user["email"],
    )
    assert left == 0
    async with make_client(app) as browser:
        signed_in = await browser.post(
            "/api/auth/login", json={"email": user["email"], "password": user["password"]}
        )
        assert signed_in.json()["email"] == user["email"]
    assert codes  # issued before, now gone


async def test_new_recovery_codes_replace_the_old_ones(
    client: Any, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock(monkeypatch)
    user = await register(client)
    _, old = await enable(client, user["password"], clock)
    fresh = await client.post("/api/auth/mfa/recovery-codes", json={"password": user["password"]})
    new = fresh.json()["recovery_codes"]
    assert len(new) == 10
    assert not set(new) & set(old)
    async with make_client(app) as browser:
        await browser.post(
            "/api/auth/login", json={"email": user["email"], "password": user["password"]}
        )
        stale = await browser.post("/api/auth/login/mfa", json={"recovery_code": old[1]})
        assert stale.status_code == 400
        current = await browser.post(
            "/api/auth/login/mfa", json={"recovery_code": new[1].upper().replace("-", " ")}
        )
        assert current.status_code == 200


async def test_turning_it_on_ends_the_other_sessions(
    client: Any, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock(monkeypatch)
    user = await register(client)
    async with make_client(app) as other:
        await other.post(
            "/api/auth/login", json={"email": user["email"], "password": user["password"]}
        )
        await enable(client, user["password"], clock)
        assert (await other.get("/api/auth/me")).status_code == 401
    assert (await client.get("/api/auth/me")).status_code == 200
    [(level,)] = await sql(
        app,
        "SELECT auth_level FROM sessions WHERE revoked_at IS NULL AND user_id ="
        " (SELECT id FROM users WHERE email = :e)",
        e=user["email"],
    )
    assert level == 2
    assert uuid.UUID(str((await client.get("/api/auth/me")).json()["id"]))
