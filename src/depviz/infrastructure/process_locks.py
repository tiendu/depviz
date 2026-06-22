from __future__ import annotations

import importlib
import json
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import BinaryIO


class ProcessLockTimeout(TimeoutError):
    pass


@dataclass(frozen=True)
class LockOwner:
    pid: int
    hostname: str
    acquired_at: float


class ProcessLock:
    """Cross-process advisory lock released automatically when the process exits."""

    def __init__(self, path: Path, *, timeout_seconds: float = 30.0) -> None:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds cannot be negative")
        self.path = path
        self.timeout_seconds = timeout_seconds
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.parent.is_symlink():
            raise ValueError(f"Refusing symlink process-lock directory: {self.path.parent}")
        if self.path.is_symlink():
            raise ValueError(f"Refusing symlink process lock: {self.path}")
        handle = self.path.open("a+b")
        if os.name == "posix":
            os.fchmod(handle.fileno(), 0o600)
        deadline = time.monotonic() + self.timeout_seconds
        try:
            while True:
                if _try_lock(handle):
                    self._handle = handle
                    _write_owner(handle)
                    return
                if time.monotonic() >= deadline:
                    owner = _read_owner(handle)
                    detail = (
                        f"; held by pid={owner.pid} host={owner.hostname}"
                        if owner is not None
                        else ""
                    )
                    raise ProcessLockTimeout(f"Timed out acquiring lock {self.path}{detail}")
                time.sleep(0.05)
        except BaseException:
            handle.close()
            raise

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            _unlock(handle)
        finally:
            handle.close()
            self._handle = None

    def __enter__(self) -> ProcessLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.release()


def _try_lock(handle: BinaryIO) -> bool:
    if os.name == "posix":
        import fcntl

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False
    if os.name == "nt":
        msvcrt = importlib.import_module("msvcrt")
        try:
            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    raise RuntimeError(f"Unsupported operating system for process locking: {os.name}")


def _unlock(handle: BinaryIO) -> None:
    if os.name == "posix":
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    if os.name == "nt":
        msvcrt = importlib.import_module("msvcrt")
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    raise RuntimeError(f"Unsupported operating system for process locking: {os.name}")


def _write_owner(handle: BinaryIO) -> None:
    owner = LockOwner(pid=os.getpid(), hostname=socket.gethostname(), acquired_at=time.time())
    payload = json.dumps(owner.__dict__, sort_keys=True).encode("utf-8") + b"\n"
    handle.seek(0)
    handle.truncate()
    handle.write(payload)
    handle.flush()
    os.fsync(handle.fileno())


def _read_owner(handle: BinaryIO) -> LockOwner | None:
    try:
        handle.seek(0)
        raw = handle.read(4097).decode("utf-8")
        if len(raw) > 4096:
            return None
        value = json.loads(raw)
        if not isinstance(value, dict):
            return None
        return LockOwner(
            pid=int(value["pid"]),
            hostname=str(value["hostname"]),
            acquired_at=float(value["acquired_at"]),
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
