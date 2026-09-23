"""Scrub secrets and personal data out of every log record (production-spec D).

Installed as a log-record factory, so it applies to records from every logger —
ours, uvicorn's, arq's, SQLAlchemy's — regardless of which handlers are attached
or when they were configured. What it removes:

* query-string credentials: the OAuth callback's ``code`` and ``state`` would
  otherwise land verbatim in uvicorn's access log on every connection
* ``Bearer`` tokens, ``session=`` cookie values and ``x-api-key`` values
* password-shaped JSON fields
* email addresses

Tracebacks are rendered and scrubbed up front (``exc_text``), since exception
messages are where stray values most often hide.
"""

from __future__ import annotations

import logging
import re
from typing import Any

REDACTED = "[redacted]"

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # key=value in query strings and form bodies
    (
        re.compile(
            r"(?i)\b(code|state|token|access_token|refresh_token|id_token|password|"
            r"invite_code|code_verifier|session)=([^&\s\"';,]+)"
        ),
        r"\1=" + REDACTED,
    ),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer " + REDACTED),
    (
        re.compile(r"(?i)(x-api-key[\"']?\s*[:=]\s*[\"']?)[^\"'\s,}]+"),
        r"\1" + REDACTED,
    ),
    # JSON-ish secrets: "password": "..."
    (
        re.compile(
            r"(?i)([\"']?(?:password|new_password|current_password|temporary_password|"
            r"access_token|refresh_token|invite_code|code)[\"']?\s*:\s*)[\"'][^\"']*[\"']"
        ),
        r'\1"' + REDACTED + '"',
    ),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[email]"),
]


def redact(text: str) -> str:
    """Return ``text`` with every known secret or address replaced."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


_installed = False


def install_log_redaction() -> None:
    """Wrap the log-record factory so every record is scrubbed at creation.

    Idempotent: the app factory and the worker both call it.
    """
    global _installed
    if _installed:
        return
    base_factory = logging.getLogRecordFactory()
    formatter = logging.Formatter()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = base_factory(*args, **kwargs)
        try:
            _scrub(record, formatter)
        except Exception:  # noqa: BLE001 - never let redaction break logging
            record.msg, record.args = "[log record withheld: redaction failed]", ()
        return record

    logging.setLogRecordFactory(factory)
    _installed = True


def _scrub(record: logging.LogRecord, formatter: logging.Formatter) -> None:
    args = record.args
    # uvicorn's AccessFormatter unpacks exactly five positional args
    # (client, method, path, http_version, status); keep that shape and scrub
    # only the path, which is where the query string lives.
    if (
        record.name == "uvicorn.access"
        and isinstance(args, tuple)
        and len(args) == 5
    ):
        client, method, path, version, status = args
        record.args = (client, method, redact(str(path)), version, status)
    else:
        message = record.getMessage()
        scrubbed = redact(message)
        if scrubbed != message or args:
            record.msg, record.args = scrubbed, ()

    if record.exc_info and not record.exc_text:
        record.exc_text = redact(formatter.formatException(record.exc_info))


def configure_app_logging() -> None:
    """Print the app's own INFO logs (the Etsy call log, generation traces).

    Neither uvicorn nor arq configures anything but its own loggers, so without
    this every ``app.*`` INFO line is dropped and warnings fall through to
    Python's bare last-resort handler. Records still propagate, so nothing is
    lost to handlers configured above. Redaction is applied when each record is
    created (:func:`install_log_redaction`).
    """
    app_logger = logging.getLogger("app")
    if any(getattr(h, "_listyro", False) for h in app_logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    handler._listyro = True  # type: ignore[attr-defined]
    app_logger.addHandler(handler)
    app_logger.setLevel(logging.INFO)
