from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from depviz.infrastructure.process_locks import ProcessLock, ProcessLockTimeout

pytestmark = [pytest.mark.failure_injection, pytest.mark.integration]


@pytest.mark.skipif(os.name != "posix", reason="subprocess lock test is POSIX-oriented")
def test_process_lock_serializes_independent_processes(tmp_path: Path) -> None:
    lock_path = tmp_path / "operation.lock"
    ready_path = tmp_path / "ready"
    script = (
        "import pathlib, time; "
        "from depviz.infrastructure.process_locks import ProcessLock; "
        f"p=pathlib.Path({str(ready_path)!r}); "
        f"l=pathlib.Path({str(lock_path)!r}); "
        "\nwith ProcessLock(l, timeout_seconds=2):\n"
        " p.write_text('ready')\n"
        " time.sleep(3)\n"
    )
    environment = dict(os.environ)
    source = str(Path(__file__).resolve().parents[2] / "src")
    environment["PYTHONPATH"] = source
    process = subprocess.Popen([sys.executable, "-c", script], env=environment)
    try:
        deadline = time.monotonic() + 5
        while not ready_path.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready_path.exists(), "lock holder never became ready"

        with pytest.raises(ProcessLockTimeout):
            ProcessLock(lock_path, timeout_seconds=0.1).acquire()
    finally:
        process.terminate()
        process.wait(timeout=5)

    with ProcessLock(lock_path, timeout_seconds=1):
        owner = json.loads(lock_path.read_text(encoding="utf-8"))
        assert owner["pid"] == os.getpid()
