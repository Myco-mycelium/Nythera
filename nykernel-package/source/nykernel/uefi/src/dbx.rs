//! The ADR-0014 chain's revocation reading: parse the firmware's
//! `dbx` (the forbidden-signature database under
//! EFI_IMAGE_SECURITY_DATABASE_GUID, UEFI 2.10 section 32.4.1) and
//! refuse the boot when the chain that JUST verified (signature +
//! validity windows, see trust.rs) is revoked BY it. A chain the
//! platform later revoked must not verify at all, which is exactly
//! why this surface sits behind the walk and refuses the boot at its
//! own named step.
//!
//! Scope, named like every other surface here: dbx entries of type
//! EFI_CERT_X509_GUID are enforced -- refusing the boot when an entry
//! (a) byte-equals the signer or the anchor certificate (the
//! whole-certificate dbx form), or (b) parses under the walk's own
//! certificate reader to an issuer Name + serialNumber that both
//! byte-equal the signer's or the anchor's (the issuer+serial pair
//! X.509 treats as THE identity a signature binds, so a re-key that
//! reuses the pair is revoked with it). Entries of ANY other type are
//! reader already carry no issuer+serial pair that can match the two
//! certificates this walk just parsed, see entry_revokes below.
//!
//! Hash-typed entries ARE enforced too: EFI_CERT_X509_SHA256_GUID
//! entries fix the payload at `EFI_TIME RevocationDate` (16 bytes)
//! plus the SHA-256 of the certificate's To-Be-Signed content (32
//! bytes, UEFI 2.10 32.4.1's EFI_CERT_X509_SHA256_SIGNATURE shape),
//! and the walk hashes the To-Be-Signed content of each chain
//! certificate (the exact bytes trust.rs located when parsing) and
//! compares: a matching hash revokes the certificate whose
//! To-Be-Signed it covers. The standoff date is the entry's EFI_TIME,
//! read as six civil fields and required to be at or before the
//! firmware clock this walk already fetched -- an entry whose
//! revocation date is in the future is not yet in force and stays a
//! countable fact, never a refusal.

use crate::efi::{CERT_X509_GUID, CERT_X509_SHA256_GUID};
use crate::trust::{cert_tbs, Cert, TrustedChain};

/// Which chain certificate an entry revoked, and by which rule: the
/// refusal names BOTH (four distinct static refusals, see below).
enum Revoked {
    SignerByte,
    SignerPair,
    AnchorByte,
    AnchorPair,
}

fn refusal_for(r: Revoked) -> &'static str {
    match r {
        Revoked::SignerByte => {
            "dbx: the carrier chain is REVOKED by the firmware's dbx (the signer certificate byte-matches an entry)"
        }
        Revoked::SignerPair => {
            "dbx: the carrier chain is REVOKED by the firmware's dbx (the signer's issuer+serial matches an entry)"
        }
        Revoked::AnchorByte => {
            "dbx: the carrier chain is REVOKED by the firmware's dbx (the anchor certificate byte-matches an entry)"
        }
        Revoked::AnchorPair => {
            "dbx: the carrier chain is REVOKED by the firmware's dbx (the anchor's issuer+serial matches an entry)"
        }
    }
}

/// The chain's revocation verdict for ONE dbx entry: None = no match,
/// Some(Revoked) = the exact named refusal to die with.
///
/// The chain's own certificates REACHED the walk parsed, so a
/// byte-equal entry is by construction parseable too -- check the
/// cheap form first. An entry that does NOT parse under the walk's
/// own certificate reader cannot carry an issuer+serial pair that
/// matches two certificates THIS walk just parsed, so it can never
/// match the parsed pair and is treated as a non-match (the byte rule
/// above still catches a byte-identical entry, which needs no parse).
fn entry_revokes(entry_cert_der: &[u8], anchor: &Cert, chain: &TrustedChain) -> Option<Revoked> {
    if entry_cert_der == chain.signer_der {
        return Some(Revoked::SignerByte);
    }
    if entry_cert_der == chain.anchor_der {
        return Some(Revoked::AnchorByte);
    }
    let entry = Cert::parse(entry_cert_der).ok()?;
    if entry.issuer == chain.signer_issuer && entry.serial == chain.signer_serial {
        return Some(Revoked::SignerPair);
    }
    if entry.issuer == anchor.issuer && entry.serial == anchor.serial {
        return Some(Revoked::AnchorPair);
    }
    None
}

/// How the walk went: the transcript facts `main.rs` prints.
pub struct WalkFacts {
    /// X.509 entries walked and confirmed NOT to revoke the chain.
    pub x509_entries_walked: usize,
    /// X.509-SHA256 entries walked and confirmed NOT (yet: see
    /// civil_le's future-dated note) in force against the chain.
    pub x509_sha256_entries_walked: usize,
    /// Entries of types this walk deliberately does not enforce,
    /// reported as skipped (X.509-SHA512, X.509-SHA1 and friends).
    pub other_type_entries_skipped: usize,
}

fn le_u32(b: &[u8]) -> u32 {
    u32::from_le_bytes([b[0], b[1], b[2], b[3]])
}

/// A dbx X.509-SHA256 entry's payload (SignatureData), read out:
/// (year, month, day, hour, minute, second) as u16s -- NOT an
/// Rfc5280Time, just the six civil fields -- and the 32-byte
/// To-Be-Signed hash. No-None if the entry is too short to be
/// UEFI 2.10 32.4.1's EFI_CERT_X509_SHA256_SIGNATURE shape at all
/// (the caller treats those as structurally broken, see below).
fn read_revocation(data: &[u8]) -> Option<([u16; 6], [u8; 32])> {
    if data.len() < 16 + 32 {
        return None;
    }
    let date = [
        u16::from_le_bytes([data[0], data[1]]),
        data[2] as u16,
        data[3] as u16,
        data[4] as u16,
        data[5] as u16,
        data[6] as u16,
    ];
    let mut hash = [0u8; 32];
    hash.copy_from_slice(&data[16..16 + 32]);
    Some((date, hash))
}

/// SHA-256 over the To-Be-Signed content of exactly the certificates
/// the walk inspects; the same hand-rolled hash the carrier walk
/// verifies signatures with.
fn sha256(bytes: &[u8]) -> [u8; 32] {
    crate::sha256::sha256(bytes)
}

/// EFI_TIME's six civil fields compared lexicographically, no
/// calendar arithmetic, matching trust.rs's Rfc5280Time posture.
fn civil_le(a: &[u16; 6], b: &[u16; 6]) -> bool {
    for i in 0..6 {
        match a[i].cmp(&b[i]) {
            core::cmp::Ordering::Less => return true,
            core::cmp::Ordering::Greater => return false,
            core::cmp::Ordering::Equal => continue,
        }
    }
    // Equal: the revocation moment itself is in force.
    true
}

/// Which chain certificate's To-Be-Signed hash the entry covers, and
/// therefore (four distinct refusals, see below) which half of the
/// chain breaks.
enum HashRevoked {
    SignerHash,
    AnchorHash,
}

fn refusal_for_hash(r: HashRevoked) -> &'static str {
    match r {
        HashRevoked::SignerHash => {
            "dbx: the carrier chain is REVOKED by the firmware's dbx (a hash-typed entry covers the signer certificate's To-Be-Signed)"
        }
        HashRevoked::AnchorHash => {
            "dbx: the carrier chain is REVOKED by the firmware's dbx (a hash-typed entry covers the anchor certificate's To-Be-Signed)"
        }
    }
}

/// Walks the whole dbx blob, refusing at the FIRST entry that revokes
/// the chain, with the refusal naming which certificate matched and
/// by which rule. Structural breakage (a list header whose sizes
/// disagree with the blob they live in) is refused, not skipped: the
/// walk's contract is that unreadable revocation data halts with a
/// name, matching the stub's refuse-loudly posture on every other
/// firmware surface it depends on.
pub fn check_blob(
    dbx: &[u8],
    boot_now_fields: &[u16; 6],
    chain: &TrustedChain,
) -> Result<WalkFacts, &'static str> {
    let x509_type = CERT_X509_GUID.bytes();
    let x509_sha256_type = CERT_X509_SHA256_GUID.bytes();
    // The anchor parses once for the issuer+serial pair comparison --
    // the walk that produced `chain` already proved it parses.
    let anchor = Cert::parse(chain.anchor_der)?;
    // The two To-Be-Signed hashes, over exactly the bytes trust.rs
    // located (the whole TLV: tag + length + content, the part RFC
    // 5280 4.1.1.2's signature hash covers).
    let signer_tbs_hash = sha256(cert_tbs(chain.signer_der)?);
    let anchor_tbs_hash = sha256(cert_tbs(chain.anchor_der)?);
    let mut pos = 0usize;
    let mut x509_entries = 0usize;
    let mut x509_sha256_entries = 0usize;
    let mut other_type = 0usize;
    while pos < dbx.len() {
        if dbx.len() - pos < 28 {
            return Err("dbx: an EFI_SIGNATURE_LIST header runs past the store's end");
        }
        let guid = &dbx[pos..pos + 16];
        let list_size = le_u32(&dbx[pos + 16..pos + 20]) as usize;
        let header_size = le_u32(&dbx[pos + 20..pos + 24]) as usize;
        let sig_size = le_u32(&dbx[pos + 24..pos + 28]) as usize;
        if list_size < 28 {
            return Err("dbx: an EFI_SIGNATURE_LIST declares list_size below its own 28-byte header");
        }
        if pos + list_size > dbx.len() {
            return Err("dbx: an EFI_SIGNATURE_LIST declares a size that runs past the store's end");
        }
        let list_end = pos + list_size;
        if guid == x509_type {
            // Per UEFI 2.10 32.4.1 the X.509 list carries no header,
            // and its entries are one owner GUID + one certificate
            // DER each, all the same size.
            if header_size != 0 {
                return Err(
                    "dbx: an X.509 signature list declares a nonzero SignatureHeaderSize (the spec fixes 0)",
                );
            }
            if sig_size < 16 {
                return Err(
                    "dbx: an X.509 signature list declares a SignatureSize below the 16-byte owner GUID",
                );
            }
            let entries_start = pos + 28;
            let entries_len = list_end - entries_start;
            if entries_len % sig_size != 0 {
                return Err(
                    "dbx: an X.509 signature list's entries do not tile its declared list size exactly",
                );
            }
            let mut e = entries_start;
            while e < list_end {
                if let Some(r) = entry_revokes(&dbx[e + 16..e + sig_size], &anchor, chain) {
                    return Err(refusal_for(r));
                }
                x509_entries += 1;
                e += sig_size;
            }
        } else if guid == x509_sha256_type {
            // UEFI 2.10 32.4.1: entries of one owner GUID + one
            // EFI_CERT_X509_SHA256_SIGNATURE = EFI_TIME (16 bytes) +
            // 32-byte hash. No header, like the X.509 list.
            if header_size != 0 {
                return Err(
                    "dbx: an X.509-SHA256 signature list declares a nonzero SignatureHeaderSize (the spec fixes 0)",
                );
            }
            if sig_size != 16 + 48 {
                return Err(
                    "dbx: an X.509-SHA256 signature list declares a SignatureSize that is not owner GUID + EFI_TIME + 32 bytes",
                );
            }
            let entries_start = pos + 28;
            let entries_len = list_end - entries_start;
            if entries_len % sig_size != 0 {
                return Err(
                    "dbx: an X.509-SHA256 signature list's entries do not tile its declared list size exactly",
                );
            }
            let mut e = entries_start;
            while e < list_end {
                let (date, hash) =
                    read_revocation(&dbx[e + 16..e + sig_size]).ok_or(
                        "dbx: an X.509-SHA256 entry's SignatureData is not the EFI_TIME + 32-byte shape",
                    )?;
                // In force only once the revocation date is at or
                // before the firmware clock the boot's first gate
                // already fetched.
                if civil_le(&date, boot_now_fields) {
                    if hash == signer_tbs_hash {
                        return Err(refusal_for_hash(HashRevoked::SignerHash));
                    }
                    if hash == anchor_tbs_hash {
                        return Err(refusal_for_hash(HashRevoked::AnchorHash));
                    }
                }
                x509_sha256_entries += 1;
                e += sig_size;
            }
        } else {
            other_type += 1;
        }
        pos = list_end;
    }
    Ok(WalkFacts {
        x509_entries_walked: x509_entries,
        x509_sha256_entries_walked: x509_sha256_entries,
        other_type_entries_skipped: other_type,
    })
}
