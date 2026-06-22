from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Mapping
from typing import BinaryIO

from depviz.api import Command, CommandResult


class LocalCommandRunner:
    """Run commands without a shell and with bounded captured output."""

    def run(
        self,
        command: Command,
        *,
        timeout_seconds: float,
        output_limit: int,
        redact: tuple[str, ...] = (),
    ) -> CommandResult:
        if not command.argv:
            raise ValueError("Command argv cannot be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if output_limit < 1:
            raise ValueError("output_limit must be at least 1")

        environment = _build_environment(command.environment, command.remove_environment)
        started = time.monotonic()
        timed_out = False

        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                list(command.argv),
                cwd=command.cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=os.name == "posix",
            )

            try:
                returncode = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_process_group(process)
                returncode = process.wait()

            stdout, stdout_truncated = _read_bounded(stdout_file, output_limit)
            stderr, stderr_truncated = _read_bounded(stderr_file, output_limit)

        duration = time.monotonic() - started
        safe_argv = tuple(_redact(value, redact) for value in command.argv)
        return CommandResult(
            argv=safe_argv,
            returncode=returncode,
            stdout=_redact(stdout, redact),
            stderr=_redact(stderr, redact),
            duration_seconds=duration,
            timed_out=timed_out,
            output_truncated=stdout_truncated or stderr_truncated,
        )


def _build_environment(
    overrides: Mapping[str, str] | None, remove: tuple[str, ...]
) -> dict[str, str]:
    environment = dict(os.environ)
    for name in remove:
        environment.pop(name, None)
    if overrides:
        environment.update(overrides)
    return environment


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
    process.kill()


def _read_bounded(handle: BinaryIO, limit: int) -> tuple[str, bool]:
    handle.seek(0)
    raw = handle.read(limit + 1)
    truncated = len(raw) > limit
    return raw[:limit].decode("utf-8", errors="replace"), truncated


def _redact(value: str, secrets: tuple[str, ...]) -> str:
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "***")
    return redacted
