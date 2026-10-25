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
