from dataclasses import dataclass, field


@dataclass(frozen=True)
class Package:
    name: str
    ecosystem: str
    constraint: str | None = field(default=None, compare=False)
    source: str | None = field(default=None, compare=False)


@dataclass
class DependencyGraph:
    adjacency_list: dict[Package, set[Package]] = field(default_factory=dict)

    def add_node(self, package: Package) -> None:
        self.adjacency_list.setdefault(package, set())

    def add_edge(self, parent: Package, child: Package) -> None:
        self.add_node(parent)
        self.add_node(child)
        self.adjacency_list[parent].add(child)

