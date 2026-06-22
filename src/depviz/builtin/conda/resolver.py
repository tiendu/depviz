from __future__ import annotations

import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from depviz.api import (
    BackendPayload,
    Command,
    DependencyIntent,
    Diagnostic,
    EnvironmentState,
    OperationContext,
    Requirement,
    Resolution,
    ResolutionStatus,
    Severity,
    Target,
)
from depviz.api.errors import ResolutionFailed, ToolUnavailable
from depviz.builtin.conda.security import (
    credential_secrets,
    redact_text,
    redact_url,
    sanitize_json,
    sanitize_requirement,
)
from depviz.builtin.conda.tooling import (
    isolated_environment,
    mamba_uses_micromamba_cli,
    read_tool_version,
    tool_settings,
)
from depviz.builtin.conda.transaction import (
    parse_json_payload,
    parse_link_packages,
    solver_failure_message,
)
from depviz.infrastructure import LocalCommandRunner


class CondaDryRunResolver:
    """Resolve a complete Conda environment through an established solver."""

    name = "conda-dry-run"

    def resolve(
        self,
        intent: DependencyIntent,
        target: Target,
        current: EnvironmentState | None,
        context: OperationContext,
    ) -> Resolution:
        del current
        _validate_intent(intent, target)

        settings = tool_settings(context, error=_resolution_configuration_error)
        tool = settings.tool
        executable = settings.executable
        timeout_seconds = settings.timeout_seconds
        output_limit = settings.output_limit
        channels = _effective_channels(intent)
        secrets = credential_secrets(channels)
        runner = context.command_runner or LocalCommandRunner()

        tool_version = read_tool_version(
            runner=runner,
            settings=settings,
            backend=self.name,
            operation="resolve",
            secrets=secrets,
        )

        with tempfile.TemporaryDirectory(prefix="depviz-conda-resolve-") as temporary_directory:
            temporary_root = Path(temporary_directory)
            candidate_prefix = temporary_root / "candidate"
            empty_rc = temporary_root / "empty-condarc.yml"
            empty_rc.write_text("{}\n", encoding="utf-8")

            transient_redactions = (*secrets, str(candidate_prefix), str(temporary_root))
            command = Command(
                argv=_build_command(
                    tool=tool,
                    executable=executable,
                    candidate_prefix=candidate_prefix,
                    platform=target.platform,
                    tool_version=tool_version,
                    channels=channels,
                    requirements=intent.requirements,
                    offline=context.offline,
                    solver=settings.solver,
                ),
                cwd=context.working_directory,
                environment=isolated_environment(tool, temporary_root, empty_rc),
            )

            try:
                result = runner.run(
                    command,
                    timeout_seconds=timeout_seconds,
                    output_limit=output_limit,
                    redact=transient_redactions,
                )
            except FileNotFoundError as error:
                raise ToolUnavailable(
                    backend=self.name,
                    operation="resolve",
                    message=f"Executable not found: {executable}",
                ) from error
            except OSError as error:
                raise ToolUnavailable(
                    backend=self.name,
                    operation="resolve",
                    message=f"Cannot execute {executable!r}: {error}",
                ) from error

        if result.timed_out:
            raise ResolutionFailed(
                backend=self.name,
                operation="resolve",
                message=f"Solver timed out after {timeout_seconds:g} seconds",
            )
        if result.output_truncated:
            raise ResolutionFailed(
                backend=self.name,
                operation="resolve",
                message=(
                    "Solver output exceeded the configured limit; refusing to parse a partial "
                    "transaction"
                ),
            )

        payload = parse_json_payload(result.stdout)
        sanitized_payload = cast(dict[str, object], sanitize_json(payload, transient_redactions))
        if result.returncode != 0 or payload.get("success") is not True:
            message = solver_failure_message(sanitized_payload, result.stderr, result.returncode)
            if "without package-specific conflict details" in message:
                message += (
                    f"; tool={tool} {tool_version}, target={target.platform}. "
                    "Try the same manifest with --tool micromamba or "
                    "--tool conda --solver libmamba to distinguish a Mamba CLI/configuration "
                    "problem from an unsatisfiable environment"
                )
            raise ResolutionFailed(
                backend=self.name,
                operation="resolve",
                message=message,
            )

        packages, package_diagnostics = parse_link_packages(payload, target.platform, secrets)
        native_payload = BackendPayload(
            schema="depviz.conda.dry-run.v1",
            data={
                "tool": tool,
                "tool_version": tool_version,
                "command": [redact_text(item, transient_redactions) for item in result.argv],
                "channels": [redact_url(item, secrets) for item in channels],
                "platform": target.platform,
                "solver": settings.solver or "default",
                "offline": context.offline,
                "transaction": sanitized_payload,
            },
        )
        diagnostics = (
            Diagnostic(
                code="resolver.conda.complete",
                message=(
                    f"{tool} produced a complete create transaction containing "
                    f"{len(packages)} packages"
                ),
                severity=Severity.INFO,
            ),
            *package_diagnostics,
        )
        return Resolution(
            requested=tuple(sanitize_requirement(item, secrets) for item in intent.requirements),
            packages=packages,
            target=target,
            status=ResolutionStatus.COMPLETE,
            diagnostics=diagnostics,
            native_payload=native_payload,
        )


def _validate_intent(intent: DependencyIntent, target: Target) -> None:
    if intent.has_errors:
        raise ResolutionFailed(
            backend="conda-dry-run",
            operation="resolve",
            message="Manifest contains errors and cannot be resolved",
            diagnostics=intent.diagnostics,
        )
    if not target.platform.strip():
        raise ResolutionFailed(
            backend="conda-dry-run",
            operation="resolve",
            message="A target Conda platform is required",
        )
    if not intent.requirements:
        raise ResolutionFailed(
            backend="conda-dry-run",
            operation="resolve",
            message="No requirements were provided",
        )
    unsupported = sorted(
        {
            requirement.ecosystem
            for requirement in intent.requirements
            if requirement.ecosystem != "conda"
        }
    )
    if unsupported:
        pypi_count = sum(requirement.ecosystem == "pypi" for requirement in intent.requirements)
        message = (
            "The Conda dry-run resolver only accepts Conda requirements; unsupported "
            f"ecosystems: {', '.join(unsupported)}"
        )
        if pypi_count:
            conda_count = sum(
                requirement.ecosystem == "conda" for requirement in intent.requirements
            )
            message += (
                f". Mixed environment detected ({conda_count} Conda, {pypi_count} pip); "
                "use --resolver conda-pip or leave --resolver as auto"
            )
        raise ResolutionFailed(
            backend="conda-dry-run",
            operation="resolve",
            message=message,
        )
    if intent.constraints:
        raise ResolutionFailed(
            backend="conda-dry-run",
            operation="resolve",
            message="Separate constraint files are not supported by the Conda resolver",
        )
    for requirement in intent.requirements:
        if requirement.marker is not None or requirement.extras:
            raise ResolutionFailed(
                backend="conda-dry-run",
                operation="resolve",
                message=f"Conda requirement {requirement.name!r} contains unsupported markers or extras",
            )


def _effective_channels(intent: DependencyIntent) -> tuple[str, ...]:
    channels = [*intent.channels]
    channels.extend(
        requirement.source for requirement in intent.requirements if requirement.source is not None
    )
    deduplicated = tuple(
        dict.fromkeys(
            channel.strip()
            for channel in channels
            if channel.strip() and channel.strip().lower() != "nodefaults"
        )
    )
    if not deduplicated:
        raise ResolutionFailed(
            backend="conda-dry-run",
            operation="resolve",
            message=(
                "No Conda channels were declared. Add channels to environment.yml or qualify "
                "requirements as channel::package; ambient user configuration is not used."
            ),
        )
    return deduplicated


def _build_command(
    *,
    tool: str,
    executable: str,
    candidate_prefix: Path,
    platform: str,
    tool_version: str,
    channels: Sequence[str],
    requirements: Sequence[Requirement],
    offline: bool,
    solver: str | None,
) -> tuple[str, ...]:
    arguments = [
        executable,
        "create",
        "--dry-run",
        "--json",
        "--yes",
        "--prefix",
        str(candidate_prefix),
    ]
    if tool == "conda" or (tool == "mamba" and not mamba_uses_micromamba_cli(tool, tool_version)):
        arguments.extend(["--subdir", platform, "--no-default-packages", "--no-pin"])
        if solver:
            arguments.extend(["--solver", solver])
    else:
        # Mamba 2 is the dynamically linked build of Micromamba and exposes
        # the same CLI. Older Mamba releases used Conda-compatible flags.
        arguments.extend(["--platform", platform])
        if solver:
            raise ResolutionFailed(
                backend="conda-dry-run",
                operation="resolve",
                message="The conda.solver option is only valid when the selected tool is 'conda'",
            )

    arguments.extend(["--override-channels", "--strict-channel-priority"])
    if offline:
        arguments.append("--offline")
    for channel in channels:
        arguments.extend(["--channel", channel])
    arguments.extend(_requirement_to_match_spec(requirement) for requirement in requirements)
    return tuple(arguments)


def _requirement_to_match_spec(requirement: Requirement) -> str:
    package = requirement.name
    if requirement.source:
        package = f"{requirement.source}::{package}"
    specifier = requirement.specifier
    if not specifier:
        return package
    separator = "" if specifier[0] in "=<>!~[" else " "
    return f"{package}{separator}{specifier}"


def _resolution_configuration_error(message: str) -> ResolutionFailed:
    return ResolutionFailed(
        backend="conda-dry-run",
        operation="resolve",
        message=message,
    )
