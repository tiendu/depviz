from __future__ import annotations

from depviz.api import Command, CommandResult, OperationContext
from depviz.api.errors import BackendError
from depviz.builtin.conda.tooling import infer_conda_tool, tool_settings
from depviz.cli import parse_args


class DiscoveryRunner:
    def __init__(self, available: dict[str, str]) -> None:
        self.available = available
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
        executable = command.argv[0]
        name = executable.rsplit("/", 1)[-1]
        version = self.available.get(name)
        if version is None:
            raise FileNotFoundError(executable)
        return CommandResult(command.argv, 0, f"{name} {version}\n", "", 0.01)


def _error(message: str) -> BackendError:
    return BackendError(backend="test", operation="select", message=message)


def test_cli_defaults_to_auto_tool_selection() -> None:
    args = parse_args(["doctor"])
    assert args.tool == "auto"


def test_auto_selection_prefers_mamba() -> None:
    runner = DiscoveryRunner({"mamba": "2.1.0", "micromamba": "2.1.0", "conda": "25.1.1"})
    settings = tool_settings(OperationContext(command_runner=runner), error=_error)

    assert settings.tool == "mamba"
    assert settings.auto_selected
    assert settings.detected_version == "2.1.0"
    assert [command.argv[0].rsplit("/", 1)[-1] for command in runner.commands] == ["mamba"]


def test_auto_selection_falls_back_to_conda() -> None:
    runner = DiscoveryRunner({"conda": "25.1.1"})
    settings = tool_settings(OperationContext(command_runner=runner), error=_error)

    assert settings.tool == "conda"
    assert settings.detected_version == "25.1.1"
    assert [command.argv[0].rsplit("/", 1)[-1] for command in runner.commands] == [
        "mamba",
        "micromamba",
        "conda",
    ]


def test_auto_selection_infers_explicit_miniforge_mamba_path() -> None:
    settings = tool_settings(
        OperationContext(
            configuration={"conda.tool": "auto", "conda.executable": "/opt/miniforge/bin/mamba"}
        ),
        error=_error,
    )

    assert settings.tool == "mamba"
    assert settings.executable == "/opt/miniforge/bin/mamba"
    assert settings.auto_selected


def test_executable_name_inference_is_ecosystem_specific() -> None:
    assert infer_conda_tool("/opt/miniforge/bin/mamba") == "mamba"
    assert infer_conda_tool("/usr/local/bin/micromamba") == "micromamba"
    assert infer_conda_tool("/opt/conda/bin/conda") == "conda"
    assert infer_conda_tool("/tmp/custom-solver") is None


def test_doctor_treats_unselected_missing_backends_as_warnings() -> None:
    from depviz.core.doctor import run_doctor
    from depviz.plugins.defaults import create_default_registry

    runner = DiscoveryRunner({"mamba": "2.1.0"})
    report = run_doctor(
        create_default_registry(discover_external=False),
        context=OperationContext(command_runner=runner),
    )

    assert report.passed
    assert any(item.code == "doctor.conda.tool" for item in report.findings)
    assert any(
        item.code == "doctor.backend.unavailable" and item.severity.value == "warning"
        for item in report.findings
    )


def test_doctor_strict_backends_requires_every_toolchain() -> None:
    from depviz.core.doctor import run_doctor
    from depviz.plugins.defaults import create_default_registry

    runner = DiscoveryRunner({"mamba": "2.1.0"})
    report = run_doctor(
        create_default_registry(discover_external=False),
        context=OperationContext(command_runner=runner),
        strict_backends=True,
    )

    assert not report.passed
    assert any(
        item.code == "doctor.backend.unavailable" and item.severity.value == "error"
        for item in report.findings
    )


def test_auto_with_solver_selects_conda_frontend() -> None:
    settings = tool_settings(
        OperationContext(configuration={"conda.tool": "auto", "conda.solver": "libmamba"}),
        error=_error,
    )

    assert settings.tool == "conda"
    assert settings.solver == "libmamba"
