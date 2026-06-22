from __future__ import annotations

from pathlib import Path

import yaml

from depviz.api import DependencyIntent, Diagnostic, Requirement, Severity, SourceLocation
from depviz.builtin.manifests.common import (
    ParseResult,
    deduplicate,
    find_conflicting_duplicates,
    parse_conda_dependency_details,
    parse_packaging_requirement,
)
from depviz.builtin.manifests.requirements import parse_requirement_option


class CondaYamlParser:
    def parse(self, file_path: Path) -> ParseResult:
        diagnostics: list[Diagnostic] = []
        requirements: list[Requirement] = []
        channels: list[str] = []
        metadata: dict[str, str] = {}
        try:
            raw_data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
        except OSError as error:
            diagnostics.append(
                Diagnostic(
                    code="manifest.unreadable",
                    message=f"Cannot read Conda manifest: {error}",
                    severity=Severity.ERROR,
                    source=SourceLocation(file_path),
                )
            )
            raw_data = None
        except yaml.YAMLError as error:
            diagnostics.append(
                Diagnostic(
                    code="manifest.invalid-yaml",
                    message=f"Invalid YAML: {error}",
                    severity=Severity.ERROR,
                    source=SourceLocation(file_path),
                )
            )
            raw_data = None

        if raw_data is None:
            return ParseResult(DependencyIntent(requirements=(), diagnostics=tuple(diagnostics)))
        if not isinstance(raw_data, dict):
            diagnostics.append(
                Diagnostic(
                    code="manifest.invalid-conda-document",
                    message="Conda manifest must contain a top-level mapping",
                    severity=Severity.ERROR,
                    source=SourceLocation(file_path),
                )
            )
            return ParseResult(DependencyIntent(requirements=(), diagnostics=tuple(diagnostics)))

        for key in ("name", "prefix"):
            value = raw_data.get(key)
            if isinstance(value, str):
                metadata[key] = value

        raw_channels = raw_data.get("channels", [])
        if raw_channels is None:
            raw_channels = []
        if not isinstance(raw_channels, list) or not all(
            isinstance(channel, str) for channel in raw_channels
        ):
            diagnostics.append(
                Diagnostic(
                    code="manifest.invalid-channels",
                    message="The channels field must be a list of strings",
                    severity=Severity.ERROR,
                    source=SourceLocation(file_path),
                )
            )
        else:
            channels.extend(raw_channels)

        raw_dependencies = raw_data.get("dependencies", [])
        if not isinstance(raw_dependencies, list):
            diagnostics.append(
                Diagnostic(
                    code="manifest.invalid-dependencies",
                    message="The dependencies field must be a list",
                    severity=Severity.ERROR,
                    source=SourceLocation(file_path),
                )
            )
            raw_dependencies = []

        for index, dependency in enumerate(raw_dependencies, start=1):
            origin = SourceLocation(file_path)
            if isinstance(dependency, str):
                parsed = parse_conda_dependency_details(dependency)
                if parsed is None:
                    diagnostics.append(
                        Diagnostic(
                            code="manifest.invalid-conda-requirement",
                            message=f"Invalid Conda dependency at list item {index}: {dependency!r}",
                            severity=Severity.ERROR,
                            source=origin,
                        )
                    )
                    continue
                name, specifier, source = parsed
                requirements.append(
                    Requirement(
                        ecosystem="conda",
                        name=name,
                        specifier=specifier,
                        source=source,
                        origin=origin,
                    )
                )
                continue

            if isinstance(dependency, dict) and set(dependency) == {"pip"}:
                pip_dependencies = dependency["pip"]
                if not isinstance(pip_dependencies, list) or not all(
                    isinstance(item, str) for item in pip_dependencies
                ):
                    diagnostics.append(
                        Diagnostic(
                            code="manifest.invalid-pip-section",
                            message="The pip dependency section must be a list of strings",
                            severity=Severity.ERROR,
                            source=origin,
                        )
                    )
                    continue
                for pip_dependency in pip_dependencies:
                    if parse_requirement_option(pip_dependency) is not None:
                        diagnostics.append(
                            Diagnostic(
                                code="manifest.unsupported-pip-option",
                                message=(
                                    "pip options and nested requirement files inside "
                                    "environment.yml are not supported yet"
                                ),
                                severity=Severity.ERROR,
                                source=origin,
                            )
                        )
                        continue
                    requirement, diagnostic = parse_packaging_requirement(
                        pip_dependency,
                        ecosystem="pypi",
                        origin=origin,
                        direct=True,
                    )
                    if diagnostic is not None:
                        diagnostics.append(diagnostic)
                    elif requirement is not None:
                        requirements.append(requirement)
                continue

            diagnostics.append(
                Diagnostic(
                    code="manifest.unsupported-conda-section",
                    message=f"Unsupported dependency entry at list item {index}: {dependency!r}",
                    severity=Severity.ERROR,
                    source=origin,
                )
            )

        diagnostics.extend(find_conflicting_duplicates(requirements))
        return ParseResult(
            DependencyIntent(
                requirements=tuple(requirements),
                channels=tuple(deduplicate(channels)),
                metadata=metadata,
                diagnostics=tuple(diagnostics),
            )
        )
