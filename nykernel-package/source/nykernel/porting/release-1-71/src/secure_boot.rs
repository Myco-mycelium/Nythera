//! Secure boot v0 (Phase 10, increment 1-32): verifies the boot image before it may run.
//!
//! GOVERNANCE (read with `RULE_CHANGES.md`): ADR-0014 (UEFI Secure Boot with user-enrollable keys) is **Accepted**. This is NOT the UEFI chain -- there is no UEFI, no shim and no
//! firmware key store in this boot path (BIOS + GRUB2 + Multiboot2, NPS-001 §5 Stage 1 as built). It is the Nyrqis-owned half of the model: a platform root key baked into the
//! kernel, user-enrollable keys, platform-signed revocation, an explicit and VISIBLE user switch to turn it off, and status reporting (NPS-023 FIND-BOOT-001). The
//! primitives are the ones the Architecture Group reviewed (AG decision D4 / NPS-026 §6.7): **Ed25519 + SHA-256**, fingerprint = SHA-256(public key), shown as the full 64-hex form.
//!
//! Objects (all little-endian; all arrive as loader modules next to `bootimg`):
//!  * `bootsig`  "NYBS0001" | signer public key [32] | signature [64]   -- signature over  "NYRQIS-BOOT-V1\0" || SHA-256(boot image)
//!  * `sbpolicy` "NYSP0001" | sequence u64 | n_keys u32 | n_images u32 | revoked key fingerprints [n_keys][32] | revoked image digests [n_images][32] | signature [64] by the PLATFORM key
//!               over "NYRQIS-POLICY-V1\0" || everything before the signature.
//!  * `mokdb`    "NYMK0001" | n u32 (<= 8) | keys [n][32] | optional signature [64] by the PLATFORM key over "NYRQIS-MOKDB-V1\0" || everything before the signature.
//! Modes (kernel command line `sb=`): absent/`enforce` -- an unsigned `mokdb` is IGNORED; `enroll` -- an unsigned `mokdb` is accepted, as an act of PHYSICAL PRESENCE (the boot menu
//! entry that passes it must be chosen by hand; ADR-0014/NPS-023 FIND-BOOT-004 accept physical presence as the enrollment authentication); `off` -- the user turned it off:
//! no signature is required, loudly.
//! NOT done (see RULE_CHANGES.md): expiry (no trusted clock), anti-rollback of `sequence` (no persistent state yet), cross-signature key rotation, TPM measurement, UEFI.

use ed25519_dalek::{Signature, VerifyingKey};
use sha2::{Digest, Sha256};

/// The platform root public key baked in at build time (tools/sb_tool.py writes target/pk.pub from keys/platform.key).
pub static PLATFORM_KEY: [u8; 32] = *include_bytes!("../target/pk.pub");

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Mode { Enforce, Enroll, Off }

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Denied {
    NoSignature, SignatureMalformed, PolicyMalformed, PolicyNotSignedByPlatform, MokMalformed, MokNotSignedByPlatform,
    SignerNotTrusted, SignerRevoked, ImageRevoked, SignatureInvalid,
}
impl Denied {
    pub fn why(self) -> &'static str {
        match self {
            Denied::NoSignature => "the boot image is unsigned (no `bootsig` module)",
            Denied::SignatureMalformed => "`bootsig` is malformed",
            Denied::PolicyMalformed => "`sbpolicy` is malformed",
            Denied::PolicyNotSignedByPlatform => "`sbpolicy` is not signed by the platform key",
            Denied::MokMalformed => "`mokdb` is malformed",
            Denied::MokNotSignedByPlatform => "`mokdb` carries a signature that is not the platform key's",
            Denied::SignerNotTrusted => "the signer is neither the platform key nor an authorised user-enrolled key",
            Denied::SignerRevoked => "the signing key is revoked by the platform policy",
            Denied::ImageRevoked => "this exact boot image is revoked by the platform policy",
            Denied::SignatureInvalid => "the signature does not verify for this boot image",
        }
    }
}

#[derive(Clone, Copy)]
pub struct Verdict {
    pub signer_fp: [u8; 32],
    pub platform_signer: bool,
    pub user_keys_accepted: usize,
    pub user_keys_ignored: usize,
    pub policy_sequence: Option<u64>,
}

pub fn parse_mode(cmdline: &[u8]) -> Mode {
    for word in cmdline.split(|&b| b == b' ') {
        match word {
            b"sb=enroll" => return Mode::Enroll,
            b"sb=off" => return Mode::Off,
            _ => {}
        }
    }
    Mode::Enforce
}

pub fn fingerprint(pubkey: &[u8; 32]) -> [u8; 32] { Sha256::digest(pubkey).into() }

pub fn hex64(d: &[u8; 32]) -> [u8; 64] {
    let mut s = [0u8; 64];
    for (i, b) in d.iter().enumerate() { s[2 * i] = b"0123456789abcdef"[(b >> 4) as usize]; s[2 * i + 1] = b"0123456789abcdef"[(b & 15) as usize]; }
    s
}

fn u32_at(d: &[u8], o: usize) -> Option<u32> { Some(u32::from_le_bytes(d.get(o..o + 4)?.try_into().ok()?)) }
fn u64_at(d: &[u8], o: usize) -> Option<u64> { Some(u64::from_le_bytes(d.get(o..o + 8)?.try_into().ok()?)) }

/// `sig` over `tag || signed` by `key` (strict verification: rejects non-canonical / small-order encodings).
fn verify(key: &[u8; 32], tag: &[u8], signed: &[u8], sig: &[u8; 64]) -> bool {
    let vk = match VerifyingKey::from_bytes(key) { Ok(k) => k, Err(_) => return false };
    let mut buf = [0u8; 2048]; // Ed25519 signs the message itself (not a digest): build tag || signed in a bounded buffer (all signed objects here are < 2 KiB)
    if tag.len() + signed.len() > buf.len() { return false; }
    buf[..tag.len()].copy_from_slice(tag);
    buf[tag.len()..tag.len() + signed.len()].copy_from_slice(signed);
    vk.verify_strict(&buf[..tag.len() + signed.len()], &Signature::from_bytes(sig)).is_ok()
}

/// The decision. Pure: all inputs are byte slices, nothing is run or mapped.
pub fn evaluate(image: &[u8], bootsig: Option<&[u8]>, policy: Option<&[u8]>, mokdb: Option<&[u8]>, mode: Mode) -> Result<Option<Verdict>, Denied> {
    if mode == Mode::Off { return Ok(None); }
    // 1. platform policy (revocations): optional, but when present it MUST be authentic (fail closed).
    let mut revoked_keys: &[u8] = &[];
    let mut revoked_imgs: &[u8] = &[];
    let mut seq = None;
    if let Some(p) = policy {
        if p.len() < 8 + 8 + 4 + 4 + 64 || &p[..8] != b"NYSP0001" { return Err(Denied::PolicyMalformed); }
        let nk = u32_at(p, 16).ok_or(Denied::PolicyMalformed)? as usize;
        let ni = u32_at(p, 20).ok_or(Denied::PolicyMalformed)? as usize;
        if nk > 16 || ni > 16 { return Err(Denied::PolicyMalformed); }
        let body = 24 + 32 * nk + 32 * ni;
        if p.len() != body + 64 { return Err(Denied::PolicyMalformed); }
        let sig: [u8; 64] = p[body..].try_into().unwrap();
        if !verify(&PLATFORM_KEY, b"NYRQIS-POLICY-V1\0", &p[..body], &sig) { return Err(Denied::PolicyNotSignedByPlatform); }
        revoked_keys = &p[24..24 + 32 * nk];
        revoked_imgs = &p[24 + 32 * nk..body];
        seq = u64_at(p, 8);
    }
    // 2. user-enrolled keys: authorised by the platform signature, or (enroll mode) by physical presence.
    let mut user_keys: [[u8; 32]; 8] = [[0; 32]; 8];
    let (mut accepted, mut ignored) = (0usize, 0usize);
    if let Some(m) = mokdb {
        if m.len() < 12 || &m[..8] != b"NYMK0001" { return Err(Denied::MokMalformed); }
        let n = u32_at(m, 8).ok_or(Denied::MokMalformed)? as usize;
        if n > 8 { return Err(Denied::MokMalformed); }
        let body = 12 + 32 * n;
        let signed_by_platform = if m.len() == body + 64 {
            let sig: [u8; 64] = m[body..].try_into().unwrap();
            if !verify(&PLATFORM_KEY, b"NYRQIS-MOKDB-V1\0", &m[..body], &sig) { return Err(Denied::MokNotSignedByPlatform); }
            true
        } else if m.len() == body { false } else { return Err(Denied::MokMalformed) };
        if signed_by_platform || mode == Mode::Enroll {
            for i in 0..n { user_keys[i].copy_from_slice(&m[12 + 32 * i..12 + 32 * i + 32]); }
            accepted = n;
        } else { ignored = n; }
    }
    // 3. the image signature
    let bs = bootsig.ok_or(Denied::NoSignature)?;
    if bs.len() != 8 + 32 + 64 || &bs[..8] != b"NYBS0001" { return Err(Denied::SignatureMalformed); }
    let signer: [u8; 32] = bs[8..40].try_into().unwrap();
    let sig: [u8; 64] = bs[40..104].try_into().unwrap();
    let platform = signer == PLATFORM_KEY;
    if !platform && !user_keys[..accepted].iter().any(|k| *k == signer) { return Err(Denied::SignerNotTrusted); }
    let fp = fingerprint(&signer);
    if revoked_keys.chunks(32).any(|r| r == fp) { return Err(Denied::SignerRevoked); }
    let digest: [u8; 32] = Sha256::digest(image).into();
    if revoked_imgs.chunks(32).any(|r| r == digest) { return Err(Denied::ImageRevoked); }
    if !verify(&signer, b"NYRQIS-BOOT-V1\0", &digest, &sig) { return Err(Denied::SignatureInvalid); }
    Ok(Some(Verdict { signer_fp: fp, platform_signer: platform, user_keys_accepted: accepted, user_keys_ignored: ignored, policy_sequence: seq }))
}
