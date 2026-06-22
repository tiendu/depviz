from __future__ import annotations

import json
from importlib import resources

from depviz.api import Capability
from depviz.plugins.defaults import create_default_registry


def test_conda_plugin_declares_only_implemented_capabilities() -> None:
    registry = create_default_registry(discover_external=False)
    plugin = next(item for item in registry.plugins() if item.name == "depviz-conda")

    assert plugin.capabilities == frozenset(
        {
            Capability.INSPECT,
            Capability.RESOLVE,
            Capability.LOCK,
            Capability.HASH_LOCKING,
            Capability.APPLY,
            Capability.VERIFY,
        }
    )
    assert [item.name for item in plugin.environment_drivers] == ["conda-prefix-driver"]
    assert [item.name for item in plugin.verifiers] == ["conda-prefix-verifier"]


def test_phase3_schemas_are_packaged_and_valid_json() -> None:
    schema_root = resources.files("depviz") / "schemas"

    for filename in (
        "resolution-v1.schema.json",
        "plan-v1.schema.json",
        "lock-v1.schema.json",
        "conda-pip-lock-v1.schema.json",
        "candidate-v1.schema.json",
        "deployment-v1.schema.json",
        "verification-v1.schema.json",
    ):
        document = json.loads((schema_root / filename).read_text(encoding="utf-8"))
        assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert document["type"] == "object"
