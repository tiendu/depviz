from __future__ import annotations

import sys

import pytest

from depviz.api import OperationContext
from depviz.builtin.python.tooling import uv_settings
from depviz.infrastructure.commands import HostSubprocessEnvironment
from depviz.infrastructure.runtime_tools import PathRuntimeTools


def test_non_frozen_runtime_uses_current_python(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert PathRuntimeTools().default_python_interpreter() == sys.executable


def test_frozen_runtime_resolves_real_python_from_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        "depviz.infrastructure.runtime_tools.shutil.which",
        lambda name: "/tools/python" if name.startswith("python") else None,
    )
    assert PathRuntimeTools().default_python_interpreter() == "/tools/python"


def test_frozen_runtime_never_returns_launcher_when_python_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr("depviz.infrastructure.runtime_tools.shutil.which", lambda _name: None)
    assert PathRuntimeTools().default_python_interpreter() is None


def test_python_settings_explain_portable_interpreter_requirement() -> None:
    class MissingRuntime:
        def find_executable(self, *names: str) -> str | None:
            return None

        def default_python_interpreter(self) -> str | None:
            return None

    with pytest.raises(Exception, match="Portable Depviz binaries"):
        uv_settings(OperationContext(runtime_tools=MissingRuntime()), error=_backend_error)


def test_subprocess_environment_restores_original_loader_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LD_LIBRARY_PATH", "/frozen/private")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/host/lib")
    environment = HostSubprocessEnvironment().build({"EXAMPLE": "1"}, ())

    assert environment["LD_LIBRARY_PATH"] == "/host/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in environment
    assert environment["EXAMPLE"] == "1"


def test_subprocess_environment_removes_injected_loader_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LD_LIBRARY_PATH", "/frozen/private")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    environment = HostSubprocessEnvironment().build(None, ())

    assert "LD_LIBRARY_PATH" not in environment


def _backend_error(message: str) -> Exception:
    return RuntimeError(message)
