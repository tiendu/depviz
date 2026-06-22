from __future__ import annotations

import json
import re
from collections.abc import Mapping

from depviz.api import Diagnostic, PackageReference, ResolvedPackage, Severity
from depviz.api.errors import ResolutionFailed
from depviz.builtin.conda.security import redact_url

_CONDA_DEPENDENCY_RE = re.compile(r"^([A-Za-z0-9_.-]+)(?:\s+(.*))?$")


def parse_json_payload(stdout: str) -> dict[str, object]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise ResolutionFailed(
            backend="conda-dry-run",
            operation="resolve",
            message=f"Solver did not return valid JSON: {error}",
        ) from error
    if not isinstance(payload, dict):
        raise ResolutionFailed(
            backend="conda-dry-run",
            operation="resolve",
            message="Solver JSON root must be an object",
        )
    return payload


def parse_link_packages(
    payload: Mapping[str, object],
    target_platform: str,
    secrets: tuple[str, ...],
) -> tuple[tuple[ResolvedPackage, ...], tuple[Diagnostic, ...]]:
    actions = payload.get("actions")
    if not isinstance(actions, dict):
        raise ResolutionFailed(
            backend="conda-dry-run",
            operation="resolve",
            message="Successful solver output has no actions object",
        )
    link_records = actions.get("LINK")
    if not isinstance(link_records, list):
        raise ResolutionFailed(
            backend="conda-dry-run",
            operation="resolve",
            message="Successful solver output has no LINK package list",
        )
    if not link_records:
        raise ResolutionFailed(
            backend="conda-dry-run",
            operation="resolve",
            message="Solver returned an empty environment for non-empty requirements",
        )

    fetch_index = _index_fetch_records(actions.get("FETCH"))
    packages_by_name: dict[str, ResolvedPackage] = {}
    missing_dependency_metadata: list[str] = []
    for index, raw_record in enumerate(link_records):
        if not isinstance(raw_record, dict):
            raise ResolutionFailed(
                backend="conda-dry-run",
                operation="resolve",
                message=f"LINK record {index} is not an object",
            )
        record = dict(raw_record)
        fetch_record = fetch_index.get(_record_identity(record))
        if fetch_record is not None:
            record = {**fetch_record, **record}
        if "depends" not in record:
            missing_dependency_metadata.append(str(record.get("name", f"record-{index}")))
        package = _parse_package_record(record, target_platform, secrets, index)
        previous = packages_by_name.get(package.name)
        if previous is not None and previous != package:
            raise ResolutionFailed(
                backend="conda-dry-run",
                operation="resolve",
                message=f"Solver returned conflicting records for package {package.name!r}",
            )
        packages_by_name[package.name] = package

    diagnostics: tuple[Diagnostic, ...] = ()
    if missing_dependency_metadata:
        diagnostics = (
            Diagnostic(
                code="resolver.conda.missing-dependency-metadata",
                message=(
                    "The solver transaction identified exact packages but omitted dependency "
                    f"metadata for {len(missing_dependency_metadata)} package(s)"
                ),
                severity=Severity.WARNING,
                hint="The package set is exact; dependency-edge analysis may be incomplete.",
            ),
        )

    packages = tuple(
        sorted(
            packages_by_name.values(),
            key=lambda package: (
                package.name,
                package.version,
                package.build or "",
                package.source or "",
            ),
        )
    )
    return packages, diagnostics


def _index_fetch_records(value: object) -> dict[tuple[str, str, str], dict[str, object]]:
    if value is None:
        return {}
    if not isinstance(value, list):
        raise ResolutionFailed(
            backend="conda-dry-run",
            operation="resolve",
            message="Solver actions FETCH field is not a list",
        )
    indexed: dict[tuple[str, str, str], dict[str, object]] = {}
    for index, record in enumerate(value):
        if not isinstance(record, dict):
            raise ResolutionFailed(
                backend="conda-dry-run",
                operation="resolve",
                message=f"FETCH record {index} is not an object",
            )
        identity = _record_identity(record)
        if all(identity):
            indexed[identity] = dict(record)
    return indexed


def _record_identity(record: Mapping[str, object]) -> tuple[str, str, str]:
    return (
        _optional_string(record.get("name")) or "",
        _optional_string(record.get("version")) or "",
        _optional_string(record.get("build")) or _optional_string(record.get("build_string")) or "",
    )


def _parse_package_record(
    record: Mapping[str, object],
    target_platform: str,
    secrets: tuple[str, ...],
    index: int,
) -> ResolvedPackage:
    name = _required_string(record, "name", index)
    version = _required_string(record, "version", index)
    build = _optional_string(record.get("build")) or _optional_string(record.get("build_string"))
    if build is None:
        raise ResolutionFailed(
            backend="conda-dry-run",
            operation="resolve",
            message=f"LINK record {index} has no build string",
        )
    platform = (
        _optional_string(record.get("subdir"))
        or _optional_string(record.get("platform"))
        or target_platform
    )
    source = (
        _optional_string(record.get("channel"))
        or _optional_string(record.get("base_url"))
        or _optional_string(record.get("channel_url"))
    )
    artifact = (
        _optional_string(record.get("url"))
        or _optional_string(record.get("fn"))
        or _optional_string(record.get("dist_name"))
    )
    if source is None:
        raise ResolutionFailed(
            backend="conda-dry-run",
            operation="resolve",
            message=f"LINK record {index} has no package source",
        )
    if artifact is None:
        raise ResolutionFailed(
            backend="conda-dry-run",
            operation="resolve",
            message=f"LINK record {index} has no artifact identity",
        )
    source = redact_url(source, secrets)
    artifact = redact_url(artifact, secrets) if artifact else None

    sha256 = _optional_string(record.get("sha256"))
    md5 = _optional_string(record.get("md5"))
    checksum = f"sha256:{sha256}" if sha256 else (f"md5:{md5}" if md5 else None)

    raw_dependencies = record.get("depends", [])
    if raw_dependencies is None:
        raw_dependencies = []
    if not isinstance(raw_dependencies, list) or not all(
        isinstance(item, str) for item in raw_dependencies
    ):
        raise ResolutionFailed(
            backend="conda-dry-run",
            operation="resolve",
            message=f"LINK record {index} has an invalid depends field",
        )
    dependencies = tuple(_parse_dependency_reference(item) for item in raw_dependencies)

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


def _parse_dependency_reference(value: str) -> PackageReference:
    match = _CONDA_DEPENDENCY_RE.match(value.strip())
    if match is None:
        raise ResolutionFailed(
            backend="conda-dry-run",
            operation="resolve",
            message=f"Cannot parse dependency MatchSpec {value!r}",
        )
    return PackageReference(
        ecosystem="conda",
        name=match.group(1),
        specifier=(match.group(2) or "").strip() or None,
    )


def _required_string(record: Mapping[str, object], key: str, index: int) -> str:
    value = _optional_string(record.get(key))
    if value is None:
        raise ResolutionFailed(
            backend="conda-dry-run",
            operation="resolve",
            message=f"LINK record {index} has no non-empty {key!r} field",
        )
    return value


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def solver_failure_message(
    payload: Mapping[str, object],
    stderr: str,
    returncode: int,
) -> str:
    parts: list[str] = []
    for key in ("error", "message", "exception_name", "exception_type"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    problems = payload.get("solver_problems")
    if isinstance(problems, list):
        parts.extend(str(problem).strip() for problem in problems if str(problem).strip())
    if not parts and stderr.strip():
        parts.append(stderr.strip())
    if not parts:
        parts.append(f"solver exited with code {returncode}")
    return "; ".join(dict.fromkeys(parts))
