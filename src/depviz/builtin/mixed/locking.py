from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from depviz.api import LockArtifact, LockedResolution, OperationContext, Resolution
from depviz.api.errors import LockFailed
from depviz.builtin.conda.locking import CondaLockProvider, read_conda_lock_bytes
from depviz.builtin.python.locking import PythonLockProvider, read_python_lock_bytes
from depviz.core.resolution import (
    digest_json,
    resolution_from_dict,
    resolution_to_dict,
)
from depviz.infrastructure.storage import read_bytes_limited

_LOCK_SCHEMA_VERSION = 1


class CondaPipLockProvider:
    name = "conda-pip-exact-lock"

    def create_lock(self, resolution: Resolution, context: OperationContext) -> LockArtifact:
        conda_resolution, python_resolution = mixed_resolution_layers(resolution)
        conda_lock = CondaLockProvider().create_lock(conda_resolution, context)
        python_lock = PythonLockProvider().create_lock(python_resolution, context)
        ownership = _ownership(resolution)
        payload: dict[str, object] = {
            "schema": "depviz.conda-pip-lock",
            "schema_version": _LOCK_SCHEMA_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "resolution_digest": digest_json(resolution_to_dict(resolution)),
            "resolution": resolution_to_dict(resolution),
            "conda_lock": _json_object(conda_lock.content, "Conda child lock"),
            "python_lock": _json_object(python_lock.content, "Python child lock"),
            "ownership": ownership,
        }
        lock_id = digest_json(payload)
        document = {**payload, "lock_id": lock_id}
        content = json.dumps(document, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        return LockArtifact(
            format="depviz.conda-pip-lock.v1",
            content=content,
            metadata={
                "lock_id": lock_id,
                "resolution_digest": str(payload["resolution_digest"]),
                "platform": resolution.target.platform,
                "environment_kind": "conda-pip-prefix",
                "deployment_kind": "managed-conda-pip-deployment",
            },
        )

    def read_lock(self, path: Path, context: OperationContext) -> LockedResolution:
        try:
            raw = read_bytes_limited(path, label="Conda+pip lock")
        except ValueError as exc:
            raise _invalid(str(exc)) from exc
        return read_conda_pip_lock_bytes(raw, context)


def read_conda_pip_lock_bytes(raw: bytes, context: OperationContext) -> LockedResolution:
    document = _json_object(raw, "Conda+pip lock")
    _require_exact_keys(
        document,
        required={
            "conda_lock",
            "created_at",
            "lock_id",
            "ownership",
            "python_lock",
            "resolution",
            "resolution_digest",
            "schema",
            "schema_version",
        },
        label="lock document",
    )
    if document.get("schema") != "depviz.conda-pip-lock":
        raise _invalid("Not a depviz Conda+pip lock")
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
    expected_conda, expected_python = mixed_resolution_layers(resolution)

    conda_raw = _json_bytes(_object(document.get("conda_lock"), "conda_lock"))
    python_raw = _json_bytes(_object(document.get("python_lock"), "python_lock"))
    conda_locked = read_conda_lock_bytes(conda_raw, context)
    python_locked = read_python_lock_bytes(python_raw)
    if resolution_to_dict(conda_locked.resolution) != resolution_to_dict(expected_conda):
        raise _invalid("Embedded Conda lock does not match the mixed resolution")
    if resolution_to_dict(python_locked.resolution) != resolution_to_dict(expected_python):
        raise _invalid("Embedded Python lock does not match the mixed resolution")
    if document.get("ownership") != _ownership(resolution):
        raise _invalid("Lock ownership map does not match the mixed resolution")

    return LockedResolution(
        resolution=resolution,
        artifact=LockArtifact(
            format="depviz.conda-pip-lock.v1",
            content=raw,
            metadata={
                "lock_id": lock_id,
                "resolution_digest": expected_digest,
                "platform": resolution.target.platform,
                "environment_kind": "conda-pip-prefix",
                "deployment_kind": "managed-conda-pip-deployment",
            },
        ),
    )


def mixed_lock_layers(
    locked: LockedResolution,
    context: OperationContext,
) -> tuple[LockedResolution, LockedResolution]:
    document = _json_object(locked.artifact.content, "Conda+pip lock")
    conda_raw = _json_bytes(_object(document.get("conda_lock"), "conda_lock"))
    python_raw = _json_bytes(_object(document.get("python_lock"), "python_lock"))
    return read_conda_lock_bytes(conda_raw, context), read_python_lock_bytes(python_raw)


def mixed_resolution_layers(resolution: Resolution) -> tuple[Resolution, Resolution]:
    if not resolution.complete:
        raise _invalid("Cannot lock an incomplete mixed resolution")
    payload = resolution.native_payload
    if payload is None or payload.schema != "depviz.conda-pip.resolution.v1":
        raise _invalid("Resolution is not a Conda+pip mixed resolution")
    conda_value = payload.data.get("conda_resolution")
    python_value = payload.data.get("python_resolution")
    if not isinstance(conda_value, dict) or not isinstance(python_value, dict):
        raise _invalid("Mixed resolution lacks child resolution payloads")
    conda = resolution_from_dict({str(key): value for key, value in conda_value.items()})
    python = resolution_from_dict({str(key): value for key, value in python_value.items()})
    combined = tuple(
        sorted(
            (*conda.packages, *python.packages),
            key=lambda package: (package.ecosystem, package.name),
        )
    )
    if combined != tuple(
        sorted(resolution.packages, key=lambda package: (package.ecosystem, package.name))
    ):
        raise _invalid("Mixed child resolutions do not match the normalized package set")
    return conda, python


def _ownership(resolution: Resolution) -> dict[str, object]:
    payload = resolution.native_payload
    if payload is None:
        raise _invalid("Mixed resolution has no native payload")
    raw = payload.data.get("ownership")
    if not isinstance(raw, dict):
        raise _invalid("Mixed resolution has no ownership map")
    policy = raw.get("policy")
    collisions = raw.get("pip_overrides")
    if policy != "pip-last":
        raise _invalid("Mixed resolution ownership policy must be 'pip-last'")
    if not isinstance(collisions, list) or not all(isinstance(item, str) for item in collisions):
        raise _invalid("Mixed resolution pip_overrides must be a list of package names")
    return {"policy": policy, "pip_overrides": sorted(collisions)}


def _json_object(raw: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _invalid(f"Invalid {label} JSON: {exc}") from exc
    return _object(value, label)


def _json_bytes(value: dict[str, object]) -> bytes:
    return json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n"


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


def _timestamp(value: object, label: str) -> str:
    text = _string(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _invalid(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise _invalid(f"{label} must include a timezone")
    return text


def _invalid(message: str) -> LockFailed:
    return LockFailed(backend="conda-pip-exact-lock", operation="lock", message=message)
