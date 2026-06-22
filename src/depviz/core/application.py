from __future__ import annotations

from depviz.api import (
    ApplyResult,
    EnvironmentDriver,
    EnvironmentTarget,
    LockedResolution,
    OperationContext,
)
from depviz.api.errors import ApplyFailed
from depviz.core.resolution import digest_json, resolution_to_dict
from depviz.infrastructure.deployment import (
    CandidateStatus,
    ManagedDeploymentStore,
    new_candidate_record,
)
from depviz.infrastructure.process_locks import ProcessLock, ProcessLockTimeout


def apply_locked_environment(
    *,
    lock: LockedResolution,
    driver: EnvironmentDriver,
    deployment: EnvironmentTarget,
    context: OperationContext,
    keep_failed: bool = False,
    lock_timeout_seconds: float = 30.0,
) -> ApplyResult:
    if deployment.kind != driver.deployment_kind:
        raise ApplyFailed(
            backend=driver.name,
            operation="apply",
            message=f"Unsupported deployment target kind {deployment.kind!r}",
        )
    lock_environment_kind = lock.artifact.metadata.get("environment_kind")
    lock_deployment_kind = lock.artifact.metadata.get("deployment_kind")
    if lock_environment_kind != driver.environment_kind:
        raise ApplyFailed(
            backend=driver.name,
            operation="apply",
            message=(
                f"Lock environment kind {lock_environment_kind!r} is incompatible with "
                f"driver kind {driver.environment_kind!r}"
            ),
        )
    if lock_deployment_kind != driver.deployment_kind:
        raise ApplyFailed(
            backend=driver.name,
            operation="apply",
            message=(
                f"Lock deployment kind {lock_deployment_kind!r} is incompatible with "
                f"driver kind {driver.deployment_kind!r}"
            ),
        )
    lock_id = lock.artifact.metadata.get("lock_id")
    resolution_digest = lock.artifact.metadata.get("resolution_digest") or digest_json(
        resolution_to_dict(lock.resolution)
    )
    if not lock_id:
        raise ApplyFailed(
            backend=driver.name,
            operation="apply",
            message="Lock artifact has no lock_id",
        )

    store = ManagedDeploymentStore(deployment.path)
    try:
        with ProcessLock(store.lock_path, timeout_seconds=lock_timeout_seconds):
            store.initialize()
            store.archive_lock(lock_id, lock.artifact.content)
            candidate = driver.create_candidate(deployment, context)
            store.write_candidate(
                new_candidate_record(
                    candidate,
                    lock_id=lock_id,
                    lock_format=lock.artifact.format,
                    resolution_digest=resolution_digest,
                )
            )
    except (OSError, ValueError, RuntimeError, ProcessLockTimeout) as error:
        if isinstance(error, ApplyFailed):
            raise
        raise ApplyFailed(
            backend=driver.name,
            operation="create candidate",
            message=str(error),
        ) from error

    try:
        result = driver.apply(lock, candidate, context)
        if result.candidate is not None and result.candidate != candidate:
            raise ApplyFailed(
                backend=driver.name,
                operation="apply",
                message="Environment driver returned an inconsistent candidate identity",
            )
        if result.lock_id is not None and result.lock_id != lock_id:
            raise ApplyFailed(
                backend=driver.name,
                operation="apply",
                message="Environment driver returned an inconsistent lock identity",
            )
        store.update_candidate(candidate.candidate_id, status=CandidateStatus.APPLIED)
        return ApplyResult(
            changed=result.changed,
            diagnostics=result.diagnostics,
            candidate=candidate,
            lock_id=lock_id,
        )
    except BaseException as error:
        try:
            store.update_candidate(candidate.candidate_id, status=CandidateStatus.FAILED)
        except (OSError, ValueError):
            pass
        if not keep_failed:
            try:
                driver.discard(candidate, context)
            except (OSError, ApplyFailed):
                pass
        if isinstance(error, (ApplyFailed, KeyboardInterrupt, SystemExit)):
            raise
        raise ApplyFailed(
            backend=driver.name,
            operation="apply",
            message=str(error),
        ) from error
