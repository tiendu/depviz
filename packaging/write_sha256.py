from __future__ import annotations

import hashlib
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: write_sha256.py FILE", file=sys.stderr)
        return 2

    target = Path(argv[1])
    if not target.is_file():
        print(f"not a file: {target}", file=sys.stderr)
        return 2

    output = target.with_name(f"{target.name}.sha256")
    output.write_text(f"{sha256(target)}  {target.name}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
