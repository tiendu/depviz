import json
from pathlib import Path

from depviz.api import (
    BackendPayload,
    PackageReference,
    Requirement,
    Resolution,
    ResolutionStatus,
    ResolvedPackage,
    Target,
)
from depviz.serialization import resolution_to_json, write_resolution_json


def _resolution() -> Resolution:
    return Resolution(
        requested=(Requirement(ecosystem="conda", name="python", specifier="=3.11"),),
        packages=(
            ResolvedPackage(
                ecosystem="conda",
                name="python",
                version="3.11.9",
                build="h123_0",
                platform="linux-64",
                source="conda-forge",
                artifact="python-3.11.9-h123_0.conda",
                checksum="sha256:abc",
                dependencies=(
                    PackageReference(ecosystem="conda", name="_libgcc_mutex", specifier=">=0.1"),
                ),
            ),
        ),
        target=Target(platform="linux-64"),
        status=ResolutionStatus.COMPLETE,
        native_payload=BackendPayload(
            schema="depviz.conda.dry-run.v1",
            data={"transaction": {"success": True}},
        ),
    )


def test_resolution_json_preserves_normalized_and_native_data() -> None:
    document = json.loads(resolution_to_json(_resolution()))

    assert document["schema_version"] == 1
    assert document["packages"][0]["build"] == "h123_0"
    assert document["packages"][0]["dependencies"][0]["name"] == "_libgcc_mutex"
    assert document["native_payload"]["data"]["transaction"]["success"] is True


def test_resolution_file_is_written_without_temporary_residue(tmp_path: Path) -> None:
    destination = tmp_path / "resolution.json"

    write_resolution_json(destination, _resolution())

    assert json.loads(destination.read_text())["status"] == "complete"
    assert list(tmp_path.iterdir()) == [destination]
