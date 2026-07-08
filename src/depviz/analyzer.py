"""Compatibility facade for graph analysis.

New code should import from :mod:`depviz.analysis.impact`.
"""

from depviz.analysis.impact import (
    build_dependency_tree,
    calculate_blast_radius,
    calculate_dependency_weight,
    count_unique_dependencies,
    find_dependencies,
    find_dependents,
    find_leaf_packages,
    find_root_packages,
    find_transitive_dependencies,
    find_transitive_dependents,
    summarize_graph,
)

__all__ = [
    "build_dependency_tree",
    "calculate_blast_radius",
    "calculate_dependency_weight",
    "count_unique_dependencies",
    "find_dependencies",
    "find_dependents",
    "find_leaf_packages",
    "find_root_packages",
    "find_transitive_dependencies",
    "find_transitive_dependents",
    "summarize_graph",
]
