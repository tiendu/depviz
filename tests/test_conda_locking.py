from __future__ import annotations

import json
from pathlib import Path

import pytest

from depviz.api import OperationContext, Resolution, ResolutionStatus, ResolvedPackage, Target
from depviz.api.errors import LockFailed
from depviz.builtin.conda import CondaLockProvider


def _resolution(*, checksum: str | None = None) -> Resolution:
    package = ResolvedPackage(
        ecosystem="conda",
        name="python",
        version="3.12.3",
        build="h123_0",
        platform="linux-64",
        source="https://conda.example/conda-forge/linux-64",
        artifact="https://conda.example/conda-forge/linux-64/python-3.12.3-h123_0.conda",
        checksum=checksum,
    )
    return Resolution(
        requested=(),
        packages=(package,),
        target=Target("linux-64"),
        status=ResolutionStatus.COMPLETE,
    )


def test_conda_lock_round_trip_and_tamper_detection(tmp_path: Path) -> None:
    provider = CondaLockProvider()
    artifact = provider.create_lock(_resolution(checksum=f"sha256:{'a' * 64}"), OperationContext())
    path = tmp_path / "depviz-lock.json"
    path.write_bytes(artifact.content)

    locked = provider.read_lock(path, OperationContext())

    assert locked.resolution.packages[0].name == "python"
    assert locked.artifact.metadata["lock_id"].startswith("sha256:")

    document = json.loads(path.read_text())
    document["artifacts"][0]["version"] = "9.9"
    path.write_text(json.dumps(document))
    with pytest.raises(LockFailed, match="lock_id"):
        provider.read_lock(path, OperationContext())


def test_conda_lock_rejects_unhashed_package() -> None:
    with pytest.raises(LockFailed, match="checksum"):
        CondaLockProvider().create_lock(_resolution(checksum=None), OperationContext())


def test_conda_lock_rejects_embedded_credentials() -> None:
    resolution = _resolution(checksum=f"sha256:{'a' * 64}")
    package = resolution.packages[0]
    unsafe = ResolvedPackage(
        **{
            **package.__dict__,
            "artifact": "https://user:secret@conda.example/python.conda",
        }
    )
    resolution = Resolution(
        requested=(),
        packages=(unsafe,),
        target=resolution.target,
        status=ResolutionStatus.COMPLETE,
    )
    with pytest.raises(LockFailed, match="credentials"):
        CondaLockProvider().create_lock(resolution, OperationContext())


def test_conda_lock_rejects_artifact_query_parameters() -> None:
    resolution = _resolution(checksum=f"sha256:{'a' * 64}")
    package = resolution.packages[0]
    unsafe = ResolvedPackage(
        **{
            **package.__dict__,
            "artifact": "https://conda.example/python.conda?token=secret",
        }
    )
    resolution = Resolution(
        requested=(),
        packages=(unsafe,),
        target=resolution.target,
        status=ResolutionStatus.COMPLETE,
    )
    with pytest.raises(LockFailed, match="query parameters"):
        CondaLockProvider().create_lock(resolution, OperationContext())
