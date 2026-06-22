from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from depviz.api import (
    ApplyResult,
    CandidateEnvironment,
    EnvironmentTarget,
    LockedResolution,
    LockArtifact,
    OperationContext,
    Resolution,
    ResolutionStatus,
    Target,
)
from depviz.core.application import apply_locked_environment
from depviz.infrastructure.deployment import CandidateStatus, ManagedDeploymentStore

pytestmark = pytest.mark.failure_injection


@dataclass
class InterruptingDriver:
    name: str = "interrupting-driver"
    environment_kind: str = "test-environment"
    deployment_kind: str = "managed-python-deployment"

    def create_candidate(
        self, target: EnvironmentTarget, context: OperationContext
    ) -> CandidateEnvironment:
        del context
        return ManagedDeploymentStore(target.path).reserve_candidate(
            kind=self.environment_kind,
            deployment_kind=self.deployment_kind,
        )

    def apply(
        self,
        lock: LockedResolution,
        candidate: CandidateEnvironment,
        context: OperationContext,
    ) -> ApplyResult:
        del lock, candidate, context
        raise KeyboardInterrupt

    def discard(self, candidate: CandidateEnvironment, context: OperationContext) -> None:
        del context
        candidate.path.rmdir()


def test_keyboard_interrupt_marks_candidate_failed_and_removes_environment(tmp_path: Path) -> None:
    lock_id = f"sha256:{'a' * 64}"
    resolution_digest = f"sha256:{'b' * 64}"
    locked = LockedResolution(
        resolution=Resolution(
            requested=(),
            packages=(),
            target=Target("test"),
            status=ResolutionStatus.COMPLETE,
        ),
        artifact=LockArtifact(
            format="test-lock.v1",
            content=b"{}\n",
            metadata={
                "lock_id": lock_id,
                "resolution_digest": resolution_digest,
                "environment_kind": "test-environment",
                "deployment_kind": "managed-python-deployment",
            },
        ),
    )
    deployment = EnvironmentTarget(tmp_path / "deployment", "managed-python-deployment")

    with pytest.raises(KeyboardInterrupt):
        apply_locked_environment(
            lock=locked,
            driver=InterruptingDriver(),
            deployment=deployment,
            context=OperationContext(),
        )

    store = ManagedDeploymentStore(deployment.path)
    records = store.list_candidates()
    assert len(records) == 1
    assert records[0].status is CandidateStatus.FAILED
    assert not store.candidate(records[0].candidate_id).path.exists()
