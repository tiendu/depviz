from __future__ import annotations

import base64
import csv
import hashlib
import json
import sys
import sysconfig
import tomllib
import venv
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from packaging.requirements import Requirement as PackagingRequirement

from depviz.api import (
    Command,
    CommandResult,
    DependencyIntent,
    EnvironmentTarget,
    OperationContext,
    Requirement,
    Target,
    VerificationPolicy,
)
from depviz.api.errors import LockFailed, ResolutionFailed
from depviz.builtin.python import create_plugin
from depviz.builtin.python.locking import PythonLockProvider
from depviz.builtin.python.resolver import UvResolver
from depviz.infrastructure.commands import LocalCommandRunner
from depviz.testing import BackendConformanceCase, run_backend_conformance_suite


@dataclass
class ControlledUvRunner:
    calls: list[Command] = field(default_factory=list)
    local: LocalCommandRunner = field(default_factory=LocalCommandRunner)

    def run(
        self,
        command: Command,
        *,
        timeout_seconds: float,
        output_limit: int,
        redact: tuple[str, ...] = (),
    ) -> CommandResult:
        self.calls.append(command)
        if command.argv[:2] == ("uv", "--version"):
            return _result(command, stdout="uv 0.10.0\n")
        if command.argv[:2] == ("uv", "lock"):
            assert command.cwd is not None
            _write_uv_lock(command.cwd)
            return _result(command, stderr="Resolved 2 packages\n")
        if command.argv[:2] == ("uv", "venv"):
            target = Path(command.argv[-1])
            venv.EnvBuilder(with_pip=False, symlinks=True, clear=False).create(target)
            return _result(command)
        if command.argv[:3] == ("uv", "pip", "sync"):
            requirements = Path(command.argv[3])
            interpreter = Path(command.argv[command.argv.index("--python") + 1])
            _install_exact_wheel(requirements, interpreter)
            return _result(command)
        return self.local.run(
            command,
            timeout_seconds=timeout_seconds,
            output_limit=output_limit,
            redact=redact,
        )


def _result(
    command: Command,
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> CommandResult:
    return CommandResult(
        argv=command.argv,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0.01,
    )


def _build_wheel(root: Path, *, version: str = "1.0.0") -> tuple[Path, str]:
    build = root / "build"
    package = build / "demopkg"
    metadata = build / f"demopkg-{version}.dist-info"
    package.mkdir(parents=True)
    metadata.mkdir()
    (package / "__init__.py").write_text(f"__version__ = {version!r}\n", encoding="utf-8")
    (metadata / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: demopkg\nVersion: {version}\n",
        encoding="utf-8",
    )
    (metadata / "WHEEL").write_text(
        "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        encoding="utf-8",
    )
    rows: list[tuple[str, str, str]] = []
    for path in sorted(build.rglob("*")):
        if not path.is_file():
            continue
        digest = base64.urlsafe_b64encode(hashlib.sha256(path.read_bytes()).digest())
        rows.append(
            (
                path.relative_to(build).as_posix(),
                f"sha256={digest.decode('ascii').rstrip('=')}",
                str(path.stat().st_size),
            )
        )
    rows.append((f"demopkg-{version}.dist-info/RECORD", "", ""))
    with (metadata / "RECORD").open("w", newline="", encoding="utf-8") as stream:
        csv.writer(stream).writerows(rows)

    wheel = root / f"demopkg-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(build.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(build).as_posix())
    checksum = hashlib.sha256(wheel.read_bytes()).hexdigest()
    return wheel, checksum


def _write_uv_lock(project: Path) -> None:
    document = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = document["project"]["dependencies"]
    assert isinstance(dependencies, list) and len(dependencies) == 1
    requirement = PackagingRequirement(str(dependencies[0]))
    assert requirement.url is not None
    parsed = urlsplit(requirement.url)
    wheel = Path(unquote(parsed.path))
    checksum = parsed.fragment.removeprefix("sha256=")
    content = f"""version = 1
revision = 3
requires-python = ">=3.11"

[[package]]
name = "demopkg"
version = "1.0.0"
source = {{ path = {json.dumps(wheel.name)} }}
wheels = [
  {{ filename = {json.dumps(wheel.name)}, hash = "sha256:{checksum}" }},
]

[[package]]
name = "depviz-resolution"
version = "0"
source = {{ virtual = "." }}
dependencies = [{{ name = "demopkg" }}]
"""
    (project / "uv.lock").write_text(content, encoding="utf-8")


def _install_exact_wheel(requirements: Path, interpreter: Path) -> None:
    line = requirements.read_text(encoding="utf-8").strip()
    requirement_text, hash_text = line.rsplit(" --hash=sha256:", 1)
    requirement = PackagingRequirement(requirement_text)
    assert requirement.url is not None
    parsed = urlsplit(requirement.url)
    wheel = Path(unquote(parsed.path))
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == hash_text
    prefix = interpreter.parent.parent
    site_packages = (
        prefix / "Lib/site-packages"
        if sys.platform == "win32"
        else prefix
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    site_packages.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(site_packages)
    metadata = next(site_packages.glob("demopkg-*.dist-info"))
    (metadata / "INSTALLER").write_text("uv\n", encoding="utf-8")
    (metadata / "direct_url.json").write_text(
        json.dumps({"url": wheel.as_uri(), "archive_info": {}}),
        encoding="utf-8",
    )


def _context(runner: ControlledUvRunner) -> OperationContext:
    return OperationContext(
        command_runner=runner,
        configuration={
            "python.uv_executable": "uv",
            "python.interpreter": sys.executable,
            "python.timeout_seconds": "30",
            "python.output_limit": "1000000",
        },
    )


def test_python_backend_full_conformance_and_file_drift_detection(tmp_path: Path) -> None:
    wheel, checksum = _build_wheel(tmp_path / "wheel")
    source = f"{wheel.as_uri()}#sha256={checksum}"
    runner = ControlledUvRunner()

    def tamper(candidate) -> None:  # type: ignore[no-untyped-def]
        module = next(candidate.path.rglob("site-packages/demopkg/__init__.py"))
        module.write_text("__version__ = 'tampered'\n", encoding="utf-8")

    result = run_backend_conformance_suite(
        BackendConformanceCase(
            plugin=create_plugin(),
            resolver="uv-lock",
            lock_provider="python-exact-lock",
            environment_driver="python-venv-driver",
            verifier="python-venv-verifier",
            intent=DependencyIntent(requirements=(Requirement("pypi", "demopkg", source=source),)),
            target=Target(platform="python-host"),
            deployment=EnvironmentTarget(tmp_path / "deployment", "managed-python-deployment"),
            context=_context(runner),
            policy=VerificationPolicy(load_packages=("demopkg",)),
            tamper=tamper,
        ),
        work_directory=tmp_path,
    )

    assert result.drift_detected is True
    assert sum(command.argv[:2] == ("uv", "lock") for command in runner.calls) == 1
    sync_commands = [
        command for command in runner.calls if command.argv[:3] == ("uv", "pip", "sync")
    ]
    assert len(sync_commands) == 3
    assert all(
        "--no-index" in command.argv and "--require-hashes" in command.argv
        for command in sync_commands
    )


def test_uv_resolution_rejects_incompatible_requires_python(tmp_path: Path) -> None:
    wheel, checksum = _build_wheel(tmp_path / "wheel")
    runner = ControlledUvRunner()
    intent = DependencyIntent(
        requirements=(
            Requirement("pypi", "demopkg", source=f"{wheel.as_uri()}#sha256={checksum}"),
        ),
        metadata={"requires-python": "<3"},
    )

    with pytest.raises(ResolutionFailed, match="does not satisfy requires-python"):
        UvResolver().resolve(intent, Target("python-host"), None, _context(runner))

    assert not any(command.argv[:2] == ("uv", "lock") for command in runner.calls)


def test_python_lock_rejects_tampering(tmp_path: Path) -> None:
    wheel, checksum = _build_wheel(tmp_path / "wheel")
    runner = ControlledUvRunner()
    intent = DependencyIntent(
        requirements=(Requirement("pypi", "demopkg", source=f"{wheel.as_uri()}#sha256={checksum}"),)
    )
    resolution = UvResolver().resolve(intent, Target("python-host"), None, _context(runner))
    provider = PythonLockProvider()
    artifact = provider.create_lock(resolution, _context(runner))
    lock_path = tmp_path / "python-lock.json"
    lock_path.write_bytes(artifact.content)
    document = json.loads(lock_path.read_text(encoding="utf-8"))
    document["artifacts"][0]["checksum"] = f"sha256:{'0' * 64}"
    lock_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(LockFailed, match="lock_id"):
        provider.read_lock(lock_path, _context(runner))


def test_python_target_contains_interpreter_abi(tmp_path: Path) -> None:
    wheel, checksum = _build_wheel(tmp_path / "wheel")
    resolution = UvResolver().resolve(
        DependencyIntent(
            requirements=(
                Requirement("pypi", "demopkg", source=f"{wheel.as_uri()}#sha256={checksum}"),
            )
        ),
        Target("python-host"),
        None,
        _context(ControlledUvRunner()),
    )

    assert resolution.target.python_version is not None
    assert resolution.target.implementation == sys.implementation.name
    assert sysconfig.get_platform() in resolution.target.platform
    assert resolution.packages[0].artifact == wheel.as_uri()
    assert resolution.packages[0].checksum == f"sha256:{checksum}"


def test_python_cli_lifecycle_uses_generic_commands(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    from depviz.cli.commands import run_command
    from depviz.cli.exit_codes import ExitCode
    from depviz.cli.parser import parse_args
    from depviz.cli.services import ApplicationServices
    from depviz.plugins.defaults import create_default_registry

    wheel, checksum = _build_wheel(tmp_path / "wheel")
    manifest = tmp_path / "requirements.in"
    manifest.write_text(
        f"demopkg @ {wheel.as_uri()}#sha256={checksum}\n",
        encoding="utf-8",
    )
    runner = ControlledUvRunner()
    services = ApplicationServices(
        registry=create_default_registry(discover_external=False),
        command_runner=runner,
    )
    resolution = tmp_path / "resolution.json"
    lock = tmp_path / "lock.json"
    deployment = tmp_path / "deployment"
    runtime = ["--python", sys.executable, "--uv-executable", "uv"]

    assert (
        run_command(
            parse_args(
                [
                    "resolve",
                    str(manifest),
                    "--resolver",
                    "uv-lock",
                    *runtime,
                    "--output",
                    str(resolution),
                    "--json",
                ]
            ),
            services,
        )
        == ExitCode.OK
    )
    capsys.readouterr()
    assert (
        run_command(
            parse_args(
                [
                    "lock",
                    str(resolution),
                    "--provider",
                    "python-exact-lock",
                    "--output",
                    str(lock),
                ]
            ),
            services,
        )
        == ExitCode.OK
    )
    capsys.readouterr()
    assert (
        run_command(
            parse_args(
                [
                    "apply",
                    str(lock),
                    "--provider",
                    "python-exact-lock",
                    "--driver",
                    "python-venv-driver",
                    "--deployment",
                    str(deployment),
                    *runtime,
                    "--json",
                ]
            ),
            services,
        )
        == ExitCode.OK
    )
    applied = json.loads(capsys.readouterr().out)
    candidate = applied["candidate_id"]
    assert applied["environment_kind"] == "python-venv"

    assert (
        run_command(
            parse_args(
                [
                    "verify",
                    str(lock),
                    "--provider",
                    "python-exact-lock",
                    "--verifier",
                    "python-venv-verifier",
                    "--deployment",
                    str(deployment),
                    "--candidate",
                    candidate,
                    "--probe-import",
                    "demopkg",
                    *runtime,
                    "--json",
                ]
            ),
            services,
        )
        == ExitCode.OK
    )
    verification = json.loads(capsys.readouterr().out)
    assert verification["passed"] is True
    assert verification["expected_state_digest"] == verification["observed_state_digest"]

    assert (
        run_command(
            parse_args(
                [
                    "promote",
                    "--provider",
                    "python-exact-lock",
                    "--verifier",
                    "python-venv-verifier",
                    "--deployment",
                    str(deployment),
                    "--candidate",
                    candidate,
                    "--probe-import",
                    "demopkg",
                    *runtime,
                    "--json",
                ]
            ),
            services,
        )
        == ExitCode.OK
    )
    promoted = json.loads(capsys.readouterr().out)
    assert promoted["current_candidate_id"] == candidate

    assert (
        run_command(
            parse_args(
                [
                    "status",
                    "--deployment",
                    str(deployment),
                    "--deployment-kind",
                    "managed-python-deployment",
                    "--json",
                ]
            ),
            services,
        )
        == ExitCode.OK
    )
    status = json.loads(capsys.readouterr().out)
    assert status["current_candidate_id"] == candidate
