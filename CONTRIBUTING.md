# Contributing to depviz

`depviz` has one job:

> **Identify which installed packages are risky to change, and explain why.**

A change belongs here only if it improves the **accuracy, explainability, reliability, or
performance** of that answer. Package installation, resolution, upgrade orchestration,
deployment, vulnerability scanning, dashboards, and plugin frameworks are deliberately out
of scope.

## Reliability rules

1. **Never silently understate blast radius.** If dependency metadata is malformed but a
   package name can be recovered, keep the edge and mark its version constraint `UNKNOWN`.
   If the name cannot be recovered, emit an explicit `analysis may be incomplete` warning.
2. **Unknown is better than false OK.** Unsupported version syntax must stay `UNKNOWN`
   unless a contradiction can still be proved from the known subset.
3. **Package identity is ecosystem-specific.** PyPI uses packaging canonicalization. Conda
   identity does not fold `.`, `_`, and `-`. Cross-ecosystem binding needs evidence that a
   Conda package provides the Python distribution.
4. **Roots are deterministic.** A supplied manifest defines roots. Without one, roots come
   from source SCCs of the dependency graph. Do not add provenance heuristics.
5. **No opaque risk score.** Ranking remains the tuple `(roots affected, transitive
   dependents, direct dependents)`.
6. **Inspection never mutates the target environment.**

## Test expectations

Behavioral tests are preferred over tests tied to implementation details. In particular,
changes to graph logic should continue to agree with the brute-force/randomized oracles.
Malformed metadata, cycles, mixed Conda/pip ownership, extras/markers, and large graphs are
release-critical cases.

Run:

```bash
make check
```

The Makefile creates an isolated `.build/venv`, installs the development checks there, and
runs pytest, Ruff, mypy, and compileall through that interpreter. Host-global copies of those
tools are deliberately not required. The CI matrix runs the same `make check` contract on
Python 3.11, 3.12, and 3.13. Ruff is constrained to the 0.16 series in `pyproject.toml`;
upgrade that range deliberately rather than inheriting a new default rule set accidentally.

## Standalone-binary invariants

When depviz is frozen into a standalone executable:

- never inspect the embedded runtime as the user's Python environment;
- prefer an explicit prefix, then active `CONDA_PREFIX`/`VIRTUAL_ENV`, then `python3`/`python` on `PATH`;
- an explicit/active prefix without Python stays that prefix (Conda-only inspection); never fall back to another Python;
- sanitize PyInstaller-modified library search paths before launching an external interpreter;
- binary packaging must not change graph or constraint semantics.

## Unix build/install contract

The public source-install workflow is:

```bash
make check && make
sudo make install
```

The Makefile must bootstrap build/check dependencies into its isolated `.build/venv`; users
should not have to install pytest, Ruff, mypy, or PyInstaller globally first. `make install`
must install the standalone command, not perform an editable Python-package install. Keep
`PREFIX`, `BINDIR`, and `DESTDIR` overrideable and never invoke `sudo` from the Makefile.
`make binary` is a lower-level alias for CI/release packaging.
