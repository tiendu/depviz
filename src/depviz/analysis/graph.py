from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from depviz.api import Diagnostic, normalize_package_name


@dataclass(frozen=True)
class Package:
    """A graph node, not an exact resolved package identity.

    Dependency graph analysis intentionally groups nodes by ecosystem and
    canonical package name. Exact versions, builds, sources, and artifacts
    belong in ``depviz.api.ResolvedPackage`` and must not be reconstructed from
    this graph view.
    """

    name: str
    ecosystem: str
    constraint: str | None = field(default=None, compare=False)
    source: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        ecosystem = self.ecosystem.strip().lower()
        object.__setattr__(self, "ecosystem", ecosystem)
        object.__setattr__(self, "name", normalize_package_name(ecosystem, self.name))


@dataclass
class DependencyGraph:
    adjacency_list: dict[Package, set[Package]] = field(default_factory=dict)

    def add_node(self, package: Package) -> None:
        self.adjacency_list.setdefault(package, set())

    def add_edge(self, parent: Package, child: Package) -> None:
        self.add_node(parent)
        self.add_node(child)
        self.adjacency_list[parent].add(child)


@dataclass
class DependencyTreeNode:
    package: Package
    children: list[DependencyTreeNode] = field(default_factory=list)
    repeated: bool = False


class InspectionStatus(StrEnum):
    COMPLETE = "complete"
    APPROXIMATE = "approximate"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class GraphInspection:
    graph: DependencyGraph
    status: InspectionStatus
    diagnostics: tuple[Diagnostic, ...] = ()
    requested_depth: int = 0
    backend: str = "metadata-graph-v1"

    @property
    def complete(self) -> bool:
        return self.status is InspectionStatus.COMPLETE
