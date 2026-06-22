import json
from importlib.resources import files


def test_phase5_python_lock_schema_is_packaged() -> None:
    schema = files("depviz.schemas").joinpath("python-lock-v1.schema.json")
    document = json.loads(schema.read_text(encoding="utf-8"))

    assert document["properties"]["schema"]["const"] == "depviz.python-lock"
    assert document["properties"]["schema_version"]["const"] == 1


def test_candidate_schema_includes_garbage_collected_status() -> None:
    schema = files("depviz.schemas").joinpath("candidate-v1.schema.json")
    document = json.loads(schema.read_text(encoding="utf-8"))

    assert "removed" in document["properties"]["status"]["enum"]
