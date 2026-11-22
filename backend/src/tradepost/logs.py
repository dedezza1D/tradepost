import logging
import sys
import traceback
from typing import Any

import structlog

# Exception text often carries the data being processed: a database error repeats the row
# (``DETAIL: Key (email)=(...)``, ``invalid input syntax for type uuid: "..."``) and a
# validation error repeats the input. Everything that leaves the process (logs, error tables,
# job and payment records) goes through the functions below, which keep the kind of error,
# its location and safe identifiers (SQLSTATE, constraint, table, column, SQL text without
# parameters), but not the values.

_CAUSE = "\nThe above exception was the direct cause of the following exception:\n\n"
_CONTEXT = "\nDuring handling of the above exception, another exception occurred:\n\n"


def _database_message(exc: BaseException) -> str | None:
    from sqlalchemy.exc import StatementError

    if isinstance(exc, StatementError):
        orig = exc.orig
        about = _database_message(orig) if orig is not None else None
        kind = type(orig).__name__ if orig is not None else type(exc).__name__
        return f"{kind}{f' ({about})' if about else ''} [SQL: {exc.statement}]"
    sqlstate = getattr(exc, "sqlstate", None)
    if sqlstate is None:
        return None
    parts = [f"SQLSTATE {sqlstate}"]
    for name in ("constraint_name", "table_name", "column_name"):
        value = getattr(exc, name, None)
        if value:
            parts.append(f"{name.removesuffix('_name')} {value}")
    return ", ".join(parts)


def _validation_message(exc: BaseException) -> str | None:
    from pydantic import ValidationError

    if not isinstance(exc, ValidationError):
        return None
    where = "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['type']}" for e in exc.errors()
    )
    return f"{exc.error_count()} validation error(s) for {exc.title}: {where}"


def safe_message(exc: BaseException) -> str:
    """The exception's message without the values it may repeat."""
    for describe in (_database_message, _validation_message):
        message = describe(exc)
        if message is not None:
            return message
    return str(exc)


def error_summary(exc: BaseException) -> str:
    """One line for records such as a failed job or payment event."""
    return f"{type(exc).__name__}: {safe_message(exc)}"


def safe_traceback(exc: BaseException) -> str:
    """Like ``traceback.format_exception``, with each message replaced by ``safe_message``."""
    chain: list[tuple[BaseException, str]] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    link = ""
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append((current, link))
        if current.__cause__ is not None:
            current, link = current.__cause__, _CAUSE
        elif current.__context__ is not None and not current.__suppress_context__:
            current, link = current.__context__, _CONTEXT
        else:
            current = None
    out: list[str] = []
    # Oldest first, as Python prints it; each link sits between an exception and the next.
    for index, (item, _) in enumerate(reversed(chain)):
        if index:
            out.append(chain[len(chain) - index][1])
        if item.__traceback__ is not None:
            out.append("Traceback (most recent call last):\n")
            out.extend(traceback.format_tb(item.__traceback__))
        kind = type(item)
        module = "" if kind.__module__ == "builtins" else f"{kind.__module__}."
        out.append(f"{module}{kind.__qualname__}: {safe_message(item)}\n")
    return "".join(out)


def _exception_of(exc_info: Any) -> BaseException | None:
    if isinstance(exc_info, BaseException):
        return exc_info
    if exc_info is True:
        return sys.exc_info()[1]
    if isinstance(exc_info, tuple) and len(exc_info) == 3:
        value = exc_info[1]
        return value if isinstance(value, BaseException) else None
    return None


def redact_exc_info(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor: renders ``exc_info`` with ``safe_traceback`` (replaces
    ``format_exc_info``)."""
    exc_info = event_dict.pop("exc_info", None)
    exc = _exception_of(exc_info)
    if exc is not None:
        event_dict["exception"] = safe_traceback(exc)
    return event_dict


class RedactExceptions(logging.Filter):
    """The same for standard-library records, such as the server's own error log (Starlette
    re-raises unhandled errors after the app's handler, and the server logs them)."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = _exception_of(record.exc_info)
        if exc is not None:
            record.msg = f"{record.getMessage()}\n{safe_traceback(exc).rstrip()}"
            record.args = None
            record.exc_info = None
            record.exc_text = None
        return True


_SERVER_LOGGERS = ("uvicorn.error",)


def configure_logging(*, json: bool) -> None:
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        redact_exc_info,
    ]
    renderer: Any = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[*processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    for name in _SERVER_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactExceptions) for f in logger.filters):
            logger.addFilter(RedactExceptions())


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
