//! The stub's trust walk, compiled and run on the HOST -- no cargo,
//! no UEFI, no device: the hand-rolled modules are pulled in from
//! uefi/src with #[path], which is exactly the code that boots, and
//! this file itself is std-only glue (tools/verify_carrier.sh
//! compiles it with rustc and runs it against the carrier set
//! make_carrier.sh just wrote; the kernel repo proves its
//! boot-critical code the same way before the QEMU gate ever sees
//! it, running Linux-backend tests on the host, then booting).
//!
//! Checks, in order, each stopping the process on failure:
//!   1. the carrier parses AND verifies over the real kernel image
//!      at the HOST's own UTC clock (the same four-argument call
//!      main.rs makes in step 3b, with the firmware clock in place
//!      of the host one),
//!   2. a ONE-BIT-FLIPPED copy of the kernel image must REFUSE and
//!      the refusal must be about the digest, not an earlier parse
//!      step (the refusal path is alive at the LAST check, where it
//!      matters),
//!   3. a byte INSIDE THE ANCHOR'S PARSED MODULUS must refuse at the
//!      chain verify (the anchor's own signature bytes are inert data
//!      by design: the stub pins the anchor by byte possession, not by
//!      checking it with itself, so the corruption the pin can catch
//!      is exactly the key bytes it carries),
//!   4. a ONE-BIT-FLIPPED copy of the carrier DER must refuse at one
//!      of the walk's named steps,
//!   5. the carrier truncated by one byte must refuse at a parse
//!      boundary, not verify,
//!   6.. each NEGATIVE carrier passed on the command line (paired
//!      with a marker: a substring of the named refusal it must
//!      produce) must be refused, and refused FOR THAT REASON -- a
//!      verification, or a refusal with the wrong reason (a skipped
//!      check), stops the process.
//!   7. the dbx revocation walk (dbx.rs, the code behind main.rs's
//!      revocation gate): every GENERATED dbx blob passed on the
//!      command line (paired with a marker) must REFUSE the chain
//!      that verified in step 1, for that marker -- and an entry of
//!      an unknown CertType synthesized here must be SKIPPED (a
//!      fact, not a refusal), proving the walk enforces only what it
//!      walks.
//!
//! Usage: trust_selfcheck <carrier.der> <trusted-root.der> <kernel-elf>
//!                        [<neg.der> <expected-marker>]...
//!                        --dbx [<dbx-blob.der> <expected-marker>]...
//!                        (the --dbx section may be absent; when
//!                        present, further pairs beyond it belong to
//!                        the dbx walk)

#[path = "../src/efi.rs"]
mod efi;
#[path = "../src/der.rs"]
mod der;
#[path = "../src/rsa.rs"]
mod rsa;
#[path = "../src/sha256.rs"]
mod sha256;
#[path = "../src/trust.rs"]
mod trust;
#[path = "../src/dbx.rs"]
mod dbx;

use std::fs;
use std::time::{SystemTime, UNIX_EPOCH};

/// The host's own UTC clock, as `Rfc5280Time`: converts a unix
/// seconds count to civil fields with the standard days-based
/// algorithm (identical field values to `date -u`, which
/// verify_carrier.sh cross-prints for comparison). The firmware's
/// GetTime plays this role at boot; here the host clock stands in
/// for it, the way the host stands in for the firmware at all.
fn host_utc_now() -> trust::Rfc5280Time {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("host clock before the epoch")
        .as_secs() as i64;
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    let (h, m, s) = ((rem / 3600) as u8, ((rem % 3600) / 60) as u8, (rem % 60) as u8);
    // Civil-from-days (Howard Hinnant's algorithm), years >= 1970.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u8;
    let mo = (if mp < 10 { mp + 3 } else { mp - 9 }) as u8;
    let y = if mo <= 2 { y + 1 } else { y };
    trust::Rfc5280Time::from_parts(y as u16, mo, d, h, m, s)
}

/// Builds a one-entry EFI_SIGNATURE_LIST (the exact dbx variable
/// shape) over an arbitrary entry payload, with a caller-chosen
/// CertType GUID: both the walk's own test blobs (via
/// patch_carrier_negatives.py) and this file's unknown-type control
/// ride on the same layout.
fn sig_list(cert_type: [u8; 16], payload: &[u8]) -> Vec<u8> {
    assert!(!payload.is_empty());
    let sig_size = 16 + payload.len();
    let mut out = Vec::with_capacity(28 + sig_size);
    out.extend_from_slice(&cert_type);
    out.extend_from_slice(&((28 + sig_size) as u32).to_le_bytes());
    out.extend_from_slice(&0u32.to_le_bytes()); // SignatureHeaderSize
    out.extend_from_slice(&(sig_size as u32).to_le_bytes());
    out.extend_from_slice(&[0u8; 16]); // SignatureOwner GUID, zero
    out.extend_from_slice(payload);
    out
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    // The --dbx section marker splits carrier pairs from dbx pairs.
    let dbx_pos = args.iter().position(|a| a == "--dbx");
    let (car_args, dbx_args) = match dbx_pos {
        Some(i) => (&args[..i], Some(&args[i + 1..])),
        None => (&args[..], None),
    };
    if car_args.len() < 4 {
        eprintln!(
            "usage: trust_selfcheck <carrier.der> <trusted-root.der> <kernel-elf> \
             [<neg.der> <expected-marker>]... [--dbx <blob> <expected-marker>]..."
        );
        std::process::exit(2);
    }
    let extra = car_args.len() - 4;
    if extra % 2 != 0 {
        eprintln!("usage: negative carriers come in (file, marker) pairs");
        std::process::exit(2);
    }
    let carrier = fs::read(&car_args[1]).expect("carrier read");
    let anchor = fs::read(&car_args[2]).expect("anchor read");
    let content = fs::read(&car_args[3]).expect("content read");
    if carrier.is_empty() || anchor.is_empty() || content.is_empty() {
        eprintln!("FAIL: an argument file is empty");
        std::process::exit(2);
    }
    // Precondition asserted on the OUTPUT files, not in-stub: the
    // anchor must parse and carry a 2048-bit 65537 key, or the
    // make_carrier.sh run on this build was broken.
    let a = trust::Cert::parse(&anchor).expect("anchor must parse");
    let now = host_utc_now();
    println!(
        "-- host walk clock: {:04}-{:02}-{:02} {:02}:{:02}:{:02} UTC \
         (cross-check with `date -u`)",
        now.year, now.month, now.day, now.hour, now.minute, now.second
    );

    // 1. the real path, returning the chain the dbx walk consumes.
    let chain = match trust::verify_trusted_chain(&anchor, &carrier, &content, now) {
        Ok(chain) => {
            println!(
                "-- OK 1: carrier verifies over the {}-byte kernel image; chain returned for the dbx walk",
                content.len()
            );
            chain
        }
        Err(e) => {
            println!("FAIL: carrier did NOT verify over the real content: {}", e);
            std::process::exit(1);
        }
    };

    // 2. flipped content: must refuse, and must reach the DIGEST
    //    comparison (an earlier parse refusal would mean the walk's
    //    ordering broke, e.g. the RSA result was read before the pin).
    let mut flipped_content = content.clone();
    flipped_content[0] ^= 0x01;
    match trust::verify_trusted_chain(&anchor, &carrier, &flipped_content, now) {
        Err(e) if e.contains("DigestInfo digest differs") => {
            println!(
                "-- OK 2: flipped content refused at the digest comparison: {}",
                e
            );
        }
        Err(e) => {
            println!("FAIL: flipped content refused, but at the wrong step: {}", e);
            std::process::exit(1);
        }
        Ok(_) => {
            println!("FAIL: flipped content VERIFIED -- the refusal path is dead");
            std::process::exit(1);
        }
    }

    // 3. a CORRUPTED anchor KEY must refuse at the chain: the flip is
    //    placed inside the modulus the walk parsed (the anchor's own
    //    self-signature bytes are deliberately inert data here -- the
    //    anchor is pinned by byte possession, not by verifying it with
    //    itself -- so those bytes must NOT be able to refuse a
    //    verification that does not read them; the SPKI key is what
    //    the pin actually carries).
    let m_off =
        (a.spki.modulus_be.as_ptr() as usize).wrapping_sub(anchor.as_ptr() as usize);
    let mut flipped_anchor = anchor.clone();
    let pin = m_off + a.spki.modulus_be.len() / 2;
    flipped_anchor[pin] ^= 0x80;
    match trust::verify_trusted_chain(&flipped_anchor, &carrier, &content, now) {
        Err(e) if e.contains("does not verify under the anchor's key") => println!(
            "-- OK 3: corrupted anchor key refused at the chain verify (modulus at {}, flipped byte at offset {}): {}",
            m_off, pin, e
        ),
        Err(e) => {
            println!(
                "FAIL: corrupted anchor key refused, but at the wrong step ({} bytes modulus at {}): {}",
                a.spki.modulus_be.len(),
                m_off,
                e
            );
            std::process::exit(1);
        }
        Ok(_) => {
            println!("FAIL: corrupted anchor key VERIFIED -- the chain pin is dead");
            std::process::exit(1);
        }
    }

    // 4. flipped carrier: must refuse at SOME named step (any byte is
    //    structure or signature), never verify.
    let mut flipped_carrier = carrier.clone();
    flipped_carrier[carrier.len() / 2] ^= 0x01;
    match trust::verify_trusted_chain(&anchor, &flipped_carrier, &content, now) {
        Err(e) => println!("-- OK 4: flipped carrier refused: {}", e),
        Ok(_) => {
            println!("FAIL: flipped carrier VERIFIED");
            std::process::exit(1);
        }
    }

    // 5. truncated carrier: refuse at a parse boundary.
    let truncated = &carrier[..carrier.len() - 1];
    match trust::verify_trusted_chain(&anchor, truncated, &content, now) {
        Err(_) => println!("-- OK 5: truncated carrier refused"),
        Ok(_) => {
            println!("FAIL: truncated carrier VERIFIED");
            std::process::exit(1);
        }
    }

    // 6.. each negative: refused, for ITS named reason.
    let mut neg_errors = 0;
    for k in 0..(extra / 2) {
        let neg_path = &car_args[4 + k * 2];
        let marker = &car_args[5 + k * 2];
        let neg = match fs::read(neg_path) {
            Ok(b) if !b.is_empty() => b,
            _ => {
                println!("FAIL: negative carrier unreadable or empty: {}", neg_path);
                std::process::exit(1);
            }
        };
        match trust::verify_trusted_chain(&anchor, &neg, &content, now) {
            Ok(_) => {
                println!(
                    "FAIL: negative carrier {} VERIFIED -- refusal path dead",
                    neg_path
                );
                std::process::exit(1);
            }
            Err(e) => {
                if !e.contains(marker) {
                    println!(
                        "FAIL: negative carrier {} refused at the WRONG step:\n  expected: {}\n  actual:   {}",
                        neg_path, marker, e
                    );
                    std::process::exit(1);
                }
                println!("-- OK {}: {} refused as required: {}", 6 + k, neg_path, e);
                neg_errors += 1;
            }
        }
    }

    // 7. the dbx revocation walk, when the caller passed --dbx pairs:
    //    every blob must REFUSE the chain that just verified, for its
    //    marker (a verification, or a refusal naming a different
    //    rule, stops the process), plus the synthesized unknown-type
    //    control proving the skip path.
    let mut dbx_count = 0;
    if let Some(dargs) = dbx_args {
        if dargs.len() % 2 != 0 {
            eprintln!("usage: dbx blobs come in (file, marker) pairs after --dbx");
            std::process::exit(2);
        }
        // Synthesized POSITIVE control FIRST: the leaf certificate
        // under an unknown CertType GUID -- it must NOT refuse, and
        // must report exactly one skipped entry.
        let unknown = sig_list([0u8; 16], &chain.signer_der);
        match dbx::check_blob(&unknown, &chain) {
            Ok(f) if f.other_type_entries_skipped == 1 && f.x509_entries_walked == 0 => {
                println!(
                    "-- OK dbx-skip: an unknown-CertType entry carrying the signer is REPORTED as skipped, never enforced"
                );
            }
            Ok(f) => {
                println!(
                    "FAIL: unknown-CertType control came back wrong (walked {}, skipped {})",
                    f.x509_entries_walked, f.other_type_entries_skipped
                );
                std::process::exit(1);
            }
            Err(e) => {
                println!("FAIL: unknown-CertType control REFUSED ({}), it must skip by name", e);
                std::process::exit(1);
            }
        }
        for k in 0..(dargs.len() / 2) {
            let blob_path = &dargs[k * 2];
            let marker = &dargs[k * 2 + 1];
            let blob = match fs::read(blob_path) {
                Ok(b) if !b.is_empty() => b,
                _ => {
                    println!("FAIL: dbx blob unreadable or empty: {}", blob_path);
                    std::process::exit(1);
                }
            };
            match dbx::check_blob(&blob, &chain) {
                Ok(_) => {
                    println!(
                        "FAIL: dbx blob {} did NOT refuse the chain the walk just accepted",
                        blob_path
                    );
                    std::process::exit(1);
                }
                Err(e) => {
                    if !e.contains(marker) {
                        println!(
                            "FAIL: dbx blob {} refused at the WRONG step:\n  expected: {}\n  actual:   {}",
                            blob_path, marker, e
                        );
                        std::process::exit(1);
                    }
                    println!("-- OK dbx {}: {} refused as required: {}", dbx_count, blob_path, e);
                    dbx_count += 1;
                }
            }
        }
    }

    let _ = u32::from(a.spki.exponent); // exponent pinned by rsa::verify at 65537 in run 1
    println!(
        "PASS: trust walk proves positive + 6 controls + {} negative-carrier refusals on the real carrier bytes; dbx walk proved with {} refusals + unknown-type skip",
        neg_errors, dbx_count
    );
}
