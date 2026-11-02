//! Verifier for the .nygi game image (NPS-006, format v0 -- see tools/make_nygi.py and RC-23). The image arrives in a one-page region that `livesh` filled with BOOT_FILE; every field is checked
//! BEFORE it is trusted: header, manifest hash, ranges, then each file's SHA-256 against the manifest (NPS-006 section 6.1 -- no decompression needed). Result codes (0 = verified):
//! -1 not an image (length / magic)  -2 unsupported version or codec  -3 inconsistent header  -4 manifest hash mismatch  -5 a file lies outside the image  -6 a file's hash does not match.
use crate::sha::sha256;
pub const REGION: u64 = 0x40000103000;
pub fn verify(len: usize) -> (i32, i32) {                          // -> (code, number of files)
    if len < 64 || len > 4096 { return (-1, 0); }
    let d = unsafe { core::slice::from_raw_parts(REGION as *const u8, len) };
    if &d[0..4] != b"NYGI" { return (-1, 0); }
    let u = |o: usize| u32::from_le_bytes([d[o], d[o + 1], d[o + 2], d[o + 3]]) as usize;
    if u(4) != 1 || u(8) != 0 { return (-2, 0); }
    let (nf, mo, co, tl) = (u(12), u(16), u(20), u(24));
    if nf == 0 || nf > 8 || mo != 64 || co != 64 + 56 * nf || tl != len || co > len { return (-3, 0); }
    if sha256(&d[mo..co])[..] != d[32..64] { return (-4, 0); }
    for i in 0..nf { let e = mo + 56 * i; let (off, sz) = (u(e + 16), u(e + 20));
        if off > len - co || sz > len - co - off { return (-5, 0); }
        if sha256(&d[co + off..co + off + sz])[..] != d[e + 24..e + 56] { return (-6, 0); } }
    (0, nf as i32)
}

/// 1-70: record `i` of the process snapshot livesh writes into the shared page: alive, container, blocked, pages, (4 reserved), name[8].
pub fn rec(i: usize) -> [u8; 16] { let mut o = [0u8; 16]; for k in 0..16 { o[k] = unsafe { core::ptr::read_volatile((REGION + 16 * i as u64 + k as u64) as *const u8) }; } o }
