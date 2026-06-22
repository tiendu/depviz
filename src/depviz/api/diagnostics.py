from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class SourceLocation:
    path: Path
    line: int | None = None
    column: int | None = None

    def format(self) -> str:
        location = str(self.path)
        if self.line is not None:
            location = f"{location}:{self.line}"
        if self.column is not None:
            location = f"{location}:{self.column}"
        return location


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    severity: Severity
    source: SourceLocation | None = None
    hint: str | None = None

    def format(self) -> str:
        prefix = self.severity.value.upper()
        location = f" {self.source.format()}" if self.source is not None else ""
        message = f"{prefix} [{self.code}]{location}: {self.message}"
        if self.hint:
            message = f"{message}\n  Hint: {self.hint}"
        return message
