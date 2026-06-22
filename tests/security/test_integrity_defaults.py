from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from depviz.api import OperationContext, Resolution, ResolutionStatus, ResolvedPackage, Target
from depviz.api.errors import LockFailed
from depviz.builtin.conda import CondaLockProvider
from depviz.core.doctor import run_doctor
from depviz.core.resolution import read_resolution_json
from depviz.infrastructure.deployment import ManagedDeploymentStore
from depviz.infrastructure.process_locks import ProcessLock
from depviz.plugins.registry import PluginRegistry
from depviz.infrastructure.storage import (
    DEFAULT_MAX_DOCUMENT_BYTES,
    read_bytes_limited,
    write_bytes_atomic,
)

pytestmark = pytest.mark.security


def _resolution(checksum: str) -> Resolution:
    return Resolution(
        requested=(),
        packages=(
            ResolvedPackage(
                ecosystem="conda",
                name="python",
                version="3.12.3",
                build="h123_0",
                platform="linux-64",
                source="https://conda.example/linux-64",
                artifact="https://conda.example/linux-64/python-3.12.3-h123_0.conda",
                checksum=checksum,
            ),
        ),
        target=Target("linux-64"),
        status=ResolutionStatus.COMPLETE,
    )


def _weak_context() -> OperationContext:
    return OperationContext(configuration={"security.allow_weak_checksums": "true"})


def test_conda_lock_requires_sha256_by_default_but_can_read_legacy_md5(
    tmp_path: Path,
) -> None:
    provider = CondaLockProvider()
    resolution = _resolution(f"md5:{'a' * 32}")

    with pytest.raises(LockFailed, match="SHA-256 is required"):
        provider.create_lock(resolution, OperationContext())

    legacy = provider.create_lock(resolution, _weak_context())
    path = tmp_path / "legacy-md5-lock.json"
    path.write_bytes(legacy.content)

    with pytest.raises(LockFailed, match="SHA-256 is required"):
        provider.read_lock(path, OperationContext())
    assert (
        provider.read_lock(path, _weak_context()).resolution.packages[0].checksum.startswith("md5:")
    )


def test_atomic_documents_are_owner_only_and_symlink_destinations_are_rejected(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state.json"
    write_bytes_atomic(target, b"{}\n")
    if os.name == "posix":
        assert target.stat().st_mode & 0o777 == 0o600

    victim = tmp_path / "victim.json"
    victim.write_bytes(b"unchanged\n")
    link = tmp_path / "link.json"
    link.symlink_to(victim)
    with pytest.raises(ValueError, match="symlink destination"):
        write_bytes_atomic(link, b"attacker-controlled\n")
    assert victim.read_bytes() == b"unchanged\n"


def test_managed_metadata_directories_are_private(tmp_path: Path) -> None:
    store = ManagedDeploymentStore(tmp_path / "deployment")
    store.initialize()
    if os.name == "posix":
        for directory in (
            store.environments_dir,
            store.metadata_dir,
            store.records_dir,
            store.locks_dir,
        ):
            assert directory.stat().st_mode & 0o777 == 0o700


def test_group_world_writable_deployment_root_is_rejected(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX permission bits required")
    root = tmp_path / "deployment"
    root.mkdir(mode=0o777)
    root.chmod(0o777)
    with pytest.raises(ValueError, match="group/world writable"):
        ManagedDeploymentStore(root).initialize()


def test_symlink_deployment_root_and_process_lock_are_rejected(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    deployment_link = tmp_path / "deployment"
    deployment_link.symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError, match="cannot be a symlink"):
        ManagedDeploymentStore(deployment_link).initialize()

    lock_target = tmp_path / "real.lock"
    lock_target.touch()
    lock_link = tmp_path / "operation.lock"
    lock_link.symlink_to(lock_target)
    with pytest.raises(ValueError, match="symlink process lock"):
        ProcessLock(lock_link).acquire()


def test_persistent_document_reads_are_bounded(tmp_path: Path) -> None:
    oversized = tmp_path / "resolution.json"
    oversized.write_bytes(b"{" + b" " * DEFAULT_MAX_DOCUMENT_BYTES + b"}")
    with pytest.raises(ValueError, match="safety limit"):
        read_resolution_json(oversized)
    with pytest.raises(ValueError, match="safety limit"):
        read_bytes_limited(oversized, label="test document")


def test_archived_lock_size_is_bounded(tmp_path: Path) -> None:
    store = ManagedDeploymentStore(tmp_path / "deployment")
    store.initialize()
    with pytest.raises(ValueError, match="safety limit"):
        store.archive_lock(
            f"sha256:{'a' * 64}",
            b"x" * (DEFAULT_MAX_DOCUMENT_BYTES + 1),
        )


def test_weak_checksum_configuration_rejects_ambiguous_values() -> None:
    with pytest.raises(LockFailed, match="must be true or false"):
        CondaLockProvider().create_lock(
            _resolution(f"sha256:{'a' * 64}"),
            OperationContext(configuration={"security.allow_weak_checksums": "sometimes"}),
        )


def test_lock_document_does_not_persist_weak_override(tmp_path: Path) -> None:
    artifact = CondaLockProvider().create_lock(
        _resolution(f"md5:{'a' * 32}"),
        _weak_context(),
    )
    document = json.loads(artifact.content)
    assert "allow_weak" not in json.dumps(document)


def test_bounded_reader_rejects_symlink_documents(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_bytes(b"{}")
    link = tmp_path / "document.json"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        read_bytes_limited(link, label="test document")


def test_process_lock_rejects_symlink_parent(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError, match="process-lock directory"):
        ProcessLock(linked_parent / "operation.lock").acquire()


def test_doctor_reports_unsafe_deployment_permissions(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX permission bits required")
    store = ManagedDeploymentStore(tmp_path / "deployment")
    store.initialize()
    store.metadata_dir.chmod(0o777)

    report = run_doctor(PluginRegistry(), deployment=store.root)

    assert not report.passed
    assert any(
        finding.code == "doctor.deployment.unreadable" and "group/world writable" in finding.message
        for finding in report.findings
    )
