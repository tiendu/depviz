from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from depviz.api import Command
from depviz.infrastructure import LocalCommandRunner
from depviz.infrastructure.deployment import DeploymentState, ManagedDeploymentStore
from depviz.infrastructure.process_locks import ProcessLock
from depviz.infrastructure.storage import write_bytes_atomic

pytestmark = pytest.mark.hardening


def _valid_state() -> dict[str, object]:
    return {
        "schema": "depviz.deployment",
        "schema_version": 1,
        "current_candidate_id": None,
        "history": [],
        "updated_at": "2026-06-22T00:00:00+00:00",
    }


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda value: value.update(schema_version=999), "Unsupported deployment schema"),
        (lambda value: value.update(unexpected=True), "unknown fields"),
        (lambda value: value.pop("history"), "missing required fields"),
        (lambda value: value.update(updated_at="not-a-time"), "ISO-8601"),
        (lambda value: value.update(updated_at="2026-06-22T00:00:00"), "timezone"),
    ],
)
def test_deployment_state_fails_closed_on_corruption(
    tmp_path: Path,
    mutation,  # type: ignore[no-untyped-def]
    match: str,
) -> None:
    store = ManagedDeploymentStore(tmp_path / "deployment")
    store.initialize()
    value = _valid_state()
    mutation(value)
    store.state_path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match=match):
        store.read_state()


def test_truncated_state_fails_closed(tmp_path: Path) -> None:
    store = ManagedDeploymentStore(tmp_path / "deployment")
    store.initialize()
    store.state_path.write_text('{"schema": "depviz.deployment",', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON"):
        store.read_state()


def test_archive_lock_is_idempotent_but_rejects_conflicting_content(tmp_path: Path) -> None:
    store = ManagedDeploymentStore(tmp_path / "deployment")
    store.initialize()
    lock_id = f"sha256:{'a' * 64}"
    first = store.archive_lock(lock_id, b"same\n")
    second = store.archive_lock(lock_id, b"same\n")
    assert first == second
    assert first.read_bytes() == b"same\n"
    with pytest.raises(ValueError, match="conflicting content"):
        store.archive_lock(lock_id, b"different\n")


def test_atomic_write_removes_temporary_file_after_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "state.json"

    def fail_replace(source: object, destination: object) -> None:
        del source, destination
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        write_bytes_atomic(target, b"content")

    assert not target.exists()
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_process_lock_is_released_after_exception(tmp_path: Path) -> None:
    path = tmp_path / "operation.lock"
    with pytest.raises(RuntimeError, match="boom"):
        with ProcessLock(path, timeout_seconds=0.1):
            raise RuntimeError("boom")
    with ProcessLock(path, timeout_seconds=0.1):
        pass


@pytest.mark.skipif(os.name != "posix", reason="process-group cleanup uses POSIX sessions")
@pytest.mark.integration
def test_timeout_kills_spawned_child_process(tmp_path: Path) -> None:
    script = (
        "import subprocess, sys, time; "
        "p=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "print(p.pid, flush=True); "
        "time.sleep(60)"
    )
    result = LocalCommandRunner().run(
        Command(argv=(sys.executable, "-c", script)),
        timeout_seconds=5.0,
        output_limit=1024,
    )
    assert result.timed_out
    assert result.stdout.strip(), "child process did not start before timeout"
    child_pid = int(result.stdout.strip())

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        stat_path = Path(f"/proc/{child_pid}/stat")
        if not stat_path.exists():
            break
        fields = stat_path.read_text(encoding="utf-8").split()
        if len(fields) > 2 and fields[2] == "Z":
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"child process {child_pid} survived command timeout")


def test_state_write_is_deterministic_and_leaves_no_temp_residue(tmp_path: Path) -> None:
    store = ManagedDeploymentStore(tmp_path / "deployment")
    store.initialize()
    state = DeploymentState(updated_at="2026-06-22T00:00:00+00:00")
    store.write_state(state)
    first = store.state_path.read_bytes()
    store.write_state(state)
    second = store.state_path.read_bytes()
    assert first == second
    assert list(store.metadata_dir.glob(".deployment.json.*.tmp")) == []


def test_candidate_and_pending_documents_reject_future_or_unknown_fields(tmp_path: Path) -> None:
    store = ManagedDeploymentStore(tmp_path / "deployment")
    store.initialize()
    candidate_id = "c-corrupt"
    candidate = store.candidate(candidate_id)
    candidate.path.mkdir()
    from depviz.infrastructure.deployment import new_candidate_record

    store.write_candidate(
        new_candidate_record(
            candidate,
            lock_id=f"sha256:{'a' * 64}",
            lock_format="depviz.conda-lock.v1",
            resolution_digest=f"sha256:{'b' * 64}",
        )
    )
    candidate_path = store.records_dir / f"{candidate_id}.json"
    candidate_document = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate_document["unknown"] = True
    candidate_path.write_text(json.dumps(candidate_document), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown fields"):
        store.read_candidate(candidate_id)

    pending_document = {
        "schema": "depviz.deployment-switch",
        "schema_version": 999,
        "operation": "promote",
        "from_candidate_id": None,
        "to_candidate_id": candidate_id,
        "next_state": {
            "current_candidate_id": candidate_id,
            "history": [],
            "updated_at": "2026-06-22T00:00:00+00:00",
        },
    }
    store.pending_path.write_text(json.dumps(pending_document), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported pending-switch schema"):
        store.read_pending()
