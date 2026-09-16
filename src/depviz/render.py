from __future__ import annotations

import json
from dataclasses import asdict

from depviz import __version__
from depviz.model import Inventory, PackageKey, PackageRisk, name_matches


def _label(key: PackageKey) -> str:
    if key.ecosystem in {"conda", "manifest"}:
        return key.name
    return f"{key.name} [pip]"


def render_text(
    inventory: Inventory,
    results: list[PackageRisk],
    roots: set[PackageKey],
    missing_roots: list[str],
    focus: str | None = None,
) -> str:
    lines: list[str] = []
    lines.append(f"depviz: {inventory.source}")
    lines.append(f"packages: {len(inventory.packages)}  roots: {len(roots)}")
    if missing_roots:
        lines.append("missing declared roots: " + ", ".join(sorted(missing_roots)))
    for diagnostic in inventory.diagnostics:
        lines.append("warning: " + diagnostic)
    lines.append("")

    if focus:
        matches = [row for row in results if name_matches(row.package, focus)]
        if not matches:
            return "\n".join(lines + [f"package not found: {focus}"])
        for idx, row in enumerate(matches):
            if idx:
                lines.append("")
            lines.extend(_render_detail(row, show_paths=True))
        return "\n".join(lines)

    lines.append(f"{'ROOTS':>5} {'TOTAL':>5} {'DIRECT':>6} {'VERSION':>9}  PACKAGE")
    for row in results:
        lines.append(
            f"{len(row.affected_roots):>5} "
            f"{row.transitive_count:>5} "
            f"{len(row.direct_dependents):>6} "
            f"{row.version_state:>9}  {_label(row.package)}"
        )

    problems = [
        row
        for row in results
        if row.version_state in {"CONFLICT", "INVALID", "UNKNOWN", "MISSING"}
    ]
    if problems:
        lines.append("")
        lines.append("version constraints")
        for row in problems:
            lines.append("")
            lines.extend(_render_detail(row))
    return "\n".join(lines)


def _render_detail(row: PackageRisk, *, show_paths: bool = False) -> list[str]:
    version = row.version if row.version is not None else "(version unknown)"
    if not row.installed:
        version = "(not installed)"
    lines = [
        f"{_label(row.package)} {version}",
        f"  roots affected: {len(row.affected_roots)}"
        + (
            " (" + ", ".join(_label(root) for root in row.affected_roots) + ")"
            if row.affected_roots
            else ""
        ),
        f"  transitive dependents: {row.transitive_count}",
        f"  direct dependents: {len(row.direct_dependents)}",
        f"  version: {row.version_state}",
    ]
    if show_paths and row.root_paths:
        lines.append("  paths:")
        visible_paths = row.root_paths[:20]
        for path in visible_paths:
            lines.append("    " + " -> ".join(_label(key) for key in path))
        hidden = len(row.root_paths) - len(visible_paths)
        if hidden:
            lines.append(f"    ... {hidden} more root paths (all paths are present in --json)")
    if row.conflict_by:
        lines.append("  conflict:")
        for contributor in row.conflict_by:
            lines.append(f"    {_label(contributor.package)} -> {contributor.raw}")
    if row.contributors:
        lines.append("  constraints:")
        violated = {(item.package, item.raw) for item in row.violated_by}
        conflict = {(item.package, item.raw) for item in row.conflict_by}
        for contributor in row.contributors:
            markers: list[str] = []
            if (contributor.package, contributor.raw) in conflict:
                markers.append("conflict")
            if (contributor.package, contributor.raw) in violated:
                markers.append("violated")
            marker = f" ! {'/'.join(markers)}" if markers else ""
            lines.append(f"    {_label(contributor.package)} -> {contributor.raw}{marker}")
    return lines


def render_json(
    inventory: Inventory,
    results: list[PackageRisk],
    roots: set[PackageKey],
    missing_roots: list[str],
    focus: str | None = None,
) -> str:
    selected = [
        row for row in results if focus is None or name_matches(row.package, focus)
    ]
    payload = {
        "schema_version": 1,
        "depviz_version": __version__,
        "source": inventory.source,
        "package_count": len(inventory.packages),
        "roots": [asdict(root) for root in sorted(roots)],
        "missing_declared_roots": sorted(missing_roots),
        "diagnostics": inventory.diagnostics,
        "packages": [
            {
                "package": asdict(row.package),
                "version": row.version,
                "installed": row.installed,
                "roots_affected": [asdict(root) for root in row.affected_roots],
                "transitive_dependents_count": row.transitive_count,
                **(
                    {"transitive_dependents": [asdict(key) for key in row.transitive_dependents]}
                    if focus is not None
                    else {}
                ),
                "direct_dependents": [asdict(key) for key in row.direct_dependents],
                "root_paths": [
                    [asdict(key) for key in path]
                    for path in row.root_paths
                ],
                "version_state": row.version_state,
                "constraints": [
                    {
                        "package": asdict(contributor.package),
                        "raw": contributor.raw,
                        "specifier": contributor.specifier,
                        "violated": contributor in row.violated_by,
                        "conflict": contributor in row.conflict_by,
                    }
                    for contributor in row.contributors
                ],
            }
            for row in selected
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)
