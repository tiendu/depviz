from __future__ import annotations

import pytest

from depviz.builtin.conda.transaction import parse_link_packages

pytestmark = pytest.mark.compatibility


@pytest.mark.parametrize(
    "payload",
    [
        # Conda/libmamba-style: URL and hash are usually in FETCH, dependencies in LINK.
        {
            "actions": {
                "FETCH": [
                    {
                        "name": "python",
                        "version": "3.12.8",
                        "build": "h123_0",
                        "url": "https://repo.example/linux-64/python-3.12.8-h123_0.conda",
                        "sha256": "a" * 64,
                        "channel": "https://repo.example/linux-64",
                    }
                ],
                "LINK": [
                    {
                        "name": "python",
                        "version": "3.12.8",
                        "build": "h123_0",
                        "subdir": "linux-64",
                        "channel": "https://repo.example/linux-64",
                        "depends": ["openssl >=3.0"],
                    }
                ],
            }
        },
        # Micromamba-style compatibility fields.
        {
            "actions": {
                "LINK": [
                    {
                        "name": "python",
                        "version": "3.12.8",
                        "build_string": "h123_0",
                        "platform": "linux-64",
                        "channel_url": "https://repo.example/linux-64",
                        "fn": "python-3.12.8-h123_0.conda",
                        "sha256": "a" * 64,
                        "depends": ["openssl >=3.0"],
                    }
                ]
            }
        },
        # Older Conda record vocabulary.
        {
            "actions": {
                "LINK": [
                    {
                        "name": "python",
                        "version": "3.12.8",
                        "build": "h123_0",
                        "platform": "linux-64",
                        "base_url": "https://repo.example/linux-64",
                        "dist_name": "python-3.12.8-h123_0.conda",
                        "sha256": "a" * 64,
                        "depends": ["openssl >=3.0"],
                    }
                ]
            }
        },
    ],
)
def test_supported_conda_transaction_shapes_normalize_identically(
    payload: dict[str, object],
) -> None:
    packages, diagnostics = parse_link_packages(payload, "linux-64", ())
    assert diagnostics == ()
    assert len(packages) == 1
    package = packages[0]
    assert package.name == "python"
    assert package.version == "3.12.8"
    assert package.build == "h123_0"
    assert package.platform == "linux-64"
    assert package.checksum == f"sha256:{'a' * 64}"
    assert package.dependencies[0].name == "openssl"
