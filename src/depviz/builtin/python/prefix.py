from __future__ import annotations

import json
import os
from collections.abc import Collection
from pathlib import Path
from urllib.parse import urlsplit

from packaging.requirements import InvalidRequirement, Requirement as PackagingRequirement

from depviz.api import (
    BackendPayload,
    Command,
    Diagnostic,
    EnvironmentState,
    EnvironmentTarget,
    OperationContext,
    PackageReference,
    ResolvedPackage,
    Severity,
    Target,
    normalize_package_name,
)
from depviz.api.errors import BackendError, InspectionFailed
from depviz.builtin.python.tooling import read_python_runtime, runner_for, uv_settings

_INSPECTION_SCRIPT = r"""
import base64
import csv
import hashlib
import importlib.metadata as metadata
import json
import sys
from io import StringIO
from pathlib import Path

items = []
environment_root = Path(sys.prefix).resolve()
for dist in metadata.distributions():
    distribution_root = Path(dist.locate_file("")).resolve()
    direct = None
    direct_text = dist.read_text("direct_url.json")
    if direct_text is not None:
        try:
            direct = json.loads(direct_text)
        except Exception as exc:
            direct = {"_error": str(exc)}
    installer_text = dist.read_text("INSTALLER")
    installer = installer_text.strip() if installer_text is not None else None

    record_errors = []
    hashed_record_entries = 0
    record_text = dist.read_text("RECORD")
    if record_text is None:
        record_errors.append("missing RECORD")
    else:
        for row in csv.reader(StringIO(record_text)):
            if len(row) != 3:
                record_errors.append("malformed RECORD row")
                continue
            relative, encoded_hash, encoded_size = row
            candidate = Path(dist.locate_file(relative)).resolve()
            try:
                candidate.relative_to(environment_root)
            except ValueError:
                record_errors.append(f"RECORD path escapes environment: {relative}")
                continue
            if not candidate.is_file():
                record_errors.append(f"missing installed file: {relative}")
                continue
            if encoded_size:
                try:
                    if candidate.stat().st_size != int(encoded_size):
                        record_errors.append(f"size mismatch: {relative}")
                except ValueError:
                    record_errors.append(f"invalid RECORD size: {relative}")
            if encoded_hash:
                try:
                    algorithm, expected = encoded_hash.split("=", 1)
                    digest = hashlib.new(algorithm, candidate.read_bytes()).digest()
                    actual = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
                    if actual != expected:
                        record_errors.append(f"hash mismatch: {relative}")
                    hashed_record_entries += 1
                except (ValueError, OSError) as exc:
                    record_errors.append(f"cannot verify {relative}: {exc}")
    if hashed_record_entries == 0:
        record_errors.append("RECORD contains no hashed files")

    items.append({
        "name": dist.metadata.get("Name"),
        "version": dist.version,
        "requires": dist.requires or [],
        "path": str(distribution_root),
        "direct_url": direct,
        "installer": installer,
        "record_errors": record_errors,
        "hashed_record_entries": hashed_record_entries,
    })
print(json.dumps(items, sort_keys=True))
""".strip()


def inspect_python_prefix(
    root: Path,
    context: OperationContext,
    *,
    backend: str,
    environment_kind: str,
    require_virtual_environment: bool,
    include_names: Collection[str] | None = None,
    include_installers: Collection[str] | None = None,
) -> EnvironmentState:
    resolved_root = root.expanduser().resolve()
    if not resolved_root.is_dir() or resolved_root.is_symlink():
        raise InspectionFailed(
            backend=backend,
            operation="inspect",
            message=f"Python environment is missing or invalid: {resolved_root}",
        )
    interpreter = python_executable(resolved_root)
    if not interpreter.is_file():
        raise InspectionFailed(
            backend=backend,
            operation="inspect",
            message=f"Python environment has no interpreter: {interpreter}",
        )
    settings = uv_settings(context, error=lambda message: _inspection_error(backend, message))
    runner = runner_for(context)
    try:
        runtime = read_python_runtime(
            runner=runner,
            settings=settings,
            backend=backend,
            operation="inspect",
            interpreter=str(interpreter),
        )
    except BackendError as exc:
        raise InspectionFailed(
            backend=exc.backend,
            operation=exc.operation,
            message=exc.message,
            diagnostics=exc.diagnostics,
        ) from exc
    if require_virtual_environment and not runtime.is_virtual_environment:
        raise InspectionFailed(
            backend=backend,
            operation="inspect",
            message=f"Target is not an isolated virtual environment: {resolved_root}",
        )
    try:
        result = runner.run(
            Command(
                argv=(str(interpreter), "-I", "-c", _INSPECTION_SCRIPT),
                environment={"PYTHONNOUSERSITE": "1"},
                remove_environment=("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"),
            ),
            timeout_seconds=settings.timeout_seconds,
            output_limit=settings.output_limit,
        )
    except FileNotFoundError as exc:
        raise InspectionFailed(
            backend=backend,
            operation="inspect",
            message=f"Interpreter not found: {interpreter}",
        ) from exc
    except OSError as exc:
        raise InspectionFailed(
            backend=backend,
            operation="inspect",
            message=f"Cannot inspect Python environment: {exc}",
        ) from exc
    if result.timed_out:
        raise InspectionFailed(
            backend=backend,
            operation="inspect",
            message="Python environment inspection timed out",
        )
    if result.output_truncated:
        raise InspectionFailed(
            backend=backend,
            operation="inspect",
            message="Python environment inspection output exceeded the configured limit",
        )
    if result.returncode != 0:
        raise InspectionFailed(
            backend=backend,
            operation="inspect",
            message=result.stderr.strip() or "Python metadata inspection failed",
        )
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise InspectionFailed(
            backend=backend,
            operation="inspect",
            message=f"Python metadata inspection returned invalid JSON: {exc}",
        ) from exc
    packages, diagnostics = _normalize_distributions(
        raw,
        resolved_root,
        runtime.target_id,
        backend=backend,
        include_names=include_names,
        include_installers=include_installers,
    )
    return EnvironmentState(
        packages=packages,
        target=Target(
            platform=runtime.target_id,
            python_version=runtime.version,
            implementation=runtime.implementation,
        ),
        complete=True,
        diagnostics=diagnostics,
        native_payload=BackendPayload(
            schema="depviz.python-inspection.native.v1",
            data={
                "interpreter": {
                    "implementation": runtime.implementation,
                    "version": runtime.version,
                    "major": runtime.major,
                    "minor": runtime.minor,
                    "platform": runtime.platform,
                    "soabi": runtime.soabi,
                    "executable": runtime.executable,
                }
            },
        ),
        environment=EnvironmentTarget(resolved_root, environment_kind),
    )


def python_executable(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _normalize_distributions(
    value: object,
    root: Path,
    platform_id: str,
    *,
    backend: str,
    include_names: Collection[str] | None,
    include_installers: Collection[str] | None,
) -> tuple[tuple[ResolvedPackage, ...], tuple[Diagnostic, ...]]:
    if not isinstance(value, list):
        raise _inspection_error(backend, "Python distribution metadata must be a list")
    selected_names = (
        {normalize_package_name("pypi", item) for item in include_names}
        if include_names is not None
        else None
    )
    selected_installers = (
        {item.strip().lower() for item in include_installers}
        if include_installers is not None
        else None
    )
    packages: list[ResolvedPackage] = []
    diagnostics: list[Diagnostic] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise _inspection_error(
                backend, f"Python distribution metadata item {index} must be an object"
            )
        name = _string(item.get("name"), f"distribution {index} name", backend)
        normalized = normalize_package_name("pypi", name)
        installer_value = item.get("installer")
        installer = installer_value.strip().lower() if isinstance(installer_value, str) else ""
        if selected_names is not None and normalized not in selected_names:
            continue
        if selected_installers is not None and installer not in selected_installers:
            continue
        version = _string(item.get("version"), f"distribution {name} version", backend)
        if normalized in seen:
            raise _inspection_error(
                backend, f"Python environment contains duplicate distribution {normalized}"
            )
        seen.add(normalized)
        metadata_path = Path(
            _string(item.get("path"), f"distribution {name} path", backend)
        ).resolve()
        if not metadata_path.is_relative_to(root):
            raise _inspection_error(
                backend, f"Distribution {name} is loaded from outside the Python prefix"
            )
        record_errors = item.get("record_errors")
        if not isinstance(record_errors, list) or not all(
            isinstance(error, str) for error in record_errors
        ):
            raise _inspection_error(
                backend, f"Distribution {name} returned invalid RECORD verification data"
            )
        if record_errors:
            raise _inspection_error(
                backend,
                f"Distribution {name} failed installed-file verification: "
                + "; ".join(record_errors),
            )
        source, artifact, checksum, editable = _direct_identity(
            item.get("direct_url"), name, backend
        )
        if editable:
            raise _inspection_error(
                backend, f"Editable distribution {name} is not supported in exact environments"
            )
        if source is None:
            source = f"installed:{installer}" if installer else "installed"
            diagnostics.append(
                Diagnostic(
                    code="inspect.python.artifact-unknown",
                    message=(
                        f"Installed distribution {name} does not record its original artifact; "
                        "the desired lock will replace it with an exact wheel identity"
                    ),
                    severity=Severity.WARNING,
                )
            )
        dependencies = _requires_dist(item.get("requires", []), name, backend)
        packages.append(
            ResolvedPackage(
                ecosystem="pypi",
                name=name,
                version=version,
                source=source,
                artifact=artifact,
                checksum=checksum,
                platform=platform_id,
                dependencies=dependencies,
            )
        )
    if selected_names is not None:
        missing = sorted(selected_names - seen)
        if missing:
            diagnostics.append(
                Diagnostic(
                    code="inspect.python.expected-missing",
                    message="Expected Python distributions are missing: " + ", ".join(missing),
                    severity=Severity.ERROR,
                )
            )
    return tuple(sorted(packages, key=lambda package: package.name)), tuple(diagnostics)


def _direct_identity(
    value: object, package: str, backend: str
) -> tuple[str | None, str | None, str | None, bool]:
    if value is None:
        return None, None, None, False
    if not isinstance(value, dict):
        raise _inspection_error(
            backend, f"Distribution {package} direct_url.json must be an object"
        )
    if "_error" in value:
        raise _inspection_error(
            backend,
            f"Distribution {package} has unreadable direct_url.json: {value['_error']}",
        )
    url = value.get("url")
    if not isinstance(url, str) or not url:
        raise _inspection_error(backend, f"Distribution {package} direct_url.json has no URL")
    parsed = urlsplit(url)
    if parsed.username is not None or parsed.password is not None or parsed.query:
        raise _inspection_error(
            backend,
            f"Distribution {package} direct URL contains credentials or query parameters",
        )
    directory = value.get("dir_info")
    editable = isinstance(directory, dict) and directory.get("editable") is True
    checksum: str | None = None
    archive = value.get("archive_info")
    if isinstance(archive, dict):
        raw_hash = archive.get("hash")
        if isinstance(raw_hash, str) and raw_hash.startswith("sha256="):
            checksum = f"sha256:{raw_hash.removeprefix('sha256=')}"
        hashes = archive.get("hashes")
        if checksum is None and isinstance(hashes, dict):
            sha = hashes.get("sha256")
            if isinstance(sha, str):
                checksum = f"sha256:{sha}"
    return url, url, checksum, editable


def _requires_dist(value: object, package: str, backend: str) -> tuple[PackageReference, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _inspection_error(
            backend, f"Distribution {package} requirements must be a list of strings"
        )
    dependencies: list[PackageReference] = []
    for text in value:
        try:
            requirement = PackagingRequirement(text)
        except InvalidRequirement as exc:
            raise _inspection_error(
                backend, f"Distribution {package} has invalid Requires-Dist {text!r}: {exc}"
            ) from exc
        dependencies.append(
            PackageReference(
                ecosystem="pypi",
                name=requirement.name,
                specifier=str(requirement.specifier) or None,
                marker=str(requirement.marker) if requirement.marker is not None else None,
            )
        )
    return tuple(dependencies)


def _string(value: object, label: str, backend: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _inspection_error(backend, f"{label} must be a non-empty string")
    return value


def _inspection_error(backend: str, message: str) -> InspectionFailed:
    return InspectionFailed(backend=backend, operation="inspect", message=message)
