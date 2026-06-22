from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Mapping

from depviz.api import CandidateEnvironment, EnvironmentTarget
from depviz.infrastructure.storage import (
    DEFAULT_MAX_DOCUMENT_BYTES,
    ensure_private_directory,
    fsync_directory,
    read_bytes_limited,
    read_text_limited,
    reject_unsafe_writable_directory,
    write_bytes_atomic,
)

_CANDIDATE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CANDIDATE_SCHEMA_VERSION = 1
_DEPLOYMENT_SCHEMA_VERSION = 1
_PENDING_SCHEMA_VERSION = 1


class CandidateStatus(StrEnum):
    CREATED = "created"
    APPLIED = "applied"
    VERIFIED = "verified"
    VERIFICATION_FAILED = "verification-failed"
    FAILED = "failed"
    REMOVED = "removed"


@dataclass(frozen=True)
class CandidateRecord:
    candidate_id: str
    relative_path: str
    environment_kind: str
    lock_id: str
    lock_format: str
    resolution_digest: str
    status: CandidateStatus
    created_at: str
    updated_at: str
    verification_expected_digest: str | None = None
    verification_observed_digest: str | None = None


@dataclass(frozen=True)
class DeploymentState:
    current_candidate_id: str | None = None
    history: tuple[str, ...] = ()
    updated_at: str | None = None


@dataclass(frozen=True)
class PendingSwitch:
    operation: str
    from_candidate_id: str | None
    to_candidate_id: str
    next_state: DeploymentState


class ManagedDeploymentStore:
    """Durable metadata and pointer storage for immutable candidate environments."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().absolute()
        self.environments_dir = self.root / "environments"
        self.metadata_dir = self.root / ".depviz"
        self.records_dir = self.metadata_dir / "candidates"
        self.locks_dir = self.metadata_dir / "locks"
        self.state_path = self.metadata_dir / "deployment.json"
        self.pending_path = self.metadata_dir / "pending-switch.json"
        self.lock_path = self.metadata_dir / "operation.lock"
        self.current_path = self.root / "current"

    def initialize(self) -> None:
        if self.root.is_symlink():
            raise ValueError(f"Deployment root cannot be a symlink: {self.root}")
        if self.root.exists():
            reject_unsafe_writable_directory(self.root, label="deployment root")
        else:
            self.root.mkdir(parents=True, mode=0o700)
        ensure_private_directory(self.environments_dir)
        ensure_private_directory(self.metadata_dir)
        ensure_private_directory(self.records_dir)
        ensure_private_directory(self.locks_dir)

    def validate_security(self) -> None:
        """Validate deployment ownership boundaries without modifying the filesystem."""

        if self.root.is_symlink():
            raise ValueError(f"Deployment root cannot be a symlink: {self.root}")
        reject_unsafe_writable_directory(self.root, label="deployment root")
        for path, label in (
            (self.environments_dir, "environment directory"),
            (self.metadata_dir, "metadata directory"),
            (self.records_dir, "candidate-record directory"),
            (self.locks_dir, "archived-lock directory"),
        ):
            if path.exists() or path.is_symlink():
                reject_unsafe_writable_directory(path, label=label)

    def candidate(
        self,
        candidate_id: str,
        *,
        kind: str = "conda-prefix",
        deployment_kind: str = "managed-conda-deployment",
    ) -> CandidateEnvironment:
        _validate_candidate_id(candidate_id)
        path = self.environments_dir / candidate_id
        return CandidateEnvironment(
            target=EnvironmentTarget(path=self.root, kind=deployment_kind),
            candidate_id=candidate_id,
            path=path,
            kind=kind,
        )

    def reserve_candidate(
        self,
        *,
        kind: str = "conda-prefix",
        deployment_kind: str = "managed-conda-deployment",
    ) -> CandidateEnvironment:
        self.initialize()
        for _attempt in range(20):
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            candidate_id = f"c-{timestamp}-{uuid.uuid4().hex[:10]}"
            candidate = self.candidate(candidate_id, kind=kind, deployment_kind=deployment_kind)
            try:
                candidate.path.mkdir(mode=0o700)
            except FileExistsError:
                continue
            fsync_directory(self.environments_dir)
            return candidate
        raise RuntimeError("Could not allocate a unique candidate environment")

    def write_candidate(self, record: CandidateRecord) -> None:
        _validate_candidate_id(record.candidate_id)
        expected = f"environments/{record.candidate_id}"
        if record.relative_path != expected:
            raise ValueError("Candidate record path does not match candidate ID")
        payload: dict[str, object] = {
            "schema": "depviz.candidate",
            "schema_version": _CANDIDATE_SCHEMA_VERSION,
            "candidate_id": record.candidate_id,
            "relative_path": record.relative_path,
            "environment_kind": record.environment_kind,
            "lock_id": record.lock_id,
            "lock_format": record.lock_format,
            "resolution_digest": record.resolution_digest,
            "status": record.status.value,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "verification_expected_digest": record.verification_expected_digest,
            "verification_observed_digest": record.verification_observed_digest,
        }
        write_bytes_atomic(
            self.records_dir / f"{record.candidate_id}.json",
            _json_bytes(payload),
        )

    def read_candidate(self, candidate_id: str) -> CandidateRecord:
        _validate_candidate_id(candidate_id)
        value = _read_object(self.records_dir / f"{candidate_id}.json", "candidate record")
        _require_exact_keys(
            value,
            required={
                "schema",
                "schema_version",
                "candidate_id",
                "relative_path",
                "environment_kind",
                "lock_id",
                "lock_format",
                "resolution_digest",
                "status",
                "created_at",
                "updated_at",
                "verification_expected_digest",
                "verification_observed_digest",
            },
            label="candidate record",
        )
        if value.get("schema") != "depviz.candidate":
            raise ValueError("Not a depviz candidate record")
        if value.get("schema_version") != _CANDIDATE_SCHEMA_VERSION:
            raise ValueError(f"Unsupported candidate schema: {value.get('schema_version')!r}")
        record = CandidateRecord(
            candidate_id=_string(value.get("candidate_id"), "candidate_id"),
            relative_path=_string(value.get("relative_path"), "relative_path"),
            environment_kind=_string(value.get("environment_kind"), "environment_kind"),
            lock_id=_string(value.get("lock_id"), "lock_id"),
            lock_format=_string(value.get("lock_format"), "lock_format"),
            resolution_digest=_string(value.get("resolution_digest"), "resolution_digest"),
            status=CandidateStatus(_string(value.get("status"), "status")),
            created_at=_timestamp(value.get("created_at"), "created_at"),
            updated_at=_timestamp(value.get("updated_at"), "updated_at"),
            verification_expected_digest=_optional_string(
                value.get("verification_expected_digest"), "verification_expected_digest"
            ),
            verification_observed_digest=_optional_string(
                value.get("verification_observed_digest"), "verification_observed_digest"
            ),
        )
        _validate_candidate_id(record.candidate_id)
        if record.candidate_id != candidate_id:
            raise ValueError("Candidate record ID does not match its filename")
        if record.relative_path != f"environments/{candidate_id}":
            raise ValueError("Candidate record contains an invalid relative path")
        return record

    def list_candidates(self) -> tuple[CandidateRecord, ...]:
        if not self.records_dir.exists():
            return ()
        records: list[CandidateRecord] = []
        for path in sorted(self.records_dir.glob("*.json")):
            records.append(self.read_candidate(path.stem))
        return tuple(records)

    def update_candidate(
        self,
        candidate_id: str,
        *,
        status: CandidateStatus,
        verification_expected_digest: str | None = None,
        verification_observed_digest: str | None = None,
    ) -> CandidateRecord:
        current = self.read_candidate(candidate_id)
        updated = replace(
            current,
            status=status,
            updated_at=_now(),
            verification_expected_digest=verification_expected_digest,
            verification_observed_digest=verification_observed_digest,
        )
        self.write_candidate(updated)
        return updated

    def archive_lock(self, lock_id: str, content: bytes) -> Path:
        if len(content) > DEFAULT_MAX_DOCUMENT_BYTES:
            raise ValueError(
                f"Exact lock exceeds the {DEFAULT_MAX_DOCUMENT_BYTES}-byte safety limit"
            )
        path = self.archived_lock_path(lock_id)
        if path.exists():
            try:
                existing = read_bytes_limited(path, label="archived lock")
            except ValueError as error:
                raise ValueError(f"Cannot read archived lock {path}: {error}") from error
            if existing != content:
                raise ValueError(f"Archived lock {lock_id} has conflicting content")
            return path
        write_bytes_atomic(path, content)
        return path

    def archived_lock_path(self, lock_id: str) -> Path:
        if not lock_id.startswith("sha256:") or len(lock_id) != 71:
            raise ValueError(f"Invalid lock ID: {lock_id!r}")
        digest = lock_id.removeprefix("sha256:")
        if any(character not in "0123456789abcdef" for character in digest):
            raise ValueError(f"Invalid lock ID: {lock_id!r}")
        return self.locks_dir / f"sha256-{digest}.json"

    def read_state(self) -> DeploymentState:
        if not self.state_path.exists():
            return DeploymentState()
        value = _read_object(self.state_path, "deployment state")
        _require_exact_keys(
            value,
            required={"schema", "schema_version", "current_candidate_id", "history", "updated_at"},
            label="deployment state",
        )
        if value.get("schema") != "depviz.deployment":
            raise ValueError("Not a depviz deployment state")
        if value.get("schema_version") != _DEPLOYMENT_SCHEMA_VERSION:
            raise ValueError(f"Unsupported deployment schema: {value.get('schema_version')!r}")
        current = _optional_string(value.get("current_candidate_id"), "current_candidate_id")
        history_value = value.get("history", [])
        if not isinstance(history_value, list) or not all(
            isinstance(item, str) for item in history_value
        ):
            raise ValueError("Deployment history must be a list of candidate IDs")
        history = tuple(history_value)
        if current is not None:
            _validate_candidate_id(current)
        for item in history:
            _validate_candidate_id(item)
        return DeploymentState(
            current_candidate_id=current,
            history=history,
            updated_at=_timestamp(value.get("updated_at"), "updated_at"),
        )

    def write_state(self, state: DeploymentState) -> None:
        if state.current_candidate_id is not None:
            _validate_candidate_id(state.current_candidate_id)
        for item in state.history:
            _validate_candidate_id(item)
        payload: dict[str, object] = {
            "schema": "depviz.deployment",
            "schema_version": _DEPLOYMENT_SCHEMA_VERSION,
            "current_candidate_id": state.current_candidate_id,
            "history": list(state.history),
            "updated_at": state.updated_at or _now(),
        }
        write_bytes_atomic(self.state_path, _json_bytes(payload))

    def read_pending(self) -> PendingSwitch | None:
        if not self.pending_path.exists():
            return None
        value = _read_object(self.pending_path, "pending deployment switch")
        _require_exact_keys(
            value,
            required={
                "schema",
                "schema_version",
                "operation",
                "from_candidate_id",
                "to_candidate_id",
                "next_state",
            },
            label="pending deployment switch",
        )
        if value.get("schema") != "depviz.deployment-switch":
            raise ValueError("Not a depviz pending deployment switch")
        if value.get("schema_version") != _PENDING_SCHEMA_VERSION:
            raise ValueError(f"Unsupported pending-switch schema: {value.get('schema_version')!r}")
        next_state_value = value.get("next_state")
        if not isinstance(next_state_value, dict):
            raise ValueError("Pending switch next_state must be an object")
        next_state = _state_from_mapping(next_state_value)
        operation = _string(value.get("operation"), "operation")
        if operation not in {"promote", "rollback"}:
            raise ValueError(f"Unsupported pending switch operation: {operation!r}")
        return PendingSwitch(
            operation=operation,
            from_candidate_id=_optional_string(value.get("from_candidate_id"), "from_candidate_id"),
            to_candidate_id=_string(value.get("to_candidate_id"), "to_candidate_id"),
            next_state=next_state,
        )

    def write_pending(self, pending: PendingSwitch) -> None:
        payload: dict[str, object] = {
            "schema": "depviz.deployment-switch",
            "schema_version": _PENDING_SCHEMA_VERSION,
            "operation": pending.operation,
            "from_candidate_id": pending.from_candidate_id,
            "to_candidate_id": pending.to_candidate_id,
            "next_state": {
                "current_candidate_id": pending.next_state.current_candidate_id,
                "history": list(pending.next_state.history),
                "updated_at": pending.next_state.updated_at or _now(),
            },
        }
        write_bytes_atomic(self.pending_path, _json_bytes(payload))

    def clear_pending(self) -> None:
        self.pending_path.unlink(missing_ok=True)
        fsync_directory(self.metadata_dir)

    def current_link_candidate_id(self) -> str | None:
        if not self.current_path.exists() and not self.current_path.is_symlink():
            return None
        if not self.current_path.is_symlink():
            raise ValueError(f"Deployment current pointer is not a symlink: {self.current_path}")
        raw = os.readlink(self.current_path)
        path = Path(raw)
        if path.is_absolute() or len(path.parts) != 2 or path.parts[0] != "environments":
            raise ValueError(f"Deployment current pointer has an invalid target: {raw}")
        candidate_id = path.parts[1]
        _validate_candidate_id(candidate_id)
        return candidate_id

    def switch_current_link(self, candidate_id: str) -> None:
        if os.name != "posix":
            raise OSError("Atomic environment promotion currently requires POSIX symlinks")
        _validate_candidate_id(candidate_id)
        candidate_path = self.environments_dir / candidate_id
        if not candidate_path.is_dir() or candidate_path.is_symlink():
            raise ValueError(f"Candidate environment is missing or invalid: {candidate_path}")
        temporary = self.root / f".current.{uuid.uuid4().hex}.tmp"
        try:
            os.symlink(f"environments/{candidate_id}", temporary)
            os.replace(temporary, self.current_path)
            fsync_directory(self.root)
        finally:
            temporary.unlink(missing_ok=True)


def new_candidate_record(
    candidate: CandidateEnvironment,
    *,
    lock_id: str,
    lock_format: str,
    resolution_digest: str,
) -> CandidateRecord:
    now = _now()
    return CandidateRecord(
        candidate_id=candidate.candidate_id,
        relative_path=f"environments/{candidate.candidate_id}",
        environment_kind=candidate.kind,
        lock_id=lock_id,
        lock_format=lock_format,
        resolution_digest=resolution_digest,
        status=CandidateStatus.CREATED,
        created_at=now,
        updated_at=now,
    )


def _state_from_mapping(value: Mapping[object, object]) -> DeploymentState:
    normalized = {str(key): item for key, item in value.items()}
    _require_exact_keys(
        normalized,
        required={"current_candidate_id", "history", "updated_at"},
        label="pending deployment state",
    )
    current = normalized.get("current_candidate_id")
    if current is not None and not isinstance(current, str):
        raise ValueError("Pending state current_candidate_id must be a string or null")
    history_value = normalized.get("history")
    if not isinstance(history_value, list) or not all(
        isinstance(item, str) for item in history_value
    ):
        raise ValueError("Pending state history must be a list of candidate IDs")
    if current is not None:
        _validate_candidate_id(current)
    for item in history_value:
        _validate_candidate_id(item)
    raw_updated = normalized.get("updated_at")
    updated = _now() if raw_updated is None else _timestamp(raw_updated, "pending state updated_at")
    return DeploymentState(
        current_candidate_id=current,
        history=tuple(history_value),
        updated_at=updated,
    )


def _validate_candidate_id(candidate_id: str) -> None:
    if _CANDIDATE_ID.fullmatch(candidate_id) is None:
        raise ValueError(f"Invalid candidate ID: {candidate_id!r}")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json_bytes(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _read_object(path: Path, label: str) -> dict[str, object]:
    try:
        text = read_text_limited(path, label=label)
    except ValueError as error:
        raise ValueError(str(error)) from error
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in {label} {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be an object")
    return {str(key): item for key, item in value.items()}


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _timestamp(value: object, label: str) -> str:
    text = _string(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return text


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


def _optional_string(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _string(value, label)
