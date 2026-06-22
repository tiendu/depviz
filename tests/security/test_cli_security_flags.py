from __future__ import annotations

from pathlib import Path

import pytest

from depviz.api import Resolution, ResolutionStatus, ResolvedPackage, Target
from depviz.cli.commands import run_command
from depviz.cli.exit_codes import ExitCode
from depviz.cli.parser import parse_args
from depviz.cli.services import ApplicationServices
from depviz.core.resolution import write_resolution_json
from depviz.infrastructure.commands import LocalCommandRunner
from depviz.plugins.defaults import create_default_registry

pytestmark = pytest.mark.security


def test_weak_checksum_override_is_explicit_on_lock_and_runtime_commands() -> None:
    lock = parse_args(
        [
            "lock",
            "resolution.json",
            "--output",
            "lock.json",
            "--allow-weak-checksum",
        ]
    )
    assert lock.allow_weak_checksum is True

    apply = parse_args(
        [
            "apply",
            "lock.json",
            "--deployment",
            "deployment",
            "--allow-weak-checksum",
        ]
    )
    assert apply.allow_weak_checksum is True


def test_weak_checksum_override_is_disabled_by_default() -> None:
    args = parse_args(["apply", "lock.json", "--deployment", "deployment"])
    assert args.allow_weak_checksum is False


def test_lock_cli_requires_explicit_override_for_md5(tmp_path: Path) -> None:
    resolution = Resolution(
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
                checksum=f"md5:{'a' * 32}",
            ),
        ),
        target=Target("linux-64"),
        status=ResolutionStatus.COMPLETE,
    )
    resolution_path = tmp_path / "resolution.json"
    lock_path = tmp_path / "lock.json"
    write_resolution_json(resolution_path, resolution)
    services = ApplicationServices(
        registry=create_default_registry(discover_external=False),
        command_runner=LocalCommandRunner(),
    )

    rejected = run_command(
        parse_args(["lock", str(resolution_path), "--output", str(lock_path)]),
        services,
    )
    assert rejected is ExitCode.LOCK_FAILED
    assert not lock_path.exists()

    accepted = run_command(
        parse_args(
            [
                "lock",
                str(resolution_path),
                "--output",
                str(lock_path),
                "--allow-weak-checksum",
            ]
        ),
        services,
    )
    assert accepted is ExitCode.OK
    assert lock_path.is_file()
