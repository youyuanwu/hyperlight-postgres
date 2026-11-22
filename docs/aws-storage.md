# AWS remote storage backend choices

Status: exploration; no AWS backend has been selected or implemented.
Service documentation checked on 2026-11-22. Recheck before implementation.
No AWS service calls, protocol-client tests, or durability tests were run.

## Scope

AWS provides managed filesystems, but their file-access protocols differ from
Azure Files' FileREST API. A filesystem management API is not a file-I/O API:
creating a filesystem through an AWS SDK does not provide operations for reading
or overwriting bytes in an individual file.

This document evaluates AWS options against the direct, no-host-mount direction
described in [Remote storage backend choices](remote-storage.md). The integration
point remains the shared [Rust storage host](../shared/storage-host/src/main.rs),
which implements Hyperlight-Unikraft's `FsBackend`.

The required contract includes offset reads/writes, stat, directory operations,
truncation, rename, stable directory pagination, and file/directory
synchronization. A remote backend also needs ownership/fencing, quota accounting,
and a remote initialization/import workflow.

## Service comparison

| Service | File/data access | Direct filesystem REST API? | Assessment for this project |
| --- | --- | --- | --- |
| Amazon EFS | NFSv4.0/4.1 | No; AWS SDK APIs manage filesystems, mount targets, policies, and access points, not individual file reads/writes. | A userspace NFS client could access the filesystem without an OS mount. Client suitability and PostgreSQL semantics need investigation. |
| Amazon FSx | NFS, SMB, or Lustre, depending on the variant | The FSx management API is not a general file-I/O API. | NFS-capable variants, such as OpenZFS and NetApp ONTAP, are candidates for userspace protocol access. Windows File Server uses SMB; Lustre requires its own client stack. |
| Amazon S3 | HTTPS object API, with the official AWS SDK for Rust (`aws-sdk-s3`) | Object operations, not a POSIX filesystem API. | Direct Rust access is available, but a custom filesystem layer must supply the missing semantics. |

EFS and FSx do not provide an Azure FileREST-style SDK interface for
`read(offset)`, `write(offset)`, `truncate`, and `rename`. Their file protocols
and their AWS management APIs are separate interfaces.

## No-host-mount approaches

### Userspace NFS to EFS or an NFS-capable FSx variant

A protocol client in the Rust storage host could communicate directly with the
service's NFS endpoint without mounting it through the host operating system.
This is a possible architecture, not a selected or validated implementation.
The official AWS EFS/FSx SDK clients do not supply the NFS data-access client.

Before selecting a client or service:

- Verify the client's supported NFS version and required operations, including
  offset I/O, truncation, rename, metadata, and directory pagination.
- Establish write stability, commit behavior, and namespace ordering rather
  than assuming every successful write is equivalent to `fsync`.
- Validate locking, reconnects, failover, and stale-writer rejection. Protocol
  locks alone are not evidence of a complete ownership/fencing design.
- Define network reachability and authentication/encryption. For EFS, mount
  targets expose NFS endpoints within a VPC; a userspace client must explicitly
  support any required IAM authorization and TLS integration.
- Measure round-trip latency and throughput for PostgreSQL page and WAL writes.

Userspace NFS is the closest filesystem-oriented path to investigate under the
no-host-mount requirement. It is not yet evidence that EFS or FSx is suitable
for this PostgreSQL backend.

### S3 SDK with a custom filesystem layer

The official Rust SDK provides direct HTTPS object access without an OS mount.
However, an S3 client or generic upload/download wrapper is not a complete
`FsBackend`.

The adapter would need a design for offset-write handling, truncation, directory
and file metadata, recoverable namespace operations, synchronization, and
ownership/fencing. Range reads do not establish arbitrary in-place overwrite
support, and successful object uploads do not by themselves define the guest's
file/directory synchronization contract. Storage-class-specific features must
be checked individually rather than assumed to supply filesystem semantics.

Avoid whole-file read/modify/reupload as a shortcut for repeated PostgreSQL
page writes. Any object-backed layout, cache, journal, or transactional metadata
layer requires a separate design and performance evaluation.

## Validation and open decisions

The [required semantics and validation gates](remote-storage.md#required-semantics)
also apply to an AWS backend: explicit error propagation, retry safety, remote
ownership, durable ordering, initialization, quota enforcement, and recovery.
Keep AWS credentials in the host, not the guest image. Use isolated AWS test
resources and a fresh database, never existing PGDATA.

Decisions still open:

- Userspace NFS versus a custom S3-backed filesystem layer.
- Exact service/variant, protocol version, and Rust client dependencies.
- Authentication, network access, ownership/fencing, and synchronization.
- Initialization/import, cache policy, quotas, latency budget, and retry policy.

No AWS backend decision changes the existing Azure investigation.

## Sources

- [How Amazon EFS works: NFS versions and mount targets](https://docs.aws.amazon.com/efs/latest/ug/how-it-works.html)
- [Amazon EFS API reference](https://docs.aws.amazon.com/efs/latest/APIReference/Welcome.html)
- [Amazon FSx overview and filesystem variants](https://aws.amazon.com/fsx/)
- [Amazon FSx management API reference](https://docs.aws.amazon.com/fsx/latest/APIReference/welcome.html)
- [Amazon S3 overview and object storage model](https://docs.aws.amazon.com/AmazonS3/latest/userguide/Welcome.html)
- [AWS SDK for Rust](https://docs.aws.amazon.com/sdk-for-rust/latest/dg/getting-started.html)
- [Amazon S3 Rust client API](https://docs.rs/aws-sdk-s3/latest/aws_sdk_s3/)
