# PostgreSQL 19.0 guest patch stack

Base: the exact archive and SHA-256 in [sources.toml](../../sources.toml).
Apply in [series](series) order with `git apply --check`, then `git apply`,
before checking the next patch. Native sources remain unmodified.

These are version-local ports of the locally authored
[PG18 adaptations](../../../pg18/patches/postgres/README.md), checked against
PG19.0. Their provenance and applicable copyright/license notices are unchanged.
All behavior changes require `HYPERLIGHT_UNIKRAFT`, and guest builds additionally
require `EXEC_BACKEND`. Ordinary Linux builds retain upstream behavior.

| Patch | PG19 adaptation |
|-------|-----------------|
| 0001 | Use `posix_spawn` while preserving child slots, variable-length startup data, inherited sockets, `--forkchild=<kind>`, and child dispatch. Reuse the resolved self executable path and clean parameter files after failed spawn. |
| 0002 | Initialize PG19's `content_offset` for its separate shared-memory allocator. Retain anonymous mmap main shared memory, data-directory-validated reattachment, non-unmapping child detach, and guest-only mmap support under EXEC_BACKEND. |
| 0003 | Retain VM-generation-checked DSM descriptors, bounds, stale-identity rejection, and explicit attach/detach/destroy errors. |

The executable-path shortcut assumes a fixed verified package for a VM lifetime.
Raw shared-memory addresses are valid only in one fresh single-address-space VM,
never a warm snapshot or another generation. Persistent storage is exclusively
owned, and only recognized stale guest lockfiles may be removed. PG19 moves the
allocator's mutable free offset and shared-memory index out of `PGShmemHeader`;
the guest header initializes `content_offset` just like upstream, leaving
allocator initialization and backend attachment to PG19's native machinery.

PG19 replaces `io_workers` with automatic I/O-worker scaling controls. The shared
version-aware configuration retains `io_method = sync` in guest profiles and the
native baseline; no worker or io_uring support is claimed. Fresh databases retain
enabled data checksums. Startup, reconnect, spill, crash/recovery, WAL fault, and
read suites validate this bounded profile.
