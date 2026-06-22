from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from depviz.api import (
    BackendPlugin,
    CandidateEnvironment,
    DependencyIntent,
    EnvironmentTarget,
    OperationContext,
    Target,
    VerificationPolicy,
)
from depviz.core.application import apply_locked_environment
from depviz.core.promotion import deployment_status, promote_candidate, rollback_deployment
from depviz.core.resolution import resolve_intent
from depviz.core.verification import verify_candidate_environment
from depviz.infrastructure.storage import write_bytes_atomic
from depviz.plugins.registry import PluginRegistry
from depviz.plugins.validation import validate_plugin


@dataclass(frozen=True)
class BackendConformanceCase:
    """Inputs required to exercise one backend through the public lifecycle contracts."""

    plugin: BackendPlugin
    resolver: str
    lock_provider: str
    environment_driver: str
    verifier: str
    intent: DependencyIntent
    target: Target
    deployment: EnvironmentTarget
    context: OperationContext
    policy: VerificationPolicy = VerificationPolicy()
    tamper: Callable[[CandidateEnvironment], None] | None = None


@dataclass(frozen=True)
class BackendConformanceResult:
    first_candidate_id: str
    second_candidate_id: str
    rollback_candidate_id: str
    drift_detected: bool | None


def run_backend_conformance_suite(
    case: BackendConformanceCase,
    *,
    work_directory: Path,
) -> BackendConformanceResult:
    """Exercise the stable plugin boundary without relying on plugin internals.

    The function raises ``AssertionError`` for a contract violation so contributors can call it
    directly from pytest, unittest, or another test framework.
    """

    validate_plugin(case.plugin)
    for health_check in case.plugin.health_checks:
        diagnostics = health_check.check(case.context)
        assert not any(diagnostic.severity.value == "error" for diagnostic in diagnostics), (
            f"health check {health_check.name} reported an error"
        )
    registry = PluginRegistry()
    registry.register(case.plugin)
    resolver = registry.find_resolver(case.resolver)
    provider = registry.find_lock_provider(case.lock_provider)
    driver = registry.find_environment_driver(case.environment_driver)
    verifier = registry.find_verifier(case.verifier)
    assert driver.environment_kind == verifier.environment_kind, (
        "driver and verifier disagree on environment kind"
    )
    assert driver.deployment_kind == verifier.deployment_kind, (
        "driver and verifier disagree on deployment kind"
    )
    assert case.deployment.kind == driver.deployment_kind, (
        "conformance deployment kind does not match the driver"
    )

    resolution = resolve_intent(
        intent=case.intent,
        resolver=resolver,
        target=case.target,
        current=None,
        context=case.context,
    )
    assert resolution.complete, "resolver returned a non-complete resolution"
    assert resolution.packages, "resolver returned an empty successful package set"

    artifact = provider.create_lock(resolution, case.context)
    assert artifact.metadata.get("lock_id"), "lock has no stable lock_id"
    lock_path = work_directory / "conformance-lock.json"
    write_bytes_atomic(lock_path, artifact.content)
    locked = provider.read_lock(lock_path, case.context)
    assert locked.resolution == resolution, "lock round-trip changed the normalized resolution"
    assert locked.artifact.metadata.get("environment_kind") == driver.environment_kind, (
        "lock does not declare the driver's environment kind"
    )
    assert locked.artifact.metadata.get("deployment_kind") == driver.deployment_kind, (
        "lock does not declare the driver's deployment kind"
    )

    first = apply_locked_environment(
        lock=locked,
        driver=driver,
        deployment=case.deployment,
        context=case.context,
    )
    assert first.candidate is not None, "apply returned no candidate"
    assert first.candidate.kind == driver.environment_kind
    current, history = deployment_status(case.deployment)
    assert current is None and not history, "apply mutated the active deployment"
    first_report = verify_candidate_environment(
        lock=locked,
        verifier=verifier,
        deployment=case.deployment,
        candidate_id=first.candidate.candidate_id,
        policy=case.policy,
        context=case.context,
    )
    assert first_report.passed, "fresh candidate failed exact verification"
    assert first_report.expected_state_digest == first_report.observed_state_digest, (
        "successful exact verification returned different expected and observed digests"
    )
    promote_candidate(
        deployment=case.deployment,
        candidate_id=first.candidate.candidate_id,
        provider=provider,
        verifier=verifier,
        policy=case.policy,
        context=case.context,
    )

    second = apply_locked_environment(
        lock=locked,
        driver=driver,
        deployment=case.deployment,
        context=case.context,
    )
    assert second.candidate is not None, "second apply returned no candidate"
    current, _history = deployment_status(case.deployment)
    assert current == first.candidate.candidate_id, "candidate construction changed current"
    second_report = verify_candidate_environment(
        lock=locked,
        verifier=verifier,
        deployment=case.deployment,
        candidate_id=second.candidate.candidate_id,
        policy=case.policy,
        context=case.context,
    )
    assert second_report.passed, "second candidate failed exact verification"
    promote_candidate(
        deployment=case.deployment,
        candidate_id=second.candidate.candidate_id,
        provider=provider,
        verifier=verifier,
        policy=case.policy,
        context=case.context,
    )
    rolled_back = rollback_deployment(
        deployment=case.deployment,
        provider=provider,
        verifier=verifier,
        policy=case.policy,
        context=case.context,
    )
    assert rolled_back.current_candidate_id == first.candidate.candidate_id

    drift_detected: bool | None = None
    if case.tamper is not None:
        third = apply_locked_environment(
            lock=locked,
            driver=driver,
            deployment=case.deployment,
            context=case.context,
        )
        assert third.candidate is not None
        case.tamper(third.candidate)
        report = verify_candidate_environment(
            lock=locked,
            verifier=verifier,
            deployment=case.deployment,
            candidate_id=third.candidate.candidate_id,
            policy=case.policy,
            context=case.context,
        )
        drift_detected = not report.passed
        assert drift_detected, "verifier did not detect candidate drift"

    return BackendConformanceResult(
        first_candidate_id=first.candidate.candidate_id,
        second_candidate_id=second.candidate.candidate_id,
        rollback_candidate_id=rolled_back.current_candidate_id,
        drift_detected=drift_detected,
    )
