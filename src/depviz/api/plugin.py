from __future__ import annotations

from dataclasses import dataclass

from depviz.api.capabilities import Capability
from depviz.api.protocols import (
    EnvironmentDriver,
    EnvironmentInspector,
    HealthCheck,
    LockProvider,
    ManifestLoader,
    Resolver,
    Verifier,
)


@dataclass(frozen=True)
class BackendPlugin:
    name: str
    plugin_version: str
    api_version: str
    capabilities: frozenset[Capability]
    health_checks: tuple[HealthCheck, ...] = ()
    manifest_loaders: tuple[ManifestLoader, ...] = ()
    inspectors: tuple[EnvironmentInspector, ...] = ()
    resolvers: tuple[Resolver, ...] = ()
    lock_providers: tuple[LockProvider, ...] = ()
    environment_drivers: tuple[EnvironmentDriver, ...] = ()
    verifiers: tuple[Verifier, ...] = ()
