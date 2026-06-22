from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from depviz.api import (
    Command,
    CommandResult,
    DependencyIntent,
    OperationContext,
    Requirement,
    ResolutionStatus,
    Target,
)
from depviz.api.errors import ResolutionFailed, ToolUnavailable
from depviz.builtin.conda import CondaDryRunResolver


class ScriptedRunner:
    def __init__(self, steps: list[Mapping[str, object]]) -> None:
        self.steps = list(steps)
        self.commands: list[Command] = []

    def run(
        self,
        command: Command,
        *,
        timeout_seconds: float,
        output_limit: int,
        redact: tuple[str, ...] = (),
    ) -> CommandResult:
        del timeout_seconds, output_limit, redact
        self.commands.append(command)
        if not self.steps:
            raise AssertionError("Unexpected command")
        step = self.steps.pop(0)
        error = step.get("raise")
        if isinstance(error, BaseException):
            raise error
        stdout = str(step.get("stdout", ""))
        if "{prefix}" in stdout:
            prefix = command.argv[command.argv.index("--prefix") + 1]
            stdout = stdout.replace("{prefix}", prefix)
        return CommandResult(
            argv=command.argv,
            returncode=int(step.get("returncode", 0)),
            stdout=stdout,
            stderr=str(step.get("stderr", "")),
            duration_seconds=0.01,
            timed_out=bool(step.get("timed_out", False)),
            output_truncated=bool(step.get("output_truncated", False)),
        )


def _intent(
    *requirements: Requirement, channels: tuple[str, ...] = ("conda-forge",)
) -> DependencyIntent:
    return DependencyIntent(requirements=requirements, channels=channels)


def _solver_payload() -> str:
    return json.dumps(
        {
            "success": True,
            "dry_run": True,
            "prefix": "{prefix}",
            "actions": {
                "FETCH": [
                    {
                        "name": "_libgcc_mutex",
                        "version": "0.1",
                        "build": "conda_forge",
                        "subdir": "linux-64",
                        "channel": "conda-forge",
                        "url": (
                            "https://conda.anaconda.org/conda-forge/linux-64/"
                            "_libgcc_mutex-0.1-conda_forge.tar.bz2"
                        ),
                        "sha256": "abc123",
                        "depends": ["__glibc >=2.17,<3.0.a0"],
                    },
                    {
                        "name": "python",
                        "version": "3.11.9",
                        "build": "h123_0_cpython",
                        "subdir": "linux-64",
                        "channel": "conda-forge",
                        "fn": "python-3.11.9-h123_0_cpython.conda",
                        "md5": "deadbeef",
                        "depends": ["libgcc-ng >=12", "openssl >=3.0.0"],
                    },
                ],
                "LINK": [
                    {
                        "name": "python",
                        "version": "3.11.9",
                        "build_string": "h123_0_cpython",
                        "channel": "conda-forge",
                        "platform": "linux-64",
                        "dist_name": "python-3.11.9-h123_0_cpython",
                    },
                    {
                        "name": "_libgcc_mutex",
                        "version": "0.1",
                        "build": "conda_forge",
                        "channel": "conda-forge",
                        "subdir": "linux-64",
                        "dist_name": "_libgcc_mutex-0.1-conda_forge",
                    },
                ],
            },
        }
    )


def _context(runner: ScriptedRunner, **configuration: str) -> OperationContext:
    values = {"conda.tool": "micromamba", **configuration}
    return OperationContext(
        command_runner=runner,
        working_directory=Path("/project"),
        configuration=values,
    )


def test_micromamba_resolver_returns_exact_normalized_packages() -> None:
    runner = ScriptedRunner(
        [
            {"stdout": "2.1.0\n"},
            {"stdout": _solver_payload()},
        ]
    )

    resolution = CondaDryRunResolver().resolve(
        _intent(Requirement(ecosystem="conda", name="python", specifier="=3.11")),
        Target(platform="linux-64"),
        None,
        _context(runner),
    )

    assert resolution.status is ResolutionStatus.COMPLETE
    assert [package.name for package in resolution.packages] == ["_libgcc_mutex", "python"]
    mutex = resolution.packages[0]
    assert mutex.checksum == "sha256:abc123"
    assert mutex.dependencies[0].name == "__glibc"
    python = resolution.packages[1]
    assert python.build == "h123_0_cpython"
    assert python.checksum == "md5:deadbeef"
    assert python.artifact == "python-3.11.9-h123_0_cpython.conda"
    assert resolution.native_payload is not None
    transaction = resolution.native_payload.data["transaction"]
    assert isinstance(transaction, dict)
    assert transaction["prefix"] == "***"

    command = runner.commands[1]
    assert command.argv[:4] == ("micromamba", "create", "--dry-run", "--json")
    assert ("--platform", "linux-64") == (
        command.argv[command.argv.index("--platform")],
        command.argv[command.argv.index("--platform") + 1],
    )
    assert "--override-channels" in command.argv
    assert "--strict-channel-priority" in command.argv
    assert command.argv[-1] == "python=3.11"
    assert command.environment is not None
    assert command.environment["CONDARC"].endswith("empty-condarc.yml")
    assert command.environment["MAMBARC"].endswith("empty-condarc.yml")
    assert command.environment["MAMBA_ROOT_PREFIX"].endswith("mamba-root")


def test_conda_tool_uses_subdir_and_explicit_solver() -> None:
    runner = ScriptedRunner([{"stdout": "conda 26.5.3\n"}, {"stdout": _solver_payload()}])

    CondaDryRunResolver().resolve(
        _intent(Requirement(ecosystem="conda", name="python")),
        Target(platform="osx-arm64"),
        None,
        _context(runner, **{"conda.tool": "conda", "conda.solver": "libmamba"}),
    )

    command = runner.commands[1].argv
    assert "--subdir" in command
    assert command[command.index("--subdir") + 1] == "osx-arm64"
    assert command[command.index("--solver") + 1] == "libmamba"
    assert "--no-default-packages" in command
    assert "--no-pin" in command


def test_channel_qualified_requirement_adds_explicit_channel() -> None:
    runner = ScriptedRunner([{"stdout": "2.1.0\n"}, {"stdout": _solver_payload()}])

    CondaDryRunResolver().resolve(
        _intent(
            Requirement(
                ecosystem="conda",
                name="samtools",
                specifier=">=1.20",
                source="bioconda",
            ),
            channels=("conda-forge",),
        ),
        Target(platform="linux-64"),
        None,
        _context(runner),
    )

    command = runner.commands[1].argv
    channel_values = [
        command[index + 1] for index, value in enumerate(command) if value == "--channel"
    ]
    assert channel_values == ["conda-forge", "bioconda"]
    assert command[-1] == "bioconda::samtools>=1.20"


def test_resolver_rejects_mixed_pip_requirements_before_running_tool() -> None:
    runner = ScriptedRunner([])

    with pytest.raises(ResolutionFailed, match="unsupported ecosystems: pypi"):
        CondaDryRunResolver().resolve(
            _intent(Requirement(ecosystem="pypi", name="numpy")),
            Target(platform="linux-64"),
            None,
            _context(runner),
        )

    assert runner.commands == []


def test_resolver_rejects_ambient_channels() -> None:
    runner = ScriptedRunner([])

    with pytest.raises(ResolutionFailed, match="No Conda channels were declared"):
        CondaDryRunResolver().resolve(
            _intent(Requirement(ecosystem="conda", name="python"), channels=()),
            Target(platform="linux-64"),
            None,
            _context(runner),
        )


def test_resolver_reports_solver_failure_details() -> None:
    runner = ScriptedRunner(
        [
            {"stdout": "2.1.0\n"},
            {
                "returncode": 1,
                "stdout": json.dumps(
                    {
                        "success": False,
                        "error": "Could not solve",
                        "solver_problems": ["package missing"],
                    }
                ),
            },
        ]
    )

    with pytest.raises(ResolutionFailed, match="Could not solve; package missing"):
        CondaDryRunResolver().resolve(
            _intent(Requirement(ecosystem="conda", name="missing")),
            Target(platform="linux-64"),
            None,
            _context(runner),
        )


def test_resolver_rejects_truncated_solver_output() -> None:
    runner = ScriptedRunner(
        [
            {"stdout": "2.1.0\n"},
            {"stdout": _solver_payload(), "output_truncated": True},
        ]
    )

    with pytest.raises(ResolutionFailed, match="partial transaction"):
        CondaDryRunResolver().resolve(
            _intent(Requirement(ecosystem="conda", name="python")),
            Target(platform="linux-64"),
            None,
            _context(runner),
        )


def test_resolver_rejects_non_json_output() -> None:
    runner = ScriptedRunner([{"stdout": "2.1.0\n"}, {"stdout": "not-json"}])

    with pytest.raises(ResolutionFailed, match="valid JSON"):
        CondaDryRunResolver().resolve(
            _intent(Requirement(ecosystem="conda", name="python")),
            Target(platform="linux-64"),
            None,
            _context(runner),
        )


def test_resolver_reports_missing_executable() -> None:
    runner = ScriptedRunner([{"raise": FileNotFoundError("missing")}])

    with pytest.raises(ToolUnavailable, match="Executable not found"):
        CondaDryRunResolver().resolve(
            _intent(Requirement(ecosystem="conda", name="python")),
            Target(platform="linux-64"),
            None,
            _context(runner),
        )


def test_private_channel_credentials_are_not_persisted() -> None:
    private_channel = "https://user:secret@example.invalid/conda"
    payload = json.loads(_solver_payload())
    payload["actions"]["FETCH"][0]["channel"] = private_channel  # type: ignore[index]
    payload["actions"]["FETCH"][0]["url"] = (  # type: ignore[index]
        f"{private_channel}/linux-64/_libgcc_mutex-0.1-conda_forge.tar.bz2"
    )
    runner = ScriptedRunner([{"stdout": "2.1.0\n"}, {"stdout": json.dumps(payload)}])

    resolution = CondaDryRunResolver().resolve(
        _intent(
            Requirement(ecosystem="conda", name="python"),
            channels=(private_channel,),
        ),
        Target(platform="linux-64"),
        None,
        _context(runner),
    )

    serialized = repr(resolution)
    assert "secret" not in serialized
    assert "user:secret" not in serialized


def test_nodefaults_channel_is_policy_not_a_solver_channel() -> None:
    runner = ScriptedRunner([{"stdout": "2.1.0\n"}, {"stdout": _solver_payload()}])

    CondaDryRunResolver().resolve(
        _intent(
            Requirement(ecosystem="conda", name="python"),
            channels=("conda-forge", "nodefaults"),
        ),
        Target(platform="linux-64"),
        None,
        _context(runner),
    )

    command = runner.commands[1].argv
    channel_values = [
        command[index + 1] for index, value in enumerate(command) if value == "--channel"
    ]
    assert channel_values == ["conda-forge"]


def test_space_separated_conda_version_is_preserved_as_match_spec() -> None:
    runner = ScriptedRunner([{"stdout": "2.1.0\n"}, {"stdout": _solver_payload()}])

    CondaDryRunResolver().resolve(
        _intent(Requirement(ecosystem="conda", name="python", specifier="3.11.* *_cpython")),
        Target(platform="linux-64"),
        None,
        _context(runner),
    )

    assert runner.commands[1].argv[-1] == "python 3.11.* *_cpython"


def test_mamba_2_uses_micromamba_target_flags() -> None:
    runner = ScriptedRunner([{"stdout": "mamba 2.1.0\n"}, {"stdout": _solver_payload()}])

    CondaDryRunResolver().resolve(
        _intent(Requirement(ecosystem="conda", name="python")),
        Target(platform="linux-64"),
        None,
        _context(runner, **{"conda.tool": "mamba", "conda.executable": "mamba"}),
    )

    command_result = runner.commands[1]
    command = command_result.argv
    assert command[0] == "mamba"
    assert command[command.index("--platform") + 1] == "linux-64"
    assert "--subdir" not in command
    assert "--no-default-packages" not in command
    assert "--no-pin" not in command
    assert command_result.environment is not None
    assert "MAMBA_ROOT_PREFIX" not in command_result.environment


def test_mamba_1_uses_conda_compatible_target_and_pin_flags() -> None:
    runner = ScriptedRunner([{"stdout": "mamba 1.5.12\n"}, {"stdout": _solver_payload()}])

    CondaDryRunResolver().resolve(
        _intent(Requirement(ecosystem="conda", name="python")),
        Target(platform="linux-64"),
        None,
        _context(runner, **{"conda.tool": "mamba", "conda.executable": "mamba"}),
    )

    command = runner.commands[1].argv
    assert command[command.index("--subdir") + 1] == "linux-64"
    assert "--platform" not in command
    assert "--no-default-packages" in command
    assert "--no-pin" in command


def test_generic_mamba_solver_failure_reports_tool_target_and_fallbacks() -> None:
    runner = ScriptedRunner(
        [
            {"stdout": "mamba 2.5.0\n"},
            {
                "returncode": 1,
                "stdout": json.dumps(
                    {"success": False, "solver_problems": ["unsupported request"]}
                ),
                "stderr": "critical libmamba Could not solve for environment specs",
            },
        ]
    )

    with pytest.raises(ResolutionFailed) as captured:
        CondaDryRunResolver().resolve(
            _intent(Requirement(ecosystem="conda", name="python")),
            Target(platform="osx-arm64"),
            None,
            _context(runner, **{"conda.tool": "mamba", "conda.executable": "mamba"}),
        )

    message = str(captured.value)
    assert "without package-specific conflict details" in message
    assert "tool=mamba 2.5.0" in message
    assert "target=osx-arm64" in message
    assert "--tool micromamba" in message
    assert "--tool conda --solver libmamba" in message
