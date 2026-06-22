from depviz.builtin import create_conda_plugin, create_manifest_plugin, create_python_plugin
from depviz.plugins.registry import PluginRegistry


def create_default_registry(*, discover_external: bool = True) -> PluginRegistry:
    registry = PluginRegistry()
    registry.register(create_manifest_plugin())
    registry.register(create_conda_plugin())
    registry.register(create_python_plugin())
    if discover_external:
        registry.discover()
    return registry
