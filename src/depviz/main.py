from __future__ import annotations

import argparse
import sys
from pathlib import Path

from depviz import __version__
from depviz.analysis import analyze
from depviz.inventory import load_inventory
from depviz.manifests import load_manifest
from depviz.model import name_matches
from depviz.render import render_json, render_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="depviz",
        description="Show which installed packages are risky to change, and expose version conflicts.",
    )
    parser.add_argument(
        "target",
        nargs="?",
        help=(
            "manifest or environment prefix. If it is not a path, it is treated as a package name "
            "in the current environment."
        ),
    )
    parser.add_argument("package", nargs="?", help="show one package only")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--version", action="version", version=f"depviz {__version__}")
    return parser


def _looks_like_path(text: str) -> bool:
    """Return True when *text* clearly expresses a path/manifest, not a package name."""

    if "/" in text or "\\" in text or text.startswith((".", "~")):
        return True
    candidate = Path(text)
    name = candidate.name.lower()
    if name == "pyproject.toml":
        return True
    if candidate.suffix.lower() in {".yml", ".yaml"}:
        return True
    return name.startswith("requirements") and candidate.suffix.lower() in {".txt", ".in"}


def _interpret(args: argparse.Namespace) -> tuple[Path | None, Path | None, str | None]:
    prefix: Path | None = None
    manifest_path: Path | None = None
    focus: str | None = args.package
    if args.target:
        candidate = Path(args.target).expanduser()
        if candidate.exists():
            if candidate.is_dir():
                prefix = candidate
            else:
                manifest_path = candidate
        elif focus is None and not _looks_like_path(args.target):
            focus = args.target
        else:
            raise ValueError(f"target does not exist: {args.target}")
    return prefix, manifest_path, focus


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        prefix, manifest_path, focus = _interpret(args)
        manifest = load_manifest(manifest_path) if manifest_path else None
        inventory = load_inventory(prefix)
        results, roots, missing_roots = analyze(inventory, manifest, focus=focus)
        if args.json:
            print(render_json(inventory, results, roots, missing_roots, focus))
        else:
            print(render_text(inventory, results, roots, missing_roots, focus))
        if focus and not any(name_matches(row.package, focus) for row in results):
            return 1
        return 0
    except (OSError, TypeError, ValueError) as error:
        print(f"depviz: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
