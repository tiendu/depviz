from depviz.analyzer import (
    find_dependencies,
    find_dependents,
    find_transitive_dependencies,
    find_transitive_dependents,
)
from depviz.models import DependencyGraph, Package


def pkg(name: str) -> Package:
    return Package(name=name, ecosystem="pypi", source="pypi")


def test_find_dependencies() -> None:
    graph = DependencyGraph()

    graph.add_edge(pkg("samtools"), pkg("htslib"))
    graph.add_edge(pkg("samtools"), pkg("libzlib"))
    graph.add_edge(pkg("htslib"), pkg("libcurl"))

    deps = find_dependencies(graph, "samtools")

    assert {package.name for package in deps} == {"htslib", "libzlib"}


def test_find_transitive_dependencies() -> None:
    graph = DependencyGraph()

    graph.add_edge(pkg("samtools"), pkg("htslib"))
    graph.add_edge(pkg("htslib"), pkg("libcurl"))
    graph.add_edge(pkg("libcurl"), pkg("openssl"))

    deps = find_transitive_dependencies(graph, "samtools")

    assert {package.name for package in deps} == {
        "htslib",
        "libcurl",
        "openssl",
    }


def test_find_dependents() -> None:
    graph = DependencyGraph()

    graph.add_edge(pkg("samtools"), pkg("htslib"))
    graph.add_edge(pkg("bcftools"), pkg("htslib"))
    graph.add_edge(pkg("htslib"), pkg("libzlib"))

    dependents = find_dependents(graph, "htslib")

    assert {package.name for package in dependents} == {
        "samtools",
        "bcftools",
    }


def test_find_transitive_dependents() -> None:
    graph = DependencyGraph()

    graph.add_edge(pkg("samtools"), pkg("htslib"))
    graph.add_edge(pkg("bcftools"), pkg("htslib"))
    graph.add_edge(pkg("htslib"), pkg("libzlib"))

    affected = find_transitive_dependents(graph, "libzlib")

    assert {package.name for package in affected} == {
        "htslib",
        "samtools",
        "bcftools",
    }


def test_queries_normalize_underscores() -> None:
    graph = DependencyGraph()

    graph.add_edge(pkg("my-tool"), pkg("typing-extensions"))

    dependents = find_dependents(graph, "typing_extensions")

    assert {package.name for package in dependents} == {"my-tool"}

