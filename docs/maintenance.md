# Deployment maintenance

## Doctor

`depviz doctor` validates plugin contracts. Optional plugin health checks can confirm that external tools and runtimes are usable:

```bash
depviz doctor --plugin depviz-python --python /usr/bin/python3.12 --uv-executable uv
depviz doctor --plugin depviz-conda --tool micromamba --executable micromamba
```

Against a managed deployment:

```bash
depviz doctor --deployment .depviz/python-app --plugin depviz-python
```

Deployment checks include:

```text
deployment and metadata-directory permission boundaries
pending switch journal
current symlink shape
pointer and state agreement
candidate record validity
candidate directory presence
removed-candidate absence
archived exact lock presence
state references
orphan candidate directories
```

Doctor does not repair state automatically. Repair must remain an explicit future operation because guessing which record is authoritative can destroy rollback history.

## Garbage collection

Garbage collection is a dry run by default:

```bash
depviz gc --deployment .depviz/python-app --keep 3
```

Execute only after reviewing the candidate IDs:

```bash
depviz gc --deployment .depviz/python-app --keep 3 --execute
```

The collector refuses to run when:

```text
a deployment switch journal is pending
the current symlink and deployment state disagree
the deployment lock cannot be acquired
a candidate path escapes the deployment
an environment path is a symlink
```

It always protects the current candidate and immediate rollback target. `--keep N` retains the N newest additional candidates.

Execution removes candidate environment directories, marks their durable records as `removed`, and retains verification records and exact archived locks for audit.


## Release-hardening checks

Before creating a release artifact, run:

```bash
make check-release
```

The hardening-only subset is:

```bash
make test-hardening
```

See `docs/release-hardening.md` for the persistent-state compatibility and failure-handling policy.


## Legacy weak-checksum locks

Conda locks require SHA-256 by default. An existing MD5-only lock can be consumed only through an explicit per-command compatibility override:

```bash
depviz verify legacy-lock.json \
  --provider conda-exact-lock \
  --verifier conda-prefix-verifier \
  --deployment .depviz/conda-app \
  --candidate <candidate-id> \
  --allow-weak-checksum
```

The same flag is required for apply, promote, and rollback when their archived lock is MD5-only. The override is intentionally not saved in deployment state. Replace the lock with a SHA-256-backed artifact as soon as the upstream repository exposes one.
