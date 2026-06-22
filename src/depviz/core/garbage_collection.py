from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from depviz.infrastructure.deployment import CandidateStatus, ManagedDeploymentStore
from depviz.infrastructure.process_locks import ProcessLock, ProcessLockTimeout


@dataclass(frozen=True)
class GarbageCollectionPlan:
    deployment: Path
    protected: tuple[str, ...]
    retained: tuple[str, ...]
    removable: tuple[str, ...]
    already_removed: tuple[str, ...]


def collect_candidates(
    deployment: Path,
    *,
    keep: int,
    execute: bool,
    lock_timeout_seconds: float = 30.0,
) -> GarbageCollectionPlan:
    if keep < 0:
        raise ValueError("keep must be non-negative")
    store = ManagedDeploymentStore(deployment)
    try:
        with ProcessLock(store.lock_path, timeout_seconds=lock_timeout_seconds):
            if not store.root.is_dir():
                raise ValueError(f"Deployment root does not exist: {store.root}")
            if store.read_pending() is not None:
                raise ValueError("Refusing garbage collection while a deployment switch is pending")
            state = store.read_state()
            linked = store.current_link_candidate_id()
            if linked != state.current_candidate_id:
                raise ValueError("Deployment state and current pointer disagree")
            protected_set = {item for item in (state.current_candidate_id,) if item is not None}
            if state.history:
                protected_set.add(state.history[-1])
            records = sorted(
                store.list_candidates(),
                key=lambda item: (item.created_at, item.candidate_id),
                reverse=True,
            )
            already_removed = tuple(
                item.candidate_id for item in records if item.status is CandidateStatus.REMOVED
            )
            available = [
                item
                for item in records
                if item.status is not CandidateStatus.REMOVED
                and item.candidate_id not in protected_set
            ]
            retained = tuple(item.candidate_id for item in available[:keep])
            removable = tuple(item.candidate_id for item in available[keep:])
            plan = GarbageCollectionPlan(
                deployment=store.root,
                protected=tuple(sorted(protected_set)),
                retained=retained,
                removable=removable,
                already_removed=already_removed,
            )
            if execute:
                for candidate_id in removable:
                    record = store.read_candidate(candidate_id)
                    candidate = store.candidate(
                        candidate_id,
                        kind=record.environment_kind,
                        deployment_kind="managed-deployment",
                    )
                    expected = store.environments_dir / candidate_id
                    if candidate.path.resolve() != expected.resolve():
                        raise ValueError(f"Candidate path escapes deployment: {candidate.path}")
                    if candidate.path.is_symlink():
                        raise ValueError(f"Refusing to remove symlink candidate: {candidate.path}")
                    if candidate.path.exists():
                        if not candidate.path.is_dir():
                            raise ValueError(f"Candidate path is not a directory: {candidate.path}")
                        shutil.rmtree(candidate.path)
                    store.update_candidate(candidate_id, status=CandidateStatus.REMOVED)
            return plan
    except ProcessLockTimeout as exc:
        raise ValueError(str(exc)) from exc
