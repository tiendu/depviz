from dataclasses import dataclass

from depviz.api import CommandRunner
from depviz.plugins.registry import PluginRegistry


@dataclass(frozen=True)
class ApplicationServices:
    registry: PluginRegistry
    command_runner: CommandRunner
