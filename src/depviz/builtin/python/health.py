from depviz.api import Diagnostic, OperationContext, Severity
from depviz.api.errors import BackendError
from depviz.builtin.python.tooling import (
    read_python_runtime,
    read_uv_version,
    require_host_compatible_runtime,
    runner_for,
    uv_settings,
)


class PythonHealthCheck:
    name = "python-uv-toolchain"

    def check(self, context: OperationContext) -> tuple[Diagnostic, ...]:
        settings = uv_settings(context, error=_health_error)
        runner = runner_for(context)
        runtime = read_python_runtime(
            runner=runner,
            settings=settings,
            backend=self.name,
            operation="doctor",
        )
        require_host_compatible_runtime(runtime, backend=self.name, operation="doctor")
        version = read_uv_version(
            runner=runner,
            settings=settings,
            backend=self.name,
            operation="doctor",
        )
        return (
            Diagnostic(
                code="doctor.python.toolchain",
                message=(
                    f"Python {runtime.version} ({runtime.implementation}) and uv {version} "
                    "are available for the host-compatible backend"
                ),
                severity=Severity.INFO,
            ),
        )


def _health_error(message: str) -> BackendError:
    return BackendError(backend="python-uv-toolchain", operation="doctor", message=message)
