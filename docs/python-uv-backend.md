# Python and uv backend

## Boundary

The built-in Python backend manages packages inside an isolated virtual environment. It does not provision or upgrade the Python runtime itself.

A resolution is bound to:

```text
implementation
full Python version
operating-system platform
architecture
SOABI
```

The first implementation requires that the selected interpreter match the Python runtime executing Depviz. This allows wheel compatibility to be selected from the running interpreter's authoritative tag set instead of approximating another interpreter's ABI.

## Resolution

The resolver synthesizes a temporary minimal project from normalized `Requirement` objects and asks `uv lock` to solve it. Ambient uv and pip configuration is removed. Indexes must be declared by the manifest or command context.

The native `uv.lock` is preserved in the resolution payload, but Depviz also normalizes one concrete host-compatible package set. For every selected package it requires:

```text
name
version
registry or immutable direct source
one compatible wheel URL
SHA-256
dependency names and markers
target runtime identity
```

Source distributions are rejected. Universal locks that select multiple versions of the same normalized package for one target are rejected rather than guessed.

## Direct wheels

A direct wheel requirement must use `https` or `file` and include an exact SHA-256 fragment:

```text
example @ file:///srv/wheels/example-1.0-py3-none-any.whl#sha256=<digest>
```

Credential-bearing URLs, query parameters, mutable VCS references, and unpinned local source trees are rejected.

## Exact lock

`depviz.python-lock.v1` contains:

```text
normalized resolution
resolution digest
interpreter identity
exact wheel URLs
SHA-256 for every wheel
content-derived lock ID
```

The reader recalculates the outer lock ID, embedded resolution digest, interpreter metadata, and artifact list. Any disagreement fails closed.

## Apply

The driver:

1. validates interpreter identity against the lock
2. creates a new managed candidate virtual environment
3. writes direct wheel requirements with `--hash=sha256:...`
4. runs `uv pip sync` with index access disabled
5. records the candidate without changing `current`

Apply never calls `uv lock` and never supplies package names for fresh resolution.

## Verification

The inspector runs the candidate interpreter with isolated mode and user-site disabled. It rejects:

```text
non-virtual environments
packages loaded outside the candidate
editable distributions
unreadable direct_url.json
missing or malformed RECORD files
RECORD paths escaping the environment
missing installed files
size or cryptographic hash mismatches
duplicate normalized distributions
```

The verifier compares runtime identity, exact distribution names and versions, direct artifact origins, and dependency metadata. It also supports explicit import and shell-free command probes.

Current uv versions may not preserve the source wheel SHA-256 in `direct_url.json`. The apply path already requires and verifies the lock hash before installation; post-install integrity is therefore established by verifying every hashed file in wheel `RECORD`, rather than pretending the original archive digest can always be recovered from installed metadata.

## Deliberate limitations

- no source distributions or local builds
- no editable installs
- no mutable Git references
- no cross-interpreter or cross-platform wheel selection
- no implicit pip or uv configuration
- no persisted credentials
- no runtime provisioning
