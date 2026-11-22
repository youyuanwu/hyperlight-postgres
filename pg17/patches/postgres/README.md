# PostgreSQL 17.12 guest patch stack

Base: the exact archive and SHA-256 in [sources.toml](../../sources.toml).
Apply in [series](series) order with `git apply --check`, then `git apply`,
before checking the next patch. Native sources remain unmodified.

These are version-local ports of the locally authored
[PG16 adaptations](../../../pg16/patches/postgres/README.md); their provenance
and applicable copyright/license notices are unchanged. All behavior changes
require `HYPERLIGHT_UNIKRAFT`, and guest builds additionally require
`EXEC_BACKEND`. Ordinary Linux builds retain upstream behavior.

| Patch | PG17 adaptation |
|-------|-----------------|
| 0001 | Replace fork/exec with `posix_spawn` in PG17's `launch_backend.c`; preserve the new `--forkchild=<kind>` arguments, variable-length startup data, inherited sockets, and upstream child dispatch. Reuse the resolved self executable path in `postmaster.c`, and clean the parameter file after a failed spawn. |
| 0002 | Rebase anonymous mmap main shared memory, validated child reattachment, non-unmapping child detach, and target-only mmap GUC support under EXEC_BACKEND. No SysV IPC is required. |
| 0003 | Rebase VM-generation-checked DSM descriptors for anonymous shared mappings. Attach/detach/destroy, bounds, stale-identity rejection, and explicit errors retain the PG16 semantics. |

The executable-path shortcut assumes a fixed verified package that is not
replaced during a VM lifetime. Raw shared-memory addresses are valid only in
one fresh single-address-space VM, never a warm snapshot or another generation.
Persistent storage is exclusively owned, and only recognized stale guest
lockfiles may be removed. PostgreSQL's mmap startup cleanup removes stale DSM
descriptors without attaching their old addresses.

Validation uses the shared process, SQL/session/reconnect, filesystem ownership,
storage/synchronization, spill, crash/recovery, WAL-fault, and benchmark suites.
The invoking non-root user's UID/GID is preserved; ownership checks and exact
version checks are not relaxed. Optional workers and parallel DSM workloads
remain outside the supported profile.
