---
title: NyVault Key Manager — Envelope Encryption with Rust-Held Key Custody
document_id: ADR-0023
version: 0.1.0
status: Proposed
owners: [Nyrqis Architecture]
created: 2026-08-15
updated: 2026-08-15
ai_assisted: true
depends_on: [NPS-004, NPS-011, ADR-0002, ADR-0014, ADR-0020, ADR-0022]
---

> **Status (2026-08-15):** Proposed — drafted for Architecture Group
> review as the required follow-on to ADR-0022. This ADR fixes the
> *key custody and encryption boundary* for NyVault; the concrete
> hardware backends (TPM2, PKCS#11 token) are named as pluggable
> implementations behind a Rust trait and are NOT required for the
> first increment.

# ADR-0023 — NyVault Key Manager: Envelope Encryption with Rust-Held Key Custody

## Context

ADR-0022 made NyVault a daemon-hosted storage service: containers
obtain capability-gated volume handles, and the daemon holds the data
plane. It explicitly deferred **volume encryption and key custody** —
"a vault key manager (daemon-held master key, per-volume keys,
hardware-bound keys) is a required follow-on ADR before at-rest
encryption is claimed."

The forces:

1. **The platform-boundary rule (ADR-0020).** Key custody is the most
   sensitive platform-critical path that exists: the plaintext that
   must never leave a guarded process. Per the language matrix,
   **security services are Rust** — the interpreter must not hold, and
   therefore cannot leak, plaintext key material.
2. **At-rest threat model.** The daemon already encrypts nothing at
   rest: NyFS images (ADR-0002) are checksummed and compressed but
   plaintext on disk. NyVault's *service* boundary (ADR-0022) protects
   access while the daemon runs; it does nothing for a stolen disk or
   a copied volume image. Encryption must be a property of the stored
   bytes, not of the access check.
3. **Rotation and revocation.** A stolen-key response, a departing
   container, or an operator rotation must be expressible without
   re-encrypting volume data. That requirement structurally forces
   **envelope encryption**: cheap per-volume key rotation and instant
   cryptographic revocation.
4. **The boot chain is a separate trust domain (ADR-0014).** Secure
   boot keys attest *which* daemon binary runs; the vault KEK is a
   *runtime* trust root unlocked by an operator. The two must not be
   conflated — but the boot chain does protect the daemon that hosts
   the key manager, so they compose.

## Decision

**NyVault encrypts volume data with per-volume data-encryption keys
(DEKs) using XChaCha20-Poly1305 (libsodium), and wraps every DEK with
a daemon-held key-encryption key (KEK) — envelope encryption.** Key
custody lives in a Rust key-manager crate behind the FFI boundary;
the Python backend and the IPC services interact with it only through
opaque handles and never receive plaintext key material.

Specifically:

- **Per-volume DEK.** Each NyFS volume gets a random 256-bit DEK at
  `volume_create`. The NyFS image is encrypted with that DEK at the
  block layer (ADR-0002 blocks, checksum-*then*-encrypt: the stored
  block is `AEAD(plaintext_block)` and the checksum covers the
  ciphertext, so a tampered block fails both checksum and AEAD
  verification — defense in depth, no ordering ambiguity).
- **Wrapped DEKs at rest.** The DEK is wrapped (AEAD-encrypted) with
  the KEK and the wrapped blob is stored in the volume's metadata,
  alongside the volume record in the daemon's vault state. A volume
  image without its wrapped-DEK metadata (a copied `.nyfs` file, a
  raw disk grab) is ciphertext with no key — this is the at-rest
  property the service boundary cannot provide.
- **The KEK is never stored in plaintext.** The daemon holds it only
  in the Rust key manager's guarded memory after an **unlock**
  operation. Persisted is a *wrapped KEK* blob, unlocked by either:
  - **Passphrase unlock** — Argon2id (memory-hard KDF) derives the
    KEK-wrapping key from an operator-supplied unlock secret; the
    wrapped-KEK blob is the only thing on disk; or
  - **Hardware-bound unlock** — a pluggable backend (TPM2 sealing /
    PKCS#11 token) wraps the KEK in hardware. This is the matrix's
    "C/C++ where hardware integration requires it": the *interface*
    is a Rust trait in this crate; concrete backends are separate
    crates behind it, deferred to a follow-on ADR. The passphrase
    path is the first increment's default.
- **Rotation without re-encryption.** Rotating the KEK re-wraps every
  DEK (cheap, metadata-only). Rotating a *volume's* DEK re-encrypts
  that volume's blocks (expensive, explicit operator op). Both are
  first-class operations because the envelope separates them.
- **Crypto-shredding revocation.** Revoking a volume = deleting its
  wrapped DEK from the vault state. The ciphertext may remain on disk
  indefinitely; without the wrapped DEK there is no key path. The
  existing capability lifecycle (NPS-011, ADR-0022) revokes the
  *handle* on container terminate; crypto-shredding is the stronger,
  explicit operator action for the *data*.
- **Rust custody behind FFI.** The key-manager crate (`rust/keys/`)
  follows ADR-0021's FFI pattern: the Python backend links a
  cdylib and receives **handles**, not keys. Operations
  (`keys_unlock`, `keys_wrap`, `keys_unwrap`, `keys_rotate_kek`,
  `keys_shred`) take and return opaque ids; the plaintext KEK and
  unwrapped DEKs exist only inside the crate's memory. A Python
  crash, a logged argument, a traceback — none can carry key
  material, because Python never held it. This is ADR-0020's rule
  applied to the single most sensitive path.
- **First crypto dependency.** The existing `rust/` crates are
  libc-only. This ADR approves the first non-libc dependency for the
  keys crate specifically: **libsodium** (via its stable FFI),
  chosen for a battle-tested XChaCha20-Poly1305, Argon2id, and
  constant-time compare in one audited library. Pure-Rust
  (RustCrypto) remains a viable equivalent behind the same trait
  surface; the crate is the seam, not the vendor.

### What this is NOT

- **Not full-disk encryption.** No dm-crypt, no kernel crypto stack:
  consistent with ADR-0022 ("not dm-crypt or kernel-level storage")
  and the pluggable NyHAL model. Encryption is a property of NyFS
  images as stored by the vault.
- **Not a PKCS#11 service** and not a general key-value HSM. The
  manager wraps and unwraps *its own* DEKs; it does not vend keys to
  arbitrary callers.
- **Not a boot-time requirement.** The vault unlocks when the daemon
  serves vault traffic (operator action), not at boot — ADR-0014's
  chain attests the binary, this ADR's unlock authorizes the data.
  A daemon that never unlocks serves an unreadable vault
  (fail-closed), which is the desired property for stolen-hardware
  scenarios.

## Alternatives considered

- **One master key, volume data encrypted directly.** Rejected: no
  rotation without re-encrypting everything, and one compromised key
  exposes every volume. Envelope encryption costs one extra AEAD per
  block-open and buys per-volume isolation and cheap rotation.
- **Python-held keys with OS-level protection (keyring/secret
  service).** Rejected: violates ADR-0020's boundary rule on the most
  sensitive path, and a Python key holder is a single interpreter
  compromise away from exfiltrating every plaintext key in the
  daemon's memory. The whole point of the FFI seam is that the
  interpreter cannot reach the keys.
- **Hardware-only custody (HSM as a hard requirement).** Rejected for
  the first increment: it makes the common path depend on hardware
  that consumer installs may not have, contradicting NTM-000's
  simplicity principle, and ADR-0022 already defers hardware
  integration. The trait + passphrase default keeps hardware an
  additive option rather than a gate.
- **Per-block keys.** Rejected: per-volume keys give the same
  cryptographic isolation with O(volume) key operations instead of
  O(blocks), and the block layer stays simple (one DEK, one AEAD
  nonce domain with a per-block counter).

## Consequences

- **Positive.** At-rest encryption becomes a property of the stored
  bytes; stolen images are ciphertext; rotation is metadata-cheap;
  revocation is cryptographic; and the interpreter is structurally
  excluded from key material, closing the most sensitive path per
  ADR-0020.
- **Negative.** Encryption cost on the block path (AEAD per block)
  and an unlock step before vault traffic can be served — the
  fail-closed default trades operator convenience for stolen-media
  safety, which is the correct trade for a storage pillar.
- **Deferred.** Hardware backends (TPM2, PKCS#11), KEK rotation
  policy (scheduled vs manual), and the FUSE passthrough's
  interaction with the encrypted block layer are follow-on decisions.
  This ADR fixes the custody and envelope model so those can be made
  against a stable seam.
