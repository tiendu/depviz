from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Mapping

from packaging.utils import canonicalize_name

from depviz.api.diagnostics import Diagnostic, Severity, SourceLocation


def normalize_package_name(ecosystem: str, name: str) -> str:
    """Normalize names only according to their own ecosystem's rules."""
    normalized_ecosystem = ecosystem.strip().lower()
    stripped_name = name.strip()
    if normalized_ecosystem == "pypi":
        return canonicalize_name(stripped_name)
    if normalized_ecosystem == "conda":
        return stripped_name.lower()
    return stripped_name


@dataclass(frozen=True)
class Requirement:
    ecosystem: str
    name: str
    specifier: str | None = None
    source: str | None = None
    marker: str | None = None
    extras: tuple[str, ...] = ()
    direct: bool = True
    origin: SourceLocation | None = None

    def __post_init__(self) -> None:
        ecosystem = self.ecosystem.strip().lower()
        object.__setattr__(self, "ecosystem", ecosystem)
        object.__setattr__(self, "name", normalize_package_name(ecosystem, self.name))
        object.__setattr__(self, "extras", tuple(sorted(set(self.extras))))


@dataclass(frozen=True)
class DependencyIntent:
    requirements: tuple[Requirement, ...]
    constraints: tuple[Requirement, ...] = ()
    channels: tuple[str, ...] = ()
    indexes: tuple[str, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def has_errors(self) -> bool:
        return any(diagnostic.severity is Severity.ERROR for diagnostic in self.diagnostics)


@dataclass(frozen=True)
class Target:
    platform: str
    python_version: str | None = None
    implementation: str | None = None


@dataclass(frozen=True)
class EnvironmentTarget:
    path: Path
    kind: str


@dataclass(frozen=True)
class BackendIdentity:
    component: str
    plugin: str
    plugin_version: str
    tool: str | None = None
    tool_version: str | None = None


@dataclass(frozen=True)
class PackageReference:
    ecosystem: str
    name: str
    specifier: str | None = None
    marker: str | None = None

    def __post_init__(self) -> None:
        ecosystem = self.ecosystem.strip().lower()
        object.__setattr__(self, "ecosystem", ecosystem)
        object.__setattr__(self, "name", normalize_package_name(ecosystem, self.name))


@dataclass(frozen=True)
class ResolvedPackage:
    ecosystem: str
    name: str
    version: str
    source: str | None = None
    artifact: str | None = None
    checksum: str | None = None
    build: str | None = None
    platform: str | None = None
    dependencies: tuple[PackageReference, ...] = ()

    def __post_init__(self) -> None:
        ecosystem = self.ecosystem.strip().lower()
        object.__setattr__(self, "ecosystem", ecosystem)
        object.__setattr__(self, "name", normalize_package_name(ecosystem, self.name))
        object.__setattr__(
            self,
            "dependencies",
            tuple(
                sorted(
                    set(self.dependencies),
                    key=lambda dependency: (
                        dependency.ecosystem,
                        dependency.name,
                        dependency.specifier or "",
                        dependency.marker or "",
                    ),
                )
            ),
        )

    @property
    def identity(self) -> tuple[str, str]:
        return self.ecosystem, self.name


@dataclass(frozen=True)
class BackendPayload:
    schema: str
    data: Mapping[str, object]


@dataclass(frozen=True)
class EnvironmentState:
    packages: tuple[ResolvedPackage, ...]
    target: Target
    complete: bool
    diagnostics: tuple[Diagnostic, ...] = ()
    native_payload: BackendPayload | None = None
    backend: BackendIdentity | None = None
    environment: EnvironmentTarget | None = None


class ResolutionStatus(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


@dataclass(frozen=True)
class Resolution:
    requested: tuple[Requirement, ...]
    packages: tuple[ResolvedPackage, ...]
    target: Target
    status: ResolutionStatus
    diagnostics: tuple[Diagnostic, ...] = ()
    native_payload: BackendPayload | None = None
    backend: BackendIdentity | None = None

    @property
    def complete(self) -> bool:
        return self.status is ResolutionStatus.COMPLETE


class ChangeKind(StrEnum):
    INSTALL = "install"
    REMOVE = "remove"
    MODIFY = "modify"
    UNCHANGED = "unchanged"


class ChangeAspect(StrEnum):
    VERSION = "version"
    BUILD = "build"
    SOURCE = "source"
    ARTIFACT = "artifact"
    CHECKSUM = "checksum"
    PLATFORM = "platform"


class VersionDirection(StrEnum):
    UPGRADE = "upgrade"
    DOWNGRADE = "downgrade"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PackageChange:
    ecosystem: str
    name: str
    kind: ChangeKind
    before: ResolvedPackage | None
    after: ResolvedPackage | None
    aspects: frozenset[ChangeAspect] = frozenset()
    version_direction: VersionDirection | None = None

    def __post_init__(self) -> None:
        ecosystem = self.ecosystem.strip().lower()
        object.__setattr__(self, "ecosystem", ecosystem)
        object.__setattr__(self, "name", normalize_package_name(ecosystem, self.name))


@dataclass(frozen=True)
class PolicyFinding:
    code: str
    message: str
    severity: Severity
    package: str | None = None
    hint: str | None = None


@dataclass(frozen=True)
class PlanPrecondition:
    name: str
    value: str


@dataclass(frozen=True)
class ChangePlan:
    plan_id: str
    created_at: str
    manifest_digest: str
    current_state_digest: str
    resolution_digest: str
    native_transaction_digest: str | None
    target: EnvironmentTarget | None
    before: EnvironmentState
    after: Resolution
    operations: tuple[PackageChange, ...]
    policy_findings: tuple[PolicyFinding, ...]
    preconditions: tuple[PlanPrecondition, ...]


@dataclass(frozen=True)
class LockArtifact:
    format: str
    content: bytes
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LockedResolution:
    resolution: Resolution
    artifact: LockArtifact


@dataclass(frozen=True)
class CandidateEnvironment:
    target: EnvironmentTarget
    candidate_id: str
    path: Path
    kind: str


@dataclass(frozen=True)
class ApplyResult:
    changed: bool
    diagnostics: tuple[Diagnostic, ...] = ()
    candidate: CandidateEnvironment | None = None
    lock_id: str | None = None


@dataclass(frozen=True)
class VerificationPolicy:
    load_packages: tuple[str, ...] = ()
    commands: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class VerificationReport:
    passed: bool
    expected_state_digest: str
    observed_state_digest: str | None
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class PromotionRecord:
    deployment: EnvironmentTarget
    current_candidate_id: str
    previous_candidate_id: str | None
    changed: bool


@dataclass(frozen=True)
class RollbackResult:
    deployment: EnvironmentTarget
    current_candidate_id: str
    replaced_candidate_id: str
    changed: bool
