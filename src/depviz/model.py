from __future__ import annotations

from dataclasses import dataclass, field

from packaging.utils import canonicalize_name


def normalize_name(name: str) -> str:
    """Return the canonical PyPI/distribution spelling used by depviz."""

    return canonicalize_name(name.strip())


def normalize_conda_name(name: str) -> str:
    """Normalize Conda identity without folding valid separator characters."""

    return name.strip().lower()


def name_matches(key: PackageKey, query: str) -> bool:
    if key.ecosystem == "conda":
        return key.name == normalize_conda_name(query)
    return key.name == normalize_name(query)


@dataclass(frozen=True, order=True)
class PackageKey:
    ecosystem: str
    name: str

    def __post_init__(self) -> None:
        ecosystem = self.ecosystem.strip().lower()
        object.__setattr__(self, "ecosystem", ecosystem)
        normalizer = normalize_conda_name if ecosystem == "conda" else normalize_name
        object.__setattr__(self, "name", normalizer(self.name))

    def label(self) -> str:
        return self.name


@dataclass(frozen=True)
class RequirementEdge:
    parent: PackageKey
    target: PackageKey
    raw: str
    specifier: str = ""
    marker: str = ""
    extras: tuple[str, ...] = ()
    constraint_ecosystem: str = ""

    def __post_init__(self) -> None:
        if not self.constraint_ecosystem:
            object.__setattr__(self, "constraint_ecosystem", self.target.ecosystem)
        else:
            object.__setattr__(self, "constraint_ecosystem", self.constraint_ecosystem.lower())


@dataclass
class PackageRecord:
    key: PackageKey
    version: str | None = None
    dependencies: list[RequirementEdge] = field(default_factory=list)
    installed: bool = True
    python_names: set[str] = field(default_factory=set)
    metadata_diagnostics: list[str] = field(default_factory=list)


@dataclass
class Inventory:
    packages: dict[PackageKey, PackageRecord] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)
    source: str = "current environment"
    marker_environment: dict[str, str] = field(default_factory=dict)

    def add(self, record: PackageRecord) -> None:
        existing = self.packages.get(record.key)
        if existing is None:
            self.packages[record.key] = record
            return

        if not existing.installed and record.installed:
            self.packages[record.key] = record
            return
        if existing.installed and not record.installed:
            return

        if existing.installed and record.installed:
            merged = list(dict.fromkeys([*existing.dependencies, *record.dependencies]))
            existing.dependencies = merged
            existing.python_names.update(record.python_names)
            if existing.version != record.version:
                versions = sorted({str(existing.version), str(record.version)})
                existing.version = None
                message = (
                    f"multiple installed versions for {record.key.ecosystem}:{record.key.name}: "
                    + ", ".join(versions)
                )
                if message not in self.diagnostics:
                    self.diagnostics.append(message)
            return

        self.packages[record.key] = record


@dataclass(frozen=True)
class ManifestRequirement:
    package: PackageKey
    raw: str
    specifier: str = ""
    extras: tuple[str, ...] = ()
    is_root: bool = True


@dataclass(frozen=True)
class Manifest:
    requirements: tuple[ManifestRequirement, ...]
    source: str

    @property
    def roots(self) -> frozenset[PackageKey]:
        return frozenset(req.package for req in self.requirements if req.is_root)


@dataclass(frozen=True)
class ConstraintContributor:
    package: PackageKey
    raw: str
    specifier: str
    ecosystem: str = ""


@dataclass(frozen=True)
class PackageRisk:
    package: PackageKey
    version: str | None
    installed: bool
    affected_roots: tuple[PackageKey, ...]
    transitive_dependents: tuple[PackageKey, ...]
    transitive_count: int
    direct_dependents: tuple[PackageKey, ...]
    root_paths: tuple[tuple[PackageKey, ...], ...]
    version_state: str
    contributors: tuple[ConstraintContributor, ...]
    violated_by: tuple[ConstraintContributor, ...] = ()
    conflict_by: tuple[ConstraintContributor, ...] = ()

    @property
    def rank(self) -> tuple[int, int, int]:
        return (
            len(self.affected_roots),
            self.transitive_count,
            len(self.direct_dependents),
        )
