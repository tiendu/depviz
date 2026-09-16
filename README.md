# depviz

`depviz` answers one question:

> **Which package is risky to change?**

It does not install, resolve, lock, apply, promote, roll back, or manage environments.
It reads dependency metadata already present in an environment, walks the graph backward,
and reports blast points plus version-constraint problems.

```bash
depviz
```

Inside a Python or Conda environment, that prints packages ranked by:

1. declared/top-level roots affected
2. all transitive dependents affected
3. direct dependents

No opaque score is invented.

```text
ROOTS TOTAL DIRECT   VERSION  PACKAGE
    8    47     12        OK  openssl
    6    51     18        OK  zlib
    3    12      4  CONFLICT  htslib
```

## Give depviz your roots

Graph topology can only infer which packages you intentionally asked for. A manifest lets
depviz use the real roots while still inspecting the installed environment:

```bash
depviz environment.yml
depviz requirements.txt
depviz pyproject.toml
```

Supported root manifests are Conda `environment.yml`/`.yaml`, requirements `.txt`/`.in`,
and `[project].dependencies` in `pyproject.toml`. Requirements `-r/--requirement` includes
and `-c/--constraint` files are followed; constraint files contribute version evidence
without becoming roots.

For one package:

```bash
depviz environment.yml openssl
```

Focused output includes one shortest dependency path from each affected root:

```text
openssl 3.4.1
  roots affected: 3 (git, python, samtools)
  paths:
    git -> libcurl -> openssl
    python -> openssl
    samtools -> htslib -> libcurl -> openssl
```

Text output shows at most 20 paths; `--json` contains them all.

Against the current environment without a manifest:

```bash
depviz openssl
```

Machine-readable output is intentionally a rendering option, not another command. JSON
contains an explicit `schema_version` plus the producing `depviz_version` so downstream
scripts can reject incompatible output deliberately:

```bash
depviz environment.yml --json | jq '.packages[:10]'
```

You can also point at an installed environment prefix:

```bash
depviz /opt/conda/envs/bio
```


## Standalone Linux command

`depviz` can be frozen into a single self-contained Linux executable. The release binary
contains depviz's own Python runtime and libraries, so the command itself does not require
Python or a package installation.

Build and install it like a normal Unix command:

```bash
make check                            # bootstraps isolated dev tools and runs checks
make                                  # bootstraps PyInstaller and builds dist/depviz
sudo make install                     # installs /usr/local/bin/depviz

depviz --version
depviz
```

`make check`, `make`, and `make install` bootstrap their own isolated `.build/venv` as needed.
You do not need to pre-install pytest, Ruff, mypy, PyInstaller, or depviz into your system Python.
The build host only needs Python 3.11+ with `venv`/`pip`, a C runtime suitable for PyInstaller,
and network/package-index access the first time the isolated environment is prepared.

Building first as your normal user and using sudo only for the final copy step is preferred:

```bash
make check && make
sudo make install
```

A one-shot `sudo make install` also works, but creates the build environment as root and is
therefore less pleasant for development.

The install location follows normal Unix make conventions:

```bash
make install PREFIX="$HOME/.local"     # no root required
make install DESTDIR=/tmp/pkgroot     # packaging/staging root
sudo make uninstall                   # removes the installed command
```

`/usr/local/bin` is the default location for a manually installed executable. `/etc` is for
system configuration, not commands. A distro package may instead set `PREFIX=/usr`.

The standalone executable deliberately does **not** inspect its embedded Python runtime.
For Python metadata it selects the target interpreter in this order:

1. an explicit environment prefix passed to `depviz`
2. the active `CONDA_PREFIX` or `VIRTUAL_ENV`
3. `python3`, then `python`, from `PATH` when no environment prefix is active

If an explicit or active prefix contains no Python interpreter, depviz keeps the inspection
scoped to that prefix; it does not fall back to an unrelated Python installation.

Conda metadata is still read directly from `conda-meta`. This means the binary can live in
`/usr/local/bin` while inspecting whichever environment the shell currently has activated.

Release Linux x86-64 binaries are built as PyInstaller one-file executables on a
`manylinux2014`/glibc 2.17 baseline for broad forward compatibility. `make binary` remains
available as the lower-level build-only target used by release automation; normal users should
prefer `make` followed by `make install`.

## What "risk" means

For package `P`, depviz walks dependency edges backward.

```text
samtools ─┐
          ├─> htslib ─> libcurl ─> openssl
bcftools ─┘                       └> zlib
```

If `openssl` changes, every package reachable in reverse is potentially affected. The
ranking tuple is simply:

```text
(number of roots affected, number of transitive dependents, number of direct dependents)
```

The tuple is sorted descending. It is deliberately not converted to a magic 0-100 score.

When a manifest is supplied, its declared packages are the roots. Without a manifest,
depviz uses source strongly connected components of the dependency graph as inferred roots.
There are no `REQUESTED`/history heuristics, so the rule stays deterministic and cycle-safe.

## Version conflicts

For each package, depviz collects the constraints imposed by direct dependents and by a
supplied manifest.

```text
A -> numpy >=1.24,<2
B -> numpy >=1.26
C -> numpy <1.27
```

Depviz reports:

- `OK` — the installed version satisfies the known constraints.
- `INVALID` — the installed version violates at least one constraint, but the constraints
  are not provably contradictory.
- `CONFLICT` — the known constraints have a provably empty intersection.
- `UNKNOWN` — metadata or version syntax cannot be interpreted safely.
- `MISSING` — another installed package depends on this package, but it is not installed.

When a conflict is provable, depviz also reports a small conflicting witness set instead
of forcing you to inspect every constraint:

```text
conflict:
  package-b -> numpy<2
  package-c -> numpy>=2
```

Conflict detection is deliberately conservative. Unknown syntax is never silently treated
as compatible.

## Mixed Conda + pip environments

PyPI distribution names use Python packaging canonicalization (`.`, `_`, and `-` are
equivalent there). Conda package identity is kept separate: Conda names are lowercased,
but valid separator characters are not folded together. Cross-ecosystem reconciliation only binds a Python requirement to a Conda package when
that package is known to provide the Python distribution, using installed Python metadata
and Conda file records. Mere name similarity is not treated as proof of identity.

This matters when, for example, a pip-installed package requires `numpy` but NumPy itself
is Conda-owned. Depviz binds that edge to the installed Conda NumPy instead of creating a
fake second `pypi:numpy` node and under-counting its blast radius.

For Conda version syntax, depviz uses Conda's native version matcher when available. Its
fallback supports only a conservative comparator/prefix subset; build-string or otherwise
unsupported syntax becomes `UNKNOWN` rather than a false `OK`.

## Where the graph comes from

- Python: installed distribution metadata (`Requires-Dist`) from the target interpreter.
- Conda: `conda-meta/*.json` from the target prefix.

Unresolved dependency references remain in the graph as explicit `MISSING` nodes so their
blast radius is still visible. Malformed metadata is handled conservatively: when a
dependency name can be recovered the edge is preserved and its constraint becomes
`UNKNOWN`; when it cannot be recovered depviz warns that analysis may be incomplete. Conda
virtual packages such as `__glibc` and `__cuda` are host capabilities rather than installed
packages, so they are not emitted as fake missing package rows.

This is inspection, not solving. Depviz never mutates the environment.

## Algorithm notes

The dependency graph is condensed into strongly connected components with an iterative
Kosaraju traversal, then reverse reachability is propagated once across the resulting DAG
using integer bitsets. This keeps cycles correct, avoids recursion-depth failures, and
avoids one full reverse BFS per package. Global analysis stores exact transitive counts
without materializing every ancestor list; full dependent lists and shortest paths are
materialized only for a focused package.

The test suite includes brute-force and randomized graph oracles plus adversarial cases for
cycles, deep chains, 10k-scale behavior, extras/marker fixpoints, malformed manifests and
installed metadata, constraint includes, duplicate metadata, mixed Conda/pip identity and
version semantics, virtual packages, wildcard exclusions, and ambiguous names.

## Development

```bash
make check
```

The check target bootstraps an isolated `.build/venv` and runs pytest, Ruff, mypy, and
compileall. Host-global copies of those tools are not required.

`pytest` also works directly from an unpacked source tree because `src` is configured as a
test import path.

## License

MIT
