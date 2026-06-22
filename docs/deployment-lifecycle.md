# Managed deployment lifecycle

## Why Depviz does not update an active prefix

In-place package changes are difficult to recover from. A process may fail after unlinking old artifacts but before linking all new ones, and reconstructing the exact previous state can require network access or unavailable builds.

Depviz instead treats a complete environment as the unit of change:

```text
exact lock
   -> isolated candidate
   -> exact verification
   -> atomic pointer switch
```

The previous candidate remains available for rollback.

## Lifecycle states

A candidate record has one of these states:

```text
created
applied
verified
verification-failed
failed
removed
```

Normal transitions are:

```text
created -> applied -> verified
    |          |
    v          v
  failed   verification-failed
removed
```

A candidate in `verification-failed` can be verified again after diagnosis. A failed installation cannot be promoted. A `removed` record remains as audit evidence after garbage collection deletes its environment directory.

Promotion does not rely only on the stored state. It performs another exact verification immediately before switching.

## Apply from an exact lock

```bash
depviz apply depviz-lock.json \
  --deployment /srv/depviz/my-tool
```

The lock is archived under `.depviz/locks` before installation. Candidate metadata references the archived lock by its content-derived ID.

The Conda driver creates an `@EXPLICIT` file from the lock. The Python driver creates a virtual environment and synchronizes only direct SHA-256-pinned wheel URLs with index access disabled. No unconstrained dependency solve occurs during either operation.

Apply leaves the deployment pointer untouched.

## Verification

```bash
depviz verify depviz-lock.json \
  --deployment /srv/depviz/my-tool \
  --candidate <candidate-id>
```

The Conda verifier reads authoritative `conda-meta` records. The Python verifier inspects isolated distribution metadata and validates every installed file represented by wheel `RECORD`. Both compare the complete normalized package set with the lock rather than only package names and versions.

Optional probes are argument vectors parsed by the CLI and run without a shell. The candidate's `bin` or `Scripts` directory is prepended to `PATH`, and `CONDA_PREFIX` is set to the candidate.

A verification report records:

```text
candidate ID
lock ID
pass/fail
expected state digest
observed state digest
diagnostics
report ID
```

## Promotion

```bash
depviz promote \
  --deployment /srv/depviz/my-tool \
  --candidate <candidate-id>
```

Promotion uses the lock archived by apply, not a new path supplied by the caller. This prevents a different lock from being substituted during approval.

While holding the deployment operation lock, promotion re-verifies the package state and any requested probes. Only then does it atomically replace `current`.

Consumers should always use:

```text
/srv/depviz/my-tool/current
```

A process that resolves the symlink once may continue using the previous candidate until restarted. Depviz changes the pointer; it does not forcibly restart consumers.

## Rollback

```bash
depviz rollback --deployment /srv/depviz/my-tool
```

Rollback identifies the previous candidate from deployment history. It reloads that candidate's archived lock and verifies the candidate again before switching.

If the old candidate has drifted or its archived lock is corrupt, rollback fails and leaves the current pointer unchanged.

## Crash recovery

A pointer switch is journaled as:

```text
from candidate
to candidate
next deployment state
operation type
```

Possible interruption points are handled as follows:

- journal written, pointer unchanged: cancel the pending switch
- pointer changed, state old: complete the state update
- pointer and state already new: remove the stale journal
- records disagree in any other way: fail closed

The journal is not a substitute for filesystem atomicity. It makes the metadata transition recoverable around the atomic symlink replacement.

## Concurrency

A deployment-wide advisory lock serializes state-changing operations. Candidate installation itself occurs outside that lock so separate candidate builds do not block each other for their entire download and install duration.

The lock coordinates Depviz processes only. Administrators should treat candidate directories and `.depviz` metadata as managed data and avoid modifying them directly.

## Operational limitations

- Promotion currently requires POSIX symlinks.
- Candidate pruning is explicit and dry-run by default through `depviz gc`; there is no automatic pruning.
- Disk capacity must accommodate the candidate and at least one rollback environment.
- External processes can still mutate files. Mandatory pre-switch verification detects ordinary drift but cannot make the filesystem intrinsically immutable.
- Private artifact authentication is not persisted in lock URLs.
