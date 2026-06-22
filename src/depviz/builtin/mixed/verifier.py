from __future__ import annotations

import json
import os
from collections.abc import Iterable

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
from depviz.builtin.conda.verifier import CondaPrefixVerifier
from depviz.builtin.mixed.locking import mixed_lock_layers
from depviz.builtin.python.locking import interpreter_metadata
from depviz.builtin.python.prefix import inspect_python_prefix, python_executable
from depviz.builtin.python.tooling import runner_for, uv_settings
from depviz.core.resolution import digest_json


class CondaPipPrefixVerifier:
    name = "conda-pip-prefix-verifier"
    environment_kind = "conda-pip-prefix"
    deployment_kind = "managed-conda-pip-deployment"

    def verify(
        self,
        expected: LockedResolution,
        environment: CandidateEnvironment,
        policy: VerificationPolicy,
        context: OperationContext,
    ) -> VerificationReport:
        if environment.target.kind != self.deployment_kind:
            raise VerificationFailed(
                backend=self.name,
                operation="verify",
                message=f"Unsupported candidate target kind {environment.target.kind!r}",
            )
        if environment.kind != self.environment_kind:
            raise VerificationFailed(
                backend=self.name,
                operation="verify",
                message=f"Unsupported candidate environment kind {environment.kind!r}",
            )
        conda_lock, python_lock = mixed_lock_layers(expected, context)
        conda_candidate = CandidateEnvironment(
            target=EnvironmentTarget(environment.target.path, "managed-conda-deployment"),
            candidate_id=environment.candidate_id,
            path=environment.path,
            kind="conda-prefix",
        )
        conda_report = CondaPrefixVerifier().verify(
            conda_lock,
            conda_candidate,
            VerificationPolicy(),
            context,
        )
        python_report = _verify_python_overlay(
            python_lock,
            environment,
            policy,
            context,
        )
        diagnostics = (*conda_report.diagnostics, *python_report.diagnostics)
        return VerificationReport(
            passed=conda_report.passed and python_report.passed,
            expected_state_digest=digest_json(
                {
                    "conda": conda_report.expected_state_digest,
                    "python": python_report.expected_state_digest,
                }
            ),
            observed_state_digest=digest_json(
                {
                    "conda": conda_report.observed_state_digest,
                    "python": python_report.observed_state_digest,
                }
            ),
            diagnostics=diagnostics,
        )


def _verify_python_overlay(
    expected: LockedResolution,
    environment: CandidateEnvironment,
    policy: VerificationPolicy,
    context: OperationContext,
) -> VerificationReport:
    expected_digest = _state_digest(expected.resolution.packages)
    names = {package.name for package in expected.resolution.packages}
    try:
        observed = inspect_python_prefix(
            environment.path,
            context,
            backend="conda-pip-prefix-verifier",
            environment_kind="conda-pip-prefix",
            require_virtual_environment=False,
            include_names=names,
        )
    except InspectionFailed as exc:
        return VerificationReport(
            passed=False,
            expected_state_digest=expected_digest,
            observed_state_digest=digest_json(
                {"candidate": environment.candidate_id, "python_inspection": "failed"}
            ),
            diagnostics=(
                Diagnostic(
                    code="verify.conda-pip.python-inspection-failed",
                    message=exc.message,
                    severity=Severity.ERROR,
                ),
                *exc.diagnostics,
            ),
        )

    diagnostics: list[Diagnostic] = list(observed.diagnostics)
    expected_runtime = interpreter_metadata(expected)
    observed_runtime = (
        observed.native_payload.data.get("interpreter")
        if observed.native_payload is not None
        else None
    )
    if not isinstance(observed_runtime, dict):
        diagnostics.append(
            Diagnostic(
                code="verify.conda-pip.runtime-missing",
                message="Candidate Python runtime metadata is missing",
                severity=Severity.ERROR,
            )
        )
    else:
        for field in ("implementation", "version"):
            if observed_runtime.get(field) != expected_runtime.get(field):
                diagnostics.append(
                    Diagnostic(
                        code="verify.conda-pip.runtime-mismatch",
                        message=(
                            f"Candidate Python {field} {observed_runtime.get(field)!r} does not "
                            f"match locked value {expected_runtime.get(field)!r}"
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
                code="verify.conda-pip.missing-wheel",
                message=f"Missing locked pip distribution {package.name}=={package.version}",
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
        if mismatches:
            diagnostics.append(
                Diagnostic(
                    code="verify.conda-pip.wheel-mismatch",
                    message=(
                        f"Installed pip distribution {locked.name} does not match its exact lock: "
                        + ", ".join(mismatches)
                    ),
                    severity=Severity.ERROR,
                )
            )

    if not any(item.severity is Severity.ERROR for item in diagnostics):
        diagnostics.extend(_run_probes(environment, policy, context))
    passed = not any(item.severity is Severity.ERROR for item in diagnostics)
    if passed:
        diagnostics.append(
            Diagnostic(
                code="verify.conda-pip.exact-match",
                message=(
                    f"Candidate {environment.candidate_id} matches "
                    f"{len(expected.resolution.packages)} locked pip wheel artifacts"
                ),
                severity=Severity.INFO,
            )
        )
    return VerificationReport(
        passed=passed,
        expected_state_digest=expected_digest,
        observed_state_digest=_state_digest(observed.packages),
        diagnostics=tuple(diagnostics),
    )


def _run_probes(
    environment: CandidateEnvironment,
    policy: VerificationPolicy,
    context: OperationContext,
) -> list[Diagnostic]:
    commands = list(policy.commands)
    interpreter = python_executable(environment.path)
    if policy.load_packages:
        modules = json.dumps(list(policy.load_packages))
        script = (
            "import importlib, json; "
            f"[importlib.import_module(name) for name in json.loads({modules!r})]"
        )
        commands.insert(0, (str(interpreter), "-I", "-c", script))
    if not commands:
        return []
    settings = uv_settings(
        context,
        error=lambda message: VerificationFailed(
            backend="conda-pip-prefix-verifier",
            operation="verify",
            message=message,
        ),
    )
    runner = runner_for(context)
    diagnostics: list[Diagnostic] = []
    for index, argv in enumerate(commands, start=1):
        if not argv:
            raise VerificationFailed(
                backend="conda-pip-prefix-verifier",
                operation="verify",
                message=f"Verification command {index} is empty",
            )
        try:
            result = runner.run(
                Command(
                    argv=argv,
                    cwd=context.working_directory or environment.path,
                    environment={
                        "PYTHONNOUSERSITE": "1",
                        "PATH": os.pathsep.join(
                            [str(interpreter.parent), os.environ.get("PATH", "")]
                        ),
                    },
                    remove_environment=("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"),
                ),
                timeout_seconds=settings.timeout_seconds,
                output_limit=settings.output_limit,
            )
        except OSError as exc:
            raise VerificationFailed(
                backend="conda-pip-prefix-verifier",
                operation="verify",
                message=f"Cannot execute verification command {argv[0]!r}: {exc}",
            ) from exc
        if result.timed_out:
            diagnostics.append(
                Diagnostic(
                    code="verify.conda-pip.probe-timeout",
                    message=f"Verification command {index} timed out",
                    severity=Severity.ERROR,
                )
            )
        elif result.output_truncated:
            diagnostics.append(
                Diagnostic(
                    code="verify.conda-pip.probe-output-truncated",
                    message=f"Verification command {index} exceeded the output limit",
                    severity=Severity.ERROR,
                )
            )
        elif result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no output"
            diagnostics.append(
                Diagnostic(
                    code="verify.conda-pip.probe-failed",
                    message=(
                        f"Verification command {index} exited with {result.returncode}: {detail}"
                    ),
                    severity=Severity.ERROR,
                )
            )
        else:
            diagnostics.append(
                Diagnostic(
                    code="verify.conda-pip.probe-passed",
                    message=f"Verification command {index} passed: {argv[0]}",
                    severity=Severity.INFO,
                )
            )
    return diagnostics


def _state_digest(packages: Iterable[ResolvedPackage]) -> str:
    return digest_json(
        {
            "packages": [
                {
                    "name": package.name,
                    "version": package.version,
                    "artifact": package.artifact,
                    "checksum": package.checksum,
                }
                for package in sorted(packages, key=lambda item: item.name)
            ]
        }
    )
