---
title: NyVault — Storage as a Daemon-Hosted Service on the IPC Transport
document_id: ADR-0022
version: 0.1.0
status: Proposed
owners: [Nyrqis Architecture]
created: 2026-08-15
updated: 2026-08-15
ai_assisted: true
depends_on: [NPS-003, NPS-004, NPS-011, ADR-0002, ADR-0003, ADR-0007, ADR-0016, ADR-0019, ADR-0020, ADR-0021]
---

> **Status (2026-08-15):** Proposed — drafted for Architecture Group
> review. This ADR names the *service boundary and trust model* for
> NyVault; the cryptographic key-management layer and the hardware
> integration points (the matrix's "C/C++ where hardware integration
> requires it") are explicitly deferred to follow-on ADRs so this
> decision can be reviewed on its own.
>
> **First increment IMPLEMENTED the same day:** the capability-gated
> lifecycle ops (`volume_create/open/list/close/info`) landed with
> real NyFS backing, and the byte path followed (see
> IMPLEMENTATION_STATUS): `volume_write`/`volume_read`/
> `volume_snapshot`/`volume_snapshots` serve real NyFS I/O through
> the handles (create-on-write blob semantics, offset paging, CoW
> snapshots, 32 KiB per-call payload cap pending the FUSE-passthrough
> streaming path).
>
> **The FUSE passthrough (the ADR's data-plane mount) IMPLEMENTED the
> same day (2026-08-15):** `fuse/vault_mount.py` — `NyVaultOperations`
> are FUSE ops whose handlers are storage-service CALLs over the
> authenticated transport (getattr/readdir/read/write/mkdir/mknod/
> unlink/rmdir/rename/truncate/statfs/fsync, paging the 32 KiB
> per-call byte path for kernel-sized requests, errno propagation),
> with `NyVaultMount` mirroring `NyFSMount` (honest deferral without
> fusepy) and `nyrqisctl vault mount`. The service's generic file
> surface sits behind the same capability + handle + path gates.
> The key-management follow-on (ADR-0023) has BOTH its increments
> landed: per-volume wrapped DEKs at `volume_create` and the
> AEAD-encrypted block layer (`NyFSFilesystem(dek=...)`) — **at-rest
> encryption is now claimed** (see ADR-0023).
>
> **Same day, later:** snapshot restore landed (`volume_restore` +
> `nyrqisctl vault restore`), and the live encrypted mount was
> benchmarked (§27): the durable per-CALL commit dominates writes
> (0.28 MB/s for batched 1 MiB writes vs native ~1,700 MB/s; reads
> ~2.1 MB/s). The benchmark surfaced and fixed a real mount bug — the
> passthrough adapter never registered the `init` marker, so the
> write-batching INIT negotiation silently never ran (4 KiB write
> requests, 0.04 MB/s); with the marker + shared capability
> negotiation it is 7× faster. **Write-commit batching** (aggregate
> `save()` at the fsync/interval boundary instead of per CALL) is the
> documented next step.

# ADR-0022 — NyVault: Storage as a Daemon-Hosted Service on the IPC Transport

## Context

Nyrqis's storage pillar (NyFS, ADR-0002/0003/0016/0019) is a
copy-on-write filesystem with per-block checksums, Zstandard
compression, snapshots, and journal-commit durability. Today it is a
host-side FUSE filesystem: the backend mounts it and the container
sees files directly. The language matrix (ADR-0020) names **NyVault /
storage** as a Rust-first platform component, but no decision yet says
what NyVault *is* as a platform surface — how containers obtain durable,
isolated, *named* storage, how that storage is accounted, and how the
trust boundary between a container and its data is enforced.

Three forces shape the decision:

1. **The platform-boundary rule (ADR-0020).** Platform-critical
   execution paths must not depend on the Python interpreter in their
   shipped form. Storage I/O is platform-critical; the byte path into a
   vault must live behind the boundary (the `rust/nyfs` codec crate is
   the first piece, ADR-0020 migration #3).
2. **The capability model (NPS-011).** Every privileged operation in
   Nyrqis is a capability-gated operation. Storage access should be no
   different: a container holds a *volume handle* grant, and the
   backend enforces it at the same enforcement point as every other
   capability.
3. **The transport (NPS-003, ADR-0021).** The daemon already serves
   status + control over the Rust serving loop, with container senders
   authenticated by the kernel-attached pid/uid. A storage *service*
   fits this surface without inventing a second control channel.

## Decision

**NyVault is a daemon-hosted storage service on the IPC transport.** A
container obtains a named volume by CALLing the storage service; the
daemon creates (or opens) a NyFS-backed volume, binds it to the
container's identity, and returns a **volume handle**. Subsequent I/O
operations on that handle are capability-gated calls the daemon serves
— the container never holds the volume's backing files directly.

Specifically:

- **The service rides the existing transport and router.** It
  registers on the daemon's service socket like `status`/`control`
  (ADR-0021's router), with the same receiver-side kernel identity
  (pid → container) and the same operator path. No new wire protocol,
  no new socket: a `service: "storage"` payload with
  `volume_create` / `volume_open` / `volume_read` / `volume_write` /
  `volume_snapshot` / `volume_close` ops.
- **A volume is a NyFS filesystem image** (ADR-0002 semantics: CoW,
  checksums, compression, journal commit). The vault's job is *access
  and lifecycle*, not a second storage format — ADR-0019's journal
  commit stays the durability default.
- **Access is capability-gated.** Every storage op requires
  `CAP_STORAGE_VOLUME` (NPS-011), validated at the same enforcement
  point as `CAP_SYSTEM_INFO` (fail-closed). Volumes are
  **creator-scoped by default**; the creator (or operator) may grant
  another container explicit access (`volume_grant` / `volume_revoke`,
  per-container, persisted with the registry) — grants never imply the
  capability itself. Revocation on container terminate is automatic,
  matching the existing capability-lifecycle hooks.
- **The in-container byte path is a FUSE passthrough to the service**
  (ADR-0016 stays): the container mounts a thin FUSE view whose
  read/write ops are CALLs to the storage service, so the *container's*
  I/O path is the same authenticated, gated transport as everything
  else. The host-side NyFS mount remains the daemon's private view.
- **Reference implementation is Python; the byte path is Rust.**
  Following ADR-0020, the service logic (routing, handles, lifecycle)
  ships first as the Python backend's reference implementation; the
  checksum/compress/journal hot path goes through the existing
  `rust/nyfs` crate (migration #3) and the serving loop (ADR-0021)
  owns the socket when the crate is present. The matrix's "C/C++ where
  hardware integration requires it" is deferred (below).

### What this is NOT

- **Not a database.** NyVault stores filesystem images and opaque
  blobs; queries, indexes, and transactional records are application
  concerns above the vault.
- **Not a key store.** Volume *encryption* and key custody are a
  separate decision — this ADR only fixes the access boundary. A vault
  key manager (daemon-held master key, per-volume keys, hardware-bound
  keys) is a required follow-on ADR before at-rest encryption is
  claimed.
- **Not dm-crypt or kernel-level storage.** No kernel module, no
  device-mapper: this stays a user-space service over NyFS, consistent
  with the containerized execution model (ADR-0004) and the pluggable
  NyHAL backend (ADR-0012).

## Alternatives considered

- **Container-local volumes** (the container owns a directory of
  files; the backend just carves quota). Rejected: there is no
  capability enforcement point — a container with the filesystem
  capability already holds the bytes, so revocation is meaningless and
  accounting is best-effort. NyVault's whole point is that the daemon
  holds the data plane.
- **Kernel-level storage (dm-crypt / LVM-style)**. Rejected for this
  increment: it hard-binds NyVault to a kernel backend, contradicts the
  pluggable-backend goal, and the hardware-binding portion is deferred
  anyway (the matrix's C/C++ carve-out). It may become a *storage
  backend* for NyVault later, behind the service boundary — that is a
  compatible extension, not a fork.
- **Direct service I/O only** (no FUSE view; containers speak the
  storage ops directly). Retained as the *programmatic* path (a Rust
  SDK client), but rejected as the only path: ordinary applications
  need a filesystem, and ADR-0003 already commits to mounted disk
  images. FUSE passthrough keeps both.

## Consequences

- **Positive.** Storage joins the capability model instead of
  bypassing it; the daemon gains a single enforcement point for all
  container data access; volume lifecycle (create/delete/snapshot)
  becomes scriptable over the same control surface operators already
  use; the Rust placement is incremental (codec crate exists, serving
  loop exists).
- **Negative.** Throughput for in-container I/O now crosses the
  service boundary (datagram transport + handler) — the FUSE
  passthrough must be benchmarked before this ADR can exit Proposed
  (same gate-on-data rule ADR-0021 used); the daemon becomes the
  storage bottleneck by design (acceptable: it already is the
  container's life-support process).
- **Deferred (required before at-rest encryption is claimed):** the
  vault key manager ADR (key custody, rotation, hardware binding) and
  the hardware-integration ADR (the matrix's C/C++ carve-out). Volume
  accounting/quota policy is likewise follow-on. The cross-container
  access matrix shipped with 0.14.8 (`volume_grant`/`volume_revoke`/
  `volume_grants` — CREATOR/OPERATOR-ONLY, open-file revoke
  semantics).

### Claimed: per-container quota & accounting (0.14.10)

The follow-on design below shipped with 0.14.10. The grant matrix of
0.14.8 was the precondition — a volume can be *shared*, so usage is
billed to the *writing* container, not the volume's creator.

- **What.** Each container gets a byte quota on the vault; the service
  accounts bytes per container across every volume it writes to, and
  enforces the quota fail-closed at write time.
- **Accounting point.** `volume_write` charges `len(data)` to the
  handle's binding container (the handle already records `container`),
  so a shared volume bills each consumer. Reads are free; truncate
  credits the delta (immediately, so the enforcement cache stays
  honest between commit refreshes). Attribution is a per-path
  **last-writer map** (`owners`), re-keyed on rename, credited on
  truncate, and retained across deletes so a restore re-attributes a
  path to its last writer.
- **Source of truth.** The NyFS tree is the ledger: per-volume,
  per-container usage is a cache refreshed at commit (`volume_fsync` /
  interval tick / close / restore) from the tree's own accounting
  (NyFS's public `walk()`; sum of file sizes = *logical* bytes — the
  operator contract; block-storage bytes are a separate *physical*
  figure the same refresh could compute, with CoW sharing and
  compression making it load-dependent — deliberately NOT billed).
  Deletes and restores re-derive from the tree, so the ledger can
  never drift from what a restore actually frees (verified: delete
  frees, restore re-accounts).
- **Enforcement.** Before applying a write, reject with `EDQUOT` when
  `accounted[container] + write > quota`. Quota is operator-set per
  container (`volume_quota_set`), unlimited by default. Revoking a
  volume grant does not zero accrued usage. The `EDQUOT` errno rides
  the CALL reply, so the FUSE passthrough surfaces it to the kernel.
- **Advisory warnings (0.14.12).** The write path is the hard stop;
  the OPERATIONAL signal is a warning level per quota-holding
  container — `near` (≥ 80%), `at` (≥ 95%), `over` (> 100%) —
  computed at every ledger refresh, logged only on a level transition
  (no spam), and persisted with the registry. `over` is unreachable
  by writing (the write path rejects it) and reachable only via
  re-derivation (a quota set below existing usage, or a restore to a
  larger snapshot). The level surfaces in `volume_quota_get` rows,
  `volume_usage` warnings, `volume_summary`'s `warning_count`, and
  the write reply (the CLI prints it at the point of action).
- **New surface.** `volume_quota_set`/`volume_quota_get`
  (CREATOR/OPERATOR-ONLY — quota is administration, exactly like
  grants), `volume_usage` (per volume per container; any opener),
  `volume_summary` (OPERATOR-ONLY whole-vault aggregate, 0.14.11),
  `EDQUOT` on the write path, and quota/usage/attribution rows in the
  registry's persistence (persisted with each commit, so accounting
  survives a daemon restart). `volume_usage` also carries the
  volume-wide PHYSICAL figure (the on-disk state footprint,
  compressed + CoW-deduped), cached with the ledger at each commit
  and re-derived on demand by the summary; it is volume-wide, never
  per-container, because CoW sharing makes per-container physical
  attribution load-dependent. This was deliberately an increment: the
  enforcement point, the ledger, and the operator surface existed in
  seed form (capability gate + handle binding + `_save_state`).
- **NPS impact:** `CAP_STORAGE_VOLUME` is already in the NPS-011
  registry (the service shipped under it); NPS-004 gains a "storage
  service" section when the platform spec is next touched. No
  renumbering (ADR-0017).

## References

- NPS-003 §3 (CALL/REPLY), §5 (capability validation in IPC)
- NPS-004 (NyFS storage guarantees: CoW, checksums, journal)
- NPS-011 (capability registry)
- ADR-0002 (CoW filesystem), ADR-0003 (mounted disk images),
  ADR-0007 (Zstandard), ADR-0016 (NyFS FUSE backend),
  ADR-0019 (journal commit default), ADR-0020 (languages + boundary),
  ADR-0021 (serving loop / transport boundary)
