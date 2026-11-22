# PostgreSQL 19 on Hyperlight-Unikraft

This version pins **PostgreSQL 19.0** in [sources.toml](sources.toml).
Native tools use unmodified sources; the musl guest server uses the
[PG19 patch stack](patches/postgres/README.md). Build/runtime code, rootfs
profiles, the Rust storage host, and validation suites live in [shared/](../shared/).
Hyperlight/Unikraft pins come from [shared/sources.toml](../shared/sources.toml);
runtime patches are shared with PG16, PG17, and PG18.
The version-local main-memory patch follows PG19's separate shared-memory
allocator using the header's `content_offset`; earlier majors' patches are unchanged.

## Build and validate

Run as an ordinary user on Linux x86_64 with KVM:

```sh
# From the repository root, only for never-initialized PG19 storage:
make PG=19 setup
make PG=19 persistent-demo
make PG=19 validate

# Helper tests without building or running a VM:
make PG=19 check
```

Equivalent commands work with `make -C pg19 <target>`. For direct Python
commands from the repository root, set `HL_PG_VERSION=19`.
See the [PG16 guide](../pg16/README.md) for shared prerequisites,
individual targets, resource bounds, and benchmark methodology.
Use the matching native PG19 clients built here, not another major's tools.

## Data and support scope

PG19 PostgreSQL sources, builds, credentials, and databases are isolated under
`pg19/target/postgres-experiment/`. Never reuse PG16/PG17/PG18 PGDATA.
Fresh PG19 databases retain upstream's default enabled data checksums;
validation checks that they remain enabled.

Both guest profiles and the matching native baseline explicitly set
`io_method = sync`; PG16/PG17 configuration stays unchanged. PG19's automatic
I/O-worker scaling does not change this synchronous profile. Worker-based AIO
and io_uring are not supported here.

The bounded feature set remains authenticated concurrent SQL backends,
transactions/session isolation, synchronized host-backed storage, disk
spilling, fresh-VM recovery, injected WAL errors, and serial benchmarks.
Optional workers, parallel query, replication, WAL summarization, REPACK, and
warm snapshots are not enabled or claimed. This is not a production PostgreSQL
port or a power-loss durability certification.

The Hyperlight-Unikraft checkout/submodules, runtime patch stamps, and exclusive
cross-version command lock are shared under `shared/target/`. Kernel and Cargo
outputs remain version-local. All versions use the shared runtime manifest.
Versions use ports 55432/55433 and native comparison port 55434; run suites serially.

The [CI workflow](../.github/workflows/postgres.yml) builds isolated PostgreSQL
bundles for PG16/17/18/19 and one shared Hyperlight bundle.
`validate-built` assembles and tests trusted artifacts without upstream source
checkouts or compiler/cache tools, while retaining version, installed-file,
runtime-binary, configuration, and UID/GID checks. It requires fresh persistent
storage and refuses to overwrite a database. Normal local build/demo commands
retain source verification. No additional Cargo manifest is required.
Other versions' performance measurements are not PG19 results.

## Validation

Local validation passed on 2026-11-22: native/guest builds, 71 helper tests
for each supported major, seven storage-host Rust tests, all nine native and
nine KVM process probes, concurrent SQL/session/reconnect checks, synchronized
storage, spilling, fresh-VM restart/crash recovery, and SQL-time WAL faults.
Six serial native/guest benchmark runs plus a separate filesystem profile
passed, along with 72 read scans and 18 independent output checks.

The complete `validate-built` suite ran from fresh targets assembled using the
exact CI tar allowlists and PG16's refreshed shared runtime artifacts. Private
mount/network namespaces hid upstream sources and isolated port reservations;
Git, curl, compiler, and compiler-cache commands were blocked, including version
queries. No blocked tools were invoked, artifacts were not rewritten, and no
test retries or relaxed guards were used. Effective synchronous I/O and enabled
data checksums were checked in running servers.

Normal source and package verification passed after retaining the validated
PG19 data and outputs alongside the original source/build trees. PG16/PG17/PG18
databases remained byte-for-byte unchanged, and host network settings were
untouched. Hosted CI has not yet run this change.
