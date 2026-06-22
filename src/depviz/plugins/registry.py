from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from depviz.api import (
    BackendPlugin,
    EnvironmentDriver,
    EnvironmentInspector,
    LockProvider,
    ManifestLoader,
    Resolver,
    Verifier,
)
from depviz.api.errors import PluginRegistrationError, UnsupportedManifest
from depviz.plugins.discovery import discover_plugins
from depviz.plugins.validation import validate_plugin


_Component = TypeVar("_Component")


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, BackendPlugin] = {}

    def register(self, plugin: BackendPlugin) -> None:
        validate_plugin(plugin)
        if plugin.name in self._plugins:
            raise PluginRegistrationError(f"Plugin {plugin.name!r} is already registered")
        self._plugins[plugin.name] = plugin

    def discover(self) -> None:
        for plugin in discover_plugins():
            self.register(plugin)

    def plugins(self) -> tuple[BackendPlugin, ...]:
        return tuple(self._plugins[name] for name in sorted(self._plugins))

    def find_manifest_loader(self, path: Path) -> ManifestLoader:
        matches = [
            loader
            for plugin in self.plugins()
            for loader in plugin.manifest_loaders
            if loader.supports(path)
        ]
        if not matches:
            raise UnsupportedManifest(
                backend="registry",
                operation="load manifest",
                message=f"No registered manifest loader supports {path}",
            )
        if len(matches) > 1:
            names = ", ".join(sorted(loader.name for loader in matches))
            raise PluginRegistrationError(f"Multiple manifest loaders support {path}: {names}")
        return matches[0]

    def find_resolver(self, name: str) -> Resolver:
        return self.find_resolver_entry(name)[1]

    def find_resolver_entry(self, name: str) -> tuple[BackendPlugin, Resolver]:
        matches = [
            (plugin, resolver)
            for plugin in self.plugins()
            for resolver in plugin.resolvers
            if resolver.name == name
        ]
        return _one_component("resolver", name, matches)

    def find_inspector(self, name: str) -> EnvironmentInspector:
        return self.find_inspector_entry(name)[1]

    def find_inspector_entry(self, name: str) -> tuple[BackendPlugin, EnvironmentInspector]:
        matches = [
            (plugin, inspector)
            for plugin in self.plugins()
            for inspector in plugin.inspectors
            if inspector.name == name
        ]
        return _one_component("inspector", name, matches)

    def find_lock_provider(self, name: str) -> LockProvider:
        return self.find_lock_provider_entry(name)[1]

    def find_lock_provider_entry(self, name: str) -> tuple[BackendPlugin, LockProvider]:
        matches = [
            (plugin, provider)
            for plugin in self.plugins()
            for provider in plugin.lock_providers
            if provider.name == name
        ]
        return _one_component("lock provider", name, matches)

    def find_environment_driver(self, name: str) -> EnvironmentDriver:
        return self.find_environment_driver_entry(name)[1]

    def find_environment_driver_entry(self, name: str) -> tuple[BackendPlugin, EnvironmentDriver]:
        matches = [
            (plugin, driver)
            for plugin in self.plugins()
            for driver in plugin.environment_drivers
            if driver.name == name
        ]
        return _one_component("environment driver", name, matches)

    def find_verifier(self, name: str) -> Verifier:
        return self.find_verifier_entry(name)[1]

    def find_verifier_entry(self, name: str) -> tuple[BackendPlugin, Verifier]:
        matches = [
            (plugin, verifier)
            for plugin in self.plugins()
            for verifier in plugin.verifiers
            if verifier.name == name
        ]
        return _one_component("verifier", name, matches)


def _one_component(
    component_type: str,
    name: str,
    matches: list[tuple[BackendPlugin, _Component]],
) -> tuple[BackendPlugin, _Component]:
    if not matches:
        raise PluginRegistrationError(f"No registered {component_type} named {name!r}")
    if len(matches) > 1:
        raise PluginRegistrationError(f"Multiple registered {component_type}s are named {name!r}")
    return matches[0]
