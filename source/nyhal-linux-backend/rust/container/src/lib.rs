//! Nyrqis container launch-plan primitives — ADR-0020 migration priority #5.
//!
//! **Implemented 2026-08-13.** The pure, well-bounded computations the
//! container manager makes when launching a container (NPS-017 §4.1 /
//! NPS-010 §7): the launcher argv (no shell interpolation,
//! FIND-BACKEND-004), the cgroup v1/v2 resource plan (release_agent
//! hardening, FIND-BACKEND-003), the `--map-root-user` uid/gid maps,
//! and the NPS-010 §4 lifecycle state machine. These are
//! platform-critical execution paths (container launch) that under
//! ADR-0020's platform-boundary rule must not depend on the Python
//! interpreter in their shipped form; the Python reference
//! (`backend/container.py`) remains the correctness floor and the
//! byte-identical differential gate verifies Rust ≡ floor.
//!
//! Each FFI entry point is a pure function of plain-data arguments: no
//! shared mutable state, no pointers into Python objects (the ABI rule
//! of ADR-0020 / ABI-001). Output buffers are `libc::malloc`'d by the
//! crate and freed by the caller through `nyrqis_container_free` (the
//! seccomp/syscalls/nyfs/ipc ownership contract).
//!
//! Wire formats are canonical (the pure-Python floor in
//! `backend/container_codec.py` produces **byte-identical** output, which
//! the differential conformance gate verifies):
//!
//! ```text
//! launcher_argv:
//!   0   4  magic "NYRQ"
//!   4   1  wire version (1)
//!   5   4  argv_count (u32 LE) + count × (u32 len + bytes)
//!
//! cgroup_plan:
//!   0   4  magic "NYRQ"
//!   4   1  wire version (1)
//!   5   4  v1_count + v1_count × (path, pairs)
//!            where path   = u32 len + bytes
//!                  pairs  = u32 pair_count + pair_count × (u32 klen + key, u32 vlen + val)
//!       4  v2_count + v2_count × (u32 klen + key, u32 vlen + val)
//!
//! root_maps:
//!   0   4  magic "NYRQ"
//!   4   1  wire version (1)
//!   5   3 × (u32 len + bytes)   [setgroups, uid_map, gid_map]
//! ```
//!
//! `transition_valid` is a scalar (no wire): 0 = valid transition,
//! `ERR_INVALID_TRANSITION` (-4098) = pair not allowed by NPS-010 §4,
//! `ERR_INVALID_ARGS` (-22/EINVAL) = state index out of range.
//!
//! Return convention: **0 on success or a negative value** — `-errno`
//! for real failures, `ERR_INVALID_WIRE` (-4097) for a malformed
//! command flat buffer, `ERR_INTERNAL` (-4096) for module failures.
//! -4096/-4097/-4098 are outside the errno range (1..=4095) by contract
//! so the `-errno → OSError` mapping on the Python side can never
//! misreport them.

use std::os::raw::{c_uchar, c_void};

/// Module ABI version (semver-major*10000 + minor*100 + patch).
pub const ABI_VERSION: u32 = 0x0001_0000;

// NyrqisErr codes (negative i32 returns). ERR_INTERNAL, ERR_INVALID_WIRE
// and ERR_INVALID_TRANSITION are OUTSIDE the errno range (1..=4095) by
// contract: -errno maps to OSError on the Python side, so in-range codes
// would be misreported.
pub const ERR_INVALID_ARGS: i32 = -22; // EINVAL — null pointers / out-of-range state
pub const ERR_TOO_LARGE: i32 = -27; // EFBIG — beyond the sanity bound
pub const ERR_INTERNAL: i32 = -4096;
pub const ERR_INVALID_WIRE: i32 = -4097; // malformed command flat buffer
pub const ERR_INVALID_TRANSITION: i32 = -4098; // NPS-010 §4 pair not allowed

/// Total output sanity bound (16 MiB).
const MAX_WIRE_BYTES: usize = 16 * 1024 * 1024;
/// Per-field length bound (1 MiB).
const MAX_FIELD_BYTES: usize = 1 * 1024 * 1024;
/// Maximum number of command entries in the flat buffer.
const MAX_COMMAND_ENTRIES: u32 = 4096;

const WIRE_VERSION: u8 = 1;
const MAGIC: &[u8; 4] = b"NYRQ";

// Early-return helpers for the two error styles the FFI entry points
// use. The `?` operator cannot be used here: these functions return
// i32 (the error code itself), not Result/Option.
macro_rules! unwrap_err {
    ($e:expr) => {
        match $e {
            Ok(v) => v,
            Err(e) => return e,
        }
    };
}
macro_rules! unwrap_wire {
    ($e:expr) => {
        match $e {
            Some(v) => v,
            None => return ERR_INVALID_WIRE,
        }
    };
}

/// Report the module ABI version.
#[no_mangle]
pub extern "C" fn nyrqis_container_version() -> u32 {
    ABI_VERSION
}

/// Build the container's launcher argv — the exact argv handed to
/// `os.execv` inside the new namespaces (FIND-BACKEND-004: hostname and
/// command are argv entries, never shell-interpolated). The argv is
/// `[python_path, launcher_path, --hostname, hostname]` plus, when
/// `policy_path` is non-empty (seccomp on), `[--policy-file, policy,
/// (--default-deny)]` plus `["--"]` plus the command entries.
///
/// `command_flat` is the pre-framed command list (`[u32 len + bytes]*`).
/// The output buffer is `libc::malloc`'d here and freed by the caller
/// via `nyrqis_container_free`. Returns 0, -errno, or ERR_INVALID_WIRE.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_container_launcher_argv(
    python_path: *const c_uchar,
    python_path_len: u32,
    launcher_path: *const c_uchar,
    launcher_path_len: u32,
    hostname: *const c_uchar,
    hostname_len: u32,
    policy_path: *const c_uchar,
    policy_path_len: u32,
    default_deny: u8,
    command_flat: *const c_uchar,
    command_flat_len: u32,
    out_ptr: *mut *mut c_uchar,
    out_len: *mut usize,
) -> i32 {
    if out_ptr.is_null() || out_len.is_null() {
        return ERR_INVALID_ARGS;
    }
    let python_slice = unwrap_err!(slice_field(python_path, python_path_len));
    let launcher_slice = unwrap_err!(slice_field(launcher_path, launcher_path_len));
    let hostname_slice = unwrap_err!(slice_field(hostname, hostname_len));
    let policy_slice = unwrap_err!(slice_field(policy_path, policy_path_len));
    let flat_slice = unwrap_err!(slice_data(command_flat, command_flat_len));
    let command_entries = unwrap_wire!(split_flat(flat_slice));

    let mut entries: Vec<Vec<u8>> = Vec::new();
    entries.push(python_slice.to_vec());
    entries.push(launcher_slice.to_vec());
    entries.push(b"--hostname".to_vec());
    entries.push(hostname_slice.to_vec());
    if !policy_slice.is_empty() {
        entries.push(b"--policy-file".to_vec());
        entries.push(policy_slice.to_vec());
        if default_deny != 0 {
            entries.push(b"--default-deny".to_vec());
        }
    }
    entries.push(b"--".to_vec());
    for cmd in command_entries {
        entries.push(cmd.to_vec());
    }

    let total = 5usize
        .checked_add(4) // argv_count prefix
        .and_then(|n| {
            entries.iter().try_fold(n, |acc, e| acc.checked_add(4 + e.len()))
        });
    let total = match total {
        Some(n) if n <= MAX_WIRE_BYTES => n,
        _ => return ERR_TOO_LARGE,
    };

    let mut wire = Vec::with_capacity(total);
    wire.extend_from_slice(MAGIC);
    wire.push(WIRE_VERSION);
    wire.extend_from_slice(&(entries.len() as u32).to_le_bytes());
    for e in entries.iter() {
        wire.extend_from_slice(&(e.len() as u32).to_le_bytes());
        wire.extend_from_slice(e);
    }

    publish(&wire, out_ptr, out_len)
}

/// Build the container's cgroup resource plan (NPS-010 §7): the v1
/// hierarchy plan (`/sys/fs/cgroup/memory/<id>` and
/// `/sys/fs/cgroup/pids/<id>` with their settings — the memory cgroup
/// carries `notify_on_release=0`, the FIND-BACKEND-003 release_agent
/// hardening) and the v2 unified-hierarchy settings (`memory.max`,
/// `pids.max`, and `cpu.max` when `cpu_quota_us >= 0`). The output is a
/// single wire with both sections (see the module doc). Returns 0,
/// -errno, or ERR_INVALID_WIRE.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_container_cgroup_plan(
    container_id: *const c_uchar,
    container_id_len: u32,
    memory_mb: u64,
    pid_limit: u64,
    cpu_quota_us: i64,
    cpu_period_us: u64,
    out_ptr: *mut *mut c_uchar,
    out_len: *mut usize,
) -> i32 {
    if out_ptr.is_null() || out_len.is_null() {
        return ERR_INVALID_ARGS;
    }
    let id_slice = unwrap_err!(slice_field(container_id, container_id_len));

    let mem_bytes = memory_mb * 1024 * 1024;
    let mem_str = format!("{}", mem_bytes);

    // v1 plan: (path, [(key, value)]).
    let mut v1_path = b"/sys/fs/cgroup/memory/".to_vec();
    v1_path.extend_from_slice(id_slice);
    let mut v1_pids = b"/sys/fs/cgroup/pids/".to_vec();
    v1_pids.extend_from_slice(id_slice);
    let v1: Vec<(Vec<u8>, Vec<(&[u8], Vec<u8>)>)> = vec![
        (
            v1_path,
            vec![
                (b"memory.limit_in_bytes".as_slice(), mem_str.clone().into_bytes()),
                (b"notify_on_release".as_slice(), b"0".to_vec()),
            ],
        ),
        (
            v1_pids,
            vec![(b"pids.max".as_slice(), format!("{}", pid_limit).into_bytes())],
        ),
    ];

    // v2 settings: (key, value).
    let mut v2: Vec<(Vec<u8>, Vec<u8>)> = vec![
        (b"memory.max".to_vec(), mem_str.into_bytes()),
        (b"pids.max".to_vec(), format!("{}", pid_limit).into_bytes()),
    ];
    if cpu_quota_us >= 0 {
        v2.push((
            b"cpu.max".to_vec(),
            format!("{} {}", cpu_quota_us, cpu_period_us).into_bytes(),
        ));
    }

    let total = 5usize
        .checked_add(4) // v1_count
        .and_then(|n| {
            v1.iter().try_fold(n, |acc, (path, pairs)| {
                acc.checked_add(4 + path.len())
                    .and_then(|m| m.checked_add(4))
                    .and_then(|m| {
                        pairs.iter().try_fold(m, |a, (k, v)| {
                            a.checked_add(4 + k.len()).and_then(|b| b.checked_add(4 + v.len()))
                        })
                    })
            })
        })
        .and_then(|n| n.checked_add(4)) // v2_count
        .and_then(|n| {
            v2.iter().try_fold(n, |acc, (k, v)| {
                acc.checked_add(4 + k.len()).and_then(|m| m.checked_add(4 + v.len()))
            })
        });
    let total = match total {
        Some(n) if n <= MAX_WIRE_BYTES => n,
        _ => return ERR_TOO_LARGE,
    };

    let mut wire = Vec::with_capacity(total);
    wire.extend_from_slice(MAGIC);
    wire.push(WIRE_VERSION);
    wire.extend_from_slice(&(v1.len() as u32).to_le_bytes());
    for (path, pairs) in v1.iter() {
        wire.extend_from_slice(&(path.len() as u32).to_le_bytes());
        wire.extend_from_slice(path);
        wire.extend_from_slice(&(pairs.len() as u32).to_le_bytes());
        for (k, v) in pairs.iter() {
            wire.extend_from_slice(&(k.len() as u32).to_le_bytes());
            wire.extend_from_slice(k);
            wire.extend_from_slice(&(v.len() as u32).to_le_bytes());
            wire.extend_from_slice(v);
        }
    }
    wire.extend_from_slice(&(v2.len() as u32).to_le_bytes());
    for (k, v) in v2.iter() {
        wire.extend_from_slice(&(k.len() as u32).to_le_bytes());
        wire.extend_from_slice(k);
        wire.extend_from_slice(&(v.len() as u32).to_le_bytes());
        wire.extend_from_slice(v);
    }

    publish(&wire, out_ptr, out_len)
}

/// Build the `--map-root-user` uid/gid map contents (written by the
/// namespace-setup child into `/proc/self/{setgroups,uid_map,gid_map}`
/// — `setgroups=deny` first, then the caller mapped to root). The
/// caller MUST capture uid/gid BEFORE `unshare(CLONE_NEWUSER)` (inside
/// the unmapped namespace getuid() reports 65534). Output wire: three
/// length-prefixed byte strings. Returns 0 or ERR_INVALID_ARGS.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_container_root_maps(
    uid: u32,
    gid: u32,
    out_ptr: *mut *mut c_uchar,
    out_len: *mut usize,
) -> i32 {
    if out_ptr.is_null() || out_len.is_null() {
        return ERR_INVALID_ARGS;
    }
    let contents: [Vec<u8>; 3] = [
        b"deny\n".to_vec(),
        format!("0 {} 1\n", uid).into_bytes(),
        format!("0 {} 1\n", gid).into_bytes(),
    ];
    let total = 5usize
        .checked_add(
            contents.iter().map(|c| 4 + c.len()).sum::<usize>(),
        );
    let total = match total {
        Some(n) if n <= MAX_WIRE_BYTES => n,
        _ => return ERR_TOO_LARGE,
    };
    let mut wire = Vec::with_capacity(total);
    wire.extend_from_slice(MAGIC);
    wire.push(WIRE_VERSION);
    for c in contents.iter() {
        wire.extend_from_slice(&(c.len() as u32).to_le_bytes());
        wire.extend_from_slice(c);
    }
    publish(&wire, out_ptr, out_len)
}

/// The NPS-010 §4 container lifecycle state machine. State indices:
/// 0 CREATED, 1 RUNNING, 2 SUSPENDED, 3 TERMINATED. Returns 0 for a
/// legal transition, `ERR_INVALID_TRANSITION` for a disallowed pair,
/// `ERR_INVALID_ARGS` (-22/EINVAL) for an out-of-range state index.
#[no_mangle]
pub extern "C" fn nyrqis_container_transition_valid(from_state: u8, to_state: u8) -> i32 {
    if from_state > 3 || to_state > 3 {
        return ERR_INVALID_ARGS;
    }
    match (from_state, to_state) {
        // CREATED -> RUNNING
        (0, 1)
        // RUNNING -> SUSPENDED, TERMINATED
        | (1, 2) | (1, 3)
        // SUSPENDED -> RUNNING, TERMINATED
        | (2, 1) | (2, 3) => 0,
        _ => ERR_INVALID_TRANSITION,
    }
}

/// Free an output buffer previously returned by this module (the
/// seccomp/syscalls/nyfs/ipc ownership contract). No-op on a null
/// pointer.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_container_free(ptr: *mut c_uchar) {
    if !ptr.is_null() {
        unsafe { libc::free(ptr as *mut c_void) };
    }
}

/// Convert a caller pointer+length into a slice, enforcing the 1 MiB
/// per-field bound.
unsafe fn slice_field<'a>(ptr: *const c_uchar, len: u32) -> Result<&'a [u8], i32> {
    slice_checked(ptr, len, MAX_FIELD_BYTES)
}

/// Convert a caller pointer+length into a slice, enforcing the 16 MiB
/// total-wire bound (the command flat buffer).
unsafe fn slice_data<'a>(ptr: *const c_uchar, len: u32) -> Result<&'a [u8], i32> {
    slice_checked(ptr, len, MAX_WIRE_BYTES)
}

unsafe fn slice_checked<'a>(
    ptr: *const c_uchar,
    len: u32,
    max: usize,
) -> Result<&'a [u8], i32> {
    if len as usize > max {
        return Err(ERR_TOO_LARGE);
    }
    if len == 0 {
        return Ok(&[]);
    }
    if ptr.is_null() {
        return Err(ERR_INVALID_ARGS);
    }
    Ok(std::slice::from_raw_parts(ptr, len as usize))
}

/// Validate and split the command flat buffer (`[u32 len + bytes]*`):
/// bounded entry count and per-entry size, exact consumption. A fn item
/// (not a closure) so the output slices bind to the input's lifetime
/// explicitly.
fn split_flat<'a>(flat: &'a [u8]) -> Option<Vec<&'a [u8]>> {
    let mut out: Vec<&'a [u8]> = Vec::new();
    let mut pos = 0usize;
    while pos < flat.len() {
        if flat.len() - pos < 4 {
            return None; // truncated length prefix
        }
        let len = u32::from_le_bytes([
            flat[pos], flat[pos + 1], flat[pos + 2], flat[pos + 3],
        ]);
        pos += 4;
        if len as usize > MAX_FIELD_BYTES {
            return None;
        }
        if flat.len() - pos < len as usize {
            return None; // truncated entry bytes
        }
        out.push(&flat[pos..pos + len as usize]);
        pos += len as usize;
        if out.len() as u32 > MAX_COMMAND_ENTRIES {
            return None;
        }
    }
    Some(out)
}

/// Copy `bytes` into a freshly malloc'd buffer (the module's ownership
/// contract).
unsafe fn publish(bytes: &[u8], out_ptr: *mut *mut c_uchar, out_len: *mut usize) -> i32 {
    let ptr = unsafe { libc_malloc(bytes.len().max(1)) };
    if ptr.is_null() {
        return ERR_INTERNAL;
    }
    if !bytes.is_empty() {
        std::ptr::copy_nonoverlapping(bytes.as_ptr(), ptr as *mut c_uchar, bytes.len());
    }
    *out_ptr = ptr as *mut c_uchar;
    *out_len = bytes.len();
    0
}

unsafe fn libc_malloc(size: usize) -> *mut c_void {
    unsafe { libc::malloc(size) }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Frame a command list as the flat buffer the FFI consumes.
    fn flat_of(entries: &[&[u8]]) -> Vec<u8> {
        let mut f = Vec::new();
        for e in entries {
            f.extend_from_slice(&(e.len() as u32).to_le_bytes());
            f.extend_from_slice(e);
        }
        f
    }

    unsafe fn launcher_argv_wire(
        py: &[u8], launcher: &[u8], host: &[u8], pol: &[u8], deny: u8, flat: &[u8],
    ) -> (i32, Vec<u8>) {
        let mut out: *mut c_uchar = std::ptr::null_mut();
        let mut out_len: usize = 0;
        let rc = nyrqis_container_launcher_argv(
            py.as_ptr(), py.len() as u32,
            launcher.as_ptr(), launcher.len() as u32,
            host.as_ptr(), host.len() as u32,
            pol.as_ptr(), pol.len() as u32,
            deny,
            flat.as_ptr(), flat.len() as u32,
            &mut out, &mut out_len,
        );
        let bytes = if out.is_null() {
            Vec::new()
        } else {
            let v = unsafe { std::slice::from_raw_parts(out, out_len).to_vec() };
            unsafe { nyrqis_container_free(out) };
            v
        };
        (rc, bytes)
    }

    unsafe fn cgroup_plan_wire(
        id: &[u8], mem: u64, pids: u64, quota: i64, period: u64,
    ) -> (i32, Vec<u8>) {
        let mut out: *mut c_uchar = std::ptr::null_mut();
        let mut out_len: usize = 0;
        let rc = nyrqis_container_cgroup_plan(
            id.as_ptr(), id.len() as u32,
            mem, pids, quota, period,
            &mut out, &mut out_len,
        );
        let bytes = if out.is_null() {
            Vec::new()
        } else {
            let v = unsafe { std::slice::from_raw_parts(out, out_len).to_vec() };
            unsafe { nyrqis_container_free(out) };
            v
        };
        (rc, bytes)
    }

    unsafe fn root_maps_wire(uid: u32, gid: u32) -> (i32, Vec<u8>) {
        let mut out: *mut c_uchar = std::ptr::null_mut();
        let mut out_len: usize = 0;
        let rc = nyrqis_container_root_maps(uid, gid, &mut out, &mut out_len);
        let bytes = if out.is_null() {
            Vec::new()
        } else {
            let v = unsafe { std::slice::from_raw_parts(out, out_len).to_vec() };
            unsafe { nyrqis_container_free(out) };
            v
        };
        (rc, bytes)
    }

    // Test-local wire readers (fn items with explicit lifetimes).
    fn read_u32<'a>(b: &'a [u8], pos: &mut usize) -> Option<u32> {
        if *pos + 4 > b.len() {
            return None;
        }
        let v = u32::from_le_bytes([b[*pos], b[*pos + 1], b[*pos + 2], b[*pos + 3]]);
        *pos += 4;
        Some(v)
    }

    fn read_str<'a>(b: &'a [u8], pos: &mut usize) -> Option<&'a [u8]> {
        let len = read_u32(b, pos)? as usize;
        if *pos + len > b.len() {
            return None;
        }
        let s = &b[*pos..*pos + len];
        *pos += len;
        Some(s)
    }

    #[test]
    fn abi_version_is_current() {
        assert_eq!(ABI_VERSION, 0x0001_0000);
    }

    #[test]
    fn err_codes_are_outside_errno_range() {
        for code in [ERR_INTERNAL, ERR_INVALID_WIRE, ERR_INVALID_TRANSITION] {
            assert!(!(1..=4095).contains(&(-code)));
        }
        assert_eq!(ERR_INVALID_ARGS, -libc::EINVAL);
        assert_eq!(ERR_TOO_LARGE, -libc::EFBIG);
    }

    #[test]
    fn ffi_symbols_exist() {
        let _ = nyrqis_container_version;
        let _ = nyrqis_container_launcher_argv;
        let _ = nyrqis_container_cgroup_plan;
        let _ = nyrqis_container_root_maps;
        let _ = nyrqis_container_transition_valid;
        let _ = nyrqis_container_free;
    }

    #[test]
    fn launcher_argv_layout_is_canonical() {
        // No policy: argv is [py, launcher, --hostname, ctr1, --, cmd].
        let flat = flat_of(&[b"/bin/sh".as_slice()]);
        let (rc, w) = unsafe {
            launcher_argv_wire(
                b"/usr/bin/python3", b"/opt/launcher.py", b"ctr1", b"", 0, &flat,
            )
        };
        assert_eq!(rc, 0);
        assert_eq!(&w[0..4], b"NYRQ");
        assert_eq!(w[4], 1);
        assert_eq!(&w[5..9], &6u32.to_le_bytes()); // 6 argv entries
        let mut pos = 9;
        for expected in [
            b"/usr/bin/python3".as_slice(),
            b"/opt/launcher.py".as_slice(),
            b"--hostname".as_slice(),
            b"ctr1".as_slice(),
            b"--".as_slice(),
            b"/bin/sh".as_slice(),
        ] {
            let s = read_str(&w, &mut pos).unwrap();
            assert_eq!(s, expected);
        }
        assert_eq!(pos, w.len());
    }

    #[test]
    fn launcher_argv_with_policy_and_default_deny() {
        let flat = flat_of(&[b"/bin/echo".as_slice(), b"hi".as_slice()]);
        let (rc, w) = unsafe {
            launcher_argv_wire(
                b"/usr/bin/python3", b"/opt/launcher.py", b"evil; rm -rf /",
                b"/tmp/policy.json", 1, &flat,
            )
        };
        assert_eq!(rc, 0);
        let mut pos = 9;
        let mut entries = Vec::new();
        while pos < w.len() {
            entries.push(read_str(&w, &mut pos).unwrap().to_vec());
        }
        assert_eq!(
            entries,
            vec![
                b"/usr/bin/python3".to_vec(),
                b"/opt/launcher.py".to_vec(),
                b"--hostname".to_vec(),
                b"evil; rm -rf /".to_vec(),
                b"--policy-file".to_vec(),
                b"/tmp/policy.json".to_vec(),
                b"--default-deny".to_vec(),
                b"--".to_vec(),
                b"/bin/echo".to_vec(),
                b"hi".to_vec(),
            ]
        );
    }

    #[test]
    fn launcher_argv_default_deny_requires_policy() {
        // default_deny without a policy path must not add the flag (the
        // Python floor's seccomp guard).
        let flat = flat_of(&[]);
        let (rc, w) = unsafe {
            launcher_argv_wire(
                b"py", b"launcher.py", b"h", b"", 1, &flat,
            )
        };
        assert_eq!(rc, 0);
        let mut pos = 9;
        let mut entries = Vec::new();
        while pos < w.len() {
            entries.push(read_str(&w, &mut pos).unwrap().to_vec());
        }
        assert_eq!(
            entries,
            vec![
                b"py".to_vec(),
                b"launcher.py".to_vec(),
                b"--hostname".to_vec(),
                b"h".to_vec(),
                b"--".to_vec(),
            ]
        );
    }

    #[test]
    fn launcher_argv_rejects_malformed_flat() {
        let bad = [0u8, 0, 0, 5, 1, 2]; // claims 5 bytes, has 2
        let (rc, _) = unsafe {
            launcher_argv_wire(b"py", b"l", b"h", b"", 0, &bad)
        };
        assert_eq!(rc, ERR_INVALID_WIRE);
    }

    #[test]
    fn launcher_argv_rejects_oversized_field() {
        // A single command entry beyond the 1 MiB per-field bound.
        let mut flat = Vec::new();
        flat.extend_from_slice(&(2 * 1024 * 1024u32).to_le_bytes()); // 2 MiB claim
        let (rc, _) = unsafe {
            launcher_argv_wire(b"py", b"l", b"h", b"", 0, &flat)
        };
        assert_eq!(rc, ERR_INVALID_WIRE);
    }

    #[test]
    fn cgroup_plan_layout_is_canonical() {
        let (rc, w) = unsafe { cgroup_plan_wire(b"ctr-1", 128, 16, -1, 100000) };
        assert_eq!(rc, 0);
        assert_eq!(&w[0..4], b"NYRQ");
        assert_eq!(w[4], 1);
        let mut pos = 5;
        assert_eq!(read_u32(&w, &mut pos), Some(2)); // v1 count
        // Entry 1: memory cgroup with limit + notify_on_release=0.
        let path = read_str(&w, &mut pos).unwrap();
        assert_eq!(path, b"/sys/fs/cgroup/memory/ctr-1");
        let n = read_u32(&w, &mut pos).unwrap();
        assert_eq!(n, 2);
        let k1 = read_str(&w, &mut pos).unwrap();
        let v1 = read_str(&w, &mut pos).unwrap();
        assert_eq!(k1, b"memory.limit_in_bytes");
        assert_eq!(v1, b"134217728"); // 128 MiB
        let k2 = read_str(&w, &mut pos).unwrap();
        let v2 = read_str(&w, &mut pos).unwrap();
        assert_eq!(k2, b"notify_on_release");
        assert_eq!(v2, b"0");
        // Entry 2: pids cgroup.
        let path = read_str(&w, &mut pos).unwrap();
        assert_eq!(path, b"/sys/fs/cgroup/pids/ctr-1");
        let n = read_u32(&w, &mut pos).unwrap();
        assert_eq!(n, 1);
        let k = read_str(&w, &mut pos).unwrap();
        let v = read_str(&w, &mut pos).unwrap();
        assert_eq!(k, b"pids.max");
        assert_eq!(v, b"16");
        // v2 section: memory.max + pids.max, no cpu.max (quota absent).
        let n2 = read_u32(&w, &mut pos).unwrap();
        assert_eq!(n2, 2);
        let k = read_str(&w, &mut pos).unwrap();
        let v = read_str(&w, &mut pos).unwrap();
        assert_eq!(k, b"memory.max");
        assert_eq!(v, b"134217728");
        let k = read_str(&w, &mut pos).unwrap();
        let v = read_str(&w, &mut pos).unwrap();
        assert_eq!(k, b"pids.max");
        assert_eq!(v, b"16");
        assert_eq!(pos, w.len());
    }

    #[test]
    fn cgroup_plan_includes_cpu_max_when_quota_set() {
        let (rc, w) = unsafe { cgroup_plan_wire(b"c", 64, 8, 50000, 100000) };
        assert_eq!(rc, 0);
        let mut pos = 5;
        assert_eq!(read_u32(&w, &mut pos), Some(2));
        // Skip the two v1 entries.
        for _ in 0..2 {
            read_str(&w, &mut pos).unwrap();
            let n = read_u32(&w, &mut pos).unwrap();
            for _ in 0..n {
                read_str(&w, &mut pos).unwrap();
                read_str(&w, &mut pos).unwrap();
            }
        }
        assert_eq!(read_u32(&w, &mut pos), Some(3)); // v2 count incl. cpu.max
        let k = read_str(&w, &mut pos).unwrap();
        let v = read_str(&w, &mut pos).unwrap();
        assert_eq!(k, b"memory.max");
        assert_eq!(v, b"67108864");
        let k = read_str(&w, &mut pos).unwrap();
        let v = read_str(&w, &mut pos).unwrap();
        assert_eq!(k, b"pids.max");
        assert_eq!(v, b"8");
        let k = read_str(&w, &mut pos).unwrap();
        let v = read_str(&w, &mut pos).unwrap();
        assert_eq!(k, b"cpu.max");
        assert_eq!(v, b"50000 100000");
        assert_eq!(pos, w.len());
    }

    #[test]
    fn root_maps_contents_are_canonical() {
        let (rc, w) = unsafe { root_maps_wire(1000, 1000) };
        assert_eq!(rc, 0);
        assert_eq!(&w[0..4], b"NYRQ");
        assert_eq!(w[4], 1);
        let mut pos = 5;
        assert_eq!(read_str(&w, &mut pos), Some(b"deny\n".as_slice()));
        assert_eq!(read_str(&w, &mut pos), Some(b"0 1000 1\n".as_slice()));
        assert_eq!(read_str(&w, &mut pos), Some(b"0 1000 1\n".as_slice()));
        assert_eq!(pos, w.len());
    }

    #[test]
    fn transition_valid_table_matches_nps010() {
        // Legal: CREATED→RUNNING, RUNNING→{SUSPENDED,TERMINATED},
        // SUSPENDED→{RUNNING,TERMINATED}.
        for (f, t) in [(0, 1), (1, 2), (1, 3), (2, 1), (2, 3)] {
            assert_eq!(nyrqis_container_transition_valid(f, t), 0, "{} -> {}", f, t);
        }
        // Everything else (incl. all TERMINATED exits and self-loops).
        for f in 0u8..=3 {
            for t in 0u8..=3 {
                let legal = matches!((f, t), (0, 1) | (1, 2) | (1, 3) | (2, 1) | (2, 3));
                let expected = if legal { 0 } else { ERR_INVALID_TRANSITION };
                assert_eq!(
                    nyrqis_container_transition_valid(f, t), expected,
                    "{} -> {}", f, t,
                );
            }
        }
    }

    #[test]
    fn transition_valid_rejects_out_of_range_state() {
        assert_eq!(nyrqis_container_transition_valid(0, 4), ERR_INVALID_ARGS);
        assert_eq!(nyrqis_container_transition_valid(4, 0), ERR_INVALID_ARGS);
        assert_eq!(nyrqis_container_transition_valid(9, 9), ERR_INVALID_ARGS);
    }

    #[test]
    fn free_null_is_safe() {
        unsafe { nyrqis_container_free(std::ptr::null_mut()) };
    }
}
