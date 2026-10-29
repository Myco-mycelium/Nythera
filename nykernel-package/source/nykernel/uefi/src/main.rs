//! Nyrqis Phase 10 UEFI-native boot stub (no_std, no_main, zero deps).
//!
//! Handoff order, each step checking the fact it relies on before
//! moving on:
//!   1. banner over the firmware's own console; SecureBoot variable read
//!   2. watchdog off; page tables + the handoff page allocated and zeroed
//!   3. embedded kernel ELF parsed and loaded to its physical addresses
//!   4. UEFI memory map acquired; Multiboot2 info structure synthesized
//!      into the handoff page (memory map + RSDP copy + vendor tag)
//!   5. ExitBootServices; CR3 switched to the stub's tables; jump to
//!      the kernel's own `uefi_entry` with the Multiboot2 contract
//!      registers set exactly the way GRUB's 32-bit handoff leaves them.
//!
//! The embedded kernel ELF is `include_bytes!` from the kernel build's
//! target/kernel.elf -- the stub is built AFTER the kernel on every
//! build (build.sh enforces the order), so a stale embed cannot sneak
//! through a fresh build; and the boot test itself verifies the
//! kernel's own transcript reaching Ready, so it cannot pass silently
//! at boot either.

#![no_std]
#![no_main]

extern crate alloc;

mod boot;
mod dbx;
mod der;
mod efi;
mod elf;
mod mb2;
mod mok;
mod rsa;
mod sha256;
mod trust;

use core::arch::asm;
use core::panic::PanicInfo;

use efi::*;

/// 1 GiB: the kernel's Phase-1 identity window. Everything the stub
/// allocates and everything it hands to the kernel must live inside
/// it, since the synthesized mapping covers exactly that range.
pub const IDENTITY_LIMIT: u64 = 0x4000_0000;

/// The kernel image, embedded at build time.
/// The kernel image's expected SHA-256, recorded by build.sh at link
/// time (exactly 64 lowercase hex characters, no newline).
const EXPECTED_KERNEL_SHA256_HEX: &[u8; 64] = include_bytes!("kernel.sha256");

const EMPTY_INPUT_SHA256: [u8; 32] = [
    0xe3, 0xb0, 0xc4, 0x42, 0x98, 0xfc, 0x1c, 0x14, 0x9a, 0xfb, 0xf4, 0xc8, 0x99, 0x6f, 0xb9, 0x24,
    0x27, 0xae, 0x41, 0xe4, 0x64, 0x9b, 0x93, 0x4c, 0xa4, 0x95, 0x99, 0x1b, 0x78, 0x52, 0xb8, 0x55,
];
const ABC_INPUT_SHA256: [u8; 32] = [
    0xba, 0x78, 0x16, 0xbf, 0x8f, 0x01, 0xcf, 0xea, 0x41, 0x41, 0x40, 0xde, 0x5d, 0xae, 0x22, 0x23,
    0xb0, 0x03, 0x61, 0xa3, 0x96, 0x17, 0x7a, 0x9c, 0xb4, 0x10, 0xff, 0x61, 0xf2, 0x00, 0x15, 0xad,
];

fn hex_nibble(b: u8) -> Option<u8> {
    match b {
        b'0'..=b'9' => Some(b - b'0'),
        b'a'..=b'f' => Some(b - b'a' + 10),
        _ => None,
    }
}

fn print_digest(d: &[u8; 32]) {
    const HEX: &[u8] = b"0123456789abcdef";
    for b in d {
        boot::conout(core::str::from_utf8(&[HEX[(b >> 4) as usize], HEX[(b & 0xf) as usize]]).unwrap());
    }
}

fn verify_kernel_integrity(kernel: &[u8]) {
    // Known-answer vectors FIRST: a mis-implemented hash must refuse
    // loudly here, not mis-declare a kernel honest downstream.
    if sha256::sha256(&[]) != EMPTY_INPUT_SHA256 {
        boot::die("sha256 known-answer test FAILED on the empty-input vector");
    }
    if sha256::sha256(b"abc") != ABC_INPUT_SHA256 {
        boot::die("sha256 known-answer test FAILED on the abc vector");
    }
    boot::conout("nyrqis ubld: sha256 known-answer vectors OK\r\n");

    let mut expected = [0u8; 32];
    for (i, byte) in expected.iter_mut().enumerate() {
        let hi = hex_nibble(EXPECTED_KERNEL_SHA256_HEX[i * 2]);
        let lo = hex_nibble(EXPECTED_KERNEL_SHA256_HEX[i * 2 + 1]);
        let (hi, lo) = match (hi, lo) {
            (Some(hi), Some(lo)) => (hi, lo),
            _ => boot::die("embedded kernel digest line malformed (not 64 lowercase hex chars)"),
        };
        *byte = (hi << 4) | lo;
    }
    let computed = sha256::sha256(kernel);
    boot::conout("nyrqis ubld: kernel image sha256 computed = ");
    print_digest(&computed);
    boot::conout("\r\n");
    boot::conout("nyrqis ubld: kernel image sha256 recorded = ");
    print_digest(&expected);
    boot::conout("\r\n");
    if computed != expected {
        boot::die("kernel image integrity: computed sha256 != the build-time digest -- refusing to load a tampered or mismatched payload");
    }

    // Tamper-sensitivity control, every boot: one flipped bit of a copy
    // must hash to a DIFFERENT digest, or the comparison above is not a
    // check at all (a hash that ignores its input would "pass" it).
    let mut tampered = alloc::vec::Vec::with_capacity(kernel.len());
    tampered.extend_from_slice(kernel);
    tampered[0] ^= 0x01;
    if sha256::sha256(&tampered) == computed {
        boot::die("integrity control FAILED: a one-bit-flipped image hashed to the same digest");
    }
    boot::conout("nyrqis ubld: integrity control OK -- a one-bit-flipped copy hashes differently, the refusal path is alive\r\n");
}

pub fn kernel_elf_bytes() -> &'static [u8] {
    include_bytes!("../../target/kernel.elf")
}

/// The ADR-0014 chain's authenticated half, both embed sides of it:
/// `TRUSTED_ROOT_BYTES` is the anchor certificate the stub TRUSTS,
/// `CARRIER_BYTES` is the DETACHED carrier signature over the kernel
/// image; `build.sh` runs `tools/make_carrier.sh` after the kernel
/// links and BOTH files land here (gitignored, regenerated every
/// build -- the same generated-state policy as `kernel.sha256`), so
/// the stub recompiles whenever the carrier does and an anchor change
/// forces a rebuild too.
const TRUSTED_ROOT_BYTES: &[u8] = include_bytes!("trusted-root.der");
const CARRIER_BYTES: &[u8] = include_bytes!("carrier.der");

/// The negative carriers `make_carrier.sh` generates alongside the
/// real one, each a way the REAL carrier could be wrong-by-design that
/// openssl would happily sign and the walk must REFUSE (one refusal
/// shape per negative, marker-matched below; the build refuses if a
/// negative status verifies -- the refusal path must be alive per
/// shape, not only for one flipped bit):
///   neg-digest:   signed over a one-byte-different kernel image
///                 (the digest under the real image will differ)
///   neg-anchor:   signed by a stray, unanchored key+cert pair
///                 (no chain from it to the embedded anchor)
///   neg-sid:      the real carrier with one byte of the SignerInfo's
///                 issuerAndSerial serialNumber flipped (the sid no
///                 longer ties the signature to the signer cert)
///   neg-attrs:    carrier signed WITH signingTime attrs (the stub
///                 walks -noattr carriers only)
///   neg-detached: carrier carrying its own eContent (the stub walks
///                 DETACHED carriers only)
static NEG_CARRIER_DIGEST: &[u8] = include_bytes!("carrier-neg-digest.der");
static NEG_CARRIER_ANCHOR: &[u8] = include_bytes!("carrier-neg-anchor.der");
static NEG_CARRIER_SID: &[u8] = include_bytes!("carrier-neg-sid.der");
static NEG_CARRIER_ATTRS: &[u8] = include_bytes!("carrier-neg-attrs.der");
static NEG_CARRIER_DETACHED: &[u8] = include_bytes!("carrier-neg-detached.der");
/// The VALIDITY-WINDOW negatives (the expiry gate's every-boot
/// controls, the ADR-0014 expiry/revocation card): carriers signed
/// by the REAL leaf key over the real kernel image, but embedded
/// certificates whose windows lie ENTIRELY outside any plausible
/// firmware clock -- one already expired (1990..2020), one not yet
/// valid (2400..2500, GeneralizedTime-encoded, past 2049). The
/// signature chains are genuine, so the window gate is the only
/// thing between them and a load; each must refuse at its own
/// window name, exactly the exclusivity the negative-carrier set
/// pins for the other shapes.
static NEG_CARRIER_WINDOW_EXPIRED: &[u8] = include_bytes!("carrier-neg-window-expired.der");
static NEG_CARRIER_WINDOW_FUTURE: &[u8] = include_bytes!("carrier-neg-window-future.der");
const NEGATIVE_CARRIERS: [&'static [u8]; 7] = [
    NEG_CARRIER_DIGEST,
    NEG_CARRIER_ANCHOR,
    NEG_CARRIER_SID,
    NEG_CARRIER_ATTRS,
    NEG_CARRIER_DETACHED,
    NEG_CARRIER_WINDOW_EXPIRED,
    NEG_CARRIER_WINDOW_FUTURE,
];
/// What each negative MUST be refused for (substring of the walk's
/// own named refusal), so a negative refused for a WRONG reason --
/// e.g. a parse truncation that skipped the actual check -- dies too.
const NEGATIVE_EXPECTED_REFUSALS: [&str; 7] = [
    "rsa: DigestInfo digest differs from the message digest",
    "carrier: the embedded signer certificate is not the anchor and does not verify under the anchor's key (no chain to the root)",
    "carrier: the SignerIdentifier's serial does not byte-equal the signer certificate's serialNumber",
    "signerInfo: signedAttrs present -- the stub walks -noattr carriers only",
    "encapContentInfo: eContent present -- the stub walks DETACHED carriers only",
    "cert: the leaf's validity window does not include the boot clock (expired at its notAfter)",
    "cert: the leaf's validity window does not include the boot clock (not valid before its notBefore)",
];

/// The dbx revocation walk's every-boot controls: GENERATED EFI
/// signature-list blobs (what the firmware's own dbx would carry)
/// that revoke the chain the walk itself verified -- the signer by
/// BYTE, the signer by issuer+serial (a re-key carrying the pair),
/// and the anchor by BYTE. `tools/make_carrier.sh` writes all three
/// next to the carriers; each must be refused for its own named
/// reason (see verify_kernel_carrier's dbx controls), the same
/// negative-set discipline as the carrier walks above.
/// and the anchor by BYTE, and the same two chains BY To-Be-Signed
/// HASH: `EFI_CERT_X509_SHA256_GUID` entries (the EFI_TIME +
/// SHA-256(TBS) payload shape, hash-typed revocations the real-world
/// Microsoft dbx updates use for revoked CAs). `tools/make_carrier.sh`
/// writes all five next to the carriers; each must be refused for its
/// own named reason (see verify_kernel_carrier's dbx controls), the
/// same negative-set discipline as the carrier walks above.
static DBX_NEG_SIGNER_BYTE: &[u8] = include_bytes!("dbx-neg-signer-byte.der");
static DBX_NEG_SIGNER_PAIR: &[u8] = include_bytes!("dbx-neg-signer-pair.der");
static DBX_NEG_ANCHOR_BYTE: &[u8] = include_bytes!("dbx-neg-anchor-byte.der");
static DBX_NEG_SIGNER_HASH: &[u8] = include_bytes!("dbx-neg-signer-hash.der");
static DBX_NEG_ANCHOR_HASH: &[u8] = include_bytes!("dbx-neg-anchor-hash.der");
const DBX_NEGATIVE_BLOBS: [&'static [u8]; 5] = [
    DBX_NEG_SIGNER_BYTE,
    DBX_NEG_SIGNER_PAIR,
    DBX_NEG_ANCHOR_BYTE,
    DBX_NEG_SIGNER_HASH,
    DBX_NEG_ANCHOR_HASH,
];
const DBX_NEGATIVE_NAMES: [&str; 5] = [
    "dbx-neg-signer-byte (the leaf sits in the revocation store)",
    "dbx-neg-signer-pair (a re-key carries the leaf's issuer+serial)",
    "dbx-neg-anchor-byte (the root sits in the revocation store)",
    "dbx-neg-signer-hash (a hash entry covers the leaf's To-Be-Signed)",
    "dbx-neg-anchor-hash (a hash entry covers the root's To-Be-Signed)",
];
/// The named refusal each dbx negative must die with (substrings of
/// dbx.rs's own refusals).
const DBX_NEGATIVE_EXPECTED_REFUSALS: [&str; 5] = [
    "(the signer certificate byte-matches an entry)",
    "(the signer's issuer+serial matches an entry)",
    "(the anchor certificate byte-matches an entry)",
    "(a hash-typed entry covers the signer certificate's To-Be-Signed)",
    "(a hash-typed entry covers the anchor certificate's To-Be-Signed)",
];

/// ---- the MOK path's every-boot control material ----

/// The generated MokList blob carrying the MOK CA (the trust shape shim's
/// MokManager would hold for a user-enrolled root), plus the STRANGER blob
/// (the REAL embedded root on the inside -- the MOK chain's stranger) as
/// the anchor-mismatch negative, and the MOK carrier (the alternate chain
/// the MOK leaf signs over the SAME kernel bytes):
static MOKLIST_POS: &[u8] = include_bytes!("moklist-pos.der");
static MOKLIST_NEG_STRANGER: &[u8] = include_bytes!("moklist-neg-stranger.der");
static MOK_CARRIER: &[u8] = include_bytes!("mok-carrier.der");

/// The dbx blob revoking the MOK leaf's TBS hash: the revocation gate
/// must cover MOK-anchored chains too, not only the built-in one.
static DBX_NEG_MOK_HASH: &[u8] = include_bytes!("dbx-neg-mok-hash.der");

static mut SYSTEM_TABLE: *const SystemTable = core::ptr::null();
static mut IMAGE_HANDLE: Handle = core::ptr::null_mut();

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    // After ExitBootServices the console is gone; the panic prints what
    // it can and halts either way. The stub's logic contains no
    // fallible-Rust paths (every refusal is a typed die() with its own
    // message), so a reaching panic handler means a code bug.
    boot::conout("\r\nnyrqis ubld: PANIC -- stub code bug; halting\r\n");
    boot::halt()
}

#[unsafe(no_mangle)]
pub extern "efiapi" fn efi_main(image_handle: Handle, system_table: *const SystemTable) -> Status {
    unsafe {
        SYSTEM_TABLE = system_table;
        IMAGE_HANDLE = image_handle;
    }

    // ---- system table sanity: signature, revision, surfaces ----
    {
        let st = unsafe { &*SYSTEM_TABLE };
        boot::check!(
            st.hdr.signature == EFI_SYSTEM_TABLE_SIGNATURE,
            "system-table signature wrong -- this is not an EFI System Table"
        );
        boot::check!(
            st.hdr.revision >= 0x0002_0000,
            "system-table revision below UEFI 2.0"
        );
        boot::check!(
            !st.boot_services.is_null() && !st.runtime_services.is_null(),
            "service tables missing from the system table"
        );
        boot::check!(!st.con_out.is_null(), "no console-output protocol");
    }

    // ---- 1. banner + SecureBoot state (a fact, not a policy: ADR-0014
    //         keeps SB-off boot legal; the record travels in the tag) ----
    boot::conout("\r\nnyrqis ubld: stub alive under UEFI; SecureBoot=");
    let sb = boot::read_secure_boot();
    boot::conout(sb.fact());
    boot::conout("\r\n");

    // ---- 1b. variable-services round trip: the protocol the
    //         SecureBoot fact rode on, proven with a variable this stub
    //         writes itself. A firmware failing one step lies about
    //         every variable, so the boot dies naming the exact step. ----
    boot::verify_variable_services();

    // ---- 1c. db enrollment: with the variable-services contract just
    //          PROVEN, hand the embedded anchor to the firmware's
    //          signature database in SetupMode (PK absent; a non-fatal,
    //          fully fact-logged step either way -- the boot's own gate
    //          is the carrier walk below, not an assumption about the
    //          db store).
    boot::enroll_trusted_root_in_db(TRUSTED_ROOT_BYTES);

    // ---- 2. watchdog off, allocations ----
    boot::disarm_watchdog();
    let pg_phys = boot::alloc_identity_pages(3, "page tables");
    let hp_phys = boot::alloc_identity_pages(1, "handoff page");
    boot::zero_range(pg_phys, 3 * 4096);
    boot::zero_range(hp_phys, 4096);

    // ---- 3. kernel image ----
    let kernel_bytes = kernel_elf_bytes();
    // ---- 3a. Stage-2 integrity verification (the ADR-0014 chain's
    //         first landed link): hash EVERY byte of the embedded
    //         kernel image and refuse to load it unless the digest
    //         matches the one build.sh recorded at link time. A
    //         corrupted or mismatched payload halts here with both
    //         digests printed, before a single kernel byte is copied.
    verify_kernel_integrity(kernel_bytes);
    // ---- 3b. The ADR-0014 chain's authenticated half lands its link
    //         right behind the integrity pin: the carrier's RSA-2048
    //         PKCS#1 v1.5 signature is checked over EXACTLY the bytes
    //         the stub is about to copy into their vaddr, with the
    //         signer certificate pinned byte-for-byte against the
    //         embedded anchor; and the refusal path is proven alive
    //         before any kernel byte moves. The chain's expiry and
    //         revocation gates (windows + dbx) run inside, each on
    //         firmware-OWNED datum: the RTC reading is refused unless
    //         it is a plausible clock, never faked.
    let walk_clock = boot::firmware_time_or_die();
    boot::conout("nyrqis ubld: walk clock: firmware GetTime = ");
    print_walk_clock(walk_clock);
    boot::conout("\r\n");
    verify_kernel_carrier(kernel_bytes, walk_clock);
    let ki = match elf::parse(kernel_bytes) {
        Ok(ki) => ki,
        Err(e) => boot::die(elf_error_str(e)),
    };
    for seg in ki.segments.iter().flatten() {
        unsafe {
            let dst = seg.vaddr as *mut u8;
            core::ptr::copy_nonoverlapping(seg.data.as_ptr(), dst, seg.data.len());
            core::ptr::write_bytes(
                dst.add(seg.data.len()),
                0,
                (seg.memsz as usize) - seg.data.len(),
            );
        }
    }
    let entry = match elf::find_symbol(kernel_bytes, "uefi_entry") {
        Ok(a) => a,
        Err(e) => boot::die(elf_error_str(e)),
    };
    boot::conout("nyrqis ubld: kernel image loaded from the stub's own bytes; uefi_entry resolved\r\n");

    // ---- 4. memory map + RSDP + synthesis into the handoff page ----
    let mut map = boot::acquire_memory_map();
    boot::conout("nyrqis ubld: memory map acquired\r\n");
    // The stub's own living structures must never appear as free frames
    // in the synthesized map: the kernel's allocator would reuse them
    // and corrupt the running paging structures. Carve them out before
    // synthesis -- the delta is machine-checkable from the MBI itself.
    {
        let mut reserved: alloc::vec::Vec<(u64, u64)> = alloc::vec::Vec::new();
        reserved.push((pg_phys, pg_phys + 3 * 4096)); // identity PML4/PDP/PD
        reserved.push((hp_phys, hp_phys + 4096)); // handoff page (the MBI)
        reserved.push((map.buf_base, map.buf_base + map.buf_capacity as u64));
        boot::carve_stub_ranges(&mut map, &reserved);
    }
    let rsdp = boot::find_acpi_rsdp();
    match rsdp {
        Some((_, rev)) => {
            boot::conout("nyrqis ubld: rsdp found (acpi config table, rev=");
            boot::conout(if rev >= 2 { "2.0" } else { "1.0" });
            boot::conout(")\r\n");
        }
        None => boot::conout("nyrqis ubld: rsdp ABSENT (no config-table entry)\r\n"),
    }
    let _synth_facts = {
        let page = unsafe { core::slice::from_raw_parts_mut(hp_phys as *mut u8, 4096) };
        match mb2::synthesize(page, &map.descriptors, map.desc_size, rsdp, sb.digit()) {
            Ok(f) => f,
            Err(e) => boot::die(e),
        }
    };
    boot::conout("nyrqis ubld: Multiboot2 info synthesized (map entries + rsdp + vendor tag)\r\n");
    // Dump ACPI-reclaimable regions (EFI type 9) for the transcript: the
    // kernel's reclaim self-check trusts this exact map.
    for d in map.descriptors.iter() {
        if d.r#type == 9 {
            boot::conout("acpi-reclaim region: base=");
            boot::conout_hex(d.physical_start);
            boot::conout(" pages=");
            boot::conout_hex(d.number_of_pages);
            boot::conout("\r\n");
        }
    }
    // One-shot MBI walk for the transcript: tag types in the synthesized
    // structure, so a missing RSDP tag is diagnosable from the log.
    {            unsafe {
            let mut c = hp_phys + 8;
            for _ in 0..8 {
                let typ = core::ptr::read_unaligned(c as *const u32);
                let sz = core::ptr::read_unaligned((c + 4) as *const u32);
                boot::conout_hex(typ as u64);
                boot::conout(":");
                boot::conout_hex(sz as u64);
                boot::conout(" ");
                if typ == 0 {
                    break;
                }
                c = (c + sz as u64 + 7) & !7u64;
            }
        }
        boot::conout("\r\n");
    }

    // ---- 5. identity tables, exit boot services, switch, jump ----
    boot::build_identity_tables(pg_phys);
    boot::exit_boot_services(&mut map);
    unsafe {
        asm!("mov {p}, %cr3", p = in(reg) pg_phys, options(att_syntax, preserves_flags));
    }
    boot::raw_uart_write("[s] cr3 switched; jumping uefi_entry\r\n");
    // No firmware calls from here: the console is gone by design; the
    // kernel's own serial driver takes over diagnostics.
    unsafe {
        asm!(
            "mov {m}, %rdi",
            "mov {b}, %rsi",
            "jmp *{e}",
            m = in(reg) elf::MB2_BOOTLOADER_MAGIC,
            b = in(reg) hp_phys,
            e = in(reg) entry,
            options(att_syntax, noreturn)
        );
    }
}

/// The ADR-0014 chain's authenticated half: the carrier walk.
///
/// Missing-carrier refusal FIRST: `include_bytes!` on a file that
/// does not exist fails the COMPILE (build.sh runs make_carrier.sh
/// before the stub ever builds), so the runtime shape of "missing"
/// here is a carrier written empty or truncated -- and both refusals
/// share the walk: an EMPTY blob is refused here by name, a truncated
/// or malformed one is refused by every named step inside
/// `trust::verify_detached_signed_data` propagating its own.
fn print_two_digits(v: u8) {
    const D: &[u8] = b"0123456789";
    boot::conout(core::str::from_utf8(&[D[(v / 10) as usize], D[(v % 10) as usize]]).unwrap());
}

fn print_decimal(mut v: u64) {
    const D: &[u8] = b"0123456789";
    let mut buf = [0u8; 20];
    let mut n = 0usize;
    if v == 0 {
        boot::conout("0");
        return;
    }
    while v > 0 {
        buf[n] = D[(v % 10) as usize];
        n += 1;
        v /= 10;
    }
    let mut out = [0u8; 20];
    for i in 0..n {
        out[i] = buf[n - 1 - i];
    }
    boot::conout(core::str::from_utf8(&out[..n]).unwrap());
}

/// The walk's clock, as the transcript shows it (YYYY-MM-DD
/// hh:mm:ss, the fields GetTime carries and the windows compare).
fn print_walk_clock(now: trust::Rfc5280Time) {
    print_decimal(now.year as u64);
    boot::conout("-");
    print_two_digits(now.month);
    boot::conout("-");
    print_two_digits(now.day);
    boot::conout(" ");
    print_two_digits(now.hour);
    boot::conout(":");
    print_two_digits(now.minute);
    boot::conout(":");
    print_two_digits(now.second);
}

fn verify_kernel_carrier(kernel: &[u8], now: trust::Rfc5280Time) {
    if TRUSTED_ROOT_BYTES.is_empty() || CARRIER_BYTES.is_empty() {
        boot::die(
            "carrier blob missing: tools/make_carrier.sh wrote no trusted-root DER and/or no carrier DER; \
             refusing to boot an UNAUTHENTICATED kernel image",
        );
    }
    boot::conout(
        "nyrqis ubld: carrier verify: pinning the signer certificate against the embedded anchor, then walking the detached carrier\r\n",
    );
    let chain = match trust::verify_trusted_chain(TRUSTED_ROOT_BYTES, CARRIER_BYTES, kernel, now) {
        Ok(chain) => chain,
        Err(err) => boot::die(err),
    };
    boot::conout(
        "nyrqis ubld: carrier verify OK -- RSA-2048 PKCS#1 v1.5 signature over the SAME bytes about to be loaded\r\n",
    );

    // The refusal path is proven live at every boot: a one-bit-flipped
    // COPY of the content must FAIL the walk, or none of the refusals
    // above can be trusted as a check at all (mirrors the hash
    // integrity control's flipped-copy test exactly).
    let mut tampered: alloc::vec::Vec<u8> = alloc::vec::Vec::with_capacity(kernel.len());
    tampered.extend_from_slice(kernel);
    tampered[0] ^= 0x01;
    if trust
        ::verify_trusted_chain(TRUSTED_ROOT_BYTES, CARRIER_BYTES, &tampered, now)
        .is_ok()
    {
        boot::die(
            "carrier refusal-path control FAILED: a one-bit-flipped copy of the kernel image still verifies PKCS#1 v1.5",
        );
    }
    boot::conout(
        "nyrqis ubld: carrier refusal-path control OK -- a one-bit-flipped copy verifies to a named refusal, the trust path is alive\r\n",
    );

    // The negative carrier set, every boot: each one must VERIFY to
    // NO fact, be REFUSED by the walk, and be refused for the RIGHT
    // named reason. A negative verifying means the refusal path is
    // dead for that exact attack shape; being refused for a different
    // reason means a check was skipped (e.g. a parse truncation)
    // -- both die, naming which negative misbehaved.
    const NEGATIVE_CARRIER_NAMES: [&str; 7] = [
        "neg-digest (signed over altered content)",
        "neg-anchor (stray unanchored signer)",
        "neg-sid (SignerInfo serial byte-flipped)",
        "neg-attrs (signedAttrs carried)",
        "neg-detached (carrier embeds its payload)",
        "neg-window-expired (signer window 1990..2020)",
        "neg-window-future (signer window 2400..2500)",
    ];
    for i in 0..NEGATIVE_CARRIERS.len() {
        match trust::verify_trusted_chain(TRUSTED_ROOT_BYTES, NEGATIVE_CARRIERS[i], kernel, now) {
            Ok(_) => boot::die(
                "carrier negative-control FAILED: a generated negative carrier VERIFIED over the real kernel image",
            ),
            Err(e) => {
                if !e.contains(NEGATIVE_EXPECTED_REFUSALS[i]) {
                    boot::die("carrier negative-control refused at the WRONG named step for the negative carrier, check skipped or misordered");
                }
            }
        }
        boot::conout("nyrqis ubld: carrier neg control: ");
        boot::conout(NEGATIVE_CARRIER_NAMES[i]);
        boot::conout(" -- refused as required (claimed");
        boot::conout(NEGATIVE_EXPECTED_REFUSALS[i]);
        boot::conout(")\r\n");
    }
    boot::conout(
        "nyrqis ubld: carrier neg controls OK -- all seven negative carriers refused for their named reasons\r\n",
    );

    // ---- the revocation gate: the chain that just verified must not
    //         sit in the firmware's dbx (the ADR-0014 chain's revocation
    //         reading). The firmware's store is the datum; the generated
    //         controls below prove the walk's limbs every boot, the
    //         same way the negative carriers prove the signature walk's.
    let (dbx_status, dbx_bytes) = boot::read_dbx();
    match dbx_status {
        EFI_NOT_FOUND => {
            boot::conout(
                "  dbx: absent on this firmware (EFI_NOT_FOUND) -- no revocation store to consult (named fact)\r\n",
            );
        }
        EFI_SUCCESS => {
            if dbx_bytes.is_empty() {
                boot::conout(
                    "  dbx: present but empty (0 signature-list bytes) -- nothing to enforce\r\n",
                );
            } else {
                // Transcript: the SERVED bytes' shape first, then the
                // walk. The type GUID is the first 16 bytes -- what
                // classification turns on -- so a served-vs-composed
                // mismatch (or a constants drift) is on the screen,
                // not inferred from the counts alone.
                boot::conout("  dbx: firmware served ");
                print_decimal(dbx_bytes.len() as u64);
                boot::conout(" signature-list bytes; type GUID (");
                boot::conout_hex(dbx_bytes[0] as u64);
                for b in &dbx_bytes[1..16] {
                    boot::conout(" ");
                    boot::conout_hex(*b as u64);
                }
                boot::conout(" )\r\n");
                match dbx::check_blob(
                    &dbx_bytes,
                    &[
                        now.year as u16,
                        now.month as u16,
                        now.day as u16,
                        now.hour as u16,
                        now.minute as u16,
                        now.second as u16,
                    ],
                    &chain,
                ) {
                    Ok(facts) => {
                        boot::conout("  dbx: walked ");
                        print_decimal(facts.x509_entries_walked as u64);
                        boot::conout(
                            " X.509 entries, none revoke the just-verified chain",
                        );
                        if facts.x509_sha256_entries_walked > 0 {
                            boot::conout("; walked ");
                            print_decimal(facts.x509_sha256_entries_walked as u64);
                            boot::conout(
                                " hash-typed entries, none in force against the chain (named)",
                            );
                        }
                        if facts.other_type_entries_skipped > 0 {
                            boot::conout("; skipped ");
                            print_decimal(facts.other_type_entries_skipped as u64);
                            boot::conout(
                                " entries of types this walk does not enforce (named)",
                            );
                        }
                        boot::conout("\r\n");
                    }
                    Err(e) => boot::die(e),
                }
            }
        }
        s => {
            boot::conout("  dbx: GetVariable(dbx) = ");
            boot::conout_hex(s);
            boot::conout(" -- unreadable, enforced nothing (named fact)\r\n");
        }
    }
    for i in 0..DBX_NEGATIVE_BLOBS.len() {
        match dbx::check_blob(
            DBX_NEGATIVE_BLOBS[i],
            &[
                now.year as u16,
                now.month as u16,
                now.day as u16,
                now.hour as u16,
                now.minute as u16,
                now.second as u16,
            ],
            &chain,
        ) {
            Ok(_) => boot::die(
                "dbx negative-control FAILED: a generated revocation blob did NOT refuse the chain the walk just accepted",
            ),
            Err(e) => {
                if !e.contains(DBX_NEGATIVE_EXPECTED_REFUSALS[i]) {
                    boot::die(
                        "dbx negative-control refused at the WRONG named step -- a limb of the revocation walk is dead or misordered",
                    );
                }
            }
        }
        boot::conout("  dbx neg control: ");
        boot::conout(DBX_NEGATIVE_NAMES[i]);
        boot::conout(" -- refused as required (claimed");
        boot::conout(DBX_NEGATIVE_EXPECTED_REFUSALS[i]);
        boot::conout(")\r\n");
    }
    boot::conout(
        "  dbx neg controls OK -- all five generated revocations refused for their named reasons\r\n",
    );

    // The MOK reading: the chain's OPTIONAL user enrollment surface,
    // facts + controls, every boot (mok_walk_and_controls below).
    mok_walk_and_controls(kernel, now);
}

fn mok_now_fields(now: trust::Rfc5280Time) -> [u16; 6] {
    [
        now.year as u16,
        now.month as u16,
        now.day as u16,
        now.hour as u16,
        now.minute as u16,
        now.second as u16,
    ]
}

// ---- the MOK reading: the chain's OPTIONAL user enrollment surface
// (mok.rs). The firmware's shim MokList variable is read whole and
// its facts printed; the generated control set below proves the
// extension's limbs every boot -- the ONLY trust the default OVMF
// store puts under the stub is the built-in carrier chain, so the
// controls are what makes the MOK path live in every build; the
// every-boot discipline is the same as the dbx set's: each control
// must produce ITS named fact, and a dead or misordered limb dies
// instead of finishing the boot.
fn mok_walk_and_controls(kernel: &[u8], now: trust::Rfc5280Time) {
    // (a) The firmware's own MokList store, named fact either way:
    // walked when present (a covered chain is a named extra fact per
    // entry), EFI_NOT_FOUND named when the firmware has no shim keys.
    let (mok_status, mok_bytes) = boot::read_moklist();
    match mok_status {
        EFI_SUCCESS => {
            if mok_bytes.is_empty() {
                boot::conout(
                    "  MokList: present but empty (0 signature-list bytes) -- no user anchors (named fact)\r\n",
                );
            } else {
                match mok::walk(&mok_bytes) {
                    Ok(w) => {
                        boot::conout("  MokList: walked ");
                        print_decimal(w.anchors.len() as u64);
                        boot::conout(" X.509 entries");
                        if w.other_type_entries_skipped > 0 {
                            boot::conout("; skipped ");
                            print_decimal(w.other_type_entries_skipped as u64);
                            boot::conout(" entries of types this walk does not enforce (named)");
                        }
                        boot::conout("\r\n");
                        for i in 0..w.anchors.len() {
                            if trust::verify_mok_anchored_chain(
                                w.anchor_ders[i],
                                CARRIER_BYTES,
                                kernel,
                                now,
                            )
                            .is_ok()
                            {
                                boot::conout("  MokList: entry #");
                                print_decimal(i as u64);
                                boot::conout(" ALSO covers the just-verified chain (MOK trust path extends the embedded anchor, named fact)\r\n");
                            }
                        }
                    }
                    Err(e) => boot::die(e),
                }
            }
        }
        EFI_NOT_FOUND => {
            boot::conout(
                "  MokList: absent on this firmware (EFI_NOT_FOUND) -- no shim user keys (named fact)\r\n",
            );
        }
        s => {
            boot::conout("  MokList: GetVariable(MokList) = ");
            boot::conout_hex(s);
            boot::conout(" -- unreadable, consulted nothing (named fact)\r\n");
        }
    }

    // (b) Control M1: the generated MOK chain (a second, INDEPENDENT
    // one-hop chain the stub does NOT anchor) verifies under the
    // generated MokList blob's first entry -- the enrollment shape
    // MokManager's writes give the walk.
    let chain_mok = match mok::walk(MOKLIST_POS) {
        Ok(w) if !w.anchor_ders.is_empty() => {
            match trust::verify_mok_anchored_chain(w.anchor_ders[0], MOK_CARRIER, kernel, now) {
                Ok(c) => c,
                Err(e) => boot::die(e),
            }
        }
        Ok(_) => boot::die("MOK control FAILED: the generated MokList blob carries no X.509 anchor"),
        Err(e) => boot::die(e),
    };
    boot::conout(
        "  MOK control: mok-carrier (the alternate MOK chain) verified under the MokList blob's entry -- MOK anchoring works as enrolled\r\n",
    );

    // (c) Control M2: the same MOK carrier must NOT verify under the
    // embedded anchor -- MOK trust is NOT the built-in pin's; a
    // carrier the built-in chain does not cover stays refused unless
    // a MokList entry takes it in.
    match trust::verify_trusted_chain(TRUSTED_ROOT_BYTES, MOK_CARRIER, kernel, now) {
        Ok(_) => boot::die(
            "MOK control FAILED: the alternate MOK chain verified under the EMBEDDED anchor -- the anchor sets are conflated",
        ),
        Err(e) => {
            if !e.contains("no chain to the root") {
                boot::die(
                    "MOK control refused at the WRONG named step -- the stranger-refusal path is dead or misordered",
                );
            }
        }
    }
    boot::conout(
        "  MOK control: mok-carrier refused under the embedded anchor -- MOK trust does not ride on the built-in chain's pin\r\n",
    );

    // (d) Control M3: the stranger MokList blob (the REAL embedded
    // root inside, nothing to do with the MOK chain) must not anchor
    // the MOK carrier either; the refusal is the same named one --
    // "no chain to the root" -- which is exactly what a real user
    // carrier faces when the enrolled MokList simply does not cover it.
    match mok::walk(MOKLIST_NEG_STRANGER) {
        Ok(w) if !w.anchor_ders.is_empty() => {
            if w.anchor_ders.len() == w.anchors.len() {
                match trust::verify_mok_anchored_chain(w.anchor_ders[0], MOK_CARRIER, kernel, now) {
                    Ok(_) => boot::die(
                        "MOK control FAILED: the STRANGER MokList entry anchored the alternate MOK chain",
                    ),
                    Err(e) => {
                        if !e.contains("no chain to the root") {
                            boot::die(
                                "MOK control refused at the WRONG named step -- the MokList-entry refusal is dead or misordered",
                            );
                        }
                    }
                }
            } else {
                boot::die("MOK control error: anchor lists misaligned (internal misuse)")
            }
        }
        Ok(_) => boot::die("MOK control FAILED: the generated stranger MokList blob carries no X.509 anchor"),
        Err(e) => boot::die(e),
    }
    boot::conout(
        "  MOK control: mok-carrier refused under the STRANGER MokList blob -- an unrelated MokList covers nothing (named refusal)\r\n",
    );

    // (e) Control M5: the revocation gate covers MOK-anchored chains
    // too -- the same dbx walk, fed the blob carrying the MOK leaf's
    // TBS hash, must refuse the MOK chain the M1 control just made.
    match dbx::check_blob(DBX_NEG_MOK_HASH, &mok_now_fields(now), &chain_mok) {
        Ok(_) => boot::die(
            "MOK control FAILED: the MOK chain's revocation blob did NOT refuse the MOK chain -- dbx skips MOK-anchored boots",
        ),
        Err(e) => {
            if !e
                .contains("(a hash-typed entry covers the signer certificate's To-Be-Signed)")
            {
                boot::die(
                    "MOK control refused at the WRONG named step -- the MOK-chain revocation is dead or misordered",
                );
            }
        }
    }
    boot::conout(
        "  MOK control: dbx-neg-mok-hash refused the MOK chain -- the revocation gate covers MOK-anchored chains\r\n",
    );
}



fn elf_error_str(e: elf::ElfError) -> &'static str {
    match e {
        elf::ElfError::TooSmall => "kernel ELF smaller than an ELF64 header",
        elf::ElfError::BadMagic => "kernel ELF bad magic",
        elf::ElfError::Not64Bit => "kernel ELF is not 64-bit",
        elf::ElfError::NotExecutable(_) => "kernel ELF is not x86-64 ELF v1",
        elf::ElfError::Truncated(_) => "kernel ELF truncated/malformed structure",
        elf::ElfError::NoLoadSegments => "kernel ELF carries no PT_LOAD segment",
        elf::ElfError::SymbolMissing(_) => "kernel ELF misses the uefi_entry symbol",
    }
}
