# Depviz architecture

## Product boundary

Depviz owns lifecycle orchestration and safety policy:

```text
manifest
   -> inspect current state
   -> resolve desired state
   -> build immutable plan
   -> create exact lock
   -> apply to isolated candidate
   -> verify candidate
   -> promote or roll back complete environments
```

Backends adapt established package managers. They do not replace core planning, policy, candidate-state, verification-recording, promotion, rollback, or recovery semantics.

Version `0.8.0rc1` implements this lifecycle for managed Conda, Python, and mixed Conda-plus-pip environments.

## Dependency direction

```text
                    main.py
                       |
        +--------------+-----------------+
        v              v                 v
      cli          plugins         infrastructure
        |              |                 |
        +--------------+-----------------+
                       v
                      core
                       |
             +---------+---------+
             v                   v
           api                analysis
```

Built-in backends depend on `api` and receive shared facilities through `OperationContext`.

Rules:

- `api` imports no other Depviz layer.
- `analysis` contains package-manager-neutral pure functions.
- `core` depends on protocols and normalized models, not the plugin registry.
- `plugins` discovers and validates components but does not orchestrate operations.
- `infrastructure` implements commands, atomic storage, deployment records, and process locks.
- `builtin` contains official adapters using the same public contracts as external plugins.
- `cli` translates arguments into use-case calls and renders results.
- `main.py` only creates concrete services and dispatches the CLI.

## Composition root

`src/depviz/main.py` is inside the installed package, beside `api`, `core`, and `cli`. There is no repository-root `main.py`.

`ApplicationServices` contains only the concrete registry and command runner. Depviz deliberately has no giant `DepvizApplication` object.

Both entry points use the same composition root:

```bash
depviz
python -m depviz
```

## Public model

The public API distinguishes:

```text
Requirement          user intent
ResolvedPackage      exact package identity
EnvironmentState     exact installed state
Resolution           exact desired state
PackageChange        normalized before/after change
ChangePlan           content-bound approval artifact
LockArtifact         exact backend lock bytes
CandidateEnvironment isolated backend environment
VerificationReport   exact expected/observed state result
PromotionRecord      managed-pointer transition result
RollbackResult       managed-pointer rollback result
```

`depviz.analysis.graph.Package` is a graph-view node only. It must never be used for installation, locking, or exact environment comparison.

Every exact package can preserve:

```text
ecosystem
name
version
build
platform
source
artifact
checksum
dependencies
```

## Protocols

The public extension surface remains coarse:

- `HealthCheck`
- `ManifestLoader`
- `EnvironmentInspector`
- `Resolver`
- `LockProvider`
- `EnvironmentDriver`
- `Verifier`

Capability negotiation is explicit. Guarantee capabilities such as `HASH_LOCKING` require their underlying operation capability.

Drivers and verifiers declare explicit `environment_kind` and `deployment_kind` values. Locks carry the same identities. Core rejects cross-backend combinations before creating or verifying a candidate.

Promotion and rollback are not backend protocols. They are core operations over complete managed candidates. A backend contributes the exact lock, driver, and verifier needed by those operations.

The mixed backend is a concrete composition inside one candidate prefix:

```text
CondaPipResolver
├── CondaDryRunResolver
└── uv wheel resolution bound to the resolved Conda Python

CondaPipPrefixDriver
├── exact Conda prefix application
└── exact pip wheel overlay into that prefix
```

The compound behavior remains backend-owned. Core planning, deployment records, verification recording, promotion, rollback, and recovery are unchanged. This proves composition without introducing a generic layer graph before R or another real backend requires one.

## Core versus registry

Core use cases accept protocol instances directly:

```text
resolve_intent(intent, resolver, target, current, context)
create_lock(resolution, provider, context)
apply_locked_environment(lock, driver, deployment, context)
verify_candidate_environment(lock, verifier, deployment, candidate, policy, context)
promote_candidate(deployment, candidate, provider, verifier, policy, context)
rollback_deployment(deployment, provider, verifier, policy, context)
```

The CLI or another composition layer finds components in the registry. Core tests can therefore use small fake implementations without constructing entry points or global registries.

## Built-in Conda package

```text
builtin/conda/
├── plugin.py       descriptor and declared capabilities
├── tooling.py      shared executable configuration and isolation
├── resolver.py     dry-run command construction and orchestration
├── transaction.py  native JSON parsing and normalization
├── security.py     credential sanitation
├── inspector.py    exact installed conda-meta state
├── locking.py      exact lock creation and validation
├── driver.py       candidate creation and explicit installation
└── verifier.py     exact state comparison and smoke probes
```

This is one reference shape for third-party backend contributors.

## Built-in Python package

```text
builtin/python/
├── plugin.py       descriptor and declared capabilities
├── health.py       uv and interpreter health check
├── tooling.py      controlled uv and interpreter configuration
├── resolver.py     uv project solve and exact wheel selection
├── inspector.py    virtual-environment metadata and RECORD verification
├── locking.py      interpreter-bound exact wheel lock
├── driver.py       isolated venv creation and hash-required wheel sync
└── verifier.py     exact package, file, import, and command verification
```

The Python backend deliberately does not force Python concepts into Conda fields. Its runtime identity is implementation, version, platform, and ABI; its exact package artifact is one compatible wheel plus SHA-256.

## Plans

A plan is created from:

```text
exact EnvironmentState
          +
exact Resolution
          v
package-neutral diff
          v
policy findings
          v
content-bound ChangePlan
```

Plans contain complete before and after state plus digests for:

- manifest bytes
- inspected current state
- desired resolution
- backend-native transaction

The plan ID is a SHA-256 digest over canonical plan content excluding only the ID field itself. Deserialization recalculates and verifies it.

Policies report findings. They do not mutate the plan.

## Locks

A lock provider owns ecosystem-specific lock semantics. The built-in Conda provider requires exact artifact URLs and checksums, stores the normalized resolution, and validates:

- outer lock ID
- embedded resolution digest
- artifact-list equality with normalized packages
- checksum syntax and length
- supported absolute URL schemes
- absence of embedded credentials, query parameters, and fragments

No graph or display value is used to reconstruct a lock.

## Managed deployment state

A deployment root contains immutable candidate prefixes and durable control metadata:

```text
deployment/
├── current -> environments/<candidate-id>
├── environments/<candidate-id>/
└── .depviz/
    ├── candidates/<candidate-id>.json
    ├── locks/sha256-<digest>.json
    ├── verifications/<candidate-id>.json
    ├── deployment.json
    ├── pending-switch.json
    └── operation.lock
```

The candidate record binds:

```text
candidate ID
relative path
environment kind
exact lock ID and format
resolution digest
lifecycle status
verification digests
```

Candidate IDs and relative paths are validated before use. Candidate paths cannot escape the managed deployment.

## Apply

Apply is split into two parts:

```text
core application orchestration
       +
backend EnvironmentDriver
```

Core:

1. validates deployment kind
2. archives the exact lock by content ID
3. reserves a candidate and writes its durable record
4. calls the driver
5. records success or failure
6. removes a failed candidate unless explicitly retained

The Conda driver:

1. requires a lock for the host platform
2. converts locked artifacts to an `@EXPLICIT` file
3. executes Conda or Micromamba without shell interpolation
4. does not invoke dependency resolution
5. requires successful JSON output and resulting `conda-meta`

Apply never changes `current`.

## Verification

Standalone verification holds the deployment operation lock while it:

1. validates candidate and lock identity
2. compares exact installed state with the normalized lock
3. runs configured direct command probes
4. writes a content-bound verification report
5. records verified or verification-failed status

Verification reports contain expected and observed package-state digests. A report is evidence about that point in time, not permanent authorization to promote.

## Promotion and rollback

Promotion and rollback always re-verify while holding the deployment operation lock. They do not trust a previous `verified` status alone.

Before switching, core:

1. reads the archived lock referenced by the candidate record
2. validates the lock through its provider
3. validates candidate-to-lock identity
4. runs the backend verifier
5. records the fresh report
6. refuses the switch on any mismatch or failed probe

A successful switch uses a durable journal:

```text
write pending switch
   -> atomically replace current symlink
   -> write deployment state
   -> remove pending switch
```

On the next managed operation, an interrupted switch is completed or rejected based on the journal, state, and symlink. Depviz never guesses when those records disagree.

Rollback uses deployment history as a stack and switches complete environments. It does not calculate reverse package operations.

## Plugin conformance

`depviz.testing.run_backend_conformance_suite` executes a contributed backend only through public protocols. It validates plugin registration and health checks, performs exact resolution and lock round-tripping, builds two isolated candidates, verifies and promotes them, rolls back, and can confirm backend-specific drift detection.

The conformance suite is intentionally framework-neutral. Contributors can call it from pytest, unittest, or another harness.

## Maintenance operations

`doctor` validates plugin contracts and optional backend health checks. Against a deployment it also checks the pending-switch journal, current pointer, deployment state, candidate records, candidate directories, and archived locks.

`gc` is conservative and dry-run by default. It protects the current candidate, the immediate rollback target, and all pending-switch state. Executed collection removes only candidate environment directories; durable records and exact archived locks remain available for audit.

## Concurrency model

`operation.lock` is a cross-process advisory lock with owner metadata. It serializes:

- candidate metadata allocation
- verification recording
- promotion
- rollback
- interrupted-switch recovery

The potentially long package installation occurs outside the global deployment lock so independent candidates can be built concurrently. A candidate cannot be promoted until installation has completed and its status permits exact verification.

The lock protects cooperating Depviz processes. It cannot prevent arbitrary external processes from editing environment files, so pre-switch re-verification remains mandatory.

## Atomic storage

Plans, locks, candidate records, verification reports, state, and journals are written using:

```text
temporary file
   -> flush
   -> fsync file
   -> atomic replace
   -> fsync parent directory
```

The active pointer is replaced atomically with a relative POSIX symlink. Windows promotion is intentionally not claimed yet.

## Compatibility facades

Pre-0.5 imports such as `depviz.models`, `depviz.analyzer`, `depviz.fetchers`, `depviz.parsers`, `depviz.renderer`, and `depviz.serialization` remain as re-export facades.

They contain no new behavior and can be removed only through a deliberate compatibility release.

## Next safety boundaries

The next practical additions are likely:

- retention policy and disk-space accounting
- credential-provider integration for private indexes and channels
- stronger candidate filesystem immutability where supported
- compound Conda plus R environments using the same lifecycle contracts
