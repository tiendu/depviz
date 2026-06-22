from __future__ import annotations

import json
import logging
import shutil
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet

from depviz.api import Command, CommandRunner, Diagnostic, Severity
from depviz.infrastructure import LocalCommandRunner
from depviz.analysis.graph import DependencyGraph, GraphInspection, InspectionStatus, Package
from depviz.parsers import ParseResult

logger = logging.getLogger(__name__)


class FetchStatus(StrEnum):
    COMPLETE = "complete"
    APPROXIMATE = "approximate"
    FAILED = "failed"


@dataclass(frozen=True)
class FetchResult:
    dependencies: frozenset[Package]
    status: FetchStatus
    diagnostics: tuple[Diagnostic, ...] = ()


class MetadataFetcher(Protocol):
    def fetch(self, package: Package) -> FetchResult: ...


class PyPIFetcher:
    def __init__(self, base_url: str = "https://pypi.org/pypi") -> None:
        self.base_url = base_url.rstrip("/")

    def fetch(self, package: Package) -> FetchResult:
        diagnostics: list[Diagnostic] = []
        exact_version = _exact_pypi_pin(package.constraint)
        quoted_name = urllib.parse.quote(package.name, safe="")

        if exact_version is None:
            url = f"{self.base_url}/{quoted_name}/json"
            diagnostics.append(
                Diagnostic(
                    code="pypi.unpinned-metadata",
                    message=(
                        f"{package.name} is not pinned to one exact version; "
                        "the graph uses metadata for the repository's current release"
                    ),
                    severity=Severity.WARNING,
                )
            )
        else:
            quoted_version = urllib.parse.quote(exact_version, safe="")
            url = f"{self.base_url}/{quoted_name}/{quoted_version}/json"

        if package.source not in {None, "pypi"}:
            diagnostics.append(
                Diagnostic(
                    code="pypi.source-not-resolved",
                    message=(
                        f"{package.name} uses source {package.source!r}; "
                        "the metadata graph still queries the configured PyPI endpoint"
                    ),
                    severity=Severity.WARNING,
                )
            )

        try:
            with urllib.request.urlopen(url, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            return FetchResult(
                dependencies=frozenset(),
                status=FetchStatus.FAILED,
                diagnostics=(
                    Diagnostic(
                        code="pypi.metadata-failed",
                        message=f"Failed to fetch metadata for {package.name}: {error}",
                        severity=Severity.ERROR,
                    ),
                ),
            )

        info = data.get("info")
        if not isinstance(info, dict):
            return FetchResult(
                dependencies=frozenset(),
                status=FetchStatus.FAILED,
                diagnostics=(
                    Diagnostic(
                        code="pypi.invalid-response",
                        message=f"PyPI returned invalid metadata for {package.name}",
                        severity=Severity.ERROR,
                    ),
                ),
            )

        requires_dist = info.get("requires_dist") or []
        if not isinstance(requires_dist, list):
            return FetchResult(
                dependencies=frozenset(),
                status=FetchStatus.FAILED,
                diagnostics=(
                    Diagnostic(
                        code="pypi.invalid-dependencies",
                        message=f"PyPI returned an invalid dependency list for {package.name}",
                        severity=Severity.ERROR,
                    ),
                ),
            )

        dependencies: set[Package] = set()
        saw_marker = False

        for dependency_text in requires_dist:
            if not isinstance(dependency_text, str):
                diagnostics.append(
                    Diagnostic(
                        code="pypi.invalid-requirement-metadata",
                        message=f"Ignored non-string dependency metadata for {package.name}",
                        severity=Severity.WARNING,
                    )
                )
                continue

            try:
                requirement = Requirement(dependency_text)
            except InvalidRequirement:
                diagnostics.append(
                    Diagnostic(
                        code="pypi.invalid-requirement-metadata",
                        message=(
                            f"Ignored invalid dependency metadata for {package.name}: "
                            f"{dependency_text!r}"
                        ),
                        severity=Severity.WARNING,
                    )
                )
                continue

            if requirement.marker is not None:
                saw_marker = True

            dependencies.add(
                Package(
                    name=requirement.name,
                    ecosystem="pypi",
                    constraint=str(requirement.specifier) or None,
                    source=requirement.url or "pypi",
                )
            )

        if saw_marker:
            diagnostics.append(
                Diagnostic(
                    code="pypi.markers-not-evaluated",
                    message=(
                        f"Conditional dependencies for {package.name} were included conservatively; "
                        "target environment markers were not evaluated"
                    ),
                    severity=Severity.WARNING,
                )
            )

        status = FetchStatus.APPROXIMATE if diagnostics else FetchStatus.COMPLETE
        return FetchResult(
            dependencies=frozenset(dependencies),
            status=status,
            diagnostics=tuple(diagnostics),
        )

    def fetch_dependencies(self, package: Package) -> set[Package]:
        """Compatibility wrapper for the original fetcher API."""
        return set(self.fetch(package).dependencies)


class CondaFetcher:
    def __init__(
        self,
        channels: list[str] | None = None,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.channels = channels or ["bioconda", "conda-forge"]
        self.command_runner = command_runner or LocalCommandRunner()

    def fetch(self, package: Package) -> FetchResult:
        executable = self._find_conda_executable()
        if executable is None:
            return FetchResult(
                dependencies=frozenset(),
                status=FetchStatus.FAILED,
                diagnostics=(
                    Diagnostic(
                        code="conda.tool-unavailable",
                        message=(
                            f"Cannot inspect {package.name}: no micromamba, mamba, or conda "
                            "executable was found"
                        ),
                        severity=Severity.ERROR,
                    ),
                ),
            )

        package_spec = f"{package.name}{package.constraint or ''}"
        cmd = [executable, "repoquery", "depends", package_spec]
        channels = list(self.channels)
        if package.source is not None:
            channels.insert(0, package.source)
        for channel in dict.fromkeys(channels):
            cmd.extend(["-c", channel])
        cmd.append("--json")

        try:
            result = self.command_runner.run(
                Command(argv=tuple(cmd)),
                timeout_seconds=30,
                output_limit=1_000_000,
            )
        except OSError as error:
            return _conda_failure(package, str(error))

        if result.timed_out:
            return _conda_failure(package, "dependency query timed out")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "query failed").strip()
            return _conda_failure(package, detail[:500])
        if result.output_truncated:
            return _conda_failure(package, "dependency query output exceeded the safety limit")

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            return _conda_failure(package, f"invalid JSON response: {error}")

        dependencies = self._parse_direct_dependencies(data)
        return FetchResult(
            dependencies=frozenset(dependencies),
            status=FetchStatus.APPROXIMATE,
            diagnostics=(
                Diagnostic(
                    code="conda.repoquery-not-environment-solve",
                    message=(
                        f"Dependencies for {package.name} came from package-level repoquery; "
                        "this is not a complete environment solve"
                    ),
                    severity=Severity.WARNING,
                ),
            ),
        )

    def fetch_dependencies(self, package: Package) -> set[Package]:
        """Compatibility wrapper for the original fetcher API."""
        return set(self.fetch(package).dependencies)

    @staticmethod
    def _find_conda_executable() -> str | None:
        for executable in ("micromamba", "mamba", "conda"):
            if shutil.which(executable):
                return executable
        return None

    @staticmethod
    def _parse_direct_dependencies(data: dict[str, Any]) -> set[Package]:
        dependencies: set[Package] = set()
        result = data.get("result")
        if not isinstance(result, dict):
            return dependencies

        roots = result.get("graph_roots") or []
        if not isinstance(roots, list):
            return dependencies

        for root in roots:
            if not isinstance(root, dict):
                continue

            root_dependencies = root.get("depends", [])
            if not isinstance(root_dependencies, list):
                continue

            for dependency_text in root_dependencies:
                if not isinstance(dependency_text, str):
                    continue

                raw_name = dependency_text.split()[0].strip().lower()
                if raw_name.startswith("__"):
                    continue

                name = raw_name.replace("_", "-")
                if not name:
                    continue

                dependencies.add(
                    Package(
                        name=name,
                        ecosystem="conda",
                        constraint=CondaFetcher._extract_constraint(dependency_text),
                        source=None,
                    )
                )

        return dependencies

    @staticmethod
    def _extract_constraint(dependency_text: str) -> str | None:
        parts = dependency_text.split(maxsplit=1)
        if len(parts) == 1:
            return None
        return parts[1].strip()


class FetcherRegistry:
    def __init__(
        self,
        channels: list[str] | None = None,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.pypi = PyPIFetcher()
        self.conda = CondaFetcher(channels=channels, command_runner=command_runner)

    def get(self, package: Package) -> MetadataFetcher:
        if package.ecosystem == "conda":
            return self.conda
        return self.pypi


def inspect_dependency_graph(
    parse_result: ParseResult,
    max_workers: int = 12,
    max_depth: int = 3,
    registry: FetcherRegistry | None = None,
) -> GraphInspection:
    if max_workers < 1:
        raise ValueError("max_workers must be at least 1")
    if max_depth < 0:
        raise ValueError("max_depth cannot be negative")

    graph = DependencyGraph()
    active_registry = registry or FetcherRegistry(channels=parse_result.channels)
    diagnostics: list[Diagnostic] = list(parse_result.diagnostics)

    visited: set[Package] = set()
    current_layer: set[Package] = set(parse_result.packages)
    for package in current_layer:
        graph.add_node(package)

    depth = 0
    saw_approximate = False
    saw_failure = parse_result.has_errors

    while current_layer and depth < max_depth:
        to_scan = current_layer - visited
        if not to_scan:
            break

        visited.update(to_scan)
        next_layer: set[Package] = set()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_package = {
                executor.submit(active_registry.get(package).fetch, package): package
                for package in to_scan
            }

            for future in as_completed(future_to_package):
                parent = future_to_package[future]
                try:
                    fetch_result = future.result()
                except Exception as error:  # plugin boundary: classify unexpected failures
                    diagnostics.append(
                        Diagnostic(
                            code="inspection.unexpected-fetch-failure",
                            message=f"Unexpected failure while inspecting {parent.name}: {error}",
                            severity=Severity.ERROR,
                        )
                    )
                    saw_failure = True
                    continue

                diagnostics.extend(fetch_result.diagnostics)
                if fetch_result.status is FetchStatus.FAILED:
                    saw_failure = True
                elif fetch_result.status is FetchStatus.APPROXIMATE:
                    saw_approximate = True

                for child in fetch_result.dependencies:
                    graph.add_edge(parent, child)
                    if child not in visited:
                        next_layer.add(child)

        current_layer = next_layer
        depth += 1

    if current_layer:
        diagnostics.append(
            Diagnostic(
                code="inspection.depth-truncated",
                message=(
                    f"Dependency traversal stopped at depth {max_depth} with "
                    f"{len(current_layer)} package(s) still uninspected"
                ),
                severity=Severity.ERROR,
                hint="Increase --depth or use a solver-backed backend when available.",
            )
        )
        saw_failure = True

    diagnostics = _deduplicate_diagnostics(diagnostics)
    if saw_failure:
        status = InspectionStatus.INCOMPLETE
    elif saw_approximate or any(
        diagnostic.severity is Severity.WARNING for diagnostic in diagnostics
    ):
        status = InspectionStatus.APPROXIMATE
    else:
        status = InspectionStatus.COMPLETE

    return GraphInspection(
        graph=graph,
        status=status,
        diagnostics=tuple(diagnostics),
        requested_depth=max_depth,
    )


def build_graph_concurrently(
    parse_result: ParseResult,
    max_workers: int = 12,
    max_depth: int = 3,
) -> DependencyGraph:
    """Compatibility wrapper returning only the graph view."""
    return inspect_dependency_graph(
        parse_result=parse_result,
        max_workers=max_workers,
        max_depth=max_depth,
    ).graph


def _exact_pypi_pin(constraint: str | None) -> str | None:
    if not constraint:
        return None
    try:
        specifiers = list(SpecifierSet(constraint))
    except InvalidSpecifier:
        return None
    if len(specifiers) != 1:
        return None
    specifier = specifiers[0]
    if specifier.operator != "==" or "*" in specifier.version:
        return None
    return specifier.version


def _conda_failure(package: Package, detail: str) -> FetchResult:
    return FetchResult(
        dependencies=frozenset(),
        status=FetchStatus.FAILED,
        diagnostics=(
            Diagnostic(
                code="conda.query-failed",
                message=f"Failed to inspect Conda package {package.name}: {detail}",
                severity=Severity.ERROR,
            ),
        ),
    )


def _deduplicate_diagnostics(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    seen: set[tuple[str, str, str | None]] = set()
    unique: list[Diagnostic] = []
    for diagnostic in diagnostics:
        source = diagnostic.source.format() if diagnostic.source is not None else None
        key = (diagnostic.code, diagnostic.message, source)
        if key in seen:
            continue
        seen.add(key)
        unique.append(diagnostic)
    return unique
