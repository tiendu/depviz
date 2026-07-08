from depviz.api import Diagnostic, OperationContext, Severity, require_command_runner
from depviz.api.errors import BackendError
from depviz.builtin.conda.tooling import read_tool_version, tool_settings


class CondaHealthCheck:
    name = "conda-tool"

    def check(self, context: OperationContext) -> tuple[Diagnostic, ...]:
        settings = tool_settings(context, error=_health_error)
        runner = require_command_runner(context, backend=self.name, operation="doctor")
        version = read_tool_version(
            runner=runner,
            settings=settings,
            backend=self.name,
            operation="doctor",
        )
        selection = "auto-selected" if settings.auto_selected else "configured"
        return (
            Diagnostic(
                code="doctor.conda.tool",
                message=(
                    f"Conda backend {selection} {settings.tool} {version} at {settings.executable}"
                ),
                severity=Severity.INFO,
            ),
        )


def _health_error(message: str) -> BackendError:
    return BackendError(backend="conda-tool", operation="doctor", message=message)
