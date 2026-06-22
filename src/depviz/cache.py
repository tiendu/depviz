"""Compatibility imports for inspection cache support."""

from depviz.infrastructure.inspection_cache import (
    CACHE_VERSION,
    default_cache_path,
    load_graph_cache,
    load_inspection_cache,
    manifest_hash,
    save_graph_cache,
    save_inspection_cache,
)

__all__ = [
    "CACHE_VERSION",
    "default_cache_path",
    "load_graph_cache",
    "load_inspection_cache",
    "manifest_hash",
    "save_graph_cache",
    "save_inspection_cache",
]
