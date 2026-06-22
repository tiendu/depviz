from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path

from depviz.api import (
    CandidateEnvironment,
    Command,
    Diagnostic,
    EnvironmentTarget,
    LockedResolution,
    OperationContext,
    ResolvedPackage,
    Severity,
    VerificationPolicy,
    VerificationReport,
)
from depviz.api.errors import InspectionFailed, VerificationFailed
from depviz.builtin.python.inspector import PythonVenvInspector
from depviz.builtin.python.tooling import runner_for, uv_settings
from depviz.core.resolution import digest_json


class PythonVenvVerifier:
    name = "python-venv-verifier"
    environment_kind = "python-venv"
    deployment_kind = "managed-python-deployment"

    def verify(
        self,
        expected: LockedResolution,
        environment: CandidateEnvironment,
        policy: VerificationPolicy,
        context: OperationContext,
    ) -> VerificationReport:
        if environment.target.kind not in {"managed-python-deployment", "managed-deployment"}:
            raise VerificationFailed(
                backend=self.name,
                operation="verify",
                message=f"Unsupported candidate target kind {environment.target.kind!r}",
            )
        if environment.kind != "python-venv":
            raise VerificationFailed(
                backend=self.name,
                operation="verify",
                message=f"Unsupported candidate environment kind {environment.kind!r}",
            )
        expected_digest = _state_digest(
            expected.resolution.target.platform, expected.resolution.packages
        )
        try:
            observed = PythonVenvInspector().inspect(
                EnvironmentTarget(environment.path, "python-venv"),
                context,
            )
        except InspectionFailed as exc:
            failure_diagnostics = (
                Diagnostic(
                    code="verify.python.inspection-failed",
                    message=exc.message,
                    severity=Severity.ERROR,
                ),
                *exc.diagnostics,
            )
            return VerificationReport(
                passed=False,
                expected_state_digest=expected_digest,
                observed_state_digest=digest_json(
                    {"candidate": environment.candidate_id, "inspection": "failed"}
                ),
                diagnostics=failure_diagnostics,
            )

        observed_digest = _state_digest(observed.target.platform, observed.packages)
        diagnostics: list[Diagnostic] = []
        if observed.target != expected.resolution.target:
            diagnostics.append(
                Diagnostic(
                    code="verify.python.runtime-mismatch",
                    message=(
                        f"Candidate runtime {observed.target.platform} does not match locked "
                        f"runtime {expected.resolution.target.platform}"
                    ),
                    severity=Severity.ERROR,
                )
            )

        expected_by_id = {package.identity: package for package in expected.resolution.packages}
        observed_by_id = {package.identity: package for package in observed.packages}
        for identity in sorted(expected_by_id.keys() - observed_by_id.keys()):
            package = expected_by_id[identity]
            diagnostics.append(
                Diagnostic(
                    code="verify.python.missing-package",
                    message=f"Missing locked distribution {package.name}=={package.version}",
                    severity=Severity.ERROR,
                )
            )
        for identity in sorted(observed_by_id.keys() - expected_by_id.keys()):
            package = observed_by_id[identity]
            diagnostics.append(
                Diagnostic(
                    code="verify.python.unexpected-package",
                    message=f"Unexpected distribution {package.name}=={package.version} is present",
                    severity=Severity.ERROR,
                )
            )
        for identity in sorted(expected_by_id.keys() & observed_by_id.keys()):
            locked = expected_by_id[identity]
            installed = observed_by_id[identity]
            mismatches: list[str] = []
            if installed.version != locked.version:
                mismatches.append("version")
            if installed.artifact != locked.artifact:
                mismatches.append("artifact URL")
            if installed.checksum is not None and installed.checksum != locked.checksum:
                mismatches.append("artifact checksum")
            expected_dependencies = {item.name for item in locked.dependencies}
            installed_dependencies = {item.name for item in installed.dependencies}
            if not expected_dependencies.issubset(installed_dependencies):
                mismatches.append("dependency metadata")
            if mismatches:
                diagnostics.append(
                    Diagnostic(
                        code="verify.python.package-mismatch",
                        message=(
                            f"Installed distribution {locked.name} does not match its exact lock: "
                            + ", ".join(mismatches)
                        ),
                        severity=Severity.ERROR,
                    )
                )

        if not any(item.severity is Severity.ERROR for item in diagnostics):
            diagnostics.extend(self._run_imports(environment, policy, context))
            diagnostics.extend(self._run_commands(environment, policy, context))
        passed = not any(item.severity is Severity.ERROR for item in diagnostics)
        if passed:
            diagnostics.append(
                Diagnostic(
                    code="verify.python.exact-match",
                    message=(
                        f"Candidate {environment.candidate_id} exactly matches "
                        f"{len(expected.resolution.packages)} locked wheel artifacts"
                    ),
                    severity=Severity.INFO,
                )
            )
        return VerificationReport(
            passed=passed,
            expected_state_digest=expected_digest,
            observed_state_digest=observed_digest,
            diagnostics=tuple(diagnostics),
        )

    def _run_imports(
        self,
        environment: CandidateEnvironment,
        policy: VerificationPolicy,
        context: OperationContext,
    ) -> list[Diagnostic]:
        if not policy.load_packages:
            return []
        interpreter = _venv_python(environment.path)
        modules = json.dumps(list(policy.load_packages))
        script = (
            "import importlib, json; "
            f"[importlib.import_module(name) for name in json.loads({modules!r})]"
        )
        return self._execute(
            environment,
            ((str(interpreter), "-I", "-c", script),),
            context,
            code_prefix="verify.python.import",
        )

    def _run_commands(
        self,
        environment: CandidateEnvironment,
        policy: VerificationPolicy,
        context: OperationContext,
    ) -> list[Diagnostic]:
        return self._execute(
            environment,
            policy.commands,
            context,
            code_prefix="verify.command",
        )

    def _execute(
        self,
        environment: CandidateEnvironment,
        commands: tuple[tuple[str, ...], ...],
        context: OperationContext,
        *,
        code_prefix: str,
    ) -> list[Diagnostic]:
        if not commands:
            return []
        settings = uv_settings(context, error=_verification_error)
        runner = runner_for(context)
        bin_dir = environment.path / ("Scripts" if os.name == "nt" else "bin")
        base_environment = {
            "VIRTUAL_ENV": str(environment.path),
            "PYTHONNOUSERSITE": "1",
            "PATH": os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]),
        }
        diagnostics: list[Diagnostic] = []
        for index, argv in enumerate(commands, start=1):
            if not argv:
                raise VerificationFailed(
                    backend=self.name,
                    operation="verify",
                    message=f"Verification command {index} is empty",
                )
            try:
                result = runner.run(
                    Command(
                        argv=argv,
                        cwd=context.working_directory or environment.path,
                        environment=base_environment,
                        remove_environment=("PYTHONPATH", "PYTHONHOME"),
                    ),
                    timeout_seconds=settings.timeout_seconds,
                    output_limit=settings.output_limit,
                )
            except OSError as exc:
                raise VerificationFailed(
                    backend=self.name,
                    operation="verify",
                    message=f"Cannot execute verification command {argv[0]!r}: {exc}",
                ) from exc
            if result.timed_out:
                diagnostics.append(
                    Diagnostic(
                        code=f"{code_prefix}.timeout",
                        message=f"Verification command {index} timed out",
                        severity=Severity.ERROR,
                    )
                )
            elif result.output_truncated:
                diagnostics.append(
                    Diagnostic(
                        code=f"{code_prefix}.output-truncated",
                        message=f"Verification command {index} exceeded the output limit",
                        severity=Severity.ERROR,
                    )
                )
            elif result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip() or "no output"
                diagnostics.append(
                    Diagnostic(
                        code=f"{code_prefix}.failed",
                        message=(
                            f"Verification command {index} exited with {result.returncode}: {detail}"
                        ),
                        severity=Severity.ERROR,
                    )
                )
            else:
                diagnostics.append(
                    Diagnostic(
                        code=f"{code_prefix}.passed",
                        message=f"Verification command {index} passed: {argv[0]}",
                        severity=Severity.INFO,
                    )
                )
        return diagnostics


def _state_digest(platform: str, packages: Iterable[ResolvedPackage]) -> str:
    return digest_json(
        {
            "platform": platform,
            "packages": [
                {
                    "name": package.name,
                    "version": package.version,
                    "artifact": package.artifact,
                    "dependencies": [dependency.name for dependency in package.dependencies],
                }
                for package in sorted(packages, key=lambda item: item.name)
            ],
        }
    )


def _venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _verification_error(message: str) -> VerificationFailed:
    return VerificationFailed(backend="python-venv-verifier", operation="verify", message=message)
