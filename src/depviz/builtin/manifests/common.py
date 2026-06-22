from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from packaging.requirements import InvalidRequirement, Requirement as PackagingRequirement

from depviz.api import DependencyIntent, Diagnostic, Requirement, Severity, SourceLocation
from depviz.analysis.graph import Package


@dataclass(frozen=True)
class ParseResult:
    intent: DependencyIntent

    @property
    def packages(self) -> set[Package]:
        return {
            Package(
                name=requirement.name,
                ecosystem=requirement.ecosystem,
                constraint=requirement.specifier,
                source=requirement.source,
            )
            for requirement in self.intent.requirements
        }

    @property
    def channels(self) -> list[str]:
        return list(self.intent.channels)

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        return self.intent.diagnostics

    @property
    def has_errors(self) -> bool:
        return self.intent.has_errors


class ManifestParser(Protocol):
    def parse(self, file_path: Path) -> ParseResult: ...


def normalize_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def strip_comment(line: str) -> str:
    """Strip comments without destroying URL fragments such as ``#sha256=``."""
    for index, character in enumerate(line):
        if character == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index].rstrip()
    return line.rstrip()


def parse_packaging_requirement(
    text: str,
    *,
    ecosystem: str,
    origin: SourceLocation,
    direct: bool,
) -> tuple[Requirement | None, Diagnostic | None]:
    try:
        parsed = PackagingRequirement(text)
    except InvalidRequirement as error:
        return None, Diagnostic(
            code="manifest.invalid-requirement",
            message=f"Unsupported or invalid requirement {text!r}: {error}",
            severity=Severity.ERROR,
            source=origin,
        )

    source = parsed.url
    if source is None and ecosystem == "pypi":
        source = "pypi"

    return (
        Requirement(
            ecosystem=ecosystem,
            name=parsed.name,
            specifier=str(parsed.specifier) or None,
            source=source,
            marker=str(parsed.marker) if parsed.marker is not None else None,
            extras=tuple(parsed.extras),
            direct=direct,
            origin=origin,
        ),
        None,
    )


def parse_requirement_line(line: str) -> tuple[str, str | None] | None:
    line = strip_comment(line).strip()
    if not line or line.startswith(("-", "--", "git+", ".", "/")):
        return None
    try:
        requirement = PackagingRequirement(line)
    except InvalidRequirement:
        return None
    return normalize_name(requirement.name), str(requirement.specifier) or None


def parse_conda_dependency_details(
    dependency: str,
) -> tuple[str, str | None, str | None] | None:
    dependency = strip_comment(dependency).strip()
    if not dependency or dependency.startswith(("-", "--")):
        return None

    source: str | None = None
    if "::" in dependency:
        source, dependency = dependency.split("::", 1)
        source = source.strip() or None
        dependency = dependency.strip()

    match = re.match(r"^([A-Za-z0-9_.-]+)\s*(.*)?$", dependency)
    if match is None:
        return None
    name = match.group(1).strip().lower()
    specifier = (match.group(2) or "").strip() or None
    if not name:
        return None
    return name, specifier, source


def parse_conda_dependency(dependency: str) -> tuple[str, str | None] | None:
    parsed = parse_conda_dependency_details(dependency)
    if parsed is None:
        return None
    name, specifier, _source = parsed
    return name, specifier


def parse_pip_dependency(dependency: str) -> tuple[str, str | None] | None:
    return parse_requirement_line(dependency)


def find_conflicting_duplicates(requirements: list[Requirement]) -> list[Diagnostic]:
    by_identity: dict[tuple[str, str], Requirement] = {}
    diagnostics: list[Diagnostic] = []
    for requirement in requirements:
        key = (requirement.ecosystem, requirement.name)
        previous = by_identity.get(key)
        if previous is None:
            by_identity[key] = requirement
            continue
        previous_definition = (
            previous.specifier,
            previous.source,
            previous.marker,
            previous.extras,
        )
        current_definition = (
            requirement.specifier,
            requirement.source,
            requirement.marker,
            requirement.extras,
        )
        if previous_definition != current_definition:
            diagnostics.append(
                Diagnostic(
                    code="manifest.conflicting-duplicate",
                    message=(
                        f"{requirement.ecosystem} package {requirement.name!r} is declared "
                        "more than once with different requirements"
                    ),
                    severity=Severity.WARNING,
                    source=requirement.origin,
                    hint=(
                        "A real solver must reconcile these declarations; "
                        "the graph view merges names."
                    ),
                )
            )
    return diagnostics


def deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
