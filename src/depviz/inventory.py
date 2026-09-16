from __future__ import annotations

import importlib.metadata
import json
import os
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement

from depviz.model import (
    Inventory,
    PackageKey,
    PackageRecord,
    RequirementEdge,
    normalize_conda_name,
    normalize_name,
)

_CONDA_DEP_NAME = re.compile(r"^(?:(?:[^:\s]+)::)?([A-Za-z0-9_.-]+)(.*)$")
_PYTHON_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _python_names_from_conda_files(files: object) -> set[str]:
    names: set[str] = set()
    if not isinstance(files, list):
        return names
    for item in files:
        if not isinstance(item, str):
            continue
        item = item.replace("\\", "/")
        for suffix in (".dist-info", ".egg-info"):
            marker = suffix + "/"
            if marker not in item:
                continue
            directory = item.split(marker, 1)[0].rsplit("/", 1)[-1] + suffix
            stem = directory[: -len(suffix)]
            # Wheel/egg metadata directories normally end in -<version>. The
            # distribution portion uses normalized separators, so the final '-' is
            # a safe best-effort version separator.
            name = stem.rsplit("-", 1)[0] if "-" in stem else stem
            if name:
                names.add(normalize_name(name))
    return names


def _parse_conda_dependency(text: str, parent: PackageKey) -> RequirementEdge | None:
    raw = text.strip()
    if not raw:
        return None
    match = _CONDA_DEP_NAME.match(raw)
    if not match:
        return None
    raw_name = match.group(1)
    if raw_name.startswith("__"):
        # Conda virtual packages describe host capabilities, not installed packages.
        return None
    name = normalize_conda_name(raw_name)
    tail = match.group(2).strip()
    # Keep the complete MatchSpec tail. The constraint layer deliberately returns
    # UNKNOWN for syntax it cannot model safely instead of silently dropping it.
    target = PackageKey("conda", name)
    return RequirementEdge(parent=parent, target=target, raw=text, specifier=tail)


def _load_conda_prefix(prefix: Path, inventory: Inventory) -> set[str]:
    meta_dir = prefix / "conda-meta"
    if not meta_dir.is_dir():
        return set()
    conda_names: set[str] = set()
    for metadata_file in sorted(meta_dir.glob("*.json")):
        try:
            data = json.loads(metadata_file.read_text(encoding="utf-8"))
            name = normalize_conda_name(str(data["name"]))
            key = PackageKey("conda", name)
            record = PackageRecord(
                key=key,
                version=str(data.get("version") or "") or None,
                python_names=_python_names_from_conda_files(data.get("files")),
            )
            for raw_dep in data.get("depends", []) or []:
                if not isinstance(raw_dep, str):
                    inventory.diagnostics.append(
                        f"could not parse Conda dependency from {name}: {raw_dep!r}; "
                        "analysis may be incomplete"
                    )
                    continue
                edge = _parse_conda_dependency(raw_dep, key)
                if edge:
                    record.dependencies.append(edge)
                    continue
                match = _CONDA_DEP_NAME.match(raw_dep.strip())
                is_virtual = bool(match and match.group(1).startswith("__"))
                if raw_dep.strip() and not is_virtual:
                    inventory.diagnostics.append(
                        f"could not parse Conda dependency from {name}: {raw_dep!r}; "
                        "analysis may be incomplete"
                    )
            inventory.add(record)
            conda_names.add(name)
        except Exception as error:  # corrupt metadata should not erase the rest of the graph
            inventory.diagnostics.append(f"could not read {metadata_file.name}: {error}")
    return conda_names


def _dist_is_pip_owned(dist: importlib.metadata.Distribution) -> bool:
    try:
        installer = (dist.read_text("INSTALLER") or "").strip().lower()
    except Exception:
        installer = ""
    return installer == "pip"


def _python_record(
    raw_name: str,
    version: str | None,
    requires: Iterable[str],
    *,
    diagnostics: list[str] | None = None,
) -> PackageRecord:
    key = PackageKey("pypi", raw_name)
    record = PackageRecord(key=key, version=version)
    for raw_req in requires:
        try:
            req = Requirement(raw_req)
        except InvalidRequirement:
            match = _PYTHON_REQ_NAME.match(raw_req)
            if match is None:
                message = (
                    f"malformed dependency metadata from {key.name}: {raw_req!r}; "
                    "dependency name could not be recovered, analysis may be incomplete"
                )
                record.metadata_diagnostics.append(message)
                if diagnostics is not None:
                    diagnostics.append(message)
                continue

            recovered_name = match.group(1)
            # Keep the remainder as an opaque constraint. The constraint layer will
            # classify unsupported syntax as UNKNOWN. Preserving the edge is safer
            # than silently understating blast radius.
            opaque = raw_req[match.end():].strip()
            if ";" in opaque:
                opaque = opaque.split(";", 1)[0].strip()
            record.dependencies.append(
                RequirementEdge(
                    parent=key,
                    target=PackageKey("pypi", recovered_name),
                    raw=raw_req,
                    specifier=opaque,
                    constraint_ecosystem="pypi",
                )
            )
            message = (
                f"malformed dependency metadata from {key.name}: {raw_req!r}; "
                f"recovered dependency name {normalize_name(recovered_name)!r}, "
                "version constraint is UNKNOWN"
            )
            record.metadata_diagnostics.append(message)
            if diagnostics is not None:
                diagnostics.append(message)
            continue

        record.dependencies.append(
            RequirementEdge(
                parent=key,
                target=PackageKey("pypi", req.name),
                raw=raw_req,
                specifier=str(req.specifier),
                marker=str(req.marker) if req.marker is not None else "",
                extras=tuple(sorted(req.extras)),
            )
        )
    return record


def _python_records_current() -> Iterable[tuple[PackageRecord, bool]]:
    for dist in importlib.metadata.distributions():
        raw_name = dist.metadata.get("Name")
        if not raw_name:
            continue
        record = _python_record(
            raw_name,
            dist.version,
            dist.requires or [],
        )
        yield record, _dist_is_pip_owned(dist)


def _python_records_external(
    python: Path,
) -> tuple[list[tuple[PackageRecord, bool]], dict[str, str]]:
    # Keep the target interpreter dependency-free: it only dumps stdlib/importlib metadata.
    # Requirement parsing and marker evaluation happen back in depviz's interpreter.
    script = r'''import importlib.metadata, json, os, platform, sys

def implementation_version():
    version = platform.python_version()
    kind = getattr(sys.implementation, "version", None)
    if kind and kind.releaselevel != "final":
        version += kind.releaselevel[0] + str(kind.serial)
    return version

marker_environment={
    "implementation_name": sys.implementation.name,
    "implementation_version": implementation_version(),
    "os_name": os.name,
    "platform_machine": platform.machine(),
    "platform_release": platform.release(),
    "platform_system": platform.system(),
    "platform_version": platform.version(),
    "python_full_version": platform.python_version(),
    "platform_python_implementation": platform.python_implementation(),
    "python_version": ".".join(platform.python_version_tuple()[:2]),
    "sys_platform": sys.platform,
    "extra": "",
}
out=[]
for dist in importlib.metadata.distributions():
    name=dist.metadata.get("Name")
    if not name: continue
    installer=(dist.read_text("INSTALLER") or "").strip().lower()
    out.append({
        "name":name,
        "version":dist.version,
        "installer":installer,
        "requires":list(dist.requires or []),
    })
print(json.dumps({"marker_environment":marker_environment,"distributions":out}))
'''
    proc = subprocess.run(
        [str(python), "-c", script],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    payload = json.loads(proc.stdout)
    marker_environment = payload["marker_environment"]
    result: list[tuple[PackageRecord, bool]] = []
    for item in payload["distributions"]:
        result.append(
            (
                _python_record(
                    item["name"],
                    item.get("version"),
                    item.get("requires", []),
                ),
                item.get("installer") == "pip",
            )
        )
    return result, marker_environment

def _python_for_prefix(prefix: Path) -> Path | None:
    candidates = [prefix / "bin" / "python", prefix / "Scripts" / "python.exe"]
    return next((p for p in candidates if p.exists()), None)


def reconcile_inventory(inventory: Inventory) -> None:
    """Bind dependency references to the package actually installed by name.

    A pip distribution inside a Conda environment may require ``numpy`` even when NumPy
    itself is Conda-owned. Without this pass that edge would point at a synthetic
    ``pypi:numpy`` node and under-count the real NumPy blast radius.
    """

    installed = {key for key, record in inventory.packages.items() if record.installed}
    by_name: dict[str, list[PackageKey]] = {}
    conda_by_python_name: dict[str, list[PackageKey]] = {}
    for key in installed:
        by_name.setdefault(key.name, []).append(key)
        record = inventory.packages[key]
        if key.ecosystem == "conda":
            for python_name in record.python_names:
                conda_by_python_name.setdefault(python_name, []).append(key)

    for record in list(inventory.packages.values()):
        if not record.installed:
            continue
        rebound: list[RequirementEdge] = []
        for edge in record.dependencies:
            target = edge.target
            if target not in installed:
                matches = by_name.get(target.name, [])
                same_ecosystem = [key for key in matches if key.ecosystem == target.ecosystem]
                if len(same_ecosystem) == 1:
                    target = same_ecosystem[0]
                elif target.ecosystem == "pypi":
                    # A Python distribution may be Conda-owned. Compare using PyPI
                    # canonicalization only for this cross-ecosystem bridge; Conda
                    # identity itself keeps '.', '_' and '-' distinct.
                    conda_matches = conda_by_python_name.get(target.name, [])
                    if len(conda_matches) == 1:
                        target = conda_matches[0]
                    elif len(conda_matches) > 1:
                        inventory.diagnostics.append(
                            f"ambiguous dependency identity for {edge.raw!r} from {record.key.name}"
                        )
                elif len(matches) > 1:
                    inventory.diagnostics.append(
                        f"ambiguous dependency identity for {edge.raw!r} from {record.key.name}"
                    )
            rebound.append(replace(edge, target=target))
        record.dependencies = rebound




def _merge_python_metadata_into_conda(
    inventory: Inventory,
    conda_key: PackageKey,
    python_record: PackageRecord,
) -> None:
    """Attach Python distribution metadata to its Conda-owned package node.

    Conda metadata is authoritative for package identity/version, while Python
    ``Requires-Dist`` metadata carries extras and environment markers that Conda's
    coarse dependency list often cannot express. Keeping both sources on one node
    avoids either duplicating the package or losing optional dependency edges.
    """

    conda_record = inventory.packages.get(conda_key)
    if conda_record is None:
        return
    merged = list(conda_record.dependencies)
    for edge in python_record.dependencies:
        rebound = replace(edge, parent=conda_key)
        if rebound not in merged:
            merged.append(rebound)
    conda_record.dependencies = merged
    conda_record.python_names.add(python_record.key.name)

def load_inventory(prefix: Path | None = None) -> Inventory:
    prefix = prefix.resolve() if prefix else None
    source = str(prefix) if prefix else "current environment"
    marker_environment = default_environment()
    marker_environment["extra"] = ""
    inventory = Inventory(source=source, marker_environment=marker_environment)

    conda_prefix: Path | None = None
    if prefix and (prefix / "conda-meta").is_dir():
        conda_prefix = prefix
    elif not prefix:
        env_prefix = os.environ.get("CONDA_PREFIX")
        if env_prefix and (Path(env_prefix) / "conda-meta").is_dir():
            conda_prefix = Path(env_prefix)

    conda_names = _load_conda_prefix(conda_prefix, inventory) if conda_prefix else set()
    conda_by_python_name: dict[str, list[PackageKey]] = {}
    for name in conda_names:
        key = PackageKey("conda", name)
        record = inventory.packages[key]
        aliases = set(record.python_names)
        # Same-name non-pip metadata is also strong ownership evidence even when old
        # Conda records do not list files. Once merged, the alias is persisted on the
        # record and reconciliation uses only proven aliases.
        aliases.add(normalize_name(name))
        for python_name in aliases:
            conda_by_python_name.setdefault(python_name, []).append(key)

    python = _python_for_prefix(prefix) if prefix else Path(sys.executable)
    if python is None:
        inventory.diagnostics.append("no Python interpreter found in target environment")
        reconcile_inventory(inventory)
        return inventory

    try:
        if not prefix or python.resolve() == Path(sys.executable).resolve():
            records = list(_python_records_current())
        else:
            records, external_marker_environment = _python_records_external(python)
            inventory.marker_environment = external_marker_environment
        for record, pip_owned in records:
            for message in record.metadata_diagnostics:
                if message not in inventory.diagnostics:
                    inventory.diagnostics.append(message)
            conda_matches = conda_by_python_name.get(record.key.name, [])
            if not pip_owned and len(conda_matches) == 1:
                _merge_python_metadata_into_conda(inventory, conda_matches[0], record)
                continue
            if not pip_owned and len(conda_matches) > 1:
                inventory.diagnostics.append(
                    f"ambiguous Conda owner for Python distribution {record.key.name!r}; "
                    "keeping it as a separate Python node"
                )
            inventory.add(record)
    except Exception as error:
        inventory.diagnostics.append(f"could not inspect Python metadata: {error}")

    reconcile_inventory(inventory)
    return inventory
