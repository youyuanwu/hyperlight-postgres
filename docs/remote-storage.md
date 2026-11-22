# Remote storage backend choices

Status: exploration; no remote backend has been selected or implemented.
SDK availability checked on 2026-11-22. Recheck before implementation.

## Confirmed direction

Access remote storage directly from the Rust storage host, without mounting
Azure storage through the host operating system. Host-mounted SMB/NFS and
BlobFuse are outside this direction.

The shared [storage-host package](../shared/storage-host/Cargo.toml) is the
integration point. Its [current implementation](../shared/storage-host/src/main.rs)
wraps a local `DirBackend` and implements Hyperlight-Unikraft's `FsBackend`.
The backend contract includes offset reads/writes, stat, directory creation
and removal, truncation, rename, stable directory pagination, and file/directory
synchronization.

A remote implementation also needs to replace the current directory-inode
ownership lock and filesystem-based quota scan. The
[initialization tooling](../shared/storage.py) currently runs native `initdb`
against a local directory; provisioning a remote database needs an explicit
design too.

## Azure options

| Service | Direct Rust access | Assessment |
| --- | --- | --- |
| Azure Files | FileREST over HTTPS, including Apache OpenDAL's third-party Rust `azfile` service. No dedicated modern Azure Files Rust client is listed in the current Microsoft Rust SDK catalog. | Leading service candidate to investigate, but OpenDAL's current standard writer is not an offset-write backend. Share type, API version, supported operations, and durability guarantees still need verification. |
| Azure Blob Storage | Official `azure_storage_blob` SDK, with `azure_identity` for authentication. Includes block, append, and page blob clients. | Best current official SDK coverage. Not a filesystem: we would need to implement namespace, metadata, atomic rename, truncation, and synchronization semantics. |
| ADLS Gen2 | Data Lake Storage REST APIs, including Apache OpenDAL's third-party Rust `azdls` service. The existing `azure_storage_datalake` Rust crate is explicitly described as unofficial and legacy, with no planned updates. | Hierarchical paths are useful, but do not establish PostgreSQL-compatible filesystem semantics. Investigate current REST clients rather than adopting the legacy crate for new work. |

SDK availability is not evidence that a service is suitable for live PostgreSQL
data. The official Blob client is `azure_storage_blob` (singular); the older
`azure_storage_blobs` crate is not the client to start new work with.

Sources:

- [Microsoft Rust SDK catalog](https://learn.microsoft.com/en-us/azure/developer/rust/azure-sdk-library-package-index)
- [Azure Files FileREST overview](https://learn.microsoft.com/en-us/rest/api/storageservices/file-service-rest-api)
- [Official Blob SDK documentation](https://docs.rs/azure_storage_blob/latest/azure_storage_blob/)
- [Page blob client](https://docs.rs/azure_storage_blob/latest/azure_storage_blob/clients/struct.PageBlobClient.html)
- [Legacy ADLS crate warning](https://docs.rs/azure_storage_datalake/latest/azure_storage_datalake/)
- [Data Lake Storage Gen2 REST API](https://learn.microsoft.com/en-us/rest/api/storageservices/data-lake-storage-gen2)

## Working recommendation

Investigate a direct Azure Files FileREST backend first. This is a recommendation,
not an adopted backend decision. OpenDAL is useful as a Rust API client and
implementation reference, but the investigation below rules out mapping its
standard `Operator` directly to our writable filesystem contract.

Use the official Blob SDK instead if we deliberately choose to build a
filesystem layer on object storage. Page blobs offer random-range updates, but
alignment requirements and filesystem metadata/transaction handling remain our
responsibility. ADLS is another REST candidate, not a confirmed drop-in backend.

Do not treat a generic upload/download wrapper as a complete `FsBackend`.

## OpenDAL investigation

Reviewed release [v0.59.5](https://github.com/apache/opendal/releases/tag/v0.59.5),
published on 2026-10-18, using release-tagged source rather than unversioned
service capability summaries. This was a source/API review only: no OpenDAL
dependency was added, and no Azure service calls or durability tests were run.

OpenDAL has native Rust `azfile`, `azdls`, and `azblob` services. It is an Apache
project, not Microsoft's official Azure SDK. Its workspace requires Rust 1.91,
which is below the existing local compiler version of 1.93.1. It offers
asynchronous operations and an optional blocking interface; neither supplies
missing filesystem semantics.

### Azure Files capability mapping

| Requirement | Reviewed implementation | Result for this project |
| --- | --- | --- |
| Read at an offset | Range reads become HTTP `Range` requests. | Useful building block. |
| Overwrite bytes without changing other file contents | Normal writes create/replace the file, set its length to the submitted buffer size, then write that buffer at offset zero. | Cannot implement `FsBackend::write(Some(offset), data)` by calling `Operator::write`. Doing so would replace unrelated file contents. |
| Append | Writer source contains an append branch, but `azfile` does not advertise `write_can_append` in this release. | Do not assume supported append semantics from the generic API or the presence of internal code. |
| Truncate/extend an existing file | No standalone resize operation in the standard operator/service contract. | Needs a separate operation, such as Azure Files `Set File Properties`, rather than rewriting the whole file. |
| File and directory synchronization | No filesystem `fsync`/`fdatasync` or directory-sync operation in that contract. | Define and prove a service-specific durability/ordering protocol; writer completion alone is not sufficient evidence. |
| Rename with replacement | Uses the server's file/directory rename endpoint with replace-if-exists enabled. | Useful, but share-protocol compatibility, atomicity, and crash behavior still need validation. |
| POSIX metadata and permission changes | Generic metadata includes file/directory type, size, modification time, ETag, and user metadata; no direct Unix UID/GID/mode or `chmod`/`set_times` contract. | Needs explicit metadata and permission handling, not silent defaults. |
| Stable name-based pagination | Uses Azure continuation tokens; emits files before directories within each response. | Needs adaptation to our globally name-ordered, start-after-name pages. Do not assume backend page order matches ours. |
| Missing-directory errors | The lister treats HTTP 404 as an empty, completed listing. | The adapter must preserve required not-found/error behavior instead of accepting an invalid storage path as an empty directory. |
| Remote ownership | No file lease/fencing operation in the standard contract; reviewed write requests do not carry lease IDs. | A separate ownership mechanism must protect every mutation, not just acquire an unrelated lock object. |

The backend advertises a 4 MiB maximum normal write because it uses one
`Put Range` update. This is not Azure Files' overall file-size limit, but it
reinforces that this writer is designed for bounded whole-file writes, not
PostgreSQL's repeated small writes to larger files.

### API version and authentication

The reviewed Azure Files implementation fixes `x-ms-version` to `2022-11-02`.
Microsoft's Create File, Put Range, and Set File Properties documentation says
those operations require `2025-05-05` or later for NFS-enabled shares.
The Rename File documentation currently lists SMB support and no NFS support.
Raising the API version alone therefore does not establish an NFS-share solution.
An SMB-enabled share accessed directly through FileREST still requires no host
SMB mount.

The implementation has explicit Shared Key and SAS credential paths. Managed
identity/OAuth behavior was not validated. FileREST OAuth also requires the
appropriate request-intent header and RBAC permissions, which must be checked
for any chosen authentication integration.

### Recommended next step

Keep OpenDAL as an API-access candidate, not a selected database filesystem.
Before a full backend, build a narrow, isolated Azure Files contract spike:

- Prove in-place overwrite preserves bytes on both sides of the written range.
- Resize existing files without replacing their retained content.
- Exercise exclusive creation, rename replacement, and metadata changes.
- Verify ownership loss blocks writes, and establish durable write/namespace
  ordering against the actual service.

If OpenDAL cannot expose the required operations, use a small dedicated
FileREST client or propose narrowly scoped upstream extensions. Avoid whole-file
read/modify/reupload as a shortcut for PostgreSQL page writes. ADLS and Blob
remain separate candidates; the Azure Files source review does not establish
their suitability.

Release-pinned evidence:

- [Azure Files backend and capability flags](https://github.com/apache/opendal/blob/v0.59.5/core/services/azfile/src/backend.rs)
- [Azure Files writer](https://github.com/apache/opendal/blob/v0.59.5/core/services/azfile/src/writer.rs)
- [Azure Files REST requests and API version](https://github.com/apache/opendal/blob/v0.59.5/core/services/azfile/src/core.rs)
- [Azure Files directory listing](https://github.com/apache/opendal/blob/v0.59.5/core/services/azfile/src/lister.rs)
- [Public operator API](https://github.com/apache/opendal/blob/v0.59.5/core/core/src/types/operator/operator.rs)
- [Low-level service contract](https://github.com/apache/opendal/blob/v0.59.5/core/core/src/raw/accessor.rs)
- [Metadata representation](https://github.com/apache/opendal/blob/v0.59.5/core/core/src/types/metadata.rs)
- [Feature flags and Rust requirement](https://github.com/apache/opendal/blob/v0.59.5/core/Cargo.toml)
- [Azure Create File](https://learn.microsoft.com/en-us/rest/api/storageservices/create-file)
- [Azure Put Range](https://learn.microsoft.com/en-us/rest/api/storageservices/put-range)
- [Azure Set File Properties](https://learn.microsoft.com/en-us/rest/api/storageservices/set-file-properties)
- [Azure Rename File](https://learn.microsoft.com/en-us/rest/api/storageservices/rename-file)

## Required semantics

### Durability and recovery

- Define what successful file and directory synchronization means on the
  selected service. Do not acknowledge synchronization based only on a local
  cache or an unverified remote API response.
- Preserve write ordering, atomic rename behavior, and recoverable namespace
  changes. If the service cannot provide them directly, specify the required
  journal or transactional metadata layer.
- Support offset overwrites, append, truncation, and the file metadata used by
  the existing guest profile. Unsupported operations must fail explicitly.
- Preserve stable, bounded directory pagination.

### Remote ownership

The existing directory lock is not a remote single-writer protocol. Specify
leases and/or fencing that prevent stale hosts from writing after ownership
is lost, including during network partitions and lease expiry.

Local locks alone are insufficient for writers on different hosts. Native
initialization, guest execution, and recovery must all follow the same
ownership rules.

### Host integration

- Keep credentials and Azure authentication in the host, not the guest image.
  Select the production credential mechanism separately from development auth.
- Bridge asynchronous SDK/HTTP calls into the synchronous backend using a
  reused runtime or worker, rather than creating a runtime for each operation.
- Define request deadlines, retry safety, and error translation. Authentication,
  permission, timeout, quota, and durability failures must reach the caller;
  retries must not duplicate non-idempotent operations.
- Preserve storage audit evidence and quota enforcement. Cache policy, quota
  accounting, and performance targets are still open decisions.

## Validation gates

Use an isolated test share/container and database, never existing PGDATA.

1. Verify the selected service's documented operation and durability contracts.
2. Test range I/O, truncation, metadata, rename, file/directory synchronization,
   pagination, and explicit error propagation against the real service.
3. Prove competing writers are rejected and an expired/fenced writer cannot
   continue modifying data.
4. Exercise interrupted requests, ambiguous write outcomes, lost leases, and
   network failures without success-shaped fallbacks.
5. Run the existing [storage probes](../shared/tests/storage.c), Rust backend
   tests, and [PostgreSQL recovery suite](../shared/recovery.py), including
   synchronized WAL, fresh-VM restart, crash recovery, and injected WAL errors.
6. Measure request counts, latency, and throughput for normal SQL and spilling.
   Emulator tests alone do not establish real-service durability.

VM crash recovery tests are not a certification of remote service power-loss
durability.

## Decisions still open

- Azure Files FileREST versus a Blob-backed filesystem layer versus ADLS REST.
- Exact API version, supported operations, and client dependencies.
- Initialization/import workflow for a fresh remote database.
- Ownership, fencing, and synchronization protocols.
- Cache design, latency budget, quota accounting, and retry policy.

The no-mount requirement is confirmed. Everything above that selects a service
or a new implementation mechanism remains subject to a separate decision.
