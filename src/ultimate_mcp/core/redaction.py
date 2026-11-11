"""Secret redaction and structured logging helpers.

All diagnostics and logs MUST go to ``stderr`` or a configured log file so ``stdout``
remains strictly reserved for the MCP stdio JSON-RPC stream.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REDACTED_PLACEHOLDER = "[REDACTED]"
MIN_SECRET_LENGTH = 6

SENSITIVE_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"api[_-]?key", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"token", re.IGNORECASE),
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"passwd", re.IGNORECASE),
    re.compile(r"authorization", re.IGNORECASE),
    re.compile(r"cookie", re.IGNORECASE),
    re.compile(r"credential", re.IGNORECASE),
    re.compile(r"private[_-]?key", re.IGNORECASE),
)

TOKEN_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Bearer\s+[A-Za-z0-9._\-+/=]{8,}", re.IGNORECASE),
    re.compile(r"tvly-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
)


def is_sensitive_key(key: str) -> bool:
    """Return True if a mapping/parameter key looks like a credential or secret name."""
    return any(pattern.search(key) for pattern in SENSITIVE_KEY_PATTERNS)


def redact_text(text: str, known_secrets: Iterable[str] = ()) -> str:
    """Redact known secret values and common token formats from a string."""
    if not text:
        return text
    cleaned = text
    for secret in known_secrets:
        if secret and len(secret) >= MIN_SECRET_LENGTH and secret in cleaned:
            cleaned = cleaned.replace(secret, REDACTED_PLACEHOLDER)
    for pattern in TOKEN_VALUE_PATTERNS:
        cleaned = pattern.sub(REDACTED_PLACEHOLDER, cleaned)
    return cleaned


def redact_value(value: Any, known_secrets: Iterable[str] = ()) -> Any:
    """Recursively redact sensitive keys and known secret strings in nested structures."""
    secrets_tuple = tuple(s for s in known_secrets if s and len(s) >= MIN_SECRET_LENGTH)
    if isinstance(value, str):
        return redact_text(value, secrets_tuple)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for k, v in value.items():
            key_str = str(k)
            if is_sensitive_key(key_str) and v not in (None, "", False):
                result[key_str] = REDACTED_PLACEHOLDER
            else:
                result[key_str] = redact_value(v, secrets_tuple)
        return result
    if isinstance(value, list | tuple):
        return [redact_value(item, secrets_tuple) for item in value]
    return value


class StructuredLogFormatter(logging.Formatter):
    """Compact JSON-line formatter that redacts secrets before emitting."""

    def __init__(self, known_secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = tuple(s for s in known_secrets if s and len(s) >= MIN_SECRET_LENGTH)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact_text(record.getMessage(), self._secrets),
        }
        for attr in ("request_id", "tool", "duration_ms", "status", "error_code", "approval_id"):
            val = getattr(record, attr, None)
            if val is not None:
                payload[attr] = redact_value(val, self._secrets)
        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
            payload["exc"] = redact_text(exc_text, self._secrets)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(
    level: str = "INFO",
    log_file: Path | None = None,
    known_secrets: Iterable[str] = (),
) -> logging.Logger:
    """Configure the ``ultimate_mcp`` logger to write to stderr and optional file only."""
    logger = logging.getLogger("ultimate_mcp")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    logger.handlers.clear()

    formatter = StructuredLogFormatter(known_secrets=known_secrets)

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setFormatter(formatter)
    logger.addHandler(stderr_handler)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
