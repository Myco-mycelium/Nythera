//! Minimal, bounds-checked ELF64 reader for the embedded kernel image.
//!
//! The stub carries the kernel ELF inside itself (include_bytes!), so
//! the kernel's trust story is: the firmware verifies the stub's
//! Authenticode signature, and the stub only ever runs the image its
//! own signed bytes contain. This module reads exactly two things out
//! of that file: the PT_LOAD segments' physical load ranges (this
//! kernel's linker places them at their physical addresses -- LMA ==
//! VMA for the Multiboot2 path -- which the stub copies into RAM
//! identically under UEFI), and the `uefi_entry` symbol address the
//! stub jumps to after handoff.
//!
//! Every read is bounds-checked against the file length, and every
//! structural mismatch is a typed `ElfError` naming the exact reason --
//! the same "a malformed surface is worse than an absent one" posture
//! every other parser in this kernel carries.

pub const ELF64_EM_X86_64: u16 = 62;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ElfError {
    TooSmall,
    BadMagic,
    Not64Bit,
    NotExecutable(Option<u16>),
    Truncated(Option<&'static str>),
    NoLoadSegments,
    SymbolMissing(&'static str),
}

#[derive(Clone, Copy)]
pub struct LoadSegment<'a> {
    pub vaddr: u64,
    /// File bytes range within `image`.
    pub data: &'a [u8],
    /// Full in-memory size (>= data.len()); the difference is zero-filled.
    pub memsz: u64,
    pub flags: u32,
}

pub struct KernelImage<'a> {
    pub image: &'a [u8],
    pub segments: [Option<LoadSegment<'a>>; 8],
    pub entry: u64,
}

impl<'a> KernelImage<'a> {
    pub fn loaded_end(&self) -> u64 {
        let mut end = 0u64;
        for seg in self.segments.iter().flatten() {
            end = end.max(seg.vaddr + seg.memsz);
        }
        end
    }
}

// Fixed widths (no #[repr] games: this is an external file format read
// through checked slices, not a structure we alias in place).
const EHDR_LEN: usize = 64;
const PHDR_LEN: usize = 56;
const SHDR_LEN: usize = 64;
const SYM_LEN: usize = 24;
/// Multiboot2 boot-loader handoff magic: what GRUB passes in %eax for a
/// Multiboot2 image, and what the stub's entry path passes in RDI.
pub const MB2_BOOTLOADER_MAGIC: u64 = 0x36d7_6289;

impl ElfError {
    /// The one symbol this stub looks up; the error carries a
    /// 'static name instead of swapping the caller's own lifetime
    /// into a 'static-carrying variant.
    fn symbol_name() -> &'static str {
        "uefi_entry"
    }
}

fn u16_le(b: &[u8], off: usize) -> Option<u16> {
    b.get(off..off + 2).map(|w| u16::from_le_bytes([w[0], w[1]]))
}
fn u32_le(b: &[u8], off: usize) -> Option<u32> {
    b.get(off..off + 4).map(|w| u32::from_le_bytes([w[0], w[1], w[2], w[3]]))
}
fn u64_le(b: &[u8], off: usize) -> Option<u64> {
    b.get(off..off + 8).map(|w| u64::from_le_bytes([
        w[0], w[1], w[2], w[3], w[4], w[5], w[6], w[7],
    ]))
}

/// Parses the embedded kernel ELF.
pub fn parse<'b>(image: &'b [u8]) -> Result<KernelImage<'b>, ElfError> {
    if image.len() < EHDR_LEN {
        return Err(ElfError::TooSmall);
    }
    if &image[0..4] != b"\x7fELF" {
        return Err(ElfError::BadMagic);
    }
    if image[4] != 2 {
        return Err(ElfError::Not64Bit);
    }
    if u16_le(image, 18) != Some(ELF64_EM_X86_64) {
        return Err(ElfError::NotExecutable(u16_le(image, 18)));
    }
    if u16_le(image, 6) != Some(1) {
        return Err(ElfError::NotExecutable(u16_le(image, 6)));
    }

    let entry = u64_le(image, 24).ok_or(ElfError::TooSmall)?;
    let phoff = u64_le(image, 32).ok_or(ElfError::TooSmall)? as usize;
    let shoff = u64_le(image, 40).ok_or(ElfError::TooSmall)? as usize;
    let phentsize = u16_le(image, 54).ok_or(ElfError::TooSmall)? as usize;
    let phnum = u16_le(image, 56).ok_or(ElfError::TooSmall)? as usize;
    let shentsize = u16_le(image, 58).ok_or(ElfError::TooSmall)? as usize;
    let shnum = u16_le(image, 60).ok_or(ElfError::TooSmall)? as usize;

    if phentsize != PHDR_LEN || shentsize != SHDR_LEN {
        return Err(ElfError::Truncated(Some("unexpected phdr/shdr entry size")));
    }
    if phoff.checked_add(phnum.checked_mul(PHDR_LEN).ok_or(ElfError::TooSmall)?)
        .ok_or(ElfError::TooSmall)? > image.len()
    {
        return Err(ElfError::Truncated(Some("program header table beyond EOF")));
    }

    let mut segments: [Option<LoadSegment<'b>>; 8] = [None; 8];
    let mut seg_count = 0usize;
    for i in 0..phnum {
        let base = phoff + i * PHDR_LEN;
        let p_type = u32_le(image, base).ok_or(ElfError::TooSmall)?;
        const PT_LOAD: u32 = 1;
        if p_type != PT_LOAD {
            continue;
        }
        let p_offset = u64_le(image, base + 8).ok_or(ElfError::TooSmall)? as usize;
        let p_vaddr = u64_le(image, base + 16).ok_or(ElfError::TooSmall)?;
        let p_filesz = u64_le(image, base + 32).ok_or(ElfError::TooSmall)?;
        let p_memsz = u64_le(image, base + 40).ok_or(ElfError::TooSmall)?;
        let p_flags = u32_le(image, base + 4).ok_or(ElfError::TooSmall)?;

        if p_memsz < p_filesz {
            return Err(ElfError::Truncated(Some("p_memsz < p_filesz")));
        }
        let file_end = p_offset
            .checked_add(p_filesz as usize)
            .ok_or(ElfError::TooSmall)?;
        if file_end > image.len() {
            return Err(ElfError::Truncated(Some("segment file bytes beyond EOF")));
        }
        if seg_count >= segments.len() {
            return Err(ElfError::Truncated(Some("more than 8 PT_LOAD segments")));
        }
        segments[seg_count] = Some(LoadSegment {
            vaddr: p_vaddr,
            data: &image[p_offset..file_end],
            memsz: p_memsz,
            flags: p_flags,
        });
        seg_count += 1;
    }
    if seg_count == 0 {
        return Err(ElfError::NoLoadSegments);
    }

    Ok(KernelImage {
        image,
        segments,
        entry,
    })
}

/// Finds `uefi_entry` in the file's own symbol table (.symtab, STT_FUNC).
pub fn find_symbol(image: &[u8], name: &str) -> Result<u64, ElfError> {
    if image.len() < EHDR_LEN {
        return Err(ElfError::TooSmall);
    }
    let shoff = u64_le(image, 40).ok_or(ElfError::TooSmall)? as usize;
    let shentsize = u16_le(image, 58).ok_or(ElfError::TooSmall)? as usize;
    let shnum = u16_le(image, 60).ok_or(ElfError::TooSmall)? as usize;
    let shstrndx = u16_le(image, 62).ok_or(ElfError::TooSmall)? as usize;

    if shentsize != SHDR_LEN {
        return Err(ElfError::Truncated(Some("unexpected shdr entry size")));
    }
    let sh_table_end = shoff
        .checked_add(shnum.checked_mul(SHDR_LEN).ok_or(ElfError::TooSmall)?)
        .ok_or(ElfError::TooSmall)?;
    if sh_table_end > image.len() {
        return Err(ElfError::Truncated(Some("section header table beyond EOF")));
    }
    if shstrndx >= shnum {
        return Err(ElfError::Truncated(Some("strndx out of range")));
    }

    let sh = |i: usize| -> Result<(u32, usize, usize), ElfError> {
        let base = shoff + i * SHDR_LEN;
        Ok((
            u32_le(image, base + 4).ok_or(ElfError::TooSmall)?,
            u64_le(image, base + 24).ok_or(ElfError::TooSmall)? as usize,
            u64_le(image, base + 32).ok_or(ElfError::TooSmall)? as usize,
        ))
    };

    let (shstr_type, shstr_off, shstr_size) = sh(shstrndx)?;
    const SHT_STRTAB: u32 = 3;
    if shstr_type != SHT_STRTAB {
        return Err(ElfError::Truncated(Some("shstrndx is not a strtab")));
    }
    let strtab = image
        .get(shstr_off..shstr_off.checked_add(shstr_size).ok_or(ElfError::TooSmall)?)
        .ok_or(ElfError::TooSmall)?;
    let _ = strtab;

    const SHT_SYMTAB: u32 = 2;
    for i in 0..shnum {
        let (sh_type, sh_off, sh_size) = sh(i)?;
        if sh_type != SHT_SYMTAB {
            continue;
        }
        let sym_end = sh_off
            .checked_add(sh_size)
            .ok_or(ElfError::TooSmall)?;
        if sym_end > image.len() {
            return Err(ElfError::Truncated(Some("symbol table beyond EOF")));
        }
        // sh_link (offset 40) names the symbol's string table.
        let link = u32_le(image, shoff + i * SHDR_LEN + 40).ok_or(ElfError::TooSmall)? as usize;
        if link >= shnum {
            return Err(ElfError::Truncated(Some("symtab link out of range")));
        }
        let (linked_type, linked_off, linked_size) = sh(link)?;
        if linked_type != SHT_STRTAB {
            return Err(ElfError::Truncated(Some("symtab link is not a strtab")));
        }
        let symstr = image
            .get(linked_off..linked_off.checked_add(linked_size).ok_or(ElfError::TooSmall)?)
            .ok_or(ElfError::TooSmall)?;

        let mut cursor = sh_off;
        while cursor + SYM_LEN <= sym_end {
            let st_name = u32_le(image, cursor).ok_or(ElfError::TooSmall)?;
            let st_info = *image.get(cursor + 4).ok_or(ElfError::TooSmall)?;
            let st_value = u64_le(image, cursor + 8).ok_or(ElfError::TooSmall)?;
            let st_shndx = u16_le(image, cursor + 6).ok_or(ElfError::TooSmall)?;
            // The entry point is defined in boot.s, so the linker emits
            // it with type STT_NOTYPE (=0), not STT_FUNC (=2); accept
            // both. It must be GLOBAL (bind != 0, excluding assembler
            // locals) and defined (a real section or abs index).
            let typ = st_info & 0xf;
            let bind = st_info >> 4;
            if (typ == 2 || typ == 0) && bind != 0 && st_shndx != 0 {
                if let Some(n) = cstr_at(symstr, st_name as usize) {
                    if n == name.as_bytes() {
                        return Ok(st_value);
                    }
                }
            }
            cursor += SYM_LEN;
        }
        break; // first real symtab only
    }
    Err(ElfError::SymbolMissing(ElfError::symbol_name()))
}

fn cstr_at(table: &[u8], off: usize) -> Option<&[u8]> {
    let rest = table.get(off..)?;
    let end = rest.iter().position(|&b| b == 0)?;
    Some(&rest[..end])
}
