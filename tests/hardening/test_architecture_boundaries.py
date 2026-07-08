from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.hardening

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "depviz"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


def test_concrete_command_runner_exists_only_at_composition_and_infrastructure() -> None:
    allowed = {
        SOURCE / "main.py",
        SOURCE / "infrastructure" / "__init__.py",
        SOURCE / "infrastructure" / "commands.py",
    }
    offenders: list[str] = []
    for path in SOURCE.rglob("*.py"):
        if path in allowed:
            continue
        if "LocalCommandRunner" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []


def test_compatibility_facades_do_not_use_wildcard_imports() -> None:
    facades = (
        "analyzer.py",
        "cache.py",
        "fetchers.py",
        "models.py",
        "parsers.py",
        "renderer.py",
        "serialization.py",
    )
    offenders: list[str] = []
    for name in facades:
        path = SOURCE / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
            for node in ast.walk(tree)
        ):
            offenders.append(name)

    assert offenders == []


def test_api_layer_does_not_import_implementation_layers() -> None:
    forbidden_prefixes = (
        "depviz.builtin",
        "depviz.cli",
        "depviz.core",
        "depviz.infrastructure",
        "depviz.plugins",
    )
    offenders: list[str] = []
    for path in (SOURCE / "api").glob("*.py"):
        for imported in _imports(path):
            if imported.startswith(forbidden_prefixes):
                offenders.append(f"{path.name}: {imported}")

    assert offenders == []
