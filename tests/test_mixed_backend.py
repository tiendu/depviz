from __future__ import annotations

import base64
import csv
import hashlib
import json
import platform
import sys
import venv
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

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
from depviz.builtin.mixed.driver import CondaPipPrefixDriver
from depviz.builtin.mixed.locking import CondaPipLockProvider
from depviz.builtin.mixed.resolver import CondaPipResolver
from depviz.builtin.mixed.verifier import CondaPipPrefixVerifier
from depviz.core.application import apply_locked_environment
from depviz.core.locking import read_lock, write_lock
from depviz.core.promotion import promote_candidate
from depviz.core.verification import verify_candidate_environment
from depviz.infrastructure.commands import LocalCommandRunner


@dataclass
class ControlledMixedRunner:
    wheel: Path
    wheel_hash: str
    conda_records: tuple[dict[str, object], ...]
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
        argv = command.argv
        if argv[-1:] == ("--version",):
            if "uv" in Path(argv[0]).name:
                return _result(command, stdout="uv 0.10.0\n")
            return _result(command, stdout="micromamba 2.1.0\n")
        if "--dry-run" in argv and "--json" in argv:
            prefix = argv[argv.index("--prefix") + 1]
            records = [dict(record) for record in self.conda_records]
            return _result(
                command,
                stdout=json.dumps(
                    {
                        "success": True,
                        "dry_run": True,
                        "prefix": prefix,
                        "actions": {"FETCH": records, "LINK": records},
                    }
                ),
            )
        if argv[:3] == ("uv", "pip", "compile"):
            output = Path(argv[argv.index("--output-file") + 1])
            output.write_text(
                f"""lock-version = "1.0"
created-by = "uv"
requires-python = ">={sys.version_info.major}.{sys.version_info.minor}"

[[packages]]
name = "demopkg"
version = "1.0.0"
wheels = [{{ url = {json.dumps(self.wheel.as_uri())}, hashes = {{ sha256 = {json.dumps(self.wheel_hash)} }} }}]
""",
                encoding="utf-8",
            )
            return _result(command, stderr="Resolved 1 package\n")
        if "--file" in argv and "--prefix" in argv and "--json" in argv:
            prefix = Path(argv[argv.index("--prefix") + 1])
            venv.EnvBuilder(with_pip=False, symlinks=True).create(prefix)
            metadata = prefix / "conda-meta"
            metadata.mkdir(exist_ok=True)
            for record in self.conda_records:
                filename = f"{record['name']}-{record['version']}-{record['build']}.json"
                (metadata / filename).write_text(json.dumps(record), encoding="utf-8")
            return _result(command, stdout=json.dumps({"success": True}))
        if argv[:3] == ("uv", "pip", "install"):
            requirements = Path(argv[argv.index("--requirement") + 1])
            interpreter = Path(argv[argv.index("--python") + 1])
            _install_exact_wheel(requirements, interpreter)
            return _result(command)
        return self.local.run(
            command,
            timeout_seconds=timeout_seconds,
            output_limit=output_limit,
            redact=redact,
        )


def _result(command: Command, *, stdout: str = "", stderr: str = "") -> CommandResult:
    return CommandResult(
        argv=command.argv,
        returncode=0,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0.01,
    )


def _build_wheel(root: Path) -> tuple[Path, str]:
    build = root / "build"
    package = build / "demopkg"
    metadata = build / "demopkg-1.0.0.dist-info"
    package.mkdir(parents=True)
    metadata.mkdir()
    (package / "__init__.py").write_text("__version__ = '1.0.0'\n", encoding="utf-8")
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: demopkg\nVersion: 1.0.0\n",
        encoding="utf-8",
    )
    (metadata / "WHEEL").write_text(
        "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        encoding="utf-8",
    )
    rows: list[tuple[str, str, str]] = []
    for path in sorted(build.rglob("*")):
        if path.is_file():
            digest = base64.urlsafe_b64encode(hashlib.sha256(path.read_bytes()).digest())
            rows.append(
                (
                    path.relative_to(build).as_posix(),
                    f"sha256={digest.decode('ascii').rstrip('=')}",
                    str(path.stat().st_size),
                )
            )
    rows.append(("demopkg-1.0.0.dist-info/RECORD", "", ""))
    with (metadata / "RECORD").open("w", newline="", encoding="utf-8") as stream:
        csv.writer(stream).writerows(rows)
    wheel = root / "demopkg-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(build.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(build).as_posix())
    return wheel, hashlib.sha256(wheel.read_bytes()).hexdigest()


def _install_exact_wheel(requirements: Path, interpreter: Path) -> None:
    line = requirements.read_text(encoding="utf-8").strip()
    requirement_text, hash_text = line.rsplit(" --hash=sha256:", 1)
    url = requirement_text.split(" @ ", 1)[1]
    wheel = Path(unquote(urlsplit(url).path))
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == hash_text
    prefix = interpreter.parent.parent
    site_packages = (
        prefix
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    site_packages.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(site_packages)
    metadata = site_packages / "demopkg-1.0.0.dist-info"
    (metadata / "INSTALLER").write_text("uv\n", encoding="utf-8")
    (metadata / "direct_url.json").write_text(
        json.dumps({"url": wheel.as_uri(), "archive_info": {"hashes": {"sha256": hash_text}}}),
        encoding="utf-8",
    )


def _records() -> tuple[dict[str, object], ...]:
    version = platform.python_version()
    platform_id = "linux-64"
    return (
        {
            "name": "python",
            "version": version,
            "build": "h123_0_cpython",
            "subdir": platform_id,
            "channel": "https://conda.example/conda-forge",
            "url": f"https://conda.example/conda-forge/{platform_id}/python-{version}-h123_0_cpython.conda",
            "sha256": "1" * 64,
            "depends": [],
        },
        {
            "name": "pip",
            "version": "25.0",
            "build": "pyh123_0",
            "subdir": "noarch",
            "channel": "https://conda.example/conda-forge",
            "url": "https://conda.example/conda-forge/noarch/pip-25.0-pyh123_0.conda",
            "sha256": "2" * 64,
            "depends": [f"python >={sys.version_info.major}.{sys.version_info.minor}"],
        },
    )


def test_mixed_backend_resolves_locks_applies_verifies_and_promotes(tmp_path: Path) -> None:
    wheel, wheel_hash = _build_wheel(tmp_path / "wheel")
    runner = ControlledMixedRunner(wheel, wheel_hash, _records())
    context = OperationContext(
        command_runner=runner,
        configuration={
            "conda.tool": "micromamba",
            "python.uv_executable": "uv",
            "python.interpreter": sys.executable,
            "conda.timeout_seconds": "30",
            "python.timeout_seconds": "30",
        },
    )
    intent = DependencyIntent(
        requirements=(
            Requirement("conda", "python", f"={platform.python_version()}"),
            Requirement("conda", "pip"),
            Requirement("pypi", "demopkg", source=f"{wheel.as_uri()}#sha256={wheel_hash}"),
        ),
        channels=("https://conda.example/conda-forge",),
    )
    resolution = CondaPipResolver().resolve(intent, Target("linux-64"), None, context)
    assert {package.ecosystem for package in resolution.packages} == {"conda", "pypi"}

    provider = CondaPipLockProvider()
    artifact = provider.create_lock(resolution, context)
    lock_path = tmp_path / "mixed-lock.json"
    write_lock(lock_path, artifact)
    locked = read_lock(path=lock_path, provider=provider, context=context)

    deployment = EnvironmentTarget(tmp_path / "deployment", "managed-conda-pip-deployment")
    applied = apply_locked_environment(
        lock=locked,
        driver=CondaPipPrefixDriver(),
        deployment=deployment,
        context=context,
    )
    assert applied.candidate is not None
    report = verify_candidate_environment(
        lock=locked,
        verifier=CondaPipPrefixVerifier(),
        deployment=deployment,
        candidate_id=applied.candidate.candidate_id,
        policy=VerificationPolicy(load_packages=("demopkg",)),
        context=context,
    )
    assert report.passed
    promoted = promote_candidate(
        deployment=deployment,
        candidate_id=applied.candidate.candidate_id,
        provider=provider,
        verifier=CondaPipPrefixVerifier(),
        policy=VerificationPolicy(load_packages=("demopkg",)),
        context=context,
    )
    assert promoted.current_candidate_id == applied.candidate.candidate_id
    assert any(command.argv[:3] == ("uv", "pip", "install") for command in runner.calls)
    install = next(
        command for command in runner.calls if command.argv[:3] == ("uv", "pip", "install")
    )
    assert "--no-deps" in install.argv
    assert "--no-index" in install.argv
    assert "--require-hashes" in install.argv


def test_mixed_resolver_rejects_direct_ownership_collision(tmp_path: Path) -> None:
    wheel, wheel_hash = _build_wheel(tmp_path / "wheel")
    runner = ControlledMixedRunner(wheel, wheel_hash, _records())
    context = OperationContext(
        command_runner=runner,
        configuration={"conda.tool": "micromamba", "python.uv_executable": "uv"},
    )
    intent = DependencyIntent(
        requirements=(
            Requirement("conda", "python", f"={platform.python_version()}"),
            Requirement("conda", "pip"),
            Requirement("conda", "demopkg"),
            Requirement("pypi", "demopkg", source=f"{wheel.as_uri()}#sha256={wheel_hash}"),
        ),
        channels=("https://conda.example/conda-forge",),
    )
    try:
        CondaPipResolver().resolve(intent, Target("linux-64"), None, context)
    except Exception as error:
        assert "both Conda and pip" in str(error)
    else:
        raise AssertionError("Expected direct ownership collision to fail")


def test_cli_auto_selects_mixed_components(tmp_path: Path, capsys: object) -> None:
    from depviz.cli import parse_args
    from depviz.cli.commands import run_command
    from depviz.cli.exit_codes import ExitCode
    from depviz.cli.services import ApplicationServices
    from depviz.plugins.defaults import create_default_registry

    wheel, wheel_hash = _build_wheel(tmp_path / "wheel")
    runner = ControlledMixedRunner(wheel, wheel_hash, _records())
    services = ApplicationServices(
        registry=create_default_registry(discover_external=False),
        command_runner=runner,
    )
    manifest = tmp_path / "environment.yml"
    manifest.write_text(
        f"""channels:
  - https://conda.example/conda-forge
dependencies:
  - python={platform.python_version()}
  - pip
  - pip:
      - "demopkg @ {wheel.as_uri()}#sha256={wheel_hash}"
""",
        encoding="utf-8",
    )
    resolution_path = tmp_path / "resolution.json"
    lock_path = tmp_path / "lock.json"
    deployment = tmp_path / "deployment"

    assert (
        run_command(
            parse_args(
                [
                    "resolve",
                    str(manifest),
                    "--platform",
                    "linux-64",
                    "--output",
                    str(resolution_path),
                    "--json",
                ]
            ),
            services,
        )
        == ExitCode.OK
    )
    capsys.readouterr()  # type: ignore[attr-defined]
    assert (
        run_command(
            parse_args(["lock", str(resolution_path), "--output", str(lock_path)]),
            services,
        )
        == ExitCode.OK
    )
    assert json.loads(lock_path.read_text(encoding="utf-8"))["schema"] == "depviz.conda-pip-lock"
    capsys.readouterr()  # type: ignore[attr-defined]

    assert (
        run_command(
            parse_args(
                [
                    "apply",
                    str(lock_path),
                    "--deployment",
                    str(deployment),
                    "--json",
                ]
            ),
            services,
        )
        == ExitCode.OK
    )
    applied = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    candidate = applied["candidate_id"]
    assert applied["deployment_kind"] == "managed-conda-pip-deployment"

    assert (
        run_command(
            parse_args(
                [
                    "verify",
                    str(lock_path),
                    "--deployment",
                    str(deployment),
                    "--candidate",
                    candidate,
                    "--probe-import",
                    "demopkg",
                    "--json",
                ]
            ),
            services,
        )
        == ExitCode.OK
    )
    verified = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert verified["passed"] is True

    assert (
        run_command(
            parse_args(
                [
                    "promote",
                    "--deployment",
                    str(deployment),
                    "--candidate",
                    candidate,
                    "--probe-import",
                    "demopkg",
                    "--json",
                ]
            ),
            services,
        )
        == ExitCode.OK
    )
    promoted = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert promoted["current_candidate_id"] == candidate
