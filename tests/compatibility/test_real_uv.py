from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from depviz.api import OperationContext
from depviz.builtin.python.tooling import read_python_runtime, read_uv_version, uv_settings
from depviz.infrastructure import LocalCommandRunner

pytestmark = [pytest.mark.compatibility, pytest.mark.integration]


def _backend_error(message: str):  # type: ignore[no-untyped-def]
    from depviz.api.errors import BackendError

    return BackendError(backend="test", operation="compatibility", message=message)


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_installed_uv_and_python_identity_are_compatible(tmp_path: Path) -> None:
    context = OperationContext(
        command_runner=LocalCommandRunner(),
        working_directory=tmp_path,
        configuration={"python.interpreter": sys.executable, "python.uv_executable": "uv"},
    )
    settings = uv_settings(context, error=_backend_error)
    runner = LocalCommandRunner()
    version = read_uv_version(
        runner=runner,
        settings=settings,
        backend="test",
        operation="compatibility",
    )
    runtime = read_python_runtime(
        runner=runner,
        settings=settings,
        backend="test",
        operation="compatibility",
    )
    assert version.count(".") >= 1
    assert runtime.executable
    assert runtime.major == sys.version_info.major
    assert runtime.minor == sys.version_info.minor
