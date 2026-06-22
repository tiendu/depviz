from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from depviz.api import LockArtifact, LockedResolution, OperationContext, Resolution, ResolvedPackage
from depviz.api.errors import LockFailed
from depviz.core.resolution import digest_json, resolution_from_dict, resolution_to_dict
from depviz.infrastructure.storage import read_bytes_limited

_LOCK_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^sha256:([0-9a-fA-F]{64})$")


@dataclass(frozen=True)
class PythonLockedArtifact:
    name: str
    version: str
    platform: str
    source: str
    url: str
    checksum: str

    @property
    def requirement_line(self) -> str:
        _algorithm, digest = self.checksum.split(":", 1)
        return f"{self.name} @ {self.url} --hash=sha256:{digest}"


def locked_artifacts(locked: LockedResolution) -> tuple[PythonLockedArtifact, ...]:
    try:
        value = json.loads(locked.artifact.content)
    except json.JSONDecodeError as exc:
        raise _invalid(f"Invalid in-memory lock JSON: {exc}") from exc
    document = _object(value, "lock root")
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list):
        raise _invalid("Lock artifacts must be a list")
    result: list[PythonLockedArtifact] = []
    for index, raw in enumerate(artifacts):
        entry = _object(raw, f"artifact {index}")
        result.append(
            PythonLockedArtifact(
                name=_string(entry.get("name"), "artifact name"),
                version=_string(entry.get("version"), "artifact version"),
                platform=_string(entry.get("platform"), "artifact platform"),
                source=_string(entry.get("source"), "artifact source"),
                url=_string(entry.get("url"), "artifact URL"),
                checksum=_string(entry.get("checksum"), "artifact checksum"),
            )
        )
    return tuple(result)


class PythonLockProvider:
    name = "python-exact-lock"

    def create_lock(self, resolution: Resolution, context: OperationContext) -> LockArtifact:
        del context
        if not resolution.complete:
            raise _invalid("Cannot lock an incomplete resolution")
        if not resolution.packages:
            raise _invalid("Cannot create an empty Python lock")
        interpreter = _interpreter_metadata(resolution)
        artifacts = [_artifact_entry(package) for package in resolution.packages]
        payload: dict[str, object] = {
            "schema": "depviz.python-lock",
            "schema_version": _LOCK_SCHEMA_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "resolution_digest": digest_json(resolution_to_dict(resolution)),
            "resolution": resolution_to_dict(resolution),
            "interpreter": interpreter,
            "artifacts": artifacts,
        }
        lock_id = digest_json(payload)
        document = {**payload, "lock_id": lock_id}
        content = json.dumps(document, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        return LockArtifact(
            format="depviz.python-lock.v1",
            content=content,
            metadata={
                "lock_id": lock_id,
                "resolution_digest": str(payload["resolution_digest"]),
                "platform": resolution.target.platform,
                "environment_kind": "python-venv",
                "deployment_kind": "managed-python-deployment",
            },
        )

    def read_lock(self, path: Path, context: OperationContext) -> LockedResolution:
        del context
        try:
            raw = read_bytes_limited(path, label="Python lock")
        except ValueError as exc:
            raise LockFailed(
                backend=self.name,
                operation="read lock",
                message=str(exc),
            ) from exc
        return read_python_lock_bytes(raw)


def read_python_lock_bytes(raw: bytes) -> LockedResolution:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LockFailed(
            backend="python-exact-lock",
            operation="read lock",
            message=f"Invalid Python lock JSON: {exc}",
        ) from exc
    document = _object(value, "lock root")
    _require_exact_keys(
        document,
        required={
            "artifacts",
            "created_at",
            "interpreter",
            "lock_id",
            "resolution",
            "resolution_digest",
            "schema",
            "schema_version",
        },
        label="lock document",
    )
    if document.get("schema") != "depviz.python-lock":
        raise _invalid("Not a depviz Python lock")
    if document.get("schema_version") != _LOCK_SCHEMA_VERSION:
        raise _invalid(f"Unsupported lock schema version: {document.get('schema_version')!r}")
    _timestamp(document.get("created_at"), "created_at")
    lock_id = _string(document.get("lock_id"), "lock_id")
    unsigned = dict(document)
    del unsigned["lock_id"]
    if digest_json(unsigned) != lock_id:
        raise _invalid("Lock content does not match lock_id")
    resolution = resolution_from_dict(_object(document.get("resolution"), "resolution"))
    expected_digest = digest_json(resolution_to_dict(resolution))
    if document.get("resolution_digest") != expected_digest:
        raise _invalid("Lock resolution does not match resolution_digest")
    interpreter = _interpreter_metadata(resolution)
    if document.get("interpreter") != interpreter:
        raise _invalid("Lock interpreter metadata does not match the normalized resolution")
    artifacts = document.get("artifacts")
    expected_artifacts = [_artifact_entry(package) for package in resolution.packages]
    if artifacts != expected_artifacts:
        raise _invalid("Lock artifacts do not match the normalized resolution")
    return LockedResolution(
        resolution=resolution,
        artifact=LockArtifact(
            format="depviz.python-lock.v1",
            content=raw,
            metadata={
                "lock_id": lock_id,
                "resolution_digest": expected_digest,
                "platform": resolution.target.platform,
                "environment_kind": "python-venv",
                "deployment_kind": "managed-python-deployment",
            },
        ),
    )


def interpreter_metadata(locked: LockedResolution) -> dict[str, object]:
    try:
        value = json.loads(locked.artifact.content)
    except json.JSONDecodeError as exc:
        raise _invalid(f"Invalid in-memory lock JSON: {exc}") from exc
    document = _object(value, "lock root")
    return _object(document.get("interpreter"), "interpreter")


def _interpreter_metadata(resolution: Resolution) -> dict[str, object]:
    payload = resolution.native_payload
    if payload is None or payload.schema != "depviz.uv-lock.native.v1":
        raise _invalid("Python resolution lacks uv interpreter metadata")
    interpreter = payload.data.get("interpreter")
    if not isinstance(interpreter, dict):
        raise _invalid("Python resolution interpreter metadata must be an object")
    required = ("implementation", "version", "major", "minor", "platform", "soabi")
    for key in required:
        value = interpreter.get(key)
        if key in {"major", "minor"}:
            if not isinstance(value, int):
                raise _invalid(f"Python interpreter field {key!r} must be an integer")
        elif not isinstance(value, str):
            raise _invalid(f"Python interpreter field {key!r} must be a string")
    return {str(key): value for key, value in interpreter.items() if key != "executable"}


def _artifact_entry(package: ResolvedPackage) -> dict[str, str]:
    if package.ecosystem != "pypi":
        raise _invalid(f"Python lock cannot contain {package.ecosystem} package {package.name}")
    if package.build is not None:
        raise _invalid(f"Python package {package.name} unexpectedly has a Conda-style build")
    if package.platform is None or package.source is None or package.artifact is None:
        raise _invalid(f"Package {package.name} lacks exact Python artifact identity")
    parsed = urlsplit(package.artifact)
    if parsed.scheme not in {"https", "file"}:
        raise _invalid(f"Package {package.name} wheel is not an absolute HTTPS or file URL")
    if parsed.username is not None or parsed.password is not None:
        raise _invalid(f"Package {package.name} wheel URL contains embedded credentials")
    if parsed.query or parsed.fragment:
        raise _invalid(f"Package {package.name} wheel URL contains query or fragment data")
    if not parsed.path.lower().endswith(".whl"):
        raise _invalid(f"Package {package.name} artifact is not a wheel")
    if package.checksum is None:
        raise _invalid(f"Package {package.name} wheel has no checksum")
    match = _SHA256.fullmatch(package.checksum)
    if match is None:
        raise _invalid(f"Package {package.name} wheel has an invalid SHA-256 checksum")
    return {
        "name": package.name,
        "version": package.version,
        "platform": package.platform,
        "source": package.source,
        "url": package.artifact,
        "checksum": f"sha256:{match.group(1).lower()}",
    }


def _invalid(message: str) -> LockFailed:
    return LockFailed(backend="python-exact-lock", operation="lock", message=message)


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
