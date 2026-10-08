//! QEMU isa-debug-exit support, compiled in only under the `qemu_test`
//! feature -- this is test infrastructure, not something a real boot
//! (or a boot on real hardware, where this I/O port doesn't exist)
//! should ever touch. `tools/qemu_boot_test.sh` runs QEMU with
//! `-device isa-debug-exit,iobase=0xf4,iosize=0x04` and checks the
//! process exit code: QEMU exits with `(value << 1) | 1`, so writing
//! `0x10` here produces host exit code 33, unambiguously distinct
//! from QEMU's own default exit codes.

use x86_64::instructions::port::Port;

#[repr(u32)]
pub enum QemuExitCode {
    Success = 0x10,
    Failed = 0x11,
}

pub fn exit(code: QemuExitCode) -> ! {
    unsafe {
        let mut port = Port::new(0xf4);
        port.write(code as u32);
    }
    // QEMU should already have exited by this point; loop defensively
    // in case it's run without the isa-debug-exit device attached.
    crate::hlt_loop();
}

pub fn success() -> ! {
    exit(QemuExitCode::Success);
}
