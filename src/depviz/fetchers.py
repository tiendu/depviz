import json
import logging
import shutil
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Protocol, Any

from packaging.requirements import InvalidRequirement, Requirement

from depviz.models import DependencyGraph, Package
from depviz.parsers import ParseResult

logger = logging.getLogger(__name__)


class MetadataFetcher(Protocol):
    def fetch_dependencies(self, package: Package) -> set[Package]:
        ...


class PyPIFetcher:
    def __init__(self, base_url: str = "https://pypi.org/pypi") -> None:
        self.base_url = base_url.rstrip("/")

    def fetch_dependencies(self, package: Package) -> set[Package]:
        url = f"{self.base_url}/{package.name}/json"

        try:
            with urllib.request.urlopen(url, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception:
            logger.debug("Failed to fetch PyPI metadata for %s", package.name)
            return set()

        requires_dist = data.get("info", {}).get("requires_dist") or []
        dependencies: set[Package] = set()

        for dep_str in requires_dist:
            try:
                req = Requirement(dep_str)
            except InvalidRequirement:
                continue

            # MVP: skip conditional / optional dependencies.
            if req.marker is not None:
                continue

            dependencies.add(
                Package(
                    name=req.name.lower().replace("_", "-"),
                    ecosystem="pypi",
                    constraint=str(req.specifier) or None,
                    source="pypi",
                )
            )

        return dependencies


class CondaFetcher:
    def __init__(self, channels: list[str] | None = None) -> None:
        self.channels = channels or ["bioconda", "conda-forge"]

    def fetch_dependencies(self, package: Package) -> set[Package]:
        executable = self._find_conda_executable()

        if executable is None:
            logger.debug("No micromamba/mamba/conda executable found; skipping %s", package.name)
            return set()

        cmd = [executable, "repoquery", "depends", package.name]

        for channel in self.channels:
            cmd.extend(["-c", channel])

        cmd.append("--json")

        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception:
            logger.debug("Failed to query Conda dependencies for %s", package.name)
            return set()

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return set()

        return self._parse_direct_dependencies(data)

    @staticmethod
    def _find_conda_executable() -> str | None:
        for executable in ("micromamba", "mamba", "conda"):
            if shutil.which(executable):
                return executable
        return None

    @staticmethod
    def _parse_direct_dependencies(data: dict[str, Any]) -> set[Package]:
        dependencies: set[Package] = set()

        roots = data.get("result", {}).get("graph_roots") or []

        for root in roots:
            if not isinstance(root, dict):
                continue

            for dep_str in root.get("depends", []):
                raw_name = dep_str.split()[0].strip().lower()

                if raw_name.startswith("__"):
                    continue

                name = raw_name.replace("_", "-")

                if not name:
                    continue

                dependencies.add(
                    Package(
                        name=name,
                        ecosystem="conda",
                        constraint=CondaFetcher._extract_constraint(dep_str),
                        source=None,
                    )
                )

        return dependencies

    @staticmethod
    def _extract_constraint(dep_str: str) -> str | None:
        parts = dep_str.split(maxsplit=1)
        if len(parts) == 1:
            return None
        return parts[1].strip()


class FetcherRegistry:
    def __init__(self, channels: list[str] | None = None) -> None:
        self.pypi = PyPIFetcher()
        self.conda = CondaFetcher(channels=channels)

    def get(self, package: Package) -> MetadataFetcher:
        if package.ecosystem == "conda":
            return self.conda
        return self.pypi


def build_graph_concurrently(
    parse_result: ParseResult,
    max_workers: int = 12,
    max_depth: int = 3,
) -> DependencyGraph:
    graph = DependencyGraph()
    registry = FetcherRegistry(channels=parse_result.channels)

    visited: set[Package] = set()
    current_layer: set[Package] = set(parse_result.packages)

    for package in current_layer:
        graph.add_node(package)

    depth = 0

    while current_layer and depth < max_depth:
        to_scan = current_layer - visited

        if not to_scan:
            break

        visited.update(to_scan)
        next_layer: set[Package] = set()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_package = {
                executor.submit(registry.get(package).fetch_dependencies, package): package
                for package in to_scan
            }

            for future in as_completed(future_to_package):
                parent = future_to_package[future]

                try:
                    dependencies = future.result()
                except Exception:
                    logger.debug("Failed resolving %s", parent.name)
                    continue

                for child in dependencies:
                    graph.add_edge(parent, child)

                    if child not in visited:
                        next_layer.add(child)

        current_layer = next_layer
        depth += 1

    return graph

