from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from depviz.api import (
    CandidateEnvironment,
    EnvironmentTarget,
    LockedResolution,
    OperationContext,
    VerificationPolicy,
    VerificationReport,
    Verifier,
)
from depviz.api.errors import VerificationFailed
from depviz.core.resolution import diagnostic_to_dict, digest_json
from depviz.infrastructure.deployment import (
    CandidateRecord,
    CandidateStatus,
    ManagedDeploymentStore,
)
from depviz.infrastructure.process_locks import ProcessLock, ProcessLockTimeout
from depviz.infrastructure.storage import write_bytes_atomic


def verify_candidate_environment(
    *,
    lock: LockedResolution,
    verifier: Verifier,
    deployment: EnvironmentTarget,
    candidate_id: str,
    policy: VerificationPolicy,
    context: OperationContext,
    lock_timeout_seconds: float = 30.0,
) -> VerificationReport:
    """Verify and record a candidate while serializing managed deployment operations."""

    if deployment.kind != verifier.deployment_kind:
        raise VerificationFailed(
            backend=verifier.name,
            operation="verify",
            message=f"Unsupported deployment target kind {deployment.kind!r}",
        )
    store = ManagedDeploymentStore(deployment.path)
    try:
        with ProcessLock(store.lock_path, timeout_seconds=lock_timeout_seconds):
            store.initialize()
            record = store.read_candidate(candidate_id)
            candidate = validate_candidate_lock(
                store=store,
                record=record,
                lock=lock,
                verifier=verifier,
            )
            report = verifier.verify(lock, candidate, policy, context)
            record_verification_result(
                store=store,
                candidate=candidate,
                lock_id=record.lock_id,
                report=report,
            )
            return report
    except VerificationFailed:
        raise
    except ProcessLockTimeout as error:
        raise VerificationFailed(
            backend=verifier.name,
            operation="verify",
            message=str(error),
        ) from error
    except (OSError, ValueError) as error:
        raise VerificationFailed(
            backend=verifier.name,
            operation="verify",
            message=str(error),
        ) from error


def validate_candidate_lock(
    *,
    store: ManagedDeploymentStore,
    record: CandidateRecord,
    lock: LockedResolution,
    verifier: Verifier,
) -> CandidateEnvironment:
    lock_id = lock.artifact.metadata.get("lock_id")
    resolution_digest = lock.artifact.metadata.get("resolution_digest")
    if not lock_id or not resolution_digest:
        raise VerificationFailed(
            backend=verifier.name,
            operation="verify",
            message="Lock artifact lacks required identity metadata",
        )
    if lock.artifact.metadata.get("environment_kind") != verifier.environment_kind:
        raise VerificationFailed(
            backend=verifier.name,
            operation="verify",
            message="Exact lock is incompatible with the selected verifier environment kind",
        )
    if lock.artifact.metadata.get("deployment_kind") != verifier.deployment_kind:
        raise VerificationFailed(
            backend=verifier.name,
            operation="verify",
            message="Exact lock is incompatible with the selected verifier deployment kind",
        )
    if record.environment_kind != verifier.environment_kind:
        raise VerificationFailed(
            backend=verifier.name,
            operation="verify",
            message="Candidate environment kind is incompatible with the selected verifier",
        )
    if record.lock_id != lock_id or record.resolution_digest != resolution_digest:
        raise VerificationFailed(
            backend=verifier.name,
            operation="verify",
            message="Candidate was not created from the supplied exact lock",
        )
    if record.lock_format != lock.artifact.format:
        raise VerificationFailed(
            backend=verifier.name,
            operation="verify",
            message="Candidate lock format does not match the supplied exact lock",
        )
    if record.status not in {
        CandidateStatus.APPLIED,
        CandidateStatus.VERIFICATION_FAILED,
        CandidateStatus.VERIFIED,
    }:
        raise VerificationFailed(
            backend=verifier.name,
            operation="verify",
            message=(
                f"Candidate {record.candidate_id} is in status {record.status.value!r}, "
                "not an applied environment"
            ),
        )
    candidate = store.candidate(
        record.candidate_id,
        kind=record.environment_kind,
        deployment_kind=verifier.deployment_kind,
    )
    if not candidate.path.is_dir() or candidate.path.is_symlink():
        raise VerificationFailed(
            backend=verifier.name,
            operation="verify",
            message=f"Candidate environment is missing or invalid: {candidate.path}",
        )
    return candidate


def record_verification_result(
    *,
    store: ManagedDeploymentStore,
    candidate: CandidateEnvironment,
    lock_id: str,
    report: VerificationReport,
) -> None:
    _write_verification_report(
        root=store.root,
        candidate=candidate,
        lock_id=lock_id,
        report=report,
    )
    store.update_candidate(
        candidate.candidate_id,
        status=(CandidateStatus.VERIFIED if report.passed else CandidateStatus.VERIFICATION_FAILED),
        verification_expected_digest=report.expected_state_digest,
        verification_observed_digest=report.observed_state_digest,
    )


def verification_report_to_dict(
    *,
    candidate: CandidateEnvironment,
    lock_id: str,
    report: VerificationReport,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "depviz.verification",
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "candidate_id": candidate.candidate_id,
        "candidate_path": str(candidate.path),
        "lock_id": lock_id,
        "passed": report.passed,
        "expected_state_digest": report.expected_state_digest,
        "observed_state_digest": report.observed_state_digest,
        "diagnostics": [diagnostic_to_dict(item) for item in report.diagnostics],
    }
    payload["report_id"] = digest_json(payload)
    return payload


def _write_verification_report(
    *,
    root: Path,
    candidate: CandidateEnvironment,
    lock_id: str,
    report: VerificationReport,
) -> None:
    document = verification_report_to_dict(
        candidate=candidate,
        lock_id=lock_id,
        report=report,
    )
    content = json.dumps(document, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    write_bytes_atomic(
        root / ".depviz" / "verifications" / f"{candidate.candidate_id}.json",
        content,
    )
