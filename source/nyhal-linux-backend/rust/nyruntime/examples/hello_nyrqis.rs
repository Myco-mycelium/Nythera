//! Hello Nyrqis — the first native Nyrqis application.
//!
//! This program demonstrates the minimal NyRuntime execution model:
//! load a program, execute it, and return an exit code.
//!
//! Usage: hello_nyrqis
//!
//! Expected output:
//!   Hello from Nyrqis!
//!   Exit code: 0

use nyrqis_nycore::{Capability, ContainerConfig};
use nyrqis_nyruntime::{Program, Runtime, RuntimeState};

fn main() {
    println!("Hello from Nyrqis Runtime!");

    // Create and initialize the runtime
    let mut rt = Runtime::new(ContainerConfig::default());
    rt.init().expect("failed to initialize runtime");

    // Grant capabilities for a minimal program
    rt.grant_capability(Capability::FilesystemRead).unwrap();
    rt.grant_capability(Capability::IpcSend).unwrap();
    rt.grant_capability(Capability::IpcReceive).unwrap();

    // Load a simple program: 3x NOP, then HALT with exit code 0
    let program = Program {
        name: b"hello_nyrqis".to_vec(),
        entry: 0,
        code: vec![0x01, 0x01, 0x01, 0x00], // NOP NOP NOP HALT
        data: vec![0],                        // exit code 0
        required_caps: Vec::new(),
    };

    rt.load(program).expect("failed to load program");
    assert_eq!(rt.state(), RuntimeState::Loaded);

    let exit_code = rt.execute().expect("failed to execute program");
    println!("Program '{}' exited with code: {}", "hello_nyrqis", exit_code);

    assert_eq!(exit_code, 0);
    println!("Nyrqis runtime: OK");
}
