from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from depviz.api import (
    LockArtifact,
    LockedResolution,
    OperationContext,
    Resolution,
    ResolvedPackage,
)
from depviz.api.errors import LockFailed
from depviz.core.resolution import (
    digest_json,
    resolution_from_dict,
    resolution_to_dict,
)
from depviz.infrastructure.storage import read_bytes_limited

_LOCK_SCHEMA_VERSION = 1
_CHECKSUM = re.compile(r"^(sha256|md5):([0-9a-fA-F]+)$")


@dataclass(frozen=True)
class CondaLockedArtifact:
    name: str
    version: str
    build: str
    platform: str
    source: str
    url: str
    checksum: str

    @property
    def explicit_spec(self) -> str:
        _algorithm, digest = self.checksum.split(":", 1)
        return f"{self.url}#{digest}"


def locked_artifacts(locked: LockedResolution) -> tuple[CondaLockedArtifact, ...]:
    try:
        value = json.loads(locked.artifact.content)
    except json.JSONDecodeError as error:
        raise _invalid(f"Invalid in-memory lock JSON: {error}") from error
    document = _object(value, "lock root")
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list):
        raise _invalid("Lock artifacts must be a list")
    result: list[CondaLockedArtifact] = []
    for index, item in enumerate(artifacts):
        entry = _object(item, f"artifact {index}")
        result.append(
            CondaLockedArtifact(
                name=_string(entry.get("name"), "artifact name"),
                version=_string(entry.get("version"), "artifact version"),
                build=_string(entry.get("build"), "artifact build"),
                platform=_string(entry.get("platform"), "artifact platform"),
                source=_string(entry.get("source"), "artifact source"),
                url=_string(entry.get("url"), "artifact URL"),
                checksum=_string(entry.get("checksum"), "artifact checksum"),
            )
        )
    return tuple(result)


class CondaLockProvider:
    name = "conda-exact-lock"

    def create_lock(
        self,
        resolution: Resolution,
        context: OperationContext,
    ) -> LockArtifact:
        if not resolution.complete:
            raise LockFailed(
                backend=self.name,
                operation="lock",
                message="Cannot lock an incomplete resolution",
            )
        if not resolution.packages:
            raise LockFailed(
                backend=self.name,
                operation="lock",
                message="Cannot create an empty Conda lock",
            )
        allow_weak = _allow_weak_checksums(context)
        artifacts = [
            _artifact_entry(package, allow_weak_checksums=allow_weak)
            for package in resolution.packages
        ]
        payload: dict[str, object] = {
            "schema": "depviz.conda-lock",
            "schema_version": _LOCK_SCHEMA_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "resolution_digest": digest_json(resolution_to_dict(resolution)),
            "resolution": resolution_to_dict(resolution),
            "artifacts": artifacts,
        }
        lock_id = digest_json(payload)
        document = {**payload, "lock_id": lock_id}
        content = (
            json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
            + b"\n"
        )
        return LockArtifact(
            format="depviz.conda-lock.v1",
            content=content,
            metadata={
                "lock_id": lock_id,
                "resolution_digest": str(payload["resolution_digest"]),
                "platform": resolution.target.platform,
                "environment_kind": "conda-prefix",
                "deployment_kind": "managed-conda-deployment",
            },
        )

    def read_lock(
        self,
        path: Path,
        context: OperationContext,
    ) -> LockedResolution:
        try:
            raw = read_bytes_limited(path, label="Conda lock")
        except ValueError as error:
            raise LockFailed(
                backend=self.name,
                operation="read lock",
                message=str(error),
            ) from error
        return read_conda_lock_bytes(raw, context)


def read_conda_lock_bytes(raw: bytes, context: OperationContext) -> LockedResolution:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise LockFailed(
            backend="conda-exact-lock",
            operation="read lock",
            message=f"Invalid Conda lock JSON: {error}",
        ) from error
    document = _object(value, "lock root")
    _require_exact_keys(
        document,
        required={
            "artifacts",
            "created_at",
            "lock_id",
            "resolution",
            "resolution_digest",
            "schema",
            "schema_version",
        },
        label="lock document",
    )
    if document.get("schema") != "depviz.conda-lock":
        raise _invalid("Not a depviz Conda lock")
    if document.get("schema_version") != _LOCK_SCHEMA_VERSION:
        raise _invalid(f"Unsupported lock schema version: {document.get('schema_version')!r}")
    _timestamp(document.get("created_at"), "created_at")
    lock_id = _string(document.get("lock_id"), "lock_id")
    unsigned = dict(document)
    del unsigned["lock_id"]
    if digest_json(unsigned) != lock_id:
        raise _invalid("Lock content does not match lock_id")
    resolution_mapping = _object(document.get("resolution"), "resolution")
    resolution = resolution_from_dict(resolution_mapping)
    expected_resolution_digest = digest_json(resolution_to_dict(resolution))
    if document.get("resolution_digest") != expected_resolution_digest:
        raise _invalid("Lock resolution does not match resolution_digest")
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(resolution.packages):
        raise _invalid("Lock artifact list does not match the resolved package set")
    allow_weak = _allow_weak_checksums(context)
    expected_entries = [
        _artifact_entry(package, allow_weak_checksums=allow_weak) for package in resolution.packages
    ]
    if artifacts != expected_entries:
        raise _invalid("Lock artifact entries do not match the normalized resolution")
    return LockedResolution(
        resolution=resolution,
        artifact=LockArtifact(
            format="depviz.conda-lock.v1",
            content=raw,
            metadata={
                "lock_id": lock_id,
                "resolution_digest": expected_resolution_digest,
                "platform": resolution.target.platform,
                "environment_kind": "conda-prefix",
                "deployment_kind": "managed-conda-deployment",
            },
        ),
    )


def _artifact_entry(
    package: ResolvedPackage, *, allow_weak_checksums: bool = False
) -> dict[str, str]:
    if package.ecosystem != "conda":
        raise _invalid(f"Conda lock cannot contain {package.ecosystem} package {package.name}")
    if package.build is None or package.platform is None or package.source is None:
        raise _invalid(f"Package {package.name} lacks exact Conda identity")
    if package.artifact is None:
        raise _invalid(f"Package {package.name} lacks artifact identity")
    url = _artifact_url(package.source, package.artifact)
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https", "file"}:
        raise _invalid(f"Package {package.name} artifact is not an absolute supported URL: {url}")
    if parsed.username is not None or parsed.password is not None:
        raise _invalid(f"Package {package.name} artifact URL contains embedded credentials")
    if parsed.query:
        raise _invalid(
            f"Package {package.name} artifact URL contains query parameters; "
            "expiring or secret-bearing URLs are not portable locks"
        )
    if parsed.fragment:
        raise _invalid(f"Package {package.name} artifact URL already contains a fragment")
    if package.checksum is None:
        raise _invalid(f"Package {package.name} has no artifact checksum")
    match = _CHECKSUM.fullmatch(package.checksum)
    if match is None:
        raise _invalid(f"Package {package.name} has invalid checksum {package.checksum!r}")
    algorithm, digest = match.groups()
    if algorithm == "md5" and not allow_weak_checksums:
        raise _invalid(
            f"Package {package.name} uses an MD5-only artifact checksum; "
            "SHA-256 is required unless weak checksums are explicitly allowed"
        )
    expected_length = 64 if algorithm == "sha256" else 32
    if len(digest) != expected_length:
        raise _invalid(f"Package {package.name} has invalid {algorithm} digest length")
    return {
        "name": package.name,
        "version": package.version,
        "build": package.build,
        "platform": package.platform,
        "source": package.source,
        "url": url,
        "checksum": f"{algorithm}:{digest.lower()}",
    }


def _artifact_url(source: str, artifact: str) -> str:
    parsed_artifact = urlsplit(artifact)
    if parsed_artifact.scheme:
        return artifact
    parsed_source = urlsplit(source)
    if parsed_source.scheme:
        base = source if source.endswith("/") else f"{source}/"
        return urljoin(base, artifact)
    return artifact


def _allow_weak_checksums(context: OperationContext) -> bool:
    raw = context.configuration.get("security.allow_weak_checksums", "false").strip().lower()
    if raw in {"true", "1", "yes"}:
        return True
    if raw in {"false", "0", "no", ""}:
        return False
    raise _invalid("security.allow_weak_checksums must be true or false")


def _invalid(message: str) -> LockFailed:
    return LockFailed(backend="conda-exact-lock", operation="lock", message=message)


def _timestamp(value: object, label: str) -> str:
    text = _string(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise _invalid(f"{label} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise _invalid(f"{label} must include a timezone")
    return text


def _require_exact_keys(
    value: dict[str, object],
    *,
    required: set[str],
    label: str,
) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required)
    if missing:
        raise _invalid(f"{label} is missing required fields: {', '.join(missing)}")
    if unknown:
        raise _invalid(f"{label} contains unknown fields: {', '.join(unknown)}")


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _invalid(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"{label} must be a non-empty string")
    return value
