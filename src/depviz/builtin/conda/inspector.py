from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from depviz.api import (
    BackendPayload,
    Diagnostic,
    EnvironmentState,
    EnvironmentTarget,
    OperationContext,
    PackageReference,
    ResolvedPackage,
    Severity,
    Target,
)
from depviz.api.errors import InspectionFailed
from depviz.infrastructure.storage import read_text_limited


class CondaPrefixInspector:
    """Inspect exact installed package records from a Conda prefix."""

    name = "conda-prefix"

    def inspect(
        self,
        target: EnvironmentTarget,
        context: OperationContext,
    ) -> EnvironmentState:
        if target.kind != "conda-prefix":
            raise InspectionFailed(
                backend=self.name,
                operation="inspect",
                message=f"Unsupported environment target kind {target.kind!r}",
            )
        prefix = target.path.expanduser().resolve()
        metadata_directory = prefix / "conda-meta"
        if not prefix.exists():
            raise InspectionFailed(
                backend=self.name,
                operation="inspect",
                message=f"Conda prefix does not exist: {prefix}",
            )
        if not metadata_directory.is_dir():
            raise InspectionFailed(
                backend=self.name,
                operation="inspect",
                message=f"Not a Conda prefix: missing {metadata_directory}",
            )

        record_paths = sorted(metadata_directory.glob("*.json"))
        if not record_paths:
            raise InspectionFailed(
                backend=self.name,
                operation="inspect",
                message=f"Conda prefix contains no package records: {prefix}",
            )

        packages_by_identity: dict[tuple[str, str], ResolvedPackage] = {}
        diagnostics: list[Diagnostic] = []
        non_noarch_platforms: set[str] = set()
        record_files: list[str] = []

        for record_path in record_paths:
            record = _read_record(record_path)
            record_files.append(record_path.name)
            package = _parse_record(record, record_path)
            if package.platform and package.platform != "noarch":
                non_noarch_platforms.add(package.platform)
            previous = packages_by_identity.get(package.identity)
            if previous is not None and previous != package:
                raise InspectionFailed(
                    backend=self.name,
                    operation="inspect",
                    message=(
                        f"Conda prefix has conflicting records for "
                        f"{package.ecosystem}:{package.name}"
                    ),
                )
            packages_by_identity[package.identity] = package
            if package.checksum is None:
                diagnostics.append(
                    Diagnostic(
                        code="inspector.conda.unhashed-installed-record",
                        message=(
                            f"Installed package {package.name}={package.version}={package.build} "
                            "has no recorded SHA-256 or MD5 checksum"
                        ),
                        severity=Severity.WARNING,
                    )
                )

        configured_platform = context.configuration.get("conda.platform")
        platform = _resolve_platform(configured_platform, non_noarch_platforms)
        packages = tuple(
            sorted(
                packages_by_identity.values(),
                key=lambda package: (
                    package.ecosystem,
                    package.name,
                    package.version,
                    package.build or "",
                ),
            )
        )
        return EnvironmentState(
            packages=packages,
            target=Target(platform=platform),
            complete=True,
            diagnostics=tuple(diagnostics),
            native_payload=BackendPayload(
                schema="depviz.conda.prefix.v1",
                data={
                    "prefix": str(prefix),
                    "record_count": len(record_files),
                    "record_files": record_files,
                },
            ),
            environment=EnvironmentTarget(path=prefix, kind="conda-prefix"),
        )


def _read_record(path: Path) -> dict[str, object]:
    try:
        text = read_text_limited(
            path,
            max_bytes=4 * 1024 * 1024,
            label="Conda package record",
        )
    except ValueError as error:
        raise InspectionFailed(
            backend="conda-prefix",
            operation="inspect",
            message=str(error),
        ) from error
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise InspectionFailed(
            backend="conda-prefix",
            operation="inspect",
            message=f"Invalid package record {path}: {error}",
        ) from error
    if not isinstance(value, dict):
        raise InspectionFailed(
            backend="conda-prefix",
            operation="inspect",
            message=f"Package record root must be an object: {path}",
        )
    return {str(key): item for key, item in value.items()}


def _parse_record(record: Mapping[str, object], path: Path) -> ResolvedPackage:
    name = _required_string(record, "name", path)
    version = _required_string(record, "version", path)
    build = _optional_string(record.get("build")) or _optional_string(record.get("build_string"))
    if build is None:
        raise _missing(path, "build")
    platform = _record_platform(record, path)
    source = (
        _optional_string(record.get("channel"))
        or _optional_string(record.get("schannel"))
        or _optional_string(record.get("base_url"))
        or _optional_string(record.get("url"))
    )
    if source is None:
        raise _missing(path, "channel/source")
    artifact = (
        _optional_string(record.get("url"))
        or _optional_string(record.get("fn"))
        or _optional_string(record.get("dist_name"))
    )
    if artifact is None:
        raise _missing(path, "url/fn")
    source = _strip_url_credentials(source)
    artifact = _strip_url_credentials(artifact)

    sha256 = _optional_string(record.get("sha256"))
    md5 = _optional_string(record.get("md5"))
    checksum = f"sha256:{sha256}" if sha256 else (f"md5:{md5}" if md5 else None)

    raw_dependencies = record.get("depends", [])
    if raw_dependencies is None:
        raw_dependencies = []
    if not isinstance(raw_dependencies, list) or not all(
        isinstance(item, str) for item in raw_dependencies
    ):
        raise InspectionFailed(
            backend="conda-prefix",
            operation="inspect",
            message=f"Package record has invalid depends field: {path}",
        )
    dependencies = tuple(_parse_dependency(item, path) for item in raw_dependencies)
    return ResolvedPackage(
        ecosystem="conda",
        name=name,
        version=version,
        source=source,
        artifact=artifact,
        checksum=checksum,
        build=build,
        platform=platform,
        dependencies=dependencies,
    )


def _record_platform(record: Mapping[str, object], path: Path) -> str:
    subdir = _optional_string(record.get("subdir"))
    if subdir is not None:
        return subdir
    platform = _optional_string(record.get("platform"))
    if platform is None:
        raise _missing(path, "subdir/platform")
    if platform == "noarch" or "-" in platform:
        return platform
    arch = _optional_string(record.get("arch"))
    if arch is None:
        raise _missing(path, "subdir or platform/arch")
    mappings = {
        ("linux", "x86_64"): "linux-64",
        ("linux", "aarch64"): "linux-aarch64",
        ("linux", "arm64"): "linux-aarch64",
        ("linux", "ppc64le"): "linux-ppc64le",
        ("linux", "s390x"): "linux-s390x",
        ("osx", "x86_64"): "osx-64",
        ("osx", "arm64"): "osx-arm64",
        ("osx", "aarch64"): "osx-arm64",
        ("win", "x86_64"): "win-64",
        ("win", "x86"): "win-32",
        ("win", "arm64"): "win-arm64",
    }
    try:
        return mappings[(platform.lower(), arch.lower())]
    except KeyError as error:
        raise InspectionFailed(
            backend="conda-prefix",
            operation="inspect",
            message=(
                f"Cannot derive Conda subdir from platform={platform!r}, arch={arch!r} in {path}"
            ),
        ) from error


def _parse_dependency(value: str, path: Path) -> PackageReference:
    stripped = value.strip()
    if not stripped:
        raise InspectionFailed(
            backend="conda-prefix",
            operation="inspect",
            message=f"Package record contains an empty dependency: {path}",
        )
    parts = stripped.split(maxsplit=1)
    return PackageReference(
        ecosystem="conda",
        name=parts[0],
        specifier=parts[1] if len(parts) == 2 else None,
    )


def _resolve_platform(configured: str | None, discovered: set[str]) -> str:
    if configured is not None and configured.strip():
        platform = configured.strip()
        incompatible = sorted(item for item in discovered if item != platform)
        if incompatible:
            raise InspectionFailed(
                backend="conda-prefix",
                operation="inspect",
                message=(
                    f"Configured platform {platform!r} conflicts with installed package "
                    f"platforms: {', '.join(incompatible)}"
                ),
            )
        return platform
    if len(discovered) == 1:
        return next(iter(discovered))
    if not discovered:
        raise InspectionFailed(
            backend="conda-prefix",
            operation="inspect",
            message=(
                "Cannot infer target platform because every installed package is noarch; "
                "provide conda.platform"
            ),
        )
    raise InspectionFailed(
        backend="conda-prefix",
        operation="inspect",
        message=f"Conda prefix contains multiple package platforms: {', '.join(sorted(discovered))}",
    )


def _required_string(record: Mapping[str, object], key: str, path: Path) -> str:
    value = _optional_string(record.get(key))
    if value is None:
        raise _missing(path, key)
    return value


def _missing(path: Path, field: str) -> InspectionFailed:
    return InspectionFailed(
        backend="conda-prefix",
        operation="inspect",
        message=f"Package record {path} has no non-empty {field!r} field",
    )


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _strip_url_credentials(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.username is None and parsed.password is None:
        return value
    hostname = parsed.hostname or ""
    if parsed.port is not None:
        hostname = f"{hostname}:{parsed.port}"
    return urlunsplit((parsed.scheme, hostname, parsed.path, parsed.query, parsed.fragment))
