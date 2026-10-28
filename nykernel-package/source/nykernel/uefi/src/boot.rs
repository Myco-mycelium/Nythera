//! Boot-services orchestration: memory map, ACPI RSDP, SecureBoot
//! variable, allocation, identity page tables, and the
//! ExitBootServices dance.

use alloc::vec::Vec;
use core::ffi::c_void;

use crate::efi::*;
use crate::{IDENTITY_LIMIT, IMAGE_HANDLE, SYSTEM_TABLE};

// ---- console ----

/// ASCII -> UTF-16 over the firmware console. Falls back to a raw halt
/// if the console refuses: a boot whose diagnostics cannot be emitted
/// is a boot that hangs silently, and this kernel treats that as
/// worse than a wrong answer.
pub fn conout(s: &str) {
    let mut wide = [0u16; 160];
    let bytes = s.as_bytes();
    let n = bytes.len().min(wide.len() - 1);
    for (i, &b) in bytes[..n].iter().enumerate() {
        wide[i] = b as u16;
    }
    let st = unsafe { &*SYSTEM_TABLE };
    let out = st.con_out;
    if out.is_null() {
        halt();
    }
    let status = unsafe { ((*out).output_string)(out, wide.as_ptr()) };
    if status != EFI_SUCCESS {
        halt();
    }
}

#[inline(never)]
pub fn halt() -> ! {
    loop {
        unsafe { core::arch::asm!("hlt") };
    }
}

/// Prints and halts: the stub has no recovery path of its own, and a
/// silent failure here would strand the boot with no diagnose-able
/// surface. Every refusal names exactly what had to hold.
pub fn conout_hex(v: u64) {
    let mut buf = [0u8; 16];
    let mut n = 0usize;
    let mut v = v;
    if v == 0 {
        conout("0x0");
        return;
    }
    while v > 0 && n < buf.len() {
        let d = (v & 0xf) as u8;
        buf[buf.len() - 1 - n] = if d < 10 { b'0' + d } else { b'a' + d - 10 };
        n += 1;
        v >>= 4;
    }
    conout("0x");
    let s = core::str::from_utf8(&buf[buf.len() - n..]).unwrap_or("?");
    conout(s);
}

pub fn die(msg: &str) -> ! {
    conout("\r\nnyrqis ubld: FAILED -- ");
    conout(msg);
    conout("\r\n");
    halt()
}

/// Assertion helper mirroring the kernel's own style: a false
/// condition ends the boot naming the fact that had to hold.
macro_rules! check {
    ($cond:expr, $msg:expr) => {
        if !$cond {
            crate::boot::die($msg);
        }
    };
}
pub(crate) use check;

// ---- SecureBoot variable ----

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum SecureBootState {
    Off,
    On,
    /// The variable does not exist (no SB enforcement surface at all).
    Absent,
    /// Exists but is not the 1-byte value the spec fixes.
    Malformed,
}

impl SecureBootState {
    /// The vendor tag carries ONE ASCII digit; absent and malformed
    /// both record '0' and the stub's own transcript line states which
    /// one it was, so nothing is hidden behind the digit.
    pub fn digit(self) -> u8 {
        match self {
            SecureBootState::Off | SecureBootState::Absent | SecureBootState::Malformed => 0,
            SecureBootState::On => 1,
        }
    }
    pub fn fact(self) -> &'static str {
        match self {
            SecureBootState::Off => "0",
            SecureBootState::On => "1",
            SecureBootState::Absent => "absent",
            SecureBootState::Malformed => "malformed",
        }
    }
}

/// Reads the `SecureBoot` firmware variable (EFI_GLOBAL_VARIABLE).
/// Per spec it is exactly one byte: 0 off, 1 on.
pub fn read_secure_boot() -> SecureBootState {
    let st = unsafe { &*SYSTEM_TABLE };
    let rs = st.runtime_services;
    get_variable_u8(b"SecureBoot", rs)
}

fn get_variable_u8(name_ascii: &[u8], rs: *const RuntimeServices) -> SecureBootState {
    let mut wide = [0u16; 32];
    for (i, &b) in name_ascii.iter().enumerate() {
        wide[i] = b as u16;
    }
    let mut attrs: u32 = 0;
    let mut size: usize = 0;
    // Query pass: a size-0 call must answer EFI_BUFFER_TOO_SMALL with
    // the data size the variable carries.
    let q = unsafe {
        ((*rs).get_variable)(wide.as_ptr(), &GLOBAL_VARIABLE_GUID, &mut attrs, &mut size, core::ptr::null_mut())
    };
    if q == EFI_NOT_FOUND {
        return SecureBootState::Absent;
    }
    if q != EFI_BUFFER_TOO_SMALL || size != 1 {
        return SecureBootState::Malformed;
    }
    let mut value: u8 = 0;
    let s = unsafe {
        ((*rs).get_variable)(
            wide.as_ptr(),
            &GLOBAL_VARIABLE_GUID,
            &mut attrs,
            &mut size,
            (&mut value as *mut u8) as *mut c_void,
        )
    };
    if s != EFI_SUCCESS {
        return SecureBootState::Malformed;
    }
    match value {
        0 => SecureBootState::Off,
        1 => SecureBootState::On,
        _ => SecureBootState::Malformed,
    }
}

// ---- the variable-services round trip (the SecureBoot read's own
// protocol, verified with a variable WE write and know the bytes of) ----
//
// `read_secure_boot` trusts the firmware's GetVariable to honor its
// documented contract (query pass answers BUFFER_TOO_SMALL with the
// TRUE size; the data pass fills the caller's buffer and reads back
// exactly the bytes stored). That contract is checkable for real, at
// every boot, with a scratch variable this stub writes itself: set /
// get / too-small-query / get-after-oversize / delete /
// get-after-delete, each answer pinned to the spec's status. The
// scratch variable is deliberately VOLATILE (BOOTSERVICE_ACCESS only --
// no NON_VOLATILE bit) so the check can never write firmware NVRAM,
// and the stub deletes it before ExitBootServices regardless.
//
// A firmware that fails any step is a firmware whose SecureBoot ANSWER
// cannot be trusted either, so the boot dies here, naming the exact
// step -- the same refuse-loudly posture as every other UEFI surface.

/// The scratch variable's name (pure ASCII, null-terminated by the
/// wide-buffer fill below).
const ROUNDTRIP_NAME: &[u8] = b"NyrqisVarChk";
/// The payload the check stores and expects back, byte-for-byte.
const ROUNDTRIP_PAYLOAD: [u8; 4] = [0x5a, 0xa5, 0x3c, 0xc3];

fn wide_name(name_ascii: &[u8]) -> [u16; 32] {
    let mut wide = [0u16; 32];
    for (i, &b) in name_ascii.iter().enumerate() {
        wide[i] = b as u16;
    }
    wide
}

fn get_scratch(rs: *const RuntimeServices) -> (Status, usize, [u8; 4]) {
    let mut attrs: u32 = 0;
    let mut size: usize = 4;
    let mut data = [0u8; 4];
    let s = unsafe {
        ((*rs).get_variable)(
            wide_name(ROUNDTRIP_NAME).as_ptr(),
            &VARCHK_VENDOR_GUID,
            &mut attrs,
            &mut size,
            data.as_mut_ptr() as *mut c_void,
        )
    };
    (s, size, data)
}

fn set_scratch_with_size(rs: *const RuntimeServices, size: usize, data: &[u8]) -> Status {
    unsafe {
        ((*rs).set_variable)(
            wide_name(ROUNDTRIP_NAME).as_ptr(),
            &VARCHK_VENDOR_GUID,
            VARIABLE_ATTRIBUTE_BOOTSERVICE,
            size,
            data.as_ptr() as *const c_void,
        )
    }
}

/// Runs the round trip, printing one fact per step; a wrong answer at
/// any step is a refused boot (a firmware whose variable protocol lies
/// about one variable lies about every variable).
pub fn verify_variable_services() {
    let st = unsafe { &*SYSTEM_TABLE };
    let rs = st.runtime_services;

    // Step 0: the scratch variable must not exist at handoff (its name
    // is this stub's own; a pre-existing one means two boots interleaved
    // and the check's answers mean different stores).
    let (s0, _, _) = get_scratch(rs);
    if s0 != EFI_NOT_FOUND {
        conout("  varchk: step0 pre-existing scratch variable, get_variable=");
        conout_hex(s0);
        die("variable-services roundtrip: scratch name collision");
    }
    conout("  varchk: step0 get = EFI_NOT_FOUND, correct (nothing written yet)\r\n");

    // Step 1: SET the 4-byte payload (volatile attributes only).
    let s1 = set_scratch_with_size(rs, ROUNDTRIP_PAYLOAD.len(), &ROUNDTRIP_PAYLOAD);
    if s1 != EFI_SUCCESS {
        conout("  varchk: step1 set_variable=");
        conout_hex(s1);
        die("variable-services roundtrip: SetVariable refused a legal volatile write");
    }
    conout("  varchk: step1 set (4 bytes) -- EFI_SUCCESS\r\n");

    // Step 2: GET must return exactly the bytes stored.
    let (s2, size2, data2) = get_scratch(rs);
    if s2 != EFI_SUCCESS || size2 != ROUNDTRIP_PAYLOAD.len() || data2 != ROUNDTRIP_PAYLOAD {
        conout("  varchk: step2 get status=");
        conout_hex(s2);
        conout(" size=");
        conout_hex(size2 as u64);
        conout("\r\n");
        die("variable-services roundtrip: GetVariable read back differs from the stored payload");
    }
    conout("  varchk: step2 get -- the 4 stored bytes, exact (5aa53cc3)\r\n");

    // Step 3: the query-pass contract -- a buffer-too-small GET must
    // answer BUFFER_TOO_SMALL naming the TRUE size (4), not the
    // caller's 2. This is exactly the contract read_secure_boot relies
    // on to learn SecureBoot's fixed size of 1.
    let mut attrs: u32 = 0;
    let mut probe: usize = 2;
    let mut _scratch = 0u64;
    let s3 = unsafe {
        ((*rs).get_variable)(
            wide_name(ROUNDTRIP_NAME).as_ptr(),
            &VARCHK_VENDOR_GUID,
            &mut attrs,
            &mut probe,
            (&mut _scratch as *mut u64) as *mut c_void,
        )
    };
    if s3 != EFI_BUFFER_TOO_SMALL || probe != ROUNDTRIP_PAYLOAD.len() {
        conout("  varchk: step3");
        conout_hex(s3);
        conout(" reported size=");
        conout_hex(probe as u64);
        conout("\r\n");
        die("variable-services roundtrip: GetVariable's too-small query answers wrongly");
    }
    conout("  varchk: step3 too-small query -- EFI_BUFFER_TOO_SMALL with the true size 4\r\n");

    // Step 4: DELETE (size 0) and confirm absence.
    let s4a = set_scratch_with_size(rs, 0, &[]);
    if s4a != EFI_SUCCESS {
        conout("  varchk: step4 delete=");
        conout_hex(s4a);
        conout("\r\n");
        die("variable-services roundtrip: SetVariable(0) refused the delete");
    }
    let (s4b, _, _) = get_scratch(rs);
    if s4b != EFI_NOT_FOUND {
        conout("  varchk: step4 after-delete get=");
        conout_hex(s4b);
        conout("\r\n");
        die("variable-services roundtrip: deleted variable still reads");
    }
    conout("  varchk: step4 delete then get -- EFI_NOT_FOUND, really gone\r\n");
    conout("  varchk: variable-services roundtrip OK -- the SecureBoot read's protocol is firmware-honest\r\n");
}

// ---- the walk's clock and the revocation store: the two firmware
// inputs the chain's expiry/revocation gates compare against (see
// trust.rs's validity windows and dbx.rs's walk). Both refuse rather
// than degrade: an unreadable clock or a malformed dbx blob halts
// with its name, NPS-001 §6.2-style, never boot an unverifiable ----

/// Reads the firmware's RTC through runtime-services GetTime and
/// converts it to the walk's clock type, refusing by NAME when the
/// reading is missing or implausible: the validity windows only gate
/// honestly if the datum they are compared against is a real clock
/// reading, and a dead clock would make the gate decorative while
/// still printing its pass line.
pub fn firmware_time_or_die() -> crate::trust::Rfc5280Time {
    let st = unsafe { &*SYSTEM_TABLE };
    let rs = st.runtime_services;
    let mut now = EfiTime::zero();
    let s = unsafe { ((*rs).get_time)(&mut now as *mut EfiTime) };
    if s != EFI_SUCCESS {
        conout("  walk-clock: GetTime = ");
        conout_hex(s);
        conout("\r\n");
        die(
            "firmware clock: GetTime refused -- the chain's expiry gate refuses an unreadable clock",
        );
    }
    if now.year < 2000
        || now.year > 2999
        || now.month < 1
        || now.month > 12
        || now.day < 1
        || now.day > 31
    {
        conout("  walk-clock: the firmware reports year=");
        conout_hex(now.year as u64);
        conout(" month=");
        conout_hex(now.month as u64);
        conout(" day=");
        conout_hex(now.day as u64);
        conout("\r\n");
        die(
            "firmware clock: the reading is implausible -- the chain's expiry gate refuses to run on it",
        );
    }
    crate::trust::Rfc5280Time::from_parts(
        now.year, now.month, now.day, now.hour, now.minute, now.second,
    )
}

/// Reads the whole `dbx` variable (the revocation store the chain's
/// gate consults): the same query+data contract as every other
/// variable read in this stub. (Status, bytes); EFI_NOT_FOUND arrives
/// as empty bytes and the caller treats it as the named fact it is.
pub fn read_dbx() -> (Status, alloc::vec::Vec<u8>) {
    let st = unsafe { &*SYSTEM_TABLE };
    let rs = st.runtime_services;
    get_variable_bytes(b"dbx", &IMAGE_SECURITY_DATABASE_GUID, rs)
}

// ---- allocation ----

/// The pre-EBS global allocator: forwards to the boot-services pool
/// allocator. It exists ONLY while boot services are alive (the entire
/// window every alloc call happens in); ExitBootServices reclaims the
/// whole pool, so dealloc is a no-op by design, not an omission -- the
/// memory's lifetime is the boot handoff's, and the kernel owns all
/// physical memory afterward. Alignment beyond the pool's 8-byte
/// guarantee is served by over-allocating and aligning up.
pub struct UefiPoolAlloc;

unsafe impl core::alloc::GlobalAlloc for UefiPoolAlloc {
    unsafe fn alloc(&self, layout: core::alloc::Layout) -> *mut u8 {
        let st = &*SYSTEM_TABLE;
        let bs = st.boot_services;
        let align = layout.align().max(8);
        let total = layout.size() + align;
        let mut raw: *mut c_void = core::ptr::null_mut();
        let s = ((*bs).allocate_pool)(MT_LOADER_DATA, total, &mut raw as *mut *mut c_void as *mut c_void);
        if s != EFI_SUCCESS || raw.is_null() {
            return core::ptr::null_mut();
        }
        let base = raw as usize;
        let aligned = (base + align - 1) & !(align - 1);
        aligned as *mut u8
    }
    unsafe fn dealloc(&self, _ptr: *mut u8, _layout: core::alloc::Layout) {
        // Intentional no-op: see the struct commentary. Nothing in this
        // stub frees before ExitBootServices reclaims the entire pool.
    }
}

#[global_allocator]
static POOL_ALLOCATOR: UefiPoolAlloc = UefiPoolAlloc;

/// AllocateAnyPages EfiLoaderData, refused if the allocator insists on
/// handing out memory outside the kernel's Phase-1 identity window.
/// `label` documents the caller for the transcript; the typed refusal
/// itself is uniform.
pub fn alloc_identity_pages(pages: usize, _label: &str) -> u64 {
    let st = unsafe { &*SYSTEM_TABLE };
    let bs = st.boot_services;
    let mut base: u64 = 0;
    let status = unsafe { ((*bs).allocate_pages)(ALLOC_ANY, MT_LOADER_DATA, pages, &mut base) };
    if status != EFI_SUCCESS {
        conout("\r\nyyrqis ubld: AllocatePages status = ");
        conout_hex(status);
        conout("\r\n");
        die("allocation refused (firmware status != success)");
    }
    check!(
        base + (pages as u64) * 4096 <= IDENTITY_LIMIT,
        "allocation outside the 1GiB identity window"
    );
    base
}

/// Direct COM1 writes for post-ExitBootServices diagnostics: the
/// firmware console is gone after EBS, but the UART is just a port.
/// Used for the three stub facts the next silence would hide: EBS
/// returned, CR3 switched, kernel handoff executed.
pub fn raw_uart_write(s: &str) {
    for &b in s.as_bytes() {
        unsafe {
            core::arch::asm!(
                "out %al, %dx",
                in("al") b,
                in("dx") 0x3f8u16,
                options(nostack, att_syntax)
            );
        }
    }
}

// EFI memory types that the synthesized Multiboot2 map may advertise
// as Available: conventional (7) and boot-services (3, 4 -- free after
// ExitBootServices by spec).
const EFI_USABLE_TYPES: [u32; 3] = [3, 4, 7];

/// Splits every usable descriptor around `reserved` page ranges so the
/// synthesized map NEVER offers the stub's own living structures
/// (identity page tables, handoff page, map buffer) to the kernel's
/// frame allocator as free frames. Without this, an allocator grab
/// overwrites the very page tables the kernel is running on -- exactly
/// the class of silent corruption a sourced MBI must exclude.
/// Overlaps that shrink to zero length are dropped; overlapping the
/// same range twice is legal and idempotent.
pub fn carve_stub_ranges(map: &mut MemoryMap, reserved: &[(u64, u64)]) {
    let mut out: Vec<MemoryDescriptor> = Vec::with_capacity(map.descriptors.len() + reserved.len());
    for d in map.descriptors.iter() {
        if !EFI_USABLE_TYPES.contains(&d.r#type) {
            out.push(*d);
            continue;
        }
        let base = d.physical_start;
        let end = base + d.number_of_pages * 4096;
        let mut cuts: Vec<(u64, u64)> = core::iter::once((base, end)).collect();
        for &(rb, re) in reserved {
            let mut next: Vec<(u64, u64)> = Vec::with_capacity(cuts.len() + 2);
            for (cb, ce) in cuts {
                // No overlap with the reserved range: keep whole.
                if ce <= rb || re <= cb {
                    next.push((cb, ce));
                    continue;
                }
                if rb > cb {
                    next.push((cb, rb));
                }
                if re < ce {
                    next.push((re, ce));
                }
            }
            cuts = next;
        }
        for (cb, ce) in cuts {
            if ce <= cb {
                continue;
            }
            out.push(MemoryDescriptor {
                r#type: d.r#type,
                pad: 0,
                physical_start: cb,
                virtual_start: 0,
                number_of_pages: (ce - cb) / 4096,
                attribute: d.attribute,
            });
        }
    }
    map.descriptors = out;
}

pub fn zero_range(phys: u64, len: usize) {
    unsafe {
        core::ptr::write_bytes(phys as *mut u8, 0, len);
    }
}

// ---- memory map ----

pub struct MemoryMap {
    /// Descriptors copied into alloc memory so synthesis reads a
    /// stable copy rather than memory the firmware may reuse.
    pub descriptors: Vec<MemoryDescriptor>,
    /// Raw buffer address/capacity, kept for the pre-EBS re-fetch
    /// (the fresh-key GetMemoryMap immediately before ExitBootServices).
    pub buf_base: u64,
    /// Bytes the fetch actually reported (on success the firmware
    /// writes back the real map size, not the requested capacity).
    pub buf_bytes: usize,
    /// Bytes ALLOCATED for the buffer -- what the re-fetch must use as
    /// its size argument, because the map can legitimately grow between
    /// the fetch and the re-fetch (the stub's own pool allocations are
    /// exactly such growth).
    pub buf_capacity: usize,
    pub key: u64,
    pub desc_size: usize,
}

/// Required growth headroom: allocating page tables between the size
/// query and the pre-EBS re-fetch grows the map. Growth beyond this is
/// a typed refusal, never an unbounded retry loop.
const HEADROOM_ENTRIES: usize = 256;

/// The real GetMemoryMap acquisition: size query (the firmware is
/// REQUIRED to answer EFI_BUFFER_TOO_SMALL with the needed size when
/// the buffer is empty), one generous allocation, one fetch.
pub fn acquire_memory_map() -> MemoryMap {
    let st = unsafe { &*SYSTEM_TABLE };
    let bs = st.boot_services;
    let mut size: usize = 0;
    let mut key: u64 = 0;
    let mut desc_size: usize = 0;
    let mut desc_version: u32 = 0;

    let mut status = unsafe {
        ((*bs).get_memory_map)(&mut size, core::ptr::null_mut(), &mut key, &mut desc_size, &mut desc_version)
    };
    check!(
        status == EFI_BUFFER_TOO_SMALL && size > 0,
        "GetMemoryMap size query did not answer BUFFER_TOO_SMALL with a positive size"
    );
    check!(
        desc_size as usize >= core::mem::size_of::<MemoryDescriptor>(),
        "firmware descriptor size below the spec minimum"
    );

    size += HEADROOM_ENTRIES * desc_size;
    check!(size <= 1024 * 1024, "firmware memory map implausibly large");

    let alloc_bytes = (size + 4095) & !4095usize;
    let buf_base = {
        let mut buf: u64 = 0;
        let s = unsafe { ((*bs).allocate_pages)(ALLOC_ANY, MT_LOADER_DATA, alloc_bytes / 4096, &mut buf) };
        check!(s == EFI_SUCCESS, "memory-map buffer allocation refused");
        check!(
            buf + (size as u64) <= IDENTITY_LIMIT,
            "memory-map buffer outside the identity window"
        );
        buf
    };

    key = 0;
    status = unsafe {
        ((*bs).get_memory_map)(
            &mut size,
            buf_base as *mut MemoryDescriptor,
            &mut key,
            &mut desc_size,
            &mut desc_version,
        )
    };
    check!(
        status == EFI_SUCCESS,
        "GetMemoryMap within fresh headroom failed -- refusing rather than guessing"
    );

    let count = size / desc_size;
    let mut descriptors = Vec::with_capacity(count);
    for i in 0..count {
        unsafe {
            // The firmware's stride is ITS descriptor size (EDK2 uses
            // 48 bytes, more than the spec's 40-byte minimum), not
            // sizeof(MemoryDescriptor) -- striding by our struct read
            // shifted garbage from the second entry on.
            descriptors.push(*((buf_base as u64 + (i as u64) * desc_size as u64) as *const MemoryDescriptor));
        }
    }
    MemoryMap {
        descriptors,
        buf_base,
        buf_bytes: size,
        buf_capacity: alloc_bytes,
        key,
        desc_size,
    }
}

// ---- ExitBootServices ----

/// One fresh-key GetMemoryMap immediately followed by
/// ExitBootServices -- nothing between them, per spec. On the
/// stale-key answer (map grew), retries once with another fresh pair;
/// a second failure is a typed halt.
pub fn exit_boot_services(map: &mut MemoryMap) {
    raw_uart_write("\r\n[s] entering EBS\r\n");
    let st = unsafe { &*SYSTEM_TABLE };
    let bs = st.boot_services;
    for attempt in 0..2 {
        // Re-fetch fully. If the map grew beyond the acquired buffer
        // (the stub's own pool allocations can do this), take ONE
        // bounded grow pass into a fresh buffer; a second
        // BUFFER_TOO_SMALL is a typed halt, not a retry loop.
        let mut size = map.buf_capacity;
        let mut key: u64 = 0;
        let mut desc_size = map.desc_size;
        let mut desc_version: u32 = 0;
        let mut buf = map.buf_base;
        let mut q = unsafe {
            ((*bs).get_memory_map)(
                &mut size,
                buf as *mut MemoryDescriptor,
                &mut key,
                &mut desc_size,
                &mut desc_version,
            )
        };
        if q == EFI_BUFFER_TOO_SMALL {
            let grow_bytes = (size + HEADROOM_ENTRIES * desc_size + 4095) & !4095usize;
            let mut fresh: u64 = 0;
            let a = unsafe { ((*bs).allocate_pages)(ALLOC_ANY, MT_LOADER_DATA, grow_bytes / 4096, &mut fresh) };
            check!(a == EFI_SUCCESS, "re-fetch grow allocation refused");
            size = grow_bytes;
            buf = fresh;
            q = unsafe {
                ((*bs).get_memory_map)(
                    &mut size,
                    buf as *mut MemoryDescriptor,
                    &mut key,
                    &mut desc_size,
                    &mut desc_version,
                )
            };
        }
        if q == EFI_SUCCESS {
            map.key = key;
        } else {
            conout("pre-EBS GetMemoryMap status = ");
            conout_hex(q);
            conout(" size=");
            conout_hex(size as u64);
            conout("\r\n\r\nyyrqis ubld: pre-EBS GetMemoryMap failed\r\n");
            die("pre-EBS GetMemoryMap failed -- refusing");
        }
        let s = unsafe { ((*bs).exit_boot_services)(IMAGE_HANDLE, map.key) };
        if s == EFI_SUCCESS {
            raw_uart_write("[s] EBS ok\r\n");
            return;
        }
        check!(
            s == EFI_INVALID_PARAMETER && attempt == 0,
            "ExitBootServices refused twice -- firmware surface is not trustworthy"
        );
    }
}

// ---- ACPI RSDP from the configuration table ----

/// Returns (rsdp bytes, revision) -- a byte-exact reference the caller
/// copies into the synthesized MBI BEFORE ExitBootServices, after
/// which firmware memory is untouchable. ACPI 2.0 tables preferred;
/// the 1.0 GUID is the fallback.
pub fn find_acpi_rsdp() -> Option<(&'static [u8], u8)> {
    let st = unsafe { &*SYSTEM_TABLE };
    let count = st.number_of_table_entries;
    let table = st.configuration_table;
    check!(!table.is_null() || count == 0, "configuration table null with nonzero count");
    let mut v2: Option<(&'static [u8], u8)> = None;
    let mut v1: Option<(&'static [u8], u8)> = None;
    for i in 0..count {
        unsafe {
            let e = &*table.add(i);
            if e.vendor_guid == ACPI_20_TABLE_GUID && v2.is_none() {
                // Copy only after the signature check: the config table
                // carries the RSDP itself, so a wrong GUID match must
                // never synthesize an RSDP tag out of foreign bytes.
                let p = e.vendor_table as *const u8;
                let sig = core::slice::from_raw_parts(p, 8);
                if sig == &b"RSD PTR "[..] && *p.add(15) >= 2 {
                    v2 = Some((core::slice::from_raw_parts(p, 36), 2));
                }
            } else if e.vendor_guid == ACPI_10_TABLE_GUID && v1.is_none() {
                let p = e.vendor_table as *const u8;
                let sig = core::slice::from_raw_parts(p, 8);
                if sig == &b"RSD PTR "[..] {
                    v1 = Some((core::slice::from_raw_parts(p, 20), 1));
                }
            }
        }
    }
    v2.or(v1)
}

// ---- identity page tables ----

/// Builds the Phase-1-class identity mapping the kernel expects on the
/// legacy path: one PML4E, one PDPT entry, and 512 2MiB pages
/// covering the first 1GiB (present + writable, huge). The three
/// backing pages were allocated contiguously by `alloc_identity_pages`.
pub fn build_identity_tables(pg_phys: u64) {
    let pml4 = pg_phys as *mut u64;
    let pdpt = (pg_phys + 0x1000) as *mut u64;
    let pd = (pg_phys + 0x2000) as *mut u64;
    unsafe {
        // PML4[0] -> PDPT; PDPT[0] -> PD.
        *pml4 = (pdpt as u64) | 0b11;
        *pdpt = (pd as u64) | 0b11;
        // PD[0..512] -> 2MiB pages (present | writable | PS).
        for i in 0..512usize {
            *pd.add(i) = ((i as u64) * 0x20_0000) | 0b1000_0011;
        }
    }
}

// ---- watchdog ----

pub fn disarm_watchdog() {
    let st = unsafe { &*SYSTEM_TABLE };
    let bs = st.boot_services;
    let s = unsafe { ((*bs).set_watchdog_timer)(0, 0, 0) };
    check!(s == EFI_SUCCESS, "watchdog disarm refused");
}

// ---- the db enrollment step (the ADR-0014 chain's firmware-facing
// half): the anchored, boot-verified root becomes a UEFI db entry ----
//
// The stub's own carrier walk already gates the boot; THIS step turns
// the embedded anchor into a fact the FIRMWARE holds too, so the same
// anchored root is a database fact (UEFI 2.10 section 32.4.1: while
// the platform certifies no PK -- SetupMode -- db updates require no
// authentication at all). Spec-gated legality check first (a PK SET
// means every db update needs a KeK-signed AUTH package this stub
// deliberately does not fabricate -- the step records the fact and
// SKIPS, never votes the boot either way); the write itself is
// REPLACE by spec, so the current db (when one exists) is read back
// and APPENDED to, never clobbered; and a successful write is
// READ BACK to prove the firmware really carries it. Every refusal
// is a named, non-fatal fact: the boot's enforcement path stays the
// stub's own carrier walk either way.

/// True when `needle` sits anywhere inside `hay` (the "is our entry
/// already in the db" scan over the read-back blob).
fn contains_bytes(hay: &[u8], needle: &[u8]) -> bool {
    !needle.is_empty()
        && hay.len() >= needle.len()
        && hay.windows(needle.len()).any(|w| w == needle)
}

fn push_le_u32(out: &mut alloc::vec::Vec<u8>, v: u32) {
    out.extend_from_slice(&v.to_le_bytes());
}

/// The anchor as ONE EFI_SIGNATURE_LIST entry (UEFI 2.10 section
/// 31.4.1), exactly the bytes `dbx`-removal tools and NVRAM dumps
/// expect to find:
///   EFI_SIGNATURE_LIST {
///     EFI_GUID SignatureType = EFI_CERT_X509_GUID;   // 16 bytes
///     UINT32 SignatureListSize;                      // 0x1c + 16 + len
///     UINT32 SignatureHeaderSize = 0;                // X.509 carries none
///     UINT32 SignatureSize;                          // 16 + len
///   }
///   EFI_SIGNATURE_DATA {
///     EFI_GUID SignatureOwner;                       // this stub's GUID
///     UINT8 SignatureData[] = the anchor DER
///   }
/// All integers little-endian u32. The list is byte-stable across
/// boots of the same anchor, so the containment scan below is exact.
fn x509_signature_list(anchor_cert_der: &[u8]) -> alloc::vec::Vec<u8> {
    let sig_size = 16u32 + anchor_cert_der.len() as u32;
    let list_size = 0x1cu32 + sig_size;
    let mut out: alloc::vec::Vec<u8> = alloc::vec::Vec::with_capacity(list_size as usize);
    out.extend_from_slice(&CERT_X509_GUID.bytes());
    push_le_u32(&mut out, list_size);
    push_le_u32(&mut out, 0);
    push_le_u32(&mut out, sig_size);
    out.extend_from_slice(&VARCHK_VENDOR_GUID.bytes());
    out.extend_from_slice(anchor_cert_der);
    out
}

/// Reads ONE whole variable onto the heap: the query pass first (the
/// same BUFFER_TOO_SMALL contract the round-trip check proves every
/// boot), then a data pass sized by the query's answer. A query
/// claiming more than 1 MiB is refused as a lying surface, not a
/// bigger database. (Status, bytes); NOT_FOUND returns empty bytes.
fn get_variable_bytes(
    name_ascii: &[u8],
    guid: &Guid,
    rs: *const RuntimeServices,
) -> (Status, alloc::vec::Vec<u8>) {
    let mut attrs: u32 = 0;
    let mut size: usize = 0;
    let q = unsafe {
        ((*rs).get_variable)(
            wide_name(name_ascii).as_ptr(),
            guid,
            &mut attrs,
            &mut size,
            core::ptr::null_mut(),
        )
    };
    if q == EFI_NOT_FOUND {
        return (q, alloc::vec::Vec::new());
    }
    if q != EFI_BUFFER_TOO_SMALL || size == 0 || size > (1 << 20) {
        return (q, alloc::vec::Vec::new());
    }
    let mut buf: alloc::vec::Vec<u8> = alloc::vec::Vec::new();
    buf.resize(size, 0u8);
    let s = unsafe {
        ((*rs).get_variable)(
            wide_name(name_ascii).as_ptr(),
            guid,
            &mut attrs,
            &mut size,
            buf.as_mut_ptr() as *mut c_void,
        )
    };
    if s != EFI_SUCCESS {
        return (s, alloc::vec::Vec::new());
    }
    buf.truncate(size);
    (s, buf)
}

/// Enrolls the embedded anchor certificate into the firmware's `db`
/// (EFI_IMAGE_SECURITY_DATABASE_GUID): non-fatal by design, every
/// step a named transcript fact, the boot's own carrier gate
/// untouched either way.
/// TEMPORARY bisection probes: write three differently-routed
/// variables BEFORE the real db attempt, name every status, so the
/// refusing STAGE of the firmware's variable service becomes a fact
/// instead of a guess. Disabled (no-op) now that its work is done:
/// it proved every TBA+auth2 write in THIS OVMF image (the plain
/// non-secboot build bundled in uefi/tools) returns
/// EFI_INVALID_PARAMETER, and GetVariable(SecureBoot/SetupMode/
/// VendorKeys) all return EFI_NOT_FOUND -- the image hosts NO
/// authenticated-variable service at all, so a db write cannot
/// succeed here and the enrollment step carries that fact (not
/// force it) by treating refusal as a non-fatal capability note.
/// The .ms sibling image compiles the secure-boot enforcement in,
/// but then refuses to even LOAd an unsigned stub (BdsDxe "Access
/// Denied"), so no bundled image can both run the stub and host
/// the service. Flip this const to re-bisect against any firmware
/// image that does.
const DB_ENROLL_PROBE: bool = false;

fn db_enroll_probe(rs: *const RuntimeServices, list: &[u8]) {
    if !DB_ENROLL_PROBE {
        return;
    }
    // auth2 envelope with an EMPTY pkcs7 win-certificate (the same
    // minimal descriptor the real enrollment builds).
    let mut auth2: alloc::vec::Vec<u8> = alloc::vec::Vec::with_capacity(40);
    let mut now = EfiTime::zero();
    let gts = unsafe { ((*rs).get_time)(&mut now as *mut EfiTime) };
    if gts != EFI_SUCCESS {
        conout("  db-probe: GetTime refused\r\n");
        return;
    }
    auth2.extend_from_slice(&now.year.to_le_bytes());
    auth2.push(now.month);
    auth2.push(now.day);
    auth2.push(now.hour);
    auth2.push(now.minute);
    auth2.push(now.second);
    auth2.push(0);
    auth2.extend_from_slice(&0u32.to_le_bytes());
    auth2.extend_from_slice(&0u16.to_le_bytes());
    auth2.push(0);
    auth2.push(0);
    auth2.extend_from_slice(&24u32.to_le_bytes());
    auth2.extend_from_slice(&0x0200u16.to_le_bytes());
    auth2.extend_from_slice(&0x0ef1u16.to_le_bytes());
    auth2.extend_from_slice(&CERT_TYPE_PKCS7_GUID.bytes());

    // P1: a PLAIN NV|BS|RT write of a non-auth name (our own vendor
    // GUID): proves or refutes the plain-NV path in isolation.
    let p1 = unsafe {
        ((*rs).set_variable)(
            wide_name(b"NyrqisProbe1").as_ptr(),
            &VARCHK_VENDOR_GUID,
            VARIABLE_ATTRIBUTE_NONVOLATILE | VARIABLE_ATTRIBUTE_BOOTSERVICE | VARIABLE_ATTRIBUTE_RUNTIME,
            4,
            b"\\x5a\\xa5\\x3c\\xc3".as_ptr() as *const c_void,
        )
    };
    conout("  db-probe: P1 plain NV|BS|RT own-guid = ");
    conout_hex(p1);
    conout("\r\n");

    // P2: TBA + the SAME auth2 envelope, under OUR vendor GUID: tests
    // the generic time-based-auth routing without the db-name's
    // signature-list format checks (any payload is fine there).
    let mut p2 = auth2.clone();
    p2.extend_from_slice(&[0x5a, 0xa5, 0x3c, 0xc3]);
    let p2s = unsafe {
        ((*rs).set_variable)(
            wide_name(b"NyrqisProbe2").as_ptr(),
            &VARCHK_VENDOR_GUID,
            VARIABLE_ATTRIBUTE_NONVOLATILE
                | VARIABLE_ATTRIBUTE_BOOTSERVICE
                | VARIABLE_ATTRIBUTE_RUNTIME
                | VARIABLE_ATTRIBUTE_TIME_BASED_AUTHENTICATED,
            p2.len(),
            p2.as_ptr() as *const c_void,
        )
    };
    conout("  db-probe: P2 TBA+auth2 own-guid = ");
    conout_hex(p2s);
    conout("\r\n");

    // P3: dcbt with the SAME valid X.509 list: the dbt and db names
    // route through the IDENTICAL auth code (ProcessVarWithPk ->
    // CheckSignatureListFormat), so a dbt result bisects "db name
    // specifically" from "auth2/list format".
    let mut p3 = auth2.clone();
    p3.extend_from_slice(list);
    let p3s = unsafe {
        ((*rs).set_variable)(
            wide_name(b"dbt").as_ptr(),
            &IMAGE_SECURITY_DATABASE_GUID,
            VARIABLE_ATTRIBUTE_NONVOLATILE
                | VARIABLE_ATTRIBUTE_BOOTSERVICE
                | VARIABLE_ATTRIBUTE_RUNTIME
                | VARIABLE_ATTRIBUTE_TIME_BASED_AUTHENTICATED,
            p3.len(),
            p3.as_ptr() as *const c_void,
        )
    };
    conout("  db-probe: P3 dbt same-list = ");
    conout_hex(p3s);
    conout("\r\n");

    // read back probe variables that succeeded, as facts.
    let (s, v) = get_variable_bytes(b"NyrqisProbe1", &VARCHK_VENDOR_GUID, rs);
    conout("  db-probe: readback Probe1 = ");
    conout_hex(s);
    conout(" size=");
    conout_hex(v.len() as u64);
    conout("\r\n");

    // Firmware capability facts: if the secure-boot feature is not
    // hosted in this OVMF build, SecureBoot/SetupMode are absent and
    // every TBA write refuses -- a capability absence, not a packet
    // shape question.
    for nm in [&b"SecureBoot"[..], &b"SetupMode"[..], &b"VendorKeys"[..]] {
        let (qsb, vb) = get_variable_bytes(nm, &GLOBAL_VARIABLE_GUID, rs);
        conout("  db-probe: GetVariable(");
        conout(core::str::from_utf8(nm).expect("ascii name"));
        conout(") = ");
        conout_hex(qsb);
        conout(" val=");
        if vb.is_empty() {
            conout("<none>");
        } else {
            conout_hex(vb[0] as u64);
        }
        conout("\r\n");
    }
}

pub fn enroll_trusted_root_in_db(anchor_cert_der: &[u8]) {
    let st = unsafe { &*SYSTEM_TABLE };
    let rs = st.runtime_services;

    // Legality gate: a PK SET means authenticated-var mode (every db
    // update needs a KeK-signed AUTH package); only a PK ABSENT
    // (SetupMode) makes the raw write legal. One unauthenticated
    // query pass answers the question.
    let mut attrs: u32 = 0;
    let mut size: usize = 0;
    let pk_status = unsafe {
        ((*rs).get_variable)(
            wide_name(b"PK").as_ptr(),
            &GLOBAL_VARIABLE_GUID,
            &mut attrs,
            &mut size,
            core::ptr::null_mut(),
        )
    };
    if pk_status != EFI_NOT_FOUND {
        conout("  db-enroll: PK variable PRESENT (not SetupMode) -- db updates would need a KeK-signed AUTH package the stub does not fabricate; skipping (named fact)\r\n");
        return;
    }
    conout("  db-enroll: PK absent -- SetupMode per UEFI 2.10 section 32.4.1 applies: the auth2-headed db write is legal, the PKCS7 signature content is simply not verified (the write itself may still be refused when this firmware image hosts no authenticated-variable service at all; that refusal is named below and is a capability fact, not a packet error)\r\n");

    let list = x509_signature_list(anchor_cert_der);

    // The firmware's own clock datums the write: the auth2 header's
    // EFI_TIME TimeStamp must be a real datum (a failed GetTime or an
    // implausible date is a named, non-fatal skip -- an all-zero or
    // fabricated timestamp would be the stub inventing an ordering
    // fact).
    let mut now = EfiTime::zero();
    let gts = unsafe { ((*rs).get_time)(&mut now as *mut EfiTime) };
    if gts != EFI_SUCCESS {
        conout("  db-enroll: GetTime = ");
        conout_hex(gts);
        conout(" -- no live timestamp to datum the write with, skipping (named fact)\r\n");
        return;
    }
    if now.year < 2000 || now.year > 2999 || now.month < 1 || now.month > 12 || now.day < 1 || now.day > 31 {
        conout("  db-enroll: GetTime returned an implausible date -- refusing to stamp the auth2 header with it, skipping (named fact)\r\n");
        return;
    }

    // The auth2 ENVELOPE (EFI_VARIABLE_AUTHENTICATION_2, UEFI 2.10
    // App.; edk2 SecurityPkg AuthService.c ProcessVarWithKek): edk2
    // routes EVERY db/dbx/dbt update through the time-based path and
    // returns EFI_INVALID_PARAMETER unless the write carries the
    // TIME_BASED attribute bit AND a well-formed auth2 descriptor.
    // In SetupMode the PKCS7 signature CONTENT of that descriptor is
    // deliberately not verified (UEFI 2.10 section 32.4.1 "shall not
    // require authentication" in edk2's own implementation: the
    // timestamp is taken, the payload is format-checked, the signature
    // is skipped) -- so the honest minimal descriptor is a live
    // firmware timestamp plus an EMPTY pkcs7-typed win-certificate:
    // 24 = 8-byte WIN_CERTIFICATE header + a 16-byte CertType GUID,
    // zero CertifiedData bytes.
    let mut auth2: alloc::vec::Vec<u8> = alloc::vec::Vec::with_capacity(40 + list.len());
    // EFI_TIME TimeStamp (exactly the spec's 16-byte layout), byte-style
    // mirrored from edk2's own EnrollDefaultKeys application: date+time
    // live from GetTime, and Pad1/Nanosecond/TimeZone/Daylight/Pad2
    // ZEROED (sub-second and zone data are not datums of a variable
    // write ordering, so the descriptor carries only what it needs --
    // the exact practice of the reference enrollment app).
    auth2.extend_from_slice(&now.year.to_le_bytes());
    auth2.push(now.month);
    auth2.push(now.day);
    auth2.push(now.hour);
    auth2.push(now.minute);
    auth2.push(now.second);
    auth2.push(0);            // Pad1
    auth2.extend_from_slice(&0u32.to_le_bytes()); // Nanosecond
    auth2.extend_from_slice(&0u16.to_le_bytes()); // TimeZone
    auth2.push(0);            // Daylight
    auth2.push(0);            // Pad2
    // WIN_CERTIFICATE Hdr: dwLength = 8 header + 16 CertType GUID; the
    // empty CertifiedData rides after the GUID.
    auth2.extend_from_slice(&24u32.to_le_bytes());
    auth2.extend_from_slice(&0x0200u16.to_le_bytes());
    auth2.extend_from_slice(&0x0ef1u16.to_le_bytes());
    auth2.extend_from_slice(&CERT_TYPE_PKCS7_GUID.bytes());

    // SetVariable REPLACES: an existing db must be read back and
    // APPENDED to, never clobbered (other signers' entries are not
    // this stub's to remove). In both arms the STORED value is the
    // payload after the 40-byte auth2 descriptor.
    let (q, current) = get_variable_bytes(b"db", &IMAGE_SECURITY_DATABASE_GUID, rs);
    let payload: alloc::vec::Vec<u8> = match q {
        EFI_NOT_FOUND => list.clone(),
        EFI_SUCCESS => {
            if contains_bytes(&current, &list) {
                conout("  db-enroll: the db already carries this stub's anchor entry (idempotent, nothing written)\r\n");
                return;
            }
            conout("  db-enroll: appending the anchor entry after the db's existing ");
            conout_hex(current.len() as u64);
            conout(" bytes (SetVariable replaces, so the old entries ride along)\r\n");
            let mut combined = current;
            combined.extend_from_slice(&list);
            combined
        }
        status => {
            conout("  db-enroll: GetVariable(db) = ");
            conout_hex(status);
            conout(" -- unrecognizable, skipping (named fact)\r\n");
            return;
        }
    };
    let mut new_db = auth2;
    new_db.extend_from_slice(&payload);

    // TEMP probes first (bisection; disabled by flipping DB_ENROLL_PROBE).
    db_enroll_probe(rs, &list);

    let set_status = unsafe {
        ((*rs).set_variable)(
            wide_name(b"db").as_ptr(),
            &IMAGE_SECURITY_DATABASE_GUID,
            VARIABLE_ATTRIBUTE_NONVOLATILE
                | VARIABLE_ATTRIBUTE_BOOTSERVICE
                | VARIABLE_ATTRIBUTE_RUNTIME
                | VARIABLE_ATTRIBUTE_TIME_BASED_AUTHENTICATED,
            new_db.len(),
            new_db.as_ptr() as *const c_void,
        )
    };
    if set_status != EFI_SUCCESS {
        conout("  db-enroll: SetVariable(db, NV|BS|RT|TBA, ");
        conout_hex(new_db.len() as u64);
        conout(" bytes) = ");
        conout_hex(set_status);
        conout(" -- REFUSED by the firmware, skipping (named fact); the stub's own carrier walk enforces the root regardless\r\n");
        return;
    }
    conout("  db-enroll: SetVariable(db) accepted ");
    conout_hex(new_db.len() as u64);
    conout(" bytes (auth2-headed) -- EFI_SUCCESS\r\n");

    // A write is only proven by its read-back: the same query+data
    // passes, and the anchor's exact list bytes must sit in the blob.
    let (rbs, readback) = get_variable_bytes(b"db", &IMAGE_SECURITY_DATABASE_GUID, rs);
    if rbs == EFI_SUCCESS && contains_bytes(&readback, &list) {
        conout("  db-enroll: read-back VERIFIED -- the firmware db carries the anchor now; a SECOND boot records 'already'\r\n");
    } else {
        conout("  db-enroll: read-back ");
        conout_hex(rbs);
        conout(" -- the write could not be confirmed from the firmware, carried as a fact only\r\n");
    }
}
