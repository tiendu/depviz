from depviz.analyzer import (
    calculate_blast_radius,
    calculate_dependency_weight,
    find_dependencies,
    find_dependents,
    find_transitive_dependencies,
    find_transitive_dependents,
    summarize_graph,
)
from depviz.models import DependencyGraph, Package


def format_pkg(pkg: Package) -> str:
    return f"{pkg.name} [{pkg.ecosystem}]"


def print_summary(graph: DependencyGraph) -> None:
    summary = summarize_graph(graph)

    print("\nSummary")
    print("=" * 40)
    print(f"Packages:   {summary['packages']}")
    print(f"Edges:      {summary['edges']}")
    print(f"Ecosystems: {summary['ecosystems']}")


def print_blast_radius(graph: DependencyGraph, limit: int) -> None:
    report = calculate_blast_radius(graph)

    print("\nBlast Radius")
    print("=" * 40)
    print("Packages with many direct dependents.\n")

    shown = 0
    for rank, (pkg, count) in enumerate(report, 1):
        if count <= 0:
            continue

        label = format_pkg(pkg)
        print(f"{rank:>2}. {label:<35} required by {count}")
        shown += 1

        if shown >= limit:
            break

    if shown == 0:
        print("No shared dependencies found.")


def print_dependency_weight(graph: DependencyGraph, limit: int) -> None:
    report = calculate_dependency_weight(graph)

    print("\nDependency Weight")
    print("=" * 40)
    print("Packages that pull in many transitive dependencies.\n")

    shown = 0
    for rank, (pkg, count) in enumerate(report, 1):
        if count <= 0:
            continue

        label = format_pkg(pkg)
        print(f"{rank:>2}. {label:<35} pulls in {count}")
        shown += 1

        if shown >= limit:
            break

    if shown == 0:
        print("No dependency chains found.")


def print_why(graph: DependencyGraph, package_name: str, limit: int) -> None:
    direct = find_dependents(graph, package_name)
    transitive = find_transitive_dependents(graph, package_name)

    print(f"\nWhy is '{package_name}' here?")
    print("=" * 40)

    if not direct and not transitive:
        print("No dependents found in the resolved graph.")
        return

    if direct:
        print("\nDirect dependents:")
        for pkg in direct[:limit]:
            print(f"- {format_pkg(pkg)}")

    if transitive:
        print(f"\nTotal transitive dependents: {len(transitive)}")
        for pkg in sorted(transitive, key=lambda p: (p.ecosystem, p.name))[:limit]:
            print(f"- {format_pkg(pkg)}")


def print_impact(graph: DependencyGraph, package_name: str, limit: int) -> None:
    affected = find_transitive_dependents(graph, package_name)

    print(f"\nImpact of changing '{package_name}'")
    print("=" * 40)

    if not affected:
        print("No affected packages found in the resolved graph.")
        return

    print(f"Potentially affected packages: {len(affected)}\n")

    for pkg in sorted(affected, key=lambda p: (p.ecosystem, p.name))[:limit]:
        print(f"- {format_pkg(pkg)}")


def print_deps(graph: DependencyGraph, package_name: str, limit: int) -> None:
    direct = find_dependencies(graph, package_name)
    transitive = find_transitive_dependencies(graph, package_name)

    print(f"\nDependencies of '{package_name}'")
    print("=" * 40)

    if not direct and not transitive:
        print("No dependencies found in the resolved graph.")
        return

    if direct:
        print("\nDirect dependencies:")
        for pkg in sorted(direct, key=lambda p: (p.ecosystem, p.name))[:limit]:
            print(f"- {format_pkg(pkg)}")

    if transitive:
        print(f"\nTotal transitive dependencies: {len(transitive)}")
        for pkg in sorted(transitive, key=lambda p: (p.ecosystem, p.name))[:limit]:
            print(f"- {format_pkg(pkg)}")

