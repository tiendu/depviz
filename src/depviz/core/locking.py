from __future__ import annotations

from pathlib import Path

from depviz.api import LockArtifact, LockProvider, LockedResolution, OperationContext, Resolution
from depviz.api.errors import LockFailed
from depviz.infrastructure.storage import write_bytes_atomic


def create_lock(
    *,
    resolution: Resolution,
    provider: LockProvider,
    context: OperationContext,
) -> LockArtifact:
    if not resolution.complete:
        raise LockFailed(
            backend=provider.name,
            operation="lock",
            message="Cannot lock an incomplete resolution",
            diagnostics=resolution.diagnostics,
        )
    return provider.create_lock(resolution, context)


def read_lock(
    *,
    path: Path,
    provider: LockProvider,
    context: OperationContext,
) -> LockedResolution:
    return provider.read_lock(path, context)


def write_lock(path: Path, artifact: LockArtifact) -> None:
    write_bytes_atomic(path, artifact.content)
