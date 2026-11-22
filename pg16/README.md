# PostgreSQL on Hyperlight-Unikraft

**PostgreSQL 16.15 runs successfully inside a Hyperlight-Unikraft KVM guest.**
The sample demonstrates authenticated SQL sessions, transactions, host-backed
PGDATA/WAL, real synchronization, disk spilling, and recovery in a fresh VM.
The same validation suites reproduce these results; the
[performance report](RESULTS.md) compares the guest with unmodified native
PostgreSQL.

| Demonstrated capability | Verified result |
|-------------------------|-----------------|
| Process model | All nine native and nine KVM process/signal/TLS/stack/shared-memory probes pass |
| Real SQL server | Two authenticated concurrent backends, commits/rollbacks, session isolation, 24 reconnects |
| Non-root execution | Guest matches the host user's uid/gid; PostgreSQL's root and ownership checks remain intact |
| Persistent storage | `fsync=on`, `synchronous_commit=on`, observed OS file/directory and WAL barriers |
| Storage semantics | Offsets, append/truncate, permissions, rename/reopen, explicit I/O/quota/read-only errors |
| Directory handling | All 5,000 entries over bounded pages, including rewind |
| Disk spilling | 100,000-row external sorts, measured temp I/O, exact native/Python checksum, temp cleanup |
| Fresh-VM recovery | Sixteen acknowledged commits survive restart and owned-host SIGKILL; stale DSM addresses are not attached |
| Failure handling | SQL-time WAL write/sync EIO and EDQUOT fail without a commit acknowledgement |
| Measurements | Six serial native/guest runs plus a separate filesystem profile, with exact results and resource bounds |
| Full-table reads | pgbench SELECT/aggregate scans and psql binary COPY; independent full-output verification outside timing |

This is a working, bounded sample, **not a production PostgreSQL port or a
power-loss durability certification**. A sync error can leave an unacknowledged
transaction recoverable from the host page cache. See [limits](#limits).

## Build and run the persistent demo

Use Linux x86_64 with KVM and run builds as an ordinary user.
The guest kernel and packaged image use that user's UID/GID. Rebuild and
repackage guest artifacts when changing host users; ownership checks remain enabled.
Run all commands below from this repository's `pg16/` directory (`cd pg16`
from the repository root), or use `make PG=16 <target>` at the repository root.
The reusable implementation lives in [shared/](../shared/); this directory
retains PG16 source pins, PostgreSQL patches, documentation, and generated data.
Direct Python commands use `HL_PG_VERSION=16 python3 ../shared/<script>.py`.
After this layout change, rebuild/repackage artifacts to update fingerprints;
do not delete or reinitialize existing persistent storage.
This project was originally
the PostgreSQL sample in
[hyperlight-process](https://github.com/youyuanwu/hyperlight-process); its
sample-specific Git history is preserved here.

For a **new** sample data directory:

```sh
make setup
make persistent-demo
```

`setup` checks resources, fetches verified sources, builds native PostgreSQL
tools and the patched guest server, builds/packages the persistent host, and
initializes PGDATA once. It deliberately refuses to overwrite existing
storage. If this sample was already initialized, skip `setup`:

```sh
make persistent-demo
```

The demo boots a guest against its dedicated host directory, runs actual
filesystem probes, connects through native `psql` over authenticated loopback
TCP, commits row `42`, reads it back, checkpoints, and shuts down cleanly.
Successful output includes:

```text
PASS storage-offsets-modes-rename
PASS storage-synchronous-write
PASS storage-sync-errors
PASS storage-write-errors
PASS storage-pagination
PASS storage
PASS persistent-sql: observed syncs/errors, paginated directory, single writer, host-backed PostgreSQL
```

Failures return a nonzero exit and leave diagnostics in the ignored artifact
directory. A successful compile or zero guest exit alone cannot satisfy the
checks. No server is deliberately left running after a demo or test.

### Requirements

Preflight checks at least 2 GiB available RAM and 4 GiB free disk, plus the
existing Git/C/Rust tools. Debian/Ubuntu builds also need `flex`, `bison`, `m4`,
`musl-tools`, and `cpio`. Missing dependencies are reported, not automatically
installed. The benchmark additionally needs two allowed logical CPUs,
`taskset`, and `findmnt`.

No Docker, Kraft, Kubernetes, or additional runtime images are required.
When direct KVM access is unavailable, deadline-wrapped guest runs use
`sudo -n`. The persistent host uses `sudo -n setpriv` to drop to the invoking
uid/gid and supply process-local access to the existing KVM group. Device
permissions, global group membership, and unrelated infrastructure are not
changed.

## Validation commands

With initialized persistent storage, run all validation suites serially:

```sh
make validate
```

Or select a capability:

| Target | Purpose |
|--------|---------|
| `check` | Helper assertions, exact thresholds/output shapes, artifact guards, and command wiring |
| `build-preflight` | Check architecture, non-root identity, tools, and build resources without requiring KVM |
| `check-runtime-pins` | Require all version manifests to use identical Hyperlight/Unikraft pins before sharing one CI build |
| `process-test` | Build and run all nine native and KVM runtime probes |
| `ram-demo` | Build/package/run the RAM-backed server and concurrent SQL/session/reconnect checks |
| `persistent-demo` | Rebuild/package the persistent host, verify managed configuration, run storage and SQL checks |
| `storage-test` | Repeat persistent filesystem/SQL validation against verified existing builds |
| `recovery-test` | Native/guest spill correctness, graceful restart, crash recovery, lifecycle, and WAL faults |
| `benchmark-build` | Compile the bounded native libpq timing client |
| `benchmark` | Three rotating native/guest repeats and a separate guest filesystem profile |
| `read-benchmark` | Standard pgbench/psql full-table reads, with fresh-server/repeated scans and separate output verification |
| `validate-built` | Package trusted build-job artifacts, initialize fresh storage, and run every suite without upstream sources or compiler tools; requires never-initialized storage |

The RAM demo explicitly uses `fsync=off`: its database is a fresh RAM copy and
SQL changes disappear on exit. It is not a fallback for persistent/recovery
validation, which always uses synchronization.

Build/configuration targets are also available individually:

```sh
make preflight fetch
make runtime-build process-test-native process-test-guest
make pg-kernel pg-native pg-guest pg-package sql-test
make storage-build storage-package
# Only for never-initialized storage:
make storage-init
# Update the managed persistent configuration while the server is stopped:
make storage-config
```

Tests reject changed source/build/configuration hashes; rebuild the matching
artifacts instead of editing metadata or bypassing guards. `benchmark` also
requires a current successful recovery result for the same inputs.
`validate-built` sets `HL_PG_ARTIFACT_ONLY=1` and trusts the source verification
already performed by successful build jobs. It still checks installed-file
and runtime binary hashes, compiler/configuration settings, version, ownership,
and the actual test results. Normal local builds retain source verification;
artifact-only mode refuses source preparation and compilation.

## What the checks exercise

### Runtime and SQL

[runtime.py](../shared/runtime.py) builds the pinned kernel/CLI and runs
[process.c](../shared/tests/process.c) as the same ordinary musl PIE natively and in KVM.
The cases are `hello`, `spawn`, `pipes`, `signals`, `blocked-signals`, `setsid`,
`tls`, `stack`, and `shared`. They check 32 child reexecutions, eight pipe
exchanges, caught/masked signals, independent globals/TLS, actual stack
touching, and 32 cross-process semaphore rounds.

[postgres.py](../shared/postgres.py) builds native tools from an unmodified source tree
and the guest server from a separately patched tree. Its SQL suite rejects
wrong credentials, requires distinct simultaneous backends and the necessary
auxiliary processes, verifies transactions/dirty-read refusal and independent
settings/temp tables, and checks reconnect RSS growth against the
32 MiB limit.

[init.c](../shared/rootfs/init.c) is a Linux PIE supervisor: it checks guest ownership and
shared-mapping lifetime, starts PostgreSQL with `posix_spawn`, and reaps it.
A separate loopback control thread accepts shutdown only with the current
VM identity. It does not rely on the pinned CLI's blocking stdin callback.

### Filesystem and recovery

[storage.py](../shared/storage.py), [storage-host.rs](../shared/storage-host.rs), and
[storage.c](../shared/tests/storage.c) implement the observed, quota-limited host backend.
Successful audit entries follow actual OS synchronization, not stubbed success.
File/directory/synchronous-write errors, partial-write EIO, EDQUOT, and EROFS
are propagated. Unsupported hostfs record locks/flock and mount-wide sync fail
explicitly. Bounded lexical pagination holds at most 128 entries per page.

[recovery.py](../shared/recovery.py) requires an actual external sort and nonzero temp
reads/writes in JSON EXPLAIN ANALYZE, checks every ordered-result checksum
against native PostgreSQL and an independent expectation, and verifies cleanup.
The fixture's checksum is `6d334d76c6d5153bbf8eebf552f99702`.

Restart tests verify exact committed rows and the same fixture in a fresh VM.
Crash tests kill only the identified host in this launcher's process tree,
with an open transaction and another active session. Recovery must complete
WAL replay and an end-of-recovery checkpoint, discard old DSM generations,
and leave uncommitted rows absent. Backend termination/client disconnect
rollbacks and twelve additional reconnects are checked.

One-shot WAL write EIO, sync EIO, and EDQUOT occur during real SQL commits.
Each must produce the expected PostgreSQL error, failed client exit, no
`COMMIT` acknowledgement, and successful fault-free fresh-VM recovery.
Write/quota test rows are absent afterward; the sync-error outcome is explicitly
allowed to be unknown rather than misreported as rollback.

### Performance

[runtime.py](../shared/runtime.py) and [storage.py](../shared/storage.py) build Rust hosts with
`cargo --release`: optimization level 3, no debug information, debug assertions,
overflow checks, or incremental compilation. The pinned kernel uses a
performance-optimized configuration. Native and guest PostgreSQL both use
`-O2 -march=x86-64` with PostgreSQL assertions disabled; native sources are
unmodified. Header-generation prerequisites are explicit for clean builds
invoked through the sample Makefile.

Artifact metadata fingerprints these settings. Packaging/measurement refuses
host metadata that does not match the release policy, stale binaries, or
PostgreSQL assertions enabled in either build. Benchmark metadata records the actual
verified host profile and both PostgreSQL configure/header identities, rather
than inferring optimization from a filename.

The hostfs mutation audit, quota checks, ownership guards, and real file/WAL
synchronization are enabled. The native server has no equivalent hostfs
audit, so these compare complete instrumented stacks, not isolated
virtualization overhead. Profiling is off in measured runs and on only in the
separate diagnostic. Disabling assertions in server builds does not disable
the sample's correctness checks or test assertions.

[evaluate.py](../shared/evaluate.py) and [query-client.c](../shared/tests/query-client.c) implement
the compiled libpq measurement path. Each mode runs three times in rotating
order, with 1,000 selects, 1,000 indexed lookups, 300 synchronous updates, and
five spill checksums per repeat, after untimed warm-ups.

Servers inherit one logical CPU and clients/samplers another; affinity is
verified and restored. Timed `PQexec` round trips exclude result validation
and CSV output. Separate batch timing measures single-client closed-loop
throughput. Main runs enable storage auditing and disable the profiler; a
reduced diagnostic records filesystem callback counts/body time separately.
See [RESULTS.md](RESULTS.md) for all measured values and caveats.

### Full-table read throughput

To compare reading actual rows out of the native and guest databases:

```sh
# After setup, or whenever the Makefile/build inputs have changed:
make pg-guest storage-build storage-package recovery-test
make check read-benchmark
# Repeat against the same verified builds:
make read-benchmark
```

[read_benchmark.py](../shared/read_benchmark.py) reuses the same 100,000-row fixture,
CPU pinning, credentials, guarded launchers, and resource limits. Preparation
recreates only `spill_fixture` and `eval_commits` in the dedicated databases,
verifies the exact checksum, checkpoints, and stops both servers.
The benchmark requires the current successful recovery gate and the native
`psql`/`pgbench` 16.15 clients built by `pg-native`; no new client binary is built.

Each mode/workload/repeat boots a separate server, then runs:

- `SELECT id,value FROM spill_fixture`, using a custom SQL script in standard
  `pgbench -n -c 1 -j 1 -M simple -t 4 -l`. It consumes the full results without
  printing rows; no pgbench-specific tables or initialization are needed.
- Binary `COPY (SELECT id,value FROM spill_fixture) TO STDOUT`, using standard
  `psql` with `\timing` and `\o /dev/null`.
- `SELECT count(*),sum(length(value)) FROM spill_fixture`, returning two
  scalars through pgbench as a scan/computation control, not a bulk-transfer test.

Each client runs one first scan and three immediately repeated scans on the
same connection. Three rotating serial native/guest repeats produce three
fresh-server and nine repeated samples per mode/workload. `LC_ALL=C` makes
standard-tool output parsing deterministic, and nonzero exits, failed
transactions, or missing/extra latency samples fail the test.

Timing comes directly from pgbench's per-transaction logs and psql's COPY
timings: query/protocol/result-consumption cost is included, connection/startup
and correctness checks are excluded. First-row latency is not reported.
pgbench/libpq can buffer a full query result; psql streams COPY output.

Throughput uses 13,200,000 logical payload bytes per transfer: a four-byte ID
and 128-byte value per row. It is not full TCP/protocol wire bandwidth.
The aggregate has no bulk-payload throughput. pgbench does not check every
returned row: **correctness is verified separately**, after all timed scans.
Each run checks the exact aggregate and uses untimed psql binary COPY ordered
by ID to compare all 14,200,021 output bytes against an independently computed
SHA-256 expectation. The temporary verification file is deleted after success.

Heap read/hit deltas are recorded outside timing for the four timed scans
combined, before untimed correctness and EXPLAIN checks. Each group measured
8,000 read blocks / 192 hits, with subsequent plans requiring a serial
100,000-row Seq Scan and no sorting/temp writes. **Repeated does not mean
fully resident in shared buffers.** The host page cache is retained throughout;
these are not cold-disk or raw storage bandwidth measurements.

The [read results](RESULTS.md#full-table-read-throughput-2026-11-10) show about
68 MB/s guest SELECT payload and 64 MB/s COPY, versus roughly 309-311 MB/s
native in the release-build run.
Raw pgbench transaction logs, psql timing output, SQL scripts, EXPLAIN plans,
tool hashes, output-validation hashes, and resource measurements stay ignored
under `target/postgres-experiment/read-benchmark/`. The full `validate` target
also includes this suite. The small-query/spill benchmark uses its compiled
timing client; the read-throughput suite uses standard PostgreSQL clients.
Both use the release build policy.

A separate [cache-hit diagnostic](RESULTS.md#cache-hit-control-separating-storage-and-result-delivery)
shows near-native aggregate scans when the same rows are fully buffered
(25.5 ms native / 27.3 ms guest), while binary COPY takes
(41.1 / 154.9 ms). This supports investigating both host-backed reads and
result delivery, not blaming PostgreSQL computation alone. It does not isolate
the cost of hypercall transitions from copying, marshalling, host operations,
or scheduling; see the diagnostic's method and four-sample limitations.

## Data, configuration, and ownership

Version-specific generated files are ignored beneath `pg16/target/postgres-experiment/`
relative to the repository root:
PostgreSQL sources, builds, CPIOs, logs/audits, passwords, result metadata, and databases.
Hyperlight-Unikraft and its submodules are checked out once under
`shared/target/sources/hyperlight-unikraft/`, with shared runtime patch stamps.
An exclusive shared command lock protects mutable upstream build files across
PG versions; competing commands fail explicitly. Kernel/Cargo build outputs
remain version-local, and mismatched runtime pins are rejected without resets.
PostgreSQL clients authenticate as the `phase2` test database role. Use the
targets above to build and validate the sample.

The persistent directory is [storage.STORAGE](../shared/storage.py); its PGDATA is
mounted at `/storage/pgdata`. Application/kernel files remain RAM-backed, and
live PGDATA is never copied into a rootfs image. Native initialization uses
real synchronization, not `--no-sync`. Generated SCRAM credentials/pgpass files
are private and never checked in. PostgreSQL/control listen on host loopback
ports 55432/55433; the serial native comparison uses 55434.

Initialization/configuration and the VM host independently lock the storage
directory inode. The host retains ownership for its entire lifetime, even if
its launcher exits. Aliases and actual competing VM hosts are rejected before
writable backend opening. Never run unrelated/native tools against this
PGDATA while a guest owns it; advisory locks do not stop noncooperating tools.

Configuration updates reject live or stale `postmaster.pid`. For crash recovery,
the exclusively owning host removes only a recognized guest lockfile and syncs
PGDATA. Malformed/native lockfiles are refused. PostgreSQL's existing mmap
cleanup removes old DSM descriptors without dereferencing their addresses;
fresh identities are verified after real crashes. An unconsumed fault-control
file stops the next test rather than being silently cleared.

## Reproducible builds and bounds

[sources.toml](sources.toml) pins PostgreSQL 16.15, Hyperlight-Unikraft, and all
kernel submodules. Locally authored ordered patches preserve
upstream notices and document their provenance:
[PostgreSQL](patches/postgres/README.md),
[Unikraft](../shared/patches/unikraft/README.md), and
[Hyperlight-Unikraft](../shared/patches/hyperlight-unikraft/README.md).
No source fork or PostgreSQL submodule is required.

Builds default to one job; `HL_PG_BUILD_JOBS` selects a positive build-job count
(CI uses two). Tests remain serial. `HL_PG_USE_SCCACHE=1` wraps C compilers
with sccache; `RUSTC_WRAPPER=sccache` enables Rust caching. CI caches compiler
outputs through GitHub's cache backend, not databases, credentials, or validation results.
CI builds PostgreSQL and Hyperlight independently, transfers allowlisted tar bundles
with executable modes and symlinks intact, and runs `validate-built` in a third job.
The Hyperlight builder runs once, checks runtime pins across all versions, and
publishes one `hyperlight-build` artifact consumed by both PG assembly/test jobs.
Its kernel/host metadata is version-neutral; PostgreSQL patch/version and package
metadata remain version-specific. Source pins/checksums/patches are verified in
builders only. Assembly does not fetch or extract upstream sources, initialize
source Git trees, or install compiler/cache tools. It verifies artifact hashes,
configuration/version/identity, packages images, and runs the full suites.
Runtime metadata is not rewritten during assembly.
Runtime profiles and guest tools are immutable, hashed build outputs; all jobs
use the same compiler settings and matching non-root UID/GID. Databases and
credentials are created only in the assembly/test job, never transferred.
Builds use explicit GNU C17/baseline `-march=x86-64`. Input stamps
invalidate stale objects. Each CLI/host embeds its source-built kernel;
building another runtime profile does not modify packaged RAM/persistent
executables. Unexpected source edits or changed prepared patch
stacks are rejected, never discarded or reset.

The configuration keeps 512 MiB guest scratch, a 512 MiB logical storage
budget, 16 MiB shared buffers, 64 KiB work_mem, two connections, and a 512 KiB
PostgreSQL stack-depth limit. Required auxiliary processes stay enabled;
optional workers/parallelism/autovacuum remain disabled. The guest reports a
1 MiB stack limit despite musl reporting 8 MiB; that report is not permission
to increase PostgreSQL stack depth.

Process probes have 30-second VM deadlines, RAM SQL 180 seconds, and persistent
VMs 120 seconds. Startup has 30 seconds, normal SQL 10 seconds, bounded
fixture/sort/checksum work 60 seconds, fault clients 20 seconds, and shutdown
15 seconds. The benchmark client has a 90-second deadline. Resource sampling
and strict disk scans fail explicitly; cleanup targets only owned processes.
Physical allocation/inode overhead is not the logical-file budget metric.

## Limits

- One cooperative vCPU and one address space; ordinary `fork` and `SA_RESTART`
  are unsupported. Exec children must be PIE. Native execution of the
  guest-specific PostgreSQL binary is not supported.
- Hostfs is path-based, not a complete OS file-description protocol. Tested
  renames close/reopen files; unlinked-open/renamed-open descriptors, arbitrary
  seekdir cookies, and full guest record locks are not implemented.
- SIGKILL retains the host OS page cache. Injected WAL errors model the host
  boundary, not power failure, arbitrary torn writes, or physical disk filling.
  Handle unknown commit outcomes instead of blindly retrying non-idempotent SQL.
- No live snapshots, writable clones of the same PGDATA, replication, SMP, or
  broad production PostgreSQL compatibility is claimed.
- Finite reconnect and sampled memory checks do not prove every lifetime path
  leak-free or instantaneous peaks. Performance compares complete instrumented
  stacks, not isolation-only overhead.
