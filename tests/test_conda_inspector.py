from __future__ import annotations

import json
from pathlib import Path

import pytest

from depviz.api import EnvironmentTarget, OperationContext
from depviz.api.errors import InspectionFailed
from depviz.builtin.conda import CondaPrefixInspector


def _write_record(prefix: Path, filename: str, record: dict[str, object]) -> None:
    metadata = prefix / "conda-meta"
    metadata.mkdir(parents=True, exist_ok=True)
    (metadata / filename).write_text(json.dumps(record), encoding="utf-8")


def test_conda_prefix_inspector_reads_exact_records(tmp_path: Path) -> None:
    prefix = tmp_path / "env"
    _write_record(
        prefix,
        "python-3.11.9-h123_0.json",
        {
            "name": "python",
            "version": "3.11.9",
            "build": "h123_0",
            "subdir": "linux-64",
            "channel": "https://conda.example/conda-forge/linux-64",
            "url": "https://conda.example/conda-forge/linux-64/python.conda",
            "sha256": "a" * 64,
            "depends": ["_libgcc_mutex 0.1 main"],
        },
    )
    _write_record(
        prefix,
        "_libgcc_mutex-0.1-main.json",
        {
            "name": "_libgcc_mutex",
            "version": "0.1",
            "build": "main",
            "subdir": "linux-64",
            "channel": "https://conda.example/conda-forge/linux-64",
            "url": "https://conda.example/conda-forge/linux-64/_libgcc_mutex.conda",
            "md5": "b" * 32,
            "depends": [],
        },
    )

    state = CondaPrefixInspector().inspect(
        EnvironmentTarget(prefix, "conda-prefix"),
        OperationContext(configuration={"conda.platform": "linux-64"}),
    )

    assert state.complete
    assert state.environment == EnvironmentTarget(prefix.resolve(), "conda-prefix")
    assert [package.name for package in state.packages] == ["_libgcc_mutex", "python"]
    assert state.packages[0].checksum == f"md5:{'b' * 32}"
    assert state.packages[1].dependencies[0].name == "_libgcc_mutex"


def test_conda_prefix_inspector_rejects_missing_source(tmp_path: Path) -> None:
    prefix = tmp_path / "env"
    _write_record(
        prefix,
        "broken.json",
        {
            "name": "broken",
            "version": "1.0",
            "build": "0",
            "subdir": "linux-64",
            "fn": "broken-1.0-0.conda",
            "depends": [],
        },
    )

    with pytest.raises(InspectionFailed, match="channel/source"):
        CondaPrefixInspector().inspect(
            EnvironmentTarget(prefix, "conda-prefix"),
            OperationContext(configuration={"conda.platform": "linux-64"}),
        )


def test_conda_prefix_inspector_rejects_platform_conflict(tmp_path: Path) -> None:
    prefix = tmp_path / "env"
    _write_record(
        prefix,
        "python.json",
        {
            "name": "python",
            "version": "3.11",
            "build": "0",
            "subdir": "osx-arm64",
            "channel": "conda-forge",
            "fn": "python.conda",
            "depends": [],
        },
    )

    with pytest.raises(InspectionFailed, match="conflicts"):
        CondaPrefixInspector().inspect(
            EnvironmentTarget(prefix, "conda-prefix"),
            OperationContext(configuration={"conda.platform": "linux-64"}),
        )


def test_conda_prefix_inspector_supports_legacy_platform_and_arch(tmp_path: Path) -> None:
    prefix = tmp_path / "env"
    _write_record(
        prefix,
        "legacy.json",
        {
            "name": "legacy",
            "version": "1.0",
            "build": "0",
            "platform": "linux",
            "arch": "x86_64",
            "channel": "conda-forge",
            "fn": "legacy-1.0-0.conda",
            "depends": [],
        },
    )

    state = CondaPrefixInspector().inspect(
        EnvironmentTarget(prefix, "conda-prefix"),
        OperationContext(),
    )

    assert state.target.platform == "linux-64"
    assert state.packages[0].platform == "linux-64"
