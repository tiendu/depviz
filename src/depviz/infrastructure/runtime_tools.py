from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class PathRuntimeTools:
    """Resolve backend tools from the host ``PATH``.

    Frozen launchers contain a Python runtime for Depviz itself, but that
    launcher is not necessarily a reusable Python interpreter. In frozen mode
    we therefore resolve a real, ABI-compatible host interpreter instead of
    returning ``sys.executable`` blindly.
    """

    def find_executable(self, *names: str) -> str | None:
        for name in names:
            if executable := shutil.which(name):
                return executable
        return None

    def default_python_interpreter(self) -> str | None:
        if not _is_frozen():
            return sys.executable

        major = sys.version_info.major
        minor = sys.version_info.minor
        if os.name == "nt":
            names = (
                f"python{major}.{minor}.exe",
                f"python{major}.exe",
                "python.exe",
                "python3.exe",
            )
        else:
            names = (
                f"python{major}.{minor}",
                f"python{major}",
                "python3",
                "python",
            )
        return self.find_executable(*names)


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))
