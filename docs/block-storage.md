# Remote block storage architecture options

Status: exploration; no block-storage backend or filesystem has been selected
or implemented. Azure Elastic SAN documentation checked on 2026-11-22.
Recheck before implementation. No service calls, client integration, or
durability tests were run.

## Block storage versus the current interface

Remote block storage exposes a disk-like address space, not files, directories,
or objects. A filesystem must sit above it to provide PostgreSQL's file
operations.

The [current storage design](remote-storage.md) accesses storage directly from
the Rust host without mounting remote storage through the host operating system.
The [storage host](../shared/storage-host/src/main.rs) wraps a local `DirBackend`
and implements Hyperlight-Unikraft's filesystem-level `FsBackend`: offset
reads/writes, stat, directory operations, truncation, rename, stable directory
pagination, and file/directory synchronization.

A block service is therefore an architectural alternative, not a drop-in
`FsBackend` implementation. The [AWS filesystem and object-storage options](aws-storage.md)
are a separate comparison.

## Azure Elastic SAN (eSAN)

Azure Elastic SAN is managed network block storage. It provides volumes accessed
through the internet Small Computer Systems Interface (iSCSI) protocol. Think
of each volume as a remote disk rather than a remote file share.

Its REST API manages SAN resources, volume groups, and volumes. Volume API
operations such as create, get, update, and delete do not read or write the
blocks inside a volume. Actual data I/O uses iSCSI, not HTTPS file operations.
The management SDK/API does not replace an iSCSI initiator.

Elastic SAN pools storage capacity and performance across volumes and offers
redundancy options. This can be useful for consolidating database workloads,
but published service capabilities do not establish this project's latency,
durability, or cost requirements.

## Integration options

| Approach | Fits the no-host-mount requirement? | Implication |
| --- | --- | --- |
| OS iSCSI client, mounted host filesystem, existing host backend | No | Conventional integration, but violates the current direction. |
| Userspace iSCSI client, userspace filesystem, `FsBackend` | Potentially | Keeps the guest filesystem interface, but requires protocol and filesystem integration in the host. |
| Host iSCSI client, virtual block device, filesystem inside Unikraft | Potentially | Moves the filesystem into the guest and changes the host/guest storage interface. Larger architectural work. |

These are possible architectures, not confirmed client or guest capabilities.
The no-host-mount requirement alone does not select a userspace client or approve
a guest block-device redesign.

### Host-side filesystem

The Rust host would translate `FsBackend` operations through a filesystem
implementation backed by a userspace iSCSI client. The filesystem would supply
directories, metadata, truncation, rename, and recovery behavior.

We must select and validate both layers. A mature on-disk format does not by
itself establish that an available userspace implementation supports the
required operations, synchronization, and crash recovery.

### Guest-side filesystem

The host would expose remote block I/O through a virtual block-device interface,
and a filesystem inside Unikraft would provide PostgreSQL's file operations.
The host would retain service credentials and remote connection management;
iSCSI access need not move into the guest.

This requires explicit design for block-device transport, filesystem support,
flush/error propagation, guest image configuration, and the replacement of
current filesystem-level host behavior. It is not merely a new `FsBackend`.

## Why consider block storage?

- Disk-like range writes are a natural substrate for repeated small, in-place
  PostgreSQL page and WAL writes.
- A suitable filesystem could supply namespace and crash-recovery semantics
  rather than building those over object storage.
- Shared capacity/performance provisioning may benefit multiple databases.

These are reasons to investigate, not measured advantages. Block storage does
not automatically make writes atomic, acknowledge them durably, or provide safe
concurrent access to a filesystem.

## Required investigation and validation

- Prove durable flush and write ordering through the entire stack: PostgreSQL,
  filesystem, host/guest transport where applicable, iSCSI client, and service.
  Check supported SCSI flush/write-stability behavior; successful ordinary
  writes alone are not evidence of the required `fsync` contract.
- Define single-writer ownership and fencing. Multiple connections to a shared
  volume do not make a normal filesystem safe for competing writers. Reject
  stale writers after ownership loss; do not assume the service supports a
  particular reservation/fencing mechanism without verification.
- Exercise reconnects, failover, interrupted or ambiguous writes, timeouts, and
  explicit error propagation. Define retry safety rather than hiding failures.
- Verify client availability, block alignment/size requirements, filesystem
  compatibility, networking, authentication, and transport protection.
- Design formatting, native initialization/import, recovery access, quotas,
  and audit evidence. Avoid reformatting or reinitializing existing PGDATA.
- Measure page/WAL latency, throughput, and provisioning costs for the intended
  workload rather than relying on aggregate SAN performance figures.
- Use isolated SAN resources and a fresh database. Apply the existing
  [storage validation gates](remote-storage.md#validation-gates), including
  synchronization, crash recovery, and injected failures.

## Working recommendation

Keep Elastic SAN as a block-storage architecture alternative, not another
FileREST client candidate. If we retain the existing filesystem-level interface,
Azure Files remains the more direct investigation path described in the
[remote-storage notes](remote-storage.md#working-recommendation).

Selecting eSAN requires a separate decision on filesystem placement, client and
transport support, ownership/fencing, synchronization, initialization, and cost.
Nothing here changes the current backend or adopts a block-device architecture.

## Sources

- [Azure Elastic SAN overview: iSCSI, resources, and pooled performance](https://learn.microsoft.com/en-us/azure/storage/elastic-san/elastic-san-introduction)
- [Elastic SAN volume management REST API](https://learn.microsoft.com/en-us/rest/api/elasticsan/volumes)
