from __future__ import annotations

import hashlib
import json
import os
import platform
import tempfile
from pathlib import Path
from typing import Any

from depviz.api import Diagnostic, Severity, SourceLocation
from depviz.analysis.graph import DependencyGraph, GraphInspection, InspectionStatus, Package
from depviz.builtin.manifests.common import ParseResult
from depviz.infrastructure.storage import read_text_limited

CACHE_VERSION = 2


def manifest_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def default_cache_path(manifest_path: Path, depth: int) -> Path:
    cache_dir = manifest_path.parent / ".depviz"
    digest = manifest_hash(manifest_path)
    target = f"{platform.system().lower()}-{platform.machine().lower()}"
    return cache_dir / f"inspection-v{CACHE_VERSION}-{target}-{digest}-depth{depth}.json"


def save_inspection_cache(
    inspection: GraphInspection,
    parse_result: ParseResult,
    cache_path: Path,
) -> None:
    graph = inspection.graph
    edge_count = sum(len(children) for children in graph.adjacency_list.values())
    data: dict[str, Any] = {
        "version": CACHE_VERSION,
        "backend": inspection.backend,
        "status": inspection.status.value,
        "requested_depth": inspection.requested_depth,
        "channels": parse_result.channels,
        "package_count": len(graph.adjacency_list),
        "edge_count": edge_count,
        "diagnostics": [_serialize_diagnostic(item) for item in inspection.diagnostics],
        "packages": [
            {
                "name": package.name,
                "ecosystem": package.ecosystem,
                "constraint": package.constraint,
                "source": package.source,
            }
            for package in sorted(
                graph.adjacency_list,
                key=lambda item: (item.ecosystem, item.name),
            )
        ],
        "edges": [
            {
                "parent": _package_key(parent),
                "child": _package_key(child),
            }
            for parent, children in sorted(
                graph.adjacency_list.items(),
                key=lambda item: (item[0].ecosystem, item[0].name),
            )
            for child in sorted(children, key=lambda item: (item.ecosystem, item.name))
        ],
    }
    _write_json_atomic(cache_path, data)


def load_inspection_cache(cache_path: Path) -> GraphInspection:
    try:
        raw = json.loads(read_text_limited(cache_path, label="inspection cache"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read cache {cache_path}: {error}") from error

    if not isinstance(raw, dict):
        raise ValueError(f"Invalid cache document: {cache_path}")
    if raw.get("version") != CACHE_VERSION:
        raise ValueError(f"Unsupported cache version in {cache_path}")

    status_text = raw.get("status")
    if not isinstance(status_text, str):
        raise ValueError(f"Invalid inspection status in cache: {cache_path}")
    try:
        status = InspectionStatus(status_text)
    except (ValueError, TypeError) as error:
        raise ValueError(f"Invalid inspection status in cache: {cache_path}") from error

    requested_depth = raw.get("requested_depth")
    if not isinstance(requested_depth, int) or requested_depth < 0:
        raise ValueError(f"Invalid requested depth in cache: {cache_path}")

    backend = raw.get("backend")
    if not isinstance(backend, str) or not backend:
        raise ValueError(f"Invalid backend identifier in cache: {cache_path}")

    graph = DependencyGraph()
    package_by_key: dict[str, Package] = {}
    packages = raw.get("packages")
    if not isinstance(packages, list):
        raise ValueError(f"Invalid package list in cache: {cache_path}")

    for item in packages:
        if not isinstance(item, dict):
            raise ValueError(f"Invalid package entry in cache: {cache_path}")
        name = item.get("name")
        ecosystem = item.get("ecosystem")
        constraint = item.get("constraint")
        source = item.get("source")
        if not isinstance(name, str) or not isinstance(ecosystem, str):
            raise ValueError(f"Invalid package identity in cache: {cache_path}")
        if constraint is not None and not isinstance(constraint, str):
            raise ValueError(f"Invalid package constraint in cache: {cache_path}")
        if source is not None and not isinstance(source, str):
            raise ValueError(f"Invalid package source in cache: {cache_path}")

        package = Package(
            name=name,
            ecosystem=ecosystem,
            constraint=constraint,
            source=source,
        )
        key = _package_key(package)
        if key in package_by_key:
            raise ValueError(f"Duplicate package identity in cache: {key}")
        package_by_key[key] = package
        graph.add_node(package)

    edges = raw.get("edges")
    if not isinstance(edges, list):
        raise ValueError(f"Invalid edge list in cache: {cache_path}")

    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError(f"Invalid edge entry in cache: {cache_path}")
        parent_key = edge.get("parent")
        child_key = edge.get("child")
        if not isinstance(parent_key, str) or not isinstance(child_key, str):
            raise ValueError(f"Invalid edge identity in cache: {cache_path}")
        parent = package_by_key.get(parent_key)
        child = package_by_key.get(child_key)
        if parent is None or child is None:
            raise ValueError(f"Cache edge references an unknown package: {cache_path}")
        graph.add_edge(parent, child)

    raw_diagnostics = raw.get("diagnostics", [])
    if not isinstance(raw_diagnostics, list):
        raise ValueError(f"Invalid diagnostics in cache: {cache_path}")
    diagnostics = tuple(_deserialize_diagnostic(item, cache_path) for item in raw_diagnostics)

    return GraphInspection(
        graph=graph,
        status=status,
        diagnostics=diagnostics,
        requested_depth=requested_depth,
        backend=backend,
    )


def save_graph_cache(
    graph: DependencyGraph,
    parse_result: ParseResult,
    cache_path: Path,
) -> None:
    """Compatibility wrapper for callers using the original cache API."""
    save_inspection_cache(
        GraphInspection(
            graph=graph,
            status=InspectionStatus.APPROXIMATE,
            requested_depth=0,
        ),
        parse_result,
        cache_path,
    )


def load_graph_cache(cache_path: Path) -> DependencyGraph:
    """Compatibility wrapper for callers using the original cache API."""
    return load_inspection_cache(cache_path).graph


def _serialize_diagnostic(diagnostic: Diagnostic) -> dict[str, object]:
    source: dict[str, object] | None = None
    if diagnostic.source is not None:
        source = {
            "path": str(diagnostic.source.path),
            "line": diagnostic.source.line,
            "column": diagnostic.source.column,
        }
    return {
        "code": diagnostic.code,
        "message": diagnostic.message,
        "severity": diagnostic.severity.value,
        "source": source,
        "hint": diagnostic.hint,
    }


def _deserialize_diagnostic(item: object, cache_path: Path) -> Diagnostic:
    if not isinstance(item, dict):
        raise ValueError(f"Invalid diagnostic entry in cache: {cache_path}")
    code = item.get("code")
    message = item.get("message")
    severity_text = item.get("severity")
    hint = item.get("hint")
    if not isinstance(code, str) or not isinstance(message, str):
        raise ValueError(f"Invalid diagnostic content in cache: {cache_path}")
    if hint is not None and not isinstance(hint, str):
        raise ValueError(f"Invalid diagnostic hint in cache: {cache_path}")
    if not isinstance(severity_text, str):
        raise ValueError(f"Invalid diagnostic severity in cache: {cache_path}")
    try:
        severity = Severity(severity_text)
    except (ValueError, TypeError) as error:
        raise ValueError(f"Invalid diagnostic severity in cache: {cache_path}") from error

    source_value = item.get("source")
    source: SourceLocation | None = None
    if source_value is not None:
        if not isinstance(source_value, dict):
            raise ValueError(f"Invalid diagnostic source in cache: {cache_path}")
        path_value = source_value.get("path")
        line = source_value.get("line")
        column = source_value.get("column")
        if not isinstance(path_value, str):
            raise ValueError(f"Invalid diagnostic path in cache: {cache_path}")
        if line is not None and not isinstance(line, int):
            raise ValueError(f"Invalid diagnostic line in cache: {cache_path}")
        if column is not None and not isinstance(column, int):
            raise ValueError(f"Invalid diagnostic column in cache: {cache_path}")
        source = SourceLocation(Path(path_value), line=line, column=column)

    return Diagnostic(
        code=code,
        message=message,
        severity=severity,
        source=source,
        hint=hint,
    )


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _package_key(package: Package) -> str:
    return f"{package.ecosystem}:{package.name}"
