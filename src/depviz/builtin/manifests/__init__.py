from pathlib import Path

from depviz.builtin.manifests.common import (
    ManifestParser,
    ParseResult,
    normalize_name,
    parse_conda_dependency,
    parse_pip_dependency,
    parse_requirement_line,
)
from depviz.builtin.manifests.conda_yaml import CondaYamlParser
from depviz.builtin.manifests.plugin import (
    CondaManifestLoader,
    RequirementsManifestLoader,
    create_plugin,
)
from depviz.builtin.manifests.requirements import RequirementsParser


def get_parser(file_path: Path) -> ManifestParser:
    name = file_path.name.lower()
    suffix = file_path.suffix.lower()
    if name.startswith("requirements") and suffix == ".txt":
        return RequirementsParser()
    if suffix in {".yml", ".yaml"}:
        return CondaYamlParser()
    raise ValueError(f"Unsupported manifest type: {file_path}")


__all__ = [
    "CondaManifestLoader",
    "CondaYamlParser",
    "ManifestParser",
    "ParseResult",
    "RequirementsManifestLoader",
    "RequirementsParser",
    "create_plugin",
    "get_parser",
    "normalize_name",
    "parse_conda_dependency",
    "parse_pip_dependency",
    "parse_requirement_line",
]
