from __future__ import annotations

import json
import stat
from pathlib import Path

from depviz.api import OperationContext, Resolution, ResolutionStatus, ResolvedPackage, Target
from depviz.builtin.conda import CondaLockProvider
from depviz.cli import parse_args
from depviz.cli.exit_codes import ExitCode
from depviz.core.locking import write_lock
from depviz.core.resolution import host_conda_platform
from depviz.main import run


def _fake_micromamba(path: Path, package: ResolvedPackage) -> None:
    algorithm, digest = (package.checksum or "").split(":", 1)
    path.write_text(
        f'''#!/usr/bin/env python3
import json
import pathlib
import sys

if "--version" in sys.argv:
    print("micromamba 2.1.0")
    raise SystemExit(0)

prefix = pathlib.Path(sys.argv[sys.argv.index("--prefix") + 1])
explicit = pathlib.Path(sys.argv[sys.argv.index("--file") + 1]).read_text()
if "@EXPLICIT" not in explicit:
    print(json.dumps({{"success": False, "error": "missing explicit marker"}}))
    raise SystemExit(1)
metadata = prefix / "conda-meta"
metadata.mkdir(parents=True, exist_ok=True)
(metadata / "{package.name}-{package.version}-{package.build}.json").write_text(json.dumps({{
    "name": "{package.name}",
    "version": "{package.version}",
    "build": "{package.build}",
    "subdir": "{package.platform}",
    "channel": "{package.source}",
    "url": "{package.artifact}",
    "{algorithm}": "{digest}",
    "depends": []
}}))
print(json.dumps({{"success": True}}))
''',
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_phase4_cli_apply_verify_promote_and_status(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    platform = host_conda_platform()
    base = f"https://conda.example/conda-forge/{platform}"
    package = ResolvedPackage(
        ecosystem="conda",
        name="example",
        version="1.0",
        build="h1_0",
        platform=platform,
        source=base,
        artifact=f"{base}/example-1.0-h1_0.conda",
        checksum=f"sha256:{'a' * 64}",
    )
    resolution = Resolution(
        requested=(),
        packages=(package,),
        target=Target(platform),
        status=ResolutionStatus.COMPLETE,
    )
    lock_path = tmp_path / "lock.json"
    artifact = CondaLockProvider().create_lock(resolution, OperationContext())
    write_lock(lock_path, artifact)
    executable = tmp_path / "fake-micromamba"
    _fake_micromamba(executable, package)
    deployment = tmp_path / "deployment"

    apply_exit = run(
        parse_args(
            [
                "apply",
                str(lock_path),
                "--deployment",
                str(deployment),
                "--executable",
                str(executable),
                "--json",
            ]
        )
    )
    assert apply_exit == ExitCode.OK
    apply_output = json.loads(capsys.readouterr().out)
    candidate_id = apply_output["candidate_id"]

    verify_exit = run(
        parse_args(
            [
                "verify",
                str(lock_path),
                "--deployment",
                str(deployment),
                "--candidate",
                candidate_id,
                "--json",
            ]
        )
    )
    assert verify_exit == ExitCode.OK
    verify_output = json.loads(capsys.readouterr().out)
    assert verify_output["passed"] is True

    promote_exit = run(
        parse_args(
            [
                "promote",
                "--deployment",
                str(deployment),
                "--candidate",
                candidate_id,
                "--json",
            ]
        )
    )
    assert promote_exit == ExitCode.OK
    promote_output = json.loads(capsys.readouterr().out)
    assert promote_output["current_candidate_id"] == candidate_id

    status_exit = run(parse_args(["status", "--deployment", str(deployment), "--json"]))
    assert status_exit == ExitCode.OK
    status_output = json.loads(capsys.readouterr().out)
    assert status_output["current_candidate_id"] == candidate_id
