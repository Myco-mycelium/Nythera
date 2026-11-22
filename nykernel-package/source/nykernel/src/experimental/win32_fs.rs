//! A read-only in-memory volume, the thing `CreateFileA`/`ReadFile`/
//! `CloseHandle` actually act on.
//!
//! # Why a separate module
//!
//! The surface (`experimental::win32`) owns Win32 *semantics*: which arguments
//! are legal, what each refusal is called, and what a Windows-typed result
//! looks like. The volume owns *state*: which files exist, which handles are
//! open, and where each one's read cursor is. Keeping them apart means the
//! dispatcher never reaches into a file table and the volume never has to know
//! what a transient handle or an `int $0x90` is.
//!
//! # What this volume is, precisely
//!
//! Three files on `C:`, read-only, loaded from the kernel image itself
//! ([`FILES`]) -- there is no disk, no block device, no cache and no writer.
//! One of them is text, one is deliberately *not* text (it contains `0x00`,
//! `0x80` and `0xfe`), and one is empty, because a byte-oriented `ReadFile`
//! and EOF-at-zero-bytes are the two behaviours a text-only table would never
//! exercise.
//!
//! Path rules, stated rather than assumed: a path must be fully qualified
//! (`C:\...`), because this volume has no current directory to resolve a
//! relative path against and guessing one would be a silent lie. Only drive
//! `C:` is mounted, so any other drive letter is [`FsError::UnknownVolume`].
//! Comparison is ASCII case-insensitive and treats `\` and `/` as the same
//! separator, which is what NTFS and Win32 path parsing do.
//!
//! # Handles
//!
//! `CreateFileA` hands out `0x100 + 4 * slot` for the first free slot of
//! [`MAX_OPEN`]. A handle is therefore a capability: a value this module never
//! issued is [`FsError::NotAFileHandle`], a value it issued and then closed is
//! [`FsError::ClosedHandle`], and slots are reused in order after a close (the
//! boot proof depends on that reuse). The console handle `0xb` is issued by the
//! surface, not here, so it is never a file handle.

use core::sync::atomic::{AtomicU64, Ordering};

/// The first file handle value. Windows makes no promise about handle values,
/// only that a valid one is distinct from `INVALID_HANDLE_VALUE`; this surface
/// picks a small aligned range so the boot transcript can name it.
pub const HANDLE_BASE: u64 = 0x100;

/// Spacing between file handles: four, the alignment NT uses for handles.
pub const HANDLE_STRIDE: u64 = 4;

/// How many files may be open at once. Small on purpose: the refusal path
/// ([`FsError::OutOfHandles`]) is part of the contract and the boot proof
/// exercises it, which it could not if the table were effectively unbounded.
pub const MAX_OPEN: usize = 4;

/// `MAX_PATH` in characters, the Windows bound on a path length. A path with
/// no NUL before this many bytes is refused rather than scanned forever.
pub const MAX_PATH: usize = 260;

/// One file on the volume: its fully qualified path and its bytes.
pub struct File {
    pub path: &'static [u8],
    pub data: &'static [u8],
}

/// The whole volume. Read-only, so this is the only copy of the contents and
/// `ReadFile` copies out of it into the client's own buffer.
pub const FILES: [File; 3] = [
    File {
        path: b"C:\\nyrqis\\hello.txt",
        data: b"hello from the read-only volume\r\n",
    },
    File {
        path: b"C:\\nyrqis\\bytes.bin",
        data: &[0x00, 0x01, 0x7f, 0x80, 0xfe, 0x00, 0x55, 0xaa],
    },
    File {
        path: b"C:\\nyrqis\\empty.txt",
        data: b"",
    },
];

/// Every way the volume can refuse a request. The surface wraps these in
/// `win32::Win32Error::File`, so a refusal from down here is still typed and
/// still says which rule it broke.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FsError {
    /// A relative path: this volume has no current directory to resolve it.
    PathNotAbsolute,
    /// A drive this kernel does not mount (only `C:` is).
    UnknownVolume,
    /// No file of that name on the volume. `CreateFileA` turns this into
    /// `INVALID_HANDLE_VALUE` rather than an error, as Windows does.
    FileNotFound,
    /// All [`MAX_OPEN`] slots are in use.
    OutOfHandles,
    /// Not a value this module issued.
    NotAFileHandle,
    /// A value this module issued, then closed.
    ClosedHandle,
}

/// One open file: which volume file it is, and how far `ReadFile` has read.
#[derive(Clone, Copy)]
struct Open {
    file: usize,
    offset: u64,
}

static mut OPEN: [Option<Open>; MAX_OPEN] = [None; MAX_OPEN];
static OPENS: AtomicU64 = AtomicU64::new(0);

/// The handle for slot `slot`. Not a lookup: the mapping is the definition.
pub const fn handle_at(slot: usize) -> u64 {
    HANDLE_BASE + slot as u64 * HANDLE_STRIDE
}

/// How many files are open right now. The boot proof asserts this is zero
/// after the client closes everything it opened.
pub fn open_count() -> usize {
    OPENS.load(Ordering::Relaxed) as usize
}

/// Opens `path` (a NUL-stripped byte string the surface already validated)
/// and returns its handle.
///
/// # Safety
/// Single-threaded kernel: no other task may touch [`OPEN`] concurrently.
pub unsafe fn open(path: &[u8]) -> Result<u64, FsError> {
    let file = lookup(path)?;
    let slots = &mut *&raw mut OPEN;
    let slot = slots
        .iter()
        .position(|s| s.is_none())
        .ok_or(FsError::OutOfHandles)?;
    slots[slot] = Some(Open { file, offset: 0 });
    OPENS.fetch_add(1, Ordering::Relaxed);
    Ok(handle_at(slot))
}

/// Reads up to `len` bytes at the handle's cursor into `dst`, advances the
/// cursor, and returns how many bytes were read -- zero at EOF, which on
/// Windows is a successful read of nothing, not a failure.
///
/// # Safety
/// `dst` must be writable for `len` bytes; the surface validates it against
/// the client's own regions before calling.
pub unsafe fn read(handle: u64, dst: *mut u8, len: u64) -> Result<u64, FsError> {
    let slot = slot_of(handle)?;
    let open = (*&mut OPEN)[slot]
        .as_mut()
        .ok_or(FsError::ClosedHandle)?;
    let data = FILES[open.file].data;
    let start = open.offset as usize;
    // `offset` only ever advances by a count bounded by the remaining bytes,
    // so `start <= data.len()` always holds; `saturating_sub` states that
    // without a panic path in the kernel.
    let remaining = data.len().saturating_sub(start);
    let count = core::cmp::min(len, remaining as u64);
    core::ptr::copy_nonoverlapping(data[start..].as_ptr(), dst, count as usize);
    open.offset += count;
    Ok(count)
}

/// Closes `handle`, releasing its slot (and its read cursor).
///
/// # Safety
/// Single-threaded kernel: no other task may touch [`OPEN`] concurrently.
pub unsafe fn close(handle: u64) -> Result<(), FsError> {
    let slot = slot_of(handle)?;
    let slots = &mut *&raw mut OPEN;
    if slots[slot].take().is_none() {
        return Err(FsError::ClosedHandle);
    }
    OPENS.fetch_sub(1, Ordering::Relaxed);
    Ok(())
}

/// The slot a handle names, or [`FsError::NotAFileHandle`] if the value is not
/// one [`handle_at`] could have produced.
fn slot_of(handle: u64) -> Result<usize, FsError> {
    let offset = handle.checked_sub(HANDLE_BASE).ok_or(FsError::NotAFileHandle)?;
    if offset % HANDLE_STRIDE != 0 {
        return Err(FsError::NotAFileHandle);
    }
    let slot = (offset / HANDLE_STRIDE) as usize;
    if slot >= MAX_OPEN {
        return Err(FsError::NotAFileHandle);
    }
    Ok(slot)
}

/// Finds the volume file `path` names, applying the path rules above.
fn lookup(path: &[u8]) -> Result<usize, FsError> {
    let qualified = path.len() >= 3 && path[1] == b':' && matches!(path[2], b'\\' | b'/');
    if !qualified {
        return Err(FsError::PathNotAbsolute);
    }
    if !path[0].eq_ignore_ascii_case(&b'C') {
        return Err(FsError::UnknownVolume);
    }
    FILES
        .iter()
        .position(|f| same_path(path, f.path))
        .ok_or(FsError::FileNotFound)
}

/// `true` when two fully qualified paths name the same file: same length,
/// ASCII case-insensitive, `\` and `/` interchangeable.
fn same_path(a: &[u8], b: &[u8]) -> bool {
    fn sep(byte: u8) -> bool {
        byte == b'\\' || byte == b'/'
    }
    a.len() == b.len()
        && a.iter().zip(b).all(|(&x, &y)| {
            if sep(x) && sep(y) {
                true
            } else {
                x.eq_ignore_ascii_case(&y)
            }
        })
}
