from __future__ import annotations

import re

from depviz.api import (
    ChangeAspect,
    ChangeKind,
    EnvironmentState,
    PackageChange,
    Resolution,
    ResolvedPackage,
    VersionDirection,
)

_NUMERIC_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+)*$")


def diff_environment(current: EnvironmentState, desired: Resolution) -> tuple[PackageChange, ...]:
    before = {package.identity: package for package in current.packages}
    after = {package.identity: package for package in desired.packages}
    identities = sorted(set(before) | set(after))
    changes: list[PackageChange] = []

    for ecosystem, name in identities:
        installed = before.get((ecosystem, name))
        resolved = after.get((ecosystem, name))
        if installed is None and resolved is not None:
            changes.append(
                PackageChange(
                    ecosystem=ecosystem,
                    name=name,
                    kind=ChangeKind.INSTALL,
                    before=None,
                    after=resolved,
                )
            )
            continue
        if installed is not None and resolved is None:
            changes.append(
                PackageChange(
                    ecosystem=ecosystem,
                    name=name,
                    kind=ChangeKind.REMOVE,
                    before=installed,
                    after=None,
                )
            )
            continue
        if installed is None or resolved is None:
            raise AssertionError("unreachable package diff state")

        aspects = _changed_aspects(installed, resolved)
        changes.append(
            PackageChange(
                ecosystem=ecosystem,
                name=name,
                kind=ChangeKind.UNCHANGED if not aspects else ChangeKind.MODIFY,
                before=installed,
                after=resolved,
                aspects=frozenset(aspects),
                version_direction=(
                    compare_plain_numeric_versions(installed.version, resolved.version)
                    if ChangeAspect.VERSION in aspects
                    else None
                ),
            )
        )

    return tuple(changes)


def compare_plain_numeric_versions(before: str, after: str) -> VersionDirection:
    """Compare only unambiguous dotted numeric versions.

    Conda version ordering is richer than PEP 440. Refusing to classify complex
    versions is safer than mislabelling a downgrade as an upgrade.
    """
    if not _NUMERIC_VERSION.fullmatch(before) or not _NUMERIC_VERSION.fullmatch(after):
        return VersionDirection.UNKNOWN
    left = tuple(int(item) for item in before.split("."))
    right = tuple(int(item) for item in after.split("."))
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    if right > left:
        return VersionDirection.UPGRADE
    if right < left:
        return VersionDirection.DOWNGRADE
    return VersionDirection.UNKNOWN


def _changed_aspects(before: ResolvedPackage, after: ResolvedPackage) -> set[ChangeAspect]:
    aspects: set[ChangeAspect] = set()
    if before.version != after.version:
        aspects.add(ChangeAspect.VERSION)
    if before.build != after.build:
        aspects.add(ChangeAspect.BUILD)
    if before.source != after.source:
        aspects.add(ChangeAspect.SOURCE)
    if before.artifact != after.artifact:
        aspects.add(ChangeAspect.ARTIFACT)
    if before.checksum != after.checksum:
        aspects.add(ChangeAspect.CHECKSUM)
    if before.platform != after.platform:
        aspects.add(ChangeAspect.PLATFORM)
    return aspects
