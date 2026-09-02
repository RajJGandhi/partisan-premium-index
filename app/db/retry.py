"""Bounded retry for *transient* Supabase/Postgres connectivity failures.

PPI produced a real production incident: a fresh connect to the Supavisor session pooler timed
out (``could not receive data from server`` / ``Operation timed out``) partway through a
multi-minute run; the manual retry succeeded. That is a connectivity blip, not a data problem --
this module retries exactly those, with exponential backoff and a hard cap, and re-raises
everything else (constraint violations, permission denied, undefined column, ...) immediately so
genuine failures stay loud.

Style matches the HTTP retries already used across ``app/ingest`` and ``app/ppi`` (tenacity,
``wait_exponential`` + ``stop_after_attempt`` + ``reraise=True``).
"""

from __future__ import annotations

import re
from typing import Any, Callable, TypeVar

from sqlalchemy.exc import (
    DBAPIError,
    IntegrityError,
    InterfaceError,
    OperationalError,
    ProgrammingError,
)
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings

T = TypeVar("T")

# Postgres SQLSTATEs that mean "the connection/server was transiently unavailable" -- safe to
# retry with a fresh connection. (Class 08 = connection exception; plus a few 57/53.)
RETRYABLE_SQLSTATES: frozenset[str] = frozenset(
    {
        "08000",  # connection_exception
        "08003",  # connection_does_not_exist
        "08006",  # connection_failure
        "08001",  # sqlclient_unable_to_establish_sqlconnection
        "08004",  # sqlserver_rejected_establishment_of_sqlconnection
        "08007",  # transaction_resolution_unknown
        "57P01",  # admin_shutdown
        "57P02",  # crash_shutdown
        "57P03",  # cannot_connect_now (server starting up)
        "57P05",  # idle_session_timeout
        "53300",  # too_many_connections (pooler saturated -- back off and retry)
        "40001",  # serialization_failure
        "40P01",  # deadlock_detected
        "55P03",  # lock_not_available
    }
)

# SQLSTATEs that must NEVER be retried even if they surface as an OperationalError.
NON_RETRYABLE_SQLSTATES: frozenset[str] = frozenset(
    {
        "28P01",  # invalid_password
        "28000",  # invalid_authorization_specification
        "3D000",  # invalid_catalog_name (bad database)
        "42501",  # insufficient_privilege
        "53400",  # configuration_limit_exceeded
    }
)

_TRANSIENT_MESSAGE_RE = re.compile(
    r"(could not receive data from server"
    r"|could not send data to server"
    r"|server closed the connection unexpectedly"
    r"|connection timed out"
    r"|connection reset by peer"
    r"|connection refused"
    r"|no connection to the server"
    r"|terminating connection due to"
    r"|SSL connection has been closed unexpectedly"
    r"|EOF detected"
    r"|Operation timed out"
    r"|timeout expired"
    r"|the database system is (starting up|shutting down|in recovery mode))",
    re.IGNORECASE,
)


def _sqlstate(exc: BaseException) -> str | None:
    code = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
    if code:
        return str(code)
    orig = getattr(exc, "orig", None)
    if orig is not None and orig is not exc:
        return _sqlstate(orig)
    cause = exc.__cause__
    if cause is not None and cause is not exc:
        return _sqlstate(cause)
    return None


def is_transient_db_error(exc: BaseException) -> bool:
    """True only for a connectivity/availability blip that a fresh connection would clear."""
    # Never retry a data/logic failure, whatever wrapper it arrives in.
    if isinstance(exc, (IntegrityError, ProgrammingError)):
        return False

    # A DBAPI InterfaceError means the connection/cursor itself is unusable (closed pointer,
    # broken protocol state) -- a fresh connection clears it. It is never a query-content error.
    if isinstance(exc, InterfaceError):
        return True

    state = _sqlstate(exc)
    if state:
        if state in NON_RETRYABLE_SQLSTATES:
            return False
        if state in RETRYABLE_SQLSTATES:
            return True
        # An explicit non-connection SQLSTATE (e.g. 23505, 42703, 22P02) -> not transient.
        if state[:2] not in {"08", "57", "53", "40", "55"}:
            return False

    if isinstance(exc, DBAPIError) and getattr(exc, "connection_invalidated", False):
        return True

    # SQLAlchemy raises OperationalError / InterfaceError for driver-level connection loss that
    # may carry no SQLSTATE (the connect itself failed) -- fall back to the message.
    message = str(getattr(exc, "orig", exc) or exc)
    if isinstance(exc, (OperationalError, InterfaceError)) and _TRANSIENT_MESSAGE_RE.search(message):
        return True
    if _TRANSIENT_MESSAGE_RE.search(message) and state is None:
        return True
    return False


def _log_before_sleep(retry_state: RetryCallState) -> None:
    from app.db.database import db_connection_mode

    exc = retry_state.outcome.exception() if retry_state.outcome else None
    state = _sqlstate(exc) if exc else None
    attempts = get_settings().db_retry_attempts
    delay = getattr(retry_state.next_action, "sleep", 0.0)
    print(
        f"[db-retry] transient {type(exc).__name__ if exc else 'error'}"
        f"{f' (sqlstate={state})' if state else ''} on attempt {retry_state.attempt_number}/{attempts}; "
        f"retrying in {delay:.0f}s (connection_mode={db_connection_mode()})"
    )


def _retryer() -> Callable[..., Any]:
    settings = get_settings()
    return retry(
        retry=retry_if_exception(is_transient_db_error),
        wait=wait_exponential(multiplier=1, min=1, max=settings.db_retry_max_wait_seconds),
        stop=stop_after_attempt(settings.db_retry_attempts),
        reraise=True,
        before_sleep=_log_before_sleep,
    )


def db_retry(fn: Callable[..., T]) -> Callable[..., T]:
    """Decorator: retry ``fn`` on a transient DB connectivity error, exponential backoff, capped
    at ``settings.db_retry_attempts``. Non-transient errors propagate on the first try."""
    return _retryer()(fn)


def run_in_session(fn: Callable[[Any], T], *, description: str = "db operation") -> T:
    """Run ``fn(session)`` as a committed unit of work, retried on a transient connectivity blip
    with a **fresh** session each attempt (a failed transaction's writes cannot be replayed on
    the same connection). Idempotency across attempts is the caller's responsibility -- every PPI
    write path is keyed by ``run_key`` / a uniqueness constraint, so a redo updates in place.
    """
    from app.db.database import SessionLocal

    @_retryer()
    def _attempt() -> T:
        session = SessionLocal()
        try:
            result = fn(session)
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    try:
        return _attempt()
    except Exception as exc:  # attach the human label to the final re-raised error's context
        if hasattr(exc, "add_note"):
            exc.add_note(f"[db] failed operation: {description}")
        raise
