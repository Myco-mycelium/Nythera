# How to Operate the NyVault Storage Service

*Applies to: running, securing, and recovering NyVault — the daemon-hosted
storage service on the Linux Backend (`source/nyhal-linux-backend/`,
ADR-0022). Every command below talks to the daemon's main socket over the
kernel-authenticated IPC transport, so the daemon must be running.*

## When you need this

You're an operator (or a container granted `CAP_STORAGE_VOLUME`) and you
want to store data in the vault, keep it encrypted at rest, mount it like a
filesystem, rotate the key, or back it up. The vault is a daemon-hosted
service: the daemon owns the volume images, the volume registry, and the
keys — a client holds only an opaque volume handle.

## Concepts (30 seconds)

- **KEK** — the master key, derived from your passphrase (Argon2id) and
  held ONLY in the daemon (the Rust keys crate's handle table when built;
  the Python floor otherwise). It never touches disk in plaintext.
- **KEK envelope** — the only persisted key material: 110 bytes
  (salt + KDF params + an AEAD check value). It is **not a secret** —
  your passphrase is. Anyone with the envelope but not the passphrase
  can do nothing.
- **DEK** — a fresh random key per volume, wrapped by the KEK. The
  wrapped DEK persists (in `volumes.json`); the plaintext DEK lives only
  in the daemon's memory. Every block at rest is
  `nonce ‖ ciphertext ‖ tag` under the volume's DEK — no plaintext ever
  lands under the vault directory.

## Steps

### 1. Initialize the vault (once)

Write the KEK envelope. `nyrqisctl vault init` is a LOCAL command — it
does not need the daemon:

```bash
nyrqisctl vault init /var/lib/nyrqis/vault.key \
  --passphrase 'choose-a-long-passphrase'
# or: NYRQIS_VAULT_PASSPHRASE=... nyrqisctl vault init ...
```

The envelope is written to the path you give; chmod 0644 is fine.

### 2. Serve the daemon with the vault

```bash
nyrqis_backend.py service serve \
  --socket /run/nyrqis/status.sock \
  --vault-dir /var/lib/nyrqis/vault \
  --vault-key-file /var/lib/nyrqis/vault.key \
  --vault-passphrase 'the-same-passphrase'   # or NYRQIS_VAULT_PASSPHRASE
```

The daemon unlocks the KEK at serve time and fails closed on a wrong
passphrase. Without `--vault-key-file` the vault serves **plaintext**
(the crate-less fallback) — an encrypted vault served with the wrong or
no key refuses to open volumes with an honest "vault key mismatch" /
"vault locked" error.

Under systemd, the unit wires all of this for you:
`StateDirectory=nyrqis` (persistent `/var/lib/nyrqis`),
`--vault-dir /var/lib/nyrqis/vault`, `--vault-key-file
/var/lib/nyrqis/vault.key`, and the passphrase from the optional
`EnvironmentFile=-/etc/nyrqis/backend.env` (`NYRQIS_VAULT_PASSPHRASE=...`).

### 3. Create volumes and use the byte path

```bash
# Create a named volume (capability-gated; the operator is always allowed)
nyrqisctl vault create assets

# Open by name or id — you get an opaque handle (the access token)
nyrqisctl vault open --name assets
# -> handle 3f9c...  (keep it for the next calls)

# Write bytes (stdin or --file), read them back (stdout or --output)
nyrqisctl vault write "$HANDLE" /assets/logo.png --file logo.png
nyrqisctl vault read  "$HANDLE" /assets/logo.png --output copy.png

# CoW snapshots: snapshot, overwrite, restore, delete
nyrqisctl vault snapshot      "$HANDLE" v1
nyrqisctl vault snapshots     "$HANDLE"
nyrqisctl vault restore       "$HANDLE" v1
nyrqisctl vault snapshot-delete "$HANDLE" v1   # drop the point-in-time copy

# Delete = crypto-shred: handles + wrapped DEK + backing image + registry
nyrqisctl vault delete --name assets
```

Per-call payloads are capped at 32 KiB (the datagram budget) — page
large blobs with `--offset`/`--size`, or use a mount (next step).

### 3b. Let another container in (grants)

A volume is **creator-scoped by default**: only the creating container
(or the operator) may open it. The creator can grant another container
explicit access, and revoke it later:

```bash
# Creator or operator:
nyrqisctl vault grant  --name assets container-b  # by id or --name
nyrqisctl vault grants --name assets             # who has access
nyrqisctl vault revoke --name assets container-b # withdraw it
```

Grants are per-container, persist with the registry, and never imply
the storage capability itself (the grantee still needs
`CAP_STORAGE_VOLUME` to reach the service). Revoking gates future
opens; a handle already open keeps working (POSIX open-file
semantics) until it is closed.

### 3c. Quotas and accounting (per container)

Every volume accounts bytes **per writing container** (ADR-0022's
accounting increment): a shared volume bills each consumer its own
writes, not the creator's. The creator (or the operator) can cap a
container's consumption; the cap is enforced **fail-closed at write
time** with `EDQUOT`:

```bash
# Creator or operator:
nyrqisctl vault quota-set --name assets container-b --bytes 1048576
nyrqisctl vault quota-get --name assets          # quota + usage rows
nyrqisctl vault usage     --name assets          # per-container usage
nyrqisctl vault quota-set --name assets container-b --unlimited  # clear
```

Semantics to rely on:

- **What counts is LOGICAL bytes** — the sum of file sizes in the
  volume tree, attributed to the last writer of each path. Physical
  block bytes (CoW sharing, compression) are NOT what quotas measure.
- **The tree is the ledger.** Usage is re-derived from the tree at
  each commit (fsync / interval / close / restore), so deleting a
  file, truncating it, or restoring a snapshot re-accounts exactly
  what the tree holds — freed bytes are credited at the next commit
  (a truncate credits immediately).
- **Quotas are unlimited by default** and per-container: a quota on
  one container never caps another's consumption of the same volume.
- Setting a quota never implies the storage capability, and a granted
  container cannot set (or read) quotas — that is creator/operator
  administration only.

### 4. Mount a volume as a filesystem

Requires `fusepy` + `/dev/fuse` + `fusermount`. The mount's operations
are storage-service CALLs, so the daemon must be running:

```bash
nyrqisctl vault mount --name assets /mnt/assets
# prints "mounted volume ... (serving until unmounted)" and BLOCKS —
# the process serves the FUSE loop; unmount with:
fusermount -u /mnt/assets
```

The mounted volume is encrypted at rest like any other (kernel writes
ride the AEAD block layer; verified — no plaintext under the vault dir).

**Durability through the mount (group commit).** Writes defer the
durable commit: data is visible to readers immediately, and is
persisted at `fsync()`, at unmount/handle close, or at the daemon's
commit-interval tick (`--commit-interval`, default 5 s — one save per
interval for a burst of short-lived files instead of one per close,
BENCHMARK_RESULTS §27). `close()` alone is NOT a durability boundary
(POSIX semantics — call `fsync()` when the write must survive a
daemon crash before the next tick; the interval bounds the loss
window).

The kernel mount itself is the **operator/host view**: seccomp
containers cannot mount FUSE by design (`mount`/`umount2` are in the
backend's always-deny set) — a container's data plane is the CALL
path (a granted container drives the same operations over the wire,
verified end-to-end).

### 5. Rotate the KEK (rekey) — no re-encryption

Changing the passphrase re-wraps every volume's DEK with the new KEK;
**no block is re-encrypted** (the DEKs, hence all ciphertext, are
untouched). Operator-only:

```bash
nyrqisctl vault rekey \
  --new-passphrase 'the-new-passphrase' \
  --new-key-file /var/lib/nyrqis/vault.key.new
# -> rekeyed N volume(s) — restart the daemon with the new key file
```

Then restart the daemon with `--vault-key-file vault.key.new` + the new
passphrase. The old key file can no longer open any volume (fail-closed).
Rotate the passphrase whenever you suspect it leaked, or on a schedule.

### 6. Backup and restore the vault

**Back up** (stop the daemon first, or back up while stopped):
- the **vault directory** (`--vault-dir`): the per-volume `.nyfs`
  images (ciphertext blocks), `volumes.json` (registry + wrapped DEKs
  + snapshots) — this is the durable data;
- the **KEK envelope file** and the **passphrase** (in your secret
  store — the passphrase is the real secret).

```bash
tar czf vault-backup.tgz /var/lib/nyrqis
```

**Restore** onto a fresh host:
1. Place the vault dir back (e.g. `/var/lib/nyrqis`).
2. Place the key envelope at the path the unit expects
   (`/var/lib/nyrqis/vault.key`).
3. Serve with the same passphrase (`EnvironmentFile`/`--vault-passphrase`).
4. `nyrqisctl vault list` — volumes appear; open/read works.

Both the envelope AND the passphrase are required: the data is
ciphertext without the passphrase, and the passphrase is useless
without the envelope's salt/KDF parameters.

### 7. Security notes

- **The passphrase is the crown jewel** — it derives the KEK, and the
  KEK is never stored in plaintext. Losing it is losing the vault.
- At rest: only ciphertext (AEAD-verified on read) + the 110-byte
  envelope. The vault dir never contains plaintext.
- `volume_delete` crypto-shreds: the ciphertext may remain on disk, but
  no key path survives — treat deleted data as unrecoverable.
- The byte path is capability-gated (`CAP_STORAGE_VOLUME`); volumes
  are creator-scoped unless the creator grants access; rekey and
  delete are operator-only.
- Hardening (future): TPM2/PKCS#11 hardware custody of the KEK behind
  the Rust trait (ADR-0023, deferred).

## Verify it works

```bash
nyrqisctl vault list                    # no volumes initially
nyrqisctl vault create smoke
H=$(nyrqisctl vault open --name smoke | cut -d' ' -f2)
echo hello | nyrqisctl vault write "$H" /greeting.txt
nyrqisctl vault read "$H" /greeting.txt  # -> hello
find /var/lib/nyrqis -type f | xargs grep -l hello || echo "no plaintext at rest"
```

## References

- ADR-0022 (NyVault — storage as a daemon-hosted service)
- ADR-0023 (NyVault key manager — envelope encryption, rotation, custody)
- `tests/BENCHMARK_RESULTS.md` §26 (byte path) and §27 (live mount)
- `CHANGELOG_LINUX_BACKEND.md` 0.14.4–0.14.9 (the increments this guide covers)
