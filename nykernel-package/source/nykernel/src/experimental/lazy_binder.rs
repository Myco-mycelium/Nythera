//! The SHARED lazy-bind kernel service: the kernel side of trap-based
//! first-use PLT binding, held here rather than in any one demo so
//! that EVERY lazy-armed object loaded in a boot binds through exactly
//! ONE IDT vector (`elf::LAZY_BIND_VECTOR`, 0x88) -- the difference
//! between "a demo hardcoded its own dependent" and "the kernel runs
//! a lazy-binding service".
//!
//! Each `elf::load` that arms lazy PLT entries emits its stubs
//! (`push idx; int 0x88; add rsp,8; jmp *slot`) into a trampoline page
//! the caller picked, and the loader publishes the first stub's
//! address in the JUMP_SLOT. Before user execution begins, the
//! demo/provider side registers THAT trampoline page here together
//! with the object's parsed dynamic tables, its load bias, and its
//! resolver scope. On trap, WHICH object called is identified not by
//! a demo-specific static but by the trampoline page the interrupted
//! PC sits on (`frame.instruction_pointer & !0xFFF`) -- one service,
//! many dependents, no per-demo plumbing.
//!
//! Resolution goes through `elf::resolve_lazy_entry` -- the SAME
//! self-dynsym-first, registry-order, version+SONAME-checked rules as
//! the load-time pass -- and the resolved address is PATCHED into the
//! real GOT slot (located via `elf::lazy_slot_for`), after which the
//! interrupted stub's tail jumps through the now-resolved slot and
//! every later call takes the direct path. Binds are counted PER
//! OBJECT so the laziness claims (bound exactly once across an
//! arbitrary number of calls) stay checkable per object while the
//! service-level proof ("multiple independents through one vector")
//! remains observably a service fact.

/// Registration/lookup error surface. The service name is plain and
/// panics with the precise diagnosis instead: this is kernel-side
/// machinery whose own contract is "armed before first use" -- a
/// violation is a demo wiring bug, not user input.
use core::arch::naked_asm;
use core::sync::atomic::{AtomicU32, AtomicU64, Ordering};
use x86_64::structures::idt::InterruptStackFrame;

use crate::experimental::elf;
use crate::experimental::elf::{FatDynTables, SymbolResolver};

struct LazyEntry {
    /// The trampoline page `elf::load` emitted this object's stubs
    /// into -- the object's dispatch identity ON THE TRAP-STUB PATH
    /// (`0`, reserved, for own-`.plt` objects, which have NO
    /// kernel-emitted trampoline and dispatch by their link-map
    /// cookie instead).
    page: u64,
    /// The per-object link-map id this object's `.got.plt`[1] carries
    /// when the GNU OWN-`.plt` protocol armed it (`elf::arm_own_plt
    /// -> allocate_lm_id`); `0` for trap-stub-armed objects.
    lm: u64,
    tables: FatDynTables,
    bias: u64,
    resolver: &'static dyn SymbolResolver,
    binds: AtomicU32,
}

static mut ENTRIES: Option<alloc::vec::Vec<LazyEntry>> = None;
/// An ATOMIC, not a `static mut` behind `&raw` gymnastics: fetch-add
/// IS the interior mutability (an `AtomicU64` is `Sync`), so a plain
/// `static` is both the at-cause form and the automatically
/// race-free one.
static NEXT_LM_ID: AtomicU64 = AtomicU64::new(0);

/// Assigns the link-map id an own-`.plt`-armed object's `.got.plt`[1]
/// carries: strictly monotonically increasing from 1 (0 is the
/// reserved none-value), so a stale or fabricated cookie can never
/// collide with a live object's identity.
///
/// # Safety
/// Must be called only while the loader arms objects (single-boot,
/// single-load-flow, no user execution interleaved) -- the same
/// serialization contract the ENTRIES registry itself relies on.
pub unsafe fn allocate_lm_id() -> u64 {
    NEXT_LM_ID.fetch_add(1, Ordering::SeqCst) + 1
}

/// The service's typed refusals. The trap handler itself still PANICS
/// on any of them (arm-before-first-use is a kernel wiring contract,
/// not user input -- a trap on an unregistered page must never be
/// "resolved" by guessing); the typed surface exists so the refusal
/// PATH can itself be a checked boot fact via
/// [`probe_unregistered`], instead of trusted by reading code.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LazyBindRefusal {
    /// Trap before anything was ever registered.
    ServiceEmpty,
    /// The interrupted PC's trampoline page belongs to no registered
    /// object.
    UnregisteredTrampoline { ip_page: u64 },
    /// The `.got.plt`[1] link-map cookie read off the intercepted GNU
    /// spill belongs to no registered object.
    UnregisteredLinkMap { lm: u64 },
}

/// Registers a lazy-armed object for first-use binding service. Must
/// run BEFORE the object's user code executes: the first PLT call can
/// trap within microseconds of entry, and an unregistered page is a
/// loud panic (below), never a silent mis-resolve.
///
/// # Safety
/// `tables` must be the parsed dynamic tables of the object loaded at
/// `bias` in the CURRENTLY-active address space, its trampoline page
/// must be the page its stubs live on, and `resolver` must outlive
/// the object's execution (demo statics -- 'static references -- are
/// the provided pattern).
pub unsafe fn register(
    page: u64,
    tables: FatDynTables,
    bias: u64,
    resolver: &'static dyn SymbolResolver,
) {
    let vec = (*&raw mut ENTRIES).get_or_insert_with(alloc::vec::Vec::new);
    vec.push(LazyEntry {
        page,
        lm: 0, // trap-stub identity: no link-map cookie
        tables,
        bias,
        resolver,
        binds: AtomicU32::new(0),
    });
}

/// Registers an OWN-`.plt` (GNU protocol) lazy-armed object for
/// first-use binding service, keyed by the link-map id the loader
/// installed into its `.got.plt`[1]. Must run BEFORE the object's
/// user code executes: an unregistered cookie is a loud panic (see
/// the `plt_service` dispatch), never a silent mis-resolve.
///
/// # Safety
/// `tables` must be the parsed dynamic tables of the object loaded at
/// `bias` in the CURRENTLY-active address space; `lm_id` must be the
/// EXACT id `elf::load` reported in `LoadedElf::own_plt` (the loader
/// wrote it into GOT[1] during arming); `resolver` must outlive the
/// object's execution ('static demo statics are the pattern).
pub unsafe fn register_lm(
    lm_id: u64,
    tables: FatDynTables,
    bias: u64,
    resolver: &'static dyn SymbolResolver,
) {
    assert!(lm_id != 0, "link-map id 0 is reserved (never handed out)");
    let vec = (*&raw mut ENTRIES).get_or_insert_with(alloc::vec::Vec::new);
    vec.push(LazyEntry {
        page: 0, // own-.plt identity: no kernel-emitted trampoline page
        lm: lm_id,
        tables,
        bias,
        resolver,
        binds: AtomicU32::new(0),
    });
}

/// How many first-use binds this object has taken (0 if it never
/// registered): the demo's per-object laziness fact.
pub fn entry_count(page: u64) -> u32 {
    // SAFETY: read-only walk of the registry; register() completed
    // before user execution in every boot path (see `start` arcs).
    unsafe {
        match (*&raw const ENTRIES).as_ref() {
            Some(v) => v
                .iter()
                .find(|e| e.page == page)
                .map(|e| e.binds.load(Ordering::SeqCst))
                .unwrap_or(0),
            None => 0,
        }
    }
}

pub fn registered_count() -> usize {
    unsafe { (*&raw const ENTRIES).as_ref().map(|v| v.len()).unwrap_or(0) }
}

/// Sum of binds across every registered object: the service-level
/// fact ("multiple independents through one vector").
pub fn total_count() -> u32 {
    unsafe { (*&raw const ENTRIES).as_ref().map(|v| v.iter().map(|e| e.binds.load(Ordering::SeqCst)).sum()).unwrap_or(0) }
}

/// How many first-use binds an OWN-`.plt` object (keyed by its
/// link-map id) has taken: the GNU-protocol path's per-object fact.
pub fn entry_count_by_lm(lm_id: u64) -> u32 {
    // SAFETY: read-only walk of the registry; register_lm() completed
    // before user execution in every boot path.
    unsafe {
        match (*&raw const ENTRIES).as_ref() {
            Some(v) => v
                .iter()
                .find(|e| e.lm == lm_id && lm_id != 0)
                .map(|e| e.binds.load(Ordering::SeqCst))
                .unwrap_or(0),
            None => 0,
        }
    }
}

/// The service's identification decision for the GNU OWN-`.plt`
/// protocol: WHICH registered object (if any) owns the link-map
/// cookie. Shared by `plt_service` and the negative-control probe so
/// the refusal is ONE code path, not two lookups that could drift.
///
/// # Safety
/// Same registry alignment as `entry_count` (registration completed
/// before every caller).
unsafe fn identify_by_lm(lm: u64) -> Result<&'static LazyEntry, LazyBindRefusal> {
    // SAFETY: read-only walk of the registry (see the service's note).
    unsafe {
        let Some(entries) = (*&raw const ENTRIES).as_ref() else {
            return Err(LazyBindRefusal::ServiceEmpty);
        };
        entries
            .iter()
            .find(|e| e.lm == lm && lm != 0)
            .ok_or(LazyBindRefusal::UnregisteredLinkMap { lm })
    }
}

/// The OWN-`.plt` NEGATIVE-CONTROL probe: what the service would
/// answer for `lm`, expressed as a typed result instead of a panic
/// (the GNU-path twin of `probe_unregistered`).
///
/// # Safety
/// Registry alignment as `identify_by_lm`; the probe never RESOLVES
/// (no spill reads, no slot writes).
pub unsafe fn probe_unregistered_lm(lm: u64) -> Result<(), LazyBindRefusal> {
    unsafe { match identify_by_lm(lm) {
        Ok(_) => Ok(()), // a registered id: no refusal to demonstrate
        Err(e) => Err(e),
    }}
}

/// The IDT `0x88` handler body: identify the object by which
/// trampoline page owns the interrupted PC, read the stub-pushed
/// entry index off the user stack, resolve through that object's own
/// resolver scope, and patch the real GOT slot. RETURNS to the
/// interrupted stub (never chains; the stub's tail jumps through the
/// now-resolved slot).
///
/// # Safety
/// Called from the ring-3-privileged IDT gate; the interrupted user
/// stack holds the stub's pushed index exactly one slot down from the
/// trapped frame (stub shape: `push idx; int 0x88`).
/// The service's identification decision: WHICH registered object (if
/// any) owns `page`. Shared by the trap handler and the negative-
/// control probe so the refusal is ONE code path, not two lookups
/// that could drift.
///
/// # Safety
/// Same registry alignment as `service`/`entry_count` (registration
/// completed before every caller).
unsafe fn identify(page: u64) -> Result<&'static LazyEntry, LazyBindRefusal> {
    // SAFETY: read-only walk of the registry (see the service's note).
    unsafe {
        let Some(entries) = (*&raw const ENTRIES).as_ref() else {
            return Err(LazyBindRefusal::ServiceEmpty);
        };
        entries
            .iter()
            .find(|e| e.page == page)
            .ok_or(LazyBindRefusal::UnregisteredTrampoline { ip_page: page })
    }
}

/// The NEGATIVE-CONTROL probe: what the service would answer for
/// `page`, expressed as a typed RESULT instead of a panic. The demo
/// checks a deliberately unregistered page against this at every boot
/// and pins that the refusal carries THAT page back, that the
/// registry size and bind totals did not move, and that the page has
/// zero binds — the `UnregisteredTrampoline` refusal (the one the
/// 0x88 handler panics on) is demonstrated, not assumed.
///
/// # Safety
/// Registry alignment as `identify`; `page` must be a page-aligned
/// trampoline address an (unregistered) object could own.
pub unsafe fn probe_unregistered(page: u64) -> Result<(), LazyBindRefusal> {
    // SAFETY: the probe never RESOLVES (no stub-frame reads, no slot
    // writes) -- it only consults the same identification the trap
    // handler consults, so a refusal here is exactly the handler's.
    unsafe { match identify(page) {
        Ok(_) => Ok(()), // a registered page: no refusal to demonstrate
        Err(e) => Err(e),
    }}
}

/// The OWN-`.plt` GNU-protocol service: the IDT `0x8B` handler body
/// for a first-use bind the REAL linker machinery routed here.
///
/// On entry the user stack carries the exact GNU `_dl_runtime_resolve`
/// spill, pushed by the object's OWN linker-emitted PLT:
///
/// ```text
///   [usp + 16]  the original CALLER's return address   (pushed by `call`)
///   [usp +  8]  the JUMP_SLOT relocation index         (pushed by PLT1 `push $idx`)
///   [usp +  0]  the link-map cookie                    (pushed by PLT0 `push GOT[1]`)
/// ```
///
/// The handler identifies the object by the cookie, resolves the
/// entry through the object's own tables + resolver scope (the same
/// rules as the trap-stub path), patches the slot in the REAL
/// `.got.plt`, and then -- instead of returning to the interrupted
/// thunk -- GUIDES the return the way GNU's `_dl_runtime_resolve`
/// ends: `rsp += 16` discards ONLY the two callee-side spilled words
/// (cookie + index), leaving the caller's return address on top, and
/// execution continues at the RESOLVED CALLEE, so the very first
/// call runs the function immediately and its own `ret` returns to
/// the caller. All user registers are preserved by the x86-interrupt
/// handler ABI, which is strictly more than GNU guarantees.
///
/// # Safety
/// Called from the ring-3-privileged 0x8B gate; the intercepted spill
/// shape must be as above (a real linker `.plt` produced it).
/// The raw ring-0 ENTRY for the OWN-`.plt` GNU resolver gate
/// (vector 0x8B): a `#[naked]` fn, exactly in the shape of
/// `ring3_scheduler::timer_entry`, because the GNU guided return
/// must leave ring 0 with EVERY user GPR restored -- the
/// `extern "x86-interrupt"` ABI restores user state only on its
/// normal-return epilogue, which the guided return (`iretq` from
/// inside the core) necessarily bypasses.
///
/// Sequence: push all 15 GPRs (same order as `timer_entry`: rax
/// first -- highest, adjacent to the CPU's hardware frame; r15 last)
/// into a blob, then hand the C-ABI core TWO pointers: the blob
/// (rdi) and the hardware frame just above it (rsi: RIP, CS,
/// RFLAGS, RSP, SS). Sub-8-byte pad for the 16-byte SysV call
/// boundary, strictly below the blob.
///
/// # Safety
/// Called by the CPU only, via the ring-3-privileged 0x8B gate.
/// Never returns: `plt_service_core` pops the blob back and performs
/// the guided `iretq` itself.
#[unsafe(naked)]
pub unsafe extern "C" fn plt_resolver_raw() {
    naked_asm!(
        // Same push order as timer_entry (rax first -- highest,
        // adjacent to the hardware frame -- r15 last -- lowest).
        "push rax",
        "push rbx",
        "push rcx",
        "push rdx",
        "push rsi",
        "push rdi",
        "push rbp",
        "push r8",
        "push r9",
        "push r10",
        "push r11",
        "push r12",
        "push r13",
        "push r14",
        "push r15",
        // rdi = the 15-qword GPR blob (arg0); rsi = the CPU hardware
        // frame immediately above it (arg1: RIP, CS, RFLAGS, RSP, SS).
        "mov rdi, rsp",
        "lea rsi, [rsp + 120]",
        "mov rax, rsp",
        "and eax, 8",
        "sub rsp, rax", // 16-byte call alignment, pad below the blob
        "call {core}",
        core = sym plt_service_core,
    );
}

/// The C-ABI core of the OWN-`.plt` GNU resolver (the `call` target
/// of `plt_resolver_raw`): reads the GNU spill off the user stack,
/// resolves + counts + patches EXACTLY as the trap-stub path does,
/// and then performs the GUIDED RETURN:
///
/// * the hardware frame's RIP slot := the resolved callee,
/// * its RSP slot := spill + 16 -- only the two callee-side spilled
///   words (the link-map cookie and the relocation index) are
///   dropped; the caller's return address stays on top, so the
///   callee's own `ret` returns to the caller,
/// * every saved GPR popped back (the user state the callee is
///   about to observe must be exactly the caller's),
/// * plain `iretq` through the rewritten hardware frame.
///
/// # Safety
/// `saved` = `plt_resolver_raw`'s blob pointer (15 GPRs: rax at the
/// blob's top, r15 at its bottom); `frame` = the hardware frame
/// immediately above the blob. Called only from `plt_resolver_raw`.
unsafe extern "C" fn plt_service_core(saved: *mut u64, frame: *mut u64) {
    // Spill reads: the trap switched the CPU stack, not CR3 -- the
    // user stack is addressable in the still-active space (the same
    // condition `service`'s spill read relies on). The hardware
    // frame's third slot is the interrupted RSP.
    let sp_user = unsafe { core::ptr::read_volatile(frame.add(3)) }; // RSP slot
    let lm = unsafe { core::ptr::read_volatile(sp_user as *const u64) };
    let idx = unsafe { core::ptr::read_volatile((sp_user + 8) as *const u64) } as usize;
    // SAFETY: registry alignment as every other registry walk (the
    // registration completed before user execution).
    let entry = match unsafe { identify_by_lm(lm) } {
        Ok(entry) => entry,
        Err(LazyBindRefusal::ServiceEmpty) => panic!(
            "own-plt resolve: the service has NO registered object (spill at {:#x})",
            sp_user
        ),
        Err(LazyBindRefusal::UnregisteredLinkMap { lm }) => panic!(
            "own-plt resolve: no registered lazy-armed object owns link-map cookie {:#x}",
            lm
        ),
        Err(other) => panic!("own-plt resolve: unexpected refusal shape: {:?}", other),
    };
    match elf::resolve_lazy_entry(&entry.tables, entry.bias, idx, Some(entry.resolver)) {
        Ok(addr) => {
            let slot = elf::lazy_slot_for(&entry.tables, entry.bias, idx)
                .unwrap_or_else(|e| panic!("own-plt lazy slot must compute: {:?}", e));
            // SAFETY: the slot's page was mapped writable by this
            // object's load and stays addressable in the active space.
            unsafe {
                core::ptr::write_volatile(slot as *mut u64, addr);
            }
            entry.binds.fetch_add(1, Ordering::SeqCst);

            // The GNU `_dl_runtime_resolve` gated return, written into
            // the LIVE hardware frame on the kernel stack...
            unsafe {
                core::ptr::write_volatile(frame, addr); // RIP slot: the resolved callee
                core::ptr::write_volatile(frame.add(3), sp_user + 16); // RSP slot: spill dropped
            }
            // ... then the interrupted context's ENTIRE register set
            // is popped back and the CPU consumes the rewritten frame.
            // (This asm never returns; the encircling Rust frame is a
            // discard by the very shape of this gate.)
            core::arch::asm!(
                "mov rsp, {saved}",
                "pop r15", "pop r14", "pop r13", "pop r12", "pop r11",
                "pop r10", "pop r9", "pop r8", "pop rbp", "pop rdi",
                "pop rsi", "pop rdx", "pop rcx", "pop rbx", "pop rax",
                "iretq",
                saved = in(reg) saved,
                options(noreturn)
            );
        }
        Err(e) => panic!("own-plt first-use resolution refused: {:?}", e),
    }
}

pub unsafe fn service(frame: InterruptStackFrame) {
    let ip = frame.instruction_pointer.as_u64();
    let page = ip & !0xFFF;
    // SAFETY: see module doc -- the registry is consulted read-only
    // here (binds count via atomics), after `register` completed.
    let entry = match unsafe { identify(page) } {
        Ok(entry) => entry,
        Err(LazyBindRefusal::ServiceEmpty) => panic!(
            "lazy-bind trap at ip {:#x}: the service has NO registered object",
            ip
        ),
        Err(LazyBindRefusal::UnregisteredTrampoline { ip_page }) => panic!(
            "lazy-bind trap at ip {:#x} (page {:#x}): no registered lazy-armed object owns this trampoline page",
            ip, ip_page
        ),
        // Unreachable through `identify` (a page-based lookup can only
        // produce the two refusals above), but the shared refusal type
        // makes the arm exist: a panic, never a silent fallthrough.
        Err(other) => panic!("lazy-bind trap: unexpected refusal shape: {:?}", other),
    };
    let idx =
        core::ptr::read_volatile(frame.stack_pointer.as_u64() as *const u64) as usize;
    match elf::resolve_lazy_entry(&entry.tables, entry.bias, idx, Some(entry.resolver)) {
        Ok(addr) => {
            let slot = elf::lazy_slot_for(&entry.tables, entry.bias, idx)
                .unwrap_or_else(|e| panic!("lazy slot address must compute: {:?}", e));
            // SAFETY: the slot's page was mapped writable by this
            // object's load and stays addressable in the active space.
            core::ptr::write_volatile(slot as *mut u64, addr);
            entry.binds.fetch_add(1, Ordering::SeqCst);
        }
        Err(e) => panic!("lazy-bind first-use resolution refused: {:?}", e),
    }
}
