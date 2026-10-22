//! Hand-rolled UEFI firmware interface (Phase 10).
//!
//! Zero dependencies by an explicit choice recorded in Cargo.toml: this
//! module declares, at the exact layouts the UEFI spec fixes for
//! x86_64, only the slice of the firmware surface this stub uses --
//! console output, the boot-services memory/exit primitives, the
//! runtime-services variable reader, and the configuration table walk
//! for the ACPI RSDP. Everything here is C-ABI: the EFI calling
//! convention on x86_64 is the Microsoft x64 ABI, which is exactly
//! what Rust's stable `efiapi` ABI compiles to on this target, so the
//! function pointer types below are declared `extern "efiapi"`.
//!
//! Rule kept from every other hand-written module in this kernel: a
//! malformed or adversarial firmware surface must refuse loudly, never
//! dereference a pointer past the bounds the structure itself declares.

use core::ffi::c_void;

pub type Handle = *mut c_void;
/// EFI_STATUS is UINTN: 64-bit on x86_64. (A u32-typed status field
/// would silently truncate the high-bit failure namespace.)
pub type Status = u64;

pub const EFI_SUCCESS: Status = 0;
pub const EFI_BUFFER_TOO_SMALL: Status = 0x8000_0000_0000_0005;
pub const EFI_INVALID_PARAMETER: Status = 0x8000_0000_0000_0002;
pub const EFI_NOT_FOUND: Status = 0x8000_0000_0000_000E;

// EFI System Table's own header signature, read back as a fact.
pub const EFI_SYSTEM_TABLE_SIGNATURE: u64 = 0x5453_5953_2049_4249; // "IBI SYST"

// Memory types (UEFI spec, EFI_MEMORY_TYPE).
pub const MT_RESERVED: u32 = 0;
pub const MT_LOADER_CODE: u32 = 1;
pub const MT_LOADER_DATA: u32 = 2;
pub const MT_BS_CODE: u32 = 3;
pub const MT_BS_DATA: u32 = 4;
pub const MT_RT_CODE: u32 = 5;
pub const MT_RT_DATA: u32 = 6;
pub const MT_CONVENTIONAL: u32 = 7;
pub const MT_UNUSABLE: u32 = 8;
pub const MT_ACPI_RECLAIM: u32 = 9;
pub const MT_ACPI_NVS: u32 = 10;
pub const MT_MMIO: u32 = 11;

// GetMemoryMap's required descriptor version.
pub const MEMORY_DESCRIPTOR_VERSION: u32 = 1;

// AllocatePages types.
pub const ALLOC_ANY: u32 = 0;
pub const ALLOC_MAX_ADDRESS: u32 = 1;
pub const ALLOC_ADDRESS: u32 = 2;

// Variable attribute: we require nonvolatile for the SecureBoot read.
pub const VARIABLE_ATTRIBUTE_NONVOLATILE: u32 = 0x1;

/// One entry of the boot-services memory map (40 bytes as the spec
/// fixes it for x86_64).
#[repr(C)]
#[derive(Clone, Copy)]
pub struct MemoryDescriptor {
    pub r#type: u32,
    pub pad: u32,
    pub physical_start: u64,
    pub virtual_start: u64,
    pub number_of_pages: u64,
    pub attribute: u64,
}

/// A 128-bit GUID, stored in the spec's little-endian mixed form.
#[repr(C)]
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Guid([u8; 16]);

impl Guid {
    /// Builds from the "canonical" compact string form
    /// (`time-low`/`time-mid`/`time-high-and-version`/clock-seq-and-node)
    /// the spec prints everywhere.
    pub const fn new(time_low: u32, time_mid: u16, time_hi: u16, rest: [u8; 8]) -> Self {
        Guid([
            time_low as u8,
            (time_low >> 8) as u8,
            (time_low >> 16) as u8,
            (time_low >> 24) as u8,
            time_mid as u8,
            (time_mid >> 8) as u8,
            time_hi as u8,
            (time_hi >> 8) as u8,
            rest[0],
            rest[1],
            rest[2],
            rest[3],
            rest[4],
            rest[5],
            rest[6],
            rest[7],
        ])
    }
}

/// EFI_GLOBAL_VARIABLE -- the vendor GUID firmware-owned variables live in.
pub const GLOBAL_VARIABLE_GUID: Guid =
    Guid::new(0x8be4df61, 0x93ca, 0x11d2, [0xaa, 0x0d, 0x00, 0xe0, 0x98, 0x03, 0x2b, 0x8c]);

/// ACPI 2.0 configuration-table GUID (RSDP ≥ rev 2 carrier's table).
pub const ACPI_20_TABLE_GUID: Guid =
    // ACPI 2.0 table GUID: {8868e871-e4f1-11d3-bc22-0080c73c8881}
    Guid::new(0x8868e871, 0xe4f1, 0x11d3, [0xbc, 0x22, 0x00, 0x80, 0xc7, 0x3c, 0x88, 0x81]);

/// ACPI 1.0 configuration-table GUID.
pub const ACPI_10_TABLE_GUID: Guid =
    Guid::new(0xeb9d2d31, 0x2d88, 0x11d3, [0x9a, 0x16, 0x00, 0x90, 0x27, 0x3f, 0xc1, 0x4d]);

#[repr(C)]
pub struct TableHeader {
    pub signature: u64,
    pub revision: u32,
    pub header_size: u32,
    pub crc32: u32,
    pub _reserved: u32,
}

/// `UINTN` is `usize` on x86_64.

/// The EFI_SYSTEM_TABLE: typed only where this stub reads it.
#[repr(C)]
pub struct SystemTable {
    pub hdr: TableHeader,
    pub firmware_vendor: usize,
    /// u32 + pad3 to the next 8-byte field (spec: FirmwareRevision at 32,
    /// ConsoleInHandle at 40). A clean record of the pad, not an accident.
    pub firmware_revision_and_pad: u64,
    pub console_in_handle: Handle,
    pub con_in: usize,
    pub console_out_handle: Handle,
    pub con_out: *const SimpleTextOut,
    pub standard_error_handle: Handle,
    pub std_err: usize,
    pub runtime_services: *const RuntimeServices,
    pub boot_services: *const BootServices,
    pub number_of_table_entries: usize,
    pub configuration_table: *const ConfigurationTable,
}

#[repr(C)]
pub struct ConfigurationTable {
    pub vendor_guid: Guid,
    pub vendor_table: *mut c_void,
}

/// The SIMPLE_TEXT_OUTPUT_PROTOCOL surface used: `output_string` and
/// `clear_screen`-slot filler so the struct layout is explicit.
#[repr(C)]
pub struct SimpleTextOut {
    pub reset: unsafe extern "efiapi" fn(*const SimpleTextOut, u8) -> Status,
    pub output_string: unsafe extern "efiapi" fn(*const SimpleTextOut, *const u16) -> Status,
    pub test_string: usize,
    pub query_mode: usize,
    pub set_mode: usize,
    pub enable_cursor: usize,
    pub clear_screen: usize,
    pub set_attribute: usize,
}

/// EFI_BOOT_SERVICES, typed only at the three functions this stub uses;
/// every other slot is carried as an opaque pointer so the offsets stay
/// conversion-visible if the spec ever grows above.
#[repr(C)]
pub struct BootServices {
    pub hdr: TableHeader,
    pub raise_tpl: usize,
    pub restore_tpl: usize,
    pub allocate_pages: unsafe extern "efiapi" fn(
        // EFI_BOOT_SERVICES functions are free functions: they carry no
        // `This` parameter (unlike protocol member functions such as
        // SimpleTextOut.OutputString). Passing one probed as EINVAL on
        // every boot-services call; the register shift WAS the bug.
        u32,
        u32,
        usize,
        *mut u64,
    ) -> Status,
    pub free_pages: usize,
    /// (map_size bytes, mmap, key, desc_size, desc_version)
    pub get_memory_map: unsafe extern "efiapi" fn(
        *mut usize,
        *mut MemoryDescriptor,
        *mut u64,
        *mut usize,
        *mut u32,
    ) -> Status,
    pub allocate_pool: unsafe extern "efiapi" fn(
        u32,
        usize,
        *mut c_void,
    ) -> Status,
    pub free_pool: usize,
    pub create_event: usize,
    pub set_timer: usize,
    pub wait_for_event: usize,
    pub signal_event: usize,
    pub close_event: usize,
    pub check_event: usize,
    pub install_protocol_interface: usize,
    pub reinstall_protocol_interface: usize,
    pub uninstall_protocol_interface: usize,
    pub handle_protocol: usize,
    pub reserved: usize,
    pub register_protocol_notify: usize,
    pub locate_handle: usize,
    pub locate_device_path: usize,
    pub install_configuration_table: usize,
    pub load_image: usize,
    pub start_image: usize,
    pub exit: usize,
    pub unload_image: usize,
    pub exit_boot_services:
        unsafe extern "efiapi" fn(Handle, u64) -> Status,
    pub get_next_monotonic_count: usize,
    pub stall: usize,
    /// (count, code, data, extended)
    pub set_watchdog_timer:
        unsafe extern "efiapi" fn(usize, u64, u64) -> Status,
    pub connect_controller: usize,
    pub disconnect_controller: usize,
    pub open_protocol: usize,
    pub close_protocol: usize,
    pub open_protocol_information: usize,
    pub protocols_per_handle: usize,
    pub locate_handle_buffer: usize,
    pub locate_protocol: usize,
    pub install_multiple_protocol_interfaces: usize,
    pub uninstall_multiple_protocol_interfaces: usize,
    pub calculate_crc32: usize,
    pub copy_mem: usize,
    pub set_mem: usize,
    pub create_event_ex: usize,
}

/// EFI_RUNTIME_SERVICES, typed at `get_variable`.
#[repr(C)]
pub struct RuntimeServices {
    pub hdr: TableHeader,
    pub get_time: usize,
    pub set_time: usize,
    pub get_wakeup_time: usize,
    pub set_virtual_address_map: usize,
    pub convert_pointer: usize,
    /// (variable_name: *const u16, guid: *const Guid, attributes: *mut u32,
    ///  data_size: *mut usize, data: *mut c_void)
    pub get_variable: unsafe extern "efiapi" fn(
        *const u16,
        *const Guid,
        *mut u32,
        *mut usize,
        *mut c_void,
    ) -> Status,
    pub get_next_variable_name: usize,
    pub set_variable: usize,
    pub get_next_high_monotonic_count: usize,
    pub reset_system: usize,
    pub update_capsule: usize,
    pub query_capsule_capabilities: usize,
    pub query_variable_info: usize,
}

// ---- offset assertions: the spec fixes these, so the compiler enforces
// that the hand-rolled layout cannot silently drift. ----
const _: () = assert!(core::mem::size_of::<TableHeader>() == 24);
const _: () = assert!(core::mem::size_of::<MemoryDescriptor>() == 40);
const _: () = assert!(core::mem::size_of::<RuntimeServices>() == 24 + 13 * 8);
const _: () = assert!(core::mem::size_of::<BootServices>() == 376);
const _: () = assert!(core::mem::offset_of!(SystemTable, con_out) == 64);
const _: () = assert!(core::mem::offset_of!(SystemTable, runtime_services) == 88);
const _: () = assert!(core::mem::offset_of!(SystemTable, boot_services) == 96);

pub fn status_string(status: Status) -> &'static str {
    match status {
        EFI_SUCCESS => "EFI_SUCCESS",
        EFI_INVALID_PARAMETER => "EFI_INVALID_PARAMETER",
        EFI_BUFFER_TOO_SMALL => "EFI_BUFFER_TOO_SMALL",
        EFI_NOT_FOUND => "EFI_NOT_FOUND",
        _ => "<other>",
    }
}
