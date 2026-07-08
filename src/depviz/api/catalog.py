from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from depviz.api.plugin import BackendPlugin
from depviz.api.protocols import (
    EnvironmentDriver,
    EnvironmentInspector,
    LockProvider,
    ManifestLoader,
    Resolver,
    Verifier,
)


@runtime_checkable
class PluginCatalog(Protocol):
    """Read-only plugin lookup surface used by application code."""

    def plugins(self) -> tuple[BackendPlugin, ...]: ...

    def find_manifest_loader(self, path: Path) -> ManifestLoader: ...

    def find_resolver(self, name: str) -> Resolver: ...

    def find_resolver_entry(self, name: str) -> tuple[BackendPlugin, Resolver]: ...

    def find_inspector(self, name: str) -> EnvironmentInspector: ...

    def find_inspector_entry(self, name: str) -> tuple[BackendPlugin, EnvironmentInspector]: ...

    def find_lock_provider(self, name: str) -> LockProvider: ...

    def find_lock_provider_entry(self, name: str) -> tuple[BackendPlugin, LockProvider]: ...

    def find_environment_driver(self, name: str) -> EnvironmentDriver: ...

    def find_environment_driver_entry(
        self, name: str
    ) -> tuple[BackendPlugin, EnvironmentDriver]: ...

    def find_verifier(self, name: str) -> Verifier: ...

    def find_verifier_entry(self, name: str) -> tuple[BackendPlugin, Verifier]: ...
