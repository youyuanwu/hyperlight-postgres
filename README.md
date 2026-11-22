# PostgreSQL on Hyperlight-Unikraft

Build, runtime, packaging, and test code live in [shared/](shared/).
Each PostgreSQL major version has its own directory containing a source
manifest, PostgreSQL patches, a small Makefile entry point, and documentation.
Runtime patches, the Rust storage host, rootfs profiles, and tests are shared.
Hyperlight-Unikraft, submodule, and Rust dependency pins have one source of truth
in [shared/sources.toml](shared/sources.toml); version manifests contain only
their PostgreSQL release, archive URL/checksum, and license.

Supported versions: [PostgreSQL 16](pg16/README.md),
[PostgreSQL 17](pg17/README.md), [PostgreSQL 18](pg18/README.md), and
[PostgreSQL 19](pg19/README.md). The PG16
[performance report](pg16/RESULTS.md) remains version-specific.

Run from the repository root:

```sh
make PG=16 setup
make PG=16 persistent-demo
make PG=16 check
```

For PostgreSQL 17, 18, or 19, use `PG=17`/`PG=18`/`PG=19` (or
`make -C pg17 <target>`/`make -C pg18 <target>`/`make -C pg19 <target>`).
Run `setup` only for a never-initialized, version-local data directory.

`PG` defaults to `16`; plain `make` runs helper tests. Existing commands such as
`make -C pg16 check` and `cd pg16 && make persistent-demo` still work.
For direct Python commands, select the major version with `HL_PG_VERSION`:

```sh
HL_PG_VERSION=16 python3 shared/runtime.py build-preflight
```

The Hyperlight-Unikraft checkout and its Unikraft/libelf/app-elfloader submodules
are fetched once under `shared/target/sources/hyperlight-unikraft/`, reused by all
local PG versions. Runtime patch stamps and an exclusive command lock live in
`shared/target/`. The lock rejects concurrent commands across versions, protecting
the shared kernel configuration and embedded kernel.
All versions use the shared runtime pins; a mismatched checkout fails without
replacing or resetting the checkout.

PostgreSQL sources, builds, logs, credentials, and databases stay isolated under
`pg<major>/target/postgres-experiment/`. Existing PG16 databases remain in place.
Kernel outputs and Cargo build directories also remain version-local; sharing
the local source checkout does not itself deduplicate compilation.
CI builds Hyperlight once and supplies the same verified runtime bundle to all
PostgreSQL assembly/test jobs. Each version's test job depends only on its
matching PostgreSQL builder and the shared Hyperlight builder; slow or failed
builds of other PostgreSQL versions do not block it. The
[CI workflow](.github/workflows/postgres.yml) declares these dependencies explicitly,
with shared steps in reusable [build](.github/workflows/postgres-build.yml) and
[test](.github/workflows/postgres-test.yml) workflows.
GitHub-maintained actions use major-version tags. The sccache action remains
pinned to a full release because upstream does not publish a major-version tag.
Runtime metadata contains only shared source,
configuration, compiler, and runtime pin identities; package metadata separately
checks each PostgreSQL version, patch stack, and installed server.
The builder uses the PG16 entry point for its output directory and loads runtime
pins from the shared manifest. Builders verify source pins,
checksums, and patches; consumers trust those successful jobs from the same run
and check installed-file/binary hashes, configuration, release settings, version,
and host UID/GID without rewriting runtime metadata or fetching upstream sources.
The process, RAM SQL, and persistent runtime profiles remain separate outputs.
Versions currently use the same loopback ports; run their demos/tests serially.
CI test runners reserve ports 55432-55434 from ephemeral client allocation,
preserving any existing reservations. This prevents SQL reconnects from leaving
a client socket in TIME_WAIT on a port needed by a later server. Local network
settings and connection endpoints are unchanged.
PG18 and PG19 explicitly use synchronous I/O in both guest profiles and the matching
native baseline; worker-based AIO and io_uring are outside the supported profile.
Their fresh databases retain upstream's default enabled data checksums.
Changed layout/script fingerprints require rebuilding and repackaging artifacts,
but do not require deleting or reinitializing existing PG16 storage.

`validate-built` explicitly enables `HL_PG_ARTIFACT_ONLY=1`: it uses only the
repository scripts/configuration and downloaded build artifacts, never upstream
Git checkouts, PostgreSQL source extraction, or compiler tools. This mode is for
artifacts from trusted successful build jobs. Normal local build/demo targets
retain source verification, and source preparation/compilation is refused when
artifact-only mode is enabled.

## Rust storage host

The root [Cargo workspace](Cargo.toml) contains the shared
[storage-host package](shared/storage-host/Cargo.toml). Its
[Rust source](shared/storage-host/src/main.rs) is compiled directly as the
`storage-host` binary, without copying an example into the upstream checkout.
There are no per-PostgreSQL Cargo packages.
Dependencies are declared in the workspace and inherited by the storage-host package.
The root [rust-toolchain.toml](rust-toolchain.toml) pins Rust 1.99.1 with Clippy
and rustfmt, matching the fetched upstream toolchain. Rustup selects this pin
for workspace commands; system-packaged Cargo/rustc do not enforce toolchain files.

The package uses the fetched, patched Hyperlight-Unikraft library as a path
dependency. That checkout is excluded from the workspace and retains its own
manifest and lockfile for the CLI and upstream hostfs tests. The committed root
[Cargo.lock](Cargo.lock) preserves the upstream dependency versions; both
lockfiles are checked against the Hyperlight dependency pins in
[shared/sources.toml](shared/sources.toml).

Use the existing Make entry point to prepare sources and the embedded guest
kernel before compiling:

```sh
make PG=16 build-preflight fetch storage-build
```

The storage build invokes `cargo build` and `cargo test` with `--release`,
`--locked`, and the workspace manifest. Cargo runs from the upstream checkout
so rustup installations retain its toolchain selection. Build-job limits,
release settings, version-local Cargo outputs, the packaged binary path, and
the shared CI artifact remain unchanged. Workspace manifests, the lockfile,
toolchain pin, and Rust source are included in storage artifact fingerprints; changing them
requires rebuilding/repackaging, never reinitializing existing databases.

Direct, mount-free Azure backend candidates and unresolved design choices are
tracked in [Remote storage backend choices](docs/remote-storage.md).

## Adding a PostgreSQL version

1. Add `pg<major>/sources.toml` with only the PostgreSQL release version,
   URL/checksum, and license. Shared runtime pins stay in
   [shared/sources.toml](shared/sources.toml).
2. Port the PostgreSQL patches into `pg<major>/patches/postgres/`, including
   an ordered `series` file. The version uses the shared runtime pins and patches.
3. Add a Makefile exporting `HL_PG_VERSION := <major>` and including
   `../shared/Makefile`, plus version-specific documentation.
4. Build and validate with matching native tools and fresh, version-local PGDATA.
   Do not reuse another major version's database.
5. Add a version-specific PostgreSQL build job and assembly/test job to the
   [CI workflow](.github/workflows/postgres.yml), passing the major to the reusable
   workflows. The test job must depend only on that version's builder and
   `hyperlight-build`. PostgreSQL artifacts
   and data remain version-qualified; assembly validates the shared runtime
   bundle without recompiling.

The shared tooling defaults to PG16 and rejects unknown versions or a source
manifest whose PostgreSQL release does not match its directory's major version.
