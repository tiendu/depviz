from pathlib import Path

from depviz.api import DependencyIntent, Diagnostic, Requirement, Severity
from depviz.cache import load_inspection_cache, save_inspection_cache
from depviz.models import DependencyGraph, GraphInspection, InspectionStatus, Package
from depviz.parsers import ParseResult


def test_inspection_cache_round_trip(tmp_path: Path) -> None:
    root = Package(name="root", ecosystem="pypi", constraint="==1", source="pypi")
    child = Package(name="child", ecosystem="pypi", constraint=">=2", source="pypi")
    graph = DependencyGraph()
    graph.add_edge(root, child)
    inspection = GraphInspection(
        graph=graph,
        status=InspectionStatus.APPROXIMATE,
        diagnostics=(
            Diagnostic(
                code="test.warning",
                message="approximate",
                severity=Severity.WARNING,
            ),
        ),
        requested_depth=3,
    )
    parsed = ParseResult(
        DependencyIntent(
            requirements=(
                Requirement(
                    ecosystem="pypi",
                    name="root",
                    specifier="==1",
                    source="pypi",
                ),
            )
        )
    )
    cache_path = tmp_path / ".depviz" / "inspection.json"

    save_inspection_cache(inspection, parsed, cache_path)
    loaded = load_inspection_cache(cache_path)

    assert loaded.status is InspectionStatus.APPROXIMATE
    assert loaded.requested_depth == 3
    assert loaded.diagnostics[0].code == "test.warning"
    assert sum(len(children) for children in loaded.graph.adjacency_list.values()) == 1
    assert not list(cache_path.parent.glob("*.tmp"))
