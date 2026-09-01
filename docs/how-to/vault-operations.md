# How to Operate NyVault Storage

*Applies to: running, securing, and recovering the NyVault storage
service — the daemon-hosted encrypted volume system.*

## Overview

NyVault is the daemon-hosted storage service (ADR-0022) that provides
encrypted, capability-gated volumes over the IPC transport. Every volume
gets its own data-encryption key (DEK) wrapped by the daemon's
key-encryption key (KEK), with at-rest encryption verified end-to-end.

## Prerequisites

```bash
pip install pynacl
```

## Initializing the vault

Before creating encrypted volumes, initialize a key file:

```bash
python3 nyrqisctl.py vault init --key-file /run/nyrqis/vault.key
```

You'll be prompted for a passphrase. This derives the KEK via Argon2id
and writes a 110-byte envelope to the key file.

Start the daemon with the key:

```bash
python3 nyrqis_backend.py service serve \
  --socket /run/nyrqis/status.sock \
  --vault-dir /var/lib/nyrqis/vault \
  --vault-key-file /run/nyrqis/vault.key
```

Without `--vault-key-file`, volumes are created **unencrypted** (the
DEK is not wrapped).

## Volume lifecycle

### Create a volume

```bash
python3 nyrqisctl.py vault create --name mydata
```

With an unlocked KEK, this:
1. Generates a random 32-byte DEK
2. Wraps it with the KEK (ad = volume id)
3. Creates a NyFS filesystem root
4. Persists the wrapped DEK in `volumes.json`

### List volumes

```bash
python3 nyrqisctl.py vault list
```

### Open a volume

```bash
python3 nyrqisctl.py vault open --name mydata
# Returns a handle for subsequent operations
```

### Write data

```bash
# From a file
python3 nyrqisctl.py vault write --name mydata --path /data.txt --file local.txt

# From stdin
echo "hello world" | python3 nyrqisctl.py vault write --name mydata --path /greeting.txt

# Overwrite at an offset
python3 nyrqisctl.py vault write --name mydata --path /data.txt --file patch.bin --offset 1024
```

### Read data

```bash
# To stdout
python3 nyrqisctl.py vault read --name mydata --path /greeting.txt

# To a file
python3 nyrqisctl.py vault read --name mydata --path /data.txt --output local.txt

# Read a range
python3 nyrqisctl.py vault read --name mydata --path /data.txt --offset 0 --size 1024
```

### Close a handle

```bash
python3 nyrqisctl.py vault close --handle <handle-id>
```

## Snapshots

Snapshots use NyFS's copy-on-write — they're instant and
space-efficient (only modified blocks are duplicated).

```bash
# Create a snapshot
python3 nyrqisctl.py vault snapshot --name mydata

# List snapshots
python3 nyrqisctl.py vault snapshots --name mydata

# Restore to a snapshot
python3 nyrqisctl.py vault restore --name mydata --snapshot <snapshot-id>

# Delete a snapshot
python3 nyrqisctl.py vault snapshot-delete --name mydata --snapshot <snapshot-id>
```

## Cross-container sharing (grants)

By default, only the creator can access a volume. Grants let other
containers open the volume:

```bash
# Grant whole-volume access
python3 nyrqisctl.py vault grant --name mydata --container other-container

# Grant path-scoped access (only /assets subtree)
python3 nyrqisctl.py vault grant --name mydata --container other-container --path /assets

# List grants
python3 nyrqisctl.py vault grants --name mydata

# Revoke access (live handles stay valid until closed)
python3 nyrqisctl.py vault revoke --name mydata --container other-container
```

## Per-container quotas

Set byte limits per container to prevent runaway writes:

```bash
# Set a 100 MiB quota for a container
python3 nyrqisctl.py vault quota-set --name mydata --container myapp --bytes 104857600

# Set a path-scoped quota (only /uploads)
python3 nyrqisctl.py vault quota-set --name mydata --container myapp \
  --path /uploads --bytes 52428800

# Check quotas
python3 nyrqisctl.py vault quota-get --name mydata

# View usage
python3 nyrqisctl.py vault usage --name mydata

# Clear a quota
python3 nyrqisctl.py vault quota-set --name mydata --container myapp --bytes null
```

## KEK rotation

Rotate the key without re-encrypting any data:

```bash
# Create a new key file
python3 nyrqisctl.py vault rekey \
  --name mydata \
  --new-key-file /run/nyrqis/vault-v2.key
```

This:
1. Unwraps the DEK with the current KEK
2. Re-wraps it with the new KEK
3. Persists the new wrapped DEK

Restart the daemon under the new key file. The old key file fails
closed with "vault key mismatch".

## Monitoring

```bash
# View events (quota warnings, grants, revocations)
python3 nyrqisctl.py vault events --name mydata

# View vault summary (all volumes)
python3 nyrqisctl.py vault summary

# View daemon status (includes vault aggregate)
python3 nyrqisctl.py status
```

## FUSE mount (kernel-visible)

Mount a volume as a real filesystem:

```bash
python3 nyrqisctl.py vault mount --name mydata /mnt/vault

# Now use it like a regular directory
ls /mnt/vault/
echo "data" > /mnt/vault/file.txt

# Unmount
fusermount -u /mnt/vault
```

The mount is operator-only — containers access volumes through IPC
CALLs, not kernel mounts.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| "vault locked: the KEK is not unlocked" | Daemon started without `--vault-key-file` | Restart with the key file |
| "vault key mismatch" | Wrong key file (after rotation) | Use the new key file |
| "quota exceeded (EDQUOT)" | Container hit its byte quota | Increase quota or clear it |
| "forbidden: not yours" | Container doesn't have a grant | Grant access or check creator |

## Security properties

- **At-rest encryption**: every block is `nonce ‖ ciphertext ‖ tag`
  (XChaCha20-Poly1305); plaintext never appears under the vault dir
- **KEK custody**: the Rust crate holds the KEK in an opaque handle
  table; the plaintext never crosses the FFI boundary
- **Crypto-shredding**: `volume_delete` destroys the wrapped DEK,
  making all data unrecoverable
- **Capability-gated**: every operation requires `CAP_STORAGE_VOLUME`
  (containers) or operator authorization
