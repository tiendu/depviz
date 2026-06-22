from __future__ import annotations

import tomllib
from pathlib import Path

from depviz.api import (
    DependencyIntent,
    Diagnostic,
    OperationContext,
    Requirement,
    Severity,
    SourceLocation,
)
from depviz.builtin.manifests.common import find_conflicting_duplicates, parse_packaging_requirement


class PyprojectManifestLoader:
    name = "pyproject-toml"
    formats = frozenset({"pyproject.toml"})

    def supports(self, path: Path) -> bool:
        return path.name.lower() == "pyproject.toml"

    def load(self, path: Path, context: OperationContext) -> DependencyIntent:
        try:
            document = tomllib.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            return _failed(path, f"Cannot read pyproject.toml: {exc}")
        except tomllib.TOMLDecodeError as exc:
            return _failed(path, f"Invalid pyproject.toml: {exc}")
        if not isinstance(document, dict):
            return _failed(path, "pyproject.toml root must be a table")

        diagnostics: list[Diagnostic] = []
        requirements: list[Requirement] = []
        project = document.get("project", {})
        if not isinstance(project, dict):
            diagnostics.append(
                _error(path, "manifest.pyproject.project", "[project] must be a table")
            )
            project = {}
        requirements.extend(
            _parse_list(project.get("dependencies", []), path, "project.dependencies", diagnostics)
        )

        extras = _selected(context, "python.extras")
        optional = project.get("optional-dependencies", {})
        if extras:
            if not isinstance(optional, dict):
                diagnostics.append(
                    _error(
                        path,
                        "manifest.pyproject.optional",
                        "[project.optional-dependencies] must be a table",
                    )
                )
            else:
                for extra in extras:
                    if extra not in optional:
                        diagnostics.append(
                            _error(
                                path,
                                "manifest.pyproject.extra-missing",
                                f"Unknown optional dependency extra {extra!r}",
                            )
                        )
                        continue
                    requirements.extend(
                        _parse_list(
                            optional[extra],
                            path,
                            f"project.optional-dependencies.{extra}",
                            diagnostics,
                        )
                    )

        groups = _selected(context, "python.groups")
        group_table = document.get("dependency-groups", {})
        if groups:
            if not isinstance(group_table, dict):
                diagnostics.append(
                    _error(path, "manifest.pyproject.groups", "[dependency-groups] must be a table")
                )
            else:
                for group in groups:
                    requirements.extend(
                        _expand_group(group, group_table, path, diagnostics, stack=())
                    )

        tool = document.get("tool", {})
        if isinstance(tool, dict):
            uv = tool.get("uv", {})
            if isinstance(uv, dict) and uv.get("sources"):
                diagnostics.append(
                    _error(
                        path,
                        "manifest.pyproject.uv-sources",
                        "[tool.uv.sources] is not supported yet; use immutable hashed direct URLs",
                    )
                )

        diagnostics.extend(find_conflicting_duplicates(requirements))
        metadata: dict[str, str] = {"manifest": "pyproject.toml"}
        requires_python = project.get("requires-python")
        if isinstance(requires_python, str):
            metadata["requires-python"] = requires_python
        return DependencyIntent(
            requirements=tuple(requirements),
            metadata=metadata,
            diagnostics=tuple(diagnostics),
        )


def _expand_group(
    group: str,
    groups: dict[object, object],
    path: Path,
    diagnostics: list[Diagnostic],
    *,
    stack: tuple[str, ...],
) -> list[Requirement]:
    if group in stack:
        diagnostics.append(
            _error(path, "manifest.pyproject.group-cycle", f"Dependency group cycle at {group!r}")
        )
        return []
    if group not in groups:
        diagnostics.append(
            _error(path, "manifest.pyproject.group-missing", f"Unknown dependency group {group!r}")
        )
        return []
    value = groups[group]
    if not isinstance(value, list):
        diagnostics.append(
            _error(path, "manifest.pyproject.group", f"Dependency group {group!r} must be a list")
        )
        return []
    result: list[Requirement] = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            result.extend(
                _parse_list([item], path, f"dependency-groups.{group}[{index}]", diagnostics)
            )
            continue
        if isinstance(item, dict) and isinstance(item.get("include-group"), str):
            result.extend(
                _expand_group(
                    item["include-group"],
                    groups,
                    path,
                    diagnostics,
                    stack=(*stack, group),
                )
            )
            continue
        diagnostics.append(
            _error(
                path,
                "manifest.pyproject.group-entry",
                f"Unsupported dependency group entry in {group!r} at index {index}",
            )
        )
    return result


def _parse_list(
    value: object,
    path: Path,
    label: str,
    diagnostics: list[Diagnostic],
) -> list[Requirement]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        diagnostics.append(
            _error(path, "manifest.pyproject.dependencies", f"{label} must be a list of strings")
        )
        return []
    result: list[Requirement] = []
    for index, text in enumerate(value, start=1):
        requirement, diagnostic = parse_packaging_requirement(
            text,
            ecosystem="pypi",
            origin=SourceLocation(path, index),
            direct=True,
        )
        if diagnostic is not None:
            diagnostics.append(diagnostic)
        elif requirement is not None:
            result.append(requirement)
    return result


def _selected(context: OperationContext, name: str) -> tuple[str, ...]:
    raw = context.configuration.get(name, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _failed(path: Path, message: str) -> DependencyIntent:
    return DependencyIntent(
        requirements=(), diagnostics=(_error(path, "manifest.pyproject.invalid", message),)
    )


def _error(path: Path, code: str, message: str) -> Diagnostic:
    return Diagnostic(
        code=code, message=message, severity=Severity.ERROR, source=SourceLocation(path)
    )
