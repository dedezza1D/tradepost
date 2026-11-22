"""Error text that leaves the process keeps the kind and place of a failure, not the data."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import IntegrityError

from tests.test_checkout import sql
from tradepost.identity.models import User
from tradepost.logs import (
    RedactExceptions,
    error_summary,
    redact_exc_info,
    safe_message,
    safe_traceback,
)
from tradepost.ops.metrics import purge

PRIVATE = "private.person@example.com"


async def duplicate_user(app: Any) -> None:
    async with app.state.db.sessionmaker() as db, db.begin():
        for name in ("first", "second"):
            db.add(User(email=PRIVATE, password_hash="x", display_name=name))
            await db.flush()


async def integrity_error(app: Any) -> IntegrityError:
    try:
        await duplicate_user(app)
    except IntegrityError as exc:
        return exc
    raise AssertionError("the duplicate email was accepted")


async def test_the_driver_repeats_the_row_so_redaction_is_needed(app: Any) -> None:
    exc = await integrity_error(app)
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None:
        chain.append(current)
        current = current.__cause__
    # The database's own DETAIL line names the value even with bound parameters hidden.
    assert any(PRIVATE in str(item) for item in chain)
    assert "SQL parameters hidden" in str(exc)
    assert PRIVATE not in str(exc)


async def test_unhandled_database_errors_are_stored_without_values(app: Any) -> None:
    async def broken() -> None:
        await duplicate_user(app)

    app.add_api_route("/api/test-duplicate", broken, methods=["POST"])
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.post("/api/test-duplicate")).status_code == 500
    [(kind, message, traceback)] = await sql(
        app, "SELECT kind, message, traceback FROM error_events"
    )
    assert kind == "IntegrityError"
    assert "SQLSTATE 23505" in message
    assert "INSERT INTO users" in message
    assert PRIVATE not in message
    assert PRIVATE not in traceback
    assert "sqlalchemy.exc.IntegrityError: UniqueViolationError" in traceback


async def test_logs_and_records_leave_the_values_out(app: Any) -> None:
    exc = await integrity_error(app)

    assert PRIVATE not in error_summary(exc)
    assert error_summary(exc).startswith("IntegrityError: UniqueViolationError (SQLSTATE 23505")

    rendered = redact_exc_info(None, "error", {"event": "failed", "exc_info": exc})
    assert "exc_info" not in rendered
    assert PRIVATE not in rendered["exception"]
    assert "Traceback (most recent call last)" in rendered["exception"]
    try:
        raise exc
    except IntegrityError:
        # What ``log.exception`` passes along.
        current = redact_exc_info(None, "error", {"event": "failed", "exc_info": True})
    assert PRIVATE not in current["exception"]

    record = logging.LogRecord(
        "uvicorn.error",
        logging.ERROR,
        __file__,
        1,
        "Exception in ASGI application\n",
        None,
        (type(exc), exc, exc.__traceback__),
    )
    assert RedactExceptions().filter(record)
    server_line = logging.Formatter().format(record)
    assert server_line.startswith("Exception in ASGI application")
    assert "SQLSTATE 23505" in server_line
    assert PRIVATE not in server_line


class InvalidTextRepresentation(Exception):
    """Shaped like the driver's errors: a SQLSTATE and the offending object."""

    sqlstate = "22P02"
    table_name = "users"


def test_the_chain_keeps_its_order_and_links() -> None:
    try:
        try:
            # Built at run time: the traceback quotes source lines, and this one is not data.
            value = "-".join(("secret", "value"))
            raise InvalidTextRepresentation(f'invalid input syntax for type uuid: "{value}"')
        except InvalidTextRepresentation as inner:
            raise RuntimeError("could not load the user") from inner
    except RuntimeError as exc:
        text = safe_traceback(exc)
    assert "secret-value" not in text
    first = text.index("InvalidTextRepresentation: SQLSTATE 22P02, table users")
    link = text.index("direct cause of the following exception")
    last = text.index("RuntimeError: could not load the user")
    assert first < link < last


def test_validation_errors_name_the_field_not_the_input() -> None:
    class Payer(BaseModel):
        age: int

    with pytest.raises(ValidationError) as caught:
        Payer.model_validate({"age": "secret-value"})
    message = safe_message(caught.value)
    assert "secret-value" not in message
    assert message == "1 validation error(s) for Payer: age: int_parsing"


def test_other_errors_keep_their_message() -> None:
    assert safe_message(KeyError("listing_id")) == "'listing_id'"


async def test_error_groups_not_seen_for_a_month_are_purged(app: Any) -> None:
    now = datetime.now(UTC)
    for fingerprint, last_seen in (("old", now - timedelta(days=31)), ("recent", now)):
        await sql(
            app,
            "INSERT INTO error_events (fingerprint, kind, route, message, traceback, count,"
            " last_seen) VALUES (:fp, 'E', 'GET /', '', '', 1, :seen)",
            fp=fingerprint,
            seen=last_seen,
        )
    async with app.state.db.sessionmaker() as db, db.begin():
        assert await purge(db) == 1
    assert await sql(app, "SELECT fingerprint FROM error_events") == [("recent",)]
