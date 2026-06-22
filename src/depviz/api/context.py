from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol


@dataclass(frozen=True)
class Command:
    argv: tuple[str, ...]
    cwd: Path | None = None
    environment: Mapping[str, str] | None = None
    remove_environment: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    output_truncated: bool = False


class CommandRunner(Protocol):
    def run(
        self,
        command: Command,
        *,
        timeout_seconds: float,
        output_limit: int,
        redact: tuple[str, ...] = (),
    ) -> CommandResult: ...


@dataclass(frozen=True)
class OperationContext:
    command_runner: CommandRunner | None = None
    offline: bool = False
    working_directory: Path | None = None
    configuration: Mapping[str, str] = field(default_factory=dict)
