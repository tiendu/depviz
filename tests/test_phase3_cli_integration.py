from __future__ import annotations

import json
import stat
from pathlib import Path

from depviz.cli import parse_args
from depviz.cli.exit_codes import ExitCode
from depviz.core.planning import read_plan_json
from depviz.main import run


def _fake_micromamba(path: Path) -> None:
    path.write_text(
        f'''#!/usr/bin/env python3
import json
import sys

if "--version" in sys.argv:
    print("2.1.0")
    raise SystemExit(0)

prefix = sys.argv[sys.argv.index("--prefix") + 1]
print(json.dumps({{
    "success": True,
    "dry_run": True,
    "prefix": prefix,
    "actions": {{
        "LINK": [{{
            "name": "python",
            "version": "3.12.3",
            "build": "h123_0",
            "subdir": "linux-64",
            "channel": "https://conda.example/conda-forge/linux-64",
            "url": "https://conda.example/conda-forge/linux-64/python-3.12.3-h123_0.conda",
            "sha256": "{"a" * 64}",
            "depends": []
        }}]
    }}
}}))
''',
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_plan_and_lock_cli_use_exact_backend_artifacts(tmp_path: Path) -> None:
    manifest = tmp_path / "environment.yml"
    manifest.write_text(
        "channels:\n  - https://conda.example/conda-forge\ndependencies:\n  - python=3.12\n",
        encoding="utf-8",
    )
    prefix = tmp_path / "existing"
    metadata = prefix / "conda-meta"
    metadata.mkdir(parents=True)
    (metadata / "python-3.11.9-h122_0.json").write_text(
        json.dumps(
            {
                "name": "python",
                "version": "3.11.9",
                "build": "h122_0",
                "subdir": "linux-64",
                "channel": "https://conda.example/conda-forge/linux-64",
                "url": "https://conda.example/conda-forge/linux-64/python-3.11.9-h122_0.conda",
                "sha256": "b" * 64,
                "depends": [],
            }
        ),
        encoding="utf-8",
    )
    executable = tmp_path / "fake-micromamba"
    _fake_micromamba(executable)

    plan_path = tmp_path / "plan.json"
    plan_exit = run(
        parse_args(
            [
                "plan",
                str(manifest),
                "--prefix",
                str(prefix),
                "--platform",
                "linux-64",
                "--executable",
                str(executable),
                "--output",
                str(plan_path),
            ]
        )
    )

    assert plan_exit == ExitCode.OK
    plan = read_plan_json(plan_path)
    python_change = next(item for item in plan.operations if item.name == "python")
    assert python_change.version_direction.value == "upgrade"
    assert plan.before.environment is not None
    assert plan.after.backend is not None
    assert plan.after.backend.component == "conda-dry-run"

    resolution_path = tmp_path / "resolution.json"
    resolve_exit = run(
        parse_args(
            [
                "resolve",
                str(manifest),
                "--platform",
                "linux-64",
                "--executable",
                str(executable),
                "--output",
                str(resolution_path),
            ]
        )
    )
    assert resolve_exit == ExitCode.OK

    lock_path = tmp_path / "depviz-lock.json"
    lock_exit = run(
        parse_args(
            [
                "lock",
                str(resolution_path),
                "--output",
                str(lock_path),
            ]
        )
    )

    assert lock_exit == ExitCode.OK
    document = json.loads(lock_path.read_text())
    assert document["schema"] == "depviz.conda-lock"
    assert document["artifacts"][0]["checksum"] == f"sha256:{'a' * 64}"
