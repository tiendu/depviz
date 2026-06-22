from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from depviz.api import Command, CommandRunner, OperationContext
from depviz.api.errors import BackendError, ToolUnavailable
from depviz.infrastructure.tool_versions import extract_tool_version

DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_OUTPUT_LIMIT = 32 * 1024 * 1024


@dataclass(frozen=True)
class CondaToolSettings:
    tool: str
    executable: str
    timeout_seconds: float
    output_limit: int
    solver: str | None


def tool_settings(
    context: OperationContext,
    *,
    error: Callable[[str], BackendError],
) -> CondaToolSettings:
    tool = context.configuration.get("conda.tool", "micromamba").strip().lower()
    if tool not in {"conda", "micromamba"}:
        raise error(f"Unsupported Conda tool {tool!r}; expected 'conda' or 'micromamba'")
    executable = context.configuration.get("conda.executable", tool).strip()
    if not executable:
        raise error("The configured Conda executable is empty")
    solver = context.configuration.get("conda.solver")
    if solver and tool != "conda":
        raise error("The conda.solver option is only valid when conda.tool is 'conda'")
    return CondaToolSettings(
        tool=tool,
        executable=executable,
        timeout_seconds=_positive_float_setting(
            context.configuration,
            "conda.timeout_seconds",
            DEFAULT_TIMEOUT_SECONDS,
            error,
        ),
        output_limit=_positive_int_setting(
            context.configuration,
            "conda.output_limit",
            DEFAULT_OUTPUT_LIMIT,
            error,
        ),
        solver=solver,
    )


def isolated_environment(temporary_root: Path, empty_rc: Path) -> dict[str, str]:
    return {
        "CONDARC": str(empty_rc),
        "MAMBARC": str(empty_rc),
        "MAMBA_ROOT_PREFIX": str(temporary_root / "mamba-root"),
    }


def read_tool_version(
    *,
    runner: CommandRunner,
    settings: CondaToolSettings,
    backend: str,
    operation: str,
    secrets: Sequence[str] = (),
) -> str:
    try:
        result = runner.run(
            Command(argv=(settings.executable, "--version")),
            timeout_seconds=min(settings.timeout_seconds, 30.0),
            output_limit=min(settings.output_limit, 1024 * 1024),
            redact=tuple(secrets),
        )
    except FileNotFoundError as error:
        raise ToolUnavailable(
            backend=backend,
            operation=operation,
            message=f"Executable not found: {settings.executable}",
        ) from error
    except OSError as error:
        raise ToolUnavailable(
            backend=backend,
            operation=operation,
            message=f"Cannot execute {settings.executable!r}: {error}",
        ) from error
    if result.timed_out or result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise ToolUnavailable(
            backend=backend,
            operation=operation,
            message=f"Cannot determine {settings.executable!r} version: {detail}",
        )
    banner = result.stdout.strip() or result.stderr.strip()
    if not banner:
        raise ToolUnavailable(
            backend=backend,
            operation=operation,
            message=f"Cannot determine {settings.executable!r} version: empty version banner",
        )
    try:
        return extract_tool_version(banner)
    except ValueError as error:
        raise ToolUnavailable(
            backend=backend,
            operation=operation,
            message=f"Cannot parse {settings.executable!r} version banner {banner!r}",
        ) from error


def _positive_float_setting(
    configuration: Mapping[str, str],
    key: str,
    default: float,
    error: Callable[[str], BackendError],
) -> float:
    raw = configuration.get(key)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exception:
        raise error(f"Configuration {key!r} must be a number") from exception
    if value <= 0:
        raise error(f"Configuration {key!r} must be positive")
    return value


def _positive_int_setting(
    configuration: Mapping[str, str],
    key: str,
    default: int,
    error: Callable[[str], BackendError],
) -> int:
    raw = configuration.get(key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exception:
        raise error(f"Configuration {key!r} must be an integer") from exception
    if value < 1:
        raise error(f"Configuration {key!r} must be positive")
    return value
