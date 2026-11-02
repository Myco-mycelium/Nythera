//! Client of the NyFS service (user-space, NPS-004): the desktop holds ONLY a SEND capability to the service and the file-data region it shares with it (no device capability).
//! Protocol (prog_nyfs.s): CALL {op, n0|a, n1|b, val} -> {status, v0, v1, v2}; names are 1..15 bytes in two little-endian words; file data (<= 2016 bytes) travels through the shared region.
use crate::sys;
pub static mut NY: u64 = 0; pub static mut READY: bool = false;
static mut REQ: [u64; 4] = [0; 4]; static mut REP: [u64; 4] = [0; 4];
pub const REGION: u64 = 0x40000101000; pub const MAXF: usize = 2016; pub const NAMES: usize = 20;
pub fn words(n: &[u8]) -> (u64, u64) { let mut b = [0u8; 16]; let l = n.len().min(16); b[..l].copy_from_slice(&n[..l]); (u64::from_le_bytes([b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7]]), u64::from_le_bytes([b[8], b[9], b[10], b[11], b[12], b[13], b[14], b[15]])) }
unsafe fn call(op: u64, a: u64, b: u64, v: u64) -> (i64, u64, u64) {
    if !READY { return (-100, 0, 0); }
    REQ = [op, a, b, v]; let (r, _) = sys(9, NY, core::ptr::addr_of!(REQ) as u64, 32, core::ptr::addr_of_mut!(REP) as u64, 32);
    if r == 32 { (REP[0] as i64, REP[1], REP[2]) } else { (-100, 0, 0) }
}
pub fn nlen(n: &[u8; 16]) -> usize { n.iter().position(|&c| c == 0).unwrap_or(16) }
pub fn list(i: usize) -> Option<[u8; 16]> { unsafe { let (st, a, b) = call(10, i as u64, 0, 0); if st != 0 { return None; } let mut o = [0u8; 16]; o[..8].copy_from_slice(&a.to_le_bytes()); o[8..].copy_from_slice(&b.to_le_bytes()); Some(o) } }
/// FREAD: -> status; the bytes are copied into `out` (returns the size in the second field)
pub fn fread(n: &[u8], out: &mut [u8; MAXF]) -> (i64, usize) { unsafe { let (a, b) = words(n); let (st, sz, _) = call(12, a, b, 0); if st != 0 { return (st, 0); } let sz = (sz as usize).min(MAXF); core::ptr::copy_nonoverlapping(REGION as *const u8, out.as_mut_ptr(), sz); (0, sz) } }
pub fn size_of(n: &[u8]) -> i32 { unsafe { let (a, b) = words(n); let (st, sz, _) = call(12, a, b, 0); if st == 0 { sz as i32 } else { -1 } } }
pub fn fwrite(n: &[u8], data: &[u8]) -> i64 { unsafe { if data.len() > MAXF { return -19; } core::ptr::copy_nonoverlapping(data.as_ptr(), REGION as *mut u8, data.len()); let (a, b) = words(n); call(11, a, b, data.len() as u64).0 } }
pub fn create(n: &[u8]) -> i64 { unsafe { let (a, b) = words(n); call(5, a, b, 0).0 } }
pub fn unlink(n: &[u8]) -> i64 { unsafe { let (a, b) = words(n); call(8, a, b, 0).0 } }
pub fn probe() -> bool { unsafe { READY = true; let (st, _, _) = call(10, 0, 0, 0); if st == 0 || st == -15 { true } else { READY = false; false } } }
