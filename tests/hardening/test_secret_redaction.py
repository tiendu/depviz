from __future__ import annotations

import json
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path

import pytest

from depviz.api import (
    Command,
    CommandResult,
    DependencyIntent,
    OperationContext,
    Requirement,
    Target,
)
from depviz.api.errors import ResolutionFailed
from depviz.builtin.python import UvResolver
from depviz.infrastructure.redaction import (
    credential_secrets,
    redact_text,
    redact_url,
    sanitize_json,
)

pytestmark = pytest.mark.hardening


@dataclass
class LeakyRunner:
    secret: str

    def run(
        self,
        command: Command,
        *,
        timeout_seconds: float,
        output_limit: int,
        redact: tuple[str, ...] = (),
    ) -> CommandResult:
        del timeout_seconds, output_limit, redact
        if command.argv[0] == sys.executable:
            payload = {
                "implementation": sys.implementation.name,
                "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                "major": sys.version_info.major,
                "minor": sys.version_info.minor,
                "platform": sysconfig.get_platform(),
                "soabi": str(sysconfig.get_config_var("SOABI") or ""),
                "executable": sys.executable,
                "prefix": sys.prefix,
                "base_prefix": sys.base_prefix,
            }
            return CommandResult(command.argv, 0, json.dumps(payload), "", 0.0)
        if command.argv[:2] == ("uv", "--version"):
            return CommandResult(command.argv, 0, "uv 0.10.0\n", "", 0.0)
        return CommandResult(
            command.argv,
            1,
            "",
            f"registry rejected token {self.secret}",
            0.0,
        )


def test_generic_redaction_handles_credentials_and_nested_json() -> None:
    url = "https://alice:super-secret@example.invalid/simple"
    secrets = credential_secrets((url,))
    assert secrets == ("alice", "super-secret")
    assert "alice" not in redact_url(url, secrets)
    assert "super-secret" not in redact_url(url, secrets)
    assert sanitize_json({"nested": [url]}, secrets) == {
        "nested": ["https://example.invalid/simple"]
    }
    assert redact_text("token=super-secret", secrets) == "token=***"


def test_python_resolver_redacts_secret_even_when_runner_does_not(tmp_path: Path) -> None:
    secret = "index-password"
    context = OperationContext(
        command_runner=LeakyRunner(secret),
        working_directory=tmp_path,
        configuration={
            "python.interpreter": sys.executable,
            "python.uv_executable": "uv",
        },
    )
    intent = DependencyIntent(
        requirements=(Requirement("pypi", "demo", specifier="==1.0"),),
        indexes=(f"https://user:{secret}@example.invalid/simple",),
    )

    with pytest.raises(ResolutionFailed) as captured:
        UvResolver().resolve(intent, Target("python-host"), None, context)

    assert secret not in str(captured.value)
    assert "***" in str(captured.value)
