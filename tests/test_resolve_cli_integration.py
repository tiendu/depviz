from __future__ import annotations

import json
import stat
from pathlib import Path

from depviz.cli import parse_args
from depviz.main import ExitCode, run


def test_resolve_cli_executes_configured_solver_and_prints_json(
    tmp_path: Path,
    capsys: object,
) -> None:
    manifest = tmp_path / "environment.yml"
    manifest.write_text(
        "channels:\n  - conda-forge\ndependencies:\n  - python=3.11\n",
        encoding="utf-8",
    )
    executable = tmp_path / "fake-micromamba"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import sys

if "--version" in sys.argv:
    print("2.1.0")
    raise SystemExit(0)

prefix = sys.argv[sys.argv.index("--prefix") + 1]
print(json.dumps({
    "success": True,
    "dry_run": True,
    "prefix": prefix,
    "actions": {
        "LINK": [{
            "name": "python",
            "version": "3.11.9",
            "build": "h123_0_cpython",
            "subdir": "linux-64",
            "channel": "conda-forge",
            "fn": "python-3.11.9-h123_0_cpython.conda",
            "depends": []
        }]
    }
}))
""",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    exit_code = run(
        parse_args(
            [
                "resolve",
                str(manifest),
                "--platform",
                "linux-64",
                "--executable",
                str(executable),
                "--json",
            ]
        )
    )

    assert exit_code == ExitCode.OK
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    document = json.loads(captured.out)
    assert document["status"] == "complete"
    assert document["packages"][0]["name"] == "python"
    assert document["native_payload"]["data"]["transaction"]["prefix"] == "***"
