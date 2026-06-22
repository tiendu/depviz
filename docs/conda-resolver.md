# Conda dry-run resolver

## Purpose

The built-in `conda-dry-run` component asks an established Conda-family solver for the complete desired environment. It does not crawl packages recursively and does not install anything.

```text
DependencyIntent
      |
      v
Conda or Micromamba create --dry-run --json
      |
      +---- normalized exact package set
      |
      +---- sanitized native transaction
```

## Supported input

The first implementation accepts only `Requirement(ecosystem="conda")` entries.

Supported information includes:

- Conda package names
- version and build MatchSpecs
- channel-qualified MatchSpecs
- ordered manifest channels
- target platform
- offline mode
- explicit Conda solver selection when the `conda` executable is used

A mixed `environment.yml` containing a `pip:` section fails before the external tool runs. Partial multi-ecosystem resolution would be unsafe because it could be mistaken for a complete environment.

## Channel isolation

The resolver does not inherit channels from a user's normal configuration.

It:

1. requires at least one explicit channel
2. passes `--override-channels`
3. uses strict channel priority
4. points `CONDARC` and `MAMBARC` at an empty temporary file
5. treats `nodefaults` as a policy marker and does not pass it as a channel

Channel-qualified requirements are added to the effective explicit channel list.

## Transaction parsing

Conda-family JSON has varied between tool and release versions. Exact package information may be divided between transaction sections.

Depviz uses `actions.LINK` as the package set and merges matching `actions.FETCH` records by:

```text
name + version + build
```

This recovers dependency metadata, hashes, URLs, and filenames when they are omitted from `LINK`.

Each normalized package preserves:

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

A package without a version, build, source, or artifact identity is rejected. Missing dependency-edge metadata produces a warning because the exact package set remains known while graph analysis may be incomplete.

## Native payload

The native payload schema is:

```text
depviz.conda.dry-run.v1
```

It records:

- selected tool
- reported tool version
- sanitized command arguments
- explicit channel order
- target platform
- selected solver mode
- offline mode
- complete sanitized JSON transaction

Temporary paths are replaced before persistence. Basic URL user-information credentials are removed. A future credential provider will handle more complex private-registry authentication without storing secrets in plans or locks.

## Failure rules

The resolver fails closed for:

- missing executable
- invalid tool configuration
- manifest errors
- mixed ecosystems
- absent explicit channels
- unsupported constraints, markers, or extras
- timeout
- truncated stdout or stderr
- malformed JSON
- non-success solver result
- missing transaction sections
- empty package set for non-empty requirements
- conflicting package records
- incomplete exact package identity

No failure becomes an empty successful resolution.

## Current limitations

Depviz now provides a separate exact prefix inspector and exact lock provider. The resolver itself still does not:

- apply the native transaction
- verify package state
- model target virtual packages such as CUDA or glibc explicitly
- promise cross-platform reproducibility
- resolve the `pip:` section of a Conda manifest

These limitations are intentional. They keep the first resolver exact within its declared boundary.
