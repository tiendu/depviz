# Changelog

## 0.8.0rc3

Cross-platform test-suite fix release candidate.

- Fixed mixed Conda+pip lifecycle tests to derive the host Conda platform instead of hardcoding `linux-64`.
- Preserved the production cross-platform apply guard; only the tests were incorrect on `osx-arm64`.
- Added regression coverage ensuring generated Conda records, resolution targets, and CLI apply targets agree with the actual host platform.

## 0.8.0rc2

Mamba adapter bug-fix release candidate.

- Fixed full `mamba` execution to use Conda-compatible `--subdir`, `--no-default-packages`, and `--no-pin` flags instead of Micromamba's standalone `--platform` mode.
- Stopped overriding `MAMBA_ROOT_PREFIX` for full Mamba and Conda. Only standalone Micromamba now receives an isolated temporary root prefix.
- Applied the same isolation and pinning rules to exact-lock installation, not only dry-run resolution.
- Improved generic libmamba `unsupported request` failures with tool version, target platform, and actionable fallback commands.
- Added regression coverage for Mamba resolve/apply command construction and tool-specific environment isolation.

## 0.8.0rc1

Mixed Conda and pip environment release candidate. This release fixes the real-world case where a Conda `environment.yml` contains a `pip:` subsection and adds a push-ready implementation without resuming R work.

- Added the `conda-pip` resolver, which splits one mixed manifest into Conda and PyPI intents, solves the complete Conda environment first, binds the Python wheel solve to the exact resolved Conda Python version and target platform, and produces one combined resolution.
- Added the `depviz.conda-pip-lock.v1` compound lock containing validated child Conda and Python exact locks, a full combined resolution digest, and an explicit package-ownership map.
- Added isolated mixed candidate application: exact Conda artifacts are installed first, then exact SHA-256-pinned wheels are overlaid into that same Conda prefix without dependency re-resolution.
- Added mixed-prefix inspection and verification, including Conda package identity, pip/uv-owned distribution identity, Python runtime binding, wheel `RECORD` validation, import probes, and command probes.
- Added atomic promotion and rollback of the complete mixed prefix through the existing managed-deployment journal and re-verification flow.
- Added automatic component selection. The CLI now chooses the Conda, Python, or mixed resolver, lock provider, driver, verifier, and inspector when the requested component is `auto`, which is now the default.
- Added direct `mamba` executable support alongside `micromamba` and `conda`; `conda --solver libmamba` remains supported.
- Replaced the generic mixed-ecosystem rejection with an actionable message directing explicit Conda-only resolution to `--resolver conda-pip` or automatic selection.
- Added the `conda-pip-lock-v1` JSON Schema and schema-contract coverage for generated compound locks.
- Added lifecycle and CLI integration tests covering mixed resolution, locking, apply, verification, promotion, ownership collisions, and mamba command construction.
- Refactored Python prefix inspection into a shared implementation usable by both virtual environments and Python installations embedded inside Conda prefixes.

The mixed backend intentionally uses a `pip-last` ownership policy. Packages requested directly in both Conda and pip fail resolution; transitive overlaps are recorded and pip becomes the verified final owner. R and general multi-layer composition remain out of scope for this release candidate.

## 0.7.4

Security and integrity hardening revision. This completes the planned `0.7.x` hardening line; no R, composite-environment, or new ecosystem support was added.

- Changed Conda exact locking to require SHA-256 artifact identity by default. Legacy MD5-only locks require the explicit `--allow-weak-checksum` override on lock creation and every later lock-consuming operation.
- Added bounded, regular-file-only readers for resolutions, plans, locks, deployment documents, inspection caches, and Conda package records. Oversized, non-UTF-8, non-regular, and final-symlink inputs now fail closed.
- Hardened durable writes with owner-only `0600` file permissions, owner-only managed metadata directories, final-symlink rejection, and directory durability checks.
- Reject group/world-writable deployment roots and managed metadata directories on POSIX systems. `depviz doctor` now reports these trust-boundary violations.
- Hardened process locks with owner-only permissions, bounded owner metadata, and rejection of symlink lock files and symlinked lock directories.
- Added security regressions for weak-checksum policy, permission boundaries, symlink attacks, oversized documents, archived-lock size limits, and CLI override behavior.
- Added `SECURITY.md` documenting supported versions, reporting, checksum policy, trust assumptions, filesystem boundaries, and explicit non-goals.
- Centralized the package and built-in plugin release version and added a regression test preventing wheel/plugin version drift.
- Stabilized the process-tree timeout test by obtaining the spawned child identity through bounded command output instead of racing a temporary file.

Version `0.7.4` remains a beta for controlled local deployments. It is the hardened baseline for dogfooding; R development remains paused until the compatibility and failure-injection workflows have accumulated real release experience.

## 0.7.3

Crash, concurrency, and filesystem-failure hardening revision. No new package ecosystem or R/composite support was added.

- Added failure injection at every durable deployment-switch boundary: before the journal, after the journal, after pointer replacement, and after state persistence.
- Added recovery assertions proving an interrupted switch either remains on the old candidate or completes the fully journaled new state; ambiguous states continue to fail closed.
- Added independent-process lock contention tests and verified lock ownership is replaced cleanly after the holder exits.
- Added simulated file and directory `fsync` failures proving atomic writers preserve the previous document or expose only the complete replacement, never partial bytes.
- Added interruption coverage proving `KeyboardInterrupt` marks a candidate failed, removes the incomplete environment, and never changes the active pointer.
- Stabilized the process-group timeout regression by allowing the child process to publish its identity before termination.
- Added a dedicated `make test-failure-injection` gate and documented the switch recovery matrix.

The lifecycle remains limited to managed local filesystems with POSIX symlink promotion. R development remains halted.

## 0.7.2

Compatibility hardening revision. No new package ecosystem or R/composite support was added.

- Added a shared, fail-closed tool-version parser used by uv, Conda, and Micromamba adapters; decorated version banners no longer produce arbitrary trailing tokens.
- Added fixture-based compatibility tests for current and legacy Conda/libmamba/Micromamba transaction record vocabularies.
- Added an optional real-uv compatibility test against the active Python interpreter without requiring package-index network access.
- Added explicit `compatibility`, `failure_injection`, and `security` pytest groups and dedicated Make targets.
- Added Linux/macOS and Python 3.11-3.13 CI coverage plus a scheduled package-manager compatibility matrix for pinned and latest uv, Conda, and Micromamba installations.
- Added compatibility documentation defining deterministic, real-tool, and network test tiers.

This revision remains a beta and does not broaden the supported dependency syntax. R development remains halted.

## 0.7.1

Release-hardening revision. No new package ecosystem or R/composite support was added.

- Added a dedicated hardening test group covering persistent-document corruption, deterministic identity, atomic-write cleanup, process-group termination, stable exit codes, and secret redaction.
- Expanded the suite from 103 to 135 tests.
- Added JSON Schema validation of documents generated by the real resolution, planning, lock, candidate, deployment, pending-switch, and verification writers.
- Added the previously missing pending-switch v1 schema and made new pending-switch writes always include a timezone-aware timestamp.
- Added regression tests proving plan and lock IDs remain hashes of the complete unsigned document, including creation metadata.
- Added strict top-level field validation for resolutions, plans, locks, candidate records, deployment state, and pending-switch journals. Unknown, missing, malformed, and future-version fields now fail closed.
- Added timezone-aware timestamp validation for persisted plans, locks, candidate records, deployment state, and pending switches.
- Added a shared backend-neutral redaction module and hardened the Python resolver so secrets are removed even when a custom command runner fails to redact its output.
- Fixed candidate reconstruction during verification to preserve the verifier's real deployment kind rather than using a generic placeholder.
- Added regression coverage proving timeout termination kills spawned child processes and that failed atomic writes leave no temporary residue.
- Added `make test-hardening` and `make check-release` release gates.
- Added `jsonschema` as a development-only dependency for schema contract tests.

This remains a beta release intended for controlled local deployments. R development remains halted while the hardening revision series continues.

## 0.7.0

- Added a complete Python virtual-environment backend through uv: exact inspection, host-bound resolution, SHA-256 wheel locking, isolated application, verification, promotion, and rollback.
- Added `requirements.in` and `pyproject.toml` manifest loading, including explicitly selected optional extras and dependency groups.
- Added interpreter identity to Python resolutions and locks: implementation, full version, platform, architecture, and SOABI.
- Added exact compatible-wheel selection and rejected source distributions, editable installs, mutable VCS sources, unsupported local projects, ambient package-manager configuration, and credential-bearing persisted URLs.
- Added Python apply through direct hashed wheel requirements with index access disabled; apply never invokes the resolver.
- Added installed Python file verification against wheel `RECORD` sizes and cryptographic hashes, plus import and shell-free command probes.
- Generalized managed deployment candidates with explicit environment and deployment kinds and added fail-closed lock/driver/verifier compatibility checks.
- Added reusable backend plugin conformance helpers under `depviz.testing`.
- Added optional `HealthCheck` plugin components and backend-aware `depviz doctor` checks.
- Added conservative, dry-run-by-default `depviz gc`; current and immediate rollback candidates are always protected, while removed candidate records and archived locks remain for audit.
- Added the Python lock v1 schema and the `removed` candidate lifecycle status.
- Bumped the public Plugin API to `2.0` because drivers and verifiers now require explicit environment/deployment identity.
- Expanded the test suite to cover the full Python lifecycle, lock tampering, incompatible runtimes, installed-file drift, pyproject groups/extras, backend health checks, and safe garbage collection.

Python source builds, cross-interpreter wheel selection, private repository credentials, Windows pointer promotion, and R/composite environments remain intentionally unsupported.

## 0.6.0

- Added managed Conda deployment roots with isolated candidate prefixes and a stable `current` pointer.
- Added exact lock application through checksum-bearing Conda `@EXPLICIT` files without dependency re-resolution.
- Added durable candidate records binding candidate path, lock ID, lock format, resolution digest, lifecycle status, and verification digests.
- Added archived exact locks so later promotion and rollback use the lock originally applied to each candidate.
- Added exact candidate verification against authoritative `conda-meta` state, including version, build, source, artifact, checksum, platform, and dependencies.
- Added direct shell-free verification probe commands with controlled environment variables, timeouts, output limits, and diagnostics.
- Added mandatory re-verification inside the deployment operation lock immediately before promotion and rollback.
- Added atomic POSIX symlink promotion, deployment history, and whole-environment rollback.
- Added durable pending-switch journals and deterministic recovery after interruption between pointer and state updates.
- Added cross-process advisory deployment locks with owner metadata and bounded acquisition.
- Added `depviz apply`, `verify`, `promote`, `rollback`, and `status` commands.
- Added candidate, deployment, and verification JSON schemas.
- Rejected artifact URLs containing query parameters or existing fragments in portable Conda locks.
- Added adversarial tests for post-verification drift, rollback drift, archived-lock tampering, failed candidate cleanup, lock contention, and interrupted switch recovery.

Promotion currently requires POSIX symlinks. Candidate garbage collection and private-channel credential-provider integration are intentionally not implemented yet.

## 0.5.0

- Reorganized the implementation around `api`, `core`, `analysis`, `infrastructure`, `plugins`, `builtin`, and `cli` boundaries.
- Reduced `main.py` to the composition root and added `python -m depviz` support.
- Added `ApplicationServices` without introducing a monolithic application object.
- Split built-in manifest and Conda adapters into contributor-oriented packages.
- Added exact Conda-prefix inspection from authoritative `conda-meta` package records.
- Added normalized package diffs covering install, remove, version, build, source, artifact, checksum, and platform changes.
- Added conservative upgrade and downgrade classification for unambiguous numeric versions.
- Added deterministic policy findings for removals, downgrades, major upgrades, source changes, runtime changes, missing hashes, and weak hashes.
- Added immutable, content-bound change plans with manifest, current-state, resolution, native-transaction, platform, and target preconditions.
- Added a checksum-enforced Conda lock provider with round-trip and tamper validation.
- Added `depviz plan` and `depviz lock` commands.
- Added published plan-v1 and lock-v1 JSON schemas.
- Added backend identity to persisted resolution and environment state.
- Moved Conda transaction parsing and credential sanitation out of the resolver.
- Preserved legacy imports and command invocation through compatibility facades.
- Expanded the suite to include exact prefix corruption, plan tampering, conservative version ordering, lock tampering, credential rejection, and end-to-end plan/lock CLI tests.

This release remains non-mutating. Apply, verification, promotion, and rollback are intentionally absent.

## 0.4.0

- Added the built-in `conda-dry-run` resolver plugin.
- Added full-environment dry-run solving through Micromamba or Conda JSON transactions.
- Added explicit resolver selection and the `depviz resolve` command.
- Added host Conda-platform detection with explicit target override.
- Added exact normalized Conda package versions, builds, sources, artifacts, checksums, platforms, and dependencies.
- Added transaction metadata merging across `FETCH` and `LINK` records.
- Added preservation of the sanitized backend-native transaction.
- Added explicit-channel isolation, strict channel priority, empty temporary rc files, and `nodefaults` handling.
- Added failure classification for missing tools, timeouts, truncated output, malformed JSON, solver failures, and malformed transaction records.
- Added ecosystem-specific package-name normalization so Conda identities such as `_libgcc_mutex` remain intact.
- Added versioned resolution JSON serialization and atomic output writes.
- Added `inspect` and `resolve` subcommands while retaining legacy `depviz <manifest>` inspection syntax.
- Added a published resolution-v1 JSON schema.
- Added Ruff formatting verification to `make check`.
- Expanded the suite from 47 to 67 tests, including an end-to-end resolver CLI test with a controlled executable.

This release resolves exact Conda package sets but does not yet create an installable Depviz lock or apply environments.

## 0.3.0

- Added the versioned public backend API.
- Added coarse lifecycle protocols and explicit capabilities.
- Added entry-point plugin discovery and runtime validation.
- Replaced the handwritten Conda YAML reader with safe YAML parsing.
- Added requirement include and constraint-file support.
- Preserved markers, extras, direct URLs, indexes, channels, and source locations.
- Changed unsupported manifest entries from silent skips to structured errors.
- Added complete, approximate, and incomplete graph-inspection states.
- Changed metadata fetch failures from fake leaf packages to structured diagnostics.
- Added explicit depth-truncation diagnostics.
- Added version-specific PyPI metadata lookup for exact pins.
- Added atomic, validated inspection-cache storage.
- Expanded the test suite from 27 to 47 tests.

This release does not provide complete solving, locking, installation, verification, or rollback.
