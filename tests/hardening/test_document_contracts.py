from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from depviz.api import (
    BackendPayload,
    CandidateEnvironment,
    EnvironmentState,
    EnvironmentTarget,
    OperationContext,
    Resolution,
    ResolutionStatus,
    ResolvedPackage,
    Target,
    VerificationReport,
)
from depviz.builtin.conda import CondaLockProvider
from depviz.builtin.mixed import CondaPipLockProvider
from depviz.builtin.python import PythonLockProvider
from depviz.core.planning import build_change_plan, plan_from_dict, plan_to_dict
from depviz.core.resolution import resolution_to_dict
from depviz.core.verification import verification_report_to_dict
from depviz.infrastructure.deployment import (
    DeploymentState,
    ManagedDeploymentStore,
    PendingSwitch,
    new_candidate_record,
)

pytestmark = __import__("pytest").mark.hardening


def _conda_package(name: str = "python", version: str = "3.12.3") -> ResolvedPackage:
    return ResolvedPackage(
        ecosystem="conda",
        name=name,
        version=version,
        build="h123_0",
        platform="linux-64",
        source="https://conda.example/conda-forge/linux-64",
        artifact=f"https://conda.example/conda-forge/linux-64/{name}-{version}-h123_0.conda",
        checksum=f"sha256:{'a' * 64}",
    )


def _conda_resolution() -> Resolution:
    return Resolution(
        requested=(),
        packages=(_conda_package(),),
        target=Target("linux-64"),
        status=ResolutionStatus.COMPLETE,
        native_payload=BackendPayload(schema="test.conda", data={"transaction": "exact"}),
    )


def _python_resolution() -> Resolution:
    platform = "python-cpython-3.12.3-linux-x86_64-cpython-312-x86_64-linux-gnu"
    return Resolution(
        requested=(),
        packages=(
            ResolvedPackage(
                ecosystem="pypi",
                name="demo",
                version="1.0.0",
                platform=platform,
                source="https://pypi.org/simple",
                artifact="https://files.pythonhosted.org/demo-1.0.0-py3-none-any.whl",
                checksum=f"sha256:{'b' * 64}",
            ),
        ),
        target=Target(platform, python_version="3.12.3", implementation="cpython"),
        status=ResolutionStatus.COMPLETE,
        native_payload=BackendPayload(
            schema="depviz.uv-lock.native.v1",
            data={
                "tool": "uv",
                "tool_version": "0.10.0",
                "interpreter": {
                    "implementation": "cpython",
                    "version": "3.12.3",
                    "major": 3,
                    "minor": 12,
                    "platform": "linux-x86_64",
                    "soabi": "cpython-312-x86_64-linux-gnu",
                    "executable": "/usr/bin/python3.12",
                },
                "uv_lock": {},
            },
        ),
    )


def _mixed_resolution() -> Resolution:
    conda = _conda_resolution()
    python = _python_resolution()
    return Resolution(
        requested=(),
        packages=tuple(
            sorted(
                (*conda.packages, *python.packages),
                key=lambda package: (package.ecosystem, package.name),
            )
        ),
        target=conda.target,
        status=ResolutionStatus.COMPLETE,
        native_payload=BackendPayload(
            schema="depviz.conda-pip.resolution.v1",
            data={
                "tool": "conda+uv",
                "tool_version": "micromamba 2.1.0; uv 0.10.0",
                "conda_resolution": resolution_to_dict(conda),
                "python_resolution": resolution_to_dict(python),
                "python_runtime": {
                    "version": "3.12.3",
                    "implementation": "cpython",
                    "conda_platform": "linux-64",
                    "uv_platform": "x86_64-unknown-linux-gnu",
                },
                "ownership": {"policy": "pip-last", "pip_overrides": []},
            },
        ),
    )


def _schema(name: str) -> dict[str, object]:
    raw = files("depviz.schemas").joinpath(name).read_text(encoding="utf-8")
    value = json.loads(raw)
    assert isinstance(value, dict)
    return value


def _validate(schema_name: str, document: dict[str, object]) -> None:
    schema = _schema(schema_name)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(document)


def test_every_published_schema_is_valid() -> None:
    schema_root = files("depviz.schemas")
    names = sorted(item.name for item in schema_root.iterdir() if item.name.endswith(".json"))
    assert names
    for name in names:
        Draft202012Validator.check_schema(_schema(name))


def test_generated_documents_match_their_published_schemas(tmp_path: Path) -> None:
    resolution = _conda_resolution()
    _validate("resolution-v1.schema.json", resolution_to_dict(resolution))

    manifest = tmp_path / "environment.yml"
    manifest.write_text("channels: [conda-forge]\ndependencies: [python=3.12]\n")
    current = EnvironmentState(
        packages=(_conda_package(version="3.11.9"),),
        target=Target("linux-64"),
        complete=True,
        environment=EnvironmentTarget(tmp_path / "current", "conda-prefix"),
    )
    plan = build_change_plan(
        manifest=manifest,
        current=current,
        desired=resolution,
        created_at=datetime(2026, 6, 22, tzinfo=UTC),
    )
    _validate("plan-v1.schema.json", plan_to_dict(plan))

    conda_lock = CondaLockProvider().create_lock(resolution, OperationContext())
    conda_document = json.loads(conda_lock.content)
    assert isinstance(conda_document, dict)
    _validate("lock-v1.schema.json", conda_document)

    python_lock = PythonLockProvider().create_lock(_python_resolution(), OperationContext())
    python_document = json.loads(python_lock.content)
    assert isinstance(python_document, dict)
    _validate("python-lock-v1.schema.json", python_document)

    mixed_lock = CondaPipLockProvider().create_lock(_mixed_resolution(), OperationContext())
    mixed_document = json.loads(mixed_lock.content)
    assert isinstance(mixed_document, dict)
    _validate("conda-pip-lock-v1.schema.json", mixed_document)

    store = ManagedDeploymentStore(tmp_path / "deployment")
    store.initialize()
    candidate = store.candidate("c-schema", kind="conda-prefix")
    candidate.path.mkdir()
    record = new_candidate_record(
        candidate,
        lock_id=str(conda_lock.metadata["lock_id"]),
        lock_format=conda_lock.format,
        resolution_digest=str(conda_lock.metadata["resolution_digest"]),
    )
    store.write_candidate(record)
    candidate_document = json.loads(
        (store.records_dir / "c-schema.json").read_text(encoding="utf-8")
    )
    _validate("candidate-v1.schema.json", candidate_document)

    state = DeploymentState(
        current_candidate_id="c-schema",
        history=("c-old",),
        updated_at="2026-06-22T00:00:00+00:00",
    )
    store.write_state(state)
    deployment_document = json.loads(store.state_path.read_text(encoding="utf-8"))
    _validate("deployment-v1.schema.json", deployment_document)

    pending = PendingSwitch(
        operation="promote",
        from_candidate_id=None,
        to_candidate_id="c-schema",
        next_state=state,
    )
    store.write_pending(pending)
    pending_document = json.loads(store.pending_path.read_text(encoding="utf-8"))
    _validate("pending-switch-v1.schema.json", pending_document)

    verification = verification_report_to_dict(
        candidate=CandidateEnvironment(
            target=EnvironmentTarget(store.root, "managed-conda-deployment"),
            candidate_id="c-schema",
            path=candidate.path,
            kind="conda-prefix",
        ),
        lock_id=str(conda_lock.metadata["lock_id"]),
        report=VerificationReport(
            passed=True,
            expected_state_digest=f"sha256:{'c' * 64}",
            observed_state_digest=f"sha256:{'c' * 64}",
        ),
    )
    _validate("verification-v1.schema.json", verification)


def test_plan_identity_is_deterministic_for_identical_documents(tmp_path: Path) -> None:
    manifest = tmp_path / "environment.yml"
    manifest.write_text("dependencies: [python=3.12]\n")
    current = EnvironmentState(
        packages=(_conda_package(version="3.11.9"),),
        target=Target("linux-64"),
        complete=True,
    )
    desired = _conda_resolution()
    timestamp = datetime(2026, 6, 22, tzinfo=UTC)
    first = build_change_plan(
        manifest=manifest,
        current=current,
        desired=desired,
        created_at=timestamp,
    )
    second = build_change_plan(
        manifest=manifest,
        current=current,
        desired=desired,
        created_at=timestamp,
    )
    later = build_change_plan(
        manifest=manifest,
        current=current,
        desired=desired,
        created_at=timestamp + timedelta(days=1),
    )

    assert first == second
    assert plan_to_dict(first) == plan_to_dict(second)
    assert first.plan_id != later.plan_id
    assert plan_from_dict(plan_to_dict(first)) == first


def test_lock_ids_bind_the_complete_document() -> None:
    from depviz.core.resolution import digest_json

    for provider, resolution in [
        (CondaLockProvider(), _conda_resolution()),
        (PythonLockProvider(), _python_resolution()),
    ]:
        artifact = provider.create_lock(resolution, OperationContext())
        document = json.loads(artifact.content)
        lock_id = document.pop("lock_id")
        assert lock_id == digest_json(document)
        assert artifact.metadata["resolution_digest"] == document["resolution_digest"]
