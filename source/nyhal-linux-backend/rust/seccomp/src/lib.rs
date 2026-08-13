//! Nyrqis seccomp BPF policy compiler — ADR-0020 first migration.
//!
//! **Implemented 2026-08-13.** This crate compiles a serialized seccomp
//! policy (the ADR-0020 FFI wire format — the JSON emitted by
//! `backend/seccomp.py`'s `SeccompPolicy.to_json()`) into a classic-BPF
//! `struct sock_filter` program, validates programs, and simulates
//! verdicts. It is the memory-safe replacement for the pure-Python
//! compiler in `backend/seccomp.py`; the two implementations share one
//! wire format and are held equal by the conformance suite — the CI
//! `rust-seccomp-conformance` job forces the full Python test suite
//! through this module via the FFI loader, and a differential test
//! asserts byte-identical programs and identical verdicts.
//!
//! FFI surface (the ABI rule of ADR-0020 / ABI-001): versioned,
//! plain-data entry points, no shared mutable state. All buffers are
//! caller- or module-owned per entry point; buffers returned by this
//! module are allocated with `libc::malloc` and freed with
//! `nyrqis_seccomp_free` (no length metadata crosses the boundary).
//!
//! Error codes (`NyrqisErr`, negative i32): -1 policy parse, -2
//! unsupported architecture, -3 invalid program, -4 internal. The
//! Python side maps these back to `PolicyError`/`ValueError` exactly as
//! the pure-Python path raises for the same conditions.

use serde::Deserialize;
use std::ffi::c_void;

/// Module ABI version (semver-major*10000 + minor*100 + patch).
pub const ABI_VERSION: u32 = 0x0001_0000;

// NyrqisErr codes (negative i32 returns).
pub const ERR_POLICY_PARSE: i32 = -1;
pub const ERR_UNSUPPORTED_ARCH: i32 = -2;
pub const ERR_INVALID_PROGRAM: i32 = -3;
pub const ERR_INTERNAL: i32 = -4;

// seccomp return actions (mirror backend/seccomp.py).
const SECCOMP_RET_KILL_PROCESS: u32 = 0x8000_0000;
const SECCOMP_RET_ERRNO: u32 = 0x0005_0000;
const SECCOMP_RET_ALLOW: u32 = 0x7FFF_0000;
const EPERM: u32 = 1;

// audit architectures (the `arch` field of seccomp_data, offset 4).
const AUDIT_ARCH_X86_64: u32 = 0xC000_003E;
const AUDIT_ARCH_AARCH64: u32 = 0xC000_00B7;

// seccomp_data field offsets (both x86_64 and aarch64).
const OFF_NR: usize = 0;
const OFF_ARCH: usize = 4;
const OFF_ARGS: usize = 16; // args[i] at OFF_ARGS + 8*i

// BPF instruction encoding (subset used by the compiler; mirrors
// backend/seccomp.py).
const BPF_LD: u16 = 0x00;
const BPF_W: u16 = 0x00;
const BPF_DW: u16 = 0x18;
const BPF_ABS: u16 = 0x20;
const BPF_JMP: u16 = 0x05;
const BPF_JEQ: u16 = 0x10;
const BPF_JSET: u16 = 0x40;
const BPF_RET: u16 = 0x06;
const BPF_K: u16 = 0x00;
const BPF_ALU: u16 = 0x04;
const BPF_AND: u16 = 0x50;
const BPF_OR: u16 = 0x40;
const BPF_ADD: u16 = 0x00;

/// The policy wire format: resolved syscall numbers (the name → number
/// resolution happens on the Python side, which owns the tables), flag
/// rules as `[nr, arg_index, mask]` triples. This is exactly the shape
/// of `SeccompPolicy.to_json()`.
#[derive(Deserialize)]
struct PolicyJson {
    arch: String,
    default_action: u32,
    #[serde(default)]
    deny: Vec<u32>,
    #[serde(default)]
    deny_flags: Vec<Vec<u32>>,
    #[serde(default)]
    allow: Vec<u32>,
    #[serde(default)]
    allow_flags: Vec<Vec<u32>>,
}

/// One classic-BPF instruction as the kernel sees it (`struct
/// sock_filter`): u16 code, u8 jt, u8 jf, u32 k.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Insn {
    code: u16,
    jt: u8,
    jf: u8,
    k: u32,
}

impl Insn {
    /// Encode to the 8-byte little-endian `sock_filter` wire layout.
    fn to_bytes(self) -> [u8; 8] {
        let mut b = [0u8; 8];
        b[0..2].copy_from_slice(&self.code.to_le_bytes());
        b[2] = self.jt;
        b[3] = self.jf;
        b[4..8].copy_from_slice(&self.k.to_le_bytes());
        b
    }

    /// Decode from the 8-byte little-endian `sock_filter` wire layout.
    fn from_bytes(b: &[u8]) -> Insn {
        Insn {
            code: u16::from_le_bytes([b[0], b[1]]),
            jt: b[2],
            jf: b[3],
            k: u32::from_le_bytes([b[4], b[5], b[6], b[7]]),
        }
    }
}

/// Tiny assembler with deferred jump targets (label fixups), mirroring
/// `backend/seccomp.py`'s `_Assembler`.
struct Assembler {
    ins: Vec<Insn>,
}

impl Assembler {
    fn new() -> Assembler {
        Assembler { ins: Vec::new() }
    }

    fn emit(&mut self, code: u16, k: u32, jt: u8, jf: u8) -> usize {
        let idx = self.ins.len();
        self.ins.push(Insn { code, jt, jf, k });
        idx
    }

    fn ld_abs(&mut self, offset: u32) -> usize {
        self.emit(BPF_LD | BPF_W | BPF_ABS, offset, 0, 0)
    }

    fn jeq_k(&mut self, k: u32, jt: u8, jf: u8) -> usize {
        self.emit(BPF_JMP | BPF_JEQ | BPF_K, k, jt, jf)
    }

    fn ret_k(&mut self, k: u32) -> usize {
        self.emit(BPF_RET | BPF_K, k, 0, 0)
    }

    fn alu_and(&mut self, k: u32) -> usize {
        self.emit(BPF_ALU | BPF_AND | BPF_K, k, 0, 0)
    }

    /// Patch an emitted jeq's `jt` to jump to `target` (the deferred
    /// whole-syscall / flag-rule targets). `delta > 255` means the
    /// program exceeds the BPF limits — the pure path raises
    /// `PolicyError("jump too far")`, mapped here to ERR_POLICY_PARSE.
    fn patch_jt(&mut self, idx: usize, target: usize) -> Result<(), i32> {
        let delta = target - idx - 1;
        if delta > 0xFF {
            return Err(ERR_POLICY_PARSE);
        }
        self.ins[idx].jt = delta as u8;
        Ok(())
    }

    /// Patch an emitted jeq's `jf` to jump to `target`.
    fn patch_jf(&mut self, idx: usize, target: usize) -> Result<(), i32> {
        let delta = target - idx - 1;
        if delta > 0xFF {
            return Err(ERR_POLICY_PARSE);
        }
        self.ins[idx].jf = delta as u8;
        Ok(())
    }
}

fn audit_arch_for(arch: &str) -> Option<u32> {
    match arch {
        "x86_64" => Some(AUDIT_ARCH_X86_64),
        "aarch64" => Some(AUDIT_ARCH_AARCH64),
        _ => None,
    }
}

/// Compile a parsed policy into a program, mirroring
/// `backend/seccomp.py`'s `build_program` instruction-for-instruction:
///
/// ```text
/// 0:  ld  [4]                    ; arch
/// 1:  jeq ARCH, jt=1, jf=0       ; match -> skip RET KILL below
/// 2:  ret KILL_PROCESS           ; wrong arch
/// 3:  ld  [0]                    ; nr
/// 4..: jeq nr_i, jt=TARGET, jf=0 ; whole-syscall rules (deny or allow)
///      jeq nr, jt=0, jf=4        ; flag rule: match -> check block
///      ld  [args[N]]             ;   check: load the flag argument
///      and MASK                  ;   mask
///      jeq 0, jt=A, jf=D         ;   branch on masked value
///      ld  [0]                   ;   restore nr for the remaining chain
///      ...
///      ret <default action>      ; ALLOW (deny model) or ERRNO|EPERM
///      ret <other action>        ; explicit target
/// ```
///
/// The wire-format number lists arrive pre-ordered (the Python side
/// emits sorted-by-name denies and insertion-ordered flag rules), so the
/// list order here IS the program order — byte-identical output depends
/// on preserving it.
fn compile(p: &PolicyJson) -> Result<Vec<Insn>, i32> {
    let audit_arch = audit_arch_for(&p.arch).ok_or(ERR_POLICY_PARSE)?;

    let default_errno = SECCOMP_RET_ERRNO | EPERM;
    let deny_default = p.default_action == default_errno;
    if p.default_action != SECCOMP_RET_ALLOW && !deny_default {
        return Err(ERR_POLICY_PARSE); // unsupported default_action
    }

    // Validate flag rules up front (the pure-Python path's
    // `SeccompPolicy.validate()`): arg_index 0..=5, mask in 32-bit range.
    for triple in p.deny_flags.iter().chain(p.allow_flags.iter()) {
        if triple.len() != 3 || triple[1] > 5 || triple[2] > u32::MAX {
            return Err(ERR_POLICY_PARSE);
        }
    }

    let mut a = Assembler::new();
    a.ld_abs(OFF_ARCH as u32);
    // On arch match skip the kill (jt=1); on mismatch fall into it (jf=0).
    a.jeq_k(audit_arch, 1, 0);
    a.ret_k(SECCOMP_RET_KILL_PROCESS);
    a.ld_abs(OFF_NR as u32);

    let (whole, flag_rules): (&[u32], &[Vec<u32>]) = if deny_default {
        (&p.allow, &p.allow_flags)
    } else {
        (&p.deny, &p.deny_flags)
    };

    if deny_default {
        // Default-deny: whole-syscall allows, then flag-based read-only
        // allows; the default ret is ERRNO and the patched target is ALLOW.
        let mut whole_indices = Vec::new();
        for nr in whole {
            whole_indices.push(a.jeq_k(*nr, 0, 0));
        }
        let mut flag_jt_indices = Vec::new();
        for t in flag_rules {
            a.jeq_k(t[0], 0, 4); // non-matching nr skips the 4 check instructions
            a.ld_abs((OFF_ARGS + 8 * t[1] as usize) as u32);
            a.alu_and(t[2]);
            flag_jt_indices.push(a.jeq_k(0, 0, 0)); // masked == 0 -> allow
            a.ld_abs(OFF_NR as u32); // restore nr for the remaining chain
        }
        let _deny_idx = a.ret_k(default_errno);
        let allow_idx = a.ret_k(SECCOMP_RET_ALLOW);
        for idx in whole_indices {
            a.patch_jt(idx, allow_idx)?;
        }
        for idx in flag_jt_indices {
            a.patch_jt(idx, allow_idx)?;
        }
    } else {
        // Default-allow: whole-syscall denies, then flag-based write-intent
        // denies; the default ret is ALLOW and the patched target is ERRNO.
        let mut whole_indices = Vec::new();
        for nr in whole {
            whole_indices.push(a.jeq_k(*nr, 0, 0));
        }
        let mut flag_jf_indices = Vec::new();
        for t in flag_rules {
            a.jeq_k(t[0], 0, 4);
            a.ld_abs((OFF_ARGS + 8 * t[1] as usize) as u32);
            a.alu_and(t[2]);
            flag_jf_indices.push(a.jeq_k(0, 0, 0)); // masked != 0 -> deny
            a.ld_abs(OFF_NR as u32);
        }
        let allow_idx = a.ret_k(SECCOMP_RET_ALLOW);
        let deny_idx = a.ret_k(default_errno);
        for idx in whole_indices {
            a.patch_jt(idx, deny_idx)?;
        }
        for idx in flag_jf_indices {
            a.patch_jf(idx, deny_idx)?;
        }
    }

    let program = a.ins;
    validate(&program)?;
    Ok(program)
}

/// Sanity-check that all jump targets stay in bounds and within the
/// kernel's 8-bit jt/jf field width (mirror of
/// `backend/seccomp.py`'s `validate_program`). In Rust the u8 fields
/// already bound jt/jf to 8 bits; the bounds walk is the real check.
fn validate(program: &[Insn]) -> Result<(), i32> {
    let n = program.len();
    for (i, ins) in program.iter().enumerate() {
        for offset in [ins.jt as usize, ins.jf as usize] {
            let target = i + 1 + offset;
            if target > n {
                return Err(ERR_INVALID_PROGRAM);
            }
        }
    }
    Ok(())
}

/// Load a 32-bit word little-endian at `offset`, or 0 when out of
/// bounds (mirror of `backend/seccomp.py`'s `_load_word`).
fn load_word(data: &[u8], offset: usize) -> u32 {
    if offset + 4 > data.len() {
        return 0;
    }
    u32::from_le_bytes([data[offset], data[offset + 1], data[offset + 2], data[offset + 3]])
}

/// Evaluate a program against synthetic `seccomp_data`, returning the
/// `SECCOMP_RET_*` verdict (mirror of `backend/seccomp.py`'s
/// `simulate`). `program` is the 8-bytes-per-instruction wire format.
fn simulate(program: &[u8], nr: u32, arch: u32, args: &[u64]) -> Result<u32, i32> {
    if program.len() % 8 != 0 {
        return Err(ERR_INVALID_PROGRAM);
    }
    let n = program.len() / 8;

    // Build the synthetic seccomp_data buffer exactly as Python does.
    let mut data = [0u8; 64];
    data[OFF_NR..OFF_NR + 4].copy_from_slice(&nr.to_le_bytes());
    data[OFF_ARCH..OFF_ARCH + 4].copy_from_slice(&arch.to_le_bytes());
    for i in 0..6 {
        let v = args.get(i).copied().unwrap_or(0);
        let off = OFF_ARGS + 8 * i;
        data[off..off + 8].copy_from_slice(&v.to_le_bytes());
    }

    let mut pc: usize = 0;
    let mut reg: u64 = 0;
    while pc < n {
        let ins = Insn::from_bytes(&program[pc * 8..pc * 8 + 8]);
        pc += 1;
        let op = ins.code & 0x07;
        if op == BPF_LD {
            let src = ins.code & 0x18;
            if src == BPF_W {
                reg = load_word(&data, ins.k as usize) as u64;
            } else if src == BPF_DW {
                let start = ins.k as usize;
                if start + 8 > data.len() {
                    return Err(ERR_INVALID_PROGRAM);
                }
                reg = u64::from_le_bytes(data[start..start + 8].try_into().unwrap());
            } else {
                return Err(ERR_INVALID_PROGRAM);
            }
        } else if op == BPF_JMP {
            // jmp class: K source is bit 3 (0x08); the sub-op lives in
            // bits 6-4 (JEQ=0x10, JSET=0x40).
            if ins.code & 0x08 != 0 {
                return Err(ERR_INVALID_PROGRAM); // register-source jump
            }
            let sub = ins.code & 0x70;
            if sub == 0x10 {
                pc += if reg == ins.k as u64 { ins.jt as usize } else { ins.jf as usize };
            } else if sub == 0x40 {
                pc += if (reg & ins.k as u64) != 0 { ins.jt as usize } else { ins.jf as usize };
            } else {
                return Err(ERR_INVALID_PROGRAM);
            }
        } else if op == BPF_RET {
            return Ok(ins.k);
        } else if op == BPF_ALU {
            if ins.code & 0xF0 == BPF_AND {
                reg &= ins.k as u64;
            } else if ins.code & 0xF0 == BPF_OR {
                reg |= ins.k as u64;
            } else if ins.code & 0xF0 == BPF_ADD {
                reg = reg.wrapping_add(ins.k as u64);
            } else {
                return Err(ERR_INVALID_PROGRAM);
            }
        } else {
            return Err(ERR_INVALID_PROGRAM);
        }
    }
    Err(ERR_INVALID_PROGRAM) // terminated without a RET
}

/// Parse the policy JSON into the program wire bytes.
fn build_program_bytes(policy: *const u8, policy_len: usize, arch: u32) -> Result<Vec<u8>, i32> {
    if policy.is_null() {
        return Err(ERR_INTERNAL);
    }
    let slice = unsafe { std::slice::from_raw_parts(policy, policy_len) };
    let text = std::str::from_utf8(slice).map_err(|_| ERR_POLICY_PARSE)?;
    let p: PolicyJson = serde_json::from_str(text).map_err(|_| ERR_POLICY_PARSE)?;
    // The JSON's arch and the passed audit arch must agree; a mismatch
    // means the caller compiled for a different architecture than the
    // policy declares (the pure-Python path would reject this via the
    // policy's own arch — here it is the module's "unsupported arch").
    match audit_arch_for(&p.arch) {
        Some(a) if a == arch => {}
        _ => return Err(ERR_UNSUPPORTED_ARCH),
    }
    let program = compile(&p)?;
    let mut out = Vec::with_capacity(program.len() * 8);
    for ins in &program {
        out.extend_from_slice(&ins.to_bytes());
    }
    Ok(out)
}

/// Report the module ABI version.
#[no_mangle]
pub extern "C" fn nyrqis_seccomp_version() -> u32 {
    ABI_VERSION
}

/// Compile a serialized policy (JSON) into a classic-BPF instruction
/// list. On success `out` receives a `libc::malloc`-allocated buffer (8
/// bytes per instruction, `struct sock_filter` layout) and `out_len`
/// its length; the caller frees via `nyrqis_seccomp_free`. Returns 0 or
/// a negative `NyrqisErr` code.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_seccomp_build_program(
    policy: *const u8,
    policy_len: usize,
    arch: u32,
    out: *mut *mut u8,
    out_len: *mut usize,
) -> i32 {
    if out.is_null() || out_len.is_null() {
        return ERR_INTERNAL;
    }
    let bytes = match build_program_bytes(policy, policy_len, arch) {
        Ok(b) => b,
        Err(e) => return e,
    };
    let len = bytes.len();
    let ptr = libc::malloc(len) as *mut u8;
    if ptr.is_null() {
        return ERR_INTERNAL;
    }
    std::ptr::copy_nonoverlapping(bytes.as_ptr(), ptr, len);
    *out = ptr;
    *out_len = len;
    0
}

/// Validate jump offsets/bounds of a classic-BPF program (wire format).
/// Returns 0 or a negative `NyrqisErr` code.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_seccomp_validate_program(
    program: *const u8,
    program_len: usize,
) -> i32 {
    if program.is_null() {
        // null + len 0 is treated as an empty program (valid); any other
        // length from a null pointer is invalid. Building a slice from a
        // null pointer is UB even for len 0, so guard before the call.
        return if program_len == 0 { 0 } else { ERR_INVALID_PROGRAM };
    }
    if program_len % 8 != 0 {
        return ERR_INVALID_PROGRAM;
    }
    let slice = std::slice::from_raw_parts(program, program_len);
    let mut insns = Vec::with_capacity(program_len / 8);
    for off in (0..program_len).step_by(8) {
        insns.push(Insn::from_bytes(&slice[off..off + 8]));
    }
    match validate(&insns) {
        Ok(()) => 0,
        Err(e) => e,
    }
}

/// Evaluate a program against a syscall; returns the `SECCOMP_RET_*`
/// verdict (positive) or a negative `NyrqisErr` code.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_seccomp_simulate(
    program: *const u8,
    program_len: usize,
    syscall_nr: u32,
    audit_arch: u32,
    args: *const u64,
    args_len: usize,
) -> i64 {
    if program.is_null() {
        // A null pointer must never become a slice (UB even at len 0);
        // an empty program could not yield a verdict anyway.
        return ERR_INVALID_PROGRAM as i64;
    }
    if program_len % 8 != 0 {
        return ERR_INVALID_PROGRAM as i64;
    }
    if args_len > 0 && args.is_null() {
        return ERR_INVALID_PROGRAM as i64;
    }
    let program = std::slice::from_raw_parts(program, program_len);
    let arg_slice = if args_len > 0 {
        std::slice::from_raw_parts(args, args_len)
    } else {
        &[]
    };
    match simulate(program, syscall_nr, audit_arch, arg_slice) {
        Ok(verdict) => verdict as i64,
        Err(e) => e as i64,
    }
}

/// Free a buffer returned by `nyrqis_seccomp_build_program` (allocated
/// with `libc::malloc`).
#[no_mangle]
pub unsafe extern "C" fn nyrqis_seccomp_free(ptr: *mut u8) {
    if !ptr.is_null() {
        libc::free(ptr as *mut c_void);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The exact policy JSON the pure-Python compiler emitted for
    /// `build_policy(set())` on x86_64 (2026-08-13) — the default
    /// read-only posture: all capability-gated families denied, plus
    /// openat/open write-intent flag rules. The Rust compiler must turn
    /// this into the byte-identical 90-instruction program pinned below.
    const GOLDEN_DENY_EMPTY_X86_64_JSON: &str = r#"{"arch": "x86_64", "default_action": 2147418112, "deny": [43, 288, 49, 321, 90, 92, 56, 435, 42, 85, 176, 285, 91, 268, 93, 260, 313, 57, 199, 190, 77, 52, 51, 55, 175, 312, 246, 94, 86, 265, 50, 83, 258, 133, 259, 165, 303, 304, 298, 155, 310, 311, 101, 169, 45, 299, 47, 197, 82, 264, 316, 84, 307, 46, 44, 171, 170, 308, 54, 188, 48, 41, 53, 168, 167, 88, 266, 76, 166, 87, 263, 323, 280, 58], "deny_flags": [[257, 2, 4195907], [2, 1, 4195907]], "allow": [], "allow_flags": []}"#;

    /// Golden wire bytes for the above: 90 instructions, 720 bytes,
    /// generated by the authoritative pure-Python compiler (2026-08-13).
    const GOLDEN_DENY_EMPTY_X86_64_WIRE: &str = "\
2000000004000000150001003e0000c006000000000000802000000000000000150054002b000000\
150053002001000015005200310000001500510041010000150050005a00000015004f005c000000\
15004e003800000015004d00b301000015004c002a00000015004b005500000015004a00b0000000\
150049001d010000150048005b000000150047000c010000150046005d0000001500450004010000\
1500440039010000150043003900000015004200c700000015004100be000000150040004d000000\
15003f003400000015003e003300000015003d003700000015003c00af00000015003b0038010000\
15003a00f6000000150039005e000000150038005600000015003700090100001500360032000000\
150035005300000015003400020100001500330085000000150032000301000015003100a5000000\
150030002f01000015002f003001000015002e002a01000015002d009b00000015002c0036010000\
15002b003701000015002a006500000015002900a9000000150028002d000000150027002b010000\
150026002f00000015002500c500000015002400520000001500230008010000150022003c010000\
1500210054000000150020003301000015001f002e00000015001e002c00000015001d00ab000000\
15001c00aa00000015001b003401000015001a003600000015001900bc0000001500180030000000\
1500170029000000150016003500000015001500a800000015001400a70000001500130058000000\
150012000a010000150011004c00000015001000a600000015000f005700000015000e0007010000\
15000d004301000015000c001801000015000b003a00000015000004010100002000000020000000\
54000000430640001500000700000000200000000000000015000004020000002000000018000000\
540000004306400015000002000000002000000000000000060000000000ff7f0600000001000500";

    /// The exact policy JSON for `build_allowlist_policy(set())` on
    /// aarch64 (2026-08-13): default-deny with the runtime baseline
    /// allowed and the read-only openat flag rule.
    const GOLDEN_ALLOW_EMPTY_AARCH64_JSON: &str = r#"{"arch": "aarch64", "default_action": 327681, "deny": [], "deny_flags": [], "allow": [214, 90, 49, 114, 113, 115, 57, 436, 285, 23, 24, 20, 21, 22, 19, 221, 281, 93, 94, 48, 439, 50, 25, 83, 32, 80, 44, 82, 98, 449, 100, 168, 17, 61, 177, 175, 176, 158, 102, 155, 172, 173, 141, 278, 165, 156, 178, 169, 174, 29, 129, 62, 233, 279, 232, 228, 230, 222, 226, 216, 462, 187, 186, 188, 189, 227, 229, 231, 215, 101, 79, 437, 92, 59, 73, 167, 67, 69, 286, 72, 68, 70, 287, 63, 78, 65, 128, 293, 134, 136, 135, 139, 133, 137, 125, 126, 123, 121, 120, 127, 124, 191, 190, 193, 192, 71, 99, 96, 103, 157, 196, 195, 197, 194, 132, 74, 43, 291, 81, 84, 179, 131, 107, 111, 109, 108, 110, 85, 153, 130, 166, 160, 260, 95, 64, 66], "allow_flags": [[56, 2, 4195907]]}"#;

    /// Golden wire bytes for the above: 147 instructions, 1176 bytes.
    const GOLDEN_ALLOW_EMPTY_AARCH64_WIRE: &str = "\
200000000400000015000100b70000c00600000000000080200000000000000015008d00d6000000\
15008c005a00000015008b003100000015008a007200000015008900710000001500880073000000\
150087003900000015008600b4010000150085001d01000015008400170000001500830018000000\
15008200140000001500810015000000150080001600000015007f001300000015007e00dd000000\
15007d001901000015007c005d00000015007b005e00000015007a003000000015007900b7010000\
15007800320000001500770019000000150076005300000015007500200000001500740050000000\
150073002c0000001500720052000000150071006200000015007000c101000015006f0064000000\
15006e00a800000015006d001100000015006c003d00000015006b00b100000015006a00af000000\
15006900b0000000150068009e0000001500670066000000150066009b00000015006500ac000000\
15006400ad000000150063008d000000150062001601000015006100a5000000150060009c000000\
15005f00b200000015005e00a900000015005d00ae00000015005c001d00000015005b0081000000\
15005a003e00000015005900e9000000150058001701000015005700e800000015005600e4000000\
15005500e600000015005400de00000015005300e200000015005200d800000015005100ce010000\
15005000bb00000015004f00ba00000015004e00bc00000015004d00bd00000015004c00e3000000\
15004b00e500000015004a00e700000015004900d70000001500480065000000150047004f000000\
15004600b5010000150045005c000000150044003b000000150043004900000015004200a7000000\
1500410043000000150040004500000015003f001e01000015003e004800000015003d0044000000\
15003c004600000015003b001f01000015003a003f000000150039004e0000001500380041000000\
15003700800000001500360025010000150035008600000015003400880000001500330087000000\
150032008b0000001500310085000000150030008900000015002f007d00000015002e007e000000\
15002d007b00000015002c007900000015002b007800000015002a007f000000150029007c000000\
15002800bf00000015002700be00000015002600c100000015002500c00000001500240047000000\
150023006300000015002200600000001500210067000000150020009d00000015001f00c4000000\
15001e00c300000015001d00c500000015001c00c200000015001b008400000015001a004a000000\
150019002b00000015001800230100001500170051000000150016005400000015001500b3000000\
1500140083000000150013006b000000150012006f000000150011006d000000150010006c000000\
15000f006e00000015000e005500000015000d009900000015000c008200000015000b00a6000000\
15000a00a00000001500090004010000150008005f00000015000700400000001500060042000000\
15000004380000002000000020000000540000004306400015000200000000002000000000000000\
0600000001000500060000000000ff7f";

    fn hex_to_bytes(hex: &str) -> Vec<u8> {
        (0..hex.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap())
            .collect()
    }

    fn compile_json(json: &str) -> Vec<u8> {
        let p: PolicyJson = serde_json::from_str(json).unwrap();
        let prog = compile(&p).expect("compile");
        let mut bytes = Vec::new();
        for ins in &prog {
            bytes.extend_from_slice(&ins.to_bytes());
        }
        bytes
    }

    #[test]
    fn abi_version_is_current() {
        assert_eq!(ABI_VERSION, 0x0001_0000);
    }

    #[test]
    fn ffi_symbols_exist() {
        // The loader requires these entry points; presence is part of the
        // ABI contract (rust/seccomp/README.md).
        let _ = nyrqis_seccomp_version;
        let _ = nyrqis_seccomp_build_program;
        let _ = nyrqis_seccomp_validate_program;
        let _ = nyrqis_seccomp_simulate;
        let _ = nyrqis_seccomp_free;
    }

    #[test]
    fn golden_deny_empty_x86_64_is_byte_identical() {
        let bytes = compile_json(GOLDEN_DENY_EMPTY_X86_64_JSON);
        let expected = hex_to_bytes(GOLDEN_DENY_EMPTY_X86_64_WIRE);
        assert_eq!(bytes.len(), 720, "90 instructions");
        assert_eq!(bytes, expected);
    }

    #[test]
    fn golden_allow_empty_aarch64_is_byte_identical() {
        let bytes = compile_json(GOLDEN_ALLOW_EMPTY_AARCH64_JSON);
        let expected = hex_to_bytes(GOLDEN_ALLOW_EMPTY_AARCH64_WIRE);
        assert_eq!(bytes.len(), 1176, "147 instructions");
        assert_eq!(bytes, expected);
    }

    #[test]
    fn golden_verdicts_match_python() {
        // deny-empty x86_64: verdicts recorded from the pure-Python
        // simulator (backend/seccomp.py, 2026-08-13).
        let bytes = hex_to_bytes(GOLDEN_DENY_EMPTY_X86_64_WIRE);
        assert_eq!(simulate(&bytes, 0, AUDIT_ARCH_X86_64, &[]).unwrap(), SECCOMP_RET_ALLOW); // read
        assert_eq!(simulate(&bytes, 257, AUDIT_ARCH_X86_64, &[]).unwrap(), SECCOMP_RET_ALLOW); // openat read-only
        assert_eq!(
            simulate(&bytes, 257, AUDIT_ARCH_X86_64, &[0u64, 0, 1]).unwrap(),
            SECCOMP_RET_ERRNO | EPERM // openat O_WRONLY -> denied
        );
        assert_eq!(
            simulate(&bytes, 257, AUDIT_ARCH_X86_64, &[0u64, 0, 0x400643]).unwrap(),
            SECCOMP_RET_ERRNO | EPERM // openat write mask -> denied
        );
        assert_eq!(
            simulate(&bytes, 56, AUDIT_ARCH_X86_64, &[]).unwrap(),
            SECCOMP_RET_ERRNO | EPERM // clone (spawn) -> denied
        );
        assert_eq!(
            simulate(&bytes, 41, AUDIT_ARCH_X86_64, &[]).unwrap(),
            SECCOMP_RET_ERRNO | EPERM // socket -> denied
        );
        assert_eq!(
            simulate(&bytes, 165, AUDIT_ARCH_X86_64, &[]).unwrap(),
            SECCOMP_RET_ERRNO | EPERM // mount (always denied) -> denied
        );
        // Wrong architecture is killed.
        assert_eq!(
            simulate(&bytes, 0, AUDIT_ARCH_AARCH64, &[]).unwrap(),
            SECCOMP_RET_KILL_PROCESS
        );

        // allow-empty aarch64: default-deny — only the baseline + the
        // read-only openat rule.
        let bytes = hex_to_bytes(GOLDEN_ALLOW_EMPTY_AARCH64_WIRE);
        assert_eq!(simulate(&bytes, 63, AUDIT_ARCH_AARCH64, &[]).unwrap(), SECCOMP_RET_ALLOW); // read
        assert_eq!(simulate(&bytes, 56, AUDIT_ARCH_AARCH64, &[0u64, 0, 0]).unwrap(), SECCOMP_RET_ALLOW); // openat read-only
        assert_eq!(
            simulate(&bytes, 56, AUDIT_ARCH_AARCH64, &[0u64, 0, 1]).unwrap(),
            SECCOMP_RET_ERRNO | EPERM // openat O_WRONLY -> denied
        );
        assert_eq!(
            simulate(&bytes, 220, AUDIT_ARCH_AARCH64, &[]).unwrap(),
            SECCOMP_RET_ERRNO | EPERM // clone (no spawn cap) -> denied
        );
        assert_eq!(
            simulate(&bytes, 64, AUDIT_ARCH_AARCH64, &[]).unwrap(),
            SECCOMP_RET_ALLOW // write IS in the baseline allowlist
        );
    }

    #[test]
    fn error_bad_json_is_policy_parse() {
        let json = b"not json";
        let mut out: *mut u8 = std::ptr::null_mut();
        let mut out_len: usize = 0;
        let rc = unsafe {
            nyrqis_seccomp_build_program(
                json.as_ptr(),
                json.len(),
                AUDIT_ARCH_X86_64,
                &mut out,
                &mut out_len,
            )
        };
        assert_eq!(rc, ERR_POLICY_PARSE);
    }

    #[test]
    fn error_unknown_arch_is_unsupported() {
        // An unrecognized "arch" value means the policy cannot be
        // compiled for any architecture this module knows — mapped to
        // ERR_UNSUPPORTED_ARCH (-2), which the Python side raises as
        // PolicyError (same as its own unknown-arch path).
        let json = b"{\"arch\":\"mips\",\"default_action\":2147418112,\"deny\":[],\"deny_flags\":[],\"allow\":[],\"allow_flags\":[]}";
        let mut out: *mut u8 = std::ptr::null_mut();
        let mut out_len: usize = 0;
        let rc = unsafe {
            nyrqis_seccomp_build_program(json.as_ptr(), json.len(), AUDIT_ARCH_X86_64, &mut out, &mut out_len)
        };
        assert_eq!(rc, ERR_UNSUPPORTED_ARCH);
    }

    #[test]
    fn error_arch_mismatch_is_unsupported() {
        let json = b"{\"arch\":\"x86_64\",\"default_action\":2147418112,\"deny\":[],\"deny_flags\":[],\"allow\":[],\"allow_flags\":[]}";
        let mut out: *mut u8 = std::ptr::null_mut();
        let mut out_len: usize = 0;
        let rc = unsafe {
            nyrqis_seccomp_build_program(json.as_ptr(), json.len(), AUDIT_ARCH_AARCH64, &mut out, &mut out_len)
        };
        assert_eq!(rc, ERR_UNSUPPORTED_ARCH);
    }

    #[test]
    fn error_bad_default_action_is_policy_parse() {
        let json = b"{\"arch\":\"x86_64\",\"default_action\":7,\"deny\":[],\"deny_flags\":[],\"allow\":[],\"allow_flags\":[]}";
        let mut out: *mut u8 = std::ptr::null_mut();
        let mut out_len: usize = 0;
        let rc = unsafe {
            nyrqis_seccomp_build_program(json.as_ptr(), json.len(), AUDIT_ARCH_X86_64, &mut out, &mut out_len)
        };
        assert_eq!(rc, ERR_POLICY_PARSE);
    }

    #[test]
    fn error_bad_flag_rule_is_policy_parse() {
        // arg_index 9 is out of the 0..=5 range the pure path validates.
        let json = b"{\"arch\":\"x86_64\",\"default_action\":2147418112,\"deny\":[],\"deny_flags\":[[257,9,4195907]],\"allow\":[],\"allow_flags\":[]}";
        let mut out: *mut u8 = std::ptr::null_mut();
        let mut out_len: usize = 0;
        let rc = unsafe {
            nyrqis_seccomp_build_program(json.as_ptr(), json.len(), AUDIT_ARCH_X86_64, &mut out, &mut out_len)
        };
        assert_eq!(rc, ERR_POLICY_PARSE);
    }

    #[test]
    fn validate_rejects_out_of_bounds_jump() {
        // ret ALLOW, then jeq that claims jt=1 past the end.
        let prog = [
            0x06u8, 0x00, 0x00, 0x00, 0x00, 0x00, 0xff, 0x7f, // ret 0x7fff0000
            0x15, 0x00, 0x01, 0x00, 0x2a, 0x00, 0x00, 0x00, // jeq 42, jt=0, jf=1 -> target 3 > n=2
        ];
        let mut insns = Vec::new();
        for off in (0..prog.len()).step_by(8) {
            insns.push(Insn::from_bytes(&prog[off..off + 8]));
        }
        assert_eq!(validate(&insns), Err(ERR_INVALID_PROGRAM));
    }

    #[test]
    fn validate_accepts_golden_programs() {
        for hex in [GOLDEN_DENY_EMPTY_X86_64_WIRE, GOLDEN_ALLOW_EMPTY_AARCH64_WIRE] {
            let bytes = hex_to_bytes(hex);
            let mut insns = Vec::new();
            for off in (0..bytes.len()).step_by(8) {
                insns.push(Insn::from_bytes(&bytes[off..off + 8]));
            }
            assert!(validate(&insns).is_ok());
        }
    }

    #[test]
    fn simulate_rejects_truncated_program() {
        assert_eq!(
            simulate(&[0x06, 0x00], 0, AUDIT_ARCH_X86_64, &[]),
            Err(ERR_INVALID_PROGRAM)
        );
    }

    #[test]
    fn simulate_rejects_unsupported_instruction() {
        // code 0x80 (MISC class) is outside the interpreter's subset.
        let prog = [0x80u8, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00];
        assert_eq!(simulate(&prog, 0, AUDIT_ARCH_X86_64, &[]), Err(ERR_INVALID_PROGRAM));
    }

    #[test]
    fn build_validate_simulate_roundtrip_via_ffi() {
        // The exact contract the Python loader exercises: build -> bytes,
        // validate -> 0, simulate -> verdict, free -> safe. This policy
        // denies socket (41) under a default-allow model: header (4) +
        // one deny jeq + ret allow + ret errno = 7 instructions.
        let json = b"{\"arch\":\"x86_64\",\"default_action\":2147418112,\"deny\":[41],\"deny_flags\":[],\"allow\":[],\"allow_flags\":[]}";
        let mut out: *mut u8 = std::ptr::null_mut();
        let mut out_len: usize = 0;
        let rc = unsafe {
            nyrqis_seccomp_build_program(json.as_ptr(), json.len(), AUDIT_ARCH_X86_64, &mut out, &mut out_len)
        };
        assert_eq!(rc, 0);
        assert!(!out.is_null());
        assert_eq!(out_len, 7 * 8);

        let vrc = unsafe { nyrqis_seccomp_validate_program(out, out_len) };
        assert_eq!(vrc, 0);

        // socket (41) denied; read (0) allowed.
        let deny_verdict = unsafe {
            nyrqis_seccomp_simulate(out, out_len, 41, AUDIT_ARCH_X86_64, std::ptr::null(), 0)
        };
        assert_eq!(deny_verdict, (SECCOMP_RET_ERRNO | EPERM) as i64);
        let allow_verdict = unsafe {
            nyrqis_seccomp_simulate(out, out_len, 0, AUDIT_ARCH_X86_64, std::ptr::null(), 0)
        };
        assert_eq!(allow_verdict, SECCOMP_RET_ALLOW as i64);

        unsafe { nyrqis_seccomp_free(out) };
        unsafe { nyrqis_seccomp_free(std::ptr::null_mut()) }; // NULL is safe
    }
}
