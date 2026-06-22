from __future__ import annotations

from pathlib import Path

from depviz import __version__
from depviz.api import (
    BackendPlugin,
    Capability,
    DependencyIntent,
    OperationContext,
    PLUGIN_API_VERSION,
)
from depviz.builtin.manifests.conda_yaml import CondaYamlParser
from depviz.builtin.manifests.requirements import RequirementsParser
from depviz.builtin.manifests.pyproject import PyprojectManifestLoader


class RequirementsManifestLoader:
    name = "requirements-txt"
    formats = frozenset({"requirements.txt", "requirements.in"})

    def supports(self, path: Path) -> bool:
        name = path.name.lower()
        return name.startswith("requirements") and path.suffix.lower() in {".txt", ".in"}

    def load(self, path: Path, context: OperationContext) -> DependencyIntent:
        del context
        return RequirementsParser().parse(path).intent


class CondaManifestLoader:
    name = "conda-environment-yaml"
    formats = frozenset({"environment.yml", "environment.yaml", "conda.yml", "conda.yaml"})

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in {".yml", ".yaml"}

    def load(self, path: Path, context: OperationContext) -> DependencyIntent:
        del context
        return CondaYamlParser().parse(path).intent


def create_plugin() -> BackendPlugin:
    return BackendPlugin(
        name="depviz-manifests",
        plugin_version=__version__,
        api_version=PLUGIN_API_VERSION,
        capabilities=frozenset({Capability.LOAD_MANIFEST}),
        manifest_loaders=(
            RequirementsManifestLoader(),
            PyprojectManifestLoader(),
            CondaManifestLoader(),
        ),
    )
