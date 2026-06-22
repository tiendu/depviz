from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from depviz.api import (
    Command,
    CommandResult,
    EnvironmentTarget,
    OperationContext,
    PackageReference,
    Resolution,
    ResolutionStatus,
    ResolvedPackage,
    Target,
    VerificationPolicy,
)
from depviz.api.errors import ApplyFailed, PromotionFailed, RollbackFailed
from depviz.builtin.conda import (
    CondaLockProvider,
    CondaPrefixDriver,
    CondaPrefixVerifier,
)
from depviz.core.application import apply_locked_environment
from depviz.core.promotion import deployment_status, promote_candidate, rollback_deployment
from depviz.core.resolution import host_conda_platform
from depviz.core.verification import verify_candidate_environment
from depviz.infrastructure.deployment import (
    CandidateStatus,
    DeploymentState,
    ManagedDeploymentStore,
    PendingSwitch,
    new_candidate_record,
)
from depviz.infrastructure.process_locks import ProcessLock, ProcessLockTimeout


@dataclass
class InstallingRunner:
    packages: tuple[ResolvedPackage, ...]
    fail_install: bool = False
    calls: list[Command] = field(default_factory=list)
    explicit_lines: tuple[str, ...] = ()

    def run(
        self,
        command: Command,
        *,
        timeout_seconds: float,
        output_limit: int,
        redact: tuple[str, ...] = (),
    ) -> CommandResult:
        del timeout_seconds, output_limit, redact
        self.calls.append(command)
        if "--version" in command.argv:
            return _result(command, stdout="micromamba 2.1.0\n")
        if "create" in command.argv:
            explicit_path = Path(command.argv[command.argv.index("--file") + 1])
            self.explicit_lines = tuple(explicit_path.read_text().splitlines())
            if self.fail_install:
                return _result(
                    command,
                    returncode=1,
                    stdout=json.dumps({"success": False, "error": "download failed"}),
                )
            prefix = Path(command.argv[command.argv.index("--prefix") + 1])
            metadata = prefix / "conda-meta"
            metadata.mkdir(parents=True, exist_ok=True)
            for package in self.packages:
                algorithm, digest = (package.checksum or "").split(":", 1)
                record: dict[str, object] = {
                    "name": package.name,
                    "version": package.version,
                    "build": package.build,
                    "subdir": package.platform,
                    "channel": package.source,
                    "url": package.artifact,
                    "depends": [
                        " ".join(
                            item
                            for item in (dependency.name, dependency.specifier)
                            if item is not None
                        )
                        for dependency in package.dependencies
                    ],
                    algorithm: digest,
                }
                (metadata / f"{package.name}-{package.version}-{package.build}.json").write_text(
                    json.dumps(record), encoding="utf-8"
                )
            return _result(command, stdout=json.dumps({"success": True}))
        return _result(command, stdout="probe ok\n")


def _result(
    command: Command,
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> CommandResult:
    return CommandResult(
        argv=command.argv,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0.01,
    )


def _packages() -> tuple[ResolvedPackage, ...]:
    platform = host_conda_platform()
    base = f"https://conda.example/conda-forge/{platform}"
    lib = ResolvedPackage(
        ecosystem="conda",
        name="libexample",
        version="1.0",
        build="h1_0",
        platform=platform,
        source=base,
        artifact=f"{base}/libexample-1.0-h1_0.conda",
        checksum=f"sha256:{'a' * 64}",
    )
    app = ResolvedPackage(
        ecosystem="conda",
        name="example",
        version="2.0",
        build="h2_0",
        platform=platform,
        source=base,
        artifact=f"{base}/example-2.0-h2_0.conda",
        checksum=f"sha256:{'b' * 64}",
        dependencies=(PackageReference("conda", "libexample", ">=1.0"),),
    )
    return lib, app


def _locked(tmp_path: Path):  # type: ignore[no-untyped-def]
    packages = _packages()
    resolution = Resolution(
        requested=(),
        packages=packages,
        target=Target(host_conda_platform()),
        status=ResolutionStatus.COMPLETE,
    )
    provider = CondaLockProvider()
    artifact = provider.create_lock(resolution, OperationContext())
    path = tmp_path / "depviz-lock.json"
    path.write_bytes(artifact.content)
    return provider.read_lock(path, OperationContext()), packages


def _context(runner: InstallingRunner) -> OperationContext:
    return OperationContext(
        command_runner=runner,
        configuration={
            "conda.tool": "micromamba",
            "conda.executable": "micromamba",
            "conda.timeout_seconds": "10",
            "conda.output_limit": "1000000",
        },
    )


def test_apply_verify_promote_and_rollback_exact_candidates(tmp_path: Path) -> None:
    locked, packages = _locked(tmp_path)
    deployment = EnvironmentTarget(tmp_path / "deployment", "managed-conda-deployment")
    runner = InstallingRunner(packages)

    first = apply_locked_environment(
        lock=locked,
        driver=CondaPrefixDriver(),
        deployment=deployment,
        context=_context(runner),
    )
    assert first.candidate is not None
    assert runner.explicit_lines[0] == "@EXPLICIT"
    assert runner.explicit_lines[1].endswith(f"#{'a' * 64}")
    assert "#sha256=" not in runner.explicit_lines[1]

    first_report = verify_candidate_environment(
        lock=locked,
        verifier=CondaPrefixVerifier(),
        deployment=deployment,
        candidate_id=first.candidate.candidate_id,
        policy=VerificationPolicy(commands=(("example", "--version"),)),
        context=_context(runner),
    )
    assert first_report.passed
    first_promotion = promote_candidate(
        deployment=deployment,
        candidate_id=first.candidate.candidate_id,
        provider=CondaLockProvider(),
        verifier=CondaPrefixVerifier(),
        policy=VerificationPolicy(commands=(("example", "--version"),)),
        context=_context(runner),
    )
    assert first_promotion.changed
    assert (deployment.path / "current").resolve() == first.candidate.path.resolve()

    second = apply_locked_environment(
        lock=locked,
        driver=CondaPrefixDriver(),
        deployment=deployment,
        context=_context(runner),
    )
    assert second.candidate is not None
    second_report = verify_candidate_environment(
        lock=locked,
        verifier=CondaPrefixVerifier(),
        deployment=deployment,
        candidate_id=second.candidate.candidate_id,
        policy=VerificationPolicy(),
        context=_context(runner),
    )
    assert second_report.passed
    promote_candidate(
        deployment=deployment,
        candidate_id=second.candidate.candidate_id,
        provider=CondaLockProvider(),
        verifier=CondaPrefixVerifier(),
        policy=VerificationPolicy(),
        context=_context(runner),
    )

    current, history = deployment_status(deployment)
    assert current == second.candidate.candidate_id
    assert history == (first.candidate.candidate_id,)

    rollback = rollback_deployment(
        deployment=deployment,
        provider=CondaLockProvider(),
        verifier=CondaPrefixVerifier(),
        policy=VerificationPolicy(),
        context=_context(runner),
    )
    assert rollback.current_candidate_id == first.candidate.candidate_id
    assert rollback.replaced_candidate_id == second.candidate.candidate_id
    assert (deployment.path / "current").resolve() == first.candidate.path.resolve()


def test_verification_failure_blocks_promotion(tmp_path: Path) -> None:
    locked, packages = _locked(tmp_path)
    deployment = EnvironmentTarget(tmp_path / "deployment", "managed-conda-deployment")
    runner = InstallingRunner(packages)
    result = apply_locked_environment(
        lock=locked,
        driver=CondaPrefixDriver(),
        deployment=deployment,
        context=_context(runner),
    )
    assert result.candidate is not None
    record_path = next((result.candidate.path / "conda-meta").glob("example-*.json"))
    document = json.loads(record_path.read_text())
    document["version"] = "9.9"
    record_path.write_text(json.dumps(document))

    report = verify_candidate_environment(
        lock=locked,
        verifier=CondaPrefixVerifier(),
        deployment=deployment,
        candidate_id=result.candidate.candidate_id,
        policy=VerificationPolicy(),
        context=_context(runner),
    )
    assert not report.passed
    store = ManagedDeploymentStore(deployment.path)
    assert (
        store.read_candidate(result.candidate.candidate_id).status
        is CandidateStatus.VERIFICATION_FAILED
    )
    with pytest.raises(PromotionFailed, match="mandatory verification"):
        promote_candidate(
            deployment=deployment,
            candidate_id=result.candidate.candidate_id,
            provider=CondaLockProvider(),
            verifier=CondaPrefixVerifier(),
            policy=VerificationPolicy(),
            context=_context(runner),
        )


def test_promotion_reverifies_after_a_previous_success(tmp_path: Path) -> None:
    locked, packages = _locked(tmp_path)
    deployment = EnvironmentTarget(tmp_path / "deployment", "managed-conda-deployment")
    runner = InstallingRunner(packages)
    result = apply_locked_environment(
        lock=locked,
        driver=CondaPrefixDriver(),
        deployment=deployment,
        context=_context(runner),
    )
    assert result.candidate is not None
    report = verify_candidate_environment(
        lock=locked,
        verifier=CondaPrefixVerifier(),
        deployment=deployment,
        candidate_id=result.candidate.candidate_id,
        policy=VerificationPolicy(),
        context=_context(runner),
    )
    assert report.passed

    record_path = next((result.candidate.path / "conda-meta").glob("example-*.json"))
    document = json.loads(record_path.read_text())
    document["build"] = "tampered_0"
    record_path.write_text(json.dumps(document))

    with pytest.raises(PromotionFailed, match="mandatory verification"):
        promote_candidate(
            deployment=deployment,
            candidate_id=result.candidate.candidate_id,
            provider=CondaLockProvider(),
            verifier=CondaPrefixVerifier(),
            policy=VerificationPolicy(),
            context=_context(runner),
        )

    store = ManagedDeploymentStore(deployment.path)
    assert store.current_link_candidate_id() is None
    assert (
        store.read_candidate(result.candidate.candidate_id).status
        is CandidateStatus.VERIFICATION_FAILED
    )


def test_rollback_reverifies_the_previous_candidate(tmp_path: Path) -> None:
    locked, packages = _locked(tmp_path)
    deployment = EnvironmentTarget(tmp_path / "deployment", "managed-conda-deployment")
    runner = InstallingRunner(packages)

    candidate_ids: list[str] = []
    for _ in range(2):
        result = apply_locked_environment(
            lock=locked,
            driver=CondaPrefixDriver(),
            deployment=deployment,
            context=_context(runner),
        )
        assert result.candidate is not None
        candidate_ids.append(result.candidate.candidate_id)
        promote_candidate(
            deployment=deployment,
            candidate_id=result.candidate.candidate_id,
            provider=CondaLockProvider(),
            verifier=CondaPrefixVerifier(),
            policy=VerificationPolicy(),
            context=_context(runner),
        )

    previous = ManagedDeploymentStore(deployment.path).candidate(candidate_ids[0])
    record_path = next((previous.path / "conda-meta").glob("example-*.json"))
    document = json.loads(record_path.read_text())
    document["version"] = "9.9"
    record_path.write_text(json.dumps(document))

    with pytest.raises(RollbackFailed, match="mandatory verification"):
        rollback_deployment(
            deployment=deployment,
            provider=CondaLockProvider(),
            verifier=CondaPrefixVerifier(),
            policy=VerificationPolicy(),
            context=_context(runner),
        )

    current, history = deployment_status(deployment)
    assert current == candidate_ids[1]
    assert history == (candidate_ids[0],)


def test_promotion_rejects_a_tampered_archived_lock(tmp_path: Path) -> None:
    locked, packages = _locked(tmp_path)
    deployment = EnvironmentTarget(tmp_path / "deployment", "managed-conda-deployment")
    runner = InstallingRunner(packages)
    result = apply_locked_environment(
        lock=locked,
        driver=CondaPrefixDriver(),
        deployment=deployment,
        context=_context(runner),
    )
    assert result.candidate is not None

    store = ManagedDeploymentStore(deployment.path)
    record = store.read_candidate(result.candidate.candidate_id)
    archived = store.archived_lock_path(record.lock_id)
    archived.write_text("{}\n", encoding="utf-8")

    with pytest.raises(PromotionFailed):
        promote_candidate(
            deployment=deployment,
            candidate_id=result.candidate.candidate_id,
            provider=CondaLockProvider(),
            verifier=CondaPrefixVerifier(),
            policy=VerificationPolicy(),
            context=_context(runner),
        )

    assert store.current_link_candidate_id() is None


def test_failed_apply_is_recorded_and_candidate_is_removed(tmp_path: Path) -> None:
    locked, packages = _locked(tmp_path)
    deployment = EnvironmentTarget(tmp_path / "deployment", "managed-conda-deployment")
    runner = InstallingRunner(packages, fail_install=True)

    with pytest.raises(ApplyFailed, match="download failed"):
        apply_locked_environment(
            lock=locked,
            driver=CondaPrefixDriver(),
            deployment=deployment,
            context=_context(runner),
        )

    store = ManagedDeploymentStore(deployment.path)
    records = list(store.records_dir.glob("*.json"))
    assert len(records) == 1
    candidate_id = records[0].stem
    assert store.read_candidate(candidate_id).status is CandidateStatus.FAILED
    assert not store.candidate(candidate_id).path.exists()


def test_pending_switch_is_recovered_after_link_replacement(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("atomic symlink promotion is POSIX-only")
    root = tmp_path / "deployment"
    store = ManagedDeploymentStore(root)
    store.initialize()
    first = store.reserve_candidate()
    second = store.reserve_candidate()
    for candidate in (first, second):
        store.write_candidate(
            new_candidate_record(
                candidate,
                lock_id=f"sha256:{'a' * 64}",
                lock_format="depviz-conda-lock-v1",
                resolution_digest=f"sha256:{'b' * 64}",
            )
        )
        store.update_candidate(candidate.candidate_id, status=CandidateStatus.VERIFIED)
    store.switch_current_link(first.candidate_id)
    store.write_state(DeploymentState(current_candidate_id=first.candidate_id))
    next_state = DeploymentState(
        current_candidate_id=second.candidate_id,
        history=(first.candidate_id,),
    )
    store.write_pending(
        PendingSwitch(
            operation="promote",
            from_candidate_id=first.candidate_id,
            to_candidate_id=second.candidate_id,
            next_state=next_state,
        )
    )
    store.switch_current_link(second.candidate_id)

    current, history = deployment_status(EnvironmentTarget(root, "managed-conda-deployment"))

    assert current == second.candidate_id
    assert history == (first.candidate_id,)
    assert store.read_state().current_candidate_id == second.candidate_id
    assert not store.pending_path.exists()


def test_process_lock_times_out_when_already_held(tmp_path: Path) -> None:
    path = tmp_path / "operation.lock"
    with ProcessLock(path, timeout_seconds=0.1):
        with pytest.raises(ProcessLockTimeout):
            ProcessLock(path, timeout_seconds=0).acquire()
