"""
Graph analysis helpers for depviz.

depviz represents dependencies as a directed graph:

    parent package  ->  dependency package

Example:

    samtools -> htslib
    htslib   -> libcurl
    htslib   -> libzlib

So each edge means:

    "the package on the left requires the package on the right"

This file does not fetch metadata and does not print reports.
It only asks questions about an already-built graph.

Main ideas:

1. Blast radius

   Count how many packages point into a package.

       samtools -> htslib
       bcftools -> htslib
       pysam    -> htslib

   htslib has blast radius 3.

   Meaning:
       If htslib changes or breaks, many packages may be affected.

2. Dependency weight

   Count how many direct and indirect dependencies a package pulls in.

       samtools -> htslib -> libcurl -> openssl

   samtools pulls in htslib, libcurl, and openssl.

   Meaning:
       A high-weight package adds a lot of dependency baggage.

3. Roots

   Packages nobody else depends on.

   These are usually the packages the user explicitly listed in
   requirements.txt or environment.yml.

4. Leaves

   Packages with no known dependencies.

   These are terminal nodes in the resolved graph.

5. Dependents

   Reverse lookup:

       "Who depends on this package?"

   Useful for answering:

       depviz --report why --package libzlib

6. Dependencies

   Forward lookup:

       "What does this package depend on?"

   Useful for answering:

       depviz --report deps --package samtools

Implementation notes:

The graph is stored as an adjacency list:

      dict[Package, set[Package]]

Forward traversal follows dependencies:

      package -> dependency -> dependency-of-dependency

Reverse traversal builds a reversed graph:

      dependency -> packages-that-use-it

Breadth-first search is used for transitive dependency and impact queries.
"""


from collections import defaultdict, deque

from depviz.models import DependencyGraph, Package


def calculate_blast_radius(graph: DependencyGraph) -> list[tuple[Package, int]]:
    """
    Calculate direct in-degree for each package.

    In this graph, edges point from package to dependency:

        parent -> dependency

    The in-degree of a package is the number of packages that directly
    depend on it.

    Example:

        samtools -> htslib
        bcftools -> htslib
        pysam    -> htslib

    htslib has blast radius 3.

    This is useful for identifying critical shared dependencies.
    """
    in_degree_counts: dict[Package, int] = defaultdict(int)

    for package in graph.adjacency_list:
        in_degree_counts[package] = 0

    for dependencies in graph.adjacency_list.values():
        for dep in dependencies:
            in_degree_counts[dep] += 1

    return sorted(
        in_degree_counts.items(),
        key=lambda item: item[1],
        reverse=True,
    )


def calculate_dependency_weight(graph: DependencyGraph) -> list[tuple[Package, int]]:
    """
    Count how many transitive dependencies each package pulls in.

    This walks forward through the graph from each package.

    Example:

        samtools -> htslib
        htslib   -> libcurl
        libcurl  -> openssl

    samtools has dependency weight 3 because it pulls in:

        htslib, libcurl, openssl

    This is useful for finding packages that make an environment large,
    slow to solve, or more fragile.
    """
    results: list[tuple[Package, int]] = []

    for package in graph.adjacency_list:
        seen: set[Package] = set()
        queue = deque(graph.adjacency_list.get(package, set()))

        while queue:
            dep = queue.popleft()

            if dep in seen:
                continue

            seen.add(dep)
            queue.extend(graph.adjacency_list.get(dep, set()))

        results.append((package, len(seen)))

    return sorted(results, key=lambda item: item[1], reverse=True)


def find_leaf_packages(graph: DependencyGraph) -> list[Package]:
    """
    Packages with no known dependencies.
    """
    return sorted(
        [pkg for pkg, deps in graph.adjacency_list.items() if not deps],
        key=lambda pkg: pkg.name,
    )


def find_root_packages(graph: DependencyGraph) -> list[Package]:
    """
    Packages that are not depended on by any other package.
    Usually the user's top-level manifest packages.
    """
    depended_on: set[Package] = set()

    for deps in graph.adjacency_list.values():
        depended_on.update(deps)

    return sorted(
        [pkg for pkg in graph.adjacency_list if pkg not in depended_on],
        key=lambda pkg: pkg.name,
    )


def summarize_graph(graph: DependencyGraph) -> dict[str, int]:
    edge_count = sum(len(deps) for deps in graph.adjacency_list.values())

    ecosystems = {
        pkg.ecosystem
        for pkg in graph.adjacency_list
    }

    return {
        "packages": len(graph.adjacency_list),
        "edges": edge_count,
        "ecosystems": len(ecosystems),
    }


def find_dependents(graph: DependencyGraph, target_name: str) -> list[Package]:
    """
    Find packages that directly depend on target_name.
    """
    target_name = target_name.lower().replace("_", "-")
    dependents: list[Package] = []

    for parent, deps in graph.adjacency_list.items():
        if any(dep.name == target_name for dep in deps):
            dependents.append(parent)

    return sorted(dependents, key=lambda pkg: (pkg.ecosystem, pkg.name))


def find_transitive_dependents(graph: DependencyGraph, target_name: str) -> set[Package]:
    """
    Find all packages that directly or indirectly depend on target_name.

    This is an impact query.

    Because the stored graph points forward:

        package -> dependency

    we first build a reverse graph:

        dependency -> package

    Then we walk backward from the target.

    Example:

        samtools -> htslib -> libzlib
        bcftools -> htslib -> libzlib

    find_transitive_dependents("libzlib") returns:

        htslib, samtools, bcftools

    This answers:

        "What might be affected if libzlib changes?"
    """
    target_name = target_name.lower().replace("_", "-")

    reverse_graph: dict[Package, set[Package]] = defaultdict(set)

    for parent, deps in graph.adjacency_list.items():
        for dep in deps:
            reverse_graph[dep].add(parent)

    matching_targets = [
        pkg for pkg in graph.adjacency_list
        if pkg.name == target_name
    ]

    affected: set[Package] = set()
    queue = deque(matching_targets)

    while queue:
        pkg = queue.popleft()

        for parent in reverse_graph.get(pkg, set()):
            if parent in affected:
                continue

            affected.add(parent)
            queue.append(parent)

    return affected


def find_dependencies(graph: DependencyGraph, target_name: str) -> set[Package]:
    """
    Find direct dependencies of target_name.
    """
    target_name = target_name.lower().replace("_", "-")

    deps: set[Package] = set()

    for pkg, children in graph.adjacency_list.items():
        if pkg.name == target_name:
            deps.update(children)

    return deps


def find_transitive_dependencies(graph: DependencyGraph, target_name: str) -> set[Package]:
    """
    Find all packages that target_name pulls in.
    """
    target_name = target_name.lower().replace("_", "-")

    roots = [
        pkg for pkg in graph.adjacency_list
        if pkg.name == target_name
    ]

    seen: set[Package] = set()
    queue: deque[Package] = deque()

    for root in roots:
        queue.extend(graph.adjacency_list.get(root, set()))

    while queue:
        dep = queue.popleft()

        if dep in seen:
            continue

        seen.add(dep)
        queue.extend(graph.adjacency_list.get(dep, set()))

    return seen

