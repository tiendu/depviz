# depviz

Inspect dependency graphs for PyPI and Conda/Bioconda environments.

## Features

- Parse `requirements.txt`
- Parse `environment.yml`
- Resolve PyPI dependencies from PyPI metadata
- Resolve Conda dependencies using `micromamba`, `mamba`, or `conda`
- Calculate dependency blast radius
- Calculate transitive dependency weight
- Explain why a dependency is present
- Estimate package impact across the resolved graph
- Show direct and transitive dependencies for a package
- Mixed Conda + pip environment support

## Installation

Clone the repository:

```bash
git clone https://github.com/tiendu/depviz.git
cd depviz
```

Create a development environment:

```bash
make install
```

Or manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

Analyze a Python requirements file:

```bash
depviz requirements.txt
```

Analyze a Conda environment:

```bash
depviz environment.yml
```

Limit dependency depth:

```bash
depviz environment.yml --depth 2
```

Show more results:

```bash
depviz environment.yml --limit 20
```

Show only blast radius:

```bash
depviz environment.yml --report blast
```

Show only dependency weight:

```bash
depviz environment.yml --report weight
```

Show why a package is present:

```bash
depviz environment.yml --report why --package libzlib
```

Show what may be affected if a package changes:

```bash
depviz environment.yml --report impact --package htslib
```

Show dependencies of a specific package:

```bash
depviz environment.yml --report deps --package samtools
```

## Example Output

```text
Found 34 root package(s)
Detected Conda channels: bioconda, conda-forge

Summary
========================================
Packages:   728
Edges:      2934
Ecosystems: 2

Blast Radius
========================================
 1. r-base [conda]                  required by 130
 2. libcxx [conda]                  required by 96
 3. python [conda]                  required by 66
```

## Development

```bash
make test
make lint
make typecheck
make check
make clean
```

## Roadmap

- ASCII dependency tree visualization
- Graphviz export
- Dependency diff between environments
- Package hub analysis
- HTML reports
- Support for `pyproject.toml`
- Support for `poetry.lock`
- Support for `conda-lock`
- Support for `Cargo.toml`
- Support for `package.json`

## License

MIT
