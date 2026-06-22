from depviz.analyzer import (
    find_dependencies,
    find_dependents,
    find_transitive_dependencies,
    find_transitive_dependents,
    build_dependency_tree,
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


def test_build_dependency_tree() -> None:
    graph = DependencyGraph()

    graph.add_edge(pkg("samtools"), pkg("htslib"))
    graph.add_edge(pkg("samtools"), pkg("ncurses"))
    graph.add_edge(pkg("htslib"), pkg("libcurl"))
    graph.add_edge(pkg("htslib"), pkg("libzlib"))

    trees = build_dependency_tree(graph, "samtools", max_depth=3)

    assert len(trees) == 1

    root = trees[0]
    assert root.package.name == "samtools"

    child_names = {child.package.name for child in root.children}
    assert child_names == {"htslib", "ncurses"}

    htslib = next(child for child in root.children if child.package.name == "htslib")
    assert {child.package.name for child in htslib.children} == {
        "libcurl",
        "libzlib",
    }


def test_build_dependency_tree_respects_max_depth() -> None:
    graph = DependencyGraph()

    graph.add_edge(pkg("samtools"), pkg("htslib"))
    graph.add_edge(pkg("htslib"), pkg("libcurl"))
    graph.add_edge(pkg("libcurl"), pkg("openssl"))

    trees = build_dependency_tree(graph, "samtools", max_depth=1)

    root = trees[0]
    assert root.package.name == "samtools"
    assert {child.package.name for child in root.children} == {"htslib"}

    htslib = root.children[0]
    assert htslib.children == []


def test_build_dependency_tree_marks_repeated_nodes() -> None:
    graph = DependencyGraph()

    graph.add_edge(pkg("samtools"), pkg("htslib"))
    graph.add_edge(pkg("samtools"), pkg("libzlib"))
    graph.add_edge(pkg("htslib"), pkg("libzlib"))

    trees = build_dependency_tree(graph, "samtools", max_depth=3)

    root = trees[0]

    repeated_nodes = [
        child for child in root.children if child.package.name == "libzlib" and child.repeated
    ]

    nested_repeated_nodes = [
        grandchild
        for child in root.children
        for grandchild in child.children
        if grandchild.package.name == "libzlib" and grandchild.repeated
    ]

    assert repeated_nodes or nested_repeated_nodes


def test_build_dependency_tree_unknown_package_returns_empty_list() -> None:
    graph = DependencyGraph()

    graph.add_edge(pkg("samtools"), pkg("htslib"))

    trees = build_dependency_tree(graph, "missing")

    assert trees == []
