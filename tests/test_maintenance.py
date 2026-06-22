from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

from depviz.api import Command, CommandResult, OperationContext, Severity
from depviz.core.doctor import run_doctor
from depviz.core.garbage_collection import collect_candidates
from depviz.infrastructure.commands import LocalCommandRunner
from depviz.infrastructure.deployment import (
    CandidateStatus,
    DeploymentState,
    ManagedDeploymentStore,
    new_candidate_record,
)
from depviz.plugins.defaults import create_default_registry


class DoctorRunner:
    def __init__(self) -> None:
        self.local = LocalCommandRunner()

    def run(
        self,
        command: Command,
        *,
        timeout_seconds: float,
        output_limit: int,
        redact: tuple[str, ...] = (),
    ) -> CommandResult:
        if command.argv[:2] == ("uv", "--version"):
            return CommandResult(command.argv, 0, "uv 0.10.0\n", "", 0.01)
        return self.local.run(
            command,
            timeout_seconds=timeout_seconds,
            output_limit=output_limit,
            redact=redact,
        )


def test_doctor_can_check_one_backend_toolchain() -> None:
    report = run_doctor(
        create_default_registry(discover_external=False),
        context=OperationContext(
            command_runner=DoctorRunner(),
            configuration={
                "python.uv_executable": "uv",
                "python.interpreter": sys.executable,
            },
        ),
        plugin_names=("depviz-python",),
    )

    assert report.passed
    assert any(item.code == "doctor.python.toolchain" for item in report.findings)
    assert not any(item.code.startswith("doctor.conda") for item in report.findings)


def test_doctor_rejects_unknown_plugin() -> None:
    report = run_doctor(
        create_default_registry(discover_external=False),
        plugin_names=("missing-plugin",),
    )

    assert not report.passed
    assert report.findings == (report.findings[0],)
    assert report.findings[0].code == "doctor.plugin.missing"
    assert report.findings[0].severity is Severity.ERROR


def test_garbage_collection_is_dry_run_then_marks_removed(tmp_path: Path) -> None:
    store = ManagedDeploymentStore(tmp_path / "deployment")
    store.initialize()
    candidate_ids: list[str] = []
    for index in range(5):
        candidate = store.reserve_candidate(
            kind="python-venv", deployment_kind="managed-python-deployment"
        )
        candidate_ids.append(candidate.candidate_id)
        record = new_candidate_record(
            candidate,
            lock_id=f"sha256:{index:064x}",
            lock_format="depviz.python-lock.v1",
            resolution_digest=f"sha256:{(index + 10):064x}",
        )
        record = replace(
            record,
            status=CandidateStatus.VERIFIED,
            created_at=f"2026-06-22T00:00:0{index}+00:00",
            updated_at=f"2026-06-22T00:00:0{index}+00:00",
        )
        store.write_candidate(record)

    current = candidate_ids[-1]
    rollback_target = candidate_ids[-2]
    store.write_state(
        DeploymentState(current_candidate_id=current, history=(candidate_ids[0], rollback_target))
    )
    store.switch_current_link(current)

    dry_run = collect_candidates(store.root, keep=1, execute=False)

    assert set(dry_run.protected) == {current, rollback_target}
    assert len(dry_run.retained) == 1
    assert len(dry_run.removable) == 2
    assert all((store.environments_dir / item).is_dir() for item in dry_run.removable)

    executed = collect_candidates(store.root, keep=1, execute=True)

    assert executed.removable == dry_run.removable
    for candidate_id in executed.removable:
        assert not (store.environments_dir / candidate_id).exists()
        assert store.read_candidate(candidate_id).status is CandidateStatus.REMOVED
    assert (store.environments_dir / current).is_dir()
    assert (store.environments_dir / rollback_target).is_dir()
