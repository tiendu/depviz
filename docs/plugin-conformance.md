# Backend plugin conformance

## Plugin API 2.0

A backend plugin is a descriptor containing coarse components:

```text
HealthCheck             optional toolchain readiness checks
ManifestLoader          user intent parsing
EnvironmentInspector    exact installed state
Resolver                exact desired state
LockProvider            immutable backend lock
EnvironmentDriver       isolated candidate creation and apply
Verifier                exact candidate verification
```

Drivers and verifiers must declare:

```python
environment_kind = "example-environment"
deployment_kind = "managed-example-deployment"
```

A lock must publish matching values in its metadata. Core checks these identities before apply and verification, preventing accidental combinations such as a Python lock with a Conda driver.

## Reusable suite

```python
from depviz.testing import BackendConformanceCase, run_backend_conformance_suite

result = run_backend_conformance_suite(
    BackendConformanceCase(
        plugin=create_plugin(),
        resolver="example-resolver",
        lock_provider="example-lock",
        environment_driver="example-driver",
        verifier="example-verifier",
        intent=intent,
        target=target,
        deployment=deployment,
        context=context,
        policy=policy,
        tamper=tamper_candidate,
    ),
    work_directory=tmp_path,
)
```

The suite validates:

1. plugin registration and component protocols
2. optional backend health checks
3. complete, non-empty resolution
4. exact lock round-trip without normalized-state loss
5. lock, driver, verifier, and deployment identity agreement
6. candidate construction without changing `current`
7. exact verification with matching state digests
8. promotion of a verified candidate
9. construction and promotion of a second candidate
10. whole-environment rollback
11. optional post-apply drift detection

Backend-specific tests are still required for command construction, native payload parsing, failure classification, credential handling, and package-manager edge cases.

## Compatibility

Plugin API major versions are strict. Depviz `0.7.x` uses Plugin API `2.0`. A v1 plugin is rejected during registration because the driver/verifier identity contract changed.

Application versions, plugin API versions, plan schemas, and backend lock schemas are versioned independently.
