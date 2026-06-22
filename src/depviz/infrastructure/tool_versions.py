from __future__ import annotations

import re

# Tool version banners vary across releases and packaging channels. Keep the parser
# deliberately narrow: accept a dotted numeric version with optional common
# pre-release/build suffixes, and ignore surrounding tool names or build metadata.
_VERSION = re.compile(
    r"(?<![0-9A-Za-z])"
    r"(?P<version>[0-9]+(?:\.[0-9]+){1,3}"
    r"(?:[-_.]?(?:a|alpha|b|beta|rc|dev|post)[-_.]?[0-9A-Za-z.]*)?"
    r"(?:\+[0-9A-Za-z.-]+)?)"
    r"(?![0-9A-Za-z])",
    re.IGNORECASE,
)


def extract_tool_version(banner: str) -> str:
    """Extract one stable version token from a package-manager banner.

    Examples include ``uv 0.10.0``, ``conda 25.1.1``, and micromamba
    banners with commit or build text after the version. Ambiguous or empty
    banners fail instead of returning an arbitrary final word.
    """

    matches = [match.group("version") for match in _VERSION.finditer(banner)]
    if not matches:
        raise ValueError("Tool version banner contains no dotted numeric version")
    return matches[0]
