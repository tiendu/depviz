from __future__ import annotations

import pytest

from depviz.infrastructure.tool_versions import extract_tool_version

pytestmark = pytest.mark.compatibility


@pytest.mark.parametrize(
    ("banner", "expected"),
    [
        ("uv 0.10.0", "0.10.0"),
        ("uv 0.10.0 (Homebrew 2026-06-01)", "0.10.0"),
        ("conda 24.11.3", "24.11.3"),
        ("micromamba 2.0.5", "2.0.5"),
        ("2.1.0 libmamba build 42", "2.1.0"),
        ("uv 0.11.0rc1 (abcdef)", "0.11.0rc1"),
    ],
)
def test_extract_tool_version_ignores_banner_decoration(banner: str, expected: str) -> None:
    assert extract_tool_version(banner) == expected


@pytest.mark.parametrize("banner", ["", "uv unknown", "release candidate"])
def test_extract_tool_version_rejects_unversioned_banner(banner: str) -> None:
    with pytest.raises(ValueError, match="no dotted numeric version"):
        extract_tool_version(banner)
