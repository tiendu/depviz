from __future__ import annotations

import tomllib
from pathlib import Path

import depviz
from depviz.plugins.defaults import create_default_registry

pytestmark = __import__("pytest").mark.hardening


def test_package_and_builtin_plugin_versions_match_pyproject() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    expected = project["project"]["version"]

    assert depviz.__version__ == expected
    assert {
        plugin.plugin_version
        for plugin in create_default_registry(discover_external=False).plugins()
    } == {expected}
