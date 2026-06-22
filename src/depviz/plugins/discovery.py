from __future__ import annotations

from importlib import metadata

from depviz.api import BackendPlugin
from depviz.api.errors import PluginRegistrationError

ENTRY_POINT_GROUP = "depviz.backends"


def discover_plugins() -> tuple[BackendPlugin, ...]:
    plugins: list[BackendPlugin] = []
    for entry_point in metadata.entry_points().select(group=ENTRY_POINT_GROUP):
        factory = entry_point.load()
        plugin = factory()
        if not isinstance(plugin, BackendPlugin):
            raise PluginRegistrationError(
                f"Entry point {entry_point.name!r} did not return BackendPlugin"
            )
        plugins.append(plugin)
    return tuple(plugins)
