from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from depviz.api import Command, CommandRunner, OperationContext
from depviz.api.errors import BackendError, ToolUnavailable
from depviz.infrastructure import LocalCommandRunner
from depviz.infrastructure.tool_versions import extract_tool_version

DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_OUTPUT_LIMIT = 32 * 1024 * 1024
AUTO_TOOL_ORDER = ("mamba", "micromamba", "conda")


@dataclass(frozen=True)
class CondaToolSettings:
    tool: str
    executable: str
    timeout_seconds: float
    output_limit: int
    solver: str | None
    auto_selected: bool = False
    detected_version: str | None = None


def tool_settings(
    context: OperationContext,
    *,
    error: Callable[[str], BackendError],
) -> CondaToolSettings:
    requested = context.configuration.get("conda.tool", "auto").strip().lower()
    if requested not in {"auto", "conda", "mamba", "micromamba"}:
        raise error(
            f"Unsupported Conda tool {requested!r}; expected 'auto', 'mamba', "
            "'micromamba', or 'conda'"
        )

    timeout_seconds = _positive_float_setting(
        context.configuration,
        "conda.timeout_seconds",
        DEFAULT_TIMEOUT_SECONDS,
        error,
    )
    output_limit = _positive_int_setting(
        context.configuration,
        "conda.output_limit",
        DEFAULT_OUTPUT_LIMIT,
        error,
    )
    configured_executable = context.configuration.get("conda.executable")
    solver = context.configuration.get("conda.solver")
    detected_version: str | None = None

    if requested == "auto":
        if configured_executable:
            executable = configured_executable.strip()
            if not executable:
                raise error("The configured Conda executable is empty")
            tool = infer_conda_tool(executable)
            if tool is None:
                raise error(
                    f"Cannot infer the Conda tool from executable {executable!r}; "
                    "set --tool mamba, --tool micromamba, or --tool conda explicitly"
                )
        elif solver:
            # --solver selects Conda's solver plugin API, so an automatic
            # frontend selection must choose the conda executable.
            tool = "conda"
            executable = shutil.which("conda") or "conda"
        else:
            tool, executable, detected_version = _discover_conda_tool(
                context,
                timeout_seconds=timeout_seconds,
                output_limit=output_limit,
                error=error,
            )
        auto_selected = True
    else:
        tool = requested
        executable = (configured_executable or tool).strip()
        if not executable:
            raise error("The configured Conda executable is empty")
        auto_selected = False

    if solver and tool != "conda":
        raise error("The conda.solver option is only valid when the selected tool is 'conda'")
    return CondaToolSettings(
        tool=tool,
        executable=executable,
        timeout_seconds=timeout_seconds,
        output_limit=output_limit,
        solver=solver,
        auto_selected=auto_selected,
        detected_version=detected_version,
    )


def infer_conda_tool(executable: str) -> str | None:
    """Infer the Conda-family frontend from an executable path or command name."""

    name = Path(executable).name.lower()
    if "micromamba" in name:
        return "micromamba"
    if "mamba" in name:
        return "mamba"
    if "conda" in name:
        return "conda"
    return None


def mamba_uses_micromamba_cli(tool: str, tool_version: str) -> bool:
    """Return whether the selected frontend uses the Mamba 2/Micromamba CLI."""

    if tool == "micromamba":
        return True
    if tool != "mamba":
        return False
    major_text = tool_version.split(".", 1)[0]
    try:
        return int(major_text) >= 2
    except ValueError:
        # Unknown future Mamba banners should use the current Mamba interface,
        # which since Mamba 2 is shared with Micromamba.
        return True


def isolated_environment(
    tool: str,
    temporary_root: Path,
    empty_rc: Path,
) -> dict[str, str]:
    environment = {
        "CONDARC": str(empty_rc),
        "MAMBARC": str(empty_rc),
    }
    # Micromamba is standalone and needs its own isolated root prefix. Mamba
    # installed by Miniforge must remain attached to its real base prefix.
    if tool == "micromamba":
        environment["MAMBA_ROOT_PREFIX"] = str(temporary_root / "mamba-root")
    return environment


def read_tool_version(
    *,
    runner: CommandRunner,
    settings: CondaToolSettings,
    backend: str,
    operation: str,
    secrets: Sequence[str] = (),
) -> str:
    if settings.detected_version is not None:
        return settings.detected_version
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


def _discover_conda_tool(
    context: OperationContext,
    *,
    timeout_seconds: float,
    output_limit: int,
    error: Callable[[str], BackendError],
) -> tuple[str, str, str]:
    runner = context.command_runner or LocalCommandRunner()
    failures: list[str] = []
    for tool in AUTO_TOOL_ORDER:
        executable = shutil.which(tool) or tool
        candidate = CondaToolSettings(
            tool=tool,
            executable=executable,
            timeout_seconds=timeout_seconds,
            output_limit=output_limit,
            solver=None,
            auto_selected=True,
            detected_version=None,
        )
        try:
            version = read_tool_version(
                runner=runner,
                settings=candidate,
                backend="conda-tool",
                operation="discover",
            )
        except ToolUnavailable as exception:
            failures.append(f"{tool}: {exception.message}")
            continue
        return tool, executable, version
    detail = "; ".join(failures)
    raise error(
        "No usable Conda-family executable was found. Tried mamba, micromamba, and conda"
        + (f" ({detail})" if detail else "")
    )


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
