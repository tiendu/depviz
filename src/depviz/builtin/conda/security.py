from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from depviz.api import Requirement
from depviz.infrastructure.redaction import (
    credential_secrets,
    redact_text,
    redact_url,
    sanitize_json,
)


def sanitize_requirement(requirement: Requirement, secrets: Sequence[str]) -> Requirement:
    if requirement.source is None:
        return requirement
    return replace(requirement, source=redact_url(requirement.source, secrets))


__all__ = [
    "credential_secrets",
    "redact_text",
    "redact_url",
    "sanitize_json",
    "sanitize_requirement",
]
