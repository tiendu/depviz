from pathlib import Path

from depviz.api import OperationContext, Severity
from depviz.builtin.manifests.plugin import RequirementsManifestLoader
from depviz.builtin.manifests.pyproject import PyprojectManifestLoader


def test_pyproject_loads_base_extra_and_dependency_groups(tmp_path: Path) -> None:
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(
        """
[project]
name = "example"
version = "1.0"
requires-python = ">=3.11"
dependencies = ["requests>=2"]

[project.optional-dependencies]
plot = ["matplotlib==3.9"]

[dependency-groups]
test = ["pytest==8", { include-group = "lint" }]
lint = ["ruff==0.11"]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    intent = PyprojectManifestLoader().load(
        manifest,
        OperationContext(configuration={"python.extras": "plot", "python.groups": "test"}),
    )

    assert not intent.has_errors
    assert intent.metadata["requires-python"] == ">=3.11"
    assert {item.name for item in intent.requirements} == {
        "requests",
        "matplotlib",
        "pytest",
        "ruff",
    }


def test_pyproject_rejects_mutable_uv_sources(tmp_path: Path) -> None:
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(
        """
[project]
name = "example"
version = "1.0"
dependencies = ["example"]

[tool.uv.sources]
example = { git = "https://example.invalid/repository.git" }
""".strip()
        + "\n",
        encoding="utf-8",
    )

    intent = PyprojectManifestLoader().load(manifest, OperationContext())

    assert intent.has_errors
    assert any(item.code == "manifest.pyproject.uv-sources" for item in intent.diagnostics)
    assert all(item.severity is Severity.ERROR for item in intent.diagnostics)


def test_requirements_loader_supports_input_files() -> None:
    loader = RequirementsManifestLoader()

    assert loader.supports(Path("requirements.in"))
    assert loader.supports(Path("requirements-dev.in"))
    assert loader.supports(Path("requirements.txt"))
    assert not loader.supports(Path("constraints.in"))
