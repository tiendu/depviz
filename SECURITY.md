# Security policy

## Supported versions

Depviz is pre-1.0 software. Security fixes are provided for the latest released revision only. Older revisions should be upgraded before reporting a defect against current behavior.

Version `0.8.0rc1` is a release candidate for controlled local environments. It is not certified for regulated, clinical, multi-tenant, or hostile-host deployments.

## Reporting a vulnerability

Use the repository's private vulnerability-reporting or security-advisory feature when available. Include:

- the Depviz version
- operating system and filesystem
- package-manager and runtime versions
- the affected command and deployment state
- a minimal reproduction
- whether credentials, lock integrity, promotion, rollback, or path isolation are affected

Do not place live credentials, private repository tokens, or exploitable deployment details in a public issue. If private reporting is unavailable, open a minimal public issue requesting a private contact channel without including the sensitive details.

## Trust model

Depviz protects managed deployments against accidental corruption, interrupted operations, malformed persistent state, package drift, and untrusted package-manager output within its documented boundaries.

Depviz assumes:

- the local operating-system account running Depviz is trusted
- the host kernel, Python runtime, package-manager executable, and filesystem are trusted
- deployment roots are on a local filesystem with reliable atomic rename and POSIX symlink semantics
- another process with the same account or root privileges is not actively rewriting files during an operation
- external package repositories and artifacts are trusted only to the extent established by their persisted cryptographic hashes

Depviz does not sandbox package installation scripts or package import code. Installing or probing a malicious package can execute package-controlled code through the underlying package manager or runtime.

## Integrity policy

- Python wheel locks require SHA-256.
- Conda locks require SHA-256 by default.
- Legacy MD5-only Conda artifacts require `--allow-weak-checksum` at lock creation and each operation that consumes the lock.
- The weak-checksum override is not persisted as approval inside the lock.
- Plans, locks, candidate records, deployment state, and switch journals carry schema and integrity validation.
- Persistent document reads are size-bounded and reject final symlinks and non-regular files.
- Depviz-owned state files are written with owner-only permissions on POSIX systems.

MD5 compatibility protects only against ordinary transfer corruption. It is not a secure artifact-authentication mechanism.

## Filesystem boundaries

Supported promotion currently relies on local POSIX symlinks and atomic rename behavior. Network filesystems, shared writable directories, container overlay edge cases, and Windows pointer semantics are not supported as equivalent safety environments.

Depviz rejects:

- symlink deployment roots
- group/world-writable deployment roots and managed metadata directories on POSIX systems
- symlink state documents, archived locks, and process-lock paths
- candidate paths that escape their managed deployment

## Credentials

Depviz removes known credentials from command output and persistent package-manager payloads and rejects credential-bearing persisted artifact URLs. Credentials supplied at runtime remain the responsibility of the external package manager and the invoking environment.

Do not commit runtime credentials, private index configuration, or authenticated temporary URLs into manifests, plans, locks, logs, or bug reports.

## Explicit non-goals

The current release does not claim protection against:

- a malicious root user or compromised host
- malicious package installation or import-time code
- package-repository compromise when only a legacy MD5 digest is available
- TOCTOU attacks by another process with the same filesystem privileges
- remote deployment or multi-host coordination
- NFS or other filesystems without the required atomicity guarantees
- regulated or clinical validation requirements
