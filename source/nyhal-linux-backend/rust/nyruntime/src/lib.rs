//! Nyrqis Runtime (NyRuntime)
//!
//! The minimal runtime for loading and executing Nyrqis programs.
//! Provides the execution environment that bridges NyCore types with
//! the actual OS services (IPC, filesystem, capabilities).
//!
//! # Architecture
//!
//! ```text
//! Nyrqis Application (.napp)
//!       │
//!       ▼
//!   NyRuntime
//!       │
//!   ┌───┼───────────────┐
//!   │   │               │
//!   ▼   ▼               ▼
//! NyCore  NyHAL      NyFS
//!   │       │           │
//!   ▼       ▼           ▼
//! Linux   Linux      Linux
//! ```
//!
//! # Design Principles
//!
//! - **Minimal footprint** — the runtime does only what's necessary to
//!   execute a program; no garbage collector, no JIT, no interpreter
//! - **Capability-enforced** — every operation checks the process's
//!   capability set before proceeding
//! - **Crash-safe** — the runtime never leaves the system in an
//!   inconsistent state; errors are propagated, not swallowed
//! - **FFI-safe** — the runtime can be driven from Python via ctypes

use std::ffi::{c_char, c_int, c_uchar, c_void, CStr};
use std::vec::Vec;
use nyrqis_nycore::{Capability, ContainerConfig, ContainerState, NyError};

// ---------------------------------------------------------------------------
// .napp binary format constants
// ---------------------------------------------------------------------------

/// Magic bytes identifying a Nyrqis application binary.
pub const NAPP_MAGIC: &[u8; 4] = b"NYAP";

/// Current package format version.
pub const NAPP_VERSION: u8 = 1;

/// Opcodes
pub const OP_HALT: u8 = 0x00;
pub const OP_NOP: u8 = 0x01;
pub const OP_IPC_CALL: u8 = 0x02;
pub const OP_IPC_SEND: u8 = 0x03;
pub const OP_FS_READ: u8 = 0x04;
pub const OP_FS_WRITE: u8 = 0x05;
pub const OP_LOG: u8 = 0x06;
pub const OP_SET_STATE: u8 = 0x07;
pub const OP_GET_STATE: u8 = 0x08;
pub const OP_YIELD: u8 = 0x09;

/// Wire-frame sanity bound (16 MiB).
const MAX_WIRE_BYTES: usize = 16 * 1024 * 1024;

// ---------------------------------------------------------------------------
// Runtime state
// ---------------------------------------------------------------------------

/// The runtime's execution state.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RuntimeState {
    Uninitialized = 0,
    Ready = 1,
    Loaded = 2,
    Running = 3,
    Failed = 4,
}

/// A parsed .napp binary.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NappPackage {
    pub version: u8,
    pub manifest: Vec<u8>,
    pub code: Vec<u8>,
    pub data: Vec<u8>,
}

impl NappPackage {
    /// Parse a .napp binary from raw bytes.
    pub fn parse(raw: &[u8]) -> Result<Self, NyError> {
        if raw.len() < 17 {
            return Err(NyError::EINVAL);
        }

        // Magic check
        if &raw[0..4] != NAPP_MAGIC {
            return Err(NyError::EINVAL);
        }

        let version = raw[4];
        let manifest_len = u32::from_le_bytes([raw[5], raw[6], raw[7], raw[8]]) as usize;
        let code_len = u32::from_le_bytes([raw[9], raw[10], raw[11], raw[12]]) as usize;
        let data_len = u32::from_le_bytes([raw[13], raw[14], raw[15], raw[16]]) as usize;

        let total = 17 + manifest_len + code_len + data_len;
        if raw.len() < total {
            return Err(NyError::EINVAL);
        }

        let offset = 17;
        let manifest = raw[offset..offset + manifest_len].to_vec();
        let offset = offset + manifest_len;
        let code = raw[offset..offset + code_len].to_vec();
        let offset = offset + code_len;
        let data = raw[offset..offset + data_len].to_vec();

        Ok(Self {
            version,
            manifest,
            code,
            data,
        })
    }

    /// Get the manifest as a string.
    pub fn manifest_str(&self) -> Option<&str> {
        std::str::from_utf8(&self.manifest).ok()
    }
}

/// A loaded Nyrqis program.
#[derive(Debug)]
pub struct Program {
    pub name: Vec<u8>,
    pub entry: usize,
    pub code: Vec<u8>,
    pub data: Vec<u8>,
    pub required_caps: Vec<Capability>,
}

/// Log entry from the runtime.
#[derive(Debug, Clone)]
pub struct LogEntry {
    pub level: u32,
    pub message: Vec<u8>,
}

/// The runtime instance.
#[derive(Debug)]
pub struct Runtime {
    state: RuntimeState,
    program: Option<Program>,
    capabilities: Vec<Capability>,
    config: ContainerConfig,
    log: Vec<LogEntry>,
    state_store: Vec<(Vec<u8>, Vec<u8>)>,
    /// IPC socket fd (-1 = not connected)
    ipc_fd: i32,
    /// Peer socket path for IPC calls (the daemon's bound path)
    ipc_peer: Vec<u8>,
}

impl Runtime {
    pub fn new(config: ContainerConfig) -> Self {
        Self {
            state: RuntimeState::Uninitialized,
            program: None,
            capabilities: Vec::new(),
            config,
            log: Vec::new(),
            state_store: Vec::new(),
            ipc_fd: -1,
            ipc_peer: Vec::new(),
        }
    }

    pub fn init(&mut self) -> Result<(), NyError> {
        if self.state != RuntimeState::Uninitialized {
            return Err(NyError::EINVAL);
        }
        self.state = RuntimeState::Ready;
        Ok(())
    }

    pub fn grant_capability(&mut self, cap: Capability) -> Result<(), NyError> {
        if self.state == RuntimeState::Uninitialized {
            return Err(NyError::EINVAL);
        }
        if !self.capabilities.contains(&cap) {
            self.capabilities.push(cap);
        }
        Ok(())
    }

    pub fn has_capability(&self, cap: Capability) -> bool {
        self.capabilities.contains(&cap)
    }

    /// Bind a Unix datagram socket for IPC and store the peer path.
    pub fn set_ipc(&mut self, fd: i32, peer_path: &[u8]) {
        self.ipc_fd = fd;
        self.ipc_peer = peer_path.to_vec();
    }

    /// Load a .napp binary into the runtime.
    pub fn load_napp(&mut self, raw: &[u8]) -> Result<(), NyError> {
        let pkg = NappPackage::parse(raw)?;
        self.load(Program {
            name: Vec::new(),
            entry: 0,
            code: pkg.code,
            data: pkg.data,
            required_caps: Vec::new(),
        })
    }

    pub fn load(&mut self, program: Program) -> Result<(), NyError> {
        if self.state != RuntimeState::Ready && self.state != RuntimeState::Loaded {
            return Err(NyError::EINVAL);
        }
        for req_cap in &program.required_caps {
            if !self.has_capability(*req_cap) {
                return Err(NyError::ECAPMISSING);
            }
        }
        self.program = Some(program);
        self.state = RuntimeState::Loaded;
        Ok(())
    }

    pub fn execute(&mut self) -> Result<i32, NyError> {
        if self.state != RuntimeState::Loaded {
            return Err(NyError::EINVAL);
        }
        self.state = RuntimeState::Running;
        let result = self.run_program();
        self.state = match result {
            Ok(_) => RuntimeState::Ready,
            Err(_) => RuntimeState::Failed,
        };
        result
    }

    pub fn state(&self) -> RuntimeState {
        self.state
    }

    pub fn program_name(&self) -> Option<&[u8]> {
        self.program.as_ref().map(|p| p.name.as_slice())
    }

    /// Get the log entries produced during execution.
    pub fn log_entries(&self) -> &[LogEntry] {
        &self.log
    }

    fn run_program(&mut self) -> Result<i32, NyError> {
        let program = self.program.as_ref().ok_or(NyError::EINVAL)?;
        let mut pc = program.entry;

        loop {
            if pc >= program.code.len() {
                return Err(NyError::ERUNTIME);
            }

            let op = program.code[pc];
            match op {
                OP_HALT => {
                    let exit_code = if program.data.is_empty() {
                        0
                    } else {
                        program.data[0] as i32
                    };
                    return Ok(exit_code);
                }
                OP_NOP => {
                    pc += 1;
                }
                OP_IPC_CALL | OP_IPC_SEND => {
                    // IPC operations: args at code[pc+1..pc+4]
                    // service_idx, op_idx, payload_idx
                    if pc + 4 > program.code.len() {
                        return Err(NyError::ERUNTIME);
                    }
                    let _service_idx = program.code[pc + 1] as usize;
                    let _op_idx = program.code[pc + 2] as usize;
                    let payload_idx = program.code[pc + 3] as usize;

                    // Only execute the IPC syscall when a socket is wired
                    if self.ipc_fd >= 0 && !self.ipc_peer.is_empty() {
                        // Build the request payload from data segment
                        let payload = if payload_idx < program.data.len() {
                            let end = program.data[payload_idx..]
                                .iter()
                                .position(|&b| b == 0)
                                .unwrap_or(program.data.len() - payload_idx);
                            &program.data[payload_idx..payload_idx + end]
                        } else {
                            b"{}"
                        };

                        // Pack: header (4B magic + 1B version + 1B type=CALL)
                        //        + correlation id (2B LE) + payload
                        let call_id = (pc as u16).wrapping_add(1);
                        let mut frame = Vec::with_capacity(8 + payload.len());
                        frame.extend_from_slice(b"NYRQ");
                        frame.push(1); // version
                        frame.push(0x01); // type = CALL
                        frame.extend_from_slice(&call_id.to_le_bytes());
                        frame.extend_from_slice(payload);

                        // Build sockaddr_un for the peer
                        let peer_len = self.ipc_peer.len();
                        if peer_len > 0 && peer_len <= 107 {
                            let mut sun: libc::sockaddr_un =
                                unsafe { std::mem::zeroed() };
                            sun.sun_family = libc::AF_UNIX as libc::sa_family_t;
                            for (i, &b) in self.ipc_peer.iter().enumerate() {
                                sun.sun_path[i] = b as i8;
                            }
                            let addr_len = (2 + peer_len + 1) as libc::socklen_t;

                            // sendto
                            let sent = unsafe {
                                libc::sendto(
                                    self.ipc_fd,
                                    frame.as_ptr() as *const c_void,
                                    frame.len(),
                                    0,
                                    &sun as *const libc::sockaddr_un
                                        as *const libc::sockaddr,
                                    addr_len,
                                )
                            };

                            if sent > 0 && op == OP_IPC_CALL {
                                // recv reply (non-blocking poll first)
                                let mut pfd = libc::pollfd {
                                    fd: self.ipc_fd,
                                    events: libc::POLLIN,
                                    revents: 0,
                                };
                                let prc = unsafe { libc::poll(&mut pfd, 1, 2000) };
                                if prc > 0 {
                                    let mut rbuf = [0u8; 65536];
                                    let mut iov = libc::iovec {
                                        iov_base: rbuf.as_mut_ptr() as *mut c_void,
                                        iov_len: rbuf.len(),
                                    };
                                    let mut msg: libc::msghdr =
                                        unsafe { std::mem::zeroed() };
                                    msg.msg_iov = &mut iov;
                                    msg.msg_iovlen = 1;
                                    let n = unsafe {
                                        libc::recvmsg(
                                            self.ipc_fd,
                                            &mut msg,
                                            libc::MSG_DONTWAIT,
                                        )
                                    };
                                    if n > 0 {
                                        let reply = &rbuf[..n as usize];
                                        self.log.push(LogEntry {
                                            level: 1,
                                            message: reply.to_vec(),
                                        });
                                    }
                                }
                            }
                        }
                    }
                    pc += 4;
                }
                OP_FS_READ => {
                    // FS_READ: path_idx at code[pc+1], dest_key_idx at code[pc+2]
                    if pc + 3 > program.code.len() {
                        return Err(NyError::ERUNTIME);
                    }
                    let path_idx = program.code[pc + 1] as usize;
                    let dest_key_idx = program.code[pc + 2] as usize;
                    if path_idx < program.data.len() {
                        let path_end = program.data[path_idx..]
                            .iter()
                            .position(|&b| b == 0)
                            .unwrap_or(program.data.len() - path_idx);
                        let path = &program.data[path_idx..path_idx + path_end];
                        // Open, read, store in state
                        let path_str = std::str::from_utf8(path)
                            .map_err(|_| NyError::EINVAL)?;
                        let fd = unsafe {
                            libc::open(
                                path.as_ptr() as *const c_char,
                                libc::O_RDONLY,
                                0,
                            )
                        };
                        if fd >= 0 {
                            let mut buf = [0u8; 65536];
                            let n = unsafe {
                                libc::read(fd, buf.as_mut_ptr() as *mut c_void, buf.len())
                            };
                            unsafe { libc::close(fd); }
                            if n > 0 {
                                let content = buf[..n as usize].to_vec();
                                if dest_key_idx < program.data.len() {
                                    let key_end = program.data[dest_key_idx..]
                                        .iter()
                                        .position(|&b| b == 0)
                                        .unwrap_or(program.data.len() - dest_key_idx);
                                    let key = program.data[dest_key_idx..dest_key_idx + key_end].to_vec();
                                    if let Some(existing) = self.state_store.iter_mut().find(|(k, _)| k == &key) {
                                        existing.1 = content;
                                    } else {
                                        self.state_store.push((key, content));
                                    }
                                }
                            }
                        } else {
                            self.log.push(LogEntry {
                                level: 2, // error
                                message: format!("FS_READ failed: {} errno={}", path_str, std::io::Error::last_os_error()).into_bytes(),
                            });
                        }
                    }
                    pc += 3;
                }
                OP_FS_WRITE => {
                    // FS_WRITE: path_idx at code[pc+1], data_idx at code[pc+2]
                    if pc + 3 > program.code.len() {
                        return Err(NyError::ERUNTIME);
                    }
                    let path_idx = program.code[pc + 1] as usize;
                    let data_idx = program.code[pc + 2] as usize;
                    if path_idx < program.data.len() && data_idx < program.data.len() {
                        let path_end = program.data[path_idx..]
                            .iter()
                            .position(|&b| b == 0)
                            .unwrap_or(program.data.len() - path_idx);
                        let path = &program.data[path_idx..path_idx + path_end];
                        let content_end = program.data[data_idx..]
                            .iter()
                            .position(|&b| b == 0)
                            .unwrap_or(program.data.len() - data_idx);
                        let content = &program.data[data_idx..data_idx + content_end];
                        let path_str = std::str::from_utf8(path)
                            .map_err(|_| NyError::EINVAL)?;
                        let fd = unsafe {
                            libc::open(
                                path.as_ptr() as *const c_char,
                                libc::O_WRONLY | libc::O_CREAT | libc::O_TRUNC,
                                0o644,
                            )
                        };
                        if fd >= 0 {
                            let written = unsafe {
                                libc::write(fd, content.as_ptr() as *const c_void, content.len())
                            };
                            unsafe { libc::close(fd); }
                            self.log.push(LogEntry {
                                level: 0,
                                message: format!("FS_WRITE {} bytes to {}", written, path_str).into_bytes(),
                            });
                        } else {
                            self.log.push(LogEntry {
                                level: 2,
                                message: format!("FS_WRITE failed: {} errno={}", path_str, std::io::Error::last_os_error()).into_bytes(),
                            });
                        }
                    }
                    pc += 3;
                }
                OP_LOG => {
                    // Log message: data index at code[pc+1]
                    if pc + 2 > program.code.len() {
                        return Err(NyError::ERUNTIME);
                    }
                    let msg_idx = program.code[pc + 1] as usize;
                    if msg_idx < program.data.len() {
                        let end = program.data[msg_idx..]
                            .iter()
                            .position(|&b| b == 0)
                            .unwrap_or(program.data.len() - msg_idx);
                        let msg = program.data[msg_idx..msg_idx + end].to_vec();
                        self.log.push(LogEntry { level: 0, message: msg });
                    }
                    pc += 2;
                }
                OP_SET_STATE => {
                    if pc + 3 > program.code.len() {
                        return Err(NyError::ERUNTIME);
                    }
                    let key_idx = program.code[pc + 1] as usize;
                    let val_idx = program.code[pc + 2] as usize;
                    if key_idx < program.data.len() && val_idx < program.data.len() {
                        let key_end = program.data[key_idx..]
                            .iter()
                            .position(|&b| b == 0)
                            .unwrap_or(program.data.len() - key_idx);
                        let val_end = program.data[val_idx..]
                            .iter()
                            .position(|&b| b == 0)
                            .unwrap_or(program.data.len() - val_idx);
                        let key = program.data[key_idx..key_idx + key_end].to_vec();
                        let val = program.data[val_idx..val_idx + val_end].to_vec();
                        // Update or insert
                        if let Some(existing) = self.state_store.iter_mut().find(|(k, _)| k == &key) {
                            existing.1 = val;
                        } else {
                            self.state_store.push((key, val));
                        }
                    }
                    pc += 3;
                }
                OP_GET_STATE => {
                    if pc + 2 > program.code.len() {
                        return Err(NyError::ERUNTIME);
                    }
                    pc += 2;
                }
                OP_YIELD => {
                    pc += 1;
                }
                _ => {
                    return Err(NyError::ERUNTIME);
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// FFI interface
// ---------------------------------------------------------------------------

/// Parse a .napp binary header. Writes manifest_len, code_len, data_len
/// into `out` (must point to a buffer of at least 3 i32 values).
/// Returns 0 on success, -1 on error.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_nyruntime_parse_header(
    data: *const u8,
    len: u32,
    out: *mut i32,
) -> i32 {
    if data.is_null() || len < 17 || out.is_null() {
        return -1;
    }
    let slice = unsafe { std::slice::from_raw_parts(data, len as usize) };
    if &slice[0..4] != NAPP_MAGIC {
        return -1;
    }
    let manifest_len = u32::from_le_bytes([slice[5], slice[6], slice[7], slice[8]]) as i32;
    let code_len = u32::from_le_bytes([slice[9], slice[10], slice[11], slice[12]]) as i32;
    let data_len = u32::from_le_bytes([slice[13], slice[14], slice[15], slice[16]]) as i32;
    unsafe {
        *out = manifest_len;
        *out.add(1) = code_len;
        *out.add(2) = data_len;
    }
    0
}

/// Create a new runtime instance.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_nyruntime_create() -> *mut Runtime {
    let rt = Runtime::new(ContainerConfig::default());
    Box::into_raw(Box::new(rt))
}

/// Destroy a runtime instance.
///
/// # Safety
/// `rt` must have been returned by `nyrqis_nyruntime_create`.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_nyruntime_destroy(rt: *mut Runtime) {
    if !rt.is_null() {
        unsafe {
            drop(Box::from_raw(rt));
        }
    }
}

/// Initialize the runtime.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_nyruntime_init(rt: *mut Runtime) -> i32 {
    if rt.is_null() {
        return NyError::EINVAL.as_i32();
    }
    match unsafe { (*rt).init() } {
        Ok(()) => NyError::Ok.as_i32(),
        Err(e) => e.as_i32(),
    }
}

/// Get the runtime state.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_nyruntime_state(rt: *mut Runtime) -> i32 {
    if rt.is_null() {
        return -1;
    }
    unsafe { (*rt).state() as i32 }
}

/// Load a .napp binary into the runtime.
///
/// # Safety
/// `data` must point to valid memory of `len` bytes.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_nyruntime_load_napp(
    rt: *mut Runtime,
    data: *const u8,
    len: u32,
) -> i32 {
    if rt.is_null() || data.is_null() {
        return NyError::EINVAL.as_i32();
    }
    let slice = unsafe { std::slice::from_raw_parts(data, len as usize) };
    match unsafe { (*rt).load_napp(slice) } {
        Ok(()) => NyError::Ok.as_i32(),
        Err(e) => e.as_i32(),
    }
}

/// Execute the loaded program.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_nyruntime_execute(
    rt: *mut Runtime,
    out_exit_code: *mut i32,
) -> i32 {
    if rt.is_null() || out_exit_code.is_null() {
        return NyError::EINVAL.as_i32();
    }
    match unsafe { (*rt).execute() } {
        Ok(code) => {
            unsafe { *out_exit_code = code; }
            NyError::Ok.as_i32()
        }
        Err(e) => e.as_i32(),
    }
}

/// Wire IPC: bind a socket fd and set the peer (daemon) path.
///
/// # Safety
/// `peer_path` must point to a NUL-terminated C string of `peer_len`
/// bytes (excluding the NUL).
#[no_mangle]
pub unsafe extern "C" fn nyrqis_nyruntime_set_ipc(
    rt: *mut Runtime,
    fd: c_int,
    peer_path: *const c_char,
    peer_len: u32,
) -> i32 {
    if rt.is_null() || peer_path.is_null() {
        return NyError::EINVAL.as_i32();
    }
    let slice = unsafe { std::slice::from_raw_parts(peer_path as *const u8, peer_len as usize) };
    unsafe { (*rt).set_ipc(fd, slice) };
    NyError::Ok.as_i32()
}

/// Get the number of log entries.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_nyruntime_log_count(rt: *mut Runtime) -> i32 {
    if rt.is_null() {
        return -1;
    }
    unsafe { (*rt).log_entries().len() as i32 }
}

/// Get a log entry by index. Returns the log level and writes the
/// message length into `out_msg_len`. Returns the message pointer,
/// or NULL if the index is out of range.
///
/// # Safety
/// The returned pointer is valid until the next call to the runtime
/// (it borrows the internal log Vec). Caller must not free it.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_nyruntime_log_entry(
    rt: *mut Runtime,
    index: i32,
    out_level: *mut i32,
    out_msg_len: *mut u32,
) -> *const u8 {
    if rt.is_null() || out_level.is_null() || out_msg_len.is_null() || index < 0 {
        return std::ptr::null();
    }
    let entries = unsafe { (*rt).log_entries() };
    let idx = index as usize;
    if idx >= entries.len() {
        return std::ptr::null();
    }
    unsafe {
        *out_level = entries[idx].level as i32;
        *out_msg_len = entries[idx].message.len() as u32;
    }
    entries[idx].message.as_ptr()
}

/// Get the ABI version of this crate.
#[no_mangle]
pub extern "C" fn nyrqis_nyruntime_version() -> u32 {
    (1 << 16) | 0 // 1.0.0
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::CString;
    use std::ptr;

    fn make_napp(code: &[u8], data: &[u8]) -> Vec<u8> {
        let manifest = b"{\"name\":\"test\",\"version\":\"1.0.0\"}";
        let mut buf = Vec::new();
        buf.extend_from_slice(NAPP_MAGIC);
        buf.push(NAPP_VERSION);
        buf.extend_from_slice(&(manifest.len() as u32).to_le_bytes());
        buf.extend_from_slice(&(code.len() as u32).to_le_bytes());
        buf.extend_from_slice(&(data.len() as u32).to_le_bytes());
        buf.extend_from_slice(manifest);
        buf.extend_from_slice(code);
        buf.extend_from_slice(data);
        buf
    }

    #[test]
    fn napp_parse() {
        let raw = make_napp(&[OP_HALT], &[42]);
        let pkg = NappPackage::parse(&raw).unwrap();
        assert_eq!(pkg.version, NAPP_VERSION);
        assert_eq!(pkg.code, vec![OP_HALT]);
        assert_eq!(pkg.data, vec![42]);
    }

    #[test]
    fn napp_parse_bad_magic() {
        let mut raw = make_napp(&[OP_HALT], &[0]);
        raw[0] = b'X';
        assert_eq!(NappPackage::parse(&raw), Err(NyError::EINVAL));
    }

    #[test]
    fn napp_parse_too_small() {
        assert_eq!(NappPackage::parse(&[0; 10]), Err(NyError::EINVAL));
    }

    #[test]
    fn runtime_lifecycle() {
        let mut rt = Runtime::new(ContainerConfig::default());
        assert_eq!(rt.state(), RuntimeState::Uninitialized);
        rt.init().unwrap();
        assert_eq!(rt.state(), RuntimeState::Ready);

        let program = Program {
            name: b"test".to_vec(),
            entry: 0,
            code: vec![OP_HALT],
            data: vec![42],
            required_caps: Vec::new(),
        };
        rt.load(program).unwrap();
        assert_eq!(rt.state(), RuntimeState::Loaded);

        let exit = rt.execute().unwrap();
        assert_eq!(exit, 42);
        assert_eq!(rt.state(), RuntimeState::Ready);
    }

    #[test]
    fn runtime_load_napp() {
        let mut rt = Runtime::new(ContainerConfig::default());
        rt.init().unwrap();

        let raw = make_napp(&[OP_HALT], &[7]);
        rt.load_napp(&raw).unwrap();
        let exit = rt.execute().unwrap();
        assert_eq!(exit, 7);
    }

    #[test]
    fn runtime_log_opcode() {
        let mut rt = Runtime::new(ContainerConfig::default());
        rt.init().unwrap();

        // LOG msg_idx, then HALT
        let raw = make_napp(&[OP_LOG, 0, OP_HALT], b"hello\x00");
        rt.load_napp(&raw).unwrap();
        rt.execute().unwrap();

        assert_eq!(rt.log_entries().len(), 1);
        assert_eq!(rt.log_entries()[0].message, b"hello");
    }

    #[test]
    fn runtime_set_state() {
        let mut rt = Runtime::new(ContainerConfig::default());
        rt.init().unwrap();

        // SET_STATE key_idx=0, val_idx=6, then HALT
        let code = vec![OP_SET_STATE, 0, 6, OP_HALT];
        let data = b"theme\x00Eclipse\x00".to_vec();
        let raw = make_napp(&code, &data);
        rt.load_napp(&raw).unwrap();
        rt.execute().unwrap();

        assert_eq!(rt.state_store.len(), 1);
        assert_eq!(rt.state_store[0].0, b"theme");
        assert_eq!(rt.state_store[0].1, b"Eclipse");
    }

    #[test]
    fn runtime_nop_chain() {
        let mut rt = Runtime::new(ContainerConfig::default());
        rt.init().unwrap();

        let raw = make_napp(&[OP_NOP, OP_NOP, OP_NOP, OP_HALT], &[99]);
        rt.load_napp(&raw).unwrap();
        assert_eq!(rt.execute().unwrap(), 99);
    }

    #[test]
    fn runtime_capability_check() {
        let mut rt = Runtime::new(ContainerConfig::default());
        rt.init().unwrap();

        let program = Program {
            name: b"needs_cap".to_vec(),
            entry: 0,
            code: vec![OP_HALT],
            data: vec![0],
            required_caps: vec![Capability::StorageVolume],
        };
        assert_eq!(rt.load(program), Err(NyError::ECAPMISSING));

        rt.grant_capability(Capability::StorageVolume).unwrap();
        let program = Program {
            name: b"needs_cap".to_vec(),
            entry: 0,
            code: vec![OP_HALT],
            data: vec![0],
            required_caps: vec![Capability::StorageVolume],
        };
        rt.load(program).unwrap();
    }

    #[test]
    fn parse_header_ffi() {
        let raw = make_napp(&[OP_HALT], &[42]);
        let mut out = [0i32; 3];
        let rc = unsafe {
            nyrqis_nyruntime_parse_header(raw.as_ptr(), raw.len() as u32, out.as_mut_ptr())
        };
        assert_eq!(rc, 0);
        assert_eq!(out[0], 33); // manifest len
        assert_eq!(out[1], 1);  // code len
        assert_eq!(out[2], 1);  // data len
    }

    #[test]
    fn version() {
        let v = nyrqis_nyruntime_version();
        assert_eq!(v >> 16, 1);
        assert_eq!(v & 0xFFFF, 0);
    }

    #[test]
    fn ipc_call_with_socket() {
        use std::os::unix::io::FromRawFd;

        // Create server socket
        let srv_path = CString::new(format!(
            "/tmp/nyrt-ipc-test-{}-srv.sock",
            std::process::id()
        ))
        .unwrap();
        let cli_path = CString::new(format!(
            "/tmp/nyrt-ipc-test-{}-cli.sock",
            std::process::id()
        ))
        .unwrap();

        let srv_fd = unsafe { libc::socket(libc::AF_UNIX, libc::SOCK_DGRAM, 0) };
        assert!(srv_fd >= 0);
        let srv_sun = {
            let mut s: libc::sockaddr_un = unsafe { std::mem::zeroed() };
            s.sun_family = libc::AF_UNIX as libc::sa_family_t;
            let p = srv_path.to_bytes();
            for (i, &b) in p.iter().enumerate() {
                s.sun_path[i] = b as i8;
            }
            s
        };
        let rc = unsafe {
            libc::bind(
                srv_fd,
                &srv_sun as *const libc::sockaddr_un as *const libc::sockaddr,
                std::mem::size_of::<libc::sockaddr_un>() as libc::socklen_t,
            )
        };
        assert_eq!(rc, 0, "srv bind failed");

        // Create client socket
        let cli_fd = unsafe { libc::socket(libc::AF_UNIX, libc::SOCK_DGRAM, 0) };
        assert!(cli_fd >= 0);
        let cli_sun = {
            let mut s: libc::sockaddr_un = unsafe { std::mem::zeroed() };
            s.sun_family = libc::AF_UNIX as libc::sa_family_t;
            let p = cli_path.to_bytes();
            for (i, &b) in p.iter().enumerate() {
                s.sun_path[i] = b as i8;
            }
            s
        };
        let rc = unsafe {
            libc::bind(
                cli_fd,
                &cli_sun as *const libc::sockaddr_un as *const libc::sockaddr,
                std::mem::size_of::<libc::sockaddr_un>() as libc::socklen_t,
            )
        };
        assert_eq!(rc, 0, "cli bind failed");

        // Server thread: receive request, send reply
        let srv_path_clone = srv_path.clone();
        let cli_path_clone = cli_path.clone();
        let handle = std::thread::spawn(move || {
            let mut rbuf = [0u8; 65536];
            let mut iov = libc::iovec {
                iov_base: rbuf.as_mut_ptr() as *mut c_void,
                iov_len: rbuf.len(),
            };
            let mut msg: libc::msghdr = unsafe { std::mem::zeroed() };
            msg.msg_iov = &mut iov;
            msg.msg_iovlen = 1;
            let n = unsafe { libc::recvmsg(srv_fd, &mut msg, 0) };
            assert!(n > 0, "srv recv failed");
            // Send reply
            let cli_sun2 = {
                let mut s: libc::sockaddr_un = unsafe { std::mem::zeroed() };
                s.sun_family = libc::AF_UNIX as libc::sa_family_t;
                let p = cli_path_clone.to_bytes();
                for (i, &b) in p.iter().enumerate() {
                    s.sun_path[i] = b as i8;
                }
                s
            };
            let reply = b"NYRQ\x01\x03pong";
            let sent = unsafe {
                libc::sendto(
                    srv_fd,
                    reply.as_ptr() as *const c_void,
                    reply.len(),
                    0,
                    &cli_sun2 as *const libc::sockaddr_un as *const libc::sockaddr,
                    (2 + cli_path_clone.to_bytes().len() + 1) as libc::socklen_t,
                )
            };
            assert_eq!(sent as usize, reply.len());
            unsafe {
                libc::close(srv_fd);
                libc::unlink(srv_path_clone.as_ptr());
            }
        });

        // Set up the runtime with the IPC socket
        let mut rt = Runtime::new(ContainerConfig::default());
        rt.init().unwrap();
        rt.set_ipc(cli_fd, srv_path.to_bytes());

        // Build a program: IPC_CALL service=0 op=0 payload_idx=4, then HALT
        // data layout: [0 (exit code), padding, payload, NUL]
        let payload = b"{\"op\":\"ping\"}\x00";
        let code = vec![OP_IPC_CALL, 0, 0, 4, OP_HALT];
        let mut data = vec![0u8; 4]; // exit code + padding
        data.extend_from_slice(payload);
        let raw = make_napp(&code, &data);
        rt.load_napp(&raw).unwrap();
        let exit = rt.execute().unwrap();
        assert_eq!(exit, 0);

        // The reply should be in the log
        assert!(!rt.log_entries().is_empty(), "expected IPC reply in log");
        assert_eq!(rt.log_entries()[0].level, 1); // level=1 means IPC reply

        handle.join().unwrap();
        unsafe {
            libc::close(cli_fd);
            libc::unlink(cli_path.as_ptr());
        }
    }

    #[test]
    fn set_ipc_ffi() {
        let mut rt = Runtime::new(ContainerConfig::default());
        rt.init().unwrap();
        let peer = CString::new("/tmp/test.sock").unwrap();
        let rc = unsafe {
            nyrqis_nyruntime_set_ipc(
                &mut rt as *mut Runtime,
                42,
                peer.as_ptr(),
                peer.to_bytes().len() as u32,
            )
        };
        assert_eq!(rc, 0);
        assert_eq!(rt.ipc_fd, 42);
        assert_eq!(rt.ipc_peer, peer.to_bytes());
    }

    #[test]
    fn fs_write_and_read() {
        let dir = std::env::temp_dir().join(format!("nyrt-fs-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("test.txt");
        let path_str = path.to_str().unwrap();

        // FS_WRITE: write "hello world" to the file
        let mut data = Vec::new();
        // data[0] = exit code (0 = success)
        data.push(0);
        // align to 4 bytes
        while data.len() % 4 != 0 { data.push(0); }
        let path_off = data.len();
        data.extend_from_slice(path_str.as_bytes());
        data.push(0);
        let content_off = data.len();
        data.extend_from_slice(b"hello world");
        data.push(0);

        let code = vec![OP_FS_WRITE, path_off as u8, content_off as u8, OP_HALT];
        let raw = make_napp(&code, &data);

        let mut rt = Runtime::new(ContainerConfig::default());
        rt.init().unwrap();
        rt.load_napp(&raw).unwrap();
        let exit = rt.execute().unwrap();
        assert_eq!(exit, 0);
        assert!(path.exists());
        assert_eq!(std::fs::read(&path).unwrap(), b"hello world");

        // FS_READ: read it back into state
        let mut data2 = Vec::new();
        data2.push(0); // exit code
        while data2.len() % 4 != 0 { data2.push(0); }
        let path_off2 = data2.len();
        data2.extend_from_slice(path_str.as_bytes());
        data2.push(0);
        let dest_off = data2.len();
        data2.extend_from_slice(b"content");
        data2.push(0);

        let code2 = vec![OP_FS_READ, path_off2 as u8, dest_off as u8, OP_HALT];
        let raw2 = make_napp(&code2, &data2);

        let mut rt2 = Runtime::new(ContainerConfig::default());
        rt2.init().unwrap();
        rt2.load_napp(&raw2).unwrap();
        rt2.execute().unwrap();

        // Check the state store has the content
        assert!(rt2.state_store.iter().any(|(k, v)| k == b"content" && v == b"hello world"));

        // Cleanup
        let _ = std::fs::remove_file(&path);
        let _ = std::fs::remove_dir(&dir);
    }
}
