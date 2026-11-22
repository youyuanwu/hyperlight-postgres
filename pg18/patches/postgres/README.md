# PostgreSQL 18.7 guest patch stack

Base: the exact archive and SHA-256 in [sources.toml](../../sources.toml).
Apply in [series](series) order with `git apply --check`, then `git apply`,
before checking the next patch. Native sources remain unmodified.

These are version-local ports of the locally authored
[PG17 adaptations](../../../pg17/patches/postgres/README.md); their provenance
and applicable copyright/license notices are unchanged. All behavior changes
require `HYPERLIGHT_UNIKRAFT`, and guest builds additionally require
`EXEC_BACKEND`. Ordinary Linux builds retain upstream behavior.

| Patch | PG18 adaptation |
|-------|-----------------|
| 0001 | Rebase the `posix_spawn` launch path for PG18's includes and child-launch interface; preserve child slots, variable-length startup data, inherited sockets, `--forkchild=<kind>`, and child dispatch. Reuse the resolved self executable path and clean parameter files after failed spawn. |
| 0002 | Retain anonymous mmap main shared memory, data-directory-validated reattachment, non-unmapping child detach, and guest-only mmap support under EXEC_BACKEND. |
| 0003 | Retain VM-generation-checked DSM descriptors, bounds, stale-identity rejection, and explicit attach/detach/destroy errors. |

The executable-path shortcut assumes a fixed verified package for a VM lifetime.
Raw shared-memory addresses are valid only in one fresh single-address-space VM,
never a warm snapshot or another generation. Persistent storage is exclusively
owned, and only recognized stale guest lockfiles may be removed.

PG18 adds AIO shared-memory state and independent I/O workers. The version-aware
configuration uses `io_method = sync` rather than changing native source code or
claiming worker/io_uring support. Startup, reconnect, spill, crash/recovery, WAL
fault, and read suites exercise this mode with data checksums enabled.
