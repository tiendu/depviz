import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from depviz.models import Package


@dataclass(frozen=True)
class ParseResult:
    packages: set[Package] = field(default_factory=set)
    channels: list[str] = field(default_factory=list)


class ManifestParser(Protocol):
    def parse(self, file_path: Path) -> ParseResult:
        ...


def normalize_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def parse_requirement_line(line: str) -> tuple[str, str | None] | None:
    line = line.strip()

    if not line or line.startswith("#"):
        return None

    line = line.split("#", 1)[0].strip()

    if line.startswith("-") or line.startswith("--"):
        return None

    match = re.match(r"^([A-Za-z0-9_.-]+)\s*([<>=!~].*)?$", line)
    if not match:
        return None

    name = normalize_name(match.group(1))
    constraint = match.group(2).strip() if match.group(2) else None

    return name, constraint


class RequirementsParser:
    def parse(self, file_path: Path) -> ParseResult:
        packages: set[Package] = set()

        for raw_line in file_path.read_text(encoding="utf-8").splitlines():
            parsed = parse_requirement_line(raw_line)
            if not parsed:
                continue

            name, constraint = parsed
            packages.add(
                Package(
                    name=name,
                    ecosystem="pypi",
                    constraint=constraint,
                    source="pypi",
                )
            )

        return ParseResult(packages=packages)


class CondaYamlParser:
    """
    Minimal environment.yml parser.

    Supports:

    channels:
      - bioconda
      - conda-forge

    dependencies:
      - python=3.12
      - samtools=1.20
      - pip:
          - requests==2.32.0
    """

    def parse(self, file_path: Path) -> ParseResult:
        packages: set[Package] = set()
        channels: list[str] = []

        section: str | None = None
        in_pip_block = False

        for raw_line in file_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip() or raw_line.lstrip().startswith("#"):
                continue

            stripped = raw_line.strip()

            if stripped == "channels:":
                section = "channels"
                in_pip_block = False
                continue

            if stripped == "dependencies:":
                section = "dependencies"
                in_pip_block = False
                continue

            if stripped.endswith(":") and not stripped.startswith("-"):
                section = None
                in_pip_block = False
                continue

            if section == "channels":
                if stripped.startswith("-"):
                    channel = stripped[1:].strip()
                    if channel:
                        channels.append(channel)
                continue

            if section != "dependencies":
                continue

            if stripped == "- pip:":
                in_pip_block = True
                continue

            if not stripped.startswith("-"):
                continue

            dep = stripped[1:].strip()

            parsed = parse_requirement_line(dep)
            if not parsed:
                continue

            name, constraint = parsed

            if in_pip_block:
                packages.add(
                    Package(
                        name=name,
                        ecosystem="pypi",
                        constraint=constraint,
                        source="pypi",
                    )
                )
            else:
                packages.add(
                    Package(
                        name=name,
                        ecosystem="conda",
                        constraint=constraint,
                        source=None,
                    )
                )

        return ParseResult(packages=packages, channels=channels)


def get_parser(file_path: Path) -> ManifestParser:
    name = file_path.name.lower()
    suffix = file_path.suffix.lower()

    if name.startswith("requirements") and suffix == ".txt":
        return RequirementsParser()

    if name in {
        "environment.yml",
        "environment.yaml",
        "conda.yml",
        "conda.yaml",
    }:
        return CondaYamlParser()

    if suffix in {".yml", ".yaml"}:
        return CondaYamlParser()

    raise ValueError(f"Unsupported manifest type: {file_path}")

