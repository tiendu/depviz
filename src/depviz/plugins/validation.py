from __future__ import annotations

from collections.abc import Iterable

from depviz.api import (
    BackendPlugin,
    Capability,
    EnvironmentDriver,
    EnvironmentInspector,
    HealthCheck,
    LockProvider,
    ManifestLoader,
    PLUGIN_API_VERSION,
    Resolver,
    Verifier,
)
from depviz.api.errors import PluginCompatibilityError, PluginRegistrationError


_CAPABILITY_COMPONENTS: dict[Capability, str] = {
    Capability.LOAD_MANIFEST: "manifest_loaders",
    Capability.INSPECT: "inspectors",
    Capability.RESOLVE: "resolvers",
    Capability.LOCK: "lock_providers",
    Capability.APPLY: "environment_drivers",
    Capability.VERIFY: "verifiers",
}

_COMPONENT_PROTOCOLS: dict[str, type[object]] = {
    "health_checks": HealthCheck,
    "manifest_loaders": ManifestLoader,
    "inspectors": EnvironmentInspector,
    "resolvers": Resolver,
    "lock_providers": LockProvider,
    "environment_drivers": EnvironmentDriver,
    "verifiers": Verifier,
}

_IMPLIED_CAPABILITIES: dict[Capability, Capability] = {
    Capability.OFFLINE_RESOLUTION: Capability.RESOLVE,
    Capability.CROSS_PLATFORM_RESOLUTION: Capability.RESOLVE,
    Capability.HASH_LOCKING: Capability.LOCK,
    Capability.TRANSACTIONAL_APPLY: Capability.APPLY,
}


def validate_plugin(plugin: BackendPlugin) -> None:
    if not plugin.name.strip():
        raise PluginRegistrationError("Plugin name cannot be empty")
    if not plugin.plugin_version.strip():
        raise PluginRegistrationError(f"Plugin {plugin.name!r} has no version")

    expected_major = _major_version(PLUGIN_API_VERSION)
    actual_major = _major_version(plugin.api_version)
    if actual_major != expected_major:
        raise PluginCompatibilityError(
            f"Plugin {plugin.name!r} uses API {plugin.api_version}; "
            f"depviz supports API major {expected_major}"
        )

    for capability, required in _IMPLIED_CAPABILITIES.items():
        if capability in plugin.capabilities and required not in plugin.capabilities:
            raise PluginRegistrationError(
                f"Plugin {plugin.name!r} declares {capability.value!r} "
                f"without required capability {required.value!r}"
            )

    for health_check in plugin.health_checks:
        if not isinstance(health_check, HealthCheck):
            raise PluginRegistrationError(
                f"Plugin {plugin.name!r} component {health_check!r} does not satisfy "
                "the HealthCheck protocol"
            )

    for capability, field_name in _CAPABILITY_COMPONENTS.items():
        components = getattr(plugin, field_name)
        if capability in plugin.capabilities and not components:
            raise PluginRegistrationError(
                f"Plugin {plugin.name!r} declares {capability.value!r} "
                f"but provides no {field_name.replace('_', ' ')}"
            )
        if capability not in plugin.capabilities and components:
            raise PluginRegistrationError(
                f"Plugin {plugin.name!r} provides {field_name.replace('_', ' ')} "
                f"but does not declare {capability.value!r}"
            )

        expected_protocol = _COMPONENT_PROTOCOLS[field_name]
        for capability_component in components:
            if not isinstance(capability_component, expected_protocol):
                raise PluginRegistrationError(
                    f"Plugin {plugin.name!r} component {capability_component!r} does not satisfy "
                    f"the {expected_protocol.__name__} protocol"
                )

    component_names: list[str] = []
    for component_group in _component_groups(plugin):
        for named_component in component_group:
            name = getattr(named_component, "name", None)
            if not isinstance(name, str) or not name.strip():
                raise PluginRegistrationError(
                    f"Plugin {plugin.name!r} contains a component without a non-empty name"
                )
            component_names.append(name)

    duplicates = sorted(name for name in set(component_names) if component_names.count(name) > 1)
    if duplicates:
        raise PluginRegistrationError(
            f"Plugin {plugin.name!r} has duplicate component names: {', '.join(duplicates)}"
        )


def _component_groups(plugin: BackendPlugin) -> Iterable[tuple[object, ...]]:
    yield plugin.health_checks
    yield plugin.manifest_loaders
    yield plugin.inspectors
    yield plugin.resolvers
    yield plugin.lock_providers
    yield plugin.environment_drivers
    yield plugin.verifiers


def _major_version(version: str) -> int:
    try:
        return int(version.split(".", 1)[0])
    except (ValueError, IndexError) as error:
        raise PluginCompatibilityError(f"Invalid plugin API version: {version!r}") from error
