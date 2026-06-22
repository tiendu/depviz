from __future__ import annotations

import os
from pathlib import Path

import pytest

from depviz.api import EnvironmentTarget
from depviz.core.promotion import _switch, deployment_status
from depviz.infrastructure.deployment import (
    DeploymentState,
    ManagedDeploymentStore,
)

pytestmark = pytest.mark.failure_injection


def _deployment(tmp_path: Path) -> tuple[ManagedDeploymentStore, str, str]:
    store = ManagedDeploymentStore(tmp_path / "deployment")
    store.initialize()
    first = store.reserve_candidate().candidate_id
    second = store.reserve_candidate().candidate_id
    store.switch_current_link(first)
    store.write_state(DeploymentState(current_candidate_id=first))
    return store, first, second


@pytest.mark.skipif(os.name != "posix", reason="promotion uses POSIX symlinks")
def test_failure_before_journal_leaves_current_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, first, second = _deployment(tmp_path)

    def fail_write_pending(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("injected before journal")

    monkeypatch.setattr(store, "write_pending", fail_write_pending)
    with pytest.raises(OSError, match="before journal"):
        _switch(
            store,
            operation="promote",
            from_candidate_id=first,
            to_candidate_id=second,
            next_state=DeploymentState(current_candidate_id=second, history=(first,)),
        )

    assert store.current_link_candidate_id() == first
    assert store.read_state().current_candidate_id == first
    assert not store.pending_path.exists()


@pytest.mark.skipif(os.name != "posix", reason="promotion uses POSIX symlinks")
def test_failure_after_journal_is_rolled_back_by_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, first, second = _deployment(tmp_path)

    def fail_switch(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("injected before pointer")

    monkeypatch.setattr(store, "switch_current_link", fail_switch)
    with pytest.raises(OSError, match="before pointer"):
        _switch(
            store,
            operation="promote",
            from_candidate_id=first,
            to_candidate_id=second,
            next_state=DeploymentState(current_candidate_id=second, history=(first,)),
        )
    assert store.pending_path.exists()

    current, history = deployment_status(EnvironmentTarget(store.root, "managed-conda-deployment"))
    assert current == first
    assert history == ()
    assert not store.pending_path.exists()


@pytest.mark.skipif(os.name != "posix", reason="promotion uses POSIX symlinks")
def test_failure_after_pointer_is_completed_by_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, first, second = _deployment(tmp_path)
    original_write_state = store.write_state
    calls = 0

    def fail_first_state(state: DeploymentState) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected after pointer")
        original_write_state(state)

    monkeypatch.setattr(store, "write_state", fail_first_state)
    with pytest.raises(OSError, match="after pointer"):
        _switch(
            store,
            operation="promote",
            from_candidate_id=first,
            to_candidate_id=second,
            next_state=DeploymentState(current_candidate_id=second, history=(first,)),
        )
    assert store.current_link_candidate_id() == second
    assert store.read_state().current_candidate_id == first
    assert store.pending_path.exists()

    # Recovery uses a fresh store, just as a new CLI invocation would.
    current, history = deployment_status(EnvironmentTarget(store.root, "managed-conda-deployment"))
    assert current == second
    assert history == (first,)
    assert not store.pending_path.exists()


@pytest.mark.skipif(os.name != "posix", reason="promotion uses POSIX symlinks")
def test_failure_after_state_is_completed_by_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, first, second = _deployment(tmp_path)

    def fail_clear() -> None:
        raise OSError("injected before journal cleanup")

    monkeypatch.setattr(store, "clear_pending", fail_clear)
    with pytest.raises(OSError, match="journal cleanup"):
        _switch(
            store,
            operation="promote",
            from_candidate_id=first,
            to_candidate_id=second,
            next_state=DeploymentState(current_candidate_id=second, history=(first,)),
        )
    assert store.current_link_candidate_id() == second
    assert store.read_state().current_candidate_id == second
    assert store.pending_path.exists()

    current, history = deployment_status(EnvironmentTarget(store.root, "managed-conda-deployment"))
    assert current == second
    assert history == (first,)
    assert not store.pending_path.exists()
