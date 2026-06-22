import sys

from depviz.api import Command
from depviz.infrastructure import LocalCommandRunner


def test_command_runner_captures_output_without_shell() -> None:
    result = LocalCommandRunner().run(
        Command(argv=(sys.executable, "-c", "print('hello')")),
        timeout_seconds=10,
        output_limit=1024,
    )

    assert result.returncode == 0
    assert result.stdout == "hello\n"
    assert not result.timed_out
    assert not result.output_truncated


def test_command_runner_times_out_and_terminates_process() -> None:
    result = LocalCommandRunner().run(
        Command(argv=(sys.executable, "-c", "import time; time.sleep(10)")),
        timeout_seconds=0.05,
        output_limit=1024,
    )

    assert result.timed_out
    assert result.returncode != 0


def test_command_runner_limits_and_redacts_output() -> None:
    secret = "super-secret-token"
    result = LocalCommandRunner().run(
        Command(
            argv=(
                sys.executable,
                "-c",
                f"print('{secret}' + 'x' * 100)",
            )
        ),
        timeout_seconds=10,
        output_limit=32,
        redact=(secret,),
    )

    assert secret not in result.stdout
    assert secret not in " ".join(result.argv)
    assert result.output_truncated
