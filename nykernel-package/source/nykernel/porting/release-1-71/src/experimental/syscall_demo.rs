//! First real syscall boundary: two ring-3 processes (a "server" and a
//! "client", separate ELF programs, separate address spaces) talk through
//! the capability-checked IPC core (`experimental::capability`) by
//! trapping into the kernel -- user code never touches kernel data
//! structures, only passes handles and buffers.
//!
//! Pre-normative (NPS-002/003 Draft); NOT an ABI proposal. The ABI used:
//!
//! ```text
//! int 0x84        rax = number; rdi, rsi, rdx = args; result in rax
//!   0 YIELD
//!   1 SEND(handle, buf, len [, r10 = handle to transfer | -1 none, r8 = max rights to grant])
//!                                    -> 0 | -err
//!   2 RECV(owned_endpoint_idx, buf, buflen>=64) -> len>=0 | -err
//!       on success rdx = handle (in the receiver's table) of a capability
//!       delivered with the message, or -1 if none
//!       r10 = 1 makes RECV BLOCKING: the task sleeps (no EMPTY ever returned)
//!       until its endpoint has a message; the trap is restarted on wake-up
//!   3 EXIT
//!   4 SPAWN(name_ptr, name_len)                      (init only)
//!   5 BLK_READ(h, lba, buf) / 6 BLK_WRITE(h, lba, buf)   512-byte sectors via a block-device capability
//!   9 CALL(h, sbuf, slen, r10 = rbuf, r8 = rlen) -> reply len | -err   (send + park until REPLY;
//!       the server's RECV delivers a one-shot REPLY capability in rdx; server death -> -5)
//!  10 REPLY(h, buf, len) -> 0 | -err   (h must be a reply cap; consumed even on later reuse -> -2)
//!   7 SHM_CREATE() -> handle (READ|WRITE|GRANT) to a new kernel-zeroed 4 KiB region
//!   8 SHM_MAP(h, vaddr) -> 0 | -err; vaddr must be page-aligned inside the SHM window
//!       (shm::WINDOW_BASE, 16 pages); mapped writable only if the capability has WRITE
//! errors: -1 BadProcess -2 BadHandle -3 NoRight -4 Revoked -5 TargetDead
//!         -6 PayloadTooLarge -7 QueueFull -8 Widening -9 NotOwner
//!         -10 TableFull -11 EFAULT (bad user buffer) -12 EMPTY -13 ENOSYS
//! ```
//!
//! User buffers are validated by walking the caller's ACTUAL page tables
//! (`memory::paging::user_range_ok`): every page of the range must be
//! present and user-accessible (and writable for RECV destinations) at
//! every table level, else EFAULT before any copy. Tested with: a kernel
//! pointer, an unmapped user page, a range that starts in a mapped page
//! and runs off its end, and a user-mapped READ-ONLY page as a RECV target.
//!
//! Scheduling is cooperative round-robin over the two tasks; RECV never
//! blocks (returns EMPTY; user code yields). Same hand-written naked trap
//! entry / saved-context layout as `ring3_scheduler` (see its docs).

use alloc::vec::Vec;
use core::arch::asm;
use x86_64::structures::idt::InterruptDescriptorTable;
use x86_64::structures::paging::PageTableFlags;
use x86_64::{PrivilegeLevel, VirtAddr};

use crate::experimental::address_space::AddressSpace;
use crate::experimental::capability::{rights, CapSystem, IpcError, MAX_PAYLOAD};
use crate::experimental::{elf, usermode};
use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::memory::paging;
use crate::{serial_print, serial_println};

pub const SYSCALL_VECTOR: u8 = 0x84;
const DATA_ADDR: u64 = 0x_4000_0000_000;
const STACK_ADDR: u64 = 0x_4000_0010_000;

static mut BOOT_IMAGE: Option<&'static [u8]> = None;
static mut SB_INFO: [u64; 3] = [0, 0, 0];                       // secure-boot facts for SYSINFO: mode (0 enforce, 1 enroll, 2 off), signer (0 none, 1 platform, 2 user key), user keys accepted
pub unsafe fn set_sb_info(mode: u64, signer: u64, keys: u64) { SB_INFO = [mode, signer, keys]; }
/// The loader-provided linear framebuffer (Phase 7): reachable by user space ONLY through a FrameBuffer capability (syscalls 20/21).
static mut FB: Option<crate::multiboot::FramebufferInfo> = None;
const FB_WIN: u64 = 0x4000_0200_000;      // user window for FB_MAP: [FB_WIN, FB_WIN + FB_WIN_SIZE)
const TYPIST_BAND_HASH: u32 = 0x9ade3840; // set from tools/check_input_routing.py's render (hash of the 1024x192 band after typing ANYRQIS)
static mut INPUT_SEEDED: usize = 0;
static mut KBD_SEEDED: usize = 0;
/// 1-39: set when a LIVE (non-`qemu_test`) kernel spawns the keyboard router: the demo report is printed when init exits, then the router + shell keep running (no teardown).
static mut LIVE_SESSION: bool = false;
static mut MOUSE_LIVE: bool = false;
static mut PTR_BITS: u64 = 0;                                   // 1-65: pointer facts for SYSINFO word 0, bits 24.. : 24 PS/2 init attempted, 25 PS/2 ACKed, 26..29 USB controller kinds present (UHCI, OHCI, EHCI, xHCI)
/// In a live session exactly one process is legitimately still parked in a blocking RECV when the report runs: the keyboard shell waiting for its next key event.
const LIVE_PARKED: u32 = 0;   // 1-49: the live kernel runs the same first pass as the test build (the interactive session is a second pass with its own process 0), so nothing is parked at report time in either build
// 1-38: the scripted key presses (PS/2 set 1) the keyboard router turns into shell events; tools/check_shell.py decodes and models the same bytes independently.
#[allow(dead_code)]
const KBD_SCRIPT: [u8; 64] = [0x0f, 0x8f, 0x1c, 0x9c, 0x0f, 0x8f, 0x1c, 0x9c, 0x2a, 0x0f, 0x8f, 0xaa, 0x3c, 0xbc, 0x2a, 0x23, 0xa3, 0xaa, 0x12, 0x92, 0x26, 0xa6, 0x26, 0xa6, 0x18, 0x98, 0x39, 0xb9, 0x11, 0x91, 0x0e, 0x8e, 0x0e, 0x8e, 0x01, 0x81, 0x01, 0x81, 0x01, 0x81, 0x3c, 0xbc, 0x10, 0x90, 0xe0, 0x4d, 0xe0, 0xcd, 0xe0, 0x4b, 0xe0, 0xcb, 0x01, 0x81, 0x3d, 0xbd, 0x3d, 0xbd, 0x3d, 0xbd, 0x3e, 0xbe, 0x3e, 0xbe];
const KBD_CALLS: i64 = if cfg!(feature = "out_of_scope_experiments") { 126 } else { 0 };   // init's `go` for the keyboard shell + the forwarded events + the router's quit (1-49: the same in the live build's first pass) (the router has forwarded nothing yet)   // init's `go` for the keyboard shell + 23 forwarded events + the router's quit
#[allow(dead_code)]
const MOUSE_SCRIPT: [u8; 370] = [0x00, 0x18, 0x14, 0x00, 0x48, 0x7f, 0x00, 0x38, 0x14, 0xfc, 0x09, 0x00, 0x00, 0x08, 0x00, 0x00, 0x08, 0x00, 0x3c, 0x09, 0x00, 0x00, 0x08, 0x00, 0x00, 0x08, 0x78, 0x00, 0x08, 0x78, 0x00, 0x08, 0x78, 0x00, 0x08, 0x78, 0x00, 0x08, 0x78, 0x00, 0x08, 0x78, 0x00, 0x08, 0x78, 0x00, 0x08, 0x78, 0x00, 0x08, 0x78, 0x00, 0x08, 0x78, 0x00, 0x18, 0xe2, 0x00, 0x09, 0x00, 0x00, 0x09, 0x00, 0x00, 0x08, 0x00, 0x00, 0x08, 0x1e, 0x00, 0x09, 0x00, 0x00, 0x19, 0xe1, 0x00, 0x19, 0xe0, 0x00, 0x19, 0xff, 0x00, 0x19, 0xe0, 0x00, 0x08, 0x00, 0x00, 0x18, 0x88, 0x00, 0x18, 0x88, 0x00, 0x18, 0x88, 0x00, 0x18, 0x88, 0x00, 0x18, 0x88, 0x00, 0x18, 0x88, 0x00, 0x18, 0x88, 0x00, 0x18, 0x88, 0x00, 0x18, 0x88, 0x00, 0x09, 0x00, 0x00, 0x09, 0x20, 0x00, 0x09, 0x20, 0x00, 0x09, 0x1e, 0x00, 0x08, 0x00, 0x00, 0x18, 0x81, 0x00, 0x09, 0x00, 0x00, 0x09, 0x1f, 0x00, 0x09, 0x20, 0x00, 0x08, 0x00, 0x00, 0x08, 0x1b, 0x00, 0x09, 0x00, 0x00, 0x09, 0x64, 0x00, 0x08, 0x00, 0x00, 0x18, 0x81, 0x00, 0x18, 0xd1, 0x00, 0x09, 0x00, 0x00, 0x09, 0x50, 0x00, 0x08, 0x00, 0x00, 0x18, 0xaf, 0x00, 0x09, 0x00, 0x00, 0x09, 0x40, 0x00, 0x08, 0x00, 0x00, 0x08, 0x7f, 0x00, 0x08, 0x7f, 0x00, 0x08, 0x7f, 0x00, 0x08, 0x7f, 0x00, 0x08, 0x7f, 0x00, 0x08, 0x7f, 0x00, 0x08, 0x7f, 0x00, 0x08, 0x7f, 0x00, 0x18, 0xf1, 0x00, 0x09, 0x00, 0x00, 0x19, 0xc0, 0x00, 0x08, 0x00, 0x00, 0x08, 0x7f, 0x00, 0x18, 0xf0, 0x00, 0x09, 0x00, 0x00, 0x19, 0xb0, 0x00, 0x08, 0x00, 0x00, 0x08, 0x00, 0x19, 0x09, 0x00, 0x00, 0x29, 0x00, 0xe0, 0x29, 0x00, 0xe0, 0x29, 0x00, 0xe2, 0x08, 0x00, 0x00, 0x09, 0x00, 0x00, 0x08, 0x00, 0x00, 0x08, 0x00, 0x5e, 0x09, 0x00, 0x00, 0x29, 0x00, 0xe1, 0x29, 0x00, 0xe0, 0x08, 0x00, 0x00, 0x08, 0x00, 0x3e, 0x09, 0x00, 0x00, 0x29, 0x00, 0xb0, 0x08, 0x00, 0x00, 0x08, 0x00, 0x51, 0x09, 0x00, 0x00, 0x29, 0x00, 0xc0, 0x08, 0x00, 0x00, 0x18, 0x81, 0x00, 0x18, 0x81, 0x00, 0x18, 0x81, 0x00, 0x18, 0x81, 0x00, 0x18, 0x81, 0x00, 0x18, 0x81, 0x00, 0x38, 0xbf, 0xeb, 0x09, 0x00, 0x00, 0x08, 0x00, 0x00, 0x09, 0x00, 0x00, 0x08, 0x00, 0x00, 0x08, 0x96, 0x00, 0x09, 0x00, 0x00, 0x08, 0x00, 0x00, 0x08, 0x00, 0x55, 0x09, 0x00, 0x00, 0x29, 0x00, 0xc0, 0x08, 0x00, 0x00, 0x18, 0x81, 0x00, 0x18, 0x85, 0x00, 0x09, 0x00, 0x00, 0x09, 0x20, 0x00, 0x09, 0x20, 0x00, 0x08, 0x00, 0x00];   // 1-41: PS/2 mouse packets, queued right after the keyboard script; tools/check_shell.py decodes and models the same bytes
const KBD_CODES: [i64; 124] = [0, 256, 1, 257, 0, 1, 1, 2, 3, 4, 5, 6, 7, 6, 5, 1, 2, 0, 1, -1, 0, 4, 1, 1, 1, 1, 1, 1, 6291732, 6553640, 257, 2621480, 256, 2621600, 2621720, 2621840, 2621960, 2622080, 2622200, 2622320, 2622440, 2622463, 2622433, 512, 2622463, 2622432, 2622400, 2622399, 0, 2622367, 2622247, 2622127, 2622007, 2621887, 2621767, 2621647, 2621527, 2621440, 2621472, 2621504, 0, 2621534, 2621440, 2621471, 2621503, 2621530, -1, 2621630, 2621503, 2621456, 256, 2621536, 2621455, 2621519, 0, 2621646, 2621773, 2621900, 2622027, 2622154, 2622281, 2622408, 2622463, 2622448, 2622384, 0, 2622463, 2622447, 512, 2622367, 983967, 3081119, 5178271, 1, 7144351, 1281, 983967, 3015583, 5112735, 1049503, -1, 6292383, 983967, 5178271, 1, 5178144, 5178017, 5177890, 5177763, 5177636, 5177509, 6553700, 1280, 1280, 6553850, 1282, 983290, 5177594, 1, 5177467, 5177344, 5177376, 5177408, 3];
const KBD_HASH: [u32; 124] = [0x2974ff9e, 0x2974ff9e, 0xb5d2563e, 0x06153ea3, 0x0bf29543, 0x42d9adda, 0x83692a3b, 0xa3e4e4e1, 0x5ded945d, 0x71d161d9, 0xe6ff4f53, 0xa00bff53, 0xa25bcb9d, 0xa00bff53, 0xe6ff4f53, 0x82a1a853, 0x97de838e, 0x97de838e, 0x1e55713e, 0x1e55713e, 0x2974ff9e, 0xcd47e1be, 0x1e55713e, 0xf3e79438, 0x025080af, 0x1e55713e, 0x75129963, 0x1e55713e, 0x22afb04e, 0xdcca9078, 0x879f13ed, 0xa7ad1577, 0x6c94c6ee, 0x193e5678, 0x7c781c78, 0x06ebe278, 0x7e99a878, 0xa9816e78, 0x4da33478, 0x30fefa78, 0x1994c078, 0xbe79faf8, 0xfd67cd44, 0xfd67cd44, 0xbe79faf8, 0x5db6c21c, 0xf8237e78, 0x7e490b44, 0x7e490b44, 0xac6f7344, 0x34b67944, 0xc9037f44, 0x6f568544, 0x2daf8b44, 0x0a0e9144, 0x0a739744, 0x34de9d44, 0x7bbd4e78, 0x697f6fae, 0xeb784fee, 0xeb784fee, 0x88221eba, 0x7bbd4e78, 0x177539de, 0x9f509d95, 0x9a7d32f8, 0x9a7d32f8, 0x74f357f8, 0x9f509d95, 0x76f1ac35, 0x76f1ac35, 0xe15a2468, 0xaa3c9207, 0xd88d3744, 0xd88d3744, 0xb5fc0bf8, 0x13508dc4, 0xa4bb8578, 0xce684444, 0x37b29ef8, 0x923c5ac4, 0x73795878, 0xbe79faf8, 0x734c9a78, 0x2a803965, 0x2a803965, 0xbe79faf8, 0x68436f44, 0x68436f44, 0xac6f7344, 0xfa747344, 0xa0d47344, 0x47347344, 0xfd221a8d, 0xb2ec1a8d, 0xee256974, 0xfa747344, 0x6ba17344, 0x12017344, 0xafa77344, 0xafa77344, 0x56d66974, 0xfa747344, 0x47347344, 0xfd221a8d, 0x8ee0ab61, 0xad6bf00d, 0xf91e25e1, 0x777d258d, 0xbeee8167, 0xb01aa147, 0x00150167, 0xd75113b3, 0x00150167, 0x4b46ad67, 0x867ffc4e, 0xe0933af8, 0x8bd33af8, 0x749ead67, 0x4ab4778d, 0x994a4361, 0x90867967, 0x1b26b967, 0xbedf1e78];
const SHELL_CMP_CODES: [i64; 18] = [257, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, -2, 2];
const SHELL_CMP_HASH: [u32; 18] = [0xb07b4b0b, 0xb224381d, 0xc4c34a02, 0x795560a7, 0xaeedcc0c, 0xd5abdc31, 0x73bae116, 0x6a522abb, 0xfab50920, 0x8b32cc45, 0x2c26c42a, 0xdcf840cf, 0x911a9234, 0xf50d0859, 0xf35af33e, 0xf99ba2e3, 0xf99ba2e3, 0xafb1aa3c];
const SHELL_MED_CODES: [i64; 24] = [1024, 769, 768, 257, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, -2, 15, 16, 2];
const SHELL_MED_HASH: [u32; 24] = [0xc26fe0f1, 0xc26fe0f1, 0xc26fe0f1, 0xfcee010b, 0xce8ee27a, 0xe4c906c2, 0xc34e650a, 0x39ff7d52, 0xdf5ccf9a, 0xd886dbe2, 0xa13e222a, 0xd3e32272, 0xf1765cba, 0x29985102, 0x22897f4a, 0xc12a6792, 0xf0fb89da, 0x6c1d6622, 0x83507c6a, 0xe5f54cb2, 0xe5f54cb2, 0x83507c6a, 0x83e83799, 0xc26fe0f1];
// band hashes of the NDL-1 shell (userprog/prog_shell.s) after it rendered the compact (360 dp), medium (640 dp) and expanded (1024 dp) layouts; tools/check_shell.py re-renders all three independently from the generated display lists
// the interactive shell (1024 dp, class 2): reply code and band hash after each of the 20 input events (pointer x5, key x15) -- tools/check_shell.py's independent state-machine model
const SHELL_EV_CODES: [i64; 98] = [257, 784, -1, -1, 0, 1, 2, 3, 4, 0, 4, 3, 2, 1, 257, 0, 256, -1, -1, 0, 1, 257, 0, 1, 2, 0, 1, 0, 1, 0, 0, 1, 2, 0, 1280, 2, 1, 1, 3, 0, 0, 1, 0, 1, 2, 1282, 1, 1, 1, 1281, 257, 1, 3, 2, 1, 0, 1, 1283, 0, 1, 0, 1283, -1, 1, 3, 1, 1, 0, 1, 0, 1, 2, 3, 4, 1285, 1, 1, 1, 1285, 1, 1284, 1, -1, 1, 0, 1, 0, 1, 0, 1, 3, 1, 0, -1, -1, -1, 1, 1];
const SHELL_EV_HASH: [u32; 98] = [0x4075b043, 0x4075b043, 0x4075b043, 0x4075b043, 0x0bf29543, 0x06153ea3, 0x8c3e6343, 0x12ec6343, 0xd8c38543, 0x0bf29543, 0xd8c38543, 0x12ec6343, 0x8c3e6343, 0x06153ea3, 0x06153ea3, 0x0bf29543, 0x2974ff9e, 0x1e55713e, 0x1e55713e, 0x1e55713e, 0x97de838e, 0xde7c06da, 0x42d9adda, 0xde7c06da, 0x97de838e, 0x97de838e, 0x1e55713e, 0x1e55713e, 0xe31c2257, 0xe31c2257, 0x26640f57, 0x06ba0f57, 0xdb100f57, 0x26640f57, 0x33122d19, 0xd9e62d19, 0xdb100f57, 0xe31c2257, 0x1e55713e, 0x1e55713e, 0x1e55713e, 0xe31c2257, 0x26640f57, 0x06ba0f57, 0xdb100f57, 0x1e55713e, 0xe31c2257, 0x1e55713e, 0xe31c2257, 0x1e55713e, 0x4075b043, 0xe31c2257, 0x4075b043, 0x1e55713e, 0xe31c2257, 0x26640f57, 0x06ba0f57, 0x58f93f40, 0x355b5240, 0x77b5c344, 0x77b5c344, 0xe31c2257, 0xe31c2257, 0x355b5240, 0xf3e79438, 0x1e55713e, 0x75129963, 0x75129963, 0xa5bd14d3, 0xe90501d3, 0xc95b01d3, 0x9db101d3, 0x0e717193, 0xc6a15813, 0xe31c2257, 0xa5bd14d3, 0xd9af2710, 0xa5bd14d3, 0xe31c2257, 0xa5bd14d3, 0x1e55713e, 0x75129963, 0x75129963, 0x1e55713e, 0x1e55713e, 0xe31c2257, 0x26640f57, 0xa5bd14d3, 0xe90501d3, 0xe31c2257, 0x1e55713e, 0x1e55713e, 0x1e55713e, 0x1e55713e, 0x1e55713e, 0x1e55713e, 0x1e55713e, 0x1e55713e];
const SHELL_HASH: [u32; 3] = [0xafb1aa3c, 0xc26fe0f1, 0x1e55713e];
const GFX_BAND_HASH: u32 = 0xe9c2b880; // hash of the whole 1024x192 band after `gfxsvc` drew the HIG layout + text (tools/check_gfx.py re-renders it independently and must agree)
fn flat_band_hash(rgb: u32) -> u32 { let mut h = 0u32; for _ in 0..(1024 * 192) { h = h.wrapping_mul(31).wrapping_add(rgb & 0xFFFFFF); } h }
const FB_WIN_SIZE: u64 = 0x40_0000;       // 4 MiB (the 1024x768x32 screen is 3 MiB)
pub unsafe fn set_framebuffer(info: Option<crate::multiboot::FramebufferInfo>) {
    FB = info.filter(|i| i.kind == 1 && i.bpp == 32 && i.width > 0 && i.height > 0 && i.pitch >= i.width * 4 && i.addr & 0xfff == 0 && i.addr >= 0x4000_0000
        && (i.pitch as u64) * (i.height as u64) <= FB_WIN_SIZE);
}
const EPERM: i64 = -14;
const ENOENT: i64 = -15;
const ENODEV: i64 = -17;
const EIO: i64 = -18;
const ERANGE: i64 = -19;
const EINVAL: i64 = -20;
static mut SHM: crate::experimental::shm::ShmTable = crate::experimental::shm::ShmTable::new();
static mut DISK: Option<crate::experimental::ata::AtaDisk> = None;
const MAX_TASKS: usize = 88;
/// Image window: where a user-space loader may map fresh, kernel-zeroed pages (MEM_MAP) and set W^X protections (MEM_PROTECT).
/// PML4 slot 10 -- private per process, never one of the shared kernel slots.
const IMG_BASE: u64 = 0x5001_0000_000; // 256 MiB above the user-program link address (0x50000000000), same PML4 slot
const IMG_SIZE: u64 = 0x400_0000; // 64 MiB
const IMG_QUOTA: u32 = 512;       // pages (2 MiB) per process

/// Called by `kernel_main` with the loader-provided boot-image module.
/// # Safety
/// `image` must be memory the allocator will never reuse (see
/// `BumpFrameAllocator::is_frame_usable`'s module exclusion).
///
/// MEASURED BOOT (1-31): the SHA-256 of the whole image is compared with the digest the build baked INTO this kernel binary (`target/bootimg.sha256`, written by
/// tools/make_boot_image.py before the kernel is compiled). A mismatch means the module the loader handed over is not the image this kernel was built with: it is
/// REFUSED (never parsed, never run) and the line carries the word MISMATCH, which fails the boot test. This is a measurement tied to the kernel build, NOT Secure Boot:
/// the kernel itself is still loaded unverified by GRUB (that is ADR-0014, Proposed).
pub unsafe fn set_boot_image(image: &'static [u8]) {
    use sha2::{Digest, Sha256};
    let got: [u8; 32] = Sha256::digest(image).into();
    let hex = |d: &[u8; 32]| { let mut s = [0u8; 64]; for (i, b) in d.iter().enumerate() { s[2 * i] = b"0123456789abcdef"[(b >> 4) as usize]; s[2 * i + 1] = b"0123456789abcdef"[(b & 15) as usize]; } s };
    let (g, w) = (hex(&got), hex(&BOOT_IMAGE_SHA256));
    let (g, w) = (core::str::from_utf8(&g).unwrap(), core::str::from_utf8(&w).unwrap());
    if got == BOOT_IMAGE_SHA256 {
        serial_println!("  measured boot: boot image {} bytes, SHA-256 {} equals the digest built into the kernel -- OK", image.len(), g);
        BOOT_IMAGE = Some(image);
    } else {
        serial_println!("  measured boot: boot image {} bytes, SHA-256 {} but the kernel was built for {} -- MISMATCH, image REFUSED, no user space will start", image.len(), g, w);
    }
}
/// The digest of the boot image this kernel was built to run (generated: see `set_boot_image`).
static BOOT_IMAGE_SHA256: [u8; 32] = *include_bytes!("../../target/bootimg.sha256");

const EFAULT: i64 = -11;
const EMPTY: i64 = -12;
const ENOSYS: i64 = -13;

fn errno(e: IpcError) -> i64 {
    match e {
        IpcError::BadProcess => -1,
        IpcError::BadHandle => -2,
        IpcError::NoRight => -3,
        IpcError::Revoked => -4,
        IpcError::TargetDead => -5,
        IpcError::PayloadTooLarge => -6,
        IpcError::QueueFull => -7,
        IpcError::Widening => -8,
        IpcError::NotOwner => -9,
        IpcError::TableFull => -10,
        IpcError::WrongType => -16,
        IpcError::Contained => -21,
    }
}

struct Task {
    space: AddressSpace,
    saved_rsp: u64,
    done: bool,
    /// Some(endpoint) while blocked in a blocking RECV on it.
    blocked_ep: Option<u32>,
    /// true when `blocked_ep` is a NOTIFY_WAIT (woken by a notification bit) rather than a RECV (woken by a queued message).
    wait_notify: bool,
    /// Some while inside a CALL waiting for the server's REPLY (or its death).
    call: Option<CallWait>,
    /// Pages this process has mapped with MEM_MAP (its image window), capped at IMG_QUOTA.
    img_pages: u32,
    /// 1-70: the first 8 bytes of the name the process was spawned under (PROCINFO).
    name: [u8; 8],
}
struct CallWait {
    server: u32,
    rbuf: u64,
    rlen: u64,
    /// None = still waiting; Some(Ok(bytes)) = replied; Some(Err(errno)) = server died.
    result: Option<Result<Vec<u8>, i64>>,
}
static mut CALLS: u32 = 0;
static mut CALL_WAKES: u32 = 0;
static mut ORIG_CR3: u64 = 0;
static mut BASELINE_IN_USE: u64 = 0;
/// Stack the teardown runs on: the trapping task's own RSP0 stack lives inside
/// the AddressSpace being destroyed, so we must not be standing on it.
const TEARDOWN_STACK_SIZE: usize = 16 * 1024;
#[repr(align(16))]
struct TeardownStack([u8; TEARDOWN_STACK_SIZE]);
static mut TEARDOWN_STACK: TeardownStack = TeardownStack([0; TEARDOWN_STACK_SIZE]);
static mut BLOCKS: u32 = 0;
static mut NOTIFY_WAKES: u32 = 0;
static mut NOTIFY_BLOCKS: u32 = 0; // NOTIFY_WAIT calls that really parked (and were woken by a notification)
static mut WAKES: u32 = 0;
/// Parks that ended because the parked task was killed with its container rather than woken by a message.
static mut KILLED_PARKED: u32 = 0;
static mut DEADLOCK: bool = false;
const SLOT_RIP: usize = 15;

static mut TASKS: Vec<Task> = Vec::new();
static mut CURRENT: usize = 0;
static mut ACTIVE: bool = false;

/// A ring-3 #GP (e.g. an `in`/`out` outside the process's I/O-port window) is delivered to the process as a
/// synchronous upcall instead of halting the kernel: the process publishes a fixup address at DATA+0xff8;
/// the kernel stores the error code at DATA+0xff0, bumps a fault counter at DATA+0xfe8 and resumes the
/// process at the fixup address. No fixup registered -> None (the caller halts as before).
pub unsafe fn user_fault(error_code: u64) -> Option<u64> {
    if !ACTIVE || CURRENT >= TASKS.len() { return None; }
    if !paging::user_range_ok(DATA_ADDR + 0xfe8, 24, true) { return None; }
    let fix = core::ptr::read_volatile((DATA_ADDR + 0xff8) as *const u64);
    if fix == 0 { return None; }
    core::ptr::write_volatile((DATA_ADDR + 0xff0) as *mut u64, error_code);
    let n = core::ptr::read_volatile((DATA_ADDR + 0xfe8) as *const u64);
    core::ptr::write_volatile((DATA_ADDR + 0xfe8) as *mut u64, n + 1);
    Some(fix)
}
static mut CAPS: Option<CapSystem> = None;
static mut FRAME_ALLOCATOR: Option<BumpFrameAllocator> = None;

pub fn install(idt: &mut InterruptDescriptorTable) {
    unsafe {
        idt[SYSCALL_VECTOR as usize]
            .set_handler_addr(VirtAddr::new(syscall_trap_entry as u64))
            .set_privilege_level(PrivilegeLevel::Ring3);
    }
}

fn build_task(fa: &mut BumpFrameAllocator, elf_bytes: &[u8], is_first: bool) -> (Task, Option<VirtAddr>, Option<VirtAddr>) {
    let mut space = AddressSpace::new_sharing_kernel(fa).expect("syscall-demo address space");
    unsafe { space.activate() };
    let mut mapper = space.mapper();
    let loaded = elf::load(elf_bytes, &mut mapper, fa, 0).expect("syscall-demo elf load");
    let flags = PageTableFlags::PRESENT | PageTableFlags::WRITABLE | PageTableFlags::USER_ACCESSIBLE | PageTableFlags::NO_EXECUTE;
    assert_eq!(paging::map_range(&mut mapper, fa, VirtAddr::new(DATA_ADDR), 1, flags), 1);
    assert_eq!(paging::map_range(&mut mapper, fa, VirtAddr::new(STACK_ADDR), 1, flags), 1);
    // a user-accessible but READ-ONLY page (DATA_ADDR+0x2000); DATA_ADDR+0x1000 stays unmapped
    let ro = PageTableFlags::PRESENT | PageTableFlags::USER_ACCESSIBLE | PageTableFlags::NO_EXECUTE;
    assert_eq!(paging::map_range(&mut mapper, fa, VirtAddr::new(DATA_ADDR + 0x2000), 1, ro), 1);
    let user_rsp = VirtAddr::new(STACK_ADDR + 4096 - 16);
    if is_first {
        (Task { space, saved_rsp: 0, done: false, blocked_ep: None, wait_notify: false, call: None, img_pages: 0, name: [0; 8] }, Some(loaded.entry_point), Some(user_rsp))
    } else {
        let cs = crate::gdt::user_code_selector().0 as u64 | 3;
        let ss = crate::gdt::user_data_selector().0 as u64 | 3;
        let rsp = crate::experimental::ring3_scheduler::build_initial_blob(
            space.kernel_stack_top(), loaded.entry_point.as_u64(), user_rsp.as_u64(), cs, ss);
        (Task { space, saved_rsp: rsp, done: false, blocked_ep: None, wait_notify: false, call: None, img_pages: 0, name: [0; 8] }, None, None)
    }
}

/// # Safety
/// Call at most once, after the heap/paging/IDT are up; diverges.
///
/// NPS-001 section 5 stages 3-4: finds `init` in the boot image, builds it as
/// process 0 (holding only its own endpoint), and enters it. Everything else
/// is launched BY init through the SPAWN syscall.
/// 1-49: the SECOND pass of the live ISO. The first pass (the whole boot demo chain, scripted keyboard demo included) has finished; this starts a fresh process system whose
/// process 0 is `livesh`, which runs only the interactive keyboard/mouse session (the real PS/2 devices, no script, no report).
#[cfg(all(feature = "out_of_scope_experiments", not(feature = "qemu_test")))]
pub unsafe fn start_live(frame_allocator: BumpFrameAllocator) -> ! {
    LIVE_SESSION = true;
    serial_println!("  experimental syscall-demo: LIVE keyboard session (1-49): the boot demo chain has finished; the user-space router `kbdlive` (Input capability holder) and a keyboard- and mouse-driven shell now run on the real PS/2 devices (Tab / arrows / Enter / Esc / F2 theme / F3 text size / F4 notification, mouse, edge swipes) -- no script is queued");
    start(frame_allocator)
}

pub unsafe fn start(frame_allocator: BumpFrameAllocator) -> ! {
    FRAME_ALLOCATOR = Some(frame_allocator);
    let image = match BOOT_IMAGE.map(crate::experimental::bootimage::BootImage::parse) {
        Some(Ok(img)) => img,
        other => {
            serial_println!("  experimental syscall-demo: no usable boot image ({}) -- skipped", match other { None => "none provided", Some(_) => "parse failed" });
            let fa = FRAME_ALLOCATOR.take().unwrap();
            crate::experimental::pie_demo::start(fa)
        }
    };
    let init_name: &[u8] = if LIVE_SESSION { b"livesh" } else { b"init" };
    let init_elf = match image.get(init_name) {
        Some(e) => e,
        None => {
            serial_println!("  experimental syscall-demo: boot image has no `init` -- FAILED");
            let fa = FRAME_ALLOCATOR.take().unwrap();
            crate::experimental::pie_demo::start(fa)
        }
    };
    let fa = FRAME_ALLOCATOR.as_mut().unwrap();
    ORIG_CR3 = crate::experimental::scheduler::current_cr3();
    BASELINE_IN_USE = fa.frames_in_use();
    let mut caps = CapSystem::new();
    let init = caps.create_process();
    let _ = caps.create_endpoint(init).unwrap(); // init handle 0 (owner cap)
    // Storage I/O fast path (NPS-001 section 3): init alone is bootstrapped with the block-device
    // capability (handle 1); everyone else can only receive an attenuated copy from it by message.
    DISK = crate::experimental::ata::AtaDisk::probe().ok();
    caps.bootstrap_device(init, rights::READ | rights::WRITE | rights::GRANT).unwrap();
    // Driver authority (ADR-0006): init alone starts with the I/O-port capability for all of port space; a driver
    // can only receive a NARROWED copy by message, and must IO_ENABLE it before ring 3 may touch those ports.
    caps.bootstrap_ioport(init, rights::READ | rights::WRITE | rights::GRANT).unwrap();
    // Display (Phase 7): init alone starts with the framebuffer capability (handle 3) -- every row, READ|WRITE|GRANT; others get narrowed/attenuated copies by message.
    if let Some(f) = FB { caps.bootstrap_fb(init, rights::READ | rights::WRITE | rights::GRANT, f.height).unwrap(); }
    // Input (Phase 9, 1-29): init alone holds the Input capability (handle 4 -- 3 when there is no framebuffer). The boot test seeds the kernel queue
    // with a known scancode stream (through the real decoder): a, then Tab-down b-down Tab-up b-up (a key between the Tab press and release), Tab, Right.
    // The PS/2 decoder is a DRIVER (ADR-0006: out of the kernel), so the whole Input path exists only in the out_of_scope_experiments configuration.
    #[cfg(feature = "out_of_scope_experiments")]
    {
        caps.bootstrap_input(init, rights::READ | rights::GRANT).unwrap();
        if !LIVE_SESSION { INPUT_SEEDED = crate::experimental::input::seed_scancodes(&[0x1e, 0x9e, 0x0f, 0x30, 0x8f, 0xb0, 0x0f, 0x8f, 0xe0, 0x4d, 0xe0, 0xcd, 0x31, 0xb1, 0x15, 0x95, 0x13, 0x93, 0x10, 0x90, 0x17, 0x97, 0x1f, 0x9f]); }
    }
    ACTIVE = true;
    CAPS = Some(caps);
    if !crate::experimental::shm::run(fa) { serial_println!("  experimental syscall-demo: shm self-test FAILED"); }

    let (t0, entry, rsp) = build_task(fa, init_elf, true);
    serial_println!("  experimental syscall-demo: boot image parsed, `{}` loaded as process 0 (holds only its own endpoint; only it may SPAWN)", core::str::from_utf8(init_name).unwrap_or("?"));
    TASKS.push(t0);
    CURRENT = 0;
    TASKS[0].space.activate();
    usermode::enter_with_stack(entry.unwrap(), rsp.unwrap())
}

/// SPAWN(name_ptr, name_len): init-only. Builds a new process from the boot
/// image entry `name`, gives it its own endpoint (its handle 0), and returns
/// to the caller a SEND|GRANT capability to that endpoint.
unsafe fn spawn(me: usize, name_ptr: u64, name_len: u64, container: u64, caps: &mut CapSystem) -> i64 {
    if me != 0 {
        return EPERM; // only init holds the right to create processes
    }
    if container > u32::MAX as u64 || !caps.container_exists(container as u32) { return EINVAL; }
    if !caps.container_has_room(container as u32) { return errno(IpcError::TableFull); } // container quota
    if name_len == 0 || name_len > 15 || !paging::user_range_ok(name_ptr, name_len, false) {
        return EFAULT;
    }
    if TASKS.len() >= MAX_TASKS {
        return errno(IpcError::TableFull);
    }
    let mut name = [0u8; 15];
    core::ptr::copy_nonoverlapping(name_ptr as *const u8, name.as_mut_ptr(), name_len as usize);
    // BOOT-TEST ONLY (1-38): spawning the keyboard router `kbdrt` is the moment a scripted user "starts typing" -- the kernel queue is filled through the real PS/2 decoder, exactly as IRQ1 would.
    #[cfg(feature = "out_of_scope_experiments")]
    if &name[..name_len as usize] == b"kbdrt" {
        if !LIVE_SESSION { KBD_SEEDED = crate::experimental::input::seed_scancodes(&KBD_SCRIPT) + crate::experimental::input::seed_mouse_bytes(&MOUSE_SCRIPT); }   // first pass (test AND live build): the scripted session
        else {
            MOUSE_LIVE = crate::experimental::input::init_mouse();
            let usb = crate::experimental::pci::usb_controllers() as u64;
            let hid = crate::experimental::usb::init() as u64;
            PTR_BITS = (1 << 24) | ((MOUSE_LIVE as u64) << 25) | (usb << 26) | ((hid != 0) as u64) << 30;
            crate::serial_println!("  pointer: PS/2 mouse ACK={}; USB controller mask={} HID={} (0 none, 1 boot mouse, 2 tablet)", MOUSE_LIVE as u8, usb, hid);
        }   // live ISO, second pass: no script -- the router reads the REAL keyboard (IRQ1) and mouse (IRQ12)
    }
    let lookup: &[u8] = if LIVE_SESSION && &name[..name_len as usize] == b"kbdrt" { b"kbdlive" } else { &name[..name_len as usize] };   // live ISO: the never-ending router build
    let elf_bytes = match BOOT_IMAGE
        .and_then(|b| crate::experimental::bootimage::BootImage::parse(b).ok())
        .and_then(|img| img.get(lookup))
    {
        Some(e) => e,
        None => return ENOENT,
    };
    let fa = FRAME_ALLOCATOR.as_mut().unwrap();
    let (task, _, _) = build_task(fa, elf_bytes, false); // activates the new space while mapping
    TASKS[me].space.activate(); // back to the caller's space (and its RSP0)
    let pid = caps.create_process();
    debug_assert_eq!(pid as usize, TASKS.len());
    caps.set_container(pid, container as u32);
    let mut task = task; { let n = (name_len as usize).min(8); for k in 0..n { task.name[k] = name[k]; } }
    TASKS.push(task);
    let (ep, _own) = match caps.create_endpoint(pid) { Ok(x) => x, Err(e) => return errno(e) };
    match caps.bootstrap_grant(me as u32, ep, rights::SEND | rights::GRANT) {
        Ok(h) => h as i64,
        Err(e) => errno(e),
    }
}

/// Saved-context slot indices (see ring3_scheduler's layout diagram).
const SLOT_R10: usize = 5;
const SLOT_R9: usize = 6;
const SLOT_R8: usize = 7;
const SLOT_RDI: usize = 9;
const SLOT_RSI: usize = 10;
const SLOT_RDX: usize = 11;
const SLOT_RAX: usize = 14;

/// BLK_READ(h, lba, buf) / BLK_WRITE(h, lba, buf): 512-byte sector I/O through a
/// block-device capability. Authority is checked FIRST (capability kind + right),
/// then the user buffer is validated against the caller's page tables.
unsafe fn blk_syscall(write: bool, me: usize, h: u64, lba: u64, buf: u64, caps: &mut CapSystem) -> i64 {
    use crate::experimental::ata::{AtaError, SECTOR_SIZE};
    let need = if write { rights::WRITE } else { rights::READ };
    if h > u32::MAX as u64 { return errno(IpcError::BadHandle); }
    let (wlo, whi) = match caps.check_device(me as u32, h as u32, need) { Ok(w) => w, Err(e) => return errno(e) };
    let disk = match DISK.as_ref() { Some(d) => d, None => return ENODEV };
    if lba > u32::MAX as u64 { return ERANGE; }
    // the capability only covers sectors [wlo, whi): outside it is a permission failure, not a range one
    if lba < wlo as u64 || lba >= whi as u64 { return EPERM; }
    if !paging::user_range_ok(buf, SECTOR_SIZE as u64, !write) { return EFAULT; }
    let mut sector = [0u8; SECTOR_SIZE];
    let r = if write {
        core::ptr::copy_nonoverlapping(buf as *const u8, sector.as_mut_ptr(), SECTOR_SIZE);
        disk.write_sector(lba as u32, &sector)
    } else {
        let r = disk.read_sector(lba as u32, &mut sector);
        if r.is_ok() { core::ptr::copy_nonoverlapping(sector.as_ptr(), buf as *mut u8, SECTOR_SIZE); }
        r
    };
    match r { Ok(()) => 0, Err(AtaError::OutOfRange) => ERANGE, Err(_) => EIO }
}

/// SHM_CREATE(): a new region, zeroed BY THE KERNEL; the creator gets READ|WRITE|GRANT.
unsafe fn shm_create(me: usize, caps: &mut CapSystem) -> i64 {
    let fa = FRAME_ALLOCATOR.as_mut().unwrap();
    let id = match SHM.create(fa) { Ok(id) => id, Err(_) => return errno(IpcError::TableFull) };
    match caps.bootstrap_region(me as u32, id, rights::READ | rights::WRITE | rights::GRANT) {
        Ok(h) => h as i64,
        Err(e) => errno(e),
    }
}

/// SHM_MAP(h, vaddr): authority first (kind, then READ|WRITE), then the address
/// window, then liveness; maps the region's single frame into the CALLER's space.
/// MEM_MAP(vaddr, npages, flags): map fresh ZEROED pages in the caller's image window. flags: 1 = writable, 2 = executable
/// (both -> -20: W^X); any other bit -> -20. Already-mapped, out-of-window or unaligned -> -20; over the per-process quota -> -10.
/// All-or-nothing: a failure part-way unmaps what this call mapped.
unsafe fn mem_map(me: usize, vaddr: u64, npages: u64, flags: u64) -> i64 {
    use x86_64::structures::paging::{FrameAllocator, FrameDeallocator, Mapper, Page, Size4KiB};
    if flags & !3 != 0 || flags == 3 || npages == 0 || vaddr % 4096 != 0 { return EINVAL; }
    let end = match vaddr.checked_add(npages.saturating_mul(4096)) { Some(e) => e, None => return EINVAL };
    if vaddr < IMG_BASE || end > IMG_BASE + IMG_SIZE { return EINVAL; }
    if TASKS[me].img_pages as u64 + npages > IMG_QUOTA as u64 { return errno(IpcError::TableFull); }
    let mut pf = PageTableFlags::PRESENT | PageTableFlags::USER_ACCESSIBLE;
    if flags & 1 != 0 { pf |= PageTableFlags::WRITABLE; }
    if flags & 2 == 0 { pf |= PageTableFlags::NO_EXECUTE; }
    let fa = FRAME_ALLOCATOR.as_mut().unwrap();
    let mut mapper = TASKS[me].space.mapper();
    let mut done = 0u64;
    let mut rc = 0i64;
    while done < npages {
        let page: Page<Size4KiB> = Page::containing_address(VirtAddr::new(vaddr + done * 4096));
        let frame = match fa.allocate_frame() { Some(f) => f, None => { rc = errno(IpcError::TableFull); break } };
        core::ptr::write_bytes(frame.start_address().as_u64() as *mut u8, 0, 4096);
        match mapper.map_to(page, frame, pf, fa) {
            Ok(f) => f.flush(),
            Err(_) => { fa.deallocate_frame(frame); rc = EINVAL; break } // already mapped
        }
        done += 1;
    }
    if rc != 0 {
        for i in 0..done {
            let page: Page<Size4KiB> = Page::containing_address(VirtAddr::new(vaddr + i * 4096));
            if let Ok((f, fl)) = mapper.unmap(page) { fl.flush(); fa.deallocate_frame(f); }
        }
        return rc;
    }
    TASKS[me].img_pages += npages as u32;
    0
}

/// MEM_PROTECT(vaddr, npages, flags): change protection of pages THIS process mapped in its image window (same flag rules as
/// MEM_MAP; flags 0 = read-only). Every page must already be mapped, else nothing changes and -20.
unsafe fn mem_protect(me: usize, vaddr: u64, npages: u64, flags: u64) -> i64 {
    use x86_64::structures::paging::{Mapper, Page, Size4KiB, mapper::TranslateResult, Translate};
    if flags & !3 != 0 || flags == 3 || npages == 0 || vaddr % 4096 != 0 { return EINVAL; }
    let end = match vaddr.checked_add(npages.saturating_mul(4096)) { Some(e) => e, None => return EINVAL };
    if vaddr < IMG_BASE || end > IMG_BASE + IMG_SIZE { return EINVAL; }
    let mut pf = PageTableFlags::PRESENT | PageTableFlags::USER_ACCESSIBLE;
    if flags & 1 != 0 { pf |= PageTableFlags::WRITABLE; }
    if flags & 2 == 0 { pf |= PageTableFlags::NO_EXECUTE; }
    let mut mapper = TASKS[me].space.mapper();
    for i in 0..npages {
        if !matches!(mapper.translate(VirtAddr::new(vaddr + i * 4096)), TranslateResult::Mapped { .. }) { return EINVAL; }
    }
    for i in 0..npages {
        let page: Page<Size4KiB> = Page::containing_address(VirtAddr::new(vaddr + i * 4096));
        match mapper.update_flags(page, pf) { Ok(f) => f.flush(), Err(_) => return EINVAL }
    }
    0
}

/// True if the physical address lies inside the scan-out buffer (its frames must never be freed into the allocator).
unsafe fn is_fb_frame(pa: u64) -> bool {
    match FB { Some(f) => pa >= f.addr && pa < f.addr + (f.pitch as u64) * (f.height as u64), None => false }
}

/// INPUT_POLL(h, ptr): pops the oldest RAW input byte if the caller holds an Input capability with READ: writes {byte u8, 0 x2, device u8 (0 keyboard, 1 mouse), 0 x4,
/// ticks u64} (16 bytes) and returns 1; returns 0 when the queue is empty. WrongType / NoRight / BadHandle otherwise; nothing is popped on an error.
/// 1-50: the kernel does NOT decode -- scancode-set-1 and mouse-packet decoding is done by the holder (user space, userprog/ps2dec.inc).
#[cfg(not(feature = "out_of_scope_experiments"))]
unsafe fn input_poll(_me: usize, _h: u64, _ptr: u64, _caps: &mut CapSystem) -> i64 { ENOSYS }
#[cfg(feature = "out_of_scope_experiments")]
unsafe fn input_poll(me: usize, h: u64, ptr: u64, caps: &mut CapSystem) -> i64 {
    use crate::experimental::input::DeviceKind;
    if h > u32::MAX as u64 { return errno(IpcError::BadHandle); }
    if let Err(e) = caps.input_check(me as u32, h as u32) { return errno(e); }
    if !paging::user_range_ok(ptr, 16, true) { return EFAULT; }
    crate::experimental::usb::poll();                                  // 1-66: reap a USB HID report (if a USB pointer is active) into the PS/2 byte stream first
    match crate::experimental::input::pop() {
        None => 0,
        Some(ev) => {
            let mut b = [0u8; 16];
            b[0] = ev.byte; b[3] = if ev.device == DeviceKind::Ps2Mouse { 1 } else { 0 };
            b[8..16].copy_from_slice(&ev.timestamp_ticks.to_le_bytes());
            core::ptr::copy_nonoverlapping(b.as_ptr(), ptr as *mut u8, 16);
            1
        }
    }
}

/// FB_INFO(h, ptr): writes {width, height, pitch, bpp, win_lo, win_hi, 0, 0} (8 x u32) for the capability's row window.
unsafe fn fb_info(me: usize, h: u64, ptr: u64, caps: &mut CapSystem) -> i64 {
    if h > u32::MAX as u64 { return errno(IpcError::BadHandle); }
    let (lo, hi, _) = match caps.fb_window(me as u32, h as u32) { Ok(x) => x, Err(e) => return errno(e) };
    let f = match FB { Some(f) => f, None => return ENOSYS };
    if !paging::user_range_ok(ptr, 32, true) { return EFAULT; }
    let v: [u32; 8] = [f.width, f.height, f.pitch, f.bpp as u32, lo, hi, 0, 0];
    core::ptr::copy_nonoverlapping(v.as_ptr() as *const u8, ptr as *mut u8, 32);
    0
}

/// FB_MAP(h, vaddr): maps the capability's ROW WINDOW of the framebuffer (uncached) at `vaddr` (page-aligned, inside the FB window): writable only if
/// the capability carries WRITE (READ alone -> read-only mapping). A WRITABLE map first ZEROES the window (the previous owner's pixels are never
/// handed to a new writer -- FIND-KERNEL-002 for the scan-out buffer). A window narrower than the whole screen needs page-aligned rows (pitch % 4096 == 0)
/// so a band can never include a byte of a neighbour's rows. The pages are device memory: never given back to the frame allocator.
unsafe fn fb_map(me: usize, h: u64, vaddr: u64, caps: &mut CapSystem) -> i64 {
    use x86_64::structures::paging::{Mapper, Page, PhysFrame, Size4KiB};
    use x86_64::PhysAddr;
    if h > u32::MAX as u64 { return errno(IpcError::BadHandle); }
    let (lo, hi, r) = match caps.fb_window(me as u32, h as u32) { Ok(x) => x, Err(e) => return errno(e) };
    let f = match FB { Some(f) => f, None => return ENOSYS };
    if r & (rights::READ | rights::WRITE) == 0 { return errno(IpcError::NoRight); }
    let full = lo == 0 && hi >= f.height;
    if !full && f.pitch % 4096 != 0 { return EINVAL; }
    let (row0, row1) = (lo as u64, (hi as u64).min(f.height as u64));
    if row0 >= row1 { return EINVAL; }
    let (b0, b1) = (row0 * f.pitch as u64, row1 * f.pitch as u64);
    let npages = (b1 - b0 + 4095) / 4096;
    if vaddr % 4096 != 0 || vaddr < FB_WIN || vaddr.checked_add(npages * 4096).map_or(true, |e| e > FB_WIN + FB_WIN_SIZE) { return EINVAL; }
    let writable = r & rights::WRITE != 0;
    let mut pf = PageTableFlags::PRESENT | PageTableFlags::USER_ACCESSIBLE | PageTableFlags::NO_EXECUTE | PageTableFlags::NO_CACHE | PageTableFlags::WRITE_THROUGH;
    if writable { pf |= PageTableFlags::WRITABLE; }
    let fa = FRAME_ALLOCATOR.as_mut().unwrap();
    let mut mapper = TASKS[me].space.mapper();
    let mut done = 0u64;
    let mut rc = 0i64;
    while done < npages {
        let page: Page<Size4KiB> = Page::containing_address(VirtAddr::new(vaddr + done * 4096));
        let frame = PhysFrame::<Size4KiB>::containing_address(PhysAddr::new(f.addr + b0 + done * 4096));
        match mapper.map_to(page, frame, pf, fa) { Ok(fl) => fl.flush(), Err(_) => { rc = EINVAL; break } }
        done += 1;
    }
    if rc != 0 {
        for i in 0..done { let page: Page<Size4KiB> = Page::containing_address(VirtAddr::new(vaddr + i * 4096)); if let Ok((_, fl)) = mapper.unmap(page) { fl.flush(); } }
        return rc;
    }
    if writable { core::ptr::write_bytes(vaddr as *mut u8, 0, (b1 - b0) as usize); }
    0
}

unsafe fn shm_map(me: usize, h: u64, vaddr: u64, caps: &mut CapSystem) -> i64 {

    use crate::experimental::shm::{ShmError, PAGE, WINDOW_BASE, WINDOW_PAGES};
    use x86_64::structures::paging::{Mapper, Page, Size4KiB};
    if h > u32::MAX as u64 { return errno(IpcError::BadHandle); }
    let (id, r) = match caps.region_cap(me as u32, h as u32) { Ok(x) => x, Err(e) => return errno(e) };
    if r & (rights::READ | rights::WRITE) == 0 { return errno(IpcError::NoRight); }
    if vaddr % PAGE as u64 != 0 || vaddr < WINDOW_BASE || vaddr >= WINDOW_BASE + WINDOW_PAGES * PAGE as u64 { return EINVAL; }
    let frame = match SHM.frame_for_map(id) { Ok(f) => f, Err(ShmError::Dead) => return errno(IpcError::TargetDead), Err(_) => return errno(IpcError::BadHandle) };
    let mut flags = PageTableFlags::PRESENT | PageTableFlags::USER_ACCESSIBLE | PageTableFlags::NO_EXECUTE;
    if r & rights::WRITE != 0 { flags |= PageTableFlags::WRITABLE; }
    let fa = FRAME_ALLOCATOR.as_mut().unwrap();
    let mut mapper = TASKS[me].space.mapper();
    let page: Page<Size4KiB> = Page::containing_address(VirtAddr::new(vaddr));
    match mapper.map_to(page, frame, flags, fa) {
        Ok(f) => { f.flush(); SHM.note_mapped(id); 0 }
        Err(_) => EINVAL, // already mapped
    }
}

/// Round-robin over tasks that can run: not finished, and either not
/// blocked or blocked on an endpoint that now has a message (that wakes it).
/// Returns None when nothing can ever run again (all done, or DEADLOCK --
/// every remaining task blocked with nothing pending).
unsafe fn pick_next(me: usize, caps: &CapSystem) -> Option<usize> {
    let n = TASKS.len();
    for i in 1..=n {
        let c = (me + i) % n;
        if TASKS[c].done { continue; }
        if let Some(cw) = &TASKS[c].call {
            if cw.result.is_none() { continue; } // parked in CALL until reply / server death
            CALL_WAKES += 1;
            return Some(c);
        }
        match TASKS[c].blocked_ep {
            None => return Some(c),
            Some(ep) if (if TASKS[c].wait_notify { caps.notify_pending(ep) } else { caps.pending(ep) > 0 }) => {
                if TASKS[c].wait_notify { NOTIFY_WAKES += 1; } else { WAKES += 1; }
                TASKS[c].blocked_ep = None; TASKS[c].wait_notify = false;
                return Some(c);
            }
            Some(_) => {}
        }
    }
    if TASKS.iter().any(|t| !t.done) {
        DEADLOCK = true; // someone is blocked forever: report, don't hang
        for (i, t) in TASKS.iter().enumerate() { if !t.done { serial_println!("  deadlock: task {} blocked_ep {:?} in_call {} pending {}", i, t.blocked_ep, t.call.is_some(), t.blocked_ep.map(|e| caps.pending(e)).unwrap_or(0)); } }
    }
    None
}

/// Switches to task `c`; if it was parked in CALL, delivers the reply into ITS address
/// space (now active) and sets its syscall result, then returns its saved trap RSP.
unsafe fn switch_to(c: usize) -> u64 {
    CURRENT = c;
    TASKS[c].space.activate();
    if let Some(cw) = TASKS[c].call.take() {
        let blob = TASKS[c].saved_rsp as *mut u64;
        let rc: i64 = match cw.result {
            Some(Ok(bytes)) => {
                if bytes.len() as u64 > cw.rlen { errno(IpcError::PayloadTooLarge) }
                else if !paging::user_range_ok(cw.rbuf, bytes.len() as u64, true) { EFAULT }
                else {
                    core::ptr::copy_nonoverlapping(bytes.as_ptr(), cw.rbuf as *mut u8, bytes.len());
                    bytes.len() as i64
                }
            }
            Some(Err(e)) => e,
            None => ENOSYS,
        };
        blob.add(SLOT_RAX).write(rc as u64);
    }
    TASKS[c].saved_rsp
}

#[no_mangle]
extern "C" fn syscall_dispatch(trap_rsp: u64) -> u64 {
    unsafe {
        let blob = trap_rsp as *mut u64;
        let nr = blob.add(SLOT_RAX).read();
        let a0 = blob.add(SLOT_RDI).read();
        let a1 = blob.add(SLOT_RSI).read();
        let a2 = blob.add(SLOT_RDX).read();
        let a_xfer = blob.add(SLOT_R10).read();
        let a_rights = blob.add(SLOT_R8).read();
        let me = CURRENT;
        let caps = CAPS.as_mut().unwrap();

        let ret: i64 = match nr {
            0 | 3 => {
                TASKS[me].saved_rsp = trap_rsp;
                if nr == 3 {
                    TASKS[me].done = true;
                    // process death: its endpoints die and its capabilities vanish (NPS-003 §7)
                    let _ = caps.terminate_process(me as u32);
                    for id in caps.drain_released() {
                        SHM.holders_gone(id, FRAME_ALLOCATOR.as_mut().unwrap()); // last capability gone
                    }
                    // callers still waiting for THIS process's reply are woken with TargetDead
                    for t in TASKS.iter_mut() {
                        if let Some(cw) = t.call.as_mut() {
                            if cw.server == me as u32 && cw.result.is_none() { cw.result = Some(Err(errno(IpcError::TargetDead))); }
                        }
                    }
                }
                // 1-49: in the live session (second pass) the keyboard shell and its router never end and no report is printed; `finale` is only reached in the first pass
                match pick_next(me, caps) {
                    None => { return finale(); }
                    Some(c) => {
                        return switch_to(c);
                    }
                }
            }
            1 => {
                // SEND(handle=a0, buf=a1, len=a2)
                if a2 > MAX_PAYLOAD as u64 {
                    // size is checked by the core too, but never read an
                    // oversized user length at all
                    errno(IpcError::PayloadTooLarge)
                } else if !paging::user_range_ok(a1, a2, false) {
                    EFAULT
                } else {
                    let mut buf = [0u8; MAX_PAYLOAD];
                    core::ptr::copy_nonoverlapping(a1 as *const u8, buf.as_mut_ptr(), a2 as usize);
                    let transfer = if a_xfer == u64::MAX {
                        Some(None)
                    } else if a_rights > 0xff || a_xfer > u32::MAX as u64 {
                        None // out-of-range operands: refuse as a widening attempt / bad handle below
                    } else {
                        Some(Some((a_xfer as u32, a_rights as u8)))
                    };
                    let transfer = match transfer { Some(t) => t, None => { blob.add(SLOT_RAX).write(errno(IpcError::Widening) as u64); return trap_rsp; } };
                    match caps.send(me as u32, a0 as u32, &buf[..a2 as usize], transfer) {
                        Ok(()) => 0,
                        Err(e) => errno(e),
                    }
                }
            }
            2 => {
                // RECV(owned_idx=a0, buf=a1, buflen=a2); buffer must hold a max message
                if a2 < MAX_PAYLOAD as u64 || !paging::user_range_ok(a1, a2, true) {
                    EFAULT
                } else {
                    match caps.owned_endpoint(me as u32, a0 as usize) {
                        None => errno(IpcError::BadHandle),
                        Some(ep) => match caps.receive(me as u32, ep) {
                            Ok(None) if a_xfer == 1 => {
                                // BLOCKING receive (r10 = 1): park this task and restart the
                                // `int 0x84` (2 bytes) when it is next scheduled.
                                TASKS[me].blocked_ep = Some(ep); TASKS[me].wait_notify = false;
                                TASKS[me].saved_rsp = trap_rsp;
                                let rip = blob.add(SLOT_RIP);
                                rip.write(rip.read() - 2);
                                BLOCKS += 1;
                                match pick_next(me, caps) {
                                    None => { return finale(); }
                                    Some(c) => {
                                        return switch_to(c);
                                    }
                                }
                            }
                            Ok(None) => EMPTY,
                            Ok(Some(m)) => {
                                core::ptr::copy_nonoverlapping(m.payload.as_ptr(), a1 as *mut u8, m.payload.len());
                                blob.add(SLOT_RDX).write(m.transferred.map(|h| h as u64).unwrap_or(u64::MAX));
                                blob.add(SLOT_R9).write(m.badge as u64); // which badged capability the sender used (0 = unbadged)
                                m.payload.len() as i64
                            }
                            Err(e) => errno(e),
                        },
                    }
                }
            }
            4 => spawn(me, a0, a1, 0, caps),
            25 => { // REVOKE(owned_idx): the endpoint's owner invalidates every outstanding send capability to it (NPS-003 §4.3) -- holders need not cooperate
                match caps.owned_endpoint(me as u32, a0 as usize) { None => errno(IpcError::BadHandle), Some(ep) => match caps.revoke(me as u32, ep) { Ok(()) => 0, Err(e) => errno(e) } }
            }
            23 => { // NOTIFY(h, bits): payload-free asynchronous signal (NPS-003 §3)
                if a1 == 0 || a0 > u32::MAX as u64 { if a1 == 0 { EINVAL } else { errno(IpcError::BadHandle) } }
                else { match caps.notify(me as u32, a0 as u32, a1) { Ok(()) => 0, Err(e) => errno(e) } }
            }
            24 => { // NOTIFY_WAIT(owned_idx, block): collect-and-clear this endpoint's notification bits -> rdx (rax = 0); empty: -EMPTY, or park when block == 1
                match caps.owned_endpoint(me as u32, a0 as usize) {
                    None => errno(IpcError::BadHandle),
                    Some(ep) => {
                        let bits = caps.take_notify(ep);
                        if bits != 0 { blob.add(SLOT_RDX).write(bits); 0 }
                        else if a1 == 1 {
                            TASKS[me].blocked_ep = Some(ep); TASKS[me].wait_notify = true;
                            TASKS[me].saved_rsp = trap_rsp;
                            let rip = blob.add(SLOT_RIP); rip.write(rip.read() - 2);   // restart the `int 0x84` when woken
                            NOTIFY_BLOCKS += 1;
                            match pick_next(me, caps) { None => { return finale(); } Some(c) => { return switch_to(c); } }
                        } else { EMPTY }
                    }
                }
            }
            14 => spawn(me, a0, a1, a2, caps), // SPAWN_IN(name, len, container)
            17 => mem_map(me, a0, a1, a2),
            18 => mem_protect(me, a0, a1, a2),
            26 => { // SYSINFO(buf): root-only, read-only facts for the desktop's System page -> 8 words {mode | signer << 8 | keys << 16, first 8 bytes of the boot-image digest (big endian), image length, live processes, processes ever started, 0, 0, 0}
                if me != 0 { EPERM } else if !paging::user_range_ok(a0, 64, true) { EFAULT }
                else {
                    let live = TASKS.iter().filter(|t| !t.done).count() as u64;
                    let mut h8 = 0u64; for i in 0..8 { h8 = (h8 << 8) | BOOT_IMAGE_SHA256[i] as u64; }
                    let w = [SB_INFO[0] | (SB_INFO[1] << 8) | (SB_INFO[2] << 16) | PTR_BITS, h8, BOOT_IMAGE.map(|b| b.len() as u64).unwrap_or(0), live, TASKS.len() as u64, 0, 0, 0];
                    core::ptr::copy_nonoverlapping(w.as_ptr() as *const u8, a0 as *mut u8, 64); 0
                }
            }
            27 => { // PROCINFO(idx, buf): root-only, read-only: 8 words for process idx {alive, container, blocked, image pages, name (8 bytes, little endian), 0, 0, 0}; -> 0, or ENOENT past the end
                if me != 0 { EPERM } else if !paging::user_range_ok(a1, 64, true) { EFAULT }
                else if a0 as usize >= TASKS.len() { ENOENT }
                else {
                    let t = &TASKS[a0 as usize]; let c = caps.container_of(a0 as u32);
                    let w = [(!t.done) as u64, c as u64, t.blocked_ep.is_some() as u64, t.img_pages as u64, u64::from_le_bytes(t.name), 0, 0, 0];
                    crate::serial_println!("  procinfo: {} alive={} container={} blocked={} pages={} name={}", a0, w[0], w[1], w[2], w[3], core::str::from_utf8(&t.name).unwrap_or("?").trim_end_matches('\0'));
                    core::ptr::copy_nonoverlapping(w.as_ptr() as *const u8, a1 as *mut u8, 64); 0
                }
            }
            19 => { // BOOT_FILE(name_ptr, name_len, buf, buflen): init-only; copy a boot-image file out -> its size
                if me != 0 { EPERM }
                else if a1 == 0 || a1 > 15 || !paging::user_range_ok(a0, a1, false) { EFAULT }
                else {
                    let mut name = [0u8; 15];
                    core::ptr::copy_nonoverlapping(a0 as *const u8, name.as_mut_ptr(), a1 as usize);
                    match BOOT_IMAGE.and_then(|b| crate::experimental::bootimage::BootImage::parse(b).ok()).and_then(|img| img.get(&name[..a1 as usize])) {
                        None => ENOENT,
                        Some(f) => if f.len() as u64 > a3_buflen(blob) { errno(IpcError::PayloadTooLarge) }
                            else if !paging::user_range_ok(a2, f.len() as u64, true) { EFAULT }
                            else { core::ptr::copy_nonoverlapping(f.as_ptr(), a2 as *mut u8, f.len()); f.len() as i64 },
                    }
                }
            }
            16 => { // BADGE(h, badge): derive a badged copy of an endpoint capability (immutable tag the receiver sees)
                if a0 > u32::MAX as u64 || a1 > u32::MAX as u64 { errno(IpcError::BadHandle) }
                else { match caps.badge(me as u32, a0 as u32, a1 as u32) { Ok(h) => h as i64, Err(e) => errno(e) } }
            }
            13 => { // CONTAINER_CREATE(kind_mask, quota): init-only
                if me != 0 { EPERM } else if a0 > 0xff || a1 == 0 || a1 > u32::MAX as u64 { EINVAL }
                else { match caps.create_container(a0 as u8, a1 as u32) { Ok(id) => id as i64, Err(e) => errno(e) } }
            }
            15 => { // CONTAINER_KILL(id): init-only; terminates every live process in the container (not the root) -> count killed
                if me != 0 { EPERM } else if a0 == 0 || a0 > u32::MAX as u64 || !caps.container_exists(a0 as u32) { EINVAL }
                else {
                    let victims = caps.container_members(a0 as u32);
                    for &v in &victims {
                        if TASKS[v as usize].blocked_ep.take().is_some() { KILLED_PARKED += 1; }
                        TASKS[v as usize].done = true;
                        let _ = caps.terminate_process(v);
                        for id in caps.drain_released() { SHM.holders_gone(id, FRAME_ALLOCATOR.as_mut().unwrap()); }
                        for t in TASKS.iter_mut() {
                            if let Some(cw) = t.call.as_mut() {
                                if cw.server == v && cw.result.is_none() { cw.result = Some(Err(errno(IpcError::TargetDead))); }
                            }
                        }
                    }
                    victims.len() as i64
                }
            }
            28 => { // PROC_KILL(pid): root-only; terminates one live process (never the root) -> 1, or EINVAL
                if me != 0 { EPERM } else if a0 == 0 || a0 as usize >= TASKS.len() || TASKS[a0 as usize].done { EINVAL }
                else {
                    let v = a0 as u32;
                    if TASKS[v as usize].blocked_ep.take().is_some() { KILLED_PARKED += 1; }
                    TASKS[v as usize].done = true;
                    let _ = caps.terminate_process(v);
                    for id in caps.drain_released() { SHM.holders_gone(id, FRAME_ALLOCATOR.as_mut().unwrap()); }
                    for t in TASKS.iter_mut() {
                        if let Some(cw) = t.call.as_mut() {
                            if cw.server == v && cw.result.is_none() { cw.result = Some(Err(errno(IpcError::TargetDead))); }
                        }
                    }
                    1
                }
            }
            5 | 6 => blk_syscall(nr == 6, me, a0, a1, a2, caps),
            9 => {
                // CALL(h=a0, sbuf=a1, slen=a2, rbuf=r10, rlen=r8): send + park until the REPLY
                let (rbuf, rlen) = (a_xfer, a_rights);
                if a2 > MAX_PAYLOAD as u64 { errno(IpcError::PayloadTooLarge) }
                else if rlen == 0 || rlen > MAX_PAYLOAD as u64 || !paging::user_range_ok(a1, a2, false) || !paging::user_range_ok(rbuf, rlen, true) { EFAULT }
                else {
                    let mut buf = [0u8; MAX_PAYLOAD];
                    core::ptr::copy_nonoverlapping(a1 as *const u8, buf.as_mut_ptr(), a2 as usize);
                    match caps.call_send(me as u32, a0 as u32, &buf[..a2 as usize]) {
                        Err(e) => errno(e),
                        Ok(server) => {
                            CALLS += 1;
                            TASKS[me].call = Some(CallWait { server, rbuf, rlen, result: None });
                            TASKS[me].saved_rsp = trap_rsp;
                            match pick_next(me, caps) {
                                None => { return finale(); }
                                Some(c) => return switch_to(c),
                            }
                        }
                    }
                }
            }
            10 => {
                // REPLY(h=a0, buf=a1, len=a2): answer ONE call; the capability is consumed
                if a0 > u32::MAX as u64 { errno(IpcError::BadHandle) }
                else {
                    match caps.reply_target(me as u32, a0 as u32) {
                        Err(e) => errno(e),
                        Ok(caller) => {
                            if a2 > MAX_PAYLOAD as u64 { errno(IpcError::PayloadTooLarge) }
                            else if !paging::user_range_ok(a1, a2, false) { EFAULT }
                            else {
                                let bytes = core::slice::from_raw_parts(a1 as *const u8, a2 as usize).to_vec();
                                caps.drop_handle(me as u32, a0 as u32);
                                match TASKS.get_mut(caller as usize).and_then(|t| t.call.as_mut()) {
                                    Some(cw) if cw.result.is_none() => { cw.result = Some(Ok(bytes)); 0 }
                                    _ => errno(IpcError::TargetDead), // caller gone
                                }
                            }
                        }
                    }
                }
            }
            7 => shm_create(me, caps),
            12 => { // IO_ENABLE(h): allow THIS process's ring-3 code to use the ports its I/O-port capability covers
                if a0 > u32::MAX as u64 { errno(IpcError::BadHandle) }
                else { match caps.ioport_window(me as u32, a0 as u32) {
                    Ok((lo, hi)) => if lo >= hi || hi > crate::gdt::IOPB_PORTS as u32 { EINVAL } else {
                        TASKS[me].space.io_ports = (lo as u16, hi as u16);
                        crate::gdt::set_io_ports(lo as u16, hi as u16);
                        0
                    },
                    Err(e) => errno(e),
                } }
            }
            11 => { // DEV_NARROW(h, lo, len): derive a block-device capability for a sub-range
                if a0 > u32::MAX as u64 { errno(IpcError::BadHandle) }
                else { match caps.narrow_device(me as u32, a0 as u32, a1, a2) { Ok(h) => h as i64, Err(e) => errno(e) } }
            }
            8 => shm_map(me, a0, a1, caps),
            20 => fb_info(me, a0, a1, caps),
            21 => fb_map(me, a0, a1, caps),
            22 => input_poll(me, a0, a1, caps),
            _ => ENOSYS,
        };
        blob.add(SLOT_RAX).write(ret as u64);
        trap_rsp
    }
}

unsafe fn a3_buflen(blob: *mut u64) -> u64 { blob.add(SLOT_R10).read() }

/// Page-table flags of task `t`'s mapping of the first framebuffer page (None if unmapped).
unsafe fn fb_flags(t: usize) -> Option<PageTableFlags> {
    use x86_64::structures::paging::mapper::TranslateResult;
    use x86_64::structures::paging::Translate;
    match TASKS[t].space.mapper().translate(VirtAddr::new(FB_WIN)) { TranslateResult::Mapped { flags, .. } => Some(flags), _ => None }
}

unsafe fn read_data(task: usize, off: u64) -> u64 {
    TASKS[task].space.activate();
    core::ptr::read_volatile((DATA_ADDR + off) as *const u64)
}

const FINALE_STACK_SIZE: usize = 128 * 1024;
#[repr(align(16))]
struct FinaleStack([u8; FINALE_STACK_SIZE]);
static mut FINALE_STACK: FinaleStack = FinaleStack([0; FINALE_STACK_SIZE]);
/// The report is large (hundreds of locals and format arguments). It used to run on the trapping task's 20 KiB kernel stack, which lives in the HEAP: 1-44 found that a few
/// more locals overflowed it into a neighbouring heap block and surfaced as a bogus "memory allocation failed". It now runs on its own 128 KiB static stack.
unsafe fn finale() -> u64 {
    if LIVE_SESSION { crate::hlt_loop() }   // 1-49: the interactive (second) pass has no report; nothing runnable means the machine just idles
    let top = (core::ptr::addr_of_mut!(FINALE_STACK) as u64) + FINALE_STACK_SIZE as u64 - 16;
    let r: u64;
    asm!("mov r12, rsp", "mov rsp, {top}", "call {f}", "mov rsp, r12", top = in(reg) top, f = sym finale_inner, out("r12") _, lateout("rax") r, clobber_abi("C"));
    r
}

unsafe extern "C" fn finale_inner() -> u64 {
    // server (task 0): sum at 0x08, count at 0x10, reply-send result at 0x18
    let s_sum = read_data(1, 0x08);
    let s_cnt = read_data(1, 0x10);
    let s_rep = read_data(1, 0x18) as i64;
    // client (task 1): reply at 0x08, errors at 0x10/0x18/0x20/0x28, send-fail 0x30, reply len 0x38
    let c_reply = read_data(2, 0x08);
    let e_forged = read_data(2, 0x10) as i64;
    let e_fault = read_data(2, 0x18) as i64;
    let e_big = read_data(2, 0x20) as i64;
    let e_notowned = read_data(2, 0x28) as i64;
    let c_sendfail = read_data(2, 0x30);
    let e_unmapped = read_data(2, 0x40) as i64;
    let e_spill = read_data(2, 0x48) as i64;
    let e_ro = read_data(2, 0x50) as i64;
    let c_len = read_data(2, 0x38);

    let i_srv_h = read_data(0, 0x08) as i64;
    let i_cli_h = read_data(0, 0x10) as i64;
    let i_send = read_data(0, 0x18) as i64;
    let i_noent = read_data(0, 0x20) as i64;
    let c_spawn = read_data(2, 0x70) as i64;
    let spawn_ok = i_srv_h >= 1 && i_srv_h < 8 && i_cli_h >= 1 && i_cli_h < 8 && i_send == 0 && i_noent == ENOENT && c_spawn == EPERM;
    serial_println!(
        "  experimental syscall-demo: init (process 0) spawned server (cap handle {}) and client (cap handle {}) from the boot image, handed the client a SEND cap to the server by message ({}); spawn of unknown name -> {} (-15 ENOENT); spawn attempted by the client -> {} (-14 EPERM)",
        i_srv_h, i_cli_h, i_send, i_noent, c_spawn
    );
    let i_mortal_h = read_data(0, 0x28) as i64;
    let i_dead_rc = read_data(0, 0x30) as i64;
    let i_tries = read_data(0, 0x38);
    let dead_ok = i_mortal_h >= 1 && i_dead_rc == -5 && i_tries >= 1 && i_tries < 50;
    serial_println!(
        "  experimental syscall-demo: process death -> init sent to `mortal` (exits immediately) {} time(s) until the kernel answered {} (-5 TargetDead; messages to a terminated process fail, never block or vanish silently)",
        i_tries, i_dead_rc
    );
    let b_read = read_data(4, 0x08) as i64;
    let b_write = read_data(4, 0x10) as i64;
    let b_wrongtype = read_data(4, 0x18) as i64;
    let b_reply = read_data(4, 0x20) as i64;
    let b_map = read_data(4, 0x28) as i64;
    let b_romap = read_data(4, 0x30) as i64;
    let b_roread = read_data(4, 0x38) as i64;
    let b_unal = read_data(4, 0x40) as i64;
    let b_outwin = read_data(4, 0x48) as i64;
    let b_twice = read_data(4, 0x50) as i64;
    let b_devreg = read_data(4, 0x58) as i64;
    let i_blk_h = read_data(0, 0x40) as i64;
    let i_len = read_data(0, 0x58) as i64;
    let i_pat = read_data(0, 0x60);
    let i_r1 = read_data(0, 0x68) as i64;
    let i_map = read_data(0, 0x70) as i64;
    let i_zero = read_data(0, 0x78);
    let i_m3 = read_data(0, 0x88) as i64;
    let i_m4 = read_data(0, 0x90) as i64;
    let disk_present = DISK.is_some();
    let storage_ok = i_blk_h >= 1 && b_write == -3 && b_wrongtype == -16 && b_reply == 0
        && if disk_present { b_read == 0 && i_len == 64 && i_pat == 1 } else { b_read == ENODEV };
    serial_println!(
        "  experimental syscall-demo: storage service -- disk {}; blk got an attenuated READ-only device cap from init by message; sector read rc {}, write attempt {} (-3 NoRight), device cap used as endpoint {} (-16 WrongType); init got reply of {} bytes; full 512-byte sector via shared page {}",
        if disk_present { "present" } else { "ABSENT (read expected -17)" }, b_read, b_write, b_wrongtype, i_len,
        if disk_present { if i_pat == 1 { "matches sector 5" } else { "WRONG" } } else { "n/a" }
    );
    let shm_ok = i_r1 >= 1 && i_map == 0 && i_zero == 0 && i_m3 == 0 && i_m4 == 0 && b_map == 0 && b_romap == 0
        && if disk_present { b_roread == EFAULT } else { b_roread == ENODEV }
        && b_unal == EINVAL && b_outwin == EINVAL && b_twice == EINVAL && b_devreg == -16;
    serial_println!(
        "  experimental syscall-demo: shared memory -- init mapped a fresh region (rc {}), kernel-zeroed page sums to {} (expected 0); lent to blk as READ|WRITE ({}) and a second one READ-only ({}); blk map rc {}/{} ; BLK_READ into a read-only mapping {} ({}); unaligned {} / outside window {} / already mapped {} (all -20); device cap as region {} (-16)",
        i_map, i_zero, i_m3, i_m4, b_map, b_romap, b_roread, if disk_present { "-11 EFAULT" } else { "-17, no disk" }, b_unal, b_outwin, b_twice, b_devreg
    );
    let c_w = read_data(5, 0x08) as i64;
    let c_r0 = read_data(5, 0x10) as i64;
    let c_unw = read_data(5, 0x18) as i64;
    let c_flip = read_data(5, 0x20) as i64;
    let c_mis = read_data(5, 0x28) as i64;
    let c_again = read_data(5, 0x30) as i64;
    let c_map = read_data(5, 0x60) as i64;
    let csum_ok = c_map == 0 && if disk_present {
        c_w == 0 && c_r0 == 0 && c_unw == -21 && c_flip == -21 && c_mis == -22 && c_again == 0
    } else {
        [c_w, c_r0, c_unw, c_flip, c_mis, c_again].iter().all(|&r| r == ENODEV)
    };
    serial_println!(
        "  experimental syscall-demo: checksummed block layer (user space, CRC-32 computed in ring 3) -- write {}, read-back {}, never-written block {} (-21), one flipped bit {} (-21), misdirected block {} (-22), good block re-read {}",
        c_w, c_r0, c_unw, c_flip, c_mis, c_again
    );
    let k_rep = read_data(6, 0x08) as i64;
    let k_twice = read_data(6, 0x10) as i64;
    let k_wrong = read_data(6, 0x18) as i64;
    let k_bad = read_data(6, 0x20) as i64;
    let k_len = read_data(5 + 1, 0x38);
    let q_len = read_data(7, 0x08) as i64;
    let q_ok = read_data(7, 0x10);
    let q_ro = read_data(7, 0x18) as i64;
    let q_dead = read_data(7, 0x20) as i64;
    let q_dead2 = read_data(7, 0x28) as i64;
    let q_big = read_data(7, 0x30) as i64;
    let (calls, cwakes) = (CALLS, CALL_WAKES);
    let call_ok = q_len == 8 && q_ok == 1 && q_ro == EFAULT && q_dead == -5 && q_dead2 == -5 && q_big == -6
        && k_rep == 0 && k_twice == -2 && k_wrong == -16 && k_bad == EFAULT && k_len == 8
        && calls as i64 == 303 + 6 + 48 + 5 + 99 + 19 + 25 + KBD_CALLS + read_data(16, 0x70) as i64 && cwakes == calls && !DEADLOCK; // 5 + 13 = init's `go` calls to the five shell processes and its 12 input events + quit to the interactive one (1-35/1-36); also:  processes (1-35); 2 (call/reply demo) + 8 (block-service client) + 134 + 140 (NyFS clients) + 19 (ATA driver client) + every sector request the two NyFS services sent to their atad instances
    serial_println!(
        "  experimental syscall-demo: call/reply -- caller got {} bytes back (+1 each: {}); server's bad-buffer REPLY {} (-11, cap NOT consumed), REPLY via non-reply handle {} (-16), real reply {}, second reply {} (-2, one-shot); caller with read-only reply buffer {} (-11); server exited without replying -> caller woken with {} (-5, {} wake(s) of {} parked call(s)), call to the dead server {} (-5), oversize request {} (-6)",
        q_len, if q_ok == 1 { "correct" } else { "WRONG" }, k_bad, k_wrong, k_rep, k_twice, q_ro, q_dead, cwakes, calls, q_dead2, q_big
    );
    let f_w = read_data(9, 0x08) as i64;
    let f_r = read_data(9, 0x10) as i64;
    let f_pat = read_data(9, 0x18);
    let f_unw = read_data(9, 0x20) as i64;
    let f_zero = read_data(9, 0x28);
    let f_op = read_data(9, 0x30) as i64;
    let f_range = read_data(9, 0x38) as i64;
    let f_devep = read_data(9, 0x40) as i64;
    let f_devnone = read_data(9, 0x48) as i64;
    let f_shut = read_data(9, 0x50) as i64;
    let f_flip = read_data(9, 0x80) as i64;
    let f_flipz = read_data(9, 0x88);
    let f_mis = read_data(9, 0x90) as i64;
    let f_misz = read_data(9, 0x98);
    let f_served = read_data(8, 0x68);
    serial_println!(
        "  experimental syscall-demo: block service vs. REAL corrupt data -- bit-flipped sector -> {} (-21), payload area {}; misdirected sector -> {} (-22), payload area {} (non-zero bytes were on disk; the service withheld them)",
        f_flip, if f_flipz == 1 { "all zero" } else { "LEAKED" }, f_mis, if f_misz == 1 { "all zero" } else { "LEAKED" }
    );
    let svc_ok = f_devep == -16 && f_devnone == -2 && f_op == -13 && f_range == -19 && f_shut == 0 && f_served == 8
        && read_data(8, 0x60) == 0 && read_data(9, 0x58) == 0
        && if disk_present {
            f_w == 0 && f_r == 0 && f_pat == 1 && f_unw == -21 && f_zero == 1
                && f_flip == -21 && f_flipz == 1 && f_mis == -22 && f_misz == 1
        } else {
            f_w == ENODEV && f_r == ENODEV && f_unw == ENODEV && f_zero == 1
                && f_flip == ENODEV && f_flipz == 1 && f_mis == ENODEV && f_misz == 1
        };
    serial_println!(
        "  experimental syscall-demo: block service (request protocol over CALL; payload via shared memory; client has NO device cap) -- {} requests served; WRITE block 3 -> {}, READ back -> {} (payload {}); never-written block -> {} (-21) with payload area {}; unknown op {} (-13); out-of-range block {} (-19); client BLK_READ via endpoint cap {} (-16) / empty slot {} (-2); shutdown {}",
        f_served, f_w, f_r, if f_pat == 1 { "verified" } else { "WRONG" }, f_unw, if f_zero == 1 { "all zero (unverified bytes withheld)" } else { "NOT ZEROED" }, f_op, f_range, f_devep, f_devnone, f_shut
    );
    let cow: [i64; 8] = core::array::from_fn(|t| read_data(10, 0x08 + 8 * t as u64) as i64);
    let cow_map = read_data(10, 0x60) as i64;
    // expected: crash before/after A'/after A'+B' -> old(1); complete -> new(2); torn superblock -> old(1);
    // bit-rot in the new data -> corruption reported (-1); naive in-place overwrite -> state destroyed (-1, the CONTROL); untouched -> 1
    let cow_expected: [i64; 8] = if disk_present { [1, 1, 1, 2, 1, -1, -1, 1] } else { [-2; 8] };
    let cow_ok = cow_map == 0 && cow == cow_expected;
    serial_println!(
        "  experimental syscall-demo: copy-on-write commit protocol (user space) -- mount results {:?}; crash before write/after A'/after A'+B' -> {},{},{} (all OLD=1), commit completes -> {} (NEW=2), torn superblock -> {} (falls back to OLD=1), bit-rot after commit -> {} (-1 reported), naive in-place overwrite control -> {} (-1: old state destroyed), untouched -> {}",
        cow, cow[0], cow[1], cow[2], cow[3], cow[4], cow[5], cow[6], cow[7]
    );
    let snap: [i64; 12] = [0x08u64, 0x10, 0x18, 0x20, 0x28, 0x30, 0x38, 0x40, 0x48, 0x50, 0x58, 0x68].map(|o| read_data(11, o) as i64);
    let snap_map = read_data(11, 0x60) as i64;
    // live=7, S0 still v1, S1 still v3 after 5 more updates; each snapshot = exactly 1 sector write;
    // snapshotted pair never reused; corrupt table -> update refused (-3), live unchanged (1); 4th snapshot -4;
    // deleted snapshot's pair reclaimed (1), reading it -> -5
    let snap_expected: [i64; 12] = [7, 1, 3, 1, 1, 1, -3, 1, -4, 7, 1, -5];
    let snap_ok = snap_map == 0 && (!disk_present || snap == snap_expected);
    serial_println!(
        "  experimental syscall-demo: snapshots (user space, on the copy-on-write volume) -- results {:?}; live version {} after 6 updates; snapshot S0 reads {} (was v1) and S1 reads {} (was v3) while live moved on; creating a snapshot = {} + {} sector write(s) (O(1), no data copied); snapshotted pair untouched {}; corrupt snapshot table -> update refused {} (-3), live still {}; 4th snapshot {} (-4); deleted snapshot: pair reclaimed {}, read -> {} (-5)",
        snap, snap[0], snap[1], snap[2], snap[3], snap[4], snap[5], snap[6], snap[7], snap[8], snap[10], snap[11]
    );
    let tr: [i64; 15] = [0x08u64, 0x10, 0x18, 0x20, 0x28, 0x30, 0x38, 0x40, 0x48, 0x50, 0x58, 0x68, 0x70, 0x78, 0x60].map(|o| read_data(12, o) as i64);
    // writes per put (first, 40th) = 4; sum(R1)=4780; R1[5]=105; 20 overwrites = 80 writes; live sum 24780;
    // R1 unchanged (4780, 105); live[5]=1105; crash-before-flip put = 3 writes, live[7] still 1107;
    // shared-leaf corruption: R1[33] -1, R1[5] 105, live[33] -1 (shared block), region map rc 0
    let tr_expected: [i64; 15] = [4, 4, 4780, 105, 80, 24780, 4780, 105, 1105, 3, 1107, -1, 105, -1, 0];
    let tree_ok = !disk_present || tr == tr_expected;
    serial_println!(
        "  experimental syscall-demo: copy-on-write metadata tree (user space) -- results {:?}; one-key update = {} writes at 1 key and {} writes at 40 keys (constant); snapshot root R1 sums to {} and R1[5]={} ; after 20 overwrites ({} writes) live sums {} while R1 still sums {} (R1[5]={}, live[5]={}); put without the superblock flip = {} writes and live[7] stays {}; corrupting a leaf SHARED by R1 and live: R1[33]={}, R1[5]={}, live[33]={} (shared blocks are shared fate)",
        tr, tr[0], tr[1], tr[2], tr[3], tr[4], tr[5], tr[6], tr[7], tr[8], tr[9], tr[10], tr[11], tr[12], tr[13]
    );
    let gc: [i64; 13] = [0x08u64, 0x10, 0x18, 0x20, 0x28, 0x30, 0x38, 0x40, 0x48, 0x50, 0x70, 0x60, 0x58].map(|o| read_data(13, o) as i64);
    // free(with pin, before churn)=84; 0 failures in 200 overwrites; high-water <= 100 of 128 although ~720
    // blocks were allocated in total; live sum 47180; pinned R1 still 4780; free with pin 40; free after unpin 84;
    // crashed update leaves free count unchanged (84: the leak is reclaimed); live[3]=1163; corrupt live leaf -> update refused -7
    let gc_ok = !disk_present || (gc[0] == 84 && gc[1] == 0 && gc[2] > 0 && gc[2] <= 100 && gc[3] == 47180 && gc[4] == 4780
        && gc[5] == 40 && gc[6] == 84 && gc[7] == 84 && gc[8] == 1163 && gc[9] == -7 && gc[10] == 0 && gc[11] == 0 && gc[12] == -8);
    serial_println!(
        "  experimental syscall-demo: free-space reclamation (derived by marking live + pinned trees) -- results {:?}; 40 inserts + 200 overwrites = {} failures; high-water {} of 128 blocks although ~720 blocks were allocated in total; live sum {}, pinned snapshot still sums {} (not a block of it was ever reused); free blocks: {} before churn, {} with the snapshot still pinned, {} after unpinning (its blocks reclaimed); crashed update -> free blocks {} (leak reclaimed, was 84); live[3]={}; corrupt live leaf -> update {} (refused, -7); torn pin sector -> update {} (refused, -8)",
        gc, gc[1], gc[2], gc[3], gc[4], gc[0], gc[5], gc[6], gc[7], gc[8], gc[9], gc[12]
    );
    let s_empty = read_data(1, 0x60);
    let c_empty = read_data(2, 0x68);
    let (blocks, wakes, dead, killed_parked) = (BLOCKS, WAKES, DEADLOCK, KILLED_PARKED);
    serial_println!(
        "  experimental syscall-demo: blocking RECV -> {} task(s) parked, {} woken by a message + {} killed while parked with their container, EMPTY seen by user code: server {}, client {} (all polling gone), deadlock detected: {}",
        blocks, wakes, killed_parked, s_empty, c_empty, dead
    );
    let block_ok = blocks >= 1 && wakes + killed_parked + LIVE_PARKED == blocks && killed_parked == 3 && s_empty == 0 && c_empty == 0 && !dead;
    let s_got_h = read_data(1, 0x20);
    let s_regrant = read_data(1, 0x28) as i64;
    let e_widen = read_data(2, 0x58) as i64;
    let e_badxfer = read_data(2, 0x60) as i64;
    serial_println!(
        "  experimental syscall-demo: capability transfer -> server received reply-cap handle {} (delivered in rdx, not payload); client widening attempt {} (-8), transfer of unheld handle {} (-2), server re-grant of attenuated cap {} (-3)",
        s_got_h as i64, e_widen, e_badxfer, s_regrant
    );
    let xfer_ok = s_got_h < 8 && s_regrant == -3 && e_widen == -8 && e_badxfer == -2;
    // NyFS service boundary: two volumes, two services (tasks 14/15), two clients (16/17)
    let ny_init: [i64; 12] = [0x810u64, 0x818, 0x820, 0x828, 0x830, 0x870, 0x890, 0x898, 0x8b0, 0x8b8, 0x8c0, 0x8c8].map(|o| read_data(0, o) as i64);
    let ny_h: [i64; 3] = [0x800u64, 0x808, 0x838].map(|o| read_data(0, o) as i64);
    let ny_cli = |t: usize| -> [i64; 18] { [0x08u64, 0x10, 0x18, 0x20, 0x28, 0x30, 0x38, 0x40, 0x48, 0x50, 0x58, 0x60, 0x68, 0x70, 0x78, 0x80, 0x88, 0x90].map(|o| read_data(t, o) as i64) };
    let ny_a = ny_cli(17);
    let ny_b = ny_cli(18);
    // the five older storage services now use the shared driver through their OWN badge/window; the driver recorded each client's LBAs
    let (st_min, st_max): (Vec<i64>, Vec<i64>) = ((0..7).map(|i| read_data(16, 0x800 + 8 * i) as i64).collect(), (0..7).map(|i| read_data(16, 0x840 + 8 * i) as i64).collect());
    let st_win: [(i64, i64); 5] = [(200, 223), (400, 527), (600, 727), (800, 1100), (1200, 1360)];
    let st_ok = (0..5).all(|k| st_min[k + 2] >= st_win[k].0 && st_max[k + 2] <= st_win[k].1 && st_max[k + 2] > st_min[k + 2]);
    serial_println!("  experimental syscall-demo: older storage services on the shared driver -- LBAs touched per service (min..max) blksvc {}..{}, cowfs {}..{}, snapfs {}..{}, treefs {}..{}, gcfs {}..{}; all inside their own windows (200..223, 400..527, 600..727, 800..1100, 1200..1360) -- {}",
        st_min[2], st_max[2], st_min[3], st_max[3], st_min[4], st_max[4], st_min[5], st_max[5], st_min[6], st_max[6], if st_ok { "OK" } else { "FAILED" });
    let ny_names = |t: usize| -> [i64; 70] { [0xa0u64, 0xa8, 0xb0, 0xb8, 0xc0, 0xc8, 0xd0, 0xd8, 0xe0, 0xe8, 0xf0, 0xf8, 0x100, 0x108, 0x110, 0x118, 0x120, 0x128, 0x130, 0x138, 0x140, 0x148, 0x150, 0x158, 0x160, 0x168, 0x170, 0x178, 0x180, 0x188, 0x190, 0x198, 0x1a0, 0x1a8, 0x1b0, 0x1b8, 0x1c0, 0x1c8, 0x1d0, 0x1d8, 0x1e0, 0x1e8, 0x1f0, 0x1f8, 0x240, 0x248, 0x250, 0x258, 0x260, 0x268, 0x270, 0x278, 0x280, 0x288, 0x290, 0x298, 0x2a0, 0x2a8, 0x2b0, 0x2b8, 0x2c0, 0x2c8, 0x2d0, 0x2d8, 0x2e0, 0x2e8, 0x2f0, 0x2f8, 0x320, 0x328].map(|o| read_data(t, o) as i64) };
    let ny_na = ny_names(17);
    let ny_nb = ny_names(18);
    // alpha=vb+1 then WRITE vb+11; gamma vb+3; delta vb+4 reuses beta's key 2; LIST order = slot order: alpha, delta, gamma
    let ny_nm = |n: &[i64; 70], vb: i64| n[0] == 0 && n[1] == 0 && n[2] == 0 && n[3] == -9 && n[4] == 0 && n[5] == 0 && n[6] == 0 && n[7] == 0
        && n[8] == -15 && n[9] == -15 && n[10] == 0 && n[11] == 2 && n[12] == 0x6168706c61 && n[13] == 0x61746c6564 && n[14] == 0x616d6d6167
        && n[15] == -15 && n[16] == -20 && n[17] == -20 && n[18] == 16 && n[19] == -6 && n[20] == -19 && n[21] == -19
        && n[22] == vb + 3 && n[23] == vb + 4 && n[24] == vb + 1 && n[25] == vb + 11 && n[26] == 75
        && n[27] == 0 && n[28] == 0 && n[29] == 0 && n[30] == 1500 && n[31] == 1 && n[32] == 1 && n[33] == 0 && n[34] == 1500 && n[35] == 1
        && n[36] == 0 && n[37] == 700 && n[38] == 1 && n[39] == 1 && n[40] == -19 && n[41] == -15 && n[42] == -15 && n[43] == 0
        && n[44] == 0 && n[45] == -18 && n[46] == 1 && n[47] == 0 && n[48] == -18 && n[49] == vb + 104 && n[55] == -15
        // n[50..55] = corrupt A, READ, value, repairs, corrupt B ; n[56..] = LIST status, name word, repairs, then the B-only block
        && n[50] == 0 && n[51] == 0 && n[52] == vb + 11 && n[53] == 2 && n[54] == 0
        && n[56] == 0 && n[57] == 0x6168706c61 && n[58] == 3
        && n[65] == 0 && n[66] == 0 && n[67] == 0 && n[68] == vb + 77 && n[69] == 1
        && if vb == 2000 { n[59] == 0 && n[60] == 0 && n[61] == -18 && n[62] == -18 && n[63] == -18 && n[64] == -18 } else { n[59] == 0 && n[60] == 0 && n[61] == 0 && n[62] == 0 && n[63] == 0 && n[64] == 0 };
    let ny_blk: [i64; 4] = [0x970u64, 0x978, 0x980, 0x988].map(|o| read_data(0, o) as i64);
    let ny_sends: [i64; 7] = [0x990u64, 0x998, 0x9a0, 0x870, 0x878, 0x880, 0x888].map(|o| read_data(0, o) as i64);
    let ny_drv: [i64; 6] = [0x60u64, 0x68, 0x70, 0x78, 0x80, 0x00].map(|o| read_data(16, o) as i64);
    let ny_unb: [i64; 3] = [0xd88u64, 0xd90, 0x990].map(|o| read_data(0, o) as i64);
    let ny_svc: [[i64; 3]; 2] = [14usize, 15].map(|t| [read_data(t, 0x70) as i64, read_data(t, 0x68) as i64, read_data(t, 0x78) as i64]);
    let ny_one = |c: &[i64; 18], vb: i64| c[0] == 0 && c[1] == 0 && c[2] == 20 * vb + 190 + 2000 && c[3] == -19 && c[4] == -1 && c[5] == 105
        && c[6] == -19 && c[7] == -19 && c[8] == 0 && c[9] == -19 && c[10] == -19 && c[11] == -16 && c[12] == -2 && c[13] == -2 && c[14] == -16
        && c[15] == 0 && c[16] == vb + 103 && c[17] == 0;
    let ny_ok = !disk_present || (ny_h[0] >= 1 && ny_h[1] >= 1 && ny_h[2] >= 1 && ny_init == [-8, -8, -8, -8, -16, 0, 0, 0, 0, 0, 0, 0] && ny_blk == [0, 0, -14, -14] && ny_sends == [0; 7] && read_data(0, 0x9a8) as i64 >= 0 && read_data(0, 0xd78) as i64 >= 0
        && ny_drv[0] == 0 && ny_drv[1] == 0 && ny_drv[2] > 200 && ny_drv[3] == 0 && ny_drv[4] == 1 && ny_unb[0] == 16 && ny_unb[1] == -14

        && ny_one(&ny_a, 1000) && ny_one(&ny_b, 2000)
        && ny_svc[0] == [0, 134, 1400] && ny_svc[1] == [0, 140, 1600] && ny_nm(&ny_na, 1000) && ny_nm(&ny_nb, 2000));
    serial_println!(
        "  experimental syscall-demo: NyFS service boundary -- init narrowed the device capability to two volumes (handles ok: {:?}); widening attempts (beyond window, other volume, empty, overflow) and narrowing a non-device cap -> {:?} (-8 x4, -16); volume A client {:?}; volume B client {:?}; naming layer A {:?}, B {:?}; services (format, requests served, base) {:?}; init's direct BLK_READ through the narrowed cap (first, last, one-past, one-before) {:?}; the shared driver (IO_ENABLE, map, requests served, faults, unbadged refusals, -) {:?}. Clients hold only an endpoint capability; raw device access outside the volume -> -19 (refused by the volume's driver); each client sees only its own data (sums 22190 vs 42190)",
        ny_ok, ny_init, ny_a, ny_b, ny_na, ny_nb, ny_svc, ny_blk, ny_drv
    );
    // user-space ATA driver (ADR-0006): task 19 = atad, task 20 = atacli
    let at_init: [i64; 9] = [0x908u64, 0x910, 0x918, 0x920, 0x940, 0x948, 0x950, 0x958, 0x900].map(|o| read_data(0, o) as i64);
    let at_cli: [i64; 25] = [0x10u64, 0x18, 0x20, 0x28, 0x30, 0x38, 0x40, 0x48, 0x50, 0x58, 0x60, 0x68, 0x70, 0x78, 0x80, 0x88, 0x90, 0x98, 0xa0, 0xa8, 0xb0, 0xb8, 0xc0, 0x58, 0x58].map(|o| read_data(20, o) as i64);
    let at_drv: [i64; 4] = [0x60u64, 0x68, 0x70, 0x78].map(|o| read_data(19, o) as i64);
    // driver served 4 writes + 4 reads + 4 out-of-window + 1 unknown op + 5 PORTIN + shutdown = 19; 4 port probes faulted
    let at_ok = !disk_present || (at_init[..8] == [-8, -8, -20, -16, 0, 0, 0, 0] && at_init[8] >= 1
        && at_cli[0..4] == [0, 0, 0, 0] && at_cli[4..8] == [1, 1, 1, 1]
        && at_cli[8..13] == [-19, -19, -19, -19, -13] && at_cli[13] == 0 && at_cli[14..18] == [-14, -14, -14, -14]
        && at_cli[18] == 1 && at_cli[19] == 0 && at_cli[20] == -16 && at_cli[21] == -2 && at_cli[22] == 0
        && at_drv == [0, 0, 19, 4]);
    serial_println!(
        "  experimental syscall-demo: user-space ATA driver -- init: narrowing past/outside the port window {:?}, IO_ENABLE of all ports {} (-20), on the block-device cap {} (-16), grants {:?}; client: writes {:?}, verified reads {:?}, outside the driver's sector window {:?} (-19 x4, unknown op -13), PORTIN 0x1F7 {} then outside the port window {:?} (-14 x4 = CPU #GP delivered as an upcall); the client's own `in` faulted {} time(s) (error code {}); IO_ENABLE on an endpoint cap {} (-16), on an empty slot {} (-2); driver (IO_ENABLE rc, map rc, served, faults) {:?} -- {}",
        &at_init[0..2], at_init[2], at_init[3], &at_init[4..8], &at_cli[0..4], &at_cli[4..8], &at_cli[8..13], at_cli[13], &at_cli[14..18], at_cli[18], at_cli[19], at_cli[20], at_cli[21], at_drv, if at_ok { "OK" } else { "FAILED" }
    );
    // containers (NPS-007 section 3 model): tasks 21, 22 = C1 members, 23 = C2 member
    let ct_i: [i64; 23] = [0xc00u64, 0xc08, 0xc10, 0xc18, 0xc20, 0xc28, 0xc30, 0xc38, 0xc40, 0xc48, 0xc50, 0xc58, 0xc60, 0xc68, 0xc70, 0xc78, 0xc80, 0xc88, 0xc90, 0xc98, 0xca0, 0xca8, 0xcb0].map(|o| read_data(0, o) as i64);
    let ct_m1: [i64; 9] = [0x08u64, 0x10, 0x18, 0x20, 0x28, 0x30, 0x38, 0x40, 0x48].map(|o| read_data(21, o) as i64);
    let ct_m3: [i64; 2] = [0x08u64, 0x10].map(|o| read_data(23, o) as i64);
    let ct_ok = ct_i[0] == 1 && ct_i[1] == 2 && ct_i[2..5] == [-20, -20, -20] && ct_i[5] >= 0 && ct_i[6] >= 0 && ct_i[7] == -10 && ct_i[8] >= 0
        && ct_i[9..12] == [-21, -21, 0] && ct_i[12] >= 0 && ct_i[13] == 0 && ct_i[14..16] == [-21, -21] && ct_i[16] == 0
        && ct_i[17] == 2 && ct_i[18] == -5 && ct_i[19] == 0 && ct_i[20] == -20 && ct_i[21] == 1 && ct_i[22] == -21
        && ct_m1[0] == 8 && ct_m1[1] != -1 && ct_m1[2] == 8 && ct_m1[3] != -1 && ct_m1[4..] == [0, -16, -2, -14, 0xc0ffee]
        && ct_m3[0] == 8 && ct_m3[1] != -1;
    serial_println!(
        "  experimental syscall-demo: containers -- init created C1 (kinds Endpoint|SharedMem|Reply, quota 2) and C2 (Endpoint only, quota 1): ids {} {}; bad mask / zero quota / unknown container {:?} (-20 x3); spawns {} {} then over-quota {} (-10); device cap into C1 {} and I/O-port cap into C1 {} (-21 each), endpoint {} and region {} accepted (0); into C2: region {} and device {} refused (-21), endpoint {} accepted; C1 member saw lens {}/{} and mapped its region ({}), BLK_READ on its own endpoint {} (-16) / empty handle {} (-2), SPAWN {} (-14), shared marker {:#x}; kill C1 -> {} (2), send to the dead member {} (-5), kill again {} (0), kill root {} (-20), kill C2 {} (1); CALL into a C2 server (no Reply kind) {} (-21) -- {}",
        ct_i[0], ct_i[1], &ct_i[2..5], ct_i[5], ct_i[6], ct_i[7], ct_i[9], ct_i[10], ct_i[11], ct_i[13], ct_i[14], ct_i[15], ct_i[16],
        ct_m1[0], ct_m1[2], ct_m1[4], ct_m1[5], ct_m1[6], ct_m1[7], ct_m1[8], ct_i[17], ct_i[18], ct_i[19], ct_i[20], ct_i[21], ct_i[22], if ct_ok { "OK" } else { "FAILED" }
    );
    // badged endpoints: task 24 = badsrv, 25/26 = its two clients
    let bd_i: [i64; 16] = [0xcd8u64, 0xce0, 0xce8, 0xcf0, 0xcf8, 0xd00, 0xd08, 0xd10, 0xd18, 0xd38, 0xd48, 0xd50, 0xd58, 0xcc0, 0xcc8, 0xcd0].map(|o| read_data(0, o) as i64);
    let bd_srv: [i64; 5] = [0x08u64, 0x100, 0x138, 0x148, 0x178].map(|o| read_data(24, o) as i64);
    let bd_plain: [i64; 4] = [0x238u64, 0x248, 0x200, 0x278].map(|o| read_data(24, o) as i64);
    let bd_a: [i64; 5] = [0x08u64, 0x10, 0x28, 0x20, 0x18].map(|o| read_data(25, o) as i64);
    let bd_b: [i64; 5] = [0x08u64, 0x10, 0x28, 0x20, 0x18].map(|o| read_data(26, o) as i64);
    let bd_ok = bd_i[0] >= 0 && bd_i[1] >= 0 && bd_i[0] != bd_i[1] && bd_i[2..6] == [-9, -9, -16, -2] && bd_i[6] == 0 && bd_i[7] == 0 && bd_i[8] == 16
        && bd_i[9] == 7 && bd_i[10] == 9 && bd_i[11] == 0 && bd_i[12] >= 1
        && bd_srv == [6, 1, 3, 2, 0] && bd_plain == [1, 1, 0, 0] && bd_a[0] == 16 && bd_a[1] == 7 && bd_a[2] == -3 && bd_b[0] == 16 && bd_b[1] == 9 && bd_b[2] == -3;
    serial_println!(
        "  experimental syscall-demo: badged endpoints -- init minted badge 7 and 9 copies of the server's capability ({} {}); re-badging a badged cap {} (-9), badge 0 {} (-9), badging a device cap {} (-16), an empty slot {} (-2); init's own calls saw badges {} / {} / {} (7/9/0); client A (cap attenuated to SEND in transit) saw badge {} and client B badge {} (7, 9: the badge survives a transfer), re-badge attempts {} / {} (-3, no GRANT); the server's per-badge counts: unbadged {}, 7 -> {}, 9 -> {}, total {} (1, 3, 2, 6); plain SENDs through the badged caps were tagged too: badge 7 x{}, badge 9 x{} -- {}",
        bd_i[0], bd_i[1], bd_i[2], bd_i[3], bd_i[4], bd_i[5], bd_i[9], bd_i[10], bd_i[11], bd_a[1], bd_b[1], bd_a[2], bd_b[2], bd_srv[1], bd_srv[2], bd_srv[3], bd_srv[0], bd_plain[0], bd_plain[1], if bd_ok { "OK" } else { "FAILED" }
    );
    // user-space PE loader in a container (task 27 = good program's loader/app, 28..35 = the eight broken variants' loaders)
    let pe_hdr: [i64; 4] = [0xe00u64, 0xe08, 0xe10, 0xfa0].map(|o| read_data(0, o) as i64);
    let pe_dev = [read_data(0, 0xfa0) as i64, read_data(0, 0xfa8) as i64];
    let pe_sz: Vec<i64> = (0..9).map(|i| read_data(0, 0xf10 + 16 * i) as i64).collect();
    let pe_st: Vec<i64> = (0..9).map(|i| read_data(0, 0xf18 + 16 * i) as i64).collect();
    let pe_ld: Vec<(i64, i64)> = (27..36).map(|t| (read_data(t, 0x08) as i64, read_data(t, 0x30) as i64)).collect();
    let pe_app: [i64; 15] = [0x100u64, 0x108, 0x110, 0x118, 0x120, 0x128, 0x130, 0x138, 0x140, 0x148, 0x150, 0x158, 0x160, 0x168, 0x170].map(|o| read_data(27, o) as i64);
    let pe_pages: Vec<u32> = (27..36).map(|t| TASKS[t].img_pages).collect();
    let pe_ok = pe_hdr[0] == 3 && pe_hdr[1] >= 0 && pe_hdr[2] == 0 && pe_dev == [-21, -21] && pe_sz[0] >= 1024 && pe_sz[0] <= 4096 && pe_sz.iter().all(|&x| x == pe_sz[0])
        && pe_st[0] == 0 && pe_st[1..] == [-20; 8] && pe_ld[0] == (0, 0) && pe_ld[1..].iter().all(|&(st, m)| st == -20 && m == 0)
        && pe_pages[0] == 4 && pe_pages[1..].iter().all(|&p| p == 0)
        && pe_app[0] == 0xFEEDC0DEFEEDC0DEu64 as i64 && pe_app[1] == -16 && pe_app[2] == -14 && pe_app[3..8] == [-20, -20, -20, -20, -10]
        && pe_app[13] == -20 && pe_app[14] == -20 && pe_app[8] == 0 && pe_app[9] == 0xabc && pe_app[10] == 0 && pe_app[11] == -20 && pe_app[12] >= 0;
    serial_println!(
        "  experimental syscall-demo: user-space PE loader -- container C3 id {}; hello.exe (a real PE32+ file, {} bytes, 3 sections) parsed, mapped (MEM_MAP) and protected (MEM_PROTECT) by `peload`, status {}; its entry point ran in ring 3 inside the container: .data marker {:#x}, BLK_READ {} (-16), SPAWN {} (-14), MEM_MAP W+X {} / PROTECT .text to W+X {} / MEM_MAP over .text {} / MEM_MAP of a free page just below the window {} (-20 each), over the loader's page {} / just above the window {} (-20), 600-page MEM_MAP {} (-10, quota), one fresh page {} then value {:#x} then read-only {}, PROTECT of an unmapped page {} (-20), SHM_CREATE {} (>= 0, SharedMem admitted); block-device cap and I/O-port cap sent into the container {:?} (-21 each); the 8 broken variants (bad MZ / PE signature / machine / base relocations / W+X section / ImageBase / raw data out of file / entry outside code) -> {:?} (-20 each) and mapped nothing (image pages per loader: {:?}) -- {}",
        pe_hdr[0], pe_sz[0], pe_st[0], pe_app[0], pe_app[1], pe_app[2], pe_app[3], pe_app[4], pe_app[5], pe_app[6], pe_app[13], pe_app[14], pe_app[7], pe_app[8], pe_app[9], pe_app[10], pe_app[11], pe_app[12], pe_dev, &pe_st[1..], pe_pages, if pe_ok { "OK" } else { "FAILED" }
    );
    // Win32 import layer (1-23): task 36 = pewin.exe's loader/app, 37..41 = the five broken-import loaders
    let pw: [i64; 9] = [0x08u64, 0x180, 0x188, 0x190, 0x198, 0x1a0, 0x1a8, 0x7f0, 0x7f8].map(|o| read_data(36, o) as i64);
    let pw_log: Vec<u8> = (0..28u64).flat_map(|i| read_data(36, 0x800 + 8 * i).to_le_bytes()).take(46).collect();
    let pw_bad: Vec<(i64, i64)> = (37..42).map(|t| (read_data(t, 0x08) as i64, read_data(t, 0x180) as i64)).collect();
    let pw_pages: Vec<u32> = (36..42).map(|t| TASKS[t].img_pages).collect();
    let pw_ok = pw[0] == 0 && pw[1] == 42 && pw[2] == 0x5001_1000_000 && pw[3] == 0x1234_5678 && pw[4] == 1 && pw[5] == 0 && pw[6] == 0x5001_1002_000 && pw[7] == 0x5001_1003_000
        && pw[8] == 46 && pw_log == b"Hello from a Windows program running on Nyrqis"
        && pw_bad.iter().all(|&(st, ex)| st == -20 && ex == 0) && pw_pages[0] == 5 && pw_pages[1..].iter().all(|&p| p == 0);
    serial_println!(
        "  experimental syscall-demo: Win32 imports -- pewin.exe (PE32+ with an import table: KERNEL32.dll OutputDebugStringA / VirtualAlloc / VirtualProtect / ExitProcess) was resolved by `peload` against user-space shims and run: loader status {}, ExitProcess code {} (42), VirtualAlloc -> {:#x} (arena), value read back {:#x}, VirtualProtect -> {} (1), PAGE_EXECUTE_READWRITE alloc {} (0, W^X), PAGE_EXECUTE_READ alloc {:#x}, arena pointer {:#x}, OutputDebugStringA log {} bytes {:?}; 5 broken import tables (unknown DLL / unknown function / ordinal import / IAT slot in a read-only section / thunk RVA outside the image) -> {:?} (-20 each, the program never ran) and mapped nothing (image pages per loader: {:?}) -- {}",
        pw[0], pw[1], pw[2], pw[3], pw[4], pw[5], pw[6], pw[7], pw[8], core::str::from_utf8(&pw_log).unwrap_or("?"), pw_bad.iter().map(|x| x.0).collect::<Vec<_>>(), pw_pages, if pw_ok { "OK" } else { "FAILED" }
    );
    // base relocation (1-24): task 42 = pereloc.exe (linked at 0x140000000, loaded at 0x50012000000), 43..47 = the five broken .reloc variants
    const RMSG: &[u8] = b"Reloc OK! (this program was linked at 0x140000000)";
    let rl: [i64; 9] = [0x08u64, 0x180, 0x1c0, 0x1c8, 0x1d0, 0x1d8, 0x1e0, 0x7f8, 0x7f0].map(|o| read_data(42, o) as i64);
    let rl_log: Vec<u8> = (0..28u64).flat_map(|i| read_data(42, 0x800 + 8 * i).to_le_bytes()).take(RMSG.len()).collect();
    let rl_bad: Vec<(i64, i64)> = (43..48).map(|t| (read_data(t, 0x08) as i64, read_data(t, 0x180) as i64)).collect();
    let rl_pages: Vec<u32> = (42..48).map(|t| TASKS[t].img_pages).collect();
    let rl_ok = rl[0] == 0 && rl[1] == 43 && rl[2] == rl[3] && rl[3] == rl[4] && rl[6] == 0x5001_2001_000 && rl[2] > rl[6] && rl[2] < 0x5001_2002_000 + 0x1000
        && rl[5] == i64::from_le_bytes([RMSG[0], RMSG[1], RMSG[2], RMSG[3], RMSG[4], RMSG[5], RMSG[6], RMSG[7]]) && rl[7] == RMSG.len() as i64 && rl_log == RMSG
        && rl_bad.iter().all(|&(st, ex)| st == -20 && ex == 0) && rl_pages[0] == 3 && rl_pages[1..].iter().all(|&p| p == 0);
    serial_println!(
        "  experimental syscall-demo: base relocation -- pereloc.exe (PE32+ linked at ImageBase 0x140000000, outside the image window, with a .reloc directory) was loaded by `peload` at {:#x} and RELOCATED (DIR64 delta applied): loader status {}, entry point {:#x} (load base + 0x1000), msg via movabs / via rip-relative lea / via a `.quad` table entry {:#x} {:#x} {:#x} (all equal), first bytes read through the relocated pointer {:#x}, relocated import slots called OutputDebugStringA ({} bytes, {:?}) and ExitProcess code {} (43); 5 broken .reloc tables (HIGHLOW type / block page outside the image / block larger than the directory / no directory at all / directory RVA outside the image) -> {:?} (-20 each, never ran) and mapped nothing (image pages per loader: {:?}) -- {}",
        0x5001_2000_000u64, rl[0], rl[6], rl[2], rl[3], rl[4], rl[5], rl[7], core::str::from_utf8(&rl_log).unwrap_or("?"), rl[1], rl_bad.iter().map(|x| x.0).collect::<Vec<_>>(), rl_pages, if rl_ok { "OK" } else { "FAILED" }
    );
    // Android DEX (1-25): task 48 = hello.dex's loader/interpreter, 49..57 = the nine refused variants, 58 = the runaway-loop variant, 59 = ops.dex
    let dx: [i64; 5] = [0x08u64, 0x180, 0x188, 0x190, 0x28].map(|o| read_data(48, o) as i64);
    let dx_bad: Vec<(i64, i64, i64)> = (49..58).map(|t| (read_data(t, 0x08) as i64, read_data(t, 0x180) as i64, read_data(t, 0x188) as i64)).collect();
    let dx_loop: [i64; 4] = [0x08u64, 0x180, 0x188, 0x190].map(|o| read_data(58, o) as i64);
    let dxo: [i64; 5] = [0x08u64, 0x180, 0x188, 0x190, 0x28].map(|o| read_data(59, o) as i64);
    let dxc: [i64; 4] = [0x08u64, 0x180, 0x188, 0x190].map(|o| read_data(60, o) as i64);
    let dxc_bad: Vec<(i64, i64, i64)> = (61..65).chain(66..67).chain(68..69).map(|t| (read_data(t, 0x08) as i64, read_data(t, 0x180) as i64, read_data(t, 0x188) as i64)).collect();
    let dxc2: [i64; 4] = [0x08u64, 0x180, 0x188, 0x190].map(|o| read_data(67, o) as i64);
    let dxc_rec: [i64; 4] = [0x08u64, 0x180, 0x188, 0x190].map(|o| read_data(65, o) as i64);
    let dx_pages: Vec<u32> = (48..69).map(|t| TASKS[t].img_pages).collect();
    let dx_ok = dx[0] == 0 && dx[1] == 172 && dx[2] == 0 && dx[3] == 48 && dx[4] == 4
        && dx_bad.iter().all(|&(st, res, run)| st == -20 && res == 0 && run == 0)
        && dx_loop == [0, 0, -62, 100_001] && dxo == [0, 1_542_886_669, 0, 69, 8] && dxc == [0, 736, 0, 52] && dxc_bad.iter().all(|&(st, res, run)| st == -20 && res == 0 && run == 0) && dxc2 == [0, 0, 0, 4] && dxc_rec == [0, 0, -75, 43] && dx_pages.iter().all(|&p| p == 0);
    serial_println!(
        "  experimental syscall-demo: Android DEX -- `dexload` (user space, inside container C3) checked hello.dex (a real DEX 035: header, Adler-32, id tables, class_def, class_data, code_item), VERIFIED its bytecode (supported Dalvik subset, register and branch-target checks) and ran `Main.run()I` in a sandboxed interpreter whose only state is 16 registers: loader status {}, result {} (172 = (1+..+10)*3+7), run status {}, {} steps (48), registers_size {}; 9 broken files (bad magic / checksum / endian tag / file_size / section offset / unsupported opcode / branch target outside the code / branch into the middle of an instruction / register >= registers_size) -> {:?} (status, result, run: -20 0 0 each, never ran); ops.dex (every other supported opcode: const/4 and const/16 negative, const, move, sub-int, mul-int, goto, all 12 if-test/if-testz comparisons in both outcomes, add-int/lit16/lit8 with negative literals) -> status {}, result {} (1542886669, equal to an interpreter-free computation on the host), {} steps (69); calls.dex (THREE methods, invoke-static + move-result, argument passing in the callee's last registers, recursion six deep: add3(5,7,4) + fact(6)) -> status {}, result {} (736 = 16 + 720, from math on the host), {} steps (52); 6 broken call files (argument count != callee's / method not in the class / argument register out of range / ins > registers / zero-argument call of a method not in the class / a callee with ins 3 > registers 1 whose body touches no register) -> {:?} (-20 0 0 each; the valid twin of the last one, registers 3, runs: status {}, steps {} (0, 4)); unbounded recursion (valid code) -> status {}, run status {} (-75, frame limit 12), {} steps (43); a verified but endless loop -> status {}, run status {} (-62, budget), {} steps (100001); no image pages mapped by any of them ({:?}) -- {}",
        dx[0], dx[1], dx[2], dx[3], dx[4], dx_bad, dxo[0], dxo[1], dxo[3], dxc[0], dxc[1], dxc[3], dxc_bad, dxc2[0], dxc2[3], dxc_rec[0], dxc_rec[2], dxc_rec[3], dx_loop[0], dx_loop[2], dx_loop[3], dx_pages, if dx_ok { "OK" } else { "FAILED" }
    );
    // Display (Phase 7 / 1-27): tasks 69 = dirty writer, 70 = writer (rows 0..192), 71 = second writer (rows 192..384), 72 = read-only reader of band 1, 73 = read-only reader of band 2, 74 = process in a container without FrameBuffer
    let gi: [i64; 9] = [0xfb0u64, 0xfb8, 0xfc0, 0xfc8, 0xfd0, 0xfd8, 0xfe0, 0xfe8, 0xff0].map(|o| read_data(0, o) as i64);
    let gi_n = read_data(0, 0xff8) as i64;
    let gh: [u32; 5] = [69usize, 70, 72, 71, 73].map(|t| read_data(t, 0x80) as u32);
    let g_dirty: [i64; 6] = [0x10u64, 0x20, 0x28, 0x38, 0x40, 0x78].map(|o| read_data(69, o) as i64);
    let g_w: [i64; 12] = [0x10u64, 0x20, 0x28, 0x30, 0x38, 0x40, 0x48, 0x50, 0x58, 0x60, 0x68, 0x78].map(|o| read_data(70, o) as i64);
    let g_r: [i64; 12] = [0x10u64, 0x20, 0x28, 0x30, 0x38, 0x40, 0x48, 0x50, 0x58, 0x60, 0x68, 0x78].map(|o| read_data(72, o) as i64);
    let g_w2: [i64; 12] = [0x10u64, 0x20, 0x28, 0x30, 0x38, 0x40, 0x48, 0x50, 0x58, 0x60, 0x68, 0x78].map(|o| read_data(71, o) as i64);
    let g_r2: [i64; 12] = [0x10u64, 0x20, 0x28, 0x30, 0x38, 0x40, 0x48, 0x50, 0x58, 0x60, 0x68, 0x78].map(|o| read_data(73, o) as i64);
    let g_info: [u64; 3] = [0x100u64, 0x108, 0x110].map(|o| read_data(70, o));
    let g_n: [i64; 5] = [0x10u64, 0x18, 0x20, 0x28, 0x78].map(|o| read_data(74, o) as i64);
    let have_fb = FB.is_some();
    let (fbw, fbh, fbp) = FB.map_or((0u64, 0u64, 0u64), |f| (f.width as u64, f.height as u64, f.pitch as u64));
    // the HIG layout, sampled: hairline separator, systemBackground, gray tab icon, SELECTED (blue) tab icon, primary button, last row of the band, empty area
    let want: [i64; 7] = [0xC6C6C8, 0xF2F2F7, 0x8E8E93, 0x007AFF, 0x007AFF, 0xF2F2F7, 0xF2F2F7];
    let gfx_ok = !have_fb || (gi[0] >= 3 && gi[1] >= 0 && gi[2] == -8 && gi[3] == -21 && gi[4] == -16 && gi[5] == -2 && gi[6] == -20 && gi[7] >= 0 && gi[8] >= 0 && gi_n >= 0
        && g_dirty == [4, 0, 0, 0xA5A5A5, 0xA5A5A5, 0]
        && gh[0] == flat_band_hash(0xA5A5A5) && gh[1] == GFX_BAND_HASH && gh[2] == GFX_BAND_HASH && gh[3] == flat_band_hash(0x112233) && gh[4] == flat_band_hash(0x112233)
        && g_w[0] == 1 && g_w[1] == 0 && g_w[2] == 0 && g_w[3] == 0 && g_w[4..11] == want && g_w[11] == 0
        && g_r[0] == 2 && g_r[1] == 0 && g_r[2] == 0 && g_r[4..11] == want && g_r[11] == 0
        && g_info == [fbw | (fbh << 32), fbp | (32u64 << 32), 192u64 << 32]
        && g_w2[0] == 5 && g_w2[1] == 0 && g_w2[2] == 0 && g_w2[3] == 0 && g_w2[4..11] == [0x112233; 7] && g_r2[0] == 2 && g_r2[2] == 0 && g_r2[4..11] == [0x112233; 7]
        && fb_flags(70).map_or(false, |f| f.contains(PageTableFlags::WRITABLE | PageTableFlags::NO_CACHE | PageTableFlags::NO_EXECUTE | PageTableFlags::USER_ACCESSIBLE))
        && fb_flags(72).map_or(false, |f| !f.contains(PageTableFlags::WRITABLE) && f.contains(PageTableFlags::NO_EXECUTE | PageTableFlags::USER_ACCESSIBLE))
        && g_n == [3, -1, 0, 0, -1]);
    serial_println!(
        "  experimental syscall-demo: display (user-space `gfxsvc`, FrameBuffer capability {}): init narrowed the screen cap to the band of rows [0,192) (handle {}) and tried to widen it to 193 rows {} (-8); FB_INFO on an endpoint cap {} (-16) / empty slot {} (-2), FB_MAP outside the window {} (-20); the FrameBuffer cap sent into a container without that kind {} (-21); dirty writer flooded its band (pixel {:#x}), the writer's FIRST pixel after FB_MAP was {:#x} (kernel zeroed it) and it then drew the HIG tab bar + button: samples hairline {:#x} bg {:#x} gray icon {:#x} blue icon {:#x} button {:#x} last-row {:#x} empty {:#x}; whole-band hash writer {:#x} / read-only reader {:#x} (both equal the host renderer's {:#x}, text included); a READ-only reader of the same rows saw {:#x} {:#x} {:#x} {:#x} {:#x} {:#x} {:#x}; a writer on rows 192..384 flooded its own band (reader of that band saw {:#x} {:#x}) without touching band 1 (still the layout above); page flags: writer maps writable+uncached+NX {}, reader read-only+NX {}; the process with no cap got status {} -- {}",
        if have_fb { "present" } else { "ABSENT: checks skipped" }, gi[1], gi[2], gi[4], gi[5], gi[6], gi[3], g_dirty[3], g_w[3], g_w[4], g_w[5], g_w[6], g_w[7], g_w[8], g_w[9], g_w[10], gh[1], gh[2], GFX_BAND_HASH, g_r[4], g_r[5], g_r[6], g_r[7], g_r[8], g_r[9], g_r[10], g_r2[4], g_r2[10], fb_flags(70).map_or(false, |f| f.contains(PageTableFlags::WRITABLE | PageTableFlags::NO_CACHE | PageTableFlags::NO_EXECUTE)), fb_flags(72).map_or(false, |f| !f.contains(PageTableFlags::WRITABLE) && f.contains(PageTableFlags::NO_EXECUTE)), g_n[4], if gfx_ok { "OK" } else { "FAILED" }
    );
    // Input routing (Phase 9 / 1-29): tasks 75 = surface A, 76 = surface B, 77 = router `inputsvc`, 78 = a surface in C3 (refused the Input capability). Init's results are at the bottom of its stack page.
    let exp = cfg!(feature = "out_of_scope_experiments");
    let rdx = |t: usize, o: u64| -> u64 { if exp { read_data(t, o) } else { 0 } };
    let ist = |o: u64| rdx(0, 0x10000 + 0x100 + o) as i64;
    let ii: [i64; 12] = [0x00u64, 0x08, 0x10, 0x18, 0x20, 0x28, 0x30, 0x38, 0x40, 0x48, 0x50, 0x58].map(|o| ist(o));
    let ip = ist(0x60);
    let ir: [i64; 11] = [0x08u64, 0x10, 0x18, 0x20, 0x28, 0x30, 0x50, 0x58, 0x38, 0x40, 0x68].map(|o| rdx(77, o) as i64);
    let sa_h: [i64; 5] = [0x08u64, 0x10, 0x18, 0x20, 0x28].map(|o| rdx(75, o) as i64);          // typist: expected, received, last len, FB_INFO rc, FB_MAP rc
    let sa_ev: [(i64, i64); 16] = core::array::from_fn(|i| (rdx(75, 0x100 + 16 * i as u64) as i64, rdx(75, 0x108 + 16 * i as u64) as i64));
    let ty_hash = rdx(75, 0x80) as u32;
    const A_EXPECT: [(i64, i64); 16] = [(0x1e, 1), (0x1e, 0), (0xe04d, 1), (0xe04d, 0), (0x31, 1), (0x31, 0), (0x15, 1), (0x15, 0), (0x13, 1), (0x13, 0), (0x10, 1), (0x10, 0), (0x17, 1), (0x17, 0), (0x1f, 1), (0x1f, 0)];
    let sb: [i64; 9] = [0x08u64, 0x10, 0x18, 0x100, 0x108, 0x110, 0x118, 0x120, 0x128].map(|o| rdx(76, o) as i64);
    let sr: [i64; 2] = [0x08u64, 0x10].map(|o| rdx(78, o) as i64);
    let inp_ok = !exp || (INPUT_SEEDED == 24 && rdx(77, 0x78) as i64 == 24 && ip == -2
        && ii[0] >= 1 && ii[1] >= 0 && ii[2] >= 0 && ii[3] >= 0 && ii[4] == 0 && ii[5] == 0 && ii[6] == 0 && ii[7] == 0 && ii[8] == 0 && ii[9] == -21 && ii[10] == 0 && ii[11] == 0
        && ir[0] == 22 && ir[1] == 16 && ir[2] == 2 && ir[3] == 4 && ir[4] == 0 && ir[5] == 0 && ir[6] == -16 && ir[7] == -2 && ir[8] >= 0 && ir[9] >= 0 && ir[10] == -3
        && sa_h == [16, 16, 16, 0, 0] && sa_ev == A_EXPECT && ty_hash == TYPIST_BAND_HASH
        && fb_flags(75).map_or(false, |f| f.contains(PageTableFlags::WRITABLE | PageTableFlags::NO_CACHE | PageTableFlags::NO_EXECUTE))
        && sb == [2, 2, 16, 0x30, 1, 0x30, 0, 0, 0]
        && sr == [0, 0]);
    if exp {
        serial_println!("  experimental syscall-demo: input routing (user-space `inputsvc`, Input capability): the kernel queued {} RAW scancode bytes (no decoder in the kernel since 1-50: `inputsvc` decodes them in user space and saw {} events); only the holder of the Input capability can poll (a router handed an ENDPOINT cap got {} (-16), an empty slot {} (-2), through a copy with no rights {} (-3); init's probe of a bad handle {}); init sent the capability into a container without that kind: {} (-21); the router polled {} events, forwarded {} to focused surface A and {} to surface B, swallowed {} Tab events (Tab toggles focus; final focus {} = A); surface A (the `typist`, which draws into rows [384,576)) logged 16 events (listed below) and its whole-band hash is {:#x} (host-rendered {:#x}); surface B saw {:#x}/{} {:#x}/{} (b down, b up); the queue was empty for init afterwards ({}) -- {}",
            INPUT_SEEDED, ir[0], ir[6], ir[7], ir[10], ip, ii[9], ir[0], ir[1], ir[2], ir[3], ir[4], ty_hash, TYPIST_BAND_HASH, sb[3], sb[4], sb[5], sb[6], ii[10], if inp_ok { "OK" } else { "FAILED" });
        serial_print!("  experimental syscall-demo: surface A events:");
        for (c, d) in sa_ev.iter() { serial_print!(" {:#x}/{}", c, d); }
        serial_println!();
    }
    // NOTIFY / NOTIFY_WAIT / REVOKE (NPS-003 §3, §4.3): the `ntf` process is task 79 when the Input demo spawned 4 helpers (experimental config), else 75.
    let ntf_task = if cfg!(feature = "out_of_scope_experiments") { 79 } else { 75 };
    let nt: [i64; 15] = [0x08u64, 0x10, 0x18, 0x20, 0x28, 0x30, 0x38, 0x40, 0x48, 0x50, 0x58, 0x60, 0x68, 0x70, 0x78].map(|o| read_data(ntf_task, o) as i64);
    let ni: [i64; 3] = [0x80u64, 0x88, 0x90].map(|o| read_data(0, 0x10000 + 0x100 + o) as i64);
    let ntf_ok = nt == [-12, 0, 0, 0, 5, -12, -20, 20000, 0, 0x80, 0, -4, -2, -2, -3] && ni == [0, -4, 0] && NOTIFY_BLOCKS == 1 && NOTIFY_WAKES == 1;
    serial_println!("  experimental syscall-demo: notify (NPS-003 §3) / owner revoke (§4.3): wait on an empty word {} (-12); two notifications (0x1, 0x4) were OR-ed into {:#x} and the word was then cleared ({}); a zero-bit notify {} (-20); {} of 20000 back-to-back notifications succeeded (a message queue holds 4); a blocking wait parked ({} time) and init's 0x80 woke it with {:#x}; the owner's REVOKE {} made its own cap {} and init's cap {} (both -4 Revoked); notify on an empty slot {} (-2), wait on a non-owned index {} (-2), notify through a capability with no rights {} (-3) -- {}",
        nt[0], nt[4], nt[5], nt[6], nt[7], NOTIFY_BLOCKS, nt[9], nt[10], nt[11], ni[1], nt[12], nt[13], nt[14], if ntf_ok { "OK" } else { "FAILED" });
    // Adaptive shell (NDL-1, 1-35): the four `shell` processes are the five tasks after `ntf`; init's results are on its stack page (ST = DATA + 0x10000 + 0x100).
    let sh_task = ntf_task + 1;
    let sh_init: [i64; 7] = [0x98u64, 0xa0, 0xd8, 0xe0, 0xe8, 0xf0, 0xf8].map(|o| read_data(0, 0x10000 + 0x100 + o) as i64);
    let sh: [[i64; 8]; 5] = core::array::from_fn(|i| [0x78u64, 0x38, 0x80, 0x30, 0x40, 0x48, 0x50, 0x88].map(|o| read_data(sh_task + i, o) as i64));
    let sh_evn = read_data(sh_task + 4, 0x4f8) as i64;
    let sh_codes: Vec<i64> = (0..98u64).map(|k| (read_data(sh_task + 4, 0x900 + 8 * k) as i64) >> 32).collect();   // 1-44: one packed slot per event: reply code (i32) in the high half, band hash in the low half
    let sh_ehash: Vec<u32> = (0..98u64).map(|k| read_data(sh_task + 4, 0x900 + 8 * k) as u32).collect();
    let sh_mn = read_data(sh_task + 3, 0x4f8) as i64;
    let sh_mc: Vec<i64> = (0..24u64).map(|k| (read_data(sh_task + 3, 0x900 + 8 * k) as i64) >> 32).collect();
    let sh_mh: Vec<u32> = (0..24u64).map(|k| read_data(sh_task + 3, 0x900 + 8 * k) as u32).collect();
    let sh_cn = read_data(sh_task + 2, 0x4f8) as i64;
    let sh_cc: Vec<i64> = (0..18u64).map(|k| (read_data(sh_task + 2, 0x900 + 8 * k) as i64) >> 32).collect();
    let sh_ch: Vec<u32> = (0..18u64).map(|k| read_data(sh_task + 2, 0x900 + 8 * k) as u32).collect();
    let sh_cmp_ok = sh_cn == 18 && sh_cc[..] == SHELL_CMP_CODES[..] && sh_ch[..] == SHELL_CMP_HASH[..];   // 1-46: compact window, the search field fills up by WIDTH
    let sh_med_ok = sh_cmp_ok && sh_mn == 24 && sh_mc[..] == SHELL_MED_CODES[..] && sh_mh[..] == SHELL_MED_HASH[..];   // FAB over card 2 -> topmost wins; FIND; 17 letters into a 16-char field; backspace; space; BACK
    let sh_ev_ok = sh_med_ok && sh_evn == 98 && sh_codes[..] == SHELL_EV_CODES[..] && sh_ehash[..] == SHELL_EV_HASH[..];
    // 1-38 keyboard path: task sh_task+5 = the shell driven by the keyboard, sh_task+6 = the router (`kbdrt`, Input capability holder). Experimental configuration only.
    let kbd_ok = !cfg!(feature = "out_of_scope_experiments") || !have_fb || {
        let kt = sh_task + 5;
        let kc: Vec<i64> = (0..124u64).map(|k| (read_data(kt, 0x900 + 8 * k) as i64) >> 32).collect();
        let kh: Vec<u32> = (0..124u64).map(|k| read_data(kt, 0x900 + 8 * k) as u32).collect();
        let kr: Vec<i64> = (0..124u64).map(|k| read_data(kt + 1, 0x500 + 8 * k) as i64).collect();
        let rs: [i64; 6] = [0x08u64, 0x10, 0x18, 0x30, 0x38, 0x40].map(|o| read_data(kt + 1, o) as i64);
        KBD_SEEDED == 434 && read_data(kt + 1, 0x78) as i64 == 434 && read_data(kt, 0x4f8) as i64 == 124 && kc[..] == KBD_CODES[..] && kh[..] == KBD_HASH[..] && kr[..] == KBD_CODES[..] && rs == [182, 124, 28, 0, 0, 0]
    };
    let sh_ok = kbd_ok && (!have_fb || (sh_ev_ok && (sh_init[0] >= 3 && sh_init[1] >= 0 && sh_init[2..].iter().all(|&r| r == 0)
        && sh[0] == [-20, -1, 0, 0, 0, 0, -1, 0] && sh[1] == [-20, -1, 0, 0, 0, 0, -1, 0]                                                   // 100 dp and 2000 dp: refused, nothing drawn, band still all zero
        && sh[2] == [0, 0, SHELL_HASH[0] as i64, 0, 0xFEF7FF, 0xFEF7FF, 0, 21]                   // 360 dp: compact; the kernel had zeroed the previous run's pixels; nothing right of the window
        && sh[3] == [0, 1, SHELL_HASH[1] as i64, 0, 0xFEF7FF, 0xFEF7FF, 0, 24]                   // 640 dp: medium (two cards)
        && sh[4] == [0, 2, SHELL_HASH[2] as i64, 0, 0xFEF7FF, 0xFEF7FF, -1, 23])));                // 1024 dp: expanded (list + detail pane), left on screen
    serial_println!("  experimental syscall-demo: adaptive shell (NDL-1: Android window-size classes, Apple-HIG rules) -- {}; five user-space `shell` processes (container C6, one {}-row band, FrameBuffer capability lent five times): width 100 dp and 2000 dp refused ({} {}; band hash {:#x} = still all zero), 360 dp -> class {} hash {:#010x}, 640 dp -> class {} hash {:#010x}, 1024 dp -> class {} hash {:#010x} (each equals tools/check_shell.py's independent render); pixel (5,0) right after each FB_MAP = {} {} {} (the kernel zeroed the previous layout); {} / {} / {} display-list records (some are state-dependent); INPUT: medium window (FAB floating over card 2): pointer -> {:#x} / {:#x} / {:#x} (FAB wins where both overlap; screen unchanged); expanded window, {} events, reply codes {:?} (pointer: FIND rail item 0x101; where the HOME card was 0x310 = the search bar, not the hidden card; right of the window and status bar -1; keys: focus next x6 wrapping 0..4,0; previous x4 wrapping 4,3,2,1; activate FIND 0x101; previous; activate HOME 0x100; a pointer miss while the focus ring shows -1 and hides it; activate with no focus -1; clear focus 0); each event's band hash equals the independent model's ({}), the last one equals the plain expanded layout ({}); the keyboard focus ring and the destination switch are real pixels; the compact window's pixel right of its edge = {}; the expanded window is left on screen -- {}",
        if have_fb { "present" } else { "ABSENT: checks skipped" }, 192, sh[0][0], sh[1][0], sh[1][2], sh[2][1], sh[2][2], sh[3][1], sh[3][2], sh[4][1], sh[4][2], sh[2][3], sh[3][3], sh[4][3], sh[2][7], sh[3][7], sh[4][7], sh_mc[0], sh_mc[1], sh_mc[2], sh_evn, sh_codes, if sh_ev_ok { "all match" } else { "MISMATCH" }, sh_ehash.last() == Some(&SHELL_HASH[2]), sh[2][6], if sh_ok { "OK" } else { "FAILED" });
    if cfg!(feature = "out_of_scope_experiments") && have_fb {
        serial_println!("  experimental syscall-demo: keyboard -> shell (1-38, 1-50: raw PS/2 bytes -> Input capability held by the user-space router `kbdrt`, which DECODES them (no decoder in the kernel) -> CALLs to the shell): {} raw bytes queued ({} bytes), router polled {} raw bytes and decoded {} events: forwarded {} / ignored {} (releases and the Q key); the shell's reply codes {:?} and the hash of the whole band after each of the {} events equal tools/check_shell.py's independent model (Tab, Enter, Shift+Tab, Esc = BACK, T = dark theme, arrows); the script ends on the untouched light layout -- {}",
            KBD_SEEDED, KBD_SCRIPT.len() + MOUSE_SCRIPT.len(), read_data(sh_task + 6, 0x78) as i64, 182, KBD_CODES.len(), 28, KBD_CODES, KBD_CODES.len(), if kbd_ok { "OK" } else { "FAILED" });
    }
    let ipc_ok = storage_ok && shm_ok && csum_ok && call_ok && svc_ok && cow_ok && snap_ok && tree_ok && gc_ok && ny_ok && st_ok && at_ok && ct_ok && bd_ok && pe_ok && pw_ok && rl_ok && dx_ok && gfx_ok && inp_ok && ntf_ok && sh_ok && dead_ok && spawn_ok && block_ok && xfer_ok && s_sum == 60 && s_cnt == 3 && s_rep == 0 && c_reply == 60 && c_len == 8 && c_sendfail == 0;
    let neg_ok = e_forged == -2 && e_fault == EFAULT && e_big == -6 && e_notowned == -2
        && e_unmapped == EFAULT && e_spill == EFAULT && e_ro == EFAULT;
    serial_println!(
        "  experimental syscall-demo: server received {} requests, sum {} (expected 60); client got reply {} (len {})",
        s_cnt, s_sum, c_reply, c_len
    );
    serial_println!(
        "  experimental syscall-demo: invalid syscalls from ring 3 -> forged handle {} (-2), kernel pointer {} (-11 EFAULT), oversize {} (-6), non-owned endpoint {} (-2)",
        e_forged, e_fault, e_big, e_notowned
    );
    serial_println!(
        "  experimental syscall-demo: page-table-walk validation -> unmapped page {}, range spilling off a mapped page {}, read-only page as RECV target {} (all expected -11)",
        e_unmapped, e_spill, e_ro
    );
    serial_println!(
        "  experimental syscall-demo self-test: ring-3 <-> ring-3 capability IPC via syscalls {}, invalid-syscall rejection {} -- {}",
        if ipc_ok { "OK" } else { "FAILED" },
        if neg_ok { "OK" } else { "FAILED" },
        if ipc_ok && neg_ok { "OK" } else { "FAILED" }
    );
    // Leave the trapping task's kernel stack (it is about to be freed) and tear down.
    let top = (core::ptr::addr_of_mut!(TEARDOWN_STACK) as u64) + TEARDOWN_STACK_SIZE as u64;
    asm!("mov rsp, {top}", "call {f}", top = in(reg) top, f = sym teardown_and_chain, options(noreturn));
}

/// Destroys both processes' address spaces (physical frames + heap kernel
/// stacks), proves every frame came back, then chains to the next demo.
/// Runs on `TEARDOWN_STACK`, with the boot address space active.
extern "C" fn teardown_and_chain() -> ! {
    unsafe {
        let frame = x86_64::structures::paging::PhysFrame::containing_address(x86_64::PhysAddr::new(ORIG_CR3));
        x86_64::registers::control::Cr3::write(frame, x86_64::registers::control::Cr3Flags::empty());
        crate::gdt::set_rsp0(VirtAddr::new((core::ptr::addr_of_mut!(TEARDOWN_STACK) as u64) + TEARDOWN_STACK_SIZE as u64));
        let fa = FRAME_ALLOCATOR.as_mut().unwrap();
        let before = fa.frames_in_use();
        let tasks = core::mem::take(&mut *core::ptr::addr_of_mut!(TASKS));
        let n = tasks.len();
        for t in tasks {
            t.space.destroy_with(fa, &mut |f, fa| is_fb_frame(f.start_address().as_u64()) || (*core::ptr::addr_of_mut!(SHM)).on_unmap(f, fa));   // framebuffer pages are device memory: never returned to the allocator
        }
        let stragglers = (*core::ptr::addr_of_mut!(SHM)).release_all(fa);
        if stragglers != 0 { serial_println!("  experimental syscall-demo: {} shared region(s) were still live after every holder and mapping was gone (refcount leak) -- FAILED", stragglers); }
        CAPS = None;
        let after = fa.frames_in_use();
        let ok = after == BASELINE_IN_USE;
        serial_println!(
            "  experimental syscall-demo: both processes destroyed ({} spaces): {} frames reclaimed ({} -> {} in use, pre-demo baseline {}) -- {}",
            n, before - after, before, after, BASELINE_IN_USE, if ok { "fully reclaimed, OK" } else { "LEAK, FAILED" }
        );
        let fa = FRAME_ALLOCATOR.take().unwrap();
        crate::experimental::pie_demo::start(fa)
    }
}

#[naked]
unsafe extern "C" fn syscall_trap_entry() {
    asm!(
        "push rax", "push rbx", "push rcx", "push rdx", "push rsi", "push rdi", "push rbp",
        "push r8", "push r9", "push r10", "push r11", "push r12", "push r13", "push r14", "push r15",
        "mov rdi, rsp",
        "call {dispatch}",
        "mov rsp, rax",
        "pop r15", "pop r14", "pop r13", "pop r12", "pop r11", "pop r10", "pop r9", "pop r8",
        "pop rbp", "pop rdi", "pop rsi", "pop rdx", "pop rcx", "pop rbx", "pop rax",
        "iretq",
        dispatch = sym syscall_dispatch,
        options(noreturn)
    );
}
