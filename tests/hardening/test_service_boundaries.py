from __future__ import annotations

from pathlib import Path

import pytest

from depviz.api import (
    Command,
    CommandResult,
    OperationContext,
    PluginCatalog,
    require_command_runner,
)
from depviz.api.errors import ToolUnavailable
from depviz.cli.services import ApplicationServices
from depviz.plugins.defaults import create_default_registry

pytestmark = pytest.mark.hardening


class RecordingRunner:
    def run(
        self,
        command: Command,
        *,
        timeout_seconds: float,
        output_limit: int,
        redact: tuple[str, ...] = (),
    ) -> CommandResult:
        del timeout_seconds, output_limit, redact
        return CommandResult(command.argv, 0, "", "", 0.0)


class StaticCatalog:
    def __init__(self, delegate: PluginCatalog) -> None:
        self._delegate = delegate

    def plugins(self):  # type: ignore[no-untyped-def]
        return self._delegate.plugins()

    def find_manifest_loader(self, path: Path):  # type: ignore[no-untyped-def]
        return self._delegate.find_manifest_loader(path)

    def find_resolver(self, name: str):  # type: ignore[no-untyped-def]
        return self._delegate.find_resolver(name)

    def find_resolver_entry(self, name: str):  # type: ignore[no-untyped-def]
        return self._delegate.find_resolver_entry(name)

    def find_inspector(self, name: str):  # type: ignore[no-untyped-def]
        return self._delegate.find_inspector(name)

    def find_inspector_entry(self, name: str):  # type: ignore[no-untyped-def]
        return self._delegate.find_inspector_entry(name)

    def find_lock_provider(self, name: str):  # type: ignore[no-untyped-def]
        return self._delegate.find_lock_provider(name)

    def find_lock_provider_entry(self, name: str):  # type: ignore[no-untyped-def]
        return self._delegate.find_lock_provider_entry(name)

    def find_environment_driver(self, name: str):  # type: ignore[no-untyped-def]
        return self._delegate.find_environment_driver(name)

    def find_environment_driver_entry(self, name: str):  # type: ignore[no-untyped-def]
        return self._delegate.find_environment_driver_entry(name)

    def find_verifier(self, name: str):  # type: ignore[no-untyped-def]
        return self._delegate.find_verifier(name)

    def find_verifier_entry(self, name: str):  # type: ignore[no-untyped-def]
        return self._delegate.find_verifier_entry(name)


def test_command_runner_must_be_injected_at_command_boundary() -> None:
    with pytest.raises(ToolUnavailable, match="No command runner"):
        require_command_runner(OperationContext(), backend="test", operation="run")


def test_application_services_accepts_structural_plugin_catalog() -> None:
    catalog = StaticCatalog(create_default_registry(discover_external=False))
    runner = RecordingRunner()

    assert isinstance(catalog, PluginCatalog)
    services = ApplicationServices(registry=catalog, command_runner=runner)

    assert services.registry.plugins()
    assert services.command_runner is runner
