from dataclasses import dataclass

from depviz.api import CommandRunner, PluginCatalog, RuntimeTools


@dataclass(frozen=True)
class ApplicationServices:
    registry: PluginCatalog
    command_runner: CommandRunner
    runtime_tools: RuntimeTools | None = None
