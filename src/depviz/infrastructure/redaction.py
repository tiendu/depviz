from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlsplit, urlunsplit


def credential_secrets(values: Sequence[str]) -> tuple[str, ...]:
    """Extract URL credentials for redaction without persisting the source URLs."""
    secrets: list[str] = []
    for value in values:
        parsed = urlsplit(value)
        if parsed.username:
            secrets.append(parsed.username)
        if parsed.password:
            secrets.append(parsed.password)
    return tuple(dict.fromkeys(secret for secret in secrets if secret))


def redact_text(value: str, secrets: Sequence[str]) -> str:
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "***")
    return redacted


def redact_url(value: str, secrets: Sequence[str]) -> str:
    redacted = redact_text(value, secrets)
    parsed = urlsplit(redacted)
    if parsed.username is None and parsed.password is None:
        return redacted
    hostname = parsed.hostname or ""
    if parsed.port is not None:
        hostname = f"{hostname}:{parsed.port}"
    return urlunsplit((parsed.scheme, hostname, parsed.path, parsed.query, parsed.fragment))


def sanitize_json(value: object, secrets: Sequence[str]) -> object:
    """Recursively redact known secret values from JSON-compatible data."""
    if isinstance(value, str):
        return redact_url(value, secrets)
    if isinstance(value, list):
        return [sanitize_json(item, secrets) for item in value]
    if isinstance(value, dict):
        return {str(key): sanitize_json(item, secrets) for key, item in value.items()}
    return value
