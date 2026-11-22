# PostgreSQL on Hyperlight-Unikraft

Build, runtime, packaging, and test code live in [shared/](shared/).
Each PostgreSQL major version has its own directory containing a source
manifest, PostgreSQL patches, a small Makefile entry point, and documentation.
Runtime patches, the Rust storage host, rootfs profiles, and tests are shared.

Supported versions: [PostgreSQL 16](pg16/README.md) and
[PostgreSQL 17](pg17/README.md). The PG16
[performance report](pg16/RESULTS.md) remains version-specific.

Run from the repository root:

```sh
make PG=16 setup
make PG=16 persistent-demo
make PG=16 check
```

For PostgreSQL 17, use `PG=17` (or `make -C pg17 <target>`).
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
the shared kernel configuration, embedded kernel, and generated Rust example.
All versions must use compatible runtime pins; a mismatch fails without
replacing or resetting the checkout.

PostgreSQL sources, builds, logs, credentials, and databases stay isolated under
`pg<major>/target/postgres-experiment/`. Existing PG16 databases remain in place.
Kernel outputs and Cargo build directories also remain version-local; sharing
the local source checkout does not itself deduplicate compilation.
CI builds Hyperlight once and supplies the same verified runtime bundle to both
PostgreSQL assembly/test jobs. Runtime metadata contains only shared source,
configuration, compiler, and runtime pin identities; package metadata separately
checks each PostgreSQL version, patch stack, and installed server.
The builder uses the PG16 entry point to select runtime pins and checks that
all version manifests agree before building. Builders verify source pins,
checksums, and patches; consumers trust those successful jobs from the same run
and check installed-file/binary hashes, configuration, release settings, version,
and host UID/GID without rewriting runtime metadata or fetching upstream sources.
The process, RAM SQL, and persistent runtime profiles remain separate outputs.
Versions currently use the same loopback ports; run their demos/tests serially.
Changed layout/script fingerprints require rebuilding and repackaging artifacts,
but do not require deleting or reinitializing existing PG16 storage.

`validate-built` explicitly enables `HL_PG_ARTIFACT_ONLY=1`: it uses only the
repository scripts/configuration and downloaded build artifacts, never upstream
Git checkouts, PostgreSQL source extraction, or compiler tools. This mode is for
artifacts from trusted successful build jobs. Normal local build/demo targets
retain source verification, and source preparation/compilation is refused when
artifact-only mode is enabled.

## Adding a PostgreSQL version

1. Add `pg<major>/sources.toml` with the PostgreSQL release URL/checksum and
   Hyperlight/Unikraft pins matching the shared checkout.
2. Port the PostgreSQL patches into `pg<major>/patches/postgres/`, including
   an ordered `series` file. Shared runtime patches must match the selected pins.
3. Add a Makefile exporting `HL_PG_VERSION := <major>` and including
   `../shared/Makefile`, plus version-specific documentation.
4. Build and validate with matching native tools and fresh, version-local PGDATA.
   Do not reuse another major version's database.
5. Add the major to the PostgreSQL build and assembly/test matrices in the
   [CI workflow](.github/workflows/postgres.yml). The single Hyperlight builder's
   `check-runtime-pins` guard rejects incompatible manifests. PostgreSQL artifacts
   and data remain version-qualified; assembly validates the shared runtime
   bundle without recompiling.

The shared tooling defaults to PG16 and rejects unknown versions or a source
manifest whose PostgreSQL release does not match its directory's major version.
