"""Compatibility imports for the pre-0.5 manifest parser module."""

from depviz.builtin.manifests import (
    CondaYamlParser,
    ManifestParser,
    ParseResult,
    RequirementsParser,
    get_parser,
    normalize_name,
    parse_conda_dependency,
    parse_pip_dependency,
    parse_requirement_line,
)

__all__ = [
    "CondaYamlParser",
    "ManifestParser",
    "ParseResult",
    "RequirementsParser",
    "get_parser",
    "normalize_name",
    "parse_conda_dependency",
    "parse_pip_dependency",
    "parse_requirement_line",
]
