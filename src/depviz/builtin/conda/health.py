from depviz.api import Diagnostic, OperationContext, Severity
from depviz.api.errors import BackendError
from depviz.builtin.conda.tooling import read_tool_version, tool_settings
from depviz.infrastructure import LocalCommandRunner


class CondaHealthCheck:
    name = "conda-tool"

    def check(self, context: OperationContext) -> tuple[Diagnostic, ...]:
        settings = tool_settings(context, error=_health_error)
        runner = context.command_runner or LocalCommandRunner()
        version = read_tool_version(
            runner=runner,
            settings=settings,
            backend=self.name,
            operation="doctor",
        )
        return (
            Diagnostic(
                code="doctor.conda.tool",
                message=f"Conda backend executable is available: {version}",
                severity=Severity.INFO,
            ),
        )


def _health_error(message: str) -> BackendError:
    return BackendError(backend="conda-tool", operation="doctor", message=message)
