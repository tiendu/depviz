"""Compatibility facade for approximate metadata inspection.

New code should import from :mod:`depviz.core.inspection`.
"""

from depviz.core.inspection import (
    CondaFetcher,
    FetcherRegistry,
    FetchResult,
    FetchStatus,
    MetadataFetcher,
    MetadataFetcherProvider,
    PyPIFetcher,
    build_graph_concurrently,
    inspect_dependency_graph,
)

__all__ = [
    "CondaFetcher",
    "FetcherRegistry",
    "FetchResult",
    "FetchStatus",
    "MetadataFetcher",
    "MetadataFetcherProvider",
    "PyPIFetcher",
    "build_graph_concurrently",
    "inspect_dependency_graph",
]
