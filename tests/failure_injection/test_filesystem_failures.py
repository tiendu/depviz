from __future__ import annotations

import os
from pathlib import Path

import pytest

from depviz.infrastructure.storage import write_bytes_atomic

pytestmark = pytest.mark.failure_injection


def test_failed_file_fsync_preserves_existing_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "state.json"
    destination.write_bytes(b"old-complete-document\n")
    real_fsync = os.fsync
    calls = 0

    def fail_first_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated disk failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_first_fsync)
    with pytest.raises(OSError, match="disk failure"):
        write_bytes_atomic(destination, b"new-complete-document\n")

    assert destination.read_bytes() == b"old-complete-document\n"
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_directory_fsync_failure_never_exposes_partial_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "state.json"
    real_fsync = os.fsync
    calls = 0

    def fail_second_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated directory durability failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_second_fsync)
    with pytest.raises(OSError, match="durability failure"):
        write_bytes_atomic(destination, b"complete-document\n")

    # Rename already happened, so the only permissible visible state is the
    # complete new document. There must never be a partial document or temp file.
    assert destination.read_bytes() == b"complete-document\n"
    assert list(tmp_path.glob(".state.json.*.tmp")) == []
