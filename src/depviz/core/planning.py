from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping

from depviz.analysis.diff import diff_environment
from depviz.analysis.policies import evaluate_plan_policies
from depviz.api import (
    ChangeAspect,
    ChangeKind,
    ChangePlan,
    EnvironmentState,
    EnvironmentTarget,
    PackageChange,
    PlanPrecondition,
    PolicyFinding,
    Resolution,
    Severity,
    VersionDirection,
)
from depviz.api.errors import PlanningFailed
from depviz.core.resolution import (
    canonical_json_bytes,
    digest_json,
    environment_state_from_dict,
    environment_state_to_dict,
    package_from_dict,
    package_to_dict,
    resolution_from_dict,
    resolution_to_dict,
)
from depviz.infrastructure.storage import read_text_limited, write_bytes_atomic

PLAN_SCHEMA_VERSION = 1


def build_change_plan(
    *,
    manifest: Path,
    current: EnvironmentState,
    desired: Resolution,
    created_at: datetime | None = None,
) -> ChangePlan:
    if not current.complete:
        raise PlanningFailed(
            backend="core",
            operation="plan",
            message="Current environment inspection is incomplete",
            diagnostics=current.diagnostics,
        )
    if not desired.complete:
        raise PlanningFailed(
            backend="core",
            operation="plan",
            message="Desired resolution is incomplete",
            diagnostics=desired.diagnostics,
        )
    if current.target.platform != desired.target.platform:
        raise PlanningFailed(
            backend="core",
            operation="plan",
            message=(
                f"Current platform {current.target.platform!r} does not match desired "
                f"platform {desired.target.platform!r}"
            ),
        )

    try:
        manifest_digest = f"sha256:{hashlib.sha256(manifest.read_bytes()).hexdigest()}"
    except OSError as error:
        raise PlanningFailed(
            backend="core",
            operation="plan",
            message=f"Cannot read manifest {manifest}: {error}",
        ) from error

    current_digest = digest_json(environment_state_to_dict(current))
    resolution_digest = digest_json(resolution_to_dict(desired))
    native_digest = (
        digest_json(
            {
                "schema": desired.native_payload.schema,
                "data": desired.native_payload.data,
            }
        )
        if desired.native_payload is not None
        else None
    )
    operations = diff_environment(current, desired)
    findings = evaluate_plan_policies(operations, desired)
    timestamp = (created_at or datetime.now(UTC)).astimezone(UTC).isoformat()
    precondition_items = [
        PlanPrecondition(name="manifest-digest", value=manifest_digest),
        PlanPrecondition(name="current-state-digest", value=current_digest),
        PlanPrecondition(name="resolution-digest", value=resolution_digest),
        PlanPrecondition(name="target-platform", value=desired.target.platform),
    ]
    if current.environment is not None:
        precondition_items.append(
            PlanPrecondition(
                name="target-environment",
                value=f"{current.environment.kind}:{current.environment.path}",
            )
        )
    preconditions = tuple(precondition_items)

    plan = ChangePlan(
        plan_id="pending",
        created_at=timestamp,
        manifest_digest=manifest_digest,
        current_state_digest=current_digest,
        resolution_digest=resolution_digest,
        native_transaction_digest=native_digest,
        target=current.environment,
        before=current,
        after=desired,
        operations=operations,
        policy_findings=findings,
        preconditions=preconditions,
    )
    plan_id = digest_json(_plan_payload(plan, include_id=False))
    return replace(plan, plan_id=plan_id)


def plan_to_dict(plan: ChangePlan) -> dict[str, object]:
    return _plan_payload(plan, include_id=True)


def _plan_payload(plan: ChangePlan, *, include_id: bool) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "depviz.plan",
        "schema_version": PLAN_SCHEMA_VERSION,
        "created_at": plan.created_at,
        "manifest_digest": plan.manifest_digest,
        "current_state_digest": plan.current_state_digest,
        "resolution_digest": plan.resolution_digest,
        "native_transaction_digest": plan.native_transaction_digest,
        "target": (
            {"path": str(plan.target.path), "kind": plan.target.kind}
            if plan.target is not None
            else None
        ),
        "before": environment_state_to_dict(plan.before),
        "after": resolution_to_dict(plan.after),
        "operations": [change_to_dict(item) for item in plan.operations],
        "policy_findings": [finding_to_dict(item) for item in plan.policy_findings],
        "preconditions": [{"name": item.name, "value": item.value} for item in plan.preconditions],
    }
    if include_id:
        payload["plan_id"] = plan.plan_id
    return payload


def plan_from_dict(value: Mapping[str, object]) -> ChangePlan:
    _require_exact_keys(
        value,
        required={
            "schema",
            "schema_version",
            "plan_id",
            "created_at",
            "manifest_digest",
            "current_state_digest",
            "resolution_digest",
            "native_transaction_digest",
            "target",
            "before",
            "after",
            "operations",
            "policy_findings",
            "preconditions",
        },
        label="plan document",
    )
    if value.get("schema") != "depviz.plan":
        raise ValueError("Not a depviz plan document")
    if value.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise ValueError(f"Unsupported plan schema version: {value.get('schema_version')!r}")
    target_value = value.get("target")
    target: EnvironmentTarget | None = None
    if target_value is not None:
        target_mapping = _object(target_value, "target")
        target = EnvironmentTarget(
            path=Path(_string(target_mapping.get("path"), "target path")),
            kind=_string(target_mapping.get("kind"), "target kind"),
        )
    plan = ChangePlan(
        plan_id=_string(value.get("plan_id"), "plan_id"),
        created_at=_timestamp(value.get("created_at"), "created_at"),
        manifest_digest=_string(value.get("manifest_digest"), "manifest_digest"),
        current_state_digest=_string(value.get("current_state_digest"), "current_state_digest"),
        resolution_digest=_string(value.get("resolution_digest"), "resolution_digest"),
        native_transaction_digest=_optional_string(value.get("native_transaction_digest")),
        target=target,
        before=environment_state_from_dict(_object(value.get("before"), "before")),
        after=resolution_from_dict(_object(value.get("after"), "after")),
        operations=tuple(
            change_from_dict(item) for item in _object_list(value.get("operations"), "operations")
        ),
        policy_findings=tuple(
            finding_from_dict(item)
            for item in _object_list(value.get("policy_findings"), "policy_findings")
        ),
        preconditions=tuple(
            PlanPrecondition(
                name=_string(item.get("name"), "precondition name"),
                value=_string(item.get("value"), "precondition value"),
            )
            for item in _object_list(value.get("preconditions"), "preconditions")
        ),
    )
    expected = digest_json(_plan_payload(plan, include_id=False))
    if plan.plan_id != expected:
        raise ValueError("Plan content does not match plan_id")
    return plan


def plan_to_json(plan: ChangePlan, *, indent: int = 2) -> str:
    return json.dumps(plan_to_dict(plan), indent=indent, sort_keys=True, ensure_ascii=False) + "\n"


def write_plan_json(path: Path, plan: ChangePlan) -> None:
    write_bytes_atomic(path, plan_to_json(plan).encode("utf-8"))


def read_plan_json(path: Path) -> ChangePlan:
    try:
        value = json.loads(read_text_limited(path, label="plan"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read plan {path}: {error}") from error
    return plan_from_dict(_object(value, "plan root"))


def change_to_dict(change: PackageChange) -> dict[str, object]:
    return {
        "ecosystem": change.ecosystem,
        "name": change.name,
        "kind": change.kind.value,
        "aspects": sorted(item.value for item in change.aspects),
        "version_direction": (
            change.version_direction.value if change.version_direction is not None else None
        ),
        "before": package_to_dict(change.before) if change.before is not None else None,
        "after": package_to_dict(change.after) if change.after is not None else None,
    }


def change_from_dict(value: Mapping[str, object]) -> PackageChange:
    aspects_value = value.get("aspects", [])
    if not isinstance(aspects_value, list) or not all(
        isinstance(item, str) for item in aspects_value
    ):
        raise ValueError("Change aspects must be a list of strings")
    before_value = value.get("before")
    after_value = value.get("after")
    direction_value = value.get("version_direction")
    return PackageChange(
        ecosystem=_string(value.get("ecosystem"), "change ecosystem"),
        name=_string(value.get("name"), "change name"),
        kind=ChangeKind(_string(value.get("kind"), "change kind")),
        aspects=frozenset(ChangeAspect(item) for item in aspects_value),
        version_direction=(
            VersionDirection(_string(direction_value, "version direction"))
            if direction_value is not None
            else None
        ),
        before=(
            package_from_dict(_object(before_value, "change before")) if before_value else None
        ),
        after=(package_from_dict(_object(after_value, "change after")) if after_value else None),
    )


def finding_to_dict(finding: PolicyFinding) -> dict[str, object]:
    return {
        "code": finding.code,
        "message": finding.message,
        "severity": finding.severity.value,
        "package": finding.package,
        "hint": finding.hint,
    }


def finding_from_dict(value: Mapping[str, object]) -> PolicyFinding:
    return PolicyFinding(
        code=_string(value.get("code"), "finding code"),
        message=_string(value.get("message"), "finding message"),
        severity=Severity(_string(value.get("severity"), "finding severity")),
        package=_optional_string(value.get("package")),
        hint=_optional_string(value.get("hint")),
    )


def plan_digest(plan: ChangePlan) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(plan_to_dict(plan))).hexdigest()}"


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


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Expected a string or null")
    return value
