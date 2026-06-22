from __future__ import annotations

import tempfile
import tomllib
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from depviz.api import (
    BackendPayload,
    Command,
    DependencyIntent,
    Diagnostic,
    EnvironmentState,
    OperationContext,
    Requirement,
    Resolution,
    ResolutionStatus,
    ResolvedPackage,
    Severity,
    Target,
    normalize_package_name,
)
from depviz.api.errors import BackendError, ResolutionFailed, ToolUnavailable
from depviz.builtin.conda.resolver import CondaDryRunResolver
from depviz.builtin.python.tooling import (
    isolated_uv_environment,
    read_uv_version,
    runner_for,
    uv_environment_to_remove,
    uv_settings,
)
from depviz.core.resolution import resolution_to_dict
from depviz.infrastructure.redaction import credential_secrets, redact_text, sanitize_json

_PLATFORM_MAP = {
    "osx-arm64": "aarch64-apple-darwin",
    "osx-64": "x86_64-apple-darwin",
    "linux-64": "x86_64-unknown-linux-gnu",
    "linux-aarch64": "aarch64-unknown-linux-gnu",
    "win-64": "x86_64-pc-windows-msvc",
}


class CondaPipResolver:
    """Resolve one Conda prefix with an exact pip wheel overlay."""

    name = "conda-pip"

    def resolve(
        self,
        intent: DependencyIntent,
        target: Target,
        current: EnvironmentState | None,
        context: OperationContext,
    ) -> Resolution:
        del current
        _validate_mixed_intent(intent)
        conda_intent, python_intent = _split_intent(intent)

        conda_resolution = CondaDryRunResolver().resolve(
            conda_intent,
            target,
            current=None,
            context=context,
        )
        python_version = _resolved_python_version(conda_resolution)
        if not any(package.name == "pip" for package in conda_resolution.packages):
            raise ResolutionFailed(
                backend=self.name,
                operation="resolve",
                message=(
                    "The mixed environment contains a pip section, but the Conda solve does not "
                    "contain pip. Add 'pip' to the Conda dependencies so the overlay has an "
                    "explicit installer runtime."
                ),
            )

        python_resolution = _resolve_python_overlay(
            python_intent,
            conda_platform=target.platform,
            python_version=python_version,
            context=context,
        )
        direct_conda = {
            normalize_package_name("pypi", requirement.name)
            for requirement in conda_intent.requirements
        }
        direct_pypi = {
            normalize_package_name("pypi", requirement.name)
            for requirement in python_intent.requirements
        }
        direct_collisions = sorted(direct_conda & direct_pypi)
        if direct_collisions:
            raise ResolutionFailed(
                backend=self.name,
                operation="resolve",
                message=(
                    "Packages are requested directly through both Conda and pip: "
                    + ", ".join(direct_collisions)
                    + ". Choose one owner for each directly requested package."
                ),
            )

        conda_names = {
            normalize_package_name("pypi", package.name) for package in conda_resolution.packages
        }
        python_names = {package.name for package in python_resolution.packages}
        overlay_collisions = tuple(sorted(conda_names & python_names))
        diagnostics: list[Diagnostic] = [
            Diagnostic(
                code="resolver.conda-pip.complete",
                message=(
                    f"Resolved {len(conda_resolution.packages)} Conda artifacts and "
                    f"{len(python_resolution.packages)} exact Python wheels for one prefix"
                ),
                severity=Severity.INFO,
            )
        ]
        if overlay_collisions:
            diagnostics.append(
                Diagnostic(
                    code="resolver.conda-pip.overlay-collisions",
                    message=(
                        "pip will be installed after Conda and will own these Python "
                        "distributions in the final prefix: " + ", ".join(overlay_collisions)
                    ),
                    severity=Severity.WARNING,
                    hint=(
                        "Move overlapping packages to one layer when possible. The combined lock "
                        "records pip as the final owner and verification checks the wheel files."
                    ),
                )
            )

        packages = tuple(
            sorted(
                (*conda_resolution.packages, *python_resolution.packages),
                key=lambda package: (package.ecosystem, package.name),
            )
        )
        payload = BackendPayload(
            schema="depviz.conda-pip.resolution.v1",
            data={
                "tool": "conda+uv",
                "tool_version": _combined_tool_version(conda_resolution, python_resolution),
                "conda_resolution": resolution_to_dict(conda_resolution),
                "python_resolution": resolution_to_dict(python_resolution),
                "python_runtime": {
                    "version": python_version,
                    "implementation": "cpython",
                    "conda_platform": target.platform,
                    "uv_platform": _uv_platform(target.platform),
                },
                "ownership": {
                    "pip_overrides": list(overlay_collisions),
                    "policy": "pip-last",
                },
            },
        )
        return Resolution(
            requested=intent.requirements,
            packages=packages,
            target=target,
            status=ResolutionStatus.COMPLETE,
            diagnostics=(
                *conda_resolution.diagnostics,
                *python_resolution.diagnostics,
                *diagnostics,
            ),
            native_payload=payload,
        )


def _validate_mixed_intent(intent: DependencyIntent) -> None:
    if intent.has_errors:
        raise ResolutionFailed(
            backend="conda-pip",
            operation="resolve",
            message="Manifest contains errors and cannot be resolved",
            diagnostics=intent.diagnostics,
        )
    ecosystems = {requirement.ecosystem for requirement in intent.requirements}
    unsupported = sorted(ecosystems - {"conda", "pypi"})
    if unsupported:
        raise ResolutionFailed(
            backend="conda-pip",
            operation="resolve",
            message="Unsupported ecosystems in mixed environment: " + ", ".join(unsupported),
        )
    if "conda" not in ecosystems or "pypi" not in ecosystems:
        raise ResolutionFailed(
            backend="conda-pip",
            operation="resolve",
            message="The conda-pip resolver requires both Conda and pip requirements",
        )
    conda_constraints = [item for item in intent.constraints if item.ecosystem == "conda"]
    if conda_constraints:
        raise ResolutionFailed(
            backend="conda-pip",
            operation="resolve",
            message="Separate Conda constraint files are not supported",
        )


def _split_intent(intent: DependencyIntent) -> tuple[DependencyIntent, DependencyIntent]:
    conda_requirements = tuple(
        requirement for requirement in intent.requirements if requirement.ecosystem == "conda"
    )
    python_requirements = tuple(
        requirement for requirement in intent.requirements if requirement.ecosystem == "pypi"
    )
    python_constraints = tuple(
        requirement for requirement in intent.constraints if requirement.ecosystem == "pypi"
    )
    conda_intent = replace(
        intent,
        requirements=conda_requirements,
        constraints=(),
        indexes=(),
    )
    python_intent = replace(
        intent,
        requirements=python_requirements,
        constraints=python_constraints,
        channels=(),
    )
    return conda_intent, python_intent


def _resolved_python_version(resolution: Resolution) -> str:
    packages = [package for package in resolution.packages if package.name == "python"]
    if len(packages) != 1:
        raise ResolutionFailed(
            backend="conda-pip",
            operation="resolve",
            message="The Conda solve must contain exactly one Python runtime",
        )
    version = packages[0].version
    parts = version.split(".")
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise ResolutionFailed(
            backend="conda-pip",
            operation="resolve",
            message=f"Cannot derive a Python compatibility target from Conda version {version!r}",
        )
    return version


def _resolve_python_overlay(
    intent: DependencyIntent,
    *,
    conda_platform: str,
    python_version: str,
    context: OperationContext,
) -> Resolution:
    platform = _uv_platform(conda_platform)
    settings = uv_settings(
        context,
        error=lambda message: ResolutionFailed(
            backend="conda-pip", operation="resolve Python overlay", message=message
        ),
    )
    runner = runner_for(context)
    secrets = credential_secrets(intent.indexes)
    try:
        uv_version = read_uv_version(
            runner=runner,
            settings=settings,
            backend="conda-pip",
            operation="resolve Python overlay",
        )
    except ToolUnavailable:
        raise
    except BackendError as exc:
        raise ResolutionFailed(
            backend="conda-pip",
            operation="resolve Python overlay",
            message=exc.message,
            diagnostics=exc.diagnostics,
        ) from exc

    with tempfile.TemporaryDirectory(prefix="depviz-conda-pip-resolve-") as temporary_directory:
        root = Path(temporary_directory)
        requirements_path = root / "requirements.in"
        requirements_path.write_text(
            "\n".join(_requirement_text(item) for item in intent.requirements) + "\n",
            encoding="utf-8",
        )
        constraints_path: Path | None = None
        if intent.constraints:
            constraints_path = root / "constraints.txt"
            constraints_path.write_text(
                "\n".join(_requirement_text(item) for item in intent.constraints) + "\n",
                encoding="utf-8",
            )
        output_path = root / "pylock.toml"
        cache_path = root / "uv-cache"
        command = Command(
            argv=_compile_command(
                settings.executable,
                requirements_path,
                output_path,
                constraints_path=constraints_path,
                python_version=python_version,
                python_platform=platform,
                indexes=intent.indexes,
                offline=context.offline,
            ),
            cwd=root,
            environment=isolated_uv_environment(cache_dir=cache_path),
            remove_environment=uv_environment_to_remove(),
        )
        try:
            result = runner.run(
                command,
                timeout_seconds=settings.timeout_seconds,
                output_limit=settings.output_limit,
                redact=(*secrets, str(root)),
            )
        except FileNotFoundError as exc:
            raise ToolUnavailable(
                backend="conda-pip",
                operation="resolve Python overlay",
                message=f"Executable not found: {settings.executable}",
            ) from exc
        except OSError as exc:
            raise ResolutionFailed(
                backend="conda-pip",
                operation="resolve Python overlay",
                message=f"Cannot execute {settings.executable!r}: {exc}",
            ) from exc
        if result.timed_out:
            raise ResolutionFailed(
                backend="conda-pip",
                operation="resolve Python overlay",
                message=f"uv resolution timed out after {settings.timeout_seconds:g} seconds",
            )
        if result.output_truncated:
            raise ResolutionFailed(
                backend="conda-pip",
                operation="resolve Python overlay",
                message="uv resolver output exceeded the configured limit",
            )
        if result.returncode != 0:
            detail = redact_text(result.stderr.strip() or result.stdout.strip(), secrets)
            raise ResolutionFailed(
                backend="conda-pip",
                operation="resolve Python overlay",
                message=detail or "uv pip compile failed",
            )
        try:
            raw_pylock = output_path.read_bytes()
            pylock = tomllib.loads(raw_pylock.decode("utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ResolutionFailed(
                backend="conda-pip",
                operation="normalize Python overlay",
                message=f"Cannot read uv pylock output: {exc}",
            ) from exc

    target_id = f"python-cpython-{python_version}-conda-{conda_platform}"
    try:
        packages = _packages_from_pylock(pylock, target_id, intent.indexes)
    except ValueError as exc:
        raise ResolutionFailed(
            backend="conda-pip",
            operation="normalize Python overlay",
            message=str(exc),
        ) from exc
    if not packages:
        raise ResolutionFailed(
            backend="conda-pip",
            operation="normalize Python overlay",
            message="uv returned no Python wheel packages",
        )
    major, minor = _major_minor(python_version)
    target = Target(
        platform=target_id,
        python_version=python_version,
        implementation="cpython",
    )
    payload = BackendPayload(
        schema="depviz.uv-lock.native.v1",
        data={
            "tool": "uv",
            "tool_version": uv_version,
            "interpreter": {
                "implementation": "cpython",
                "version": python_version,
                "major": major,
                "minor": minor,
                "platform": conda_platform,
                "soabi": "",
            },
            "uv_pylock": sanitize_json(_json_safe(pylock), secrets),
            "resolution_mode": "targeted-pylock",
        },
    )
    return Resolution(
        requested=intent.requirements,
        packages=packages,
        target=target,
        status=ResolutionStatus.COMPLETE,
        diagnostics=(
            Diagnostic(
                code="resolver.python.targeted-wheel-set",
                message=(
                    f"uv selected {len(packages)} exact wheels for Python {python_version} "
                    f"on {conda_platform}"
                ),
                severity=Severity.INFO,
            ),
        ),
        native_payload=payload,
    )


def _compile_command(
    executable: str,
    requirements: Path,
    output: Path,
    *,
    constraints_path: Path | None,
    python_version: str,
    python_platform: str,
    indexes: tuple[str, ...],
    offline: bool,
) -> tuple[str, ...]:
    arguments = [
        executable,
        "pip",
        "compile",
        str(requirements),
        "--format",
        "pylock.toml",
        "--output-file",
        str(output),
        "--generate-hashes",
        "--only-binary",
        ":all:",
        "--python-version",
        python_version,
        "--python-platform",
        python_platform,
        "--no-header",
        "--no-annotate",
        "--no-config",
        "--no-progress",
        "--no-python-downloads",
    ]
    if constraints_path is not None:
        arguments.extend(["--constraints", str(constraints_path)])
    if offline:
        arguments.append("--offline")
    if indexes:
        arguments.extend(["--default-index", indexes[0]])
        for index in indexes[1:]:
            arguments.extend(["--index", index])
    return tuple(arguments)


def _packages_from_pylock(
    value: dict[str, object],
    target_id: str,
    indexes: tuple[str, ...],
) -> tuple[ResolvedPackage, ...]:
    raw_packages = value.get("packages")
    if not isinstance(raw_packages, list):
        raise ValueError("pylock.toml packages section must be a list")
    packages: list[ResolvedPackage] = []
    seen: set[str] = set()
    default_source = indexes[0] if indexes else "pypi"
    for index, raw in enumerate(raw_packages):
        if not isinstance(raw, dict):
            raise ValueError(f"pylock package {index} must be an object")
        name = _string(raw.get("name"), f"package {index} name")
        normalized = normalize_package_name("pypi", name)
        if normalized in seen:
            raise ValueError(f"pylock selected more than one version of {normalized}")
        seen.add(normalized)
        version = _string(raw.get("version"), f"package {name} version")
        wheels = raw.get("wheels")
        if not isinstance(wheels, list) or len(wheels) != 1:
            raise ValueError(
                f"Package {name} must resolve to exactly one wheel for the concrete target"
            )
        wheel = wheels[0]
        if not isinstance(wheel, dict):
            raise ValueError(f"Package {name} wheel entry must be an object")
        url = _safe_wheel_url(_string(wheel.get("url"), f"package {name} wheel URL"), name)
        hashes = wheel.get("hashes")
        if not isinstance(hashes, dict):
            raise ValueError(f"Package {name} wheel has no hash object")
        digest = hashes.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"Package {name} wheel lacks a valid SHA-256 hash")
        normalized_digest = digest.lower()
        if any(character not in "0123456789abcdef" for character in normalized_digest):
            raise ValueError(f"Package {name} wheel has an invalid SHA-256 hash")
        packages.append(
            ResolvedPackage(
                ecosystem="pypi",
                name=name,
                version=version,
                source=default_source,
                artifact=url,
                checksum=f"sha256:{normalized_digest}",
                platform=target_id,
            )
        )
    return tuple(sorted(packages, key=lambda package: package.name))


def _requirement_text(requirement: Requirement) -> str:
    extras = f"[{','.join(requirement.extras)}]" if requirement.extras else ""
    if requirement.source and requirement.source != "pypi":
        base = f"{requirement.name}{extras} @ {requirement.source}"
    else:
        base = f"{requirement.name}{extras}{requirement.specifier or ''}"
    if requirement.marker:
        base += f" ; {requirement.marker}"
    return base


def _safe_wheel_url(url: str, package: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "file"}:
        raise ValueError(f"Package {package} wheel URL has unsupported scheme")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"Package {package} wheel URL contains embedded credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(f"Package {package} wheel URL contains query or fragment data")
    if not parsed.path.lower().endswith(".whl"):
        raise ValueError(f"Package {package} artifact is not a wheel")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _uv_platform(conda_platform: str) -> str:
    try:
        return _PLATFORM_MAP[conda_platform]
    except KeyError as exc:
        supported = ", ".join(sorted(_PLATFORM_MAP))
        raise ResolutionFailed(
            backend="conda-pip",
            operation="resolve Python overlay",
            message=(
                f"Conda platform {conda_platform!r} has no safe uv wheel-target mapping; "
                f"supported platforms: {supported}"
            ),
        ) from exc


def _major_minor(version: str) -> tuple[int, int]:
    parts = version.split(".")
    return int(parts[0]), int(parts[1])


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _combined_tool_version(conda: Resolution, python: Resolution) -> str:
    conda_version = _payload_string(conda, "tool_version") or "unknown"
    python_version = _payload_string(python, "tool_version") or "unknown"
    return f"conda-family:{conda_version};uv:{python_version}"


def _payload_string(resolution: Resolution, key: str) -> str | None:
    if resolution.native_payload is None:
        return None
    value = resolution.native_payload.data.get(key)
    return value if isinstance(value, str) else None


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
