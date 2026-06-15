from depviz.fetchers import CondaFetcher, FetcherRegistry
from depviz.models import Package


def test_fetcher_registry_returns_pypi_fetcher_for_pypi_package() -> None:
    registry = FetcherRegistry()

    fetcher = registry.get(Package(name="requests", ecosystem="pypi", source="pypi"))

    assert fetcher.__class__.__name__ == "PyPIFetcher"


def test_fetcher_registry_returns_conda_fetcher_for_conda_package() -> None:
    registry = FetcherRegistry(channels=["bioconda", "conda-forge"])

    fetcher = registry.get(Package(name="samtools", ecosystem="conda", source=None))

    assert fetcher.__class__.__name__ == "CondaFetcher"


def test_conda_fetcher_parses_direct_dependencies() -> None:
    data = {
        "result": {
            "graph_roots": [
                {
                    "name": "samtools",
                    "depends": [
                        "libzlib >=1.3.1,<2.0a0",
                        "htslib >=1.23.1,<1.24.0a0",
                        "ncurses >=6.5,<7.0a0",
                        "__osx >=11.0",
                    ],
                }
            ]
        }
    }

    deps = CondaFetcher._parse_direct_dependencies(data)

    names = {pkg.name for pkg in deps}
    constraints = {pkg.name: pkg.constraint for pkg in deps}

    assert names == {"libzlib", "htslib", "ncurses"}
    assert constraints["libzlib"] == ">=1.3.1,<2.0a0"
    assert constraints["htslib"] == ">=1.23.1,<1.24.0a0"
    assert constraints["ncurses"] == ">=6.5,<7.0a0"


def test_conda_fetcher_parses_empty_dependencies() -> None:
    data = {"result": {"graph_roots": [{"name": "ncurses", "depends": []}]}}

    deps = CondaFetcher._parse_direct_dependencies(data)

    assert deps == set()


def test_conda_fetcher_ignores_invalid_root_entries() -> None:
    data = {
        "result": {
            "graph_roots": [
                "not-a-dict",
                {"name": "bad", "depends": ["__linux >=5"]},
            ]
        }
    }

    deps = CondaFetcher._parse_direct_dependencies(data)

    assert deps == set()

