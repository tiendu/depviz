from __future__ import annotations

import re

from depviz.api import (
    ChangeAspect,
    ChangeKind,
    PackageChange,
    PolicyFinding,
    Resolution,
    Severity,
    VersionDirection,
)

_RUNTIME_PACKAGES = frozenset({"python", "r-base", "nodejs", "openjdk", "java"})
_MAJOR_VERSION = re.compile(r"^([0-9]+)(?:\.|$)")


def evaluate_plan_policies(
    changes: tuple[PackageChange, ...],
    desired: Resolution,
) -> tuple[PolicyFinding, ...]:
    findings: list[PolicyFinding] = []

    for change in changes:
        if change.kind is ChangeKind.REMOVE:
            findings.append(
                PolicyFinding(
                    code="policy.package-removal",
                    message=f"Package {change.name} will be removed",
                    severity=Severity.WARNING,
                    package=change.name,
                )
            )
        if change.version_direction is VersionDirection.DOWNGRADE:
            findings.append(
                PolicyFinding(
                    code="policy.package-downgrade",
                    message=(
                        f"Package {change.name} will be downgraded from "
                        f"{change.before.version if change.before else '?'} to "
                        f"{change.after.version if change.after else '?'}"
                    ),
                    severity=Severity.WARNING,
                    package=change.name,
                )
            )
        if _is_major_upgrade(change):
            findings.append(
                PolicyFinding(
                    code="policy.major-version-upgrade",
                    message=(
                        f"Package {change.name} crosses a major version boundary: "
                        f"{change.before.version if change.before else '?'} -> "
                        f"{change.after.version if change.after else '?'}"
                    ),
                    severity=Severity.WARNING,
                    package=change.name,
                )
            )
        if ChangeAspect.SOURCE in change.aspects:
            findings.append(
                PolicyFinding(
                    code="policy.source-change",
                    message=(
                        f"Package {change.name} changes source from "
                        f"{change.before.source if change.before else '?'} to "
                        f"{change.after.source if change.after else '?'}"
                    ),
                    severity=Severity.WARNING,
                    package=change.name,
                )
            )
        if change.name in _RUNTIME_PACKAGES and change.kind is not ChangeKind.UNCHANGED:
            findings.append(
                PolicyFinding(
                    code="policy.runtime-change",
                    message=f"Runtime package {change.name} will change",
                    severity=Severity.WARNING,
                    package=change.name,
                )
            )

    for package in desired.packages:
        if package.source is None or package.artifact is None:
            findings.append(
                PolicyFinding(
                    code="policy.incomplete-artifact-identity",
                    message=f"Package {package.name} lacks complete source or artifact identity",
                    severity=Severity.ERROR,
                    package=package.name,
                )
            )
        if package.checksum is None:
            findings.append(
                PolicyFinding(
                    code="policy.unhashed-artifact",
                    message=f"Package {package.name} has no recorded artifact checksum",
                    severity=Severity.ERROR,
                    package=package.name,
                    hint="Exact lock generation will reject this package.",
                )
            )
        elif not package.checksum.startswith("sha256:"):
            findings.append(
                PolicyFinding(
                    code="policy.weak-artifact-hash",
                    message=f"Package {package.name} is identified only by {package.checksum.split(':', 1)[0]}",
                    severity=Severity.WARNING,
                    package=package.name,
                )
            )

    return tuple(
        sorted(findings, key=lambda item: (item.severity.value, item.code, item.package or ""))
    )


def _is_major_upgrade(change: PackageChange) -> bool:
    if change.version_direction is not VersionDirection.UPGRADE:
        return False
    if change.before is None or change.after is None:
        return False
    before = _MAJOR_VERSION.match(change.before.version)
    after = _MAJOR_VERSION.match(change.after.version)
    return before is not None and after is not None and int(after.group(1)) > int(before.group(1))
