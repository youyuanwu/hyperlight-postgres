# PostgreSQL 17 on Hyperlight-Unikraft

This version pins **PostgreSQL 17.12** in [sources.toml](sources.toml).
Native tools use unmodified sources; the musl guest server uses the
[PG17 patch stack](patches/postgres/README.md). Build/runtime code, rootfs
profiles, the Rust storage host, and validation suites live in [shared/](../shared/).
Hyperlight/Unikraft pins come from [shared/sources.toml](../shared/sources.toml);
runtime patches are shared with PG16 and PG18.

## Build and validate

Run as an ordinary user on Linux x86_64 with KVM:

```sh
# From the repository root, only for never-initialized PG17 storage:
make PG=17 setup
make PG=17 persistent-demo
make PG=17 validate

# Helper tests without building or running a VM:
make PG=17 check
```

Equivalent commands work from this directory without `PG=17`:

```sh
cd pg17
make persistent-demo
```

For direct Python commands, explicitly select the major version:

```sh
HL_PG_VERSION=17 python3 shared/runtime.py build-preflight
```

That Python example runs from the repository root. See the
[PG16 build and validation guide](../pg16/README.md) for shared prerequisites,
individual targets, synchronization/ownership checks, resource bounds, and
benchmark methodology. Substitute `pg17`/`PG=17` for its version-specific paths
and use the native PG17 clients built here, not PG16 clients.

## Data and support scope

PG17 native/guest PostgreSQL sources, builds, credentials,
and databases, live under `pg17/target/postgres-experiment/`. PG16 artifacts and
data are not reused or modified. The Hyperlight-Unikraft checkout and submodules
are shared at `shared/target/sources/hyperlight-unikraft/`; runtime patch stamps
and an exclusive cross-version command lock are shared as well. Kernel and Cargo
outputs remain version-local. Runtime pins must match the existing checkout;
mismatches fail without replacing it.
Never initialize a PG17 server against PG16
PGDATA; major-version data migration is outside this sample.
Both versions use ports 55432/55433 and the serial native comparison uses
55434, so run version suites serially.

PG17 targets the same bounded feature set as PG16: authenticated concurrent
SQL backends, transactions/session isolation, synchronized host-backed storage,
disk spilling, fresh-VM recovery, injected WAL errors, and serial benchmarks.
Optional workers, parallel query, replication, WAL summarization, and warm
snapshots are not enabled or claimed. This is not a production PostgreSQL port
or a power-loss durability certification.

The [CI workflow](../.github/workflows/postgres.yml) builds all supported major versions
with isolated PostgreSQL artifact bundles and one shared Hyperlight bundle, then runs
`validate-built` without recompiling. That target requires never-initialized
persistent storage and refuses to overwrite a database.
The single Hyperlight builder loads the shared runtime manifest.
Kernel and storage-host metadata exclude PostgreSQL-specific inputs;
builders verify upstream pins/checksums/patches. Each assembly trusts those
successful jobs, checks its PostgreSQL version and installed-file hashes, and
validates runtime configuration/compiler settings, binary hashes, and UID/GID.
`validate-built` enables `HL_PG_ARTIFACT_ONLY=1`; upstream checkouts, PostgreSQL
source extraction, and compiler/cache tools are not needed in test jobs. Normal
local builds keep source verification. Metadata is not rewritten to accommodate
another version.
PG16 performance measurements are not PG17 results.

## Validation

Local PG17 validation passed on 2026-11-22: native/guest builds, helper tests,
seven targeted Rust tests, all nine native and nine KVM process probes,
concurrent SQL/session checks, synchronized storage, spilling, fresh-VM
restart/crash recovery, and SQL-time WAL fault injection.
Six serial native/guest benchmark runs and the separate filesystem profile
passed, along with all 72 full-table read scans and 18 independent output checks.

The complete `validate-built` suite ran against a fresh source checkout
assembled from the exact CI tar allowlists, with compiler commands rejected
except for `--version`. No compilation, automatic test retries, or relaxed
ownership/version/resource checks were used during assembly validation.
PG16 helper tests also passed and its existing data was left untouched.

The shared CI runtime bundle was subsequently validated with the complete
`validate-built` suite for both PG16 and PG17, still with compilation blocked.
Both consumed identical runtime binaries and metadata without modification;
each passed all 72 read scans and independent output checks. The original
version-local databases were restored after testing.

Source-free artifact validation also passed for both versions from empty
version-local targets, with the upstream checkout unavailable and Git, curl,
compiler, and compiler-cache commands blocked. Each complete suite passed,
including all 72 read scans. Normal source-verifying packaging passed again
after restoring the original checkouts and databases.
