from __future__ import annotations

from dataclasses import replace

from depviz.api import (
    EnvironmentTarget,
    LockProvider,
    OperationContext,
    PromotionRecord,
    RollbackResult,
    VerificationPolicy,
    Verifier,
)
from depviz.api.errors import (
    BackendError,
    LockFailed,
    PromotionFailed,
    RollbackFailed,
    VerificationFailed,
)
from depviz.core.verification import (
    record_verification_result,
    validate_candidate_lock,
)
from depviz.infrastructure.deployment import (
    CandidateRecord,
    DeploymentState,
    ManagedDeploymentStore,
    PendingSwitch,
)
from depviz.infrastructure.process_locks import ProcessLock, ProcessLockTimeout


def promote_candidate(
    *,
    deployment: EnvironmentTarget,
    candidate_id: str,
    provider: LockProvider,
    verifier: Verifier,
    policy: VerificationPolicy,
    context: OperationContext,
    lock_timeout_seconds: float = 30.0,
) -> PromotionRecord:
    """Re-verify and atomically promote one immutable managed candidate."""

    store = _store(deployment, "promote")
    try:
        with ProcessLock(store.lock_path, timeout_seconds=lock_timeout_seconds):
            store.initialize()
            _recover_pending(store)
            record = store.read_candidate(candidate_id)
            _verify_for_switch(
                store=store,
                record=record,
                provider=provider,
                verifier=verifier,
                policy=policy,
                context=context,
                operation="promote",
                error_type=PromotionFailed,
            )

            state = store.read_state()
            _validate_pointer(store, state)
            previous = state.current_candidate_id
            if previous == candidate_id:
                return PromotionRecord(
                    deployment=deployment,
                    current_candidate_id=candidate_id,
                    previous_candidate_id=previous,
                    changed=False,
                )

            history = state.history + ((previous,) if previous is not None else ())
            next_state = DeploymentState(
                current_candidate_id=candidate_id,
                history=history,
            )
            _switch(
                store,
                operation="promote",
                from_candidate_id=previous,
                to_candidate_id=candidate_id,
                next_state=next_state,
            )
            return PromotionRecord(
                deployment=deployment,
                current_candidate_id=candidate_id,
                previous_candidate_id=previous,
                changed=True,
            )
    except PromotionFailed:
        raise
    except (OSError, ValueError, ProcessLockTimeout) as error:
        raise PromotionFailed(
            backend="core",
            operation="promote",
            message=str(error),
        ) from error


def rollback_deployment(
    *,
    deployment: EnvironmentTarget,
    provider: LockProvider,
    verifier: Verifier,
    policy: VerificationPolicy,
    context: OperationContext,
    lock_timeout_seconds: float = 30.0,
) -> RollbackResult:
    """Re-verify and atomically restore the previous managed candidate."""

    store = _store(deployment, "rollback")
    try:
        with ProcessLock(store.lock_path, timeout_seconds=lock_timeout_seconds):
            store.initialize()
            _recover_pending(store)
            state = store.read_state()
            _validate_pointer(store, state)
            current = state.current_candidate_id
            if current is None:
                raise RollbackFailed(
                    backend="core",
                    operation="rollback",
                    message="Deployment has no current candidate",
                )
            if not state.history:
                raise RollbackFailed(
                    backend="core",
                    operation="rollback",
                    message="Deployment has no previous candidate to restore",
                )

            previous = state.history[-1]
            record = store.read_candidate(previous)
            _verify_for_switch(
                store=store,
                record=record,
                provider=provider,
                verifier=verifier,
                policy=policy,
                context=context,
                operation="rollback",
                error_type=RollbackFailed,
            )

            next_state = DeploymentState(
                current_candidate_id=previous,
                history=state.history[:-1],
            )
            _switch(
                store,
                operation="rollback",
                from_candidate_id=current,
                to_candidate_id=previous,
                next_state=next_state,
            )
            return RollbackResult(
                deployment=deployment,
                current_candidate_id=previous,
                replaced_candidate_id=current,
                changed=True,
            )
    except RollbackFailed:
        raise
    except (OSError, ValueError, ProcessLockTimeout) as error:
        raise RollbackFailed(
            backend="core",
            operation="rollback",
            message=str(error),
        ) from error


def deployment_status(deployment: EnvironmentTarget) -> tuple[str | None, tuple[str, ...]]:
    store = _store(deployment, "status")
    try:
        with ProcessLock(store.lock_path, timeout_seconds=5.0):
            store.initialize()
            _recover_pending(store)
            state = store.read_state()
            _validate_pointer(store, state)
            return state.current_candidate_id, state.history
    except (OSError, ValueError, ProcessLockTimeout) as error:
        raise PromotionFailed(
            backend="core",
            operation="status",
            message=str(error),
        ) from error


def _verify_for_switch(
    *,
    store: ManagedDeploymentStore,
    record: CandidateRecord,
    provider: LockProvider,
    verifier: Verifier,
    policy: VerificationPolicy,
    context: OperationContext,
    operation: str,
    error_type: type[PromotionFailed] | type[RollbackFailed],
) -> None:
    """Verify archived lock identity and current candidate state under the switch lock."""

    try:
        lock_path = store.archived_lock_path(record.lock_id)
        if not lock_path.is_file() or lock_path.is_symlink():
            raise ValueError(f"Archived exact lock is missing or invalid: {lock_path}")
        locked = provider.read_lock(lock_path, context)
        candidate = validate_candidate_lock(
            store=store,
            record=record,
            lock=locked,
            verifier=verifier,
        )
        report = verifier.verify(locked, candidate, policy, context)
        record_verification_result(
            store=store,
            candidate=candidate,
            lock_id=record.lock_id,
            report=report,
        )
    except (LockFailed, VerificationFailed) as error:
        raise error_type(
            backend=error.backend,
            operation=operation,
            message=error.message,
            diagnostics=error.diagnostics,
        ) from error
    except BackendError as error:
        raise error_type(
            backend=error.backend,
            operation=operation,
            message=error.message,
            diagnostics=error.diagnostics,
        ) from error
    except (OSError, ValueError) as error:
        raise error_type(
            backend="core",
            operation=operation,
            message=str(error),
        ) from error

    if not report.passed:
        raise error_type(
            backend=verifier.name,
            operation=operation,
            message=(
                f"Candidate {record.candidate_id} failed mandatory verification immediately "
                f"before {operation}"
            ),
            diagnostics=report.diagnostics,
        )


def _store(deployment: EnvironmentTarget, operation: str) -> ManagedDeploymentStore:
    if deployment.kind not in {
        "managed-conda-deployment",
        "managed-python-deployment",
        "managed-conda-pip-deployment",
    }:
        error_type = RollbackFailed if operation == "rollback" else PromotionFailed
        raise error_type(
            backend="core",
            operation=operation,
            message=f"Unsupported deployment target kind {deployment.kind!r}",
        )
    return ManagedDeploymentStore(deployment.path)


def _validate_pointer(store: ManagedDeploymentStore, state: DeploymentState) -> None:
    link = store.current_link_candidate_id()
    if link != state.current_candidate_id:
        raise ValueError(
            "Deployment state and current symlink disagree; refusing to guess which is authoritative"
        )


def _switch(
    store: ManagedDeploymentStore,
    *,
    operation: str,
    from_candidate_id: str | None,
    to_candidate_id: str,
    next_state: DeploymentState,
) -> None:
    pending = PendingSwitch(
        operation=operation,
        from_candidate_id=from_candidate_id,
        to_candidate_id=to_candidate_id,
        next_state=next_state,
    )
    store.write_pending(pending)
    store.switch_current_link(to_candidate_id)
    store.write_state(replace(next_state, updated_at=None))
    store.clear_pending()


def _recover_pending(store: ManagedDeploymentStore) -> None:
    pending = store.read_pending()
    if pending is None:
        return
    state = store.read_state()
    link = store.current_link_candidate_id()
    old_matches = state.current_candidate_id == pending.from_candidate_id
    new_matches = state.current_candidate_id == pending.to_candidate_id
    if old_matches and link == pending.from_candidate_id:
        store.clear_pending()
        return
    if old_matches and link == pending.to_candidate_id:
        store.write_state(pending.next_state)
        store.clear_pending()
        return
    if new_matches and link == pending.to_candidate_id:
        store.clear_pending()
        return
    raise ValueError(
        "Cannot recover interrupted deployment switch: state, symlink, and journal disagree"
    )
