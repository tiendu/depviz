from __future__ import annotations

import json
import tempfile
import tomllib
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.tags import Tag, sys_tags
from packaging.utils import InvalidWheelFilename, parse_wheel_filename

from depviz.api import (
    BackendPayload,
    DependencyIntent,
    Diagnostic,
    EnvironmentState,
    OperationContext,
    PackageReference,
    Requirement,
    Resolution,
    ResolutionStatus,
    ResolvedPackage,
    Severity,
    Target,
)
from depviz.api.errors import BackendError, ResolutionFailed
from depviz.builtin.python.tooling import (
    isolated_uv_environment,
    read_python_runtime,
    read_uv_version,
    require_host_compatible_runtime,
    runner_for,
    uv_environment_to_remove,
    uv_settings,
)
from depviz.api import Command
from depviz.infrastructure.redaction import credential_secrets, redact_text, sanitize_json


class UvResolver:
    """Resolve one host-compatible Python environment through uv's project solver."""

    name = "uv-lock"

    def resolve(
        self,
        intent: DependencyIntent,
        target: Target,
        current: EnvironmentState | None,
        context: OperationContext,
    ) -> Resolution:
        del target, current
        _validate_intent(intent)
        settings = uv_settings(context, error=_resolution_error)
        runner = runner_for(context)
        secrets = credential_secrets(
            (*intent.indexes, *(item.source or "" for item in intent.requirements))
        )
        try:
            runtime = read_python_runtime(
                runner=runner,
                settings=settings,
                backend=self.name,
                operation="resolve",
            )
            require_host_compatible_runtime(runtime, backend=self.name, operation="resolve")
            _validate_requires_python(intent, runtime.version)
            tool_version = read_uv_version(
                runner=runner,
                settings=settings,
                backend=self.name,
                operation="resolve",
            )
        except BackendError as exc:
            raise _as_resolution_failed(exc) from exc

        with tempfile.TemporaryDirectory(prefix="depviz-uv-resolve-") as temporary_directory:
            root = Path(temporary_directory)
            pyproject = root / "pyproject.toml"
            pyproject.write_text(
                _project_document(intent, runtime.major, runtime.minor), encoding="utf-8"
            )
            cache = root / "cache"
            command = Command(
                argv=_lock_command(
                    settings.executable,
                    settings.interpreter,
                    intent,
                    offline=context.offline,
                ),
                cwd=root,
                environment=isolated_uv_environment(cache_dir=cache),
                remove_environment=uv_environment_to_remove(),
            )
            try:
                result = runner.run(
                    command,
                    timeout_seconds=settings.timeout_seconds,
                    output_limit=settings.output_limit,
                    redact=secrets,
                )
            except FileNotFoundError as exc:
                raise ResolutionFailed(
                    backend=self.name,
                    operation="resolve",
                    message=f"Executable not found: {settings.executable}",
                ) from exc
            except OSError as exc:
                raise ResolutionFailed(
                    backend=self.name,
                    operation="resolve",
                    message=f"Cannot execute {settings.executable!r}: {exc}",
                ) from exc
            if result.timed_out:
                raise ResolutionFailed(
                    backend=self.name,
                    operation="resolve",
                    message=f"uv resolution timed out after {settings.timeout_seconds:g} seconds",
                )
            if result.output_truncated:
                raise ResolutionFailed(
                    backend=self.name,
                    operation="resolve",
                    message="uv resolver output exceeded the configured limit",
                )
            if result.returncode != 0:
                raise ResolutionFailed(
                    backend=self.name,
                    operation="resolve",
                    message=(
                        redact_text(result.stderr.strip() or result.stdout.strip(), secrets)
                        or "uv lock failed"
                    ),
                )
            lock_path = root / "uv.lock"
            try:
                raw_lock = lock_path.read_bytes()
                lock = tomllib.loads(raw_lock.decode("utf-8"))
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
                raise ResolutionFailed(
                    backend=self.name,
                    operation="resolve",
                    message=f"Cannot read uv solver output: {exc}",
                ) from exc

        try:
            packages = _packages_from_uv_lock(lock, runtime.target_id, _direct_artifacts(intent))
        except ValueError as exc:
            raise ResolutionFailed(
                backend=self.name,
                operation="normalize resolution",
                message=str(exc),
            ) from exc
        if not packages:
            raise ResolutionFailed(
                backend=self.name,
                operation="resolve",
                message="uv returned no installable packages",
            )

        normalized_target = Target(
            platform=runtime.target_id,
            python_version=runtime.version,
            implementation=runtime.implementation,
        )
        payload = BackendPayload(
            schema="depviz.uv-lock.native.v1",
            data={
                "tool": "uv",
                "tool_version": tool_version,
                "interpreter": {
                    "implementation": runtime.implementation,
                    "version": runtime.version,
                    "major": runtime.major,
                    "minor": runtime.minor,
                    "platform": runtime.platform,
                    "soabi": runtime.soabi,
                    "executable": runtime.executable,
                },
                "uv_lock": sanitize_json(_json_safe(lock), secrets),
            },
        )
        diagnostics = (
            Diagnostic(
                code="resolve.python.host-bound",
                message=(
                    "The Python resolution is bound to the selected interpreter, host platform, "
                    "and ABI; cross-interpreter artifact selection is intentionally rejected"
                ),
                severity=Severity.INFO,
            ),
        )
        return Resolution(
            requested=intent.requirements,
            packages=packages,
            target=normalized_target,
            status=ResolutionStatus.COMPLETE,
            diagnostics=diagnostics,
            native_payload=payload,
        )


def _validate_requires_python(intent: DependencyIntent, version: str) -> None:
    raw = intent.metadata.get("requires-python")
    if raw is None:
        return
    try:
        specifier = SpecifierSet(raw)
    except InvalidSpecifier as exc:
        raise ResolutionFailed(
            backend="uv-lock",
            operation="resolve",
            message=f"Manifest requires-python value is invalid: {raw!r}",
        ) from exc
    if not specifier.contains(version, prereleases=True):
        raise ResolutionFailed(
            backend="uv-lock",
            operation="resolve",
            message=f"Selected Python {version} does not satisfy requires-python {raw!r}",
        )


def _validate_intent(intent: DependencyIntent) -> None:
    if intent.has_errors:
        raise ResolutionFailed(
            backend="uv-lock",
            operation="resolve",
            message="Manifest contains unsupported or invalid entries",
            diagnostics=intent.diagnostics,
        )
    foreign = sorted(
        {item.ecosystem for item in (*intent.requirements, *intent.constraints)} - {"pypi"}
    )
    if foreign:
        raise ResolutionFailed(
            backend="uv-lock",
            operation="resolve",
            message=f"uv resolver cannot resolve ecosystems: {', '.join(foreign)}",
        )
    if not intent.requirements:
        raise ResolutionFailed(
            backend="uv-lock",
            operation="resolve",
            message="Python resolution requires at least one package requirement",
        )
    for requirement in (*intent.requirements, *intent.constraints):
        if requirement.source and requirement.source != "pypi":
            _validate_direct_requirement(requirement)


def _validate_direct_requirement(requirement: Requirement) -> None:
    assert requirement.source is not None
    parsed = urlsplit(requirement.source)
    if parsed.scheme not in {"https", "file"}:
        raise ResolutionFailed(
            backend="uv-lock",
            operation="resolve",
            message=f"Direct requirement {requirement.name} must use an HTTPS or file URL",
        )
    if parsed.username is not None or parsed.password is not None or parsed.query:
        raise ResolutionFailed(
            backend="uv-lock",
            operation="resolve",
            message=f"Direct requirement {requirement.name} contains credentials or query parameters",
        )
    if (
        not parsed.fragment.startswith("sha256=")
        or len(parsed.fragment.removeprefix("sha256=")) != 64
    ):
        raise ResolutionFailed(
            backend="uv-lock",
            operation="resolve",
            message=f"Direct requirement {requirement.name} must include an exact #sha256= fragment",
        )


def _direct_artifacts(intent: DependencyIntent) -> dict[str, tuple[str, str]]:
    artifacts: dict[str, tuple[str, str]] = {}
    for requirement in intent.requirements:
        if requirement.source is None or requirement.source == "pypi":
            continue
        parsed = urlsplit(requirement.source)
        digest = parsed.fragment.removeprefix("sha256=").lower()
        artifact = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
        artifacts[requirement.name] = (artifact, f"sha256:{digest}")
    return artifacts


def _project_document(intent: DependencyIntent, major: int, minor: int) -> str:
    dependencies = [_requirement_text(item) for item in intent.requirements]
    constraints = [_requirement_text(item) for item in intent.constraints]
    lines = [
        "[project]",
        'name = "depviz-resolution"',
        'version = "0"',
        f'requires-python = "=={major}.{minor}.*"',
        f"dependencies = {json.dumps(dependencies, ensure_ascii=False)}",
    ]
    if constraints:
        lines.extend(
            [
                "",
                "[tool.uv]",
                f"constraint-dependencies = {json.dumps(constraints, ensure_ascii=False)}",
            ]
        )
    return "\n".join(lines) + "\n"


def _requirement_text(requirement: Requirement) -> str:
    extras = f"[{','.join(requirement.extras)}]" if requirement.extras else ""
    if requirement.source and requirement.source != "pypi":
        base = f"{requirement.name}{extras} @ {requirement.source}"
    else:
        base = f"{requirement.name}{extras}{requirement.specifier or ''}"
    if requirement.marker:
        base += f" ; {requirement.marker}"
    return base


def _lock_command(
    executable: str,
    interpreter: str,
    intent: DependencyIntent,
    *,
    offline: bool,
) -> tuple[str, ...]:
    arguments = [
        executable,
        "lock",
        "--python",
        interpreter,
        "--no-config",
        "--no-progress",
        "--no-python-downloads",
    ]
    if offline:
        arguments.append("--offline")
    indexes = [item for item in intent.indexes if item]
    if indexes:
        arguments.extend(["--default-index", indexes[0]])
        for index in indexes[1:]:
            arguments.extend(["--index", index])
    return tuple(arguments)


def _packages_from_uv_lock(
    lock: dict[str, object],
    platform_id: str,
    direct_artifacts: dict[str, tuple[str, str]],
) -> tuple[ResolvedPackage, ...]:
    raw_packages = lock.get("package")
    if not isinstance(raw_packages, list):
        raise ValueError("uv.lock package section must be a list")
    tag_order = {tag: index for index, tag in enumerate(sys_tags())}
    packages: list[ResolvedPackage] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_packages):
        if not isinstance(raw, dict):
            raise ValueError(f"uv.lock package {index} must be an object")
        source = raw.get("source")
        if isinstance(source, dict) and "virtual" in source:
            continue
        name = _string(raw.get("name"), f"package {index} name")
        version = _string(raw.get("version"), f"package {name} version")
        normalized = name.lower().replace("_", "-")
        if normalized in seen:
            raise ValueError(
                f"uv resolution selected more than one version of {normalized}; "
                "forked universal locks are not supported for one concrete target"
            )
        seen.add(normalized)
        direct = direct_artifacts.get(normalized)
        if direct is None:
            package_source = _registry_source(source, name)
            wheel = _select_wheel(raw.get("wheels"), name, tag_order)
        else:
            package_source = direct[0]
            wheel = _select_wheel(
                raw.get("wheels"),
                name,
                tag_order,
                artifact_override=direct[0],
            )
            if wheel[1] != direct[1]:
                raise ValueError(
                    f"Package {name} direct URL hash does not match uv solver metadata"
                )
        dependencies = _dependencies(raw.get("dependencies", []), name)
        packages.append(
            ResolvedPackage(
                ecosystem="pypi",
                name=name,
                version=version,
                source=package_source,
                artifact=wheel[0],
                checksum=wheel[1],
                platform=platform_id,
                dependencies=dependencies,
            )
        )
    return tuple(sorted(packages, key=lambda package: package.name))


def _select_wheel(
    value: object,
    package: str,
    tag_order: dict[Tag, int],
    *,
    artifact_override: str | None = None,
) -> tuple[str, str]:
    if not isinstance(value, list) or not value:
        raise ValueError(
            f"Package {package} has no wheel artifact; source distributions are not supported yet"
        )
    candidates: list[tuple[int, str, str]] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError(f"Package {package} wheel entry must be an object")
        raw_url = raw.get("url")
        if artifact_override is not None:
            url = artifact_override
            raw_filename = raw.get("filename")
            filename = (
                _string(raw_filename, f"package {package} wheel filename")
                if raw_filename is not None
                else Path(urlsplit(url).path).name
            )
            if filename != Path(urlsplit(url).path).name:
                raise ValueError(
                    f"Package {package} direct wheel filename does not match solver metadata"
                )
        else:
            url = _safe_artifact_url(_string(raw_url, f"package {package} wheel URL"), package)
            filename = Path(urlsplit(url).path).name
        digest = _wheel_hash(raw, package)
        try:
            _name, _version, _build, tags = parse_wheel_filename(filename)
        except InvalidWheelFilename as exc:
            raise ValueError(f"Package {package} has invalid wheel filename {filename!r}") from exc
        ranks = [tag_order[tag] for tag in tags if tag in tag_order]
        if ranks:
            candidates.append((min(ranks), url, digest))
    if not candidates:
        raise ValueError(f"Package {package} has no wheel compatible with the selected interpreter")
    _rank, url, digest = min(candidates, key=lambda item: (item[0], item[1]))
    return url, digest


def _wheel_hash(value: dict[object, object], package: str) -> str:
    raw = value.get("hash")
    if isinstance(raw, str):
        checksum = raw
    else:
        hashes = value.get("hashes")
        checksum = ""
        if isinstance(hashes, dict):
            sha = hashes.get("sha256")
            if isinstance(sha, str):
                checksum = f"sha256:{sha}"
    if not checksum.startswith("sha256:") or len(checksum) != 71:
        raise ValueError(f"Package {package} wheel lacks a valid SHA-256 hash")
    digest = checksum.removeprefix("sha256:").lower()
    if any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"Package {package} wheel has an invalid SHA-256 hash")
    return f"sha256:{digest}"


def _registry_source(value: object, package: str) -> str:
    if not isinstance(value, dict):
        raise ValueError(f"Package {package} source must be an object")
    registry = value.get("registry")
    if not isinstance(registry, str) or not registry:
        raise ValueError(f"Package {package} is not resolved from a supported package registry")
    return _safe_artifact_url(registry, package, allow_simple=True)


def _safe_artifact_url(url: str, package: str, *, allow_simple: bool = False) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "file"}:
        raise ValueError(f"Package {package} artifact/source URL has unsupported scheme")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"Package {package} artifact/source URL contains embedded credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(f"Package {package} artifact/source URL contains query or fragment data")
    if not allow_simple and not Path(parsed.path).name:
        raise ValueError(f"Package {package} artifact URL has no filename")
    return url


def _dependencies(value: object, package: str) -> tuple[PackageReference, ...]:
    if not isinstance(value, list):
        raise ValueError(f"Package {package} dependencies must be a list")
    dependencies: list[PackageReference] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError(f"Package {package} dependency must be an object")
        name = _string(raw.get("name"), f"package {package} dependency name")
        marker = raw.get("marker")
        if marker is not None and not isinstance(marker, str):
            raise ValueError(f"Package {package} dependency marker must be a string")
        dependencies.append(PackageReference("pypi", name, marker=marker))
    return tuple(dependencies)


def _json_safe(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _resolution_error(message: str) -> ResolutionFailed:
    return ResolutionFailed(backend="uv-lock", operation="resolve", message=message)


def _as_resolution_failed(error: BackendError) -> ResolutionFailed:
    return ResolutionFailed(
        backend=error.backend,
        operation=error.operation,
        message=error.message,
        diagnostics=error.diagnostics,
    )
