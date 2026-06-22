from __future__ import annotations

from pathlib import Path

import pytest

from depviz.cli.commands import run_command
from depviz.cli.exit_codes import ExitCode
from depviz.cli.parser import parse_args
from depviz.cli.services import ApplicationServices
from depviz.infrastructure import LocalCommandRunner
from depviz.plugins.defaults import create_default_registry

pytestmark = pytest.mark.hardening


@pytest.fixture
def services() -> ApplicationServices:
    return ApplicationServices(
        registry=create_default_registry(discover_external=False),
        command_runner=LocalCommandRunner(),
    )


def test_exit_code_values_are_stable_and_unique() -> None:
    expected = {
        "OK": 0,
        "INVALID_INPUT": 1,
        "UNSUPPORTED_MANIFEST": 2,
        "INSPECTION_FAILED": 3,
        "INCOMPLETE": 4,
        "TOOL_UNAVAILABLE": 5,
        "RESOLUTION_FAILED": 6,
        "PLAN_REJECTED": 7,
        "LOCK_FAILED": 8,
        "APPLY_FAILED": 9,
        "VERIFICATION_FAILED": 10,
        "PROMOTION_FAILED": 11,
        "ROLLBACK_FAILED": 12,
        "MAINTENANCE_FAILED": 13,
    }
    assert {item.name: item.value for item in ExitCode} == expected
    assert len({item.value for item in ExitCode}) == len(ExitCode)


@pytest.mark.parametrize(
    "argv",
    [
        ["inspect", "missing.yml"],
        ["resolve", "missing.yml"],
        ["plan", "missing.yml", "--empty"],
        ["lock", "missing-resolution.json", "--output", "out.json"],
        ["apply", "missing-lock.json", "--deployment", "deployment"],
        [
            "verify",
            "missing-lock.json",
            "--deployment",
            "deployment",
            "--candidate",
            "c-missing",
        ],
    ],
)
def test_missing_input_paths_return_invalid_input(
    argv: list[str], services: ApplicationServices
) -> None:
    assert run_command(parse_args(argv), services) == ExitCode.INVALID_INPUT


def test_corrupt_status_state_returns_promotion_failure(
    tmp_path: Path, services: ApplicationServices
) -> None:
    deployment = tmp_path / "deployment"
    metadata = deployment / ".depviz"
    metadata.mkdir(parents=True)
    (metadata / "deployment.json").write_text("{broken", encoding="utf-8")

    assert (
        run_command(parse_args(["status", "--deployment", str(deployment)]), services)
        == ExitCode.PROMOTION_FAILED
    )


def test_unknown_doctor_plugin_returns_maintenance_failure(
    services: ApplicationServices,
) -> None:
    assert (
        run_command(parse_args(["doctor", "--plugin", "does-not-exist"]), services)
        == ExitCode.MAINTENANCE_FAILED
    )


def test_gc_invalid_arguments_return_invalid_input(services: ApplicationServices) -> None:
    assert (
        run_command(parse_args(["gc", "--deployment", "deployment", "--keep", "-1"]), services)
        == ExitCode.INVALID_INPUT
    )
