from depviz.analysis.impact import (
    calculate_blast_radius,
    calculate_dependency_weight,
    find_dependencies,
    find_dependents,
    find_transitive_dependencies,
    find_transitive_dependents,
    summarize_graph,
    count_unique_dependencies,
    build_dependency_tree,
)

from depviz.api import (
    ApplyResult,
    BackendPlugin,
    ChangePlan,
    Diagnostic,
    PromotionRecord,
    Resolution,
    ResolvedPackage,
    RollbackResult,
    VerificationReport,
)
from depviz.analysis.graph import DependencyGraph, DependencyTreeNode, GraphInspection, Package


def print_inspection_status(inspection: GraphInspection) -> None:
    print("\nInspection Status")
    print("=" * 40)
    print(f"Status:  {inspection.status.value}")
    print(f"Backend: {inspection.backend}")
    print(f"Depth:   {inspection.requested_depth}")


def print_diagnostics(diagnostics: tuple[Diagnostic, ...]) -> None:
    if not diagnostics:
        return

    print("\nDiagnostics")
    print("=" * 40)
    for diagnostic in diagnostics:
        print(diagnostic.format())


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


def print_tree(
    graph: DependencyGraph,
    package_name: str,
    max_depth: int,
) -> None:
    trees = build_dependency_tree(
        graph=graph,
        target_name=package_name,
        max_depth=max_depth,
    )

    if not trees:
        print(f"No package named '{package_name}' found.")
        return

    print(f"\nDependency Tree for '{package_name}'")
    print("=" * 40)

    for index, tree in enumerate(trees):
        if index > 0:
            print()

        _render_tree(
            tree,
            prefix="",
            is_last=True,
            is_root=True,
        )

    total = count_unique_dependencies(graph, package_name)

    print("\nSummary")
    print("=" * 40)
    print(f"Total unique dependencies: {total}")

    print("\nLegend")
    print("=" * 40)
    print("(*) already shown elsewhere in this tree")


def _render_tree(
    node: DependencyTreeNode,
    prefix: str = "",
    is_last: bool = True,
    is_root: bool = True,
) -> None:
    marker = " (*)" if node.repeated else ""
    label = f"{format_pkg(node.package)}{marker}"

    if is_root:
        print(label)
    else:
        branch = "└── " if is_last else "├── "
        print(f"{prefix}{branch}{label}")

    if node.repeated:
        return

    child_prefix = prefix

    if not is_root:
        child_prefix += "    " if is_last else "│   "

    for index, child in enumerate(node.children):
        _render_tree(
            child,
            prefix=child_prefix,
            is_last=index == len(node.children) - 1,
            is_root=False,
        )


def print_resolution_summary(resolution: Resolution, limit: int) -> None:
    print("Resolution")
    print("=" * 40)
    print(f"Status:   {resolution.status.value}")
    print(f"Platform: {resolution.target.platform}")
    print(f"Packages: {len(resolution.packages)}")
    if resolution.backend is not None:
        print(f"Backend:  {resolution.backend.plugin}/{resolution.backend.component}")
    if limit == 0:
        return
    print()
    for package in resolution.packages[:limit]:
        build = f"={package.build}" if package.build else ""
        source = f"  [{package.source}]" if package.source else ""
        print(f"{package.name}={package.version}{build}{source}")
    remaining = len(resolution.packages) - min(len(resolution.packages), limit)
    if remaining > 0:
        print(f"... {remaining} more package(s)")


def print_plan_summary(plan: ChangePlan, limit: int) -> None:
    from collections import Counter

    counts = Counter(item.kind.value for item in plan.operations)
    changed = [item for item in plan.operations if item.kind.value != "unchanged"]
    print("Change Plan")
    print("=" * 40)
    print(f"Plan ID:   {plan.plan_id}")
    print(f"Platform:  {plan.after.target.platform}")
    print(f"Packages:  {len(plan.after.packages)} desired")
    print(f"Changes:   {len(changed)}")
    print("Operations: " + ", ".join(f"{name}={count}" for name, count in sorted(counts.items())))
    if changed and limit != 0:
        print("\nPackage Changes")
        print("-" * 40)
        for change in changed[:limit]:
            before = _package_version(change.before)
            after = _package_version(change.after)
            details = ",".join(sorted(item.value for item in change.aspects))
            direction = (
                f"/{change.version_direction.value}" if change.version_direction is not None else ""
            )
            suffix = f" ({details}{direction})" if details or direction else ""
            print(f"{change.kind.value:<8} {change.name}: {before} -> {after}{suffix}")
        remaining = len(changed) - min(len(changed), limit)
        if remaining > 0:
            print(f"... {remaining} more change(s)")
    if plan.policy_findings:
        print("\nPolicy Findings")
        print("-" * 40)
        for finding in plan.policy_findings:
            package = f" [{finding.package}]" if finding.package else ""
            print(f"{finding.severity.value.upper()}: {finding.code}{package}: {finding.message}")


def print_plugins(plugins: tuple[BackendPlugin, ...]) -> None:
    print("Registered plugins")
    print("=" * 40)
    for plugin in plugins:
        capabilities = ", ".join(sorted(item.value for item in plugin.capabilities))
        print(f"{plugin.name} {plugin.plugin_version}")
        print(f"  capabilities: {capabilities or 'none'}")
        if plugin.health_checks:
            print(f"  health checks: {', '.join(item.name for item in plugin.health_checks)}")
        if plugin.inspectors:
            print(f"  inspectors: {', '.join(item.name for item in plugin.inspectors)}")
        if plugin.resolvers:
            print(f"  resolvers: {', '.join(item.name for item in plugin.resolvers)}")
        if plugin.lock_providers:
            print(f"  lock providers: {', '.join(item.name for item in plugin.lock_providers)}")
        if plugin.environment_drivers:
            print(
                f"  environment drivers: {', '.join(item.name for item in plugin.environment_drivers)}"
            )
        if plugin.verifiers:
            print(f"  verifiers: {', '.join(item.name for item in plugin.verifiers)}")


def _package_version(package: ResolvedPackage | None) -> str:
    if package is None:
        return "-"
    build = f"={package.build}" if package.build else ""
    return f"{package.version}{build}"


def print_apply_result(result: ApplyResult) -> None:
    if result.candidate is None or result.lock_id is None:
        raise ValueError("Applied result lacks candidate or lock identity")
    print("Candidate Applied")
    print("=" * 40)
    print(f"Candidate ID: {result.candidate.candidate_id}")
    print(f"Path:         {result.candidate.path}")
    print(f"Lock ID:      {result.lock_id}")
    print_diagnostics(result.diagnostics)


def print_verification_report(candidate_id: str, report: VerificationReport) -> None:
    print("Candidate Verification")
    print("=" * 40)
    print(f"Candidate ID: {candidate_id}")
    print(f"Passed:       {'yes' if report.passed else 'no'}")
    print(f"Expected:     {report.expected_state_digest}")
    print(f"Observed:     {report.observed_state_digest or '-'}")
    print_diagnostics(report.diagnostics)


def print_promotion(record: PromotionRecord) -> None:
    print("Deployment Promotion")
    print("=" * 40)
    print(f"Current:  {record.current_candidate_id}")
    print(f"Previous: {record.previous_candidate_id or '-'}")
    print(f"Changed:  {'yes' if record.changed else 'no'}")
    print(f"Path:     {record.deployment.path / 'current'}")


def print_rollback(result: RollbackResult) -> None:
    print("Deployment Rollback")
    print("=" * 40)
    print(f"Current:  {result.current_candidate_id}")
    print(f"Replaced: {result.replaced_candidate_id}")
    print(f"Path:     {result.deployment.path / 'current'}")


def print_deployment_status(
    deployment_path: str,
    current_candidate_id: str | None,
    history: tuple[str, ...],
) -> None:
    print("Deployment Status")
    print("=" * 40)
    print(f"Root:    {deployment_path}")
    print(f"Current: {current_candidate_id or '-'}")
    print(f"History: {len(history)}")
    for candidate_id in reversed(history):
        print(f"- {candidate_id}")
