from __future__ import annotations

from depviz.api import BackendPayload, EnvironmentState, EnvironmentTarget, OperationContext
from depviz.api.errors import InspectionFailed
from depviz.builtin.conda.inspector import CondaPrefixInspector
from depviz.builtin.python.prefix import inspect_python_prefix
from depviz.core.resolution import environment_state_to_dict


class CondaPipPrefixInspector:
    name = "conda-pip-prefix"

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
        conda_state = CondaPrefixInspector().inspect(
            EnvironmentTarget(target.path, "conda-prefix"),
            context,
        )
        python_state = inspect_python_prefix(
            target.path,
            context,
            backend=self.name,
            environment_kind=self.name,
            require_virtual_environment=False,
            include_installers={"pip", "uv"},
        )
        packages = tuple(
            sorted(
                (*conda_state.packages, *python_state.packages),
                key=lambda package: (package.ecosystem, package.name),
            )
        )
        return EnvironmentState(
            packages=packages,
            target=conda_state.target,
            complete=conda_state.complete and python_state.complete,
            diagnostics=(*conda_state.diagnostics, *python_state.diagnostics),
            native_payload=BackendPayload(
                schema="depviz.conda-pip.inspection.v1",
                data={
                    "conda": environment_state_to_dict(conda_state),
                    "python": environment_state_to_dict(python_state),
                },
            ),
            environment=EnvironmentTarget(target.path.resolve(), self.name),
        )
