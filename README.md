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
the source checkout does not deduplicate compilation or CI runner checkouts.
Versions currently use the same loopback ports; run their demos/tests serially.
Changed layout/script fingerprints require rebuilding and repackaging artifacts,
but do not require deleting or reinitializing existing PG16 storage.

## Adding a PostgreSQL version

1. Add `pg<major>/sources.toml` with the PostgreSQL release URL/checksum and
   Hyperlight/Unikraft pins matching the shared checkout.
2. Port the PostgreSQL patches into `pg<major>/patches/postgres/`, including
   an ordered `series` file. Shared runtime patches must match the selected pins.
3. Add a Makefile exporting `HL_PG_VERSION := <major>` and including
   `../shared/Makefile`, plus version-specific documentation.
4. Build and validate with matching native tools and fresh, version-local PGDATA.
   Do not reuse another major version's database.
5. Add the major to all three matrices in the
   [CI workflow](.github/workflows/postgres.yml). Artifacts and working directories
   are version-qualified; assembly still validates without recompiling.

The shared tooling defaults to PG16 and rejects unknown versions or a source
manifest whose PostgreSQL release does not match its directory's major version.
