# PostgreSQL 18 on Hyperlight-Unikraft

This version pins **PostgreSQL 18.7** in [sources.toml](sources.toml).
Native tools use unmodified sources; the musl guest server uses the
[PG18 patch stack](patches/postgres/README.md). Build/runtime code, rootfs
profiles, the Rust storage host, and validation suites live in [shared/](../shared/).
Hyperlight/Unikraft pins come from [shared/sources.toml](../shared/sources.toml);
runtime patches are shared with PG16, PG17, and PG19.

## Build and validate

Run as an ordinary user on Linux x86_64 with KVM:

```sh
# From the repository root, only for never-initialized PG18 storage:
make PG=18 setup
make PG=18 persistent-demo
make PG=18 validate

# Helper tests without building or running a VM:
make PG=18 check
```

Equivalent commands work with `make -C pg18 <target>`. For direct Python
commands from the repository root, set `HL_PG_VERSION=18`.
See the [PG16 guide](../pg16/README.md) for shared prerequisites,
individual targets, resource bounds, and benchmark methodology.
Use the matching native PG18 clients built here, not another major's tools.

## Data and support scope

PG18 PostgreSQL sources, builds, credentials, and databases are isolated under
`pg18/target/postgres-experiment/`. Never reuse PG16/PG17 PGDATA.
Fresh PG18 databases retain upstream's default enabled data checksums;
validation checks that they remain enabled.

PG18 defaults to worker-based asynchronous I/O, independently of
`max_worker_processes`. Both guest profiles and the matching native baseline
explicitly set `io_method = sync`; PG16/PG17 configuration stays unchanged.
Validation checks the effective I/O method. Upstream excludes io_uring when
building with EXEC_BACKEND. Worker-based AIO and io_uring are not supported here.

The bounded feature set remains authenticated concurrent SQL backends,
transactions/session isolation, synchronized host-backed storage, disk
spilling, fresh-VM recovery, injected WAL errors, and serial benchmarks.
Optional workers, parallel query, replication, WAL summarization, and warm
snapshots are not enabled or claimed. This is not a production PostgreSQL port
or a power-loss durability certification.

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
Other versions' performance measurements are not PG18 results.

## Validation

Local validation passed on 2026-11-22: native/guest builds, 71 helper tests
for each supported major, seven storage-host Rust tests, all nine native and
nine KVM process probes, concurrent SQL/session/reconnect checks, synchronized
storage, spilling, fresh-VM restart/crash recovery, and SQL-time WAL faults.
Six serial native/guest benchmark runs plus a separate filesystem profile
passed, along with 72 read scans and 18 independent output checks.

The complete `validate-built` suite ran from fresh targets assembled using the
exact CI tar allowlists and PG17's shared runtime artifacts. Upstream sources
were unavailable and Git, curl, compiler, and compiler-cache commands were
blocked, including version queries. No blocked tools were invoked, no artifacts
were rewritten, and no test retries or relaxed guards were used. Effective
synchronous I/O and enabled data checksums were checked in running servers.
Normal source verification passed after restoring the checkout/build trees.
Validated PG18 data and outputs were retained; PG16/PG17 databases stayed
byte-for-byte unchanged. Hosted CI has not yet been verified for this change.
