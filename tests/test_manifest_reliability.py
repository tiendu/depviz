from pathlib import Path

from depviz.api import Severity
from depviz.parsers import CondaYamlParser, RequirementsParser


def test_requirements_parser_follows_includes_and_constraints(tmp_path: Path) -> None:
    base = tmp_path / "base.txt"
    base.write_text("requests==2.32.0\n", encoding="utf-8")
    constraints = tmp_path / "constraints.txt"
    constraints.write_text("urllib3<3\n", encoding="utf-8")
    root = tmp_path / "requirements.txt"
    root.write_text(
        "-r base.txt\n-c constraints.txt\n--extra-index-url https://packages.example/simple\n",
        encoding="utf-8",
    )

    intent = RequirementsParser().parse(root).intent

    assert [(item.name, item.specifier) for item in intent.requirements] == [
        ("requests", "==2.32.0")
    ]
    assert [(item.name, item.specifier) for item in intent.constraints] == [("urllib3", "<3")]
    assert intent.indexes == ("https://packages.example/simple",)
    assert not intent.has_errors


def test_requirements_parser_preserves_markers_extras_and_direct_url(tmp_path: Path) -> None:
    manifest = tmp_path / "requirements.txt"
    manifest.write_text(
        'demo[cli] @ https://example.com/demo.whl ; python_version >= "3.11"\n',
        encoding="utf-8",
    )

    requirement = RequirementsParser().parse(manifest).intent.requirements[0]

    assert requirement.name == "demo"
    assert requirement.extras == ("cli",)
    assert requirement.source == "https://example.com/demo.whl"
    assert requirement.marker == 'python_version >= "3.11"'


def test_requirements_parser_reports_unsupported_editable(tmp_path: Path) -> None:
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("-e .\n", encoding="utf-8")

    intent = RequirementsParser().parse(manifest).intent

    assert intent.has_errors
    assert any(item.code == "manifest.unsupported-option" for item in intent.diagnostics)


def test_requirements_parser_reports_include_cycle(tmp_path: Path) -> None:
    first = tmp_path / "requirements.txt"
    second = tmp_path / "other.txt"
    first.write_text("-r other.txt\n", encoding="utf-8")
    second.write_text("-r requirements.txt\n", encoding="utf-8")

    intent = RequirementsParser().parse(first).intent

    assert any(item.code == "manifest.include-cycle" for item in intent.diagnostics)
    assert any(item.severity is Severity.ERROR for item in intent.diagnostics)


def test_conda_parser_preserves_channel_qualified_source(tmp_path: Path) -> None:
    manifest = tmp_path / "environment.yml"
    manifest.write_text(
        "dependencies:\n  - conda-forge::numpy=2.0\n",
        encoding="utf-8",
    )

    requirement = CondaYamlParser().parse(manifest).intent.requirements[0]

    assert requirement.name == "numpy"
    assert requirement.specifier == "=2.0"
    assert requirement.source == "conda-forge"


def test_conda_parser_rejects_unsupported_nested_section(tmp_path: Path) -> None:
    manifest = tmp_path / "environment.yml"
    manifest.write_text(
        "dependencies:\n  - poetry:\n      - requests\n",
        encoding="utf-8",
    )

    intent = CondaYamlParser().parse(manifest).intent

    assert intent.has_errors
    assert any(item.code == "manifest.unsupported-conda-section" for item in intent.diagnostics)
