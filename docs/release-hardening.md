# Release hardening

R and composite-environment development are paused during the `0.7.x` hardening series.

The purpose of this series is not to add features. It is to turn every discovered failure mode into a permanent regression test and to make persistent state fail closed.

## Revision 0.7.1

The first revision establishes a deterministic and corruption-focused release gate.

Covered invariants include:

```text
successful persistent documents match their published schemas
unknown or missing persistent fields are rejected
future schema versions are rejected
persisted timestamps are timezone-aware ISO-8601 values
plan and lock IDs bind the complete unsigned document
failed atomic writes leave no temporary residue
process timeout terminates the complete spawned process group
operation locks are released after exceptions
repeated archive and state operations are idempotent
conflicting archived lock content is rejected
custom backend runners cannot force known credentials into Python resolver errors
CLI exit-code numbers remain stable
```

## Test groups

Run the complete local suite:

```bash
make test
```

Run only hardening regressions:

```bash
make test-hardening
```

Run the release gate:

```bash
make check-release
```

`check-release` runs linting, formatting verification, strict typing, and all tests not marked as requiring external network access.

## Compatibility policy during 0.7.x

- Current schema versions are written.
- Known v0.7.0 documents remain readable where compatibility is safe.
- Unknown future schema versions are rejected.
- Unknown fields are rejected rather than ignored.
- Archived locks are never rewritten automatically.
- R, remote deployment, credential providers, and new ecosystems remain out of scope.

## Completed revision line

The planned `0.7.x` hardening revisions are complete:

```text
0.7.1  deterministic state, schemas, cleanup, and redaction
0.7.2  tool-output compatibility and CI matrix
0.7.3  crash, concurrency, and filesystem failure injection
0.7.4  integrity defaults, permission boundaries, and security policy
```

No test suite removes operational risk permanently. Every reproduced defect must gain a regression test before it is considered fixed.

## Revision 0.7.2

The compatibility revision adds a stable tool-version parser, deterministic Conda-family transaction fixtures, optional real-uv checks, and CI across Python 3.11-3.13 on Linux and macOS.

Compatibility tests are available through:

```bash
make test-compatibility
```

Public-repository tests remain separate and network-marked.

## Revision 0.7.3

The failure-injection revision tests all durable switch boundaries:

```text
journal absent, pointer old, state old      -> old candidate remains current
journal present, pointer old, state old     -> journal is abandoned safely
journal present, pointer new, state old     -> new state is completed
journal present, pointer new, state new     -> journal is cleared
any unexplained combination                 -> fail closed
```

It also injects file and directory `fsync` failures, package-application interruption, and independent-process lock contention.

```bash
make test-failure-injection
```

## Revision 0.7.4

The security revision establishes the hardened baseline:

```text
SHA-256 required by default
legacy MD5 requires an explicit per-command override
persistent reads are bounded
state and lock symlinks are rejected
Depviz metadata is owner-only
unsafe writable deployment roots are rejected
process-lock metadata is bounded and private
doctor reports permission-boundary violations
```

Run these regressions with:

```bash
make test-security
```

The trust assumptions and reporting process are documented in `SECURITY.md`. R and composite-environment development remain paused while this baseline is dogfooded.
