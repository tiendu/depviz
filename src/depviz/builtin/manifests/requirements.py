from __future__ import annotations

import shlex
from pathlib import Path

from depviz.api import DependencyIntent, Diagnostic, Requirement, Severity, SourceLocation
from depviz.builtin.manifests.common import (
    ParseResult,
    deduplicate,
    find_conflicting_duplicates,
    parse_packaging_requirement,
    strip_comment,
)


class RequirementsParser:
    def parse(self, file_path: Path) -> ParseResult:
        requirements: list[Requirement] = []
        constraints: list[Requirement] = []
        indexes: list[str] = []
        diagnostics: list[Diagnostic] = []
        self._parse_file(
            file_path=file_path.resolve(),
            as_constraint=False,
            stack=(),
            requirements=requirements,
            constraints=constraints,
            indexes=indexes,
            diagnostics=diagnostics,
        )
        diagnostics.extend(find_conflicting_duplicates(requirements))
        return ParseResult(
            DependencyIntent(
                requirements=tuple(requirements),
                constraints=tuple(constraints),
                indexes=tuple(deduplicate(indexes)),
                diagnostics=tuple(diagnostics),
            )
        )

    def _parse_file(
        self,
        *,
        file_path: Path,
        as_constraint: bool,
        stack: tuple[Path, ...],
        requirements: list[Requirement],
        constraints: list[Requirement],
        indexes: list[str],
        diagnostics: list[Diagnostic],
    ) -> None:
        if file_path in stack:
            diagnostics.append(
                Diagnostic(
                    code="manifest.include-cycle",
                    message=f"Requirement include cycle detected at {file_path}",
                    severity=Severity.ERROR,
                    source=SourceLocation(file_path),
                )
            )
            return
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            diagnostics.append(
                Diagnostic(
                    code="manifest.include-unreadable",
                    message=f"Cannot read requirement file: {error}",
                    severity=Severity.ERROR,
                    source=SourceLocation(file_path),
                )
            )
            return

        next_stack = (*stack, file_path)
        for line_number, raw_line in enumerate(lines, start=1):
            line = strip_comment(raw_line).strip()
            if not line:
                continue
            origin = SourceLocation(file_path, line_number)
            option = parse_requirement_option(line)
            if option is not None:
                kind, value = option
                if kind in {"requirement", "constraint"}:
                    self._parse_file(
                        file_path=(file_path.parent / value).resolve(),
                        as_constraint=kind == "constraint",
                        stack=next_stack,
                        requirements=requirements,
                        constraints=constraints,
                        indexes=indexes,
                        diagnostics=diagnostics,
                    )
                    continue
                if kind in {"index-url", "extra-index-url"}:
                    indexes.append(value)
                    continue
                diagnostics.append(
                    Diagnostic(
                        code="manifest.unsupported-option",
                        message=f"Requirement option {line!r} is not supported yet",
                        severity=Severity.ERROR,
                        source=origin,
                        hint=(
                            "Remove it or add explicit backend support before resolving "
                            "this manifest."
                        ),
                    )
                )
                continue

            requirement, diagnostic = parse_packaging_requirement(
                line,
                ecosystem="pypi",
                origin=origin,
                direct=not as_constraint,
            )
            if diagnostic is not None:
                diagnostics.append(diagnostic)
            elif requirement is not None:
                (constraints if as_constraint else requirements).append(requirement)


def parse_requirement_option(line: str) -> tuple[str, str] | None:
    try:
        parts = shlex.split(line)
    except ValueError:
        return "unsupported", line
    if not parts:
        return None
    first = parts[0]
    mappings = {
        "-r": "requirement",
        "--requirement": "requirement",
        "-c": "constraint",
        "--constraint": "constraint",
        "-i": "index-url",
        "--index-url": "index-url",
        "--extra-index-url": "extra-index-url",
    }
    for option, kind in mappings.items():
        if first == option and len(parts) == 2:
            return kind, parts[1]
        prefix = f"{option}="
        if first.startswith(prefix):
            return kind, first[len(prefix) :]
    if first.startswith("-r") and first != "-r":
        return "requirement", first[2:]
    if first.startswith("-c") and first != "-c":
        return "constraint", first[2:]
    if first.startswith("-"):
        return "unsupported", line
    return None
