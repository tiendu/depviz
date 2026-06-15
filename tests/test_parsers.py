from pathlib import Path

import pytest

from depviz.parsers import (
    CondaYamlParser,
    RequirementsParser,
    get_parser,
    parse_requirement_line,
)


def test_parse_requirement_line_parses_name_and_constraint() -> None:
    assert parse_requirement_line("requests==2.32.0") == ("requests", "==2.32.0")
    assert parse_requirement_line("pandas>=2") == ("pandas", ">=2")
    assert parse_requirement_line("numpy") == ("numpy", None)


def test_parse_requirement_line_normalizes_underscores() -> None:
    assert parse_requirement_line("typing_extensions>=4") == (
        "typing-extensions",
        ">=4",
    )


def test_parse_requirement_line_strips_inline_comments() -> None:
    assert parse_requirement_line("requests==2.32.0 # needed for api") == (
        "requests",
        "==2.32.0",
    )


def test_parse_requirement_line_ignores_comments_and_options() -> None:
    assert parse_requirement_line("# comment") is None
    assert parse_requirement_line("--extra-index-url https://example.com/simple") is None
    assert parse_requirement_line("-r base.txt") is None


def test_requirements_parser_parses_packages(tmp_path: Path) -> None:
    file_path = tmp_path / "requirements.txt"
    file_path.write_text(
        """
# comment
requests==2.32.0
pandas>=2
numpy
--extra-index-url https://example.com/simple
""",
        encoding="utf-8",
    )

    result = RequirementsParser().parse(file_path)

    names = {pkg.name for pkg in result.packages}
    ecosystems = {pkg.ecosystem for pkg in result.packages}

    assert names == {"requests", "pandas", "numpy"}
    assert ecosystems == {"pypi"}
    assert result.channels == []


def test_requirements_parser_preserves_constraints(tmp_path: Path) -> None:
    file_path = tmp_path / "requirements.txt"
    file_path.write_text("requests==2.32.0\npandas>=2\n", encoding="utf-8")

    result = RequirementsParser().parse(file_path)

    constraints = {pkg.name: pkg.constraint for pkg in result.packages}

    assert constraints["requests"] == "==2.32.0"
    assert constraints["pandas"] == ">=2"


def test_requirements_parser_ignores_inline_comments(tmp_path: Path) -> None:
    file_path = tmp_path / "requirements.txt"
    file_path.write_text(
        "requests==2.32.0 # needed for api\n",
        encoding="utf-8",
    )

    result = RequirementsParser().parse(file_path)

    by_name = {pkg.name: pkg for pkg in result.packages}

    assert by_name["requests"].constraint == "==2.32.0"


def test_conda_yaml_parser_parses_channels_and_dependencies(tmp_path: Path) -> None:
    file_path = tmp_path / "environment.yml"
    file_path.write_text(
        """
name: test-env
channels:
  - bioconda
  - conda-forge
dependencies:
  - python=3.12
  - samtools=1.20
  - pip:
      - requests==2.32.0
      - rich>=13
""",
        encoding="utf-8",
    )

    result = CondaYamlParser().parse(file_path)

    assert result.channels == ["bioconda", "conda-forge"]

    by_name = {pkg.name: pkg for pkg in result.packages}

    assert by_name["python"].ecosystem == "conda"
    assert by_name["python"].constraint == "=3.12"

    assert by_name["samtools"].ecosystem == "conda"
    assert by_name["samtools"].constraint == "=1.20"

    assert by_name["requests"].ecosystem == "pypi"
    assert by_name["requests"].constraint == "==2.32.0"

    assert by_name["rich"].ecosystem == "pypi"
    assert by_name["rich"].constraint == ">=13"


def test_conda_yaml_parser_handles_no_channels(tmp_path: Path) -> None:
    file_path = tmp_path / "environment.yml"
    file_path.write_text(
        """
dependencies:
  - python=3.12
  - samtools=1.20
""",
        encoding="utf-8",
    )

    result = CondaYamlParser().parse(file_path)

    assert result.channels == []
    assert {pkg.name for pkg in result.packages} == {"python", "samtools"}


def test_conda_yaml_parser_ignores_other_sections(tmp_path: Path) -> None:
    file_path = tmp_path / "environment.yml"
    file_path.write_text(
        """
name: test-env
variables:
  FOO: bar
dependencies:
  - python=3.12
""",
        encoding="utf-8",
    )

    result = CondaYamlParser().parse(file_path)

    assert {pkg.name for pkg in result.packages} == {"python"}


def test_get_parser_detects_requirements_file() -> None:
    parser = get_parser(Path("requirements.txt"))
    assert isinstance(parser, RequirementsParser)


def test_get_parser_detects_environment_file() -> None:
    parser = get_parser(Path("environment.yml"))
    assert isinstance(parser, CondaYamlParser)


def test_get_parser_rejects_unknown_file() -> None:
    with pytest.raises(ValueError):
        get_parser(Path("package.json"))

