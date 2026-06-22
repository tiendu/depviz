from __future__ import annotations

from depviz.api import EnvironmentState, EnvironmentTarget, OperationContext
from depviz.api.errors import InspectionFailed
from depviz.builtin.python.prefix import inspect_python_prefix


class PythonVenvInspector:
    name = "python-venv"

    def inspect(
        self,
        target: EnvironmentTarget,
        context: OperationContext,
    ) -> EnvironmentState:
        if target.kind != self.name:
            raise InspectionFailed(
                backend=self.name,
                operation="inspect",
                message=f"Unsupported environment target kind {target.kind!r}",
            )
        return inspect_python_prefix(
            target.path,
            context,
            backend=self.name,
            environment_kind=self.name,
            require_virtual_environment=True,
        )
