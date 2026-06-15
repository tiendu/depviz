import hashlib
import json
from pathlib import Path

from depviz.models import DependencyGraph, Package
from depviz.parsers import ParseResult


def manifest_hash(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest()[:12]


def default_cache_path(manifest_path: Path, depth: int) -> Path:
    cache_dir = manifest_path.parent / ".depviz"
    digest = manifest_hash(manifest_path)
    return cache_dir / f"graph-{digest}-depth{depth}.json"


def save_graph_cache(
    graph: DependencyGraph,
    parse_result: ParseResult,
    cache_path: Path,
) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "version": 1,
        "channels": parse_result.channels,
        "packages": [
            {
                "name": pkg.name,
                "ecosystem": pkg.ecosystem,
                "constraint": pkg.constraint,
                "source": pkg.source,
            }
            for pkg in graph.adjacency_list
        ],
        "edges": [
            {
                "parent": _package_key(parent),
                "child": _package_key(child),
            }
            for parent, children in graph.adjacency_list.items()
            for child in children
        ],
    }

    cache_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def load_graph_cache(cache_path: Path) -> DependencyGraph:
    data = json.loads(cache_path.read_text(encoding="utf-8"))

    graph = DependencyGraph()

    package_by_key: dict[str, Package] = {}

    for item in data.get("packages", []):
        pkg = Package(
            name=item["name"],
            ecosystem=item["ecosystem"],
            constraint=item.get("constraint"),
            source=item.get("source"),
        )
        package_by_key[_package_key(pkg)] = pkg
        graph.add_node(pkg)

    for edge in data.get("edges", []):
        parent = package_by_key.get(edge["parent"])
        child = package_by_key.get(edge["child"])

        if parent is None or child is None:
            continue

        graph.add_edge(parent, child)

    return graph


def _package_key(pkg: Package) -> str:
    return f"{pkg.ecosystem}:{pkg.name}"
