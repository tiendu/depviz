from __future__ import annotations

from dataclasses import dataclass

from depviz.api.diagnostics import Diagnostic


class DepvizError(Exception):
    """Base class for expected depviz failures."""


@dataclass(eq=False)
class BackendError(DepvizError):
    backend: str
    operation: str
    message: str
    diagnostics: tuple[Diagnostic, ...] = ()

    def __str__(self) -> str:
        return f"{self.backend} {self.operation} failed: {self.message}"


class ToolUnavailable(BackendError):
    pass


class UnsupportedManifest(BackendError):
    pass


class ResolutionFailed(BackendError):
    pass


class InspectionFailed(BackendError):
    pass


class PlanningFailed(BackendError):
    pass


class LockFailed(BackendError):
    pass


class IncompleteResolution(BackendError):
    pass


class ApplyFailed(BackendError):
    pass


class VerificationFailed(BackendError):
    pass


class PromotionFailed(BackendError):
    pass


class RollbackFailed(BackendError):
    pass


class PluginError(DepvizError):
    pass


class PluginRegistrationError(PluginError):
    pass


class PluginCompatibilityError(PluginError):
    pass


class UnsupportedCapabilityError(PluginError):
    pass
