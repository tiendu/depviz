# depviz

Depviz is a **solver-backed dependency change controller**.

It coordinates established package managers through a staged lifecycle:

```text
inspect -> resolve -> plan -> lock -> apply -> verify -> promote or roll back
```

Version `0.8.0rc1` implements the complete managed lifecycle for:

- Conda environments through Conda, Mamba, or Micromamba
- Python virtual environments through `uv`
- mixed Conda prefixes with an exact pip wheel overlay

Release status: **release candidate**. It is suitable for controlled local dogfooding and non-critical local deployments, but is not presented as a regulated or unattended fleet controller. R and general composite environments remain intentionally paused.

The safety model is deliberately conservative:

- resolution is delegated to the package manager
- locks select exact artifacts and checksums
- apply consumes the lock without solving again
- every apply creates a new isolated candidate
- verification checks the complete installed package state
- promotion and rollback re-verify immediately before switching
- the active environment is selected through an atomic managed pointer
- arbitrary existing environments are never mutated in place

## Release validation

The normal development gate is:

```bash
make check
```

The release gate runs all deterministic tests except explicitly network-marked tests:

```bash
make check-release
```

Hardening groups can be run independently:

```bash
make test-hardening
make test-compatibility
make test-failure-injection
make test-security
```

Persistent state readers fail closed on unknown fields, future schema versions, malformed timestamps, truncated JSON, and identity mismatches. Plan and lock IDs remain full-document integrity hashes.

## Installation

```bash
git clone https://github.com/tiendu/depviz.git
cd depviz
make install
```

Install the external backend tools you intend to use:

```text
Conda backend       conda, mamba, or micromamba
Python backend      uv and a selected Python interpreter
Mixed backend       one Conda-family tool plus uv
```

Depviz does not embed or reimplement their solvers.

## Dependency-risk inspection

```bash
depviz inspect environment.yml --report blast
depviz inspect requirements.txt --require-complete
```

The legacy form remains supported:

```bash
depviz environment.yml --report impact --package htslib
```

Inspection builds a metadata graph. It is not an environment solve, and its result can be marked `approximate` or `incomplete`.

## Mixed Conda and pip lifecycle

A normal Conda environment file may contain both package ecosystems:

```yaml
channels:
  - conda-forge
  - bioconda
  - nodefaults

dependencies:
  - python=3.12
  - pip
  - samtools
  - bcftools
  - pip:
      - pydantic>=2
      - polars
```

Depviz detects this shape automatically. It solves the Conda layer first, binds the wheel resolution to the Python version and platform selected by that solve, and manages both layers inside one candidate prefix:

```bash
depviz resolve environment.yml \
  --tool micromamba \
  --platform osx-arm64 \
  --output resolution.json

depviz lock resolution.json --output lock.json

depviz apply lock.json \
  --deployment .depviz/mixed-app
```

The same command can use Mamba directly:

```bash
depviz resolve environment.yml \
  --tool mamba \
  --platform linux-64 \
  --output resolution.json
```

Or Conda with libmamba:

```bash
depviz resolve environment.yml \
  --tool conda \
  --solver libmamba \
  --platform linux-64 \
  --output resolution.json
```

The compound lock contains validated child Conda and Python locks. Apply installs exact Conda artifacts first and then exact SHA-256-pinned wheels into the candidate prefix without re-solving. Promotion and rollback switch the whole prefix atomically.

Direct ownership collisions fail:

```text
numpy requested under dependencies
numpy also requested under pip
```

Transitive overlaps are recorded under a conservative `pip-last` policy, and verification treats the pip wheel as the final owner of that Python distribution. Prefer moving overlapping packages into one layer whenever possible.

See [`docs/conda-pip-backend.md`](docs/conda-pip-backend.md) for the detailed guarantees and limitations.

## Conda lifecycle

### Resolve

```bash
depviz resolve environment.yml \
  --platform linux-64 \
  --output conda-resolution.json
```

Using Conda with libmamba:

```bash
depviz resolve environment.yml \
  --resolver conda-dry-run \
  --tool conda \
  --solver libmamba \
  --platform linux-64 \
  --output conda-resolution.json
```

### Plan

Against an existing prefix:

```bash
depviz plan environment.yml \
  --resolver conda-dry-run \
  --inspector conda-prefix \
  --prefix .conda/envs/current \
  --platform linux-64 \
  --output conda-plan.json
```

For a new environment:

```bash
depviz plan environment.yml \
  --resolver conda-dry-run \
  --empty \
  --platform linux-64 \
  --output conda-plan.json
```

### Lock and apply

```bash
depviz lock conda-resolution.json \
  --provider conda-exact-lock \
  --output conda-lock.json

depviz apply conda-lock.json \
  --provider conda-exact-lock \
  --driver conda-prefix-driver \
  --deployment .depviz/conda-app
```

Conda locks require SHA-256 by default. A legacy MD5-only artifact can be accepted only with the explicit unsafe compatibility flag:

```bash
depviz lock conda-resolution.json \
  --provider conda-exact-lock \
  --output conda-lock.json \
  --allow-weak-checksum
```

The Conda driver writes a checksum-bearing `@EXPLICIT` file. It does not invoke dependency resolution during apply.

## Python and uv lifecycle

The initial Python backend is intentionally host-bound and wheel-only. It records:

```text
Python implementation and version
host platform and ABI
exact wheel URL
SHA-256
normalized dependency edges
uv version and native lock payload
```

It rejects source distributions, editable installs, mutable VCS references, unsupported local projects, credential-bearing URLs, and target interpreters whose ABI differs from the running Depviz process.

### Supported manifests

```text
requirements.in
requirements.txt
pyproject.toml [project].dependencies
selected [project.optional-dependencies]
selected [dependency-groups]
```

Select extras and dependency groups explicitly:

```bash
depviz resolve pyproject.toml \
  --resolver uv-lock \
  --python /usr/bin/python3.12 \
  --extra plot \
  --group test \
  --output python-resolution.json
```

An immutable direct wheel is supported when its requirement contains an exact SHA-256 fragment:

```text
example @ file:///artifacts/example-1.0-py3-none-any.whl#sha256=<64 hex characters>
```

### Resolve and plan

```bash
depviz resolve requirements.in \
  --resolver uv-lock \
  --python /usr/bin/python3.12 \
  --output python-resolution.json
```

For a new virtual environment:

```bash
depviz plan requirements.in \
  --resolver uv-lock \
  --inspector python-venv \
  --python /usr/bin/python3.12 \
  --empty \
  --output python-plan.json
```

Against an existing virtual environment:

```bash
depviz plan requirements.in \
  --resolver uv-lock \
  --inspector python-venv \
  --python /usr/bin/python3.12 \
  --prefix .venv \
  --output python-plan.json
```

### Lock and apply

```bash
depviz lock python-resolution.json \
  --provider python-exact-lock \
  --output python-lock.json

depviz apply python-lock.json \
  --provider python-exact-lock \
  --driver python-venv-driver \
  --python /usr/bin/python3.12 \
  --deployment .depviz/python-app
```

Apply creates a new virtual environment and invokes `uv pip sync` only against direct, SHA-256-pinned wheel URLs with index access disabled. It does not ask uv or pip to resolve packages again.

## Verify, promote, and roll back

### Conda candidate

```bash
depviz verify conda-lock.json \
  --provider conda-exact-lock \
  --verifier conda-prefix-verifier \
  --deployment .depviz/conda-app \
  --candidate <candidate-id>
```

### Python candidate

```bash
depviz verify python-lock.json \
  --provider python-exact-lock \
  --verifier python-venv-verifier \
  --python /usr/bin/python3.12 \
  --deployment .depviz/python-app \
  --candidate <candidate-id> \
  --probe-import requests
```

Python verification checks:

- runtime identity
- exact installed distribution set and versions
- direct artifact origins
- dependency metadata
- every installed file covered by wheel `RECORD` hashes
- configured import and command probes

Promotion reloads the archived lock and re-verifies under the deployment operation lock:

```bash
depviz promote \
  --provider python-exact-lock \
  --verifier python-venv-verifier \
  --python /usr/bin/python3.12 \
  --deployment .depviz/python-app \
  --candidate <candidate-id> \
  --probe-import requests
```

Rollback performs the same mandatory re-verification on the previous candidate:

```bash
depviz rollback \
  --provider python-exact-lock \
  --verifier python-venv-verifier \
  --python /usr/bin/python3.12 \
  --deployment .depviz/python-app
```

The environment should be consumed through:

```text
.depviz/python-app/current
```

not through an individual candidate path.

## Deployment status and maintenance

```bash
depviz status \
  --deployment .depviz/python-app \
  --deployment-kind managed-python-deployment
```

Check plugin contracts, backend executables, and optionally one deployment:

```bash
depviz doctor \
  --plugin depviz-python \
  --python /usr/bin/python3.12 \
  --uv-executable uv \
  --deployment .depviz/python-app
```

Garbage collection is a dry run unless `--execute` is present:

```bash
depviz gc --deployment .depviz/python-app --keep 3
depviz gc --deployment .depviz/python-app --keep 3 --execute
```

Garbage collection never removes:

- the current candidate
- the immediate rollback target
- a candidate involved in a pending switch

Candidate records and archived locks are retained as audit evidence after an environment directory is removed.

## Managed deployment layout

```text
my-environment/
├── current -> environments/<candidate-id>
├── environments/
│   ├── <candidate-id>/
│   └── <candidate-id>/
└── .depviz/
    ├── candidates/
    ├── locks/
    ├── verifications/
    ├── deployment.json
    ├── operation.lock
    └── pending-switch.json
```

Candidate building can occur independently, but metadata changes and pointer switches are serialized with a cross-process advisory lock. Interrupted pointer switches are recovered from the durable journal.

## Plugin API 2.0

The public API under `depviz.api` provides coarse lifecycle protocols:

- `HealthCheck`
- `ManifestLoader`
- `EnvironmentInspector`
- `Resolver`
- `LockProvider`
- `EnvironmentDriver`
- `Verifier`

Drivers and verifiers declare explicit `environment_kind` and `deployment_kind` values. Core rejects mismatched locks, drivers, verifiers, candidates, and deployment types before mutation.

Third-party plugins use standard Python entry points:

```toml
[project.entry-points."depviz.backends"]
example = "depviz_example.plugin:create_plugin"
```

```bash
depviz --list-plugins
```

The reusable contributor test kit is available from `depviz.testing`:

```python
from depviz.testing import BackendConformanceCase, run_backend_conformance_suite
```

It exercises plugin validation, health checks, resolution, lock round-tripping, isolated application, verification, promotion, rollback, and optional drift detection through public protocols only.

## Source layout

```text
src/depviz/
├── api/             public domain objects and protocols
├── core/            lifecycle orchestration and maintenance
├── analysis/        graph, diff, impact, and policy functions
├── infrastructure/  subprocess, durable storage, deployment state, and locks
├── plugins/         discovery, validation, and registry
├── builtin/         official manifest, Conda, and Python adapters
├── testing/         reusable plugin conformance helpers
├── cli/             parser, dispatch, handlers, rendering, and exit codes
├── schemas/         versioned persisted-document schemas
├── main.py          composition root
└── __main__.py      python -m depviz entry point
```

## Current limitations

- Atomic promotion currently requires POSIX symlinks.
- Conda and Python application are host-platform only.
- Python resolution currently requires the selected interpreter to match Depviz's running implementation, major/minor version, platform, and ABI.
- Python source distributions and source builds are intentionally unsupported.
- Depviz cannot prevent arbitrary external processes from editing candidate files. Mandatory pre-switch verification detects normal file drift before promotion or rollback.
- Authentication-bearing artifact URLs are not persisted. Private repository credentials require a separate provider design.
- R and compound multi-layer environments are not implemented yet.

## Development

```bash
make test
make lint
make format-check
make typecheck
make check
```

See:

- `docs/architecture.md`
- `docs/conda-resolver.md`
- `docs/python-uv-backend.md`
- `docs/planning-locking.md`
- `docs/deployment-lifecycle.md`
- `docs/plugin-conformance.md`
- `docs/maintenance.md`
- `CONTRIBUTING.md`

## License

MIT
