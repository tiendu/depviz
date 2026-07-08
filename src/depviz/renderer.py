"""Compatibility facade for terminal rendering.

New code should import from :mod:`depviz.cli.rendering`.
"""

from depviz.cli.rendering import (
    print_inspection_status,
    print_diagnostics,
    format_pkg,
    print_summary,
    print_blast_radius,
    print_dependency_weight,
    print_why,
    print_impact,
    print_deps,
    print_tree,
    print_resolution_summary,
    print_plan_summary,
    print_plugins,
    print_apply_result,
    print_verification_report,
    print_promotion,
    print_rollback,
    print_deployment_status,
)

__all__ = [
    "print_inspection_status",
    "print_diagnostics",
    "format_pkg",
    "print_summary",
    "print_blast_radius",
    "print_dependency_weight",
    "print_why",
    "print_impact",
    "print_deps",
    "print_tree",
    "print_resolution_summary",
    "print_plan_summary",
    "print_plugins",
    "print_apply_result",
    "print_verification_report",
    "print_promotion",
    "print_rollback",
    "print_deployment_status",
]
