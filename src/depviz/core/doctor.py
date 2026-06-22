from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from depviz.api import OperationContext, Severity
from depviz.api.errors import BackendError
from depviz.infrastructure.deployment import CandidateStatus, ManagedDeploymentStore
from depviz.infrastructure.process_locks import ProcessLock, ProcessLockTimeout
from depviz.plugins.registry import PluginRegistry
from depviz.plugins.validation import validate_plugin


@dataclass(frozen=True)
class DoctorFinding:
    code: str
    message: str
    severity: Severity


@dataclass(frozen=True)
class DoctorReport:
    findings: tuple[DoctorFinding, ...]

    @property
    def passed(self) -> bool:
        return not any(item.severity is Severity.ERROR for item in self.findings)


def run_doctor(
    registry: PluginRegistry,
    *,
    context: OperationContext | None = None,
    plugin_names: tuple[str, ...] = (),
    deployment: Path | None = None,
    lock_timeout_seconds: float = 5.0,
) -> DoctorReport:
    findings: list[DoctorFinding] = []
    operation_context = context or OperationContext()
    plugins = registry.plugins()
    available = {plugin.name for plugin in plugins}
    unknown = sorted(set(plugin_names) - available)
    for name in unknown:
        findings.append(
            DoctorFinding(
                code="doctor.plugin.missing",
                message=f"No registered plugin is named {name!r}",
                severity=Severity.ERROR,
            )
        )
    selected = tuple(
        plugin for plugin in plugins if not plugin_names or plugin.name in plugin_names
    )
    for plugin in selected:
        try:
            validate_plugin(plugin)
        except Exception as exc:  # validation converts contract defects to stable plugin errors
            findings.append(
                DoctorFinding(
                    code="doctor.plugin.invalid",
                    message=f"Plugin {plugin.name} failed contract validation: {exc}",
                    severity=Severity.ERROR,
                )
            )
        else:
            findings.append(
                DoctorFinding(
                    code="doctor.plugin.valid",
                    message=(
                        f"Plugin {plugin.name} {plugin.plugin_version} satisfies API "
                        f"{plugin.api_version}"
                    ),
                    severity=Severity.INFO,
                )
            )
            for health_check in plugin.health_checks:
                try:
                    diagnostics = health_check.check(operation_context)
                except BackendError as exc:
                    findings.append(
                        DoctorFinding(
                            code="doctor.backend.unavailable",
                            message=str(exc),
                            severity=Severity.ERROR,
                        )
                    )
                except Exception as exc:
                    findings.append(
                        DoctorFinding(
                            code="doctor.backend.failed",
                            message=(
                                f"Health check {plugin.name}/{health_check.name} failed "
                                f"unexpectedly: {exc}"
                            ),
                            severity=Severity.ERROR,
                        )
                    )
                else:
                    findings.extend(
                        DoctorFinding(item.code, item.message, item.severity)
                        for item in diagnostics
                    )
    if deployment is not None:
        findings.extend(_deployment_findings(deployment, lock_timeout_seconds))
    return DoctorReport(tuple(findings))


def _deployment_findings(root: Path, lock_timeout_seconds: float) -> list[DoctorFinding]:
    store = ManagedDeploymentStore(root)
    if not store.root.exists():
        return [
            DoctorFinding(
                code="doctor.deployment.missing",
                message=f"Deployment root does not exist: {store.root}",
                severity=Severity.ERROR,
            )
        ]
    findings: list[DoctorFinding] = []
    try:
        store.validate_security()
        with ProcessLock(store.lock_path, timeout_seconds=lock_timeout_seconds):
            state = store.read_state()
            pending = store.read_pending()
            if pending is not None:
                findings.append(
                    DoctorFinding(
                        code="doctor.deployment.pending-switch",
                        message=(
                            f"Deployment has an interrupted {pending.operation} journal from "
                            f"{pending.from_candidate_id or '-'} to {pending.to_candidate_id}"
                        ),
                        severity=Severity.ERROR,
                    )
                )
            try:
                linked = store.current_link_candidate_id()
            except ValueError as exc:
                findings.append(
                    DoctorFinding(
                        code="doctor.deployment.pointer-invalid",
                        message=str(exc),
                        severity=Severity.ERROR,
                    )
                )
                linked = None
            if linked != state.current_candidate_id:
                findings.append(
                    DoctorFinding(
                        code="doctor.deployment.state-pointer-mismatch",
                        message=(
                            f"State names current={state.current_candidate_id!r}, but the current "
                            f"pointer names {linked!r}"
                        ),
                        severity=Severity.ERROR,
                    )
                )

            record_ids: set[str] = set()
            for record in store.list_candidates():
                record_ids.add(record.candidate_id)
                candidate = store.candidate(
                    record.candidate_id,
                    kind=record.environment_kind,
                    deployment_kind="managed-deployment",
                )
                if record.status is CandidateStatus.REMOVED:
                    if candidate.path.exists() or candidate.path.is_symlink():
                        findings.append(
                            DoctorFinding(
                                code="doctor.candidate.removed-present",
                                message=f"Removed candidate still exists: {candidate.path}",
                                severity=Severity.ERROR,
                            )
                        )
                elif not candidate.path.is_dir() or candidate.path.is_symlink():
                    findings.append(
                        DoctorFinding(
                            code="doctor.candidate.missing",
                            message=(
                                f"Candidate record {record.candidate_id} has no valid environment "
                                f"directory"
                            ),
                            severity=Severity.ERROR,
                        )
                    )
                try:
                    lock_path = store.archived_lock_path(record.lock_id)
                except ValueError as exc:
                    findings.append(
                        DoctorFinding(
                            code="doctor.candidate.lock-id-invalid",
                            message=f"Candidate {record.candidate_id}: {exc}",
                            severity=Severity.ERROR,
                        )
                    )
                else:
                    if not lock_path.is_file() or lock_path.is_symlink():
                        findings.append(
                            DoctorFinding(
                                code="doctor.candidate.lock-missing",
                                message=f"Candidate {record.candidate_id} has no archived exact lock",
                                severity=Severity.ERROR,
                            )
                        )
            for candidate_id in (state.current_candidate_id, *state.history):
                if candidate_id is not None and candidate_id not in record_ids:
                    findings.append(
                        DoctorFinding(
                            code="doctor.deployment.record-missing",
                            message=f"Deployment state references missing candidate {candidate_id}",
                            severity=Severity.ERROR,
                        )
                    )
            if store.environments_dir.exists():
                for path in store.environments_dir.iterdir():
                    if path.name not in record_ids:
                        findings.append(
                            DoctorFinding(
                                code="doctor.candidate.orphan-directory",
                                message=f"Untracked candidate directory: {path}",
                                severity=Severity.WARNING,
                            )
                        )
    except (OSError, ValueError, ProcessLockTimeout) as exc:
        findings.append(
            DoctorFinding(
                code="doctor.deployment.unreadable",
                message=str(exc),
                severity=Severity.ERROR,
            )
        )
    if not findings:
        findings.append(
            DoctorFinding(
                code="doctor.deployment.healthy",
                message=f"Managed deployment is structurally consistent: {store.root}",
                severity=Severity.INFO,
            )
        )
    return findings
