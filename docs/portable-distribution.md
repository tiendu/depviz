# Portable distribution

Depviz supports a **thin native executable** distribution. Each executable
contains Depviz, a private Python runtime, and Depviz's Python dependencies.
It does not bundle package-manager backends or a target Python environment.

## Why the executable is thin

Depviz coordinates existing package managers instead of reimplementing their
solvers. Keeping those tools external preserves their independent security and
compatibility update cycles and avoids turning Depviz into a large toolchain
installer.

The executable discovers or accepts explicit paths for:

- Conda, Mamba, or Micromamba for Conda-family operations;
- `uv` for Python operations;
- a reusable target Python interpreter for Python operations.

Use `depviz doctor` after installation to inspect the available toolchain.
Use the existing CLI overrides when a backend tool is not on `PATH`.

## Supported release artifacts

A tagged release builds these independent artifacts:

| Artifact | Host |
| --- | --- |
| `depviz-linux-x86_64.tar.gz` | Linux x86-64 |
| `depviz-linux-arm64.tar.gz` | Linux arm64 |
| `depviz-macos-x86_64.tar.gz` | macOS Intel |
| `depviz-macos-arm64.tar.gz` | macOS Apple silicon |
| `depviz-windows-x86_64.zip` | Windows x86-64 |

Each bundle has a sibling `.sha256` file and contains one native executable.
Unix bundles preserve the executable mode through CI and download. A native
executable is portable within its host family; no executable is universal
across operating systems or CPU architectures.

## Build locally

```bash
make binary
./dist/depviz --help
./dist/depviz doctor
```

The direct build command, useful where GNU Make is unavailable, is:

```bash
python -m pip install -e ".[binary]"
python -m PyInstaller --clean --noconfirm packaging/depviz.spec
```

## Publish a release

Run the release gate, commit the release state, and push an annotated tag:

```bash
make check-release
git tag -a v0.8.0rc6 -m "depviz 0.8.0rc6"
git push origin main v0.8.0rc6
```

The `Portable binaries` workflow validates the source once, builds all target
artifacts independently, smoke-tests them, generates SHA-256 files, and uploads
them to the matching GitHub Release.

## Runtime boundaries

A frozen executable must not use its launcher path as the target Python
interpreter. Depviz resolves a host interpreter in frozen mode or requires an
explicit `--python` path.

Frozen launchers can also modify dynamic-library search paths. Depviz restores
the original host loader paths before invoking external commands so Conda,
`uv`, Python, and probes do not accidentally load libraries from the frozen
application.

External Python entry-point plugins are not dynamically installed into a
frozen executable. The native artifacts contain the built-in plugins. Use a
normal Python or `pipx` installation when third-party plugin discovery is
required.

## Distribution hardening still required for a public stable release

- Sign and notarize macOS artifacts with an Apple Developer ID.
- Sign Windows artifacts with an Authenticode certificate.
- Preserve the Linux build baseline or move it to a controlled manylinux-style
  builder when a formal minimum GNU C Library version is declared.
- Generate and publish an SBOM and provenance/attestation for stable releases.
- Test each artifact on a clean host with no project checkout or development
  virtual environment present.
