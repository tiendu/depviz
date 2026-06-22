from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

DEFAULT_MAX_DOCUMENT_BYTES = 16 * 1024 * 1024


def read_bytes_limited(
    path: Path,
    *,
    max_bytes: int = DEFAULT_MAX_DOCUMENT_BYTES,
    label: str = "document",
) -> bytes:
    """Read a bounded regular file without following a final symlink."""

    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    if path.is_symlink():
        raise ValueError(f"Refusing symlink {label}: {path}")
    flags = os.O_RDONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError(f"Cannot open {label} {path}: {error}") from error
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"{label.capitalize()} is not a regular file: {path}")
        if file_stat.st_size > max_bytes:
            raise ValueError(
                f"{label.capitalize()} exceeds the {max_bytes}-byte safety limit: {path}"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            content = handle.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise ValueError(
                f"{label.capitalize()} exceeds the {max_bytes}-byte safety limit: {path}"
            )
        return content
    finally:
        os.close(descriptor)


def read_text_limited(
    path: Path,
    *,
    max_bytes: int = DEFAULT_MAX_DOCUMENT_BYTES,
    label: str = "document",
) -> str:
    try:
        return read_bytes_limited(path, max_bytes=max_bytes, label=label).decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label.capitalize()} is not valid UTF-8: {path}") from error


def ensure_private_directory(path: Path) -> None:
    """Create or validate a Depviz-owned directory and make it owner-only."""

    if path.is_symlink():
        raise ValueError(f"Refusing symlink directory: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir() or path.is_symlink():
        raise ValueError(f"Path is not a private directory: {path}")
    if os.name == "posix":
        path.chmod(0o700)


def reject_unsafe_writable_directory(path: Path, *, label: str) -> None:
    """Reject directories writable by group or other users on POSIX systems."""

    if path.is_symlink():
        raise ValueError(f"Refusing symlink {label}: {path}")
    if not path.is_dir():
        raise ValueError(f"{label.capitalize()} is not a directory: {path}")
    if os.name == "posix" and path.stat().st_mode & 0o022:
        raise ValueError(f"{label.capitalize()} is group/world writable: {path}")


def write_bytes_atomic(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    """Write bytes durably without exposing a partial or symlink destination."""

    if mode & ~0o777:
        raise ValueError("mode must contain only permission bits")
    if path.is_symlink():
        raise ValueError(f"Refusing to replace symlink destination: {path}")
    if path.parent.is_symlink():
        raise ValueError(f"Refusing symlink parent directory: {path.parent}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.parent.is_dir():
        raise ValueError(f"Destination parent is not a directory: {path.parent}")
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        if os.name == "posix":
            os.fchmod(file_descriptor, mode)
        with os.fdopen(file_descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        if os.name == "posix":
            path.chmod(mode)
        fsync_directory(path.parent)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def fsync_directory(directory: Path) -> None:
    if os.name != "posix":
        return
    flags = os.O_RDONLY
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
