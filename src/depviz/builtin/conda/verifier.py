from __future__ import annotations

import os

from depviz.api import (
    CandidateEnvironment,
    Command,
    Diagnostic,
    EnvironmentTarget,
    LockedResolution,
    OperationContext,
    Severity,
    VerificationPolicy,
    VerificationReport,
)
from depviz.api.errors import InspectionFailed, VerificationFailed
from depviz.builtin.conda.inspector import CondaPrefixInspector
from depviz.builtin.conda.tooling import tool_settings
from depviz.core.resolution import package_set_digest
from depviz.infrastructure import LocalCommandRunner


class CondaPrefixVerifier:
    """Verify exact installed state and optional smoke commands in a candidate prefix."""

    name = "conda-prefix-verifier"
    environment_kind = "conda-prefix"
    deployment_kind = "managed-conda-deployment"

    def verify(
        self,
        expected: LockedResolution,
        environment: CandidateEnvironment,
        policy: VerificationPolicy,
        context: OperationContext,
    ) -> VerificationReport:
        if environment.target.kind not in {"managed-conda-deployment", "managed-deployment"}:
            raise VerificationFailed(
                backend=self.name,
                operation="verify",
                message=f"Unsupported candidate target kind {environment.target.kind!r}",
            )
        if policy.load_packages:
            raise VerificationFailed(
                backend=self.name,
                operation="verify",
                message="Generic Conda verification does not support language package-load probes",
            )
        expected_digest = package_set_digest(
            target=expected.resolution.target,
            packages=expected.resolution.packages,
        )
        try:
            observed = CondaPrefixInspector().inspect(
                EnvironmentTarget(path=environment.path, kind="conda-prefix"),
                OperationContext(
                    command_runner=context.command_runner,
                    working_directory=context.working_directory,
                    offline=context.offline,
                    configuration={
                        **context.configuration,
                        "conda.platform": expected.resolution.target.platform,
                    },
                ),
            )
        except InspectionFailed as error:
            raise VerificationFailed(
                backend=self.name,
                operation="verify",
                message=error.message,
                diagnostics=error.diagnostics,
            ) from error

        observed_digest = package_set_digest(target=observed.target, packages=observed.packages)
        diagnostics: list[Diagnostic] = []
        expected_by_id = {package.identity: package for package in expected.resolution.packages}
        observed_by_id = {package.identity: package for package in observed.packages}

        for identity in sorted(expected_by_id.keys() - observed_by_id.keys()):
            package = expected_by_id[identity]
            diagnostics.append(
                Diagnostic(
                    code="verify.conda.missing-package",
                    message=f"Missing locked package {package.name}={package.version}={package.build}",
                    severity=Severity.ERROR,
                )
            )
        for identity in sorted(observed_by_id.keys() - expected_by_id.keys()):
            package = observed_by_id[identity]
            diagnostics.append(
                Diagnostic(
                    code="verify.conda.unexpected-package",
                    message=(
                        f"Unexpected package {package.name}={package.version}={package.build} "
                        "is present"
                    ),
                    severity=Severity.ERROR,
                )
            )
        for identity in sorted(expected_by_id.keys() & observed_by_id.keys()):
            expected_package = expected_by_id[identity]
            observed_package = observed_by_id[identity]
            if expected_package != observed_package:
                diagnostics.append(
                    Diagnostic(
                        code="verify.conda.package-mismatch",
                        message=(
                            f"Installed package {expected_package.name} does not match its exact "
                            "locked version/build/source/artifact/checksum/dependencies"
                        ),
                        severity=Severity.ERROR,
                    )
                )

        if not diagnostics and expected_digest != observed_digest:
            diagnostics.append(
                Diagnostic(
                    code="verify.conda.state-digest-mismatch",
                    message="Installed package state does not match the exact lock",
                    severity=Severity.ERROR,
                )
            )

        if not any(item.severity is Severity.ERROR for item in diagnostics):
            diagnostics.extend(self._run_commands(environment, policy, context))

        passed = not any(item.severity is Severity.ERROR for item in diagnostics)
        if passed:
            diagnostics.append(
                Diagnostic(
                    code="verify.conda.exact-match",
                    message=(
                        f"Candidate {environment.candidate_id} exactly matches "
                        f"{len(expected.resolution.packages)} locked packages"
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

    def _run_commands(
        self,
        environment: CandidateEnvironment,
        policy: VerificationPolicy,
        context: OperationContext,
    ) -> list[Diagnostic]:
        if not policy.commands:
            return []
        settings = tool_settings(context, error=_verification_configuration_error)
        runner = context.command_runner or LocalCommandRunner()
        base_environment = {
            "CONDA_PREFIX": str(environment.path),
            "CONDA_DEFAULT_ENV": str(environment.path),
            "PYTHONNOUSERSITE": "1",
            "PATH": os.pathsep.join(
                [
                    str(environment.path / ("Scripts" if os.name == "nt" else "bin")),
                    os.environ.get("PATH", ""),
                ]
            ),
        }
        diagnostics: list[Diagnostic] = []
        for index, argv in enumerate(policy.commands, start=1):
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
                    ),
                    timeout_seconds=settings.timeout_seconds,
                    output_limit=settings.output_limit,
                )
            except OSError as error:
                raise VerificationFailed(
                    backend=self.name,
                    operation="verify",
                    message=f"Cannot execute verification command {argv[0]!r}: {error}",
                ) from error
            if result.timed_out:
                diagnostics.append(
                    Diagnostic(
                        code="verify.command.timeout",
                        message=f"Verification command {index} timed out",
                        severity=Severity.ERROR,
                    )
                )
                continue
            if result.output_truncated:
                diagnostics.append(
                    Diagnostic(
                        code="verify.command.output-truncated",
                        message=f"Verification command {index} exceeded the output limit",
                        severity=Severity.ERROR,
                    )
                )
                continue
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip() or "no output"
                diagnostics.append(
                    Diagnostic(
                        code="verify.command.failed",
                        message=(
                            f"Verification command {index} exited with {result.returncode}: {detail}"
                        ),
                        severity=Severity.ERROR,
                    )
                )
            else:
                diagnostics.append(
                    Diagnostic(
                        code="verify.command.passed",
                        message=f"Verification command {index} passed: {argv[0]}",
                        severity=Severity.INFO,
                    )
                )
        return diagnostics


def _verification_configuration_error(message: str) -> VerificationFailed:
    return VerificationFailed(
        backend="conda-prefix-verifier",
        operation="verify",
        message=message,
    )
