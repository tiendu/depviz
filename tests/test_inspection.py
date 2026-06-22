from depviz.api import DependencyIntent, Diagnostic, Requirement, Severity
from depviz.fetchers import (
    FetchResult,
    FetchStatus,
    FetcherRegistry,
    inspect_dependency_graph,
)
from depviz.models import InspectionStatus, Package
from depviz.parsers import ParseResult


class StaticFetcher:
    def __init__(self, results: dict[str, FetchResult]) -> None:
        self.results = results

    def fetch(self, package: Package) -> FetchResult:
        return self.results[package.name]


class StaticRegistry(FetcherRegistry):
    def __init__(self, results: dict[str, FetchResult]) -> None:
        self.fetcher = StaticFetcher(results)

    def get(self, package: Package) -> StaticFetcher:
        del package
        return self.fetcher


def parse_result_for(name: str) -> ParseResult:
    return ParseResult(
        DependencyIntent(requirements=(Requirement(ecosystem="pypi", name=name, source="pypi"),))
    )


def test_failed_fetch_marks_inspection_incomplete() -> None:
    registry = StaticRegistry(
        {
            "root": FetchResult(
                dependencies=frozenset(),
                status=FetchStatus.FAILED,
                diagnostics=(
                    Diagnostic(
                        code="test.failed",
                        message="network failed",
                        severity=Severity.ERROR,
                    ),
                ),
            )
        }
    )

    inspection = inspect_dependency_graph(
        parse_result_for("root"),
        max_depth=1,
        registry=registry,
    )

    assert inspection.status is InspectionStatus.INCOMPLETE
    assert any(item.code == "test.failed" for item in inspection.diagnostics)


def test_approximate_fetch_is_not_reported_as_complete() -> None:
    registry = StaticRegistry(
        {
            "root": FetchResult(
                dependencies=frozenset(),
                status=FetchStatus.APPROXIMATE,
                diagnostics=(
                    Diagnostic(
                        code="test.approximate",
                        message="metadata only",
                        severity=Severity.WARNING,
                    ),
                ),
            )
        }
    )

    inspection = inspect_dependency_graph(
        parse_result_for("root"),
        max_depth=1,
        registry=registry,
    )

    assert inspection.status is InspectionStatus.APPROXIMATE


def test_depth_truncation_is_explicit() -> None:
    child = Package(name="child", ecosystem="pypi", source="pypi")
    registry = StaticRegistry(
        {
            "root": FetchResult(
                dependencies=frozenset({child}),
                status=FetchStatus.COMPLETE,
            ),
            "child": FetchResult(
                dependencies=frozenset(),
                status=FetchStatus.COMPLETE,
            ),
        }
    )

    inspection = inspect_dependency_graph(
        parse_result_for("root"),
        max_depth=1,
        registry=registry,
    )

    assert inspection.status is InspectionStatus.INCOMPLETE
    assert any(item.code == "inspection.depth-truncated" for item in inspection.diagnostics)


def test_complete_finite_graph_can_be_reported_complete() -> None:
    child = Package(name="child", ecosystem="pypi", source="pypi")
    registry = StaticRegistry(
        {
            "root": FetchResult(
                dependencies=frozenset({child}),
                status=FetchStatus.COMPLETE,
            ),
            "child": FetchResult(
                dependencies=frozenset(),
                status=FetchStatus.COMPLETE,
            ),
        }
    )

    inspection = inspect_dependency_graph(
        parse_result_for("root"),
        max_depth=2,
        registry=registry,
    )

    assert inspection.status is InspectionStatus.COMPLETE
    assert {item.name for item in inspection.graph.adjacency_list} == {"root", "child"}
