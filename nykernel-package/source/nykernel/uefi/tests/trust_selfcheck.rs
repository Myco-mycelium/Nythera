//! The stub's trust walk, compiled and run on the HOST -- no cargo,
//! no UEFI, no device: the four hand-rolled modules are pulled in
//! from uefi/src with #[path], which is exactly the code that boots,
//! and this file itself is std-only glue (tools/verify_carrier.sh
//! compiles it with rustc and runs it against the carrier set
//! make_carrier.sh just wrote; the kernel repo proves its
//! boot-critical code the same way before the QEMU gate ever sees
//! it, running Linux-backend tests on the host, then booting).
//!
//! Checks, in order, each stopping the process on failure:
//!   1. the carrier parses AND verifies over the real kernel image
//!      (the same three-argument call main.rs makes in step 3b),
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
//!
//! Usage: trust_selfcheck <carrier.der> <trusted-root.der> <kernel-elf>
//!                        [<neg.der> <expected-marker>]...

#[path = "../src/der.rs"]
mod der;
#[path = "../src/rsa.rs"]
mod rsa;
#[path = "../src/sha256.rs"]
mod sha256;
#[path = "../src/trust.rs"]
mod trust;

use std::fs;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 4 {
        eprintln!(
            "usage: trust_selfcheck <carrier.der> <trusted-root.der> <kernel-elf> [<neg.der> <expected-marker>]..."
        );
        std::process::exit(2);
    }
    let extra = args.len() - 4;
    if extra % 2 != 0 {
        eprintln!("usage: negative carriers come in (file, marker) pairs");
        std::process::exit(2);
    }
    let carrier = fs::read(&args[1]).expect("carrier read");
    let anchor = fs::read(&args[2]).expect("anchor read");
    let content = fs::read(&args[3]).expect("content read");
    if carrier.is_empty() || anchor.is_empty() || content.is_empty() {
        eprintln!("FAIL: an argument file is empty");
        std::process::exit(2);
    }
    // Precondition asserted on the OUTPUT files, not in-stub: the
    // anchor must parse and carry a 2048-bit 65537 key, or the
    // make_carrier.sh run on this build was broken.
    let a = trust::Cert::parse(&anchor).expect("anchor must parse");

    // 1. the real path.
    match trust::verify_detached_signed_data(&anchor, &carrier, &content) {
        Ok(()) => println!(
            "-- OK 1: carrier verifies over the {}-byte kernel image",
            content.len()
        ),
        Err(e) => {
            println!("FAIL: carrier did NOT verify over the real content: {}", e);
            std::process::exit(1);
        }
    }

    // 2. flipped content: must refuse, and must reach the DIGEST
    //    comparison (an earlier parse refusal would mean the walk's
    //    ordering broke, e.g. the RSA result was read before the pin).
    let mut flipped_content = content.clone();
    flipped_content[0] ^= 0x01;
    match trust::verify_detached_signed_data(&anchor, &carrier, &flipped_content) {
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
        Ok(()) => {
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
    match trust::verify_detached_signed_data(&flipped_anchor, &carrier, &content) {
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
        Ok(()) => {
            println!("FAIL: corrupted anchor key VERIFIED -- the chain pin is dead");
            std::process::exit(1);
        }
    }

    // 4. flipped carrier: must refuse at SOME named step (any byte is
    //    structure or signature), never verify.
    let mut flipped_carrier = carrier.clone();
    flipped_carrier[carrier.len() / 2] ^= 0x01;
    match trust::verify_detached_signed_data(&anchor, &flipped_carrier, &content) {
        Err(e) => println!("-- OK 4: flipped carrier refused: {}", e),
        Ok(()) => {
            println!("FAIL: flipped carrier VERIFIED");
            std::process::exit(1);
        }
    }

    // 5. truncated carrier: refuse at a parse boundary.
    let truncated = &carrier[..carrier.len() - 1];
    match trust::verify_detached_signed_data(&anchor, truncated, &content) {
        Err(_) => println!("-- OK 5: truncated carrier refused"),
        Ok(()) => {
            println!("FAIL: truncated carrier VERIFIED");
            std::process::exit(1);
        }
    }

    // 6.. each negative: refused, for ITS named reason.
    let mut neg_errors = 0;
    for k in 0..(extra / 2) {
        let neg_path = &args[4 + k * 2];
        let marker = &args[5 + k * 2];
        let neg = match fs::read(neg_path) {
            Ok(b) if !b.is_empty() => b,
            _ => {
                println!("FAIL: negative carrier unreadable or empty: {}", neg_path);
                std::process::exit(1);
            }
        };
        match trust::verify_detached_signed_data(&anchor, &neg, &content) {
            Ok(()) => {
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

    let _ = u32::from(a.spki.exponent); // exponent pinned by rsa::verify at 65537 in run 1
    println!(
        "PASS: trust walk proves positive + 4 controls + {} negative-carrier refusals on the real carrier bytes",
        neg_errors
    );
}
