from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from depviz.api import (
    ApplyResult,
    CandidateEnvironment,
    Command,
    Diagnostic,
    EnvironmentTarget,
    LockedResolution,
    OperationContext,
    Severity,
)
from depviz.api.errors import ApplyFailed, BackendError, ToolUnavailable
from depviz.builtin.conda.driver import CondaPrefixDriver
from depviz.builtin.mixed.locking import mixed_lock_layers
from depviz.builtin.python.locking import interpreter_metadata, locked_artifacts
from depviz.builtin.python.prefix import python_executable
from depviz.builtin.python.tooling import (
    isolated_uv_environment,
    read_python_runtime,
    read_uv_version,
    runner_for,
    uv_environment_to_remove,
    uv_settings,
)
from depviz.infrastructure.deployment import ManagedDeploymentStore


class CondaPipPrefixDriver:
    name = "conda-pip-prefix-driver"
    environment_kind = "conda-pip-prefix"
    deployment_kind = "managed-conda-pip-deployment"

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
        _validate_candidate(candidate)
        lock_id = lock.artifact.metadata.get("lock_id")
        if not lock_id:
            raise ApplyFailed(
                backend=self.name,
                operation="apply",
                message="Exact mixed lock has no lock_id",
            )
        conda_lock, python_lock = mixed_lock_layers(lock, context)
        conda_candidate = CandidateEnvironment(
            target=EnvironmentTarget(candidate.target.path, "managed-conda-deployment"),
            candidate_id=candidate.candidate_id,
            path=candidate.path,
            kind="conda-prefix",
        )
        conda_result = CondaPrefixDriver().apply(conda_lock, conda_candidate, context)
        python_diagnostics = _install_python_overlay(python_lock, candidate, context)
        return ApplyResult(
            changed=conda_result.changed or bool(python_lock.resolution.packages),
            diagnostics=(*conda_result.diagnostics, *python_diagnostics),
            candidate=candidate,
            lock_id=lock_id,
        )

    def discard(
        self,
        candidate: CandidateEnvironment,
        context: OperationContext,
    ) -> None:
        del context
        _validate_candidate(candidate)
        if candidate.path.is_symlink():
            raise ApplyFailed(
                backend=self.name,
                operation="discard",
                message=f"Refusing to remove symlink candidate: {candidate.path}",
            )
        shutil.rmtree(candidate.path, ignore_errors=False)


def _install_python_overlay(
    lock: LockedResolution,
    candidate: CandidateEnvironment,
    context: OperationContext,
) -> tuple[Diagnostic, ...]:
    settings = uv_settings(
        context,
        error=lambda message: ApplyFailed(
            backend="conda-pip-prefix-driver",
            operation="apply Python overlay",
            message=message,
        ),
    )
    runner = runner_for(context)
    interpreter = python_executable(candidate.path)
    if not interpreter.is_file():
        raise ApplyFailed(
            backend="conda-pip-prefix-driver",
            operation="apply Python overlay",
            message=f"Conda candidate contains no Python interpreter: {interpreter}",
        )
    try:
        runtime = read_python_runtime(
            runner=runner,
            settings=settings,
            backend="conda-pip-prefix-driver",
            operation="apply Python overlay",
            interpreter=str(interpreter),
        )
        uv_version = read_uv_version(
            runner=runner,
            settings=settings,
            backend="conda-pip-prefix-driver",
            operation="apply Python overlay",
        )
    except ToolUnavailable:
        raise
    except BackendError as exc:
        raise ApplyFailed(
            backend="conda-pip-prefix-driver",
            operation="apply Python overlay",
            message=exc.message,
            diagnostics=exc.diagnostics,
        ) from exc
    expected = interpreter_metadata(lock)
    if runtime.implementation != expected.get("implementation"):
        raise ApplyFailed(
            backend="conda-pip-prefix-driver",
            operation="apply Python overlay",
            message="Candidate Python implementation does not match the mixed lock",
        )
    if runtime.version != expected.get("version"):
        raise ApplyFailed(
            backend="conda-pip-prefix-driver",
            operation="apply Python overlay",
            message=(
                f"Candidate Python {runtime.version} does not match locked Python "
                f"{expected.get('version')}"
            ),
        )
    artifacts = locked_artifacts(lock)
    if not artifacts:
        raise ApplyFailed(
            backend="conda-pip-prefix-driver",
            operation="apply Python overlay",
            message="Mixed lock contains no Python wheel artifacts",
        )

    with tempfile.TemporaryDirectory(prefix="depviz-conda-pip-apply-") as temporary_directory:
        root = Path(temporary_directory)
        requirements = root / "requirements.txt"
        requirements.write_text(
            "\n".join(artifact.requirement_line for artifact in artifacts) + "\n",
            encoding="utf-8",
        )
        requirements.chmod(0o600)
        command = Command(
            argv=_install_command(
                settings.executable,
                interpreter,
                requirements,
                offline=context.offline,
            ),
            cwd=context.working_directory or candidate.path,
            environment={
                **isolated_uv_environment(cache_dir=root / "uv-cache"),
                "PYTHONNOUSERSITE": "1",
                "PATH": os.pathsep.join([str(interpreter.parent), os.environ.get("PATH", "")]),
            },
            remove_environment=uv_environment_to_remove(),
        )
        try:
            result = runner.run(
                command,
                timeout_seconds=settings.timeout_seconds,
                output_limit=settings.output_limit,
                redact=(str(root), str(requirements)),
            )
        except FileNotFoundError as exc:
            raise ToolUnavailable(
                backend="conda-pip-prefix-driver",
                operation="apply Python overlay",
                message=f"Executable not found: {settings.executable}",
            ) from exc
        except OSError as exc:
            raise ApplyFailed(
                backend="conda-pip-prefix-driver",
                operation="apply Python overlay",
                message=f"Cannot execute {settings.executable!r}: {exc}",
            ) from exc
    if result.timed_out:
        raise ApplyFailed(
            backend="conda-pip-prefix-driver",
            operation="apply Python overlay",
            message=f"Python wheel installation timed out after {settings.timeout_seconds:g} seconds",
        )
    if result.output_truncated:
        raise ApplyFailed(
            backend="conda-pip-prefix-driver",
            operation="apply Python overlay",
            message="Python installer output exceeded the configured limit",
        )
    if result.returncode != 0:
        raise ApplyFailed(
            backend="conda-pip-prefix-driver",
            operation="apply Python overlay",
            message=result.stderr.strip() or result.stdout.strip() or "uv pip install failed",
        )
    return (
        Diagnostic(
            code="apply.conda-pip.exact-overlay",
            message=(
                f"uv {uv_version} installed {len(artifacts)} exact locked wheels into the "
                "isolated Conda candidate without dependency resolution"
            ),
            severity=Severity.INFO,
        ),
    )


def _install_command(
    executable: str,
    interpreter: Path,
    requirements: Path,
    *,
    offline: bool,
) -> tuple[str, ...]:
    arguments = [
        executable,
        "pip",
        "install",
        "--python",
        str(interpreter),
        "--requirement",
        str(requirements),
        "--no-deps",
        "--require-hashes",
        "--no-index",
        "--no-build",
        "--reinstall",
        "--no-config",
        "--no-progress",
        "--no-python-downloads",
    ]
    if offline:
        arguments.append("--offline")
    return tuple(arguments)


def _validate_candidate(candidate: CandidateEnvironment) -> None:
    if candidate.target.kind != "managed-conda-pip-deployment":
        raise ApplyFailed(
            backend="conda-pip-prefix-driver",
            operation="apply",
            message=f"Unsupported candidate target kind {candidate.target.kind!r}",
        )
    if candidate.kind != "conda-pip-prefix":
        raise ApplyFailed(
            backend="conda-pip-prefix-driver",
            operation="apply",
            message=f"Unsupported candidate environment kind {candidate.kind!r}",
        )
    store = ManagedDeploymentStore(candidate.target.path)
    expected = store.environments_dir / candidate.candidate_id
    if candidate.path.resolve() != expected.resolve():
        raise ApplyFailed(
            backend="conda-pip-prefix-driver",
            operation="apply",
            message="Candidate path escapes its managed deployment",
        )
    if not candidate.path.is_dir() or candidate.path.is_symlink():
        raise ApplyFailed(
            backend="conda-pip-prefix-driver",
            operation="apply",
            message=f"Candidate path is missing or invalid: {candidate.path}",
        )
