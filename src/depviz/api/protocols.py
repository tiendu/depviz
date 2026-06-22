from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from depviz.api.context import OperationContext
from depviz.api.diagnostics import Diagnostic
from depviz.api.models import (
    ApplyResult,
    CandidateEnvironment,
    DependencyIntent,
    EnvironmentState,
    EnvironmentTarget,
    LockArtifact,
    LockedResolution,
    Resolution,
    Target,
    VerificationPolicy,
    VerificationReport,
)


@runtime_checkable
class HealthCheck(Protocol):
    name: str

    def check(self, context: OperationContext) -> tuple[Diagnostic, ...]: ...


@runtime_checkable
class ManifestLoader(Protocol):
    name: str
    formats: frozenset[str]

    def supports(self, path: Path) -> bool: ...

    def load(self, path: Path, context: OperationContext) -> DependencyIntent: ...


@runtime_checkable
class EnvironmentInspector(Protocol):
    name: str

    def inspect(
        self,
        target: EnvironmentTarget,
        context: OperationContext,
    ) -> EnvironmentState: ...


@runtime_checkable
class Resolver(Protocol):
    name: str

    def resolve(
        self,
        intent: DependencyIntent,
        target: Target,
        current: EnvironmentState | None,
        context: OperationContext,
    ) -> Resolution: ...


@runtime_checkable
class LockProvider(Protocol):
    name: str

    def create_lock(
        self,
        resolution: Resolution,
        context: OperationContext,
    ) -> LockArtifact: ...

    def read_lock(
        self,
        path: Path,
        context: OperationContext,
    ) -> LockedResolution: ...


@runtime_checkable
class EnvironmentDriver(Protocol):
    name: str
    environment_kind: str
    deployment_kind: str

    def create_candidate(
        self,
        target: EnvironmentTarget,
        context: OperationContext,
    ) -> CandidateEnvironment: ...

    def apply(
        self,
        lock: LockedResolution,
        candidate: CandidateEnvironment,
        context: OperationContext,
    ) -> ApplyResult: ...

    def discard(
        self,
        candidate: CandidateEnvironment,
        context: OperationContext,
    ) -> None: ...


@runtime_checkable
class Verifier(Protocol):
    name: str
    environment_kind: str
    deployment_kind: str

    def verify(
        self,
        expected: LockedResolution,
        environment: CandidateEnvironment,
        policy: VerificationPolicy,
        context: OperationContext,
    ) -> VerificationReport: ...
