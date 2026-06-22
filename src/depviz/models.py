"""Compatibility imports for graph models moved to :mod:`depviz.analysis.graph`."""

from depviz.analysis.graph import (
    DependencyGraph,
    DependencyTreeNode,
    GraphInspection,
    InspectionStatus,
    Package,
)

__all__ = [
    "DependencyGraph",
    "DependencyTreeNode",
    "GraphInspection",
    "InspectionStatus",
    "Package",
]
