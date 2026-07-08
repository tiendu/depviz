from __future__ import annotations

import re
from importlib.metadata import version as distribution_version
from pathlib import Path

import depviz
from depviz.plugins.defaults import create_default_registry

pytestmark = __import__("pytest").mark.hardening

ROOT = Path(__file__).resolve().parents[2]


def test_distribution_and_builtin_plugin_versions_match_runtime_version() -> None:
    expected = depviz.__version__

    assert distribution_version("depviz") == expected
    assert {
        plugin.plugin_version
        for plugin in create_default_registry(discover_external=False).plugins()
    } == {expected}


def test_changelog_leads_with_runtime_version() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(r"^##\s+(\S+)\s*$", changelog, flags=re.MULTILINE)

    assert match is not None, "CHANGELOG.md has no release heading"
    assert match.group(1) == depviz.__version__


def test_readme_does_not_embed_the_current_release_number() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert depviz.__version__ not in readme
