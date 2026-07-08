from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol

from depviz.api.errors import ToolUnavailable


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


class RuntimeTools(Protocol):
    """Discover host executables needed by backend adapters.

    The application composition root provides this service. Backends depend on
    the structural contract rather than on ``shutil.which`` or frozen-runtime
    details directly.
    """

    def find_executable(self, *names: str) -> str | None: ...

    def default_python_interpreter(self) -> str | None: ...


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
    runtime_tools: RuntimeTools | None = None
    offline: bool = False
    working_directory: Path | None = None
    configuration: Mapping[str, str] = field(default_factory=dict)


def require_command_runner(
    context: OperationContext,
    *,
    backend: str,
    operation: str,
) -> CommandRunner:
    """Return the injected command runner or fail at the service boundary.

    Backends must not silently construct process-running infrastructure. The
    composition root owns concrete service selection; tests and embedders can
    provide any object satisfying :class:`CommandRunner`.
    """
    if context.command_runner is None:
        raise ToolUnavailable(
            backend=backend,
            operation=operation,
            message="No command runner was provided in OperationContext",
        )
    return context.command_runner
