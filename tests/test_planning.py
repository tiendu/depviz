from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from depviz.analysis.diff import diff_environment
from depviz.api import (
    BackendPayload,
    ChangeAspect,
    ChangeKind,
    EnvironmentState,
    EnvironmentTarget,
    Resolution,
    ResolutionStatus,
    ResolvedPackage,
    Target,
    VersionDirection,
)
from depviz.core.planning import build_change_plan, plan_from_dict, plan_to_dict


def _package(
    name: str,
    version: str,
    *,
    build: str = "0",
    source: str = "https://conda.example/linux-64",
    checksum: str | None = None,
) -> ResolvedPackage:
    return ResolvedPackage(
        ecosystem="conda",
        name=name,
        version=version,
        build=build,
        platform="linux-64",
        source=source,
        artifact=f"https://conda.example/linux-64/{name}-{version}-{build}.conda",
        checksum=checksum or f"sha256:{'a' * 64}",
    )


def test_diff_classifies_install_remove_rebuild_and_upgrade() -> None:
    current = EnvironmentState(
        packages=(
            _package("python", "3.11.0"),
            _package("openssl", "3.0.0", build="old"),
            _package("remove-me", "1.0"),
        ),
        target=Target("linux-64"),
        complete=True,
    )
    desired = Resolution(
        requested=(),
        packages=(
            _package("python", "3.12.0"),
            _package("openssl", "3.0.0", build="new"),
            _package("new-package", "1.0"),
        ),
        target=Target("linux-64"),
        status=ResolutionStatus.COMPLETE,
    )

    changes = {change.name: change for change in diff_environment(current, desired)}

    assert changes["python"].kind is ChangeKind.MODIFY
    assert changes["python"].version_direction is VersionDirection.UPGRADE
    assert changes["openssl"].aspects == frozenset({ChangeAspect.BUILD, ChangeAspect.ARTIFACT})
    assert changes["remove-me"].kind is ChangeKind.REMOVE
    assert changes["new-package"].kind is ChangeKind.INSTALL


def test_complex_conda_version_direction_is_unknown() -> None:
    current = EnvironmentState(
        packages=(_package("tool", "1.0rc1"),),
        target=Target("linux-64"),
        complete=True,
    )
    desired = Resolution(
        requested=(),
        packages=(_package("tool", "1.0"),),
        target=Target("linux-64"),
        status=ResolutionStatus.COMPLETE,
    )

    change = diff_environment(current, desired)[0]

    assert change.version_direction is VersionDirection.UNKNOWN


def test_plan_is_content_bound_and_detects_tampering(tmp_path: Path) -> None:
    manifest = tmp_path / "environment.yml"
    manifest.write_text("channels: [conda-forge]\ndependencies: [python=3.12]\n")
    current = EnvironmentState(
        packages=(_package("python", "3.11.0"),),
        target=Target("linux-64"),
        complete=True,
        environment=EnvironmentTarget(tmp_path / "env", "conda-prefix"),
    )
    desired = Resolution(
        requested=(),
        packages=(_package("python", "3.12.0"),),
        target=Target("linux-64"),
        status=ResolutionStatus.COMPLETE,
        native_payload=BackendPayload(schema="test", data={"transaction": "exact"}),
    )
    plan = build_change_plan(
        manifest=manifest,
        current=current,
        desired=desired,
        created_at=datetime(2026, 6, 22, tzinfo=UTC),
    )

    assert plan.plan_id.startswith("sha256:")
    assert any(finding.code == "policy.runtime-change" for finding in plan.policy_findings)
    assert plan_from_dict(plan_to_dict(plan)) == plan

    tampered = json.loads(json.dumps(plan_to_dict(plan)))
    tampered["operations"][0]["name"] = "tampered"
    with pytest.raises(ValueError, match="plan_id"):
        plan_from_dict(tampered)
