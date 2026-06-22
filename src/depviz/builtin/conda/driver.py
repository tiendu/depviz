from __future__ import annotations

import json
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
from depviz.api.errors import ApplyFailed, ToolUnavailable
from depviz.builtin.conda.locking import locked_artifacts
from depviz.builtin.conda.tooling import isolated_environment, read_tool_version, tool_settings
from depviz.core.resolution import host_conda_platform
from depviz.infrastructure import LocalCommandRunner
from depviz.infrastructure.deployment import ManagedDeploymentStore


class CondaPrefixDriver:
    """Create isolated Conda candidates and install only exact locked artifacts."""

    name = "conda-prefix-driver"
    environment_kind = "conda-prefix"
    deployment_kind = "managed-conda-deployment"

    def create_candidate(
        self,
        target: EnvironmentTarget,
        context: OperationContext,
    ) -> CandidateEnvironment:
        del context
        if target.kind != "managed-conda-deployment":
            raise ApplyFailed(
                backend=self.name,
                operation="create candidate",
                message=f"Unsupported deployment target kind {target.kind!r}",
            )
        try:
            return ManagedDeploymentStore(target.path).reserve_candidate(
                kind=self.environment_kind, deployment_kind=self.deployment_kind
            )
        except (OSError, ValueError, RuntimeError) as error:
            raise ApplyFailed(
                backend=self.name,
                operation="create candidate",
                message=str(error),
            ) from error

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
                message="Exact lock has no lock_id",
            )
        _validate_candidate(candidate)
        expected_platform = lock.resolution.target.platform
        try:
            host_platform = host_conda_platform()
        except RuntimeError as error:
            raise ApplyFailed(
                backend=self.name,
                operation="apply",
                message=str(error),
            ) from error
        if expected_platform != host_platform:
            raise ApplyFailed(
                backend=self.name,
                operation="apply",
                message=(
                    f"Lock targets {expected_platform}, but apply host is {host_platform}; "
                    "cross-platform installation is not supported"
                ),
            )

        settings = tool_settings(context, error=_apply_configuration_error)
        runner = context.command_runner or LocalCommandRunner()
        tool_version = read_tool_version(
            runner=runner,
            settings=settings,
            backend=self.name,
            operation="apply",
        )
        artifacts = locked_artifacts(lock)
        if not artifacts:
            raise ApplyFailed(
                backend=self.name,
                operation="apply",
                message="Exact lock contains no artifacts",
            )
        if any(item.platform not in {expected_platform, "noarch"} for item in artifacts):
            raise ApplyFailed(
                backend=self.name,
                operation="apply",
                message="Lock contains artifacts for an incompatible platform",
            )

        with tempfile.TemporaryDirectory(prefix="depviz-conda-apply-") as temporary_directory:
            temporary_root = Path(temporary_directory)
            empty_rc = temporary_root / "empty-condarc.yml"
            empty_rc.write_text("{}\n", encoding="utf-8")
            explicit_file = temporary_root / "explicit.txt"
            explicit_file.write_text(
                "@EXPLICIT\n" + "\n".join(item.explicit_spec for item in artifacts) + "\n",
                encoding="utf-8",
            )
            explicit_file.chmod(0o600)
            command = Command(
                argv=_build_apply_command(
                    tool=settings.tool,
                    executable=settings.executable,
                    prefix=candidate.path,
                    explicit_file=explicit_file,
                    offline=context.offline,
                ),
                cwd=context.working_directory,
                environment=isolated_environment(settings.tool, temporary_root, empty_rc),
            )
            try:
                result = runner.run(
                    command,
                    timeout_seconds=settings.timeout_seconds,
                    output_limit=settings.output_limit,
                    redact=(str(temporary_root), str(explicit_file)),
                )
            except FileNotFoundError as error:
                raise ToolUnavailable(
                    backend=self.name,
                    operation="apply",
                    message=f"Executable not found: {settings.executable}",
                ) from error
            except OSError as error:
                raise ApplyFailed(
                    backend=self.name,
                    operation="apply",
                    message=f"Cannot execute {settings.executable!r}: {error}",
                ) from error

        if result.timed_out:
            raise ApplyFailed(
                backend=self.name,
                operation="apply",
                message=f"Exact installation timed out after {settings.timeout_seconds:g} seconds",
            )
        if result.output_truncated:
            raise ApplyFailed(
                backend=self.name,
                operation="apply",
                message="Installer output exceeded the configured limit",
            )
        success, detail = _installer_result(result.stdout, result.stderr, result.returncode)
        if not success:
            raise ApplyFailed(
                backend=self.name,
                operation="apply",
                message=detail,
            )
        if not (candidate.path / "conda-meta").is_dir():
            raise ApplyFailed(
                backend=self.name,
                operation="apply",
                message="Installer reported success but produced no Conda metadata",
            )
        return ApplyResult(
            changed=True,
            candidate=candidate,
            lock_id=lock_id,
            diagnostics=(
                Diagnostic(
                    code="apply.conda.exact",
                    message=(
                        f"{settings.tool} {tool_version} installed {len(artifacts)} exact "
                        "locked artifacts into an isolated candidate"
                    ),
                    severity=Severity.INFO,
                ),
            ),
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


def _build_apply_command(
    *,
    tool: str,
    executable: str,
    prefix: Path,
    explicit_file: Path,
    offline: bool,
) -> tuple[str, ...]:
    arguments = [
        executable,
        "create",
        "--yes",
        "--prefix",
        str(prefix),
        "--file",
        str(explicit_file),
        "--json",
    ]
    if tool in {"conda", "mamba"}:
        arguments.extend(["--no-default-packages", "--no-pin"])
    if offline:
        arguments.append("--offline")
    return tuple(arguments)


def _validate_candidate(candidate: CandidateEnvironment) -> None:
    if candidate.target.kind != "managed-conda-deployment":
        raise ApplyFailed(
            backend="conda-prefix-driver",
            operation="apply",
            message=f"Unsupported candidate target kind {candidate.target.kind!r}",
        )
    store = ManagedDeploymentStore(candidate.target.path)
    expected = store.environments_dir / candidate.candidate_id
    if candidate.path.resolve() != expected.resolve():
        raise ApplyFailed(
            backend="conda-prefix-driver",
            operation="apply",
            message="Candidate path escapes its managed deployment",
        )
    if not candidate.path.is_dir() or candidate.path.is_symlink():
        raise ApplyFailed(
            backend="conda-prefix-driver",
            operation="apply",
            message=f"Candidate path is missing or invalid: {candidate.path}",
        )


def _apply_configuration_error(message: str) -> ApplyFailed:
    return ApplyFailed(backend="conda-prefix-driver", operation="apply", message=message)


def _installer_result(stdout: str, stderr: str, returncode: int) -> tuple[bool, str]:
    text = stdout.strip()
    if text:
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return False, "Installer returned invalid JSON despite --json"
        if not isinstance(value, dict):
            return False, "Installer JSON root is not an object"
        if returncode == 0 and value.get("success") is True:
            return True, "success"
        for key in ("error", "message", "exception_name", "exception_type"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return False, item.strip()
    detail = stderr.strip()
    if detail:
        return False, detail
    return False, f"Installer exited with code {returncode}"
