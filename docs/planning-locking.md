# Planning and locking

## Exact current state

`CondaPrefixInspector` reads JSON records from `<prefix>/conda-meta`.

It rejects:

- missing prefix
- non-Conda directory
- empty package metadata
- malformed JSON
- missing name, version, build, platform, source, or artifact identity
- conflicting duplicate package records
- target-platform conflicts

Checksums are preserved where present. A missing checksum is a warning for installed state because planning can still compare exact installed version, build, and source. Lock creation is stricter and rejects an unhashed desired artifact.

The inspector does not persist complete raw package records. Its native payload contains prefix and record-file identity only, avoiding accidental private-registry credential retention.

## Package changes

The common diff model reports one package entry with a primary kind:

```text
install
remove
modify
unchanged
```

A modified package carries every changed aspect:

```text
version
build
source
artifact
checksum
platform
```

This means a package can simultaneously change version, build, source, and artifact without losing information to one display label.

## Version direction

Conda version semantics are richer than PEP 440. Depviz does not use Python package ordering as a substitute.

Upgrade or downgrade is classified only when both values are plain dotted numeric versions such as:

```text
1
1.2
3.11.9
```

Complex versions such as `1.0rc1`, epochs, local versions, or vendor suffixes are marked `unknown`. The exact old and new versions remain in the plan.

## Policies

Version `0.7.x` reports:

- package removal
- package downgrade when direction is unambiguous
- major numeric version upgrade
- source or channel change
- runtime package change
- incomplete artifact identity
- missing artifact checksum
- MD5-only artifact identity

Policy evaluation is deterministic and side-effect free.

The CLI can return a non-zero result based on findings:

```bash
--fail-on-policy never
--fail-on-policy error
--fail-on-policy warning
```

The plan is still serializable and inspectable when policy findings exist.

## Plan integrity

A plan records:

```text
manifest digest
current-state digest
resolution digest
native-transaction digest
target platform
target prefix identity
complete before state
complete desired state
operations
policy findings
preconditions
```

Canonical JSON is hashed to form `plan_id`. Reading a modified plan fails.

Plan files are written through atomic durable storage.

## Conda lock integrity

The built-in lock is a Depviz JSON document rather than a hand-built dependency graph.

Each artifact entry requires:

```text
name
version
build
platform
source
absolute URL
SHA-256
```

Legacy MD5-only Conda artifacts are rejected by default. They can be read or locked only when the operator supplies `--allow-weak-checksum` to that specific command. The override is deliberately not persisted as approval in the lock.

The provider refuses:

- incomplete resolutions
- empty environments
- non-Conda packages
- missing build, platform, source, URL, or checksum
- unsupported relative artifact locations
- malformed hash values
- embedded credentials
- query parameters or pre-existing URL fragments

The lock binds the full normalized resolution. On read, Depviz verifies the lock ID, resolution digest, and artifact entries before returning `LockedResolution`.

## Python lock integrity

The Python lock binds the exact interpreter implementation, version, platform, SOABI, selected wheel URL, and SHA-256 for every package. It rejects source distributions, missing hashes, unsupported URL schemes, embedded credentials, and artifact entries that disagree with the normalized resolution.

The Python lock is host-runtime-specific by design. It is not a universal multi-platform lock.

## Consumption in 0.7

An exact backend lock is the only package input accepted by an apply driver. The Conda driver converts its artifact entries to a checksum-bearing `@EXPLICIT` file. The Python driver converts its wheel entries to direct SHA-256 requirements and calls uv with index access disabled. Both create a new managed candidate without re-solving.

The lock is archived inside the deployment and re-read for mandatory verification during promotion and rollback. A candidate record binds both the lock ID and resolution digest, so a different lock cannot be substituted for the candidate later.
