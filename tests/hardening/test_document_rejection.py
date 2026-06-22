from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from depviz.api import (
    EnvironmentState,
    OperationContext,
    Resolution,
    ResolutionStatus,
    ResolvedPackage,
    Target,
)
from depviz.builtin.conda import CondaLockProvider
from depviz.api.errors import LockFailed
from depviz.core.planning import build_change_plan, plan_from_dict, plan_to_dict
from depviz.core.resolution import resolution_from_dict, resolution_to_dict

pytestmark = pytest.mark.hardening


def _package(version: str = "1.0") -> ResolvedPackage:
    return ResolvedPackage(
        ecosystem="conda",
        name="demo",
        version=version,
        build="0",
        platform="linux-64",
        source="https://conda.example/linux-64",
        artifact=f"https://conda.example/linux-64/demo-{version}-0.conda",
        checksum=f"sha256:{'a' * 64}",
    )


def _resolution() -> Resolution:
    return Resolution(
        requested=(),
        packages=(_package(),),
        target=Target("linux-64"),
        status=ResolutionStatus.COMPLETE,
    )


def test_resolution_rejects_unknown_and_future_fields() -> None:
    document = resolution_to_dict(_resolution())
    document["unknown"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        resolution_from_dict(document)

    document = resolution_to_dict(_resolution())
    document["schema_version"] = 999
    with pytest.raises(ValueError, match="Unsupported resolution schema"):
        resolution_from_dict(document)


def test_plan_rejects_unknown_fields(tmp_path: Path) -> None:
    manifest = tmp_path / "environment.yml"
    manifest.write_text("dependencies: [demo]\n")
    plan = build_change_plan(
        manifest=manifest,
        current=EnvironmentState(
            packages=(_package("0.9"),),
            target=Target("linux-64"),
            complete=True,
        ),
        desired=_resolution(),
        created_at=datetime(2026, 6, 22, tzinfo=UTC),
    )
    document = plan_to_dict(plan)
    document["unknown"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        plan_from_dict(document)


def test_lock_rejects_unknown_fields(tmp_path: Path) -> None:
    provider = CondaLockProvider()
    artifact = provider.create_lock(_resolution(), OperationContext())
    document = json.loads(artifact.content)
    document["unknown"] = True
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(LockFailed, match="unknown fields"):
        provider.read_lock(path, OperationContext())


def test_plan_and_lock_reject_invalid_timestamps(tmp_path: Path) -> None:
    manifest = tmp_path / "environment.yml"
    manifest.write_text("dependencies: [demo]\n")
    plan = build_change_plan(
        manifest=manifest,
        current=EnvironmentState(
            packages=(_package("0.9"),),
            target=Target("linux-64"),
            complete=True,
        ),
        desired=_resolution(),
        created_at=datetime(2026, 6, 22, tzinfo=UTC),
    )
    plan_document = plan_to_dict(plan)
    plan_document["created_at"] = "not-a-time"
    with pytest.raises(ValueError, match="ISO-8601"):
        plan_from_dict(plan_document)

    provider = CondaLockProvider()
    lock_document = json.loads(provider.create_lock(_resolution(), OperationContext()).content)
    lock_document["created_at"] = "2026-06-22T00:00:00"
    path = tmp_path / "lock-invalid-time.json"
    path.write_text(json.dumps(lock_document), encoding="utf-8")
    with pytest.raises(LockFailed, match="timezone"):
        provider.read_lock(path, OperationContext())
