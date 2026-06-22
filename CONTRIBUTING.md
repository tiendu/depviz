# Contributing to Depviz

## Principles

Reliability and maintainability take priority over compact or clever code.

A contribution should prefer:

- explicit state over inferred state
- structured failures over empty results
- mature package-manager behavior over custom solving
- small protocols over inheritance trees
- isolated candidate environments over in-place mutation
- boring, testable control flow over abstraction for its own sake

## Development setup

```bash
make install
make check
```

`make check` runs tests, Ruff linting, Ruff formatting verification, and strict mypy.

## Source boundaries

New behavior belongs under `api`, `core`, `analysis`, `infrastructure`, `plugins`, `builtin`, or `cli`. The old flat modules are compatibility facades only.

`main.py` is the composition root. Do not place package-manager behavior, planning, rendering, or validation there. Core functions accept protocol implementations directly and must not depend on the concrete plugin registry.

Do not add empty architectural placeholders. Add a module only with a real contract, behavior, and tests.

## Backend plugin package

Third-party packages register through the `depviz.backends` entry-point group:

```toml
[project.entry-points."depviz.backends"]
example = "depviz_example.plugin:create_plugin"
```

The factory must return `depviz.api.BackendPlugin`:

```python
from depviz.api import BackendPlugin, Capability, PLUGIN_API_VERSION


def create_plugin() -> BackendPlugin:
    return BackendPlugin(
        name="depviz-example",
        plugin_version="0.1.0",
        api_version=PLUGIN_API_VERSION,
        capabilities=frozenset({Capability.LOAD_MANIFEST}),
        manifest_loaders=(ExampleManifestLoader(),),
    )
```

The plugin must declare exactly the capabilities represented by its lifecycle components. Registration rejects mismatches. Optional `HealthCheck` components do not advertise a lifecycle capability.

## Component rules

Each component:

- has a stable, non-empty `name`
- satisfies one public protocol
- returns public domain objects
- preserves package-manager-native data needed for exact application
- raises classified backend errors or returns structured diagnostics
- never converts tool absence, timeout, malformed output, or network failure into success

Environment drivers and verifiers must also declare matching `environment_kind` and `deployment_kind` values. Exact locks must carry the same identities in `LockArtifact.metadata`; core rejects mismatched combinations before mutation.

Health checks should be fast, bounded, non-mutating checks of required external executables and target runtimes. They return structured diagnostics and may raise classified backend errors.

## Manifest loaders

Manifest loaders must preserve all dependency-relevant input:

- version constraints
- package source
- markers
- extras/features
- indexes or channels
- includes and constraint files
- source location where available

Unsupported entries must produce an error diagnostic. Do not silently ignore them.

## Resolvers

Resolvers must delegate solving to an established solver. They must not recursively crawl package metadata and call that a resolution.

A resolution should retain:

- exact version
- build identifier where applicable
- platform
- source/index/channel
- artifact identity
- checksum where available
- solver and tool versions
- target configuration
- backend-native transaction or lock data

## Inspectors and lock providers

Environment inspectors must distinguish a missing environment, an invalid environment, incomplete package metadata, and a complete exact state. They must not infer success from missing records.

Lock providers must round-trip exact resolution identity and reject missing artifact information. Lock deserialization must validate content integrity rather than only parse JSON.

## Apply and verification implementations

Apply must consume an already approved exact lock or native transaction. It must not perform an unconstrained fresh resolution.

Apply must target a new candidate environment. Failure must leave the active environment unchanged. Drivers must validate that candidate paths remain inside their managed deployment and must provide deterministic cleanup through `discard()`.

Verifiers must compare the complete exact state represented by the lock. A name/version-only check is insufficient when the ecosystem has builds, sources, artifacts, checksums, platforms, or dependency metadata.

Promotion and rollback remain core responsibilities. Backend plugins contribute a `LockProvider`, `EnvironmentDriver`, and `Verifier`; they must not implement an alternative active-pointer or history model.

## Testing expectations

At minimum, backend tests should cover:

- missing external tool
- invalid manifest
- unsupported source type
- unsatisfiable request
- incomplete solver output
- malformed tool output
- timeout
- deterministic lock round-trip
- apply without re-resolution
- verification detecting drift
- cleanup after failure
- concurrent target mutation rejection
- post-verification drift detected again during promotion
- rollback refusing a drifted previous candidate
- archived lock tamper detection
- redaction of credentials in diagnostics

Use `depviz.plugins.validation.validate_plugin()` in plugin unit tests. Resolver plugins should also test their complete native transaction parser against representative outputs from every supported external tool family.

Contributors should additionally call the framework-neutral conformance suite:

```python
from depviz.testing import BackendConformanceCase, run_backend_conformance_suite
```

The shared suite exercises registration, health checks, resolution, lock round-tripping, isolated apply, exact verification, promotion, rollback, and optional drift detection through public protocols only. It supplements rather than replaces backend-specific tests.

## Compatibility

Plugin API compatibility is based on `PLUGIN_API_VERSION`, independently from the Depviz application version and plan/lock schema versions.

An incompatible API major version is rejected during registration. Depviz `0.8.0rc1` uses Plugin API `2.0`; v1 plugins must be updated to declare explicit environment and deployment kinds.
