from __future__ import annotations

import json
import os
import platform
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from depviz.api import Command, CommandResult, CommandRunner, OperationContext
from depviz.api.errors import BackendError, ToolUnavailable
from depviz.infrastructure import LocalCommandRunner
from depviz.infrastructure.tool_versions import extract_tool_version

_UV_ENVIRONMENT = (
    "UV_INDEX",
    "UV_DEFAULT_INDEX",
    "UV_INDEX_URL",
    "UV_EXTRA_INDEX_URL",
    "UV_FIND_LINKS",
    "UV_CONSTRAINT",
    "UV_OVERRIDE",
    "UV_EXCLUDE",
    "UV_PYTHON",
    "UV_PROJECT",
    "UV_CONFIG_FILE",
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
    "PIP_FIND_LINKS",
    "PIP_CONFIG_FILE",
    "PYTHONPATH",
    "PYTHONHOME",
)

_IDENTITY_SCRIPT = r"""
import json
import os
import platform
import sys
import sysconfig
print(json.dumps({
    "implementation": sys.implementation.name,
    "version": platform.python_version(),
    "major": sys.version_info.major,
    "minor": sys.version_info.minor,
    "platform": sysconfig.get_platform(),
    "soabi": sysconfig.get_config_var("SOABI") or "",
    "executable": sys.executable,
    "prefix": sys.prefix,
    "base_prefix": sys.base_prefix,
}))
""".strip()


@dataclass(frozen=True)
class UvSettings:
    executable: str
    interpreter: str
    timeout_seconds: float
    output_limit: int


@dataclass(frozen=True)
class PythonRuntime:
    implementation: str
    version: str
    major: int
    minor: int
    platform: str
    soabi: str
    executable: str
    prefix: str
    base_prefix: str

    @property
    def target_id(self) -> str:
        abi = self.soabi or "none"
        return f"python-{self.implementation}-{self.version}-{self.platform}-{abi}"

    @property
    def is_virtual_environment(self) -> bool:
        return Path(self.prefix).resolve() != Path(self.base_prefix).resolve()


def uv_settings(
    context: OperationContext,
    *,
    error: Callable[[str], BackendError],
) -> UvSettings:
    executable = context.configuration.get("python.uv_executable", "uv").strip()
    interpreter = context.configuration.get("python.interpreter", sys.executable).strip()
    try:
        timeout_seconds = float(context.configuration.get("python.timeout_seconds", "300"))
        output_limit = int(context.configuration.get("python.output_limit", str(32 * 1024 * 1024)))
    except ValueError as exc:
        raise error("Python backend timeout and output limit must be numeric") from exc
    if not executable:
        raise error("Python backend uv executable cannot be empty")
    if not interpreter:
        raise error("Python backend interpreter cannot be empty")
    if timeout_seconds <= 0:
        raise error("Python backend timeout must be positive")
    if output_limit < 1:
        raise error("Python backend output limit must be positive")
    return UvSettings(executable, interpreter, timeout_seconds, output_limit)


def runner_for(context: OperationContext) -> CommandRunner:
    return context.command_runner or LocalCommandRunner()


def isolated_uv_environment(*, cache_dir: Path | None = None) -> dict[str, str]:
    environment = {
        "UV_NO_CONFIG": "1",
        "UV_NO_PROGRESS": "1",
        "UV_PYTHON_DOWNLOADS": "never",
        "PYTHONNOUSERSITE": "1",
        "PIP_CONFIG_FILE": os.devnull,
    }
    if cache_dir is not None:
        environment["UV_CACHE_DIR"] = str(cache_dir)
    return environment


def uv_environment_to_remove() -> tuple[str, ...]:
    return _UV_ENVIRONMENT


def read_uv_version(
    *,
    runner: CommandRunner,
    settings: UvSettings,
    backend: str,
    operation: str,
) -> str:
    result = _run(
        runner=runner,
        command=Command(
            argv=(settings.executable, "--version"),
            environment=isolated_uv_environment(),
            remove_environment=uv_environment_to_remove(),
        ),
        settings=settings,
        backend=backend,
        operation=operation,
    )
    if result.returncode != 0:
        raise BackendError(
            backend=backend,
            operation=operation,
            message=result.stderr.strip() or result.stdout.strip() or "uv --version failed",
        )
    text = result.stdout.strip() or result.stderr.strip()
    if not text:
        raise BackendError(backend=backend, operation=operation, message="uv returned no version")
    try:
        return extract_tool_version(text)
    except ValueError as exc:
        raise BackendError(
            backend=backend,
            operation=operation,
            message=f"Cannot parse uv version banner {text!r}",
        ) from exc


def read_python_runtime(
    *,
    runner: CommandRunner,
    settings: UvSettings,
    backend: str,
    operation: str,
    interpreter: str | None = None,
) -> PythonRuntime:
    executable = interpreter or settings.interpreter
    result = _run(
        runner=runner,
        command=Command(
            argv=(executable, "-I", "-c", _IDENTITY_SCRIPT),
            environment={"PYTHONNOUSERSITE": "1"},
            remove_environment=("PYTHONPATH", "PYTHONHOME"),
        ),
        settings=settings,
        backend=backend,
        operation=operation,
    )
    if result.returncode != 0:
        raise BackendError(
            backend=backend,
            operation=operation,
            message=result.stderr.strip() or "Python interpreter identity query failed",
        )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BackendError(
            backend=backend,
            operation=operation,
            message=f"Python interpreter returned invalid identity JSON: {exc}",
        ) from exc
    if not isinstance(value, dict):
        raise BackendError(
            backend=backend,
            operation=operation,
            message="Python interpreter identity must be a JSON object",
        )
    try:
        return PythonRuntime(
            implementation=_required_string(value, "implementation"),
            version=_required_string(value, "version"),
            major=_required_int(value, "major"),
            minor=_required_int(value, "minor"),
            platform=_required_string(value, "platform"),
            soabi=_optional_string(value, "soabi"),
            executable=_required_string(value, "executable"),
            prefix=_required_string(value, "prefix"),
            base_prefix=_required_string(value, "base_prefix"),
        )
    except ValueError as exc:
        raise BackendError(backend=backend, operation=operation, message=str(exc)) from exc


def require_host_compatible_runtime(
    runtime: PythonRuntime, *, backend: str, operation: str
) -> None:
    current_platform = sysconfig.get_platform()
    current_soabi = str(sysconfig.get_config_var("SOABI") or "")
    if runtime.implementation != sys.implementation.name:
        raise BackendError(
            backend=backend,
            operation=operation,
            message=(
                f"Target interpreter implementation {runtime.implementation!r} does not match "
                f"the running depviz implementation {sys.implementation.name!r}"
            ),
        )
    if (runtime.major, runtime.minor) != (sys.version_info.major, sys.version_info.minor):
        raise BackendError(
            backend=backend,
            operation=operation,
            message=(
                f"Target interpreter Python {runtime.major}.{runtime.minor} does not match "
                f"the running depviz Python {sys.version_info.major}.{sys.version_info.minor}; "
                "cross-interpreter artifact selection is not supported yet"
            ),
        )
    if runtime.platform != current_platform or runtime.soabi != current_soabi:
        raise BackendError(
            backend=backend,
            operation=operation,
            message=(
                "Target interpreter platform or ABI does not match the running depviz process; "
                "refusing to guess wheel compatibility"
            ),
        )


def current_runtime_identity() -> tuple[str, str, str, str]:
    return (
        sys.implementation.name,
        platform.python_version(),
        sysconfig.get_platform(),
        str(sysconfig.get_config_var("SOABI") or ""),
    )


def _run(
    *,
    runner: CommandRunner,
    command: Command,
    settings: UvSettings,
    backend: str,
    operation: str,
) -> CommandResult:
    try:
        result = runner.run(
            command,
            timeout_seconds=settings.timeout_seconds,
            output_limit=settings.output_limit,
            redact=(settings.interpreter,),
        )
    except FileNotFoundError as exc:
        raise ToolUnavailable(
            backend=backend,
            operation=operation,
            message=f"Executable not found: {command.argv[0]}",
        ) from exc
    except OSError as exc:
        raise BackendError(
            backend=backend,
            operation=operation,
            message=f"Cannot execute {command.argv[0]!r}: {exc}",
        ) from exc
    if result.timed_out:
        raise BackendError(
            backend=backend,
            operation=operation,
            message=f"Command timed out after {settings.timeout_seconds:g} seconds",
        )
    if result.output_truncated:
        raise BackendError(
            backend=backend,
            operation=operation,
            message="Command output exceeded the configured limit",
        )
    return result


def _required_string(value: dict[object, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"Python runtime field {key!r} must be a non-empty string")
    return item


def _optional_string(value: dict[object, object], key: str) -> str:
    item = value.get(key, "")
    if not isinstance(item, str):
        raise ValueError(f"Python runtime field {key!r} must be a string")
    return item


def _required_int(value: dict[object, object], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int):
        raise ValueError(f"Python runtime field {key!r} must be an integer")
    return item
