//! NyVault key manager — ADR-0023.
//!
//! Envelope encryption: per-volume 256-bit DEKs AEAD-wrapped by a
//! daemon-held KEK; the KEK is Argon2id-derived from an operator
//! unlock secret and NEVER stored in plaintext — only the KEK
//! envelope (salt + KDF parameters + a check value) is on disk.
//!
//! Key custody lives HERE, behind the FFI boundary: `unlock` derives
//! the KEK inside this process, stores it in the handle table, and
//! returns an opaque `u64` handle — the plaintext KEK never crosses
//! FFI (ADR-0020's platform-boundary rule applied to the most
//! sensitive path). The Python backend interacts through handles only.
//!
//! Byte-format compatibility with the PyNaCl floor (`backend/keys.py`)
//! is pinned by the differential conformance gate (CI
//! `rust-keys-conformance`, required): same Argon2id parameters →
//! identical KDF bytes; same construction (XChaCha20-Poly1305 IETF,
//! 24-byte nonce, 16-byte tag, caller-supplied associated data) →
//! identical ciphertext. The two implementations are interchangeable
//! on each other's blobs.
//!
//! FFI error contract (matches the other migration crates): 0 on
//! success, a negative value on failure — `-1` (ERR_AUTH) for
//! verification failures (wrong unlock secret, tampered blob), `-2`
//! (ERR_ARGS) for invalid arguments, `-3` (ERR_HANDLE) for an unknown
//! handle, `-4096` (ERR_INTERNAL) for module failures.
//!
//! References: ADR-0023 (vault key manager), ADR-0020 (platform
//! boundary), ABI-001 (FFI versioning rule).

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Mutex, OnceLock};

use argon2::{Algorithm, Argon2, Params, Version};
use chacha20poly1305::aead::{Aead, KeyInit, Payload};
use chacha20poly1305::{Key, XChaCha20Poly1305, XNonce};

/// Module ABI version (semver-major*10000 + minor*100 + patch).
pub const ABI_VERSION: u32 = 0x0001_0000;

const MAGIC: &[u8] = b"NYRQIS-KEYS"; // 11 bytes + version + kdf = 13-byte header
const KDF_ARGO2ID: u8 = 0;
const SALT_LEN: usize = 16;
const KEK_LEN: usize = 32;
const DEK_LEN: usize = 32;
const NONCE_LEN: usize = 24;
const TAG_LEN: usize = 16;
const CHECK_AD: &[u8] = b"nyrqis-kek-check";
const CHECK_PLAINTEXT: [u8; 32] = [0u8; 32];

// 12 + 1 + 1 + 4 + 4 + 1 + 16 + 24 + 32 + 16
const KEK_BLOB_SIZE: usize =
    MAGIC.len() + 1 + 1 + 4 + 4 + 1 + SALT_LEN + NONCE_LEN + DEK_LEN + TAG_LEN;
// 24 + 32 + 16
const DEK_BLOB_SIZE: usize = NONCE_LEN + DEK_LEN + TAG_LEN;

// FFI error codes.
const OK: i32 = 0;
const ERR_AUTH: i32 = -1;
const ERR_ARGS: i32 = -2;
const ERR_HANDLE: i32 = -3;
const ERR_INTERNAL: i32 = -4096;

// -- key custody ------------------------------------------------------

static NEXT_HANDLE: AtomicU64 = AtomicU64::new(1);
static HANDLES: OnceLock<Mutex<HashMap<u64, [u8; KEK_LEN]>>> = OnceLock::new();

fn handles() -> &'static Mutex<HashMap<u64, [u8; KEK_LEN]>> {
    HANDLES.get_or_init(|| Mutex::new(HashMap::new()))
}

fn store_kek(kek: [u8; KEK_LEN]) -> u64 {
    let handle = NEXT_HANDLE.fetch_add(1, Ordering::Relaxed);
    if let Ok(mut map) = handles().lock() {
        map.insert(handle, kek);
    }
    handle
}

fn take_kek(handle: u64) -> Result<[u8; KEK_LEN], i32> {
    let mut map = handles().lock().map_err(|_| ERR_INTERNAL)?;
    map.remove(&handle).ok_or(ERR_HANDLE)
}

fn get_kek(handle: u64) -> Result<[u8; KEK_LEN], i32> {
    let map = handles().lock().map_err(|_| ERR_INTERNAL)?;
    map.get(&handle).copied().ok_or(ERR_HANDLE)
}

// -- primitives -------------------------------------------------------

fn argon2id(password: &[u8], salt: &[u8], opslimit: u32,
            memlimit_kib: u32) -> Result<[u8; KEK_LEN], i32> {
    if salt.len() != SALT_LEN {
        return Err(ERR_ARGS);
    }
    // Argon2 requires m_cost >= 8 * p (KiB); the KDF contract fixes
    // p = 1, matching libsodium's argon2id KDF.
    let params = Params::new(memlimit_kib, opslimit, 1, Some(KEK_LEN))
        .map_err(|_| ERR_ARGS)?;
    let argon = Argon2::new(Algorithm::Argon2id, Version::V0x13, params);
    let mut out = [0u8; KEK_LEN];
    argon.hash_password_into(password, salt, &mut out)
        .map_err(|_| ERR_ARGS)?;
    Ok(out)
}

fn aead_encrypt(key: &[u8; KEK_LEN], nonce: &[u8],
                ad: &[u8], plaintext: &[u8]) -> Result<Vec<u8>, ()> {
    if nonce.len() != NONCE_LEN {
        return Err(());
    }
    let cipher = XChaCha20Poly1305::new(Key::from_slice(key));
    let nonce = XNonce::clone_from_slice(nonce);
    cipher.encrypt(&nonce, Payload { msg: plaintext, aad: ad })
        .map_err(|_| ())
}

fn aead_decrypt(key: &[u8; KEK_LEN], nonce: &[u8],
                ad: &[u8], ciphertext: &[u8]) -> Result<Vec<u8>, ()> {
    if nonce.len() != NONCE_LEN {
        return Err(());
    }
    let cipher = XChaCha20Poly1305::new(Key::from_slice(key));
    let nonce = XNonce::clone_from_slice(nonce);
    cipher.decrypt(&nonce, Payload { msg: ciphertext, aad: ad })
        .map_err(|_| ())
}

// -- envelope parsing -------------------------------------------------

struct KekBlob<'a> {
    opslimit: u32,
    memlimit_kib: u32,
    salt: &'a [u8],
    check_nonce: &'a [u8],
    check_ct: &'a [u8],
}

fn parse_kek_blob(blob: &[u8]) -> Result<KekBlob<'_>, i32> {
    if blob.len() != KEK_BLOB_SIZE {
        return Err(ERR_ARGS);
    }
    let mut off = 0;
    let magic = &blob[off..off + MAGIC.len()];
    off += MAGIC.len();
    if magic != MAGIC {
        return Err(ERR_ARGS);
    }
    let version = blob[off];
    let kdf = blob[off + 1];
    off += 2;
    if version != 1 || kdf != KDF_ARGO2ID {
        return Err(ERR_ARGS);
    }
    let opslimit = u32::from_be_bytes(blob[off..off + 4].try_into().unwrap());
    off += 4;
    let memlimit_kib = u32::from_be_bytes(blob[off..off + 4].try_into().unwrap());
    off += 4;
    let p = blob[off];
    off += 1;
    if p != 1 {
        return Err(ERR_ARGS);
    }
    let salt = &blob[off..off + SALT_LEN];
    off += SALT_LEN;
    let check_nonce = &blob[off..off + NONCE_LEN];
    off += NONCE_LEN;
    let check_ct = &blob[off..off + DEK_LEN + TAG_LEN];
    Ok(KekBlob { opslimit, memlimit_kib, salt, check_nonce, check_ct })
}

// -- FFI surface ------------------------------------------------------

/// Report the module ABI version.
#[no_mangle]
pub extern "C" fn nyrqis_keys_version() -> u32 {
    ABI_VERSION
}

/// Argon2id KDF (pure, for the differential gate): fills `out` (32
/// bytes) with the KEK for `password` + `salt`. Parallelism fixed at 1.
#[no_mangle]
pub extern "C" fn nyrqis_keys_derive_kek(
    password: *const u8, password_len: usize,
    salt: *const u8, salt_len: usize,
    opslimit: u32, memlimit_kib: u32, parallelism: u8,
    out: *mut u8, out_len: usize,
) -> i32 {
    if parallelism != 1 || out_len != KEK_LEN {
        return ERR_ARGS;
    }
    let password = unsafe { std::slice::from_raw_parts(password, password_len) };
    let salt = unsafe { std::slice::from_raw_parts(salt, salt_len) };
    match argon2id(password, salt, opslimit, memlimit_kib) {
        Ok(kek) => {
            unsafe { std::ptr::copy_nonoverlapping(kek.as_ptr(), out, KEK_LEN) };
            OK
        }
        Err(code) => code,
    }
}

/// Build the KEK envelope (111 bytes) into `out` for `password` +
/// caller-supplied 16-byte `salt` (the salt is an input so the
/// differential gate can pin both implementations to the same bytes;
/// production callers generate a fresh random salt).
#[no_mangle]
pub extern "C" fn nyrqis_keys_make_blob(
    password: *const u8, password_len: usize,
    opslimit: u32, memlimit_kib: u32,
    salt: *const u8,
    out: *mut u8, out_len: usize,
) -> i32 {
    if out_len != KEK_BLOB_SIZE {
        return ERR_ARGS;
    }
    let password = unsafe { std::slice::from_raw_parts(password, password_len) };
    let salt = unsafe { std::slice::from_raw_parts(salt, SALT_LEN) };
    let kek = match argon2id(password, salt, opslimit, memlimit_kib) {
        Ok(k) => k,
        Err(code) => return code,
    };
    let mut nonce = [0u8; NONCE_LEN];
    // A fresh random nonce for the check value (the KEK is fresh per
    // blob, so uniqueness holds); getrandom via /dev/urandom.
    if let Err(_) = std::fs::File::open("/dev/urandom").and_then(|mut f| {
        use std::io::Read;
        f.read_exact(&mut nonce)
    }) {
        return ERR_INTERNAL;
    }
    let check = match aead_encrypt(&kek, &nonce, CHECK_AD, &CHECK_PLAINTEXT) {
        Ok(c) => c,
        Err(_) => return ERR_INTERNAL,
    };
    let mut blob = Vec::with_capacity(KEK_BLOB_SIZE);
    blob.extend_from_slice(MAGIC);
    blob.extend_from_slice(&[1u8, KDF_ARGO2ID]);
    blob.extend_from_slice(&opslimit.to_be_bytes());
    blob.extend_from_slice(&memlimit_kib.to_be_bytes());
    blob.push(1u8); // parallelism
    blob.extend_from_slice(salt);
    blob.extend_from_slice(&nonce);
    blob.extend_from_slice(&check);
    unsafe { std::ptr::copy_nonoverlapping(blob.as_ptr(), out, KEK_BLOB_SIZE) };
    OK
}

/// Derive + verify the KEK from an envelope and the unlock secret, and
/// store it in the handle table. `handle_out` receives the opaque id;
/// the plaintext KEK never leaves this process.
#[no_mangle]
pub extern "C" fn nyrqis_keys_unlock(
    blob: *const u8, blob_len: usize,
    password: *const u8, password_len: usize,
    handle_out: *mut u64,
) -> i32 {
    let blob = unsafe { std::slice::from_raw_parts(blob, blob_len) };
    let password = unsafe { std::slice::from_raw_parts(password, password_len) };
    let parsed = match parse_kek_blob(blob) {
        Ok(p) => p,
        Err(code) => return code,
    };
    let kek = match argon2id(password, parsed.salt, parsed.opslimit,
                            parsed.memlimit_kib) {
        Ok(k) => k,
        Err(code) => return code,
    };
    // Verify the check value: the KEK is right iff the AEAD opens and
    // yields the fixed plaintext.
    let got = match aead_decrypt(&kek, parsed.check_nonce, CHECK_AD,
                                 parsed.check_ct) {
        Ok(g) => g,
        Err(_) => return ERR_AUTH,
    };
    if got.as_slice() != CHECK_PLAINTEXT {
        return ERR_AUTH;
    }
    let handle = store_kek(kek);
    unsafe { std::ptr::write(handle_out, handle) };
    OK
}

/// Wrap `plaintext` (a DEK) with the handle's KEK: `out` receives the
/// 72-byte envelope (nonce + ciphertext + tag). `nonce` is
/// caller-supplied (24 bytes) so the differential gate can pin both
/// implementations to the same bytes; production callers generate a
/// fresh random nonce.
#[no_mangle]
pub extern "C" fn nyrqis_keys_wrap(
    handle: u64,
    ad: *const u8, ad_len: usize,
    plaintext: *const u8, plaintext_len: usize,
    nonce: *const u8,
    out: *mut u8, out_len: usize,
) -> i32 {
    if out_len != DEK_BLOB_SIZE || plaintext_len != DEK_LEN {
        return ERR_ARGS;
    }
    let kek = match get_kek(handle) {
        Ok(k) => k,
        Err(code) => return code,
    };
    let ad = unsafe { std::slice::from_raw_parts(ad, ad_len) };
    let plaintext = unsafe { std::slice::from_raw_parts(plaintext, plaintext_len) };
    let nonce = unsafe { std::slice::from_raw_parts(nonce, NONCE_LEN) };
    let nonce: [u8; NONCE_LEN] = match nonce.try_into() {
        Ok(n) => n,
        Err(_) => return ERR_ARGS,
    };
    let ct = match aead_encrypt(&kek, &nonce, ad, plaintext) {
        Ok(c) => c,
        Err(_) => return ERR_INTERNAL,
    };
    let mut blob = Vec::with_capacity(DEK_BLOB_SIZE);
    blob.extend_from_slice(&nonce);
    blob.extend_from_slice(&ct);
    unsafe { std::ptr::copy_nonoverlapping(blob.as_ptr(), out, DEK_BLOB_SIZE) };
    OK
}

/// Unwrap a DEK envelope with the handle's KEK: `out` receives the 32
/// plaintext bytes. `-1` on tampering or a wrong KEK. (The plaintext
/// DEK crosses FFI because the block layer is Python-side in this
/// increment; moving block encrypt/decrypt behind the boundary is the
/// documented next step of ADR-0023.)
#[no_mangle]
pub extern "C" fn nyrqis_keys_unwrap(
    handle: u64,
    ad: *const u8, ad_len: usize,
    blob: *const u8, blob_len: usize,
    out: *mut u8, out_len: usize,
) -> i32 {
    if out_len != DEK_LEN {
        return ERR_ARGS;
    }
    let kek = match get_kek(handle) {
        Ok(k) => k,
        Err(code) => return code,
    };
    let ad = unsafe { std::slice::from_raw_parts(ad, ad_len) };
    let blob = unsafe { std::slice::from_raw_parts(blob, blob_len) };
    if blob.len() != DEK_BLOB_SIZE {
        return ERR_ARGS;
    }
    let (nonce, ct) = blob.split_at(NONCE_LEN);
    let nonce: [u8; NONCE_LEN] = match nonce.try_into() {
        Ok(n) => n,
        Err(_) => return ERR_ARGS,
    };
    let dek = match aead_decrypt(&kek, &nonce, ad, ct) {
        Ok(d) => d,
        Err(_) => return ERR_AUTH,
    };
    if dek.len() != DEK_LEN {
        return ERR_AUTH;
    }
    unsafe { std::ptr::copy_nonoverlapping(dek.as_ptr(), out, DEK_LEN) };
    OK
}

/// Drop a KEK from the handle table (crypto-shredding the unwrapped
/// key from this process's memory).
#[no_mangle]
pub extern "C" fn nyrqis_keys_shred(handle: u64) -> i32 {
    match take_kek(handle) {
        Ok(_) => OK,
        Err(code) => code,
    }
}

/// AEAD-encrypt a block payload (the NyFS at-rest byte path, ADR-0023
/// "checksum-then-encrypt"). `out` receives nonce + ciphertext+tag
/// (`plaintext_len + NONCE_LEN + TAG_LEN`). The caller supplies the
/// 24-byte nonce (a fresh random nonce per block WRITE — persisted
/// with the block, so an overwrite never reuses it) and the associated
/// data (the volume context). `dek` is the volume's 32-byte DEK.
#[no_mangle]
pub extern "C" fn nyrqis_keys_block_encrypt(
    dek: *const u8, dek_len: usize,
    nonce: *const u8, nonce_len: usize,
    ad: *const u8, ad_len: usize,
    plaintext: *const u8, plaintext_len: usize,
    out: *mut u8, out_len: usize,
) -> i32 {
    if dek_len != DEK_LEN || nonce_len != NONCE_LEN {
        return ERR_ARGS;
    }
    if out_len != plaintext_len + NONCE_LEN + TAG_LEN {
        return ERR_ARGS;
    }
    let dek: [u8; KEK_LEN] = unsafe {
        std::slice::from_raw_parts(dek, DEK_LEN).try_into().unwrap()
    };
    let nonce = unsafe { std::slice::from_raw_parts(nonce, NONCE_LEN) };
    let ad = unsafe { std::slice::from_raw_parts(ad, ad_len) };
    let plaintext =
        unsafe { std::slice::from_raw_parts(plaintext, plaintext_len) };
    let ct = match aead_encrypt(&dek, nonce, ad, plaintext) {
        Ok(c) => c,
        Err(_) => return ERR_INTERNAL,
    };
    let mut blob = Vec::with_capacity(out_len);
    blob.extend_from_slice(nonce);
    blob.extend_from_slice(&ct);
    unsafe { std::ptr::copy_nonoverlapping(blob.as_ptr(), out, out_len) };
    OK
}

/// AEAD-decrypt a block payload. `blob` is nonce + ciphertext+tag as
/// produced by `block_encrypt`; `out` receives the plaintext. `-1` on
/// tampering or a wrong DEK.
#[no_mangle]
pub extern "C" fn nyrqis_keys_block_decrypt(
    dek: *const u8, dek_len: usize,
    ad: *const u8, ad_len: usize,
    blob: *const u8, blob_len: usize,
    out: *mut u8, out_len: usize,
) -> i32 {
    if dek_len != DEK_LEN || blob_len < NONCE_LEN + TAG_LEN {
        return ERR_ARGS;
    }
    let pt_len = blob_len - NONCE_LEN - TAG_LEN;
    if out_len != pt_len {
        return ERR_ARGS;
    }
    let dek: [u8; KEK_LEN] = unsafe {
        std::slice::from_raw_parts(dek, DEK_LEN).try_into().unwrap()
    };
    let ad = unsafe { std::slice::from_raw_parts(ad, ad_len) };
    let blob = unsafe { std::slice::from_raw_parts(blob, blob_len) };
    let (nonce, ct) = blob.split_at(NONCE_LEN);
    let plaintext = match aead_decrypt(&dek, nonce, ad, ct) {
        Ok(p) => p,
        Err(_) => return ERR_AUTH,
    };
    unsafe { std::ptr::copy_nonoverlapping(plaintext.as_ptr(), out, pt_len) };
    OK
}

// -- unit tests -------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn blob_bytes() -> Vec<u8> {
        let mut out = vec![0u8; KEK_BLOB_SIZE];
        let password = b"correct horse battery staple";
        let salt = [7u8; SALT_LEN];
        let rc = nyrqis_keys_make_blob(
            password.as_ptr(), password.len(), 2, 64 * 1024,
            salt.as_ptr(), out.as_mut_ptr(), out.len());
        assert_eq!(rc, OK);
        out
    }

    #[test]
    fn version_is_1_0_0() {
        assert_eq!(nyrqis_keys_version(), 0x0001_0000);
    }

    #[test]
    fn derive_kek_is_deterministic() {
        let salt = [3u8; SALT_LEN];
        let mut a = [0u8; KEK_LEN];
        let mut b = [0u8; KEK_LEN];
        let rc = nyrqis_keys_derive_kek(
            b"pw".as_ptr(), 2, salt.as_ptr(), SALT_LEN, 2, 64 * 1024, 1,
            a.as_mut_ptr(), KEK_LEN);
        assert_eq!(rc, OK);
        let rc = nyrqis_keys_derive_kek(
            b"pw".as_ptr(), 2, salt.as_ptr(), SALT_LEN, 2, 64 * 1024, 1,
            b.as_mut_ptr(), KEK_LEN);
        assert_eq!(rc, OK);
        assert_eq!(a, b);
    }

    #[test]
    fn unlock_wrap_unwrap_roundtrip() {
        let blob = blob_bytes();
        let mut handle: u64 = 0;
        let rc = nyrqis_keys_unlock(
            blob.as_ptr(), blob.len(), b"correct horse battery staple".as_ptr(),
            28, &mut handle);
        assert_eq!(rc, OK);
        let dek = [0xabu8; DEK_LEN];
        let nonce = [0x42u8; NONCE_LEN];
        let mut wrapped = vec![0u8; DEK_BLOB_SIZE];
        let rc = nyrqis_keys_wrap(
            handle, b"vol-1".as_ptr(), 5, dek.as_ptr(), DEK_LEN,
            nonce.as_ptr(), wrapped.as_mut_ptr(), wrapped.len());
        assert_eq!(rc, OK);
        let mut out = [0u8; DEK_LEN];
        let rc = nyrqis_keys_unwrap(
            handle, b"vol-1".as_ptr(), 5, wrapped.as_ptr(), wrapped.len(),
            out.as_mut_ptr(), DEK_LEN);
        assert_eq!(rc, OK);
        assert_eq!(out, dek);
        // Wrong AD fails verification.
        let rc = nyrqis_keys_unwrap(
            handle, b"vol-2".as_ptr(), 5, wrapped.as_ptr(), wrapped.len(),
            out.as_mut_ptr(), DEK_LEN);
        assert_eq!(rc, ERR_AUTH);
        // Shred, then the handle is gone.
        assert_eq!(nyrqis_keys_shred(handle), OK);
        let rc = nyrqis_keys_wrap(
            handle, b"vol-1".as_ptr(), 5, dek.as_ptr(), DEK_LEN,
            nonce.as_ptr(), wrapped.as_mut_ptr(), wrapped.len());
        assert_eq!(rc, ERR_HANDLE);
    }

    #[test]
    fn wrong_unlock_secret_fails() {
        let blob = blob_bytes();
        let mut handle: u64 = 0;
        let rc = nyrqis_keys_unlock(
            blob.as_ptr(), blob.len(), b"wrong secret".as_ptr(),
            12, &mut handle);
        assert_eq!(rc, ERR_AUTH);
    }

    #[test]
    fn tampered_blob_fails() {
        let mut blob = blob_bytes();
        let last = blob.len() - 1;
        blob[last] ^= 0xff; // flip a check-ciphertext byte
        let mut handle: u64 = 0;
        let rc = nyrqis_keys_unlock(
            blob.as_ptr(), blob.len(), b"correct horse battery staple".as_ptr(),
            28, &mut handle);
        assert_eq!(rc, ERR_AUTH);
    }

    #[test]
    fn malformed_envelope_fails() {
        let mut handle: u64 = 0;
        let rc = nyrqis_keys_unlock(
            b"garbage".as_ptr(), 7, b"pw".as_ptr(), 2, &mut handle);
        assert_eq!(rc, ERR_ARGS);
    }

    #[test]
    fn block_encrypt_decrypt_roundtrip() {
        let dek = [0xabu8; DEK_LEN];
        let nonce = [0x11u8; NONCE_LEN];
        let ad = b"volume-42";
        let pt = b"compressible-block-data;" .repeat(64); // > 1 KiB
        let mut out = vec![0u8; pt.len() + NONCE_LEN + TAG_LEN];
        let rc = nyrqis_keys_block_encrypt(
            dek.as_ptr(), DEK_LEN, nonce.as_ptr(), NONCE_LEN,
            ad.as_ptr(), ad.len(), pt.as_ptr(), pt.len(),
            out.as_mut_ptr(), out.len());
        assert_eq!(rc, OK);
        // The ciphertext never contains the plaintext.
        assert!(!out.windows(16).any(|w| w == &pt[0..16]));
        let mut back = vec![0u8; pt.len()];
        let rc = nyrqis_keys_block_decrypt(
            dek.as_ptr(), DEK_LEN, ad.as_ptr(), ad.len(),
            out.as_ptr(), out.len(), back.as_mut_ptr(), back.len());
        assert_eq!(rc, OK);
        assert_eq!(back, pt);
        // Wrong AD fails.
        let rc = nyrqis_keys_block_decrypt(
            dek.as_ptr(), DEK_LEN, b"volume-43".as_ptr(), 9,
            out.as_ptr(), out.len(), back.as_mut_ptr(), back.len());
        assert_eq!(rc, ERR_AUTH);
        // Tampering fails.
        out[pt.len() / 2] ^= 1;
        let rc = nyrqis_keys_block_decrypt(
            dek.as_ptr(), DEK_LEN, ad.as_ptr(), ad.len(),
            out.as_ptr(), out.len(), back.as_mut_ptr(), back.len());
        assert_eq!(rc, ERR_AUTH);
    }

    #[test]
    fn block_encrypt_deterministic_given_nonce() {
        let dek = [7u8; DEK_LEN];
        let nonce = [9u8; NONCE_LEN];
        let ad = b"vol";
        let pt = b"same input, same output";
        let mut a = vec![0u8; pt.len() + NONCE_LEN + TAG_LEN];
        let mut b = vec![0u8; pt.len() + NONCE_LEN + TAG_LEN];
        let rc = nyrqis_keys_block_encrypt(
            dek.as_ptr(), DEK_LEN, nonce.as_ptr(), NONCE_LEN,
            ad.as_ptr(), 3, pt.as_ptr(), pt.len(),
            a.as_mut_ptr(), a.len());
        assert_eq!(rc, OK);
        let rc = nyrqis_keys_block_encrypt(
            dek.as_ptr(), DEK_LEN, nonce.as_ptr(), NONCE_LEN,
            ad.as_ptr(), 3, pt.as_ptr(), pt.len(),
            b.as_mut_ptr(), b.len());
        assert_eq!(rc, OK);
        assert_eq!(a, b);
    }
}
