from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from depviz.api import (
    ApplyResult,
    CandidateEnvironment,
    Command,
    CommandRunner,
    Diagnostic,
    EnvironmentTarget,
    LockedResolution,
    OperationContext,
    Severity,
)
from depviz.api.errors import ApplyFailed, BackendError, ToolUnavailable
from depviz.builtin.python.locking import interpreter_metadata, locked_artifacts
from depviz.builtin.python.tooling import (
    PythonRuntime,
    UvSettings,
    isolated_uv_environment,
    read_python_runtime,
    read_uv_version,
    runner_for,
    uv_environment_to_remove,
    uv_settings,
)
from depviz.infrastructure.deployment import ManagedDeploymentStore


class PythonVenvDriver:
    name = "python-venv-driver"
    environment_kind = "python-venv"
    deployment_kind = "managed-python-deployment"

    def create_candidate(
        self,
        target: EnvironmentTarget,
        context: OperationContext,
    ) -> CandidateEnvironment:
        del context
        if target.kind != self.deployment_kind:
            raise ApplyFailed(
                backend=self.name,
                operation="create candidate",
                message=f"Unsupported deployment target kind {target.kind!r}",
            )
        try:
            return ManagedDeploymentStore(target.path).reserve_candidate(
                kind=self.environment_kind,
                deployment_kind=self.deployment_kind,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            raise ApplyFailed(
                backend=self.name,
                operation="create candidate",
                message=str(exc),
            ) from exc

    def apply(
        self,
        lock: LockedResolution,
        candidate: CandidateEnvironment,
        context: OperationContext,
    ) -> ApplyResult:
        lock_id = lock.artifact.metadata.get("lock_id")
        if not lock_id:
            raise ApplyFailed(
                backend=self.name,
                operation="apply",
                message="Exact Python lock has no lock_id",
            )
        _validate_candidate(candidate)
        settings = uv_settings(context, error=_apply_error)
        runner = runner_for(context)
        try:
            base_runtime = read_python_runtime(
                runner=runner,
                settings=settings,
                backend=self.name,
                operation="apply",
            )
            version = read_uv_version(
                runner=runner,
                settings=settings,
                backend=self.name,
                operation="apply",
            )
        except ToolUnavailable:
            raise
        except BackendError as exc:
            raise _as_apply_failed(exc) from exc
        _validate_interpreter_lock(interpreter_metadata(lock), base_runtime)
        artifacts = locked_artifacts(lock)
        if not artifacts:
            raise ApplyFailed(
                backend=self.name,
                operation="apply",
                message="Exact Python lock contains no wheel artifacts",
            )
        if any(item.platform != lock.resolution.target.platform for item in artifacts):
            raise ApplyFailed(
                backend=self.name,
                operation="apply",
                message="Python lock contains artifacts for an incompatible target",
            )

        with tempfile.TemporaryDirectory(prefix="depviz-uv-apply-") as temporary_directory:
            root = Path(temporary_directory)
            cache = root / "cache"
            requirements = root / "requirements.txt"
            requirements.write_text(
                "\n".join(item.requirement_line for item in artifacts) + "\n",
                encoding="utf-8",
            )
            requirements.chmod(0o600)
            environment = isolated_uv_environment(cache_dir=cache)
            venv_command = Command(
                argv=(
                    settings.executable,
                    "venv",
                    "--python",
                    settings.interpreter,
                    "--no-config",
                    "--no-project",
                    "--no-python-downloads",
                    "--allow-existing",
                    str(candidate.path),
                ),
                cwd=context.working_directory,
                environment=environment,
                remove_environment=uv_environment_to_remove(),
            )
            _run_apply_command(
                runner=runner,
                command=venv_command,
                settings=settings,
                operation="create virtual environment",
                redact=(str(root),),
            )
            interpreter = _venv_python(candidate.path)
            sync_arguments = [
                settings.executable,
                "pip",
                "sync",
                str(requirements),
                "--python",
                str(interpreter),
                "--require-hashes",
                "--no-index",
                "--no-config",
                "--no-progress",
                "--no-python-downloads",
            ]
            if context.offline:
                sync_arguments.append("--offline")
            sync_command = Command(
                argv=tuple(sync_arguments),
                cwd=context.working_directory,
                environment=environment,
                remove_environment=uv_environment_to_remove(),
            )
            _run_apply_command(
                runner=runner,
                command=sync_command,
                settings=settings,
                operation="sync exact wheels",
                redact=(str(root), str(requirements)),
            )

        if not _venv_python(candidate.path).is_file():
            raise ApplyFailed(
                backend=self.name,
                operation="apply",
                message="uv reported success but produced no candidate interpreter",
            )
        return ApplyResult(
            changed=True,
            candidate=candidate,
            lock_id=lock_id,
            diagnostics=(
                Diagnostic(
                    code="apply.python.exact",
                    message=(
                        f"uv {version} installed {len(artifacts)} exact hashed wheels into an "
                        "isolated virtual environment without consulting a package index"
                    ),
                    severity=Severity.INFO,
                ),
            ),
        )

    def discard(self, candidate: CandidateEnvironment, context: OperationContext) -> None:
        del context
        _validate_candidate(candidate)
        if candidate.path.is_symlink():
            raise ApplyFailed(
                backend=self.name,
                operation="discard",
                message=f"Refusing to remove symlink candidate: {candidate.path}",
            )
        shutil.rmtree(candidate.path, ignore_errors=False)


def _run_apply_command(
    *,
    runner: CommandRunner,
    command: Command,
    settings: UvSettings,
    operation: str,
    redact: tuple[str, ...],
) -> None:
    try:
        result = runner.run(
            command,
            timeout_seconds=settings.timeout_seconds,
            output_limit=settings.output_limit,
            redact=redact,
        )
    except FileNotFoundError as exc:
        raise ToolUnavailable(
            backend="python-venv-driver",
            operation=operation,
            message=f"Executable not found: {command.argv[0]}",
        ) from exc
    except OSError as exc:
        raise ApplyFailed(
            backend="python-venv-driver",
            operation=operation,
            message=f"Cannot execute {command.argv[0]!r}: {exc}",
        ) from exc
    if result.timed_out:
        raise ApplyFailed(
            backend="python-venv-driver",
            operation=operation,
            message=f"Command timed out after {settings.timeout_seconds:g} seconds",
        )
    if result.output_truncated:
        raise ApplyFailed(
            backend="python-venv-driver",
            operation=operation,
            message="Installer output exceeded the configured limit",
        )
    if result.returncode != 0:
        raise ApplyFailed(
            backend="python-venv-driver",
            operation=operation,
            message=result.stderr.strip() or result.stdout.strip() or f"{operation} failed",
        )


def _validate_interpreter_lock(expected: dict[str, object], actual: PythonRuntime) -> None:
    comparisons = {
        "implementation": actual.implementation,
        "version": actual.version,
        "major": actual.major,
        "minor": actual.minor,
        "platform": actual.platform,
        "soabi": actual.soabi,
    }
    differences = [
        f"{key}: lock={expected.get(key)!r}, interpreter={value!r}"
        for key, value in comparisons.items()
        if expected.get(key) != value
    ]
    if differences:
        raise ApplyFailed(
            backend="python-venv-driver",
            operation="apply",
            message="Selected Python interpreter does not match the exact lock: "
            + "; ".join(differences),
        )


def _validate_candidate(candidate: CandidateEnvironment) -> None:
    if candidate.target.kind != "managed-python-deployment" or candidate.kind != "python-venv":
        raise ApplyFailed(
            backend="python-venv-driver",
            operation="apply",
            message=(f"Unsupported candidate target {candidate.target.kind!r}/{candidate.kind!r}"),
        )
    store = ManagedDeploymentStore(candidate.target.path)
    expected = store.environments_dir / candidate.candidate_id
    if candidate.path.resolve() != expected.resolve():
        raise ApplyFailed(
            backend="python-venv-driver",
            operation="apply",
            message="Candidate path escapes its managed deployment",
        )
    if not candidate.path.is_dir() or candidate.path.is_symlink():
        raise ApplyFailed(
            backend="python-venv-driver",
            operation="apply",
            message=f"Candidate path is missing or invalid: {candidate.path}",
        )


def _venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _apply_error(message: str) -> ApplyFailed:
    return ApplyFailed(backend="python-venv-driver", operation="apply", message=message)


def _as_apply_failed(error: BackendError) -> ApplyFailed:
    return ApplyFailed(
        backend=error.backend,
        operation=error.operation,
        message=error.message,
        diagnostics=error.diagnostics,
    )
