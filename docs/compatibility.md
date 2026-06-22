# Package-manager compatibility

Depviz separates compatibility validation into three tiers.

## Deterministic fixtures

These tests run on every commit and cover the accepted JSON and metadata vocabulary for supported package-manager families. They do not require network access and are the primary regression boundary for upstream output changes.

```bash
make test-compatibility
```

## Real local tools

When uv, Conda, or Micromamba is installed, compatibility tests query the real executable and exercise local, immutable fixtures. A missing tool causes only the relevant test to skip.

The supported Python matrix is 3.11, 3.12, and 3.13. Linux and macOS are tested in CI. Atomic promotion remains POSIX-symlink based.

## Network compatibility

Public-repository solves are intentionally marked `network` and are excluded from the normal release gate. They run on a scheduled workflow so transient repository failures do not make local validation unreliable.

## Policy

- Unknown output shapes fail closed.
- Version banners must contain an unambiguous dotted numeric version.
- Support claims follow the tested matrix, not unverified assumptions.
- A newly reproduced upstream incompatibility must gain a deterministic fixture before it is considered fixed.
