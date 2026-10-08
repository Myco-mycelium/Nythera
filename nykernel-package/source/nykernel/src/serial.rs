//! Minimal 16550 UART driver over COM1 (I/O port 0x3F8).
//!
//! This is the kernel's primary diagnostic channel: QEMU's `-serial
//! stdio` connects COM1 straight to the host terminal, so every
//! `serial_println!` in this kernel is visible without a display and
//! is exactly what the CI boot-test (`tools/qemu_boot_test.sh`) greps
//! for to determine pass/fail. Written by hand rather than pulling in
//! the `uart_16550` crate: it's a handful of port writes, and keeping
//! it in-tree means there's one fewer external dependency touching
//! the earliest, hardest-to-debug part of the kernel.

use core::fmt;
use x86_64::instructions::port::Port;
use spin::Mutex;

const COM1: u16 = 0x3F8;

pub struct SerialPort {
    data: Port<u8>,
    interrupt_enable: Port<u8>,
    fifo_control: Port<u8>,
    line_control: Port<u8>,
    modem_control: Port<u8>,
    line_status: Port<u8>,
}

impl SerialPort {
    const fn new(base: u16) -> Self {
        SerialPort {
            data: Port::new(base),
            interrupt_enable: Port::new(base + 1),
            fifo_control: Port::new(base + 2),
            line_control: Port::new(base + 3),
            modem_control: Port::new(base + 4),
            line_status: Port::new(base + 5),
        }
    }

    /// Standard 16550 bring-up sequence: disable interrupts, set the
    /// baud-rate divisor (38400 baud), configure 8N1 framing, enable
    /// and reset the FIFOs, and assert DTR/RTS/OUT2 so QEMU's serial
    /// backend actually sees the line as "up".
    unsafe fn init(&mut self) {
        self.interrupt_enable.write(0x00);
        self.line_control.write(0x80); // enable DLAB to set divisor
        self.data.write(0x03); //  divisor low byte  (38400 baud)
        self.interrupt_enable.write(0x00); // divisor high byte
        self.line_control.write(0x03); // 8 bits, no parity, 1 stop bit; DLAB off
        self.fifo_control.write(0xC7); // enable FIFO, clear, 14-byte threshold
        self.modem_control.write(0x0B); // DTR | RTS | OUT2
    }

    fn line_status(&mut self) -> u8 {
        unsafe { self.line_status.read() }
    }

    fn write_byte(&mut self, byte: u8) {
        while self.line_status() & 0x20 == 0 {
            core::hint::spin_loop(); // wait for the transmit-empty bit
        }
        unsafe { self.data.write(byte) };
    }
}

impl fmt::Write for SerialPort {
    fn write_str(&mut self, s: &str) -> fmt::Result {
        for byte in s.bytes() {
            match byte {
                b'\n' => {
                    self.write_byte(b'\r');
                    self.write_byte(b'\n');
                }
                _ => self.write_byte(byte),
            }
        }
        Ok(())
    }
}

pub static SERIAL1: Mutex<SerialPort> = Mutex::new(SerialPort::new(COM1));

/// MUST be called exactly once, during [`crate::boot_phase::BootPhase::DiagnosticsInit`].
pub fn init() {
    unsafe { SERIAL1.lock().init() };
}

#[doc(hidden)]
pub fn _print(args: fmt::Arguments) {
    use fmt::Write;
    SERIAL1
        .lock()
        .write_fmt(args)
        .expect("serial write should never fail");
}

#[macro_export]
macro_rules! serial_print {
    ($($arg:tt)*) => {
        $crate::serial::_print(format_args!($($arg)*))
    };
}

#[macro_export]
macro_rules! serial_println {
    () => ($crate::serial_print!("\n"));
    ($fmt:expr) => ($crate::serial_print!(concat!($fmt, "\n")));
    ($fmt:expr, $($arg:tt)*) => ($crate::serial_print!(concat!($fmt, "\n"), $($arg)*));
}
