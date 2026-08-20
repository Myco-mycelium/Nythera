//! Nyrqis Core (NyCore)
//!
//! The foundation crate for the Nyrqis platform. Contains core types,
//! error definitions, and the IPC protocol shared between NyRuntime,
//! NyHAL services, and Nyrqis applications.
//!
//! # Design Principles
//!
//! - **No allocator dependency** — all types are stack-allocated or use
//!   caller-provided buffers
//! - **FFI-safe** — every public type is `#[repr(C)]` for cross-language interop
//! - **Zero-cost abstractions** — enums are `#[repr(u32)]` for wire efficiency
//! - **No_std compatible** — can run in bare-metal contexts

use std::fmt;

// ---------------------------------------------------------------------------
// Error types
// ---------------------------------------------------------------------------

/// Nyrqis error codes — mirrors the POSIX convention of negative errno
/// values for FFI compatibility, with Nyrqis-specific extensions above 512.
#[repr(i32)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum NyError {
    /// Success (not an error)
    Ok = 0,
    /// Invalid argument
    EINVAL = -22,
    /// No such file or directory
    ENOENT = -2,
    /// Permission denied
    EACCES = -13,
    /// Operation not supported
    ENOSYS = -38,
    /// No buffer space available
    ENOBUFS = -105,
    /// Connection refused
    ECONNREFUSED = -111,
    /// Message too long
    EMSGSIZE = -90,
    /// Timer expired (Nyrqis-specific, base 512)
    ETIMEDOUT = -512,
    /// Capability not granted (Nyrqis-specific)
    ECAPMISSING = -513,
    /// IPC transport error (Nyrqis-specific)
    EIPCTRANSPORT = -514,
    /// Container not found (Nyrqis-specific)
    ECONTAINERNOTFOUND = -515,
    /// Storage volume error (Nyrqis-specific)
    ESTORAGE = -516,
    /// Runtime error (Nyrqis-specific)
    ERUNTIME = -517,
}

impl NyError {
    /// Convert to a negative i32 for FFI return values.
    pub fn as_i32(self) -> i32 {
        self as i32
    }

    /// Convert from a raw i32 (e.g., a syscall return).
    pub fn from_i32(val: i32) -> Option<Self> {
        match val {
            0 => Some(NyError::Ok),
            -22 => Some(NyError::EINVAL),
            -2 => Some(NyError::ENOENT),
            -13 => Some(NyError::EACCES),
            -38 => Some(NyError::ENOSYS),
            -105 => Some(NyError::ENOBUFS),
            -111 => Some(NyError::ECONNREFUSED),
            -90 => Some(NyError::EMSGSIZE),
            -512 => Some(NyError::ETIMEDOUT),
            -513 => Some(NyError::ECAPMISSING),
            -514 => Some(NyError::EIPCTRANSPORT),
            -515 => Some(NyError::ECONTAINERNOTFOUND),
            -516 => Some(NyError::ESTORAGE),
            -517 => Some(NyError::ERUNTIME),
            _ => None,
        }
    }
}

impl fmt::Display for NyError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            NyError::Ok => write!(f, "success"),
            NyError::EINVAL => write!(f, "invalid argument"),
            NyError::ENOENT => write!(f, "no such file or directory"),
            NyError::EACCES => write!(f, "permission denied"),
            NyError::ENOSYS => write!(f, "operation not supported"),
            NyError::ENOBUFS => write!(f, "no buffer space available"),
            NyError::ECONNREFUSED => write!(f, "connection refused"),
            NyError::EMSGSIZE => write!(f, "message too long"),
            NyError::ETIMEDOUT => write!(f, "timer expired"),
            NyError::ECAPMISSING => write!(f, "capability not granted"),
            NyError::EIPCTRANSPORT => write!(f, "IPC transport error"),
            NyError::ECONTAINERNOTFOUND => write!(f, "container not found"),
            NyError::ESTORAGE => write!(f, "storage volume error"),
            NyError::ERUNTIME => write!(f, "runtime error"),
        }
    }
}

// ---------------------------------------------------------------------------
// IPC protocol
// ---------------------------------------------------------------------------

/// IPC message types — the wire protocol shared between all Nyrqis services.
#[repr(u32)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IpcMessageType {
    /// Request a service operation
    Call = 1,
    /// Reply to a Call
    Reply = 2,
    /// Fire-and-forget notification
    Notify = 3,
    /// Error reply
    Error = 4,
}

/// IPC message header — fixed-size prefix before the variable-length payload.
/// All fields are in network byte order (big-endian) on the wire.
#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct IpcMessageHeader {
    /// Message type (Call, Reply, Notify, Error)
    pub msg_type: u32,
    /// Message ID for correlation (Call → Reply)
    pub msg_id: u32,
    /// Sender PID (kernel-authenticated via SCM_CREDENTIALS)
    pub sender_pid: u32,
    /// Payload length in bytes
    pub payload_len: u32,
    /// Metadata length in bytes (JSON-encoded key-value pairs)
    pub metadata_len: u32,
    /// Reserved for future use
    pub reserved: u32,
}

impl IpcMessageHeader {
    /// Size of the fixed header in bytes.
    pub const SIZE: usize = 24;

    /// Create a new header for a Call message.
    pub fn call(msg_id: u32, sender_pid: u32, payload_len: u32, metadata_len: u32) -> Self {
        Self {
            msg_type: IpcMessageType::Call as u32,
            msg_id,
            sender_pid,
            payload_len,
            metadata_len,
            reserved: 0,
        }
    }

    /// Create a new header for a Reply message.
    pub fn reply(msg_id: u32, sender_pid: u32, payload_len: u32, metadata_len: u32) -> Self {
        Self {
            msg_type: IpcMessageType::Reply as u32,
            msg_id,
            sender_pid,
            payload_len,
            metadata_len,
            reserved: 0,
        }
    }
}

// ---------------------------------------------------------------------------
// Container types
// ---------------------------------------------------------------------------

/// Container lifecycle states — mirrors the NPS-010 state machine.
#[repr(u32)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ContainerState {
    /// Container created but not yet started
    Created = 0,
    /// Container is running
    Running = 1,
    /// Container is suspended (frozen)
    Suspended = 2,
    /// Container has terminated
    Terminated = 3,
}

/// Container configuration — passed to the launcher.
#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct ContainerConfig {
    /// Memory limit in bytes (0 = unlimited)
    pub memory_limit: u64,
    /// Maximum number of PIDs (0 = unlimited)
    pub pid_limit: u32,
    /// CPU shares (1024 = default)
    pub cpu_shares: u32,
    /// Enable seccomp filtering
    pub seccomp: bool,
    /// Strict seccomp mode (fail if filter cannot be installed)
    pub strict_seccomp: bool,
    /// Give the container its own network namespace
    pub network: bool,
    /// Reserved
    pub _reserved: [u8; 3],
}

impl Default for ContainerConfig {
    fn default() -> Self {
        Self {
            memory_limit: 0,
            pid_limit: 0,
            cpu_shares: 1024,
            seccomp: true,
            strict_seccomp: false,
            network: false,
            _reserved: [0; 3],
        }
    }
}

// ---------------------------------------------------------------------------
// Capability types
// ---------------------------------------------------------------------------

/// Nyrqis capabilities — the security tokens that govern what a container
/// can do. Each capability grants access to a specific class of operations.
#[repr(u32)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Capability {
    /// Send IPC messages
    IpcSend = 1,
    /// Receive IPC messages
    IpcReceive = 2,
    /// Read from the filesystem
    FilesystemRead = 3,
    /// Write to the filesystem
    FilesystemWrite = 4,
    /// Spawn child processes
    ProcessSpawn = 5,
    /// Access network sockets
    NetworkSocket = 6,
    /// Bind to network ports
    NetworkBind = 7,
    /// Access system information
    SystemInfo = 8,
    /// Access storage volumes
    StorageVolume = 9,
    /// Access GPU/graphics
    Graphics = 10,
    /// Access AI subsystem
    AiAccess = 11,
}

// ---------------------------------------------------------------------------
// FFI helpers
// ---------------------------------------------------------------------------

/// Return a C-compatible error code from a Rust result.
///
/// # Safety
///
/// `out` must point to valid, aligned memory for an `i32`.
///
/// # Example
///
/// ```ignore
/// let result = some_operation();
/// unsafe { nyrqis_nycore_error_to_errno(result, &mut out) };
/// ```
#[no_mangle]
pub unsafe extern "C" fn nyrqis_nycore_error_to_errno(
    error: i32,
    out: *mut i32,
) -> i32 {
    if out.is_null() {
        return NyError::EINVAL.as_i32();
    }
    *out = error;
    NyError::Ok.as_i32()
}

/// Get the ABI version of this crate.
#[no_mangle]
pub extern "C" fn nyrqis_nycore_version() -> u32 {
    // ABI version: major.minor packed as (major << 16) | minor
    (1 << 16) | 0 // 1.0.0
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn error_roundtrip() {
        for err in [
            NyError::Ok,
            NyError::EINVAL,
            NyError::ENOENT,
            NyError::EACCES,
            NyError::ENOSYS,
            NyError::ECAPMISSING,
            NyError::EIPCTRANSPORT,
            NyError::ECONTAINERNOTFOUND,
            NyError::ESTORAGE,
            NyError::ERUNTIME,
        ] {
            let raw = err.as_i32();
            let recovered = NyError::from_i32(raw);
            assert_eq!(recovered, Some(err), "roundtrip failed for {:?}", err);
        }
    }

    #[test]
    fn error_display() {
        assert_eq!(format!("{}", NyError::Ok), "success");
        assert_eq!(format!("{}", NyError::ECAPMISSING), "capability not granted");
    }

    #[test]
    fn ipc_header_size() {
        assert_eq!(IpcMessageHeader::SIZE, 24);
    }

    #[test]
    fn ipc_header_call() {
        let h = IpcMessageHeader::call(1, 42, 100, 50);
        assert_eq!(h.msg_type, IpcMessageType::Call as u32);
        assert_eq!(h.msg_id, 1);
        assert_eq!(h.sender_pid, 42);
        assert_eq!(h.payload_len, 100);
        assert_eq!(h.metadata_len, 50);
    }

    #[test]
    fn container_config_default() {
        let cfg = ContainerConfig::default();
        assert_eq!(cfg.memory_limit, 0);
        assert_eq!(cfg.pid_limit, 0);
        assert_eq!(cfg.cpu_shares, 1024);
        assert!(cfg.seccomp);
        assert!(!cfg.strict_seccomp);
        assert!(!cfg.network);
    }

    #[test]
    fn container_states() {
        assert_eq!(ContainerState::Created as u32, 0);
        assert_eq!(ContainerState::Running as u32, 1);
        assert_eq!(ContainerState::Suspended as u32, 2);
        assert_eq!(ContainerState::Terminated as u32, 3);
    }

    #[test]
    fn capabilities() {
        assert_eq!(Capability::IpcSend as u32, 1);
        assert_eq!(Capability::StorageVolume as u32, 9);
        assert_eq!(Capability::AiAccess as u32, 11);
    }

    #[test]
    fn version() {
        let v = nyrqis_nycore_version();
        assert_eq!(v >> 16, 1); // major
        assert_eq!(v & 0xFFFF, 0); // minor
    }
}
