//! Nyrqis Runtime (NyRuntime)
//!
//! The minimal runtime for loading and executing Nyrqis programs.
//! Provides the execution environment that bridges NyCore types with
//! the actual OS services (IPC, filesystem, capabilities).
//!
//! # Architecture
//!
//! ```text
//! Nyrqis Application
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

use std::fmt;
use std::vec::Vec;
use nyrqis_nycore::{Capability, ContainerConfig, ContainerState, NyError};

// ---------------------------------------------------------------------------
// Runtime state
// ---------------------------------------------------------------------------

/// The runtime's execution state.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RuntimeState {
    /// Runtime not yet initialized
    Uninitialized,
    /// Runtime initialized, ready to load programs
    Ready,
    /// A program is loaded and ready to execute
    Loaded,
    /// A program is currently executing
    Running,
    /// Runtime has encountered a fatal error
    Failed,
}

/// A loaded Nyrqis program — the minimal representation needed to
/// execute it.
#[derive(Debug)]
pub struct Program {
    /// Program name (for diagnostics)
    pub name: Vec<u8>,
    /// Entry point offset (into the code segment)
    pub entry: usize,
    /// Code segment
    pub code: Vec<u8>,
    /// Data segment
    pub data: Vec<u8>,
    /// Required capabilities
    pub required_caps: Vec<Capability>,
}

/// The runtime instance — holds the execution state and provides
/// the operations that a Nyrqis program can call.
#[derive(Debug)]
pub struct Runtime {
    /// Current state
    state: RuntimeState,
    /// Loaded program (if any)
    program: Option<Program>,
    /// Granted capabilities
    capabilities: Vec<Capability>,
    /// Container configuration
    config: ContainerConfig,
}

impl Runtime {
    /// Create a new runtime with the given configuration.
    pub fn new(config: ContainerConfig) -> Self {
        Self {
            state: RuntimeState::Uninitialized,
            program: None,
            capabilities: Vec::new(),
            config,
        }
    }

    /// Initialize the runtime. Must be called before loading programs.
    pub fn init(&mut self) -> Result<(), NyError> {
        if self.state != RuntimeState::Uninitialized {
            return Err(NyError::EINVAL);
        }
        self.state = RuntimeState::Ready;
        Ok(())
    }

    /// Grant a capability to the runtime.
    pub fn grant_capability(&mut self, cap: Capability) -> Result<(), NyError> {
        if self.state == RuntimeState::Uninitialized {
            return Err(NyError::EINVAL);
        }
        if !self.capabilities.contains(&cap) {
            self.capabilities.push(cap);
        }
        Ok(())
    }

    /// Check if the runtime has a specific capability.
    pub fn has_capability(&self, cap: Capability) -> bool {
        self.capabilities.contains(&cap)
    }

    /// Load a program into the runtime.
    pub fn load(&mut self, program: Program) -> Result<(), NyError> {
        if self.state != RuntimeState::Ready && self.state != RuntimeState::Loaded {
            return Err(NyError::EINVAL);
        }

        // Check that all required capabilities are granted
        for req_cap in &program.required_caps {
            if !self.has_capability(*req_cap) {
                return Err(NyError::ECAPMISSING);
            }
        }

        self.program = Some(program);
        self.state = RuntimeState::Loaded;
        Ok(())
    }

    /// Execute the loaded program. This is a minimal execution loop
    /// that processes the program's code segment as a sequence of
    /// operations.
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

    /// Get the current runtime state.
    pub fn state(&self) -> RuntimeState {
        self.state
    }

    /// Get the loaded program's name (if any).
    pub fn program_name(&self) -> Option<&[u8]> {
        self.program.as_ref().map(|p| p.name.as_slice())
    }

    /// Internal: run the program through the minimal execution loop.
    fn run_program(&self) -> Result<i32, NyError> {
        let program = self.program.as_ref().ok_or(NyError::EINVAL)?;

        // Minimal execution: interpret the code segment as a sequence
        // of operation codes. For now, the only op is OP_HALT (0x00)
        // which returns the first byte of the data segment as the
        // exit code.
        let mut pc = program.entry;
        loop {
            if pc >= program.code.len() {
                return Err(NyError::ERUNTIME);
            }

            let op = program.code[pc];
            match op {
                0x00 => {
                    // OP_HALT: exit with data[0] as code
                    let exit_code = if program.data.is_empty() {
                        0
                    } else {
                        program.data[0] as i32
                    };
                    return Ok(exit_code);
                }
                0x01 => {
                    // OP_NOP: no operation, advance
                    pc += 1;
                }
                0x02 => {
                    // OP_PRINT: print data[data[pc+1]..data[pc+2]]
                    // For now, this is a no-op in the no_std context
                    // (would need a console service via IPC in production)
                    pc += 3;
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

/// Create a new runtime instance.
///
/// # Safety
///
/// Returns a pointer to a heap-allocated Runtime. The caller must
/// eventually call `nyrqis_nyruntime_destroy` to free it.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_nyruntime_create() -> *mut Runtime {
    let rt = Runtime::new(ContainerConfig::default());
    Box::into_raw(Box::new(rt))
}

/// Destroy a runtime instance.
///
/// # Safety
///
/// `rt` must have been returned by `nyrqis_nyruntime_create` and not
/// previously destroyed.
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

    #[test]
    fn runtime_lifecycle() {
        let mut rt = Runtime::new(ContainerConfig::default());
        assert_eq!(rt.state(), RuntimeState::Uninitialized);

        rt.init().unwrap();
        assert_eq!(rt.state(), RuntimeState::Ready);

        // Load a simple program
        let program = Program {
            name: b"test".to_vec(),
            entry: 0,
            code: vec![0x00], // OP_HALT
            data: vec![42],
            required_caps: Vec::new(),
        };
        rt.load(program).unwrap();
        assert_eq!(rt.state(), RuntimeState::Loaded);

        // Execute
        let exit = rt.execute().unwrap();
        assert_eq!(exit, 42);
        assert_eq!(rt.state(), RuntimeState::Ready);
    }

    #[test]
    fn runtime_capability_check() {
        let mut rt = Runtime::new(ContainerConfig::default());
        rt.init().unwrap();

        let program = Program {
            name: b"needs_cap".to_vec(),
            entry: 0,
            code: vec![0x00],
            data: vec![0],
            required_caps: vec![Capability::StorageVolume],
        };

        // Should fail without the capability
        assert_eq!(rt.load(program), Err(NyError::ECAPMISSING));

        // Grant the capability
        rt.grant_capability(Capability::StorageVolume).unwrap();
        assert!(rt.has_capability(Capability::StorageVolume));

        // Now it should work
        let program = Program {
            name: b"needs_cap".to_vec(),
            entry: 0,
            code: vec![0x00],
            data: vec![0],
            required_caps: vec![Capability::StorageVolume],
        };
        rt.load(program).unwrap();
    }

    #[test]
    fn runtime_nop_program() {
        let mut rt = Runtime::new(ContainerConfig::default());
        rt.init().unwrap();

        let program = Program {
            name: b"nop".to_vec(),
            entry: 0,
            code: vec![0x01, 0x01, 0x01, 0x00], // 3x NOP, then HALT
            data: vec![7],
            required_caps: Vec::new(),
        };
        rt.load(program).unwrap();
        let exit = rt.execute().unwrap();
        assert_eq!(exit, 7);
    }

    #[test]
    fn runtime_empty_data_halt() {
        let mut rt = Runtime::new(ContainerConfig::default());
        rt.init().unwrap();

        let program = Program {
            name: b"empty".to_vec(),
            entry: 0,
            code: vec![0x00], // HALT with empty data
            data: Vec::new(),
            required_caps: Vec::new(),
        };
        rt.load(program).unwrap();
        let exit = rt.execute().unwrap();
        assert_eq!(exit, 0);
    }

    #[test]
    fn runtime_bad_opcode() {
        let mut rt = Runtime::new(ContainerConfig::default());
        rt.init().unwrap();

        let program = Program {
            name: b"bad".to_vec(),
            entry: 0,
            code: vec![0xFF], // invalid opcode
            data: vec![0],
            required_caps: Vec::new(),
        };
        rt.load(program).unwrap();
        assert_eq!(rt.execute(), Err(NyError::ERUNTIME));
    }

    #[test]
    fn runtime_double_init_fails() {
        let mut rt = Runtime::new(ContainerConfig::default());
        rt.init().unwrap();
        assert_eq!(rt.init(), Err(NyError::EINVAL));
    }

    #[test]
    fn runtime_version() {
        let v = nyrqis_nyruntime_version();
        assert_eq!(v >> 16, 1);
        assert_eq!(v & 0xFFFF, 0);
    }
}
