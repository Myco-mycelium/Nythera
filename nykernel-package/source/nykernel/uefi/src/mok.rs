//! The ADR-0014 chain's MOK reading: the shim's `MokList` variable
//! (name `MokList`, vendor GUID 605dab50-e046-4300-abb6-3dd810dd8b23
//! -- shim's own EFI file-vendor GUID; UEFI 2.10 section 32.4.1
//! signature-list shape) is the OPTIONAL user-enrollment surface the
//! chain's stub side can honor: entries an enrolled user's MokManager
//! put there act as ADDITIONAL chain anchors, verified with the SAME
//! one-anchor walk (mok.rs's `verify_mok_anchored_chain` entry in
//! trust.rs) the embedded anchor's verification runs -- so a user
//! carrier verified under a MokList entry gets every downstream gate
//! (validity windows, content digest, dbx revocation over its own
//! chain) for free.
//!
//! Scope, named like every other surface here: MokList's
//! EFI_CERT_X509_GUID entries are the anchors; entries of any other
//! type are counted and reported as skipped -- named facts, never
//! silently enforced or silently dropped. Structural breakage (a
//! list header whose sizes disagree with the blob it lives in, or an
//! X.509 entry that does not parse as the certificate it claims to
//! be) refuses rather than truncates: unreadable trust data halts
//! with a name, matching the stub's refuse-loudly posture on every
//! other firmware surface it depends on.
//!
//! Enrollment itself is NOT done here: MokManager's UI (or
//! virt-fw-vars offline composition, which tools/qemu_uefi_ms_secboot_test.sh
//! already uses for PK/KEK/db/dbx) is what writes the variable; this
//! stub READS what is there, honors what it can verify, and names
//! what it cannot.

use crate::efi::CERT_X509_GUID;
use crate::trust::Cert;

/// How the walk went: the anchors found plus the transcript facts
/// `main.rs` prints.
pub struct MokWalk<'a> {
    /// The X.509 entries, parsed. ANY of them may be a chain anchor
    /// for a user carrier (`trust.rs verify_mok_anchored_chain`).
    pub anchors: alloc::vec::Vec<Cert<'a>>,
    /// The same entries' whole DER, index-aligned with `anchors`:
    /// the anchor DER the verification call needs (anchor-side
    /// window/covers data), not just the parsed fields.
    pub anchor_ders: alloc::vec::Vec<&'a [u8]>,
    /// Entries of types this walk deliberately does not enforce
    /// (hash-typed and friends -- a MokList is a signer store, not a
    /// revocation store), reported as skipped.
    pub other_type_entries_skipped: usize,
}

fn le_u32(b: &[u8]) -> u32 {
    u32::from_le_bytes([b[0], b[1], b[2], b[3]])
}

/// Walks the whole MokList blob, parsing every X.509 entry. The
/// header rules are the SAME spec rules dbx.rs enforces (they are
/// properties of the EFI_SIGNATURE_LIST shape, not of dbx), named
/// with MokList prefixes so a breakage names the variable it was
/// found in.
pub fn walk(mok: &[u8]) -> Result<MokWalk<'_>, &'static str> {
    let x509_type = CERT_X509_GUID.bytes();
    let mut pos = 0usize;
    let mut anchors = alloc::vec::Vec::new();
    let mut anchor_ders: alloc::vec::Vec<&[u8]> = alloc::vec::Vec::new();
    let mut other_type = 0usize;
    while pos < mok.len() {
        if mok.len() - pos < 28 {
            return Err("MokList: an EFI_SIGNATURE_LIST header runs past the store's end");
        }
        let guid = &mok[pos..pos + 16];
        let list_size = le_u32(&mok[pos + 16..pos + 20]) as usize;
        let header_size = le_u32(&mok[pos + 20..pos + 24]) as usize;
        let sig_size = le_u32(&mok[pos + 24..pos + 28]) as usize;
        if list_size < 28 {
            return Err(
                "MokList: an EFI_SIGNATURE_LIST declares list_size below its own 28-byte header",
            );
        }
        if pos + list_size > mok.len() {
            return Err("MokList: an EFI_SIGNATURE_LIST declares a size that runs past the store's end");
        }
        let list_end = pos + list_size;
        if guid == x509_type {
            if header_size != 0 {
                return Err(
                    "MokList: an X.509 signature list declares a nonzero SignatureHeaderSize (the spec fixes 0)",
                );
            }
            if sig_size < 16 {
                return Err(
                    "MokList: an X.509 signature list declares a SignatureSize below the 16-byte owner GUID",
                );
            }
            let entries_start = pos + 28;
            let entries_len = list_end - entries_start;
            if entries_len % sig_size != 0 {
                return Err(
                    "MokList: an X.509 signature list's entries do not tile its declared list size exactly",
                );
            }
            let mut e = entries_start;
            while e < list_end {
                let der = &mok[e + 16..e + sig_size];
                anchors.push(
                    Cert::parse(der).map_err(|_| {
                        "MokList: an X.509 entry does not parse as the certificate it claims to be"
                    })?,
                );
                anchor_ders.push(der);
                e += sig_size;
            }
        } else {
            other_type += 1;
        }
        pos = list_end;
    }
    Ok(MokWalk {
        anchors,
        anchor_ders,
        other_type_entries_skipped: other_type,
    })
}
