"""Compatibility imports for resolution persistence moved to :mod:`depviz.core.resolution`."""

from depviz.core.resolution import (
    RESOLUTION_SCHEMA_VERSION,
    read_resolution_json,
    resolution_from_dict,
    resolution_to_dict,
    resolution_to_json,
    write_resolution_json,
)

__all__ = [
    "RESOLUTION_SCHEMA_VERSION",
    "read_resolution_json",
    "resolution_from_dict",
    "resolution_to_dict",
    "resolution_to_json",
    "write_resolution_json",
]
