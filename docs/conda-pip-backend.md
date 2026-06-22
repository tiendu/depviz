# Conda and pip backend

The `depviz-conda-pip` backend manages one Conda prefix containing an exact pip wheel overlay.

It exists for the common `environment.yml` shape:

```yaml
channels:
  - conda-forge
  - bioconda
  - nodefaults

dependencies:
  - python=3.12
  - pip
  - samtools
  - pip:
      - pydantic>=2
      - polars
```

## Lifecycle

```text
environment.yml
    |
    +-- Conda requirements -> complete Conda dry-run solve
    |                           |
    |                           +-- exact Python runtime
    |
    +-- pip requirements -----> uv wheel solve for that runtime and platform
                                |
                                v
                       compound resolution
                                |
                       compound exact lock
                                |
                  isolated Conda candidate prefix
                                |
                    exact pip wheel overlay
                                |
                     combined verification
                                |
                    promote or roll back prefix
```

The active environment is never modified in place.

## Commands

Automatic backend selection is the default:

```bash
depviz resolve environment.yml \
  --tool micromamba \
  --platform osx-arm64 \
  --output resolution.json

depviz lock resolution.json --output lock.json

depviz apply lock.json --deployment .depviz/project
```

The explicit component names are:

```text
resolver          conda-pip
inspector         conda-pip-prefix
lock provider     conda-pip-exact-lock
driver            conda-pip-prefix-driver
verifier          conda-pip-prefix-verifier
deployment kind   managed-conda-pip-deployment
```

## Resolution guarantees

The backend:

1. Splits the manifest into Conda and PyPI requirements.
2. Performs one complete Conda environment solve.
3. Requires the solve to contain exactly one Python runtime and an explicit `pip` package.
4. Resolves the pip section to compatible wheels for the selected Conda Python version and target platform.
5. Requires one exact wheel URL and SHA-256 for every pip package.
6. Preserves both native child resolutions in the combined resolution payload.

Resolution currently supports these Conda target mappings:

```text
osx-arm64       aarch64-apple-darwin
osx-64          x86_64-apple-darwin
linux-64        x86_64-unknown-linux-gnu
linux-aarch64   aarch64-unknown-linux-gnu
win-64          x86_64-pc-windows-msvc
```

Managed promotion still requires the POSIX deployment semantics documented by Depviz; listing a Windows resolution target does not imply Windows promotion support.

## Ownership policy

A package requested directly through both Conda and pip is rejected. The manifest must choose one direct owner.

Transitive overlap can still occur. The lock records those names using a `pip-last` policy:

```text
Conda installs the base prefix
pip overlays the exact locked wheel
verification treats that wheel as the final Python distribution owner
```

This mirrors the install order used by Conda environment files while keeping the final ownership explicit. Avoid overlap when a package can be managed entirely by one layer.

## Lock and apply

The lock format is:

```text
depviz.conda-pip-lock.v1
```

It contains:

- the complete combined resolution
- one validated exact Conda lock
- one validated exact Python wheel lock
- the target Python runtime binding
- the ownership map
- a content-derived lock ID

Apply does not resolve dependencies. It:

1. Creates a new candidate prefix.
2. Installs the Conda `@EXPLICIT` artifact list.
3. Validates the candidate Python runtime.
4. Installs exact wheel URLs using uv with dependency resolution and index access disabled.
5. Records the candidate for verification.

Any failure discards the incomplete candidate unless diagnostic retention was explicitly requested.

## Verification

Combined verification checks:

- exact Conda package versions, builds, platforms, channels, artifacts, and checksums
- exact pip distribution versions and direct artifact origins
- the Conda Python implementation and version bound by the lock
- every pip-installed file represented by wheel `RECORD`, including hashes and sizes
- configured import probes
- configured shell-free command probes

Promotion and rollback reload the archived compound lock and repeat verification under the deployment operation lock immediately before switching.

## Deliberate limitations

The backend rejects or does not yet support:

- source distributions
- editable or local source packages
- mutable VCS dependencies
- unhashable wheel artifacts
- direct package ownership in both layers
- general multi-layer composition beyond one Conda prefix and one pip overlay
- R or `renv` libraries
- Windows managed promotion
- implicit ambient package-manager configuration

These limits are intentional. Depviz does not describe a mixed environment as exact unless both layers can be locked and verified as one candidate.
