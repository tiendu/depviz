from __future__ import annotations

import hashlib
import json
import platform as host_platform
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from depviz.api import (
    BackendIdentity,
    BackendPayload,
    DependencyIntent,
    Diagnostic,
    EnvironmentState,
    EnvironmentTarget,
    OperationContext,
    PackageReference,
    Requirement,
    Resolution,
    ResolutionStatus,
    ResolvedPackage,
    Resolver,
    Severity,
    SourceLocation,
    Target,
)
from depviz.api.errors import ResolutionFailed
from depviz.infrastructure.storage import read_text_limited, write_bytes_atomic

RESOLUTION_SCHEMA_VERSION = 1


def host_conda_platform() -> str:
    system = host_platform.system().lower()
    machine = host_platform.machine().lower()
    mapping = {
        ("linux", "x86_64"): "linux-64",
        ("linux", "amd64"): "linux-64",
        ("linux", "aarch64"): "linux-aarch64",
        ("linux", "arm64"): "linux-aarch64",
        ("linux", "ppc64le"): "linux-ppc64le",
        ("linux", "s390x"): "linux-s390x",
        ("darwin", "x86_64"): "osx-64",
        ("darwin", "arm64"): "osx-arm64",
        ("darwin", "aarch64"): "osx-arm64",
        ("windows", "amd64"): "win-64",
        ("windows", "x86_64"): "win-64",
        ("windows", "x86"): "win-32",
        ("windows", "i386"): "win-32",
        ("windows", "arm64"): "win-arm64",
    }
    try:
        return mapping[(system, machine)]
    except KeyError as error:
        raise RuntimeError(
            f"Cannot infer a Conda platform for host system={system!r}, machine={machine!r}"
        ) from error


def resolve_intent(
    *,
    intent: DependencyIntent,
    resolver: Resolver,
    target: Target,
    current: EnvironmentState | None,
    context: OperationContext,
    backend: BackendIdentity | None = None,
) -> Resolution:
    if intent.has_errors:
        raise ResolutionFailed(
            backend=resolver.name,
            operation="resolve",
            message="Manifest contains unsupported or invalid entries",
            diagnostics=intent.diagnostics,
        )
    resolution = resolver.resolve(intent, target, current, context)
    if backend is not None and resolution.backend is None:
        resolution = replace(resolution, backend=backend)
    return resolution


def resolution_to_dict(resolution: Resolution) -> dict[str, object]:
    return {
        "schema": "depviz.resolution",
        "schema_version": RESOLUTION_SCHEMA_VERSION,
        "status": resolution.status.value,
        "target": target_to_dict(resolution.target),
        "backend": backend_to_dict(resolution.backend),
        "requested": [requirement_to_dict(item) for item in resolution.requested],
        "packages": [package_to_dict(item) for item in resolution.packages],
        "diagnostics": [diagnostic_to_dict(item) for item in resolution.diagnostics],
        "native_payload": payload_to_dict(resolution.native_payload),
    }


def resolution_from_dict(value: Mapping[str, object]) -> Resolution:
    _require_exact_keys(
        value,
        required={
            "schema",
            "schema_version",
            "status",
            "target",
            "backend",
            "requested",
            "packages",
            "diagnostics",
            "native_payload",
        },
        label="resolution document",
    )
    if value.get("schema") != "depviz.resolution":
        raise ValueError("Not a depviz resolution document")
    if value.get("schema_version") != RESOLUTION_SCHEMA_VERSION:
        raise ValueError(f"Unsupported resolution schema version: {value.get('schema_version')!r}")
    return Resolution(
        requested=tuple(
            requirement_from_dict(item)
            for item in _object_list(value.get("requested"), "requested")
        ),
        packages=tuple(
            package_from_dict(item) for item in _object_list(value.get("packages"), "packages")
        ),
        target=target_from_dict(_object(value.get("target"), "target")),
        status=ResolutionStatus(_string(value.get("status"), "status")),
        diagnostics=tuple(
            diagnostic_from_dict(item)
            for item in _object_list(value.get("diagnostics", []), "diagnostics")
        ),
        native_payload=payload_from_value(value.get("native_payload")),
        backend=backend_from_value(value.get("backend")),
    )


def resolution_to_json(resolution: Resolution, *, indent: int = 2) -> str:
    return (
        json.dumps(
            resolution_to_dict(resolution),
            indent=indent,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    )


def write_resolution_json(path: Path, resolution: Resolution) -> None:
    write_bytes_atomic(path, resolution_to_json(resolution).encode("utf-8"))


def read_resolution_json(path: Path) -> Resolution:
    try:
        value = json.loads(read_text_limited(path, label="resolution"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read resolution {path}: {error}") from error
    return resolution_from_dict(_object(value, "resolution root"))


def environment_state_to_dict(state: EnvironmentState) -> dict[str, object]:
    return {
        "target": target_to_dict(state.target),
        "complete": state.complete,
        "backend": backend_to_dict(state.backend),
        "environment": environment_target_to_dict(state.environment),
        "packages": [package_to_dict(item) for item in state.packages],
        "diagnostics": [diagnostic_to_dict(item) for item in state.diagnostics],
        "native_payload": payload_to_dict(state.native_payload),
    }


def environment_state_from_dict(value: Mapping[str, object]) -> EnvironmentState:
    complete = value.get("complete")
    if not isinstance(complete, bool):
        raise ValueError("Environment state complete must be boolean")
    return EnvironmentState(
        packages=tuple(
            package_from_dict(item) for item in _object_list(value.get("packages"), "packages")
        ),
        target=target_from_dict(_object(value.get("target"), "target")),
        complete=complete,
        diagnostics=tuple(
            diagnostic_from_dict(item)
            for item in _object_list(value.get("diagnostics", []), "diagnostics")
        ),
        native_payload=payload_from_value(value.get("native_payload")),
        backend=backend_from_value(value.get("backend")),
        environment=environment_target_from_value(value.get("environment")),
    )


def package_set_digest(
    *,
    target: Target,
    packages: tuple[ResolvedPackage, ...],
) -> str:
    ordered = sorted(
        packages,
        key=lambda package: (
            package.ecosystem,
            package.name,
            package.version,
            package.build or "",
        ),
    )
    return digest_json(
        {
            "target": target_to_dict(target),
            "packages": [package_to_dict(package) for package in ordered],
        }
    )


def package_to_dict(package: ResolvedPackage) -> dict[str, object]:
    return {
        "ecosystem": package.ecosystem,
        "name": package.name,
        "version": package.version,
        "build": package.build,
        "platform": package.platform,
        "source": package.source,
        "artifact": package.artifact,
        "checksum": package.checksum,
        "dependencies": [
            {
                "ecosystem": dependency.ecosystem,
                "name": dependency.name,
                "specifier": dependency.specifier,
                "marker": dependency.marker,
            }
            for dependency in package.dependencies
        ],
    }


def package_from_dict(value: Mapping[str, object]) -> ResolvedPackage:
    dependencies = tuple(
        PackageReference(
            ecosystem=_string(item.get("ecosystem"), "dependency ecosystem"),
            name=_string(item.get("name"), "dependency name"),
            specifier=_optional_string(item.get("specifier"), "dependency specifier"),
            marker=_optional_string(item.get("marker"), "dependency marker"),
        )
        for item in _object_list(value.get("dependencies", []), "dependencies")
    )
    return ResolvedPackage(
        ecosystem=_string(value.get("ecosystem"), "package ecosystem"),
        name=_string(value.get("name"), "package name"),
        version=_string(value.get("version"), "package version"),
        build=_optional_string(value.get("build"), "package build"),
        platform=_optional_string(value.get("platform"), "package platform"),
        source=_optional_string(value.get("source"), "package source"),
        artifact=_optional_string(value.get("artifact"), "package artifact"),
        checksum=_optional_string(value.get("checksum"), "package checksum"),
        dependencies=dependencies,
    )


def requirement_to_dict(requirement: Requirement) -> dict[str, object]:
    return {
        "ecosystem": requirement.ecosystem,
        "name": requirement.name,
        "specifier": requirement.specifier,
        "source": requirement.source,
        "marker": requirement.marker,
        "extras": list(requirement.extras),
        "direct": requirement.direct,
        "origin": source_location_to_dict(requirement.origin),
    }


def requirement_from_dict(value: Mapping[str, object]) -> Requirement:
    extras_value = value.get("extras", [])
    if not isinstance(extras_value, list) or not all(
        isinstance(item, str) for item in extras_value
    ):
        raise ValueError("Requirement extras must be a list of strings")
    direct = value.get("direct", True)
    if not isinstance(direct, bool):
        raise ValueError("Requirement direct must be boolean")
    return Requirement(
        ecosystem=_string(value.get("ecosystem"), "requirement ecosystem"),
        name=_string(value.get("name"), "requirement name"),
        specifier=_optional_string(value.get("specifier"), "requirement specifier"),
        source=_optional_string(value.get("source"), "requirement source"),
        marker=_optional_string(value.get("marker"), "requirement marker"),
        extras=tuple(extras_value),
        direct=direct,
        origin=source_location_from_value(value.get("origin")),
    )


def target_to_dict(target: Target) -> dict[str, object]:
    return {
        "platform": target.platform,
        "python_version": target.python_version,
        "implementation": target.implementation,
    }


def target_from_dict(value: Mapping[str, object]) -> Target:
    return Target(
        platform=_string(value.get("platform"), "target platform"),
        python_version=_optional_string(value.get("python_version"), "target python_version"),
        implementation=_optional_string(value.get("implementation"), "target implementation"),
    )


def backend_to_dict(backend: BackendIdentity | None) -> dict[str, object] | None:
    if backend is None:
        return None
    return {
        "component": backend.component,
        "plugin": backend.plugin,
        "plugin_version": backend.plugin_version,
        "tool": backend.tool,
        "tool_version": backend.tool_version,
    }


def backend_from_value(value: object) -> BackendIdentity | None:
    if value is None:
        return None
    item = _object(value, "backend")
    return BackendIdentity(
        component=_string(item.get("component"), "backend component"),
        plugin=_string(item.get("plugin"), "backend plugin"),
        plugin_version=_string(item.get("plugin_version"), "backend plugin_version"),
        tool=_optional_string(item.get("tool"), "backend tool"),
        tool_version=_optional_string(item.get("tool_version"), "backend tool_version"),
    )


def environment_target_to_dict(target: EnvironmentTarget | None) -> dict[str, object] | None:
    if target is None:
        return None
    return {"path": str(target.path), "kind": target.kind}


def environment_target_from_value(value: object) -> EnvironmentTarget | None:
    if value is None:
        return None
    item = _object(value, "environment")
    return EnvironmentTarget(
        path=Path(_string(item.get("path"), "environment path")),
        kind=_string(item.get("kind"), "environment kind"),
    )


def diagnostic_to_dict(diagnostic: Diagnostic) -> dict[str, object]:
    return {
        "code": diagnostic.code,
        "message": diagnostic.message,
        "severity": diagnostic.severity.value,
        "source": source_location_to_dict(diagnostic.source),
        "hint": diagnostic.hint,
    }


def diagnostic_from_dict(value: Mapping[str, object]) -> Diagnostic:
    return Diagnostic(
        code=_string(value.get("code"), "diagnostic code"),
        message=_string(value.get("message"), "diagnostic message"),
        severity=Severity(_string(value.get("severity"), "diagnostic severity")),
        source=source_location_from_value(value.get("source")),
        hint=_optional_string(value.get("hint"), "diagnostic hint"),
    )


def payload_to_dict(payload: BackendPayload | None) -> dict[str, object] | None:
    if payload is None:
        return None
    return {"schema": payload.schema, "data": json_value(payload.data)}


def payload_from_value(value: object) -> BackendPayload | None:
    if value is None:
        return None
    item = _object(value, "native_payload")
    return BackendPayload(
        schema=_string(item.get("schema"), "native payload schema"),
        data=_object(item.get("data"), "native payload data"),
    )


def source_location_to_dict(source: SourceLocation | None) -> dict[str, object] | None:
    if source is None:
        return None
    return {"path": str(source.path), "line": source.line, "column": source.column}


def source_location_from_value(value: object) -> SourceLocation | None:
    if value is None:
        return None
    item = _object(value, "source location")
    line = item.get("line")
    column = item.get("column")
    if line is not None and not isinstance(line, int):
        raise ValueError("Source location line must be integer or null")
    if column is not None and not isinstance(column, int):
        raise ValueError("Source location column must be integer or null")
    return SourceLocation(
        path=Path(_string(item.get("path"), "source path")),
        line=line,
        column=column,
    )


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def digest_json(value: object) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"


def json_value(value: object) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_value(item) for item in value]
    raise TypeError(f"Value is not JSON serializable: {type(value).__name__}")


def _require_exact_keys(
    value: Mapping[str, object],
    *,
    required: set[str],
    label: str,
) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required)
    if missing:
        raise ValueError(f"{label} is missing required fields: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"{label} contains unknown fields: {', '.join(unknown)}")


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _object_list(value: object, label: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return [_object(item, f"{label} item") for item in value]


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _optional_string(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string or null")
    return value
