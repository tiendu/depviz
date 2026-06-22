from pathlib import Path

import pytest

from depviz.api import BackendPlugin, Capability, PLUGIN_API_VERSION
from depviz.api.errors import PluginRegistrationError
from depviz.builtin.manifests import CondaManifestLoader, RequirementsManifestLoader
from depviz.plugins.defaults import create_default_registry
from depviz.plugins.registry import PluginRegistry
from depviz.plugins.validation import validate_plugin


def test_default_registry_selects_requirements_loader() -> None:
    registry = create_default_registry(discover_external=False)

    loader = registry.find_manifest_loader(Path("requirements.txt"))

    assert isinstance(loader, RequirementsManifestLoader)


def test_default_registry_selects_conda_loader() -> None:
    registry = create_default_registry(discover_external=False)

    loader = registry.find_manifest_loader(Path("environment.yml"))

    assert isinstance(loader, CondaManifestLoader)


def test_plugin_must_provide_components_for_declared_capability() -> None:
    plugin = BackendPlugin(
        name="broken",
        plugin_version="1.0.0",
        api_version=PLUGIN_API_VERSION,
        capabilities=frozenset({Capability.RESOLVE}),
    )

    with pytest.raises(PluginRegistrationError, match="provides no resolvers"):
        validate_plugin(plugin)


def test_registry_rejects_duplicate_plugin_name() -> None:
    registry = PluginRegistry()
    plugin = BackendPlugin(
        name="empty-but-valid",
        plugin_version="1.0.0",
        api_version=PLUGIN_API_VERSION,
        capabilities=frozenset(),
    )

    registry.register(plugin)

    with pytest.raises(PluginRegistrationError, match="already registered"):
        registry.register(plugin)


def test_plugin_component_must_satisfy_runtime_protocol() -> None:
    plugin = BackendPlugin(
        name="wrong-component",
        plugin_version="1.0.0",
        api_version=PLUGIN_API_VERSION,
        capabilities=frozenset({Capability.LOAD_MANIFEST}),
        manifest_loaders=(object(),),  # type: ignore[arg-type]
    )

    with pytest.raises(PluginRegistrationError, match="ManifestLoader protocol"):
        validate_plugin(plugin)


def test_guarantee_capability_requires_base_operation() -> None:
    plugin = BackendPlugin(
        name="impossible-guarantee",
        plugin_version="1.0.0",
        api_version=PLUGIN_API_VERSION,
        capabilities=frozenset({Capability.TRANSACTIONAL_APPLY}),
    )

    with pytest.raises(PluginRegistrationError, match="without required capability"):
        validate_plugin(plugin)


def test_default_registry_exposes_conda_resolver() -> None:
    registry = create_default_registry(discover_external=False)

    resolver = registry.find_resolver("conda-dry-run")

    assert resolver.name == "conda-dry-run"
