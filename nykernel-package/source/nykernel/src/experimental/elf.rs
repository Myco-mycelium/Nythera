//! Minimal ELF64 loader.
//!
//! Parses just enough of the ELF64 format to load a flat, statically-
//! linked executable -- fixed-address (`ET_EXEC`) or position-
//! independent (`ET_DYN`, see below) -- the file header (for `e_entry`
//! and sanity checks) and `PT_LOAD` program headers (for what to map
//! where). No dynamic linking (no symbol resolution, no imported
//! libraries), no `.bss`-only segments beyond simple
//! `p_memsz > p_filesz` zero-fill. This is deliberately the smallest
//! useful subset -- see `crate::experimental`'s module banner: a real
//! Windows PE loader or a real Android ELF/DEX loader (Phase 5/6) will
//! need substantially more (real dynamic linking, symbol resolution,
//! imported libraries), but they'll need this exact category of work
//! -- parse untrusted headers defensively, map segments with
//! file-specified permissions, zero what the file says should be
//! zero, relocate what the file says needs relocating -- underneath
//! whatever else they add.
//!
//! # `ET_DYN` / position-independent executables
//!
//! `ET_EXEC` binaries bake absolute addresses into their code and data
//! at link time -- they can only run at exactly the address they were
//! linked for, so `load` always maps them with `load_bias = 0` (any
//! other value would be wrong, since nothing in the file would be
//! adjusted to match). `ET_DYN` binaries (built with `ld -pie
//! --no-dynamic-linker`, producing a self-contained "static-pie"
//! executable with no interpreter and no external symbol dependencies)
//! can be loaded at *any* base address, provided the loader applies
//! the relocations the file's `PT_DYNAMIC` segment describes. This
//! loader implements exactly one relocation type -- `R_X86_64_RELATIVE`
//! (`*(base + r_offset) = base + r_addend`) -- which is the only kind
//! a self-relocating, no-external-symbols static-pie binary needs; a
//! binary requiring symbol-resolved relocations against symbols the
//! object does not itself DEFINE (`SHN_UNDEF` entries -- genuinely
//! cross-object dependencies) is out of scope and REFUSED with a
//! typed error; in-object symbol resolution (a `ld -shared`-produced
//! object whose GLOB_DAT/JUMP_SLOT/64 entries point at its own
//! exports) IS implemented -- see [`DynTables`], [`lookup_symbol`],
//! and `experimental::elf_dyn_demo` for the end-to-end proof.
//! Full dynamic linking (dependency walking, interposition, lazy
//! PLT binding) remains future work and is refused rather than
//! approximated.
//!
//! Like `crate::multiboot`, this stays paranoid about untrusted input:
//! every offset and size is bounds-checked against the actual byte
//! slice before being dereferenced. A malformed or adversarial ELF
//! file must be rejected with an error, not walk this kernel off the
//! end of its own image.

use x86_64::structures::paging::{Mapper, OffsetPageTable, Page, PageTableFlags, Size4KiB};
use x86_64::VirtAddr;

use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::memory::paging;

const ELF_MAGIC: [u8; 4] = [0x7f, b'E', b'L', b'F'];
const ELFCLASS64: u8 = 2;
const ELFDATA2LSB: u8 = 1; // little-endian
const ET_EXEC: u16 = 2; // fixed-address executable
const ET_DYN: u16 = 3; // position-independent executable (see module doc)
const PT_LOAD: u32 = 1;
const PT_DYNAMIC: u32 = 2;
const PF_X: u32 = 1 << 0;
const PF_W: u32 = 1 << 1;
#[allow(dead_code)]
const PF_R: u32 = 1 << 2; // every segment this loader maps is readable regardless; kept for documentation

const EHDR_SIZE: usize = 64;
const PHDR_SIZE: usize = 56;
const DYN_ENTRY_SIZE: usize = 16; // Elf64_Dyn: { d_tag: i64, d_val/d_ptr: u64 }
const RELA_ENTRY_SIZE: usize = 24; // Elf64_Rela: { r_offset: u64, r_info: u64, r_addend: i64 }
const SYM_ENTRY_SIZE: usize = 24; // Elf64_Sym: { st_name: u32, st_info: u8, st_other: u8, st_shndx: u16, st_value: u64, st_size: u64 }

/// SHN_UNDEF (0): a symbol defined in no part of THIS object; symbol-
/// resolved relocations against it need cross-object resolution, which
/// this loader honestly refuses rather than guessing.
const SHN_UNDEF: u16 = 0;

const DT_NULL: i64 = 0; // terminates the dynamic-entry array
const DT_NEEDED: i64 = 1; // strtab offset of a required object's SONAME
const DT_HASH: i64 = 4; // SysV hash table address (defines the symbol count via nchain)
const DT_STRTAB: i64 = 5; // string table address
const DT_SYMTAB: i64 = 6; // symbol table address
const DT_RELA: i64 = 7; // address (pre-bias) of the relocation table
const DT_RELASZ: i64 = 8; // total size in bytes of the relocation table
const DT_RELAENT: i64 = 9; // size of one relocation entry (must be RELA_ENTRY_SIZE)
const DT_STRSZ: i64 = 10; // total string-table size in bytes (its sanity bound)
const DT_SYMENT: i64 = 11; // size of one symbol-hash table entry (must be SYM_ENTRY_SIZE)
const DT_SONAME: i64 = 14; // strtab offset of this object's own SONAME
const DT_PLTGOT: i64 = 3; // base of the PLT GOT
const DT_PLTRELSZ: i64 = 2; // total size of the PLT relocation table
const DT_PLTREL: i64 = 20; // 7 (DT_RELA) for x86-64
const DT_JMPREL: i64 = 23; // address (pre-bias) of the PLT relocation table
const DT_VERSYM: i64 = 0x6fff_fff0; // per-dynsym-entry u16 version index array
const DT_VERDEF: i64 = 0x6fff_fffc; // version-definition table (what this object PROVIDES)
const DT_VERDEFNUM: i64 = 0x6fff_fffd; // verdef entry count
const DT_VERNEED: i64 = 0x6fff_fffe; // version-requirement table (what provider objects must provide)
const DT_VERNEEDNUM: i64 = 0x6fff_ffff; // verneed entry count
const DT_GNU_HASH: i64 = 0x6fff_fef5; // GNU-style hash table (the DT_HASH ALTERNATIVE when present; never replaces DT_HASH's authority over the symbol count)

const R_X86_64_RELATIVE: u32 = 8;
const R_X86_64_64: u32 = 1; // S + A, 64-bit: symbol value plus addend
const R_X86_64_GLOB_DAT: u32 = 6; // S, 64-bit: the raw symbol address into a GOT slot
const R_X86_64_JUMP_SLOT: u32 = 7; // S, 64-bit: the PLT GOT slot (same shape as GLOB_DAT here)

/// The shared lazy-binding service vector the loader's trampoline
/// stubs trap on (`int 0x88`): the IDT gate is ring-3-privileged,
/// and its handler dispatches to
/// `experimental::lazy_binder::service`, which keeps the per-object
/// state for EVERY lazy-armed object loaded this boot (one service,
/// many dependents).
pub const LAZY_BIND_VECTOR: u8 = 0x88;

/// The OWN-.plt resolver vector: the LOADer-emitted thunk a GNU
/// lazy-armed object's `.got.plt`[2] points at traps `int 0x8B` here
/// on a JUMP_SLOT's first use -- with the REAL linker-protocol spill
/// on the user stack (PLT1's `push idx`, PLT0's pushed link-map
/// cookie), not the synthetic stub shape. Handler:
/// `experimental::lazy_binder::plt_service`, which resolves + patches
/// and then GUIDES the return straight into the resolved callee
/// (GNU `_dl_runtime_resolve`'s very shape).
pub const PLT_RESOLVE_VECTOR: u8 = 0x8B;

/// Version-definition flag bit: a weak version requirement
/// (`Elf64_Vermaux::vna_flags & VER_FLG_WEAK`). Modelled after the
/// ELF gABI: a weak requirement MAY go unsatisfied; the semantics
/// this loader implements are "resolve if a real candidate exists,
/// otherwise leave the slot 0 and keep going" -- unlike a strong
/// requirement, whose failure is a hard, load-refusing typed error.
const VER_FLG_WEAK: u16 = 0x0002;

/// `STB_WEAK` (the symbol's binding from `st_info`'s upper nibble):
/// a weak UNDEFINED symbol that cannot be resolved is the other half
/// of the ELF weak semantics above -- its slot resolves to 0, the
/// load proceeds, and dereferencing it is the user program's own
/// dynamic consequence (the same posture glibc takes).
const STB_WEAK: u8 = 2;
const STB_BIND_SHIFT: u8 = 4;

/// GNU ld's own program-header type marking the region GNU_RELRO
/// (`PT_GNU_RELRO`, 0x6474e552). See `LoadedRelro` for how this loader
/// honors it.
const PT_GNU_RELRO: u32 = 0x6474_e552;

#[derive(Debug)]
pub enum ElfError {
    TooShort,
    BadMagic,
    Not64Bit,
    NotLittleEndian,
    UnsupportedType,
    NonZeroBiasOnFixedAddressExecutable,
    ProgramHeaderOutOfBounds,
    SegmentDataOutOfBounds,
    NoLoadSegments,
    MappingFailed,
    UnsupportedRelocationType,
    UnsupportedRelocationEntrySize,
    /// A RELRO page's per-page permission update (the temporary
    /// write-grant, or the revoke-to-read-only) could not be applied
    /// through the page tables. Never silent: leaving a RELRO page
    /// writable would silently discard the whole property the region
    /// exists for, so a failed update is a refused load, not a
    /// shrug.
    RelroPermissionUpdateFailed,
    /// A load-time write grant could not be revoked back to the
    /// file-requested read-only state (see the grant/revoke note at
    /// the PT_LOAD mapping). Same posture as the RELRO variant: a
    /// code segment left writable on failure would silently discard
    /// the W^X property the file declared, so this is a refused load.
    SegmentProtectFailed,
    /// A symbol-resolved relocation whose symbol is UNDEFINED in this
    /// object (SHN_UNDEF). This loader resolves within-object only --
    /// a genuinely undefined symbol needs cross-object resolution
    /// (a real dynamic linker / dependency walking), which is the
    /// documented boundary of this increment; refusing here beats
    /// resolving to garbage.
    UndefinedSymbol,
    /// Malformed dynamic-symbol tables: a DT_HASH nchain exceeding a
    /// sane bound, a symbol/table index out of table range, a
    /// DT_SYMENT other than 24, a string offset past DT_STRSZ, or a
    /// missing DT_HASH/DT_SYMTAB/DT_STRTAB where a symbol lookup was
    /// requested. The details live in the variant's doc-comment-free
    /// enum shape for now; each is a hard refusal, never a guessed
    /// answer.
    MalformedDynamicTables,
    /// A symbol LOOKUP (by hash-string) failed: the name is not in
    /// this object's exported symbol set. Distinguishable from
    /// MalformedDynamicTables so a caller can treat "not present" as
    /// an expected negative-control outcome rather than a parse
    /// error.
    SymbolNotFound,
    /// The object declares `DT_NEEDED` entries whose SONAME no object
    /// in the caller's resolver scope provides. Checked BEFORE any
    /// relocation work (the loader never starts relocating a file
    /// whose declared dependencies do not exist in scope). Filesystem
    /// discovery of a NEEDED file remains future work (needs a
    /// storage surface — Phase 8 scope); what this loader enforces is
    /// the CONTRACT between already-loaded objects.
    MissingDependency,
    /// A versioned (DT_VERSYM/DT_VERNEED-driven) symbol requirement
    /// could be found in scope by name but the required VERSION
    /// is not provided by the candidate object (its DT_VERDEF names
    /// do not include the required string), or the candidate is
    /// unversioned where a version was required. Refused, never
    /// resolved ignoring the version.
    VersionNotProvided,
    /// The recursive dependency loader (see
    /// [`load_tree`]) walked a catalog's NEEDED names and found them
    /// looping back into already-in-progress loads (A needs B needs
    /// A). Refused BEFORE any further mapping work: reloading an
    /// object to satisfy itself can never terminate.
    DependencyCycle,
    /// The recursive dependency load exceeded the catalog's depth
    /// cap (a policy constant of [`DepCatalog`]). A dependency chain
    /// deeper than the policy is treated as malformed input, not
    /// recursively consumed to progress.
    DependencyDepthExceeded,
}

/// A cross-object symbol-resolution surface: consulted when the
/// object being relocated carries a symbol-resolved relocation whose
/// symbol is UNDEFINED in the object's own dynsym (SHN_UNDEF). The
/// loader never guesses: what the resolver cannot resolve stays
/// REFUSED (`UndefinedSymbol`), never resolved-to-garbage.
///
/// Search-order semantics, documented and deliberately simple: the
/// object being relocated resolves its OWN dynsym entries first (a
/// relocated `.so`'s defined symbols are never displaced by a
/// registered object's same-named definition), then the resolver is
/// consulted in whatever order it implements -- [`SymbolRegistry`]`'s
/// order is load order, first match wins (a deliberately simple
/// approximation of real interposition's depth-first dependency-graph
/// order; registry order being load order is pinned every boot by
/// [`elf_dyn_cross_demo`]'s registry-order check).
pub trait SymbolResolver {
    /// Per this resolver's search order: `Ok(Some(addr))` = the
    /// runtime address of `name`'s DEFINITION in the search scope,
    /// `Ok(None)` = not defined anywhere this resolver searches, and
    /// `Err` = a resolver-side structural failure (a registered
    /// object's dynamic tables are malformed) -- distinguishable from
    /// "absent" so a malformed registry can never be misread as
    /// "nothing defines it".
    fn resolve(&self, name: &[u8]) -> Result<Option<u64>, ElfError>;

    /// The version-aware form: `required` = the (name, file/SONAME)
    /// pair the requesting object's DT_VERNEED names for the symbol,
    /// `None` = unversioned. [`SymbolRegistry`] implements the full
    /// contract (the candidate must carry the required SONAME and
    /// PROVIDED version, else `VersionNotProvided` / match rules);
    /// the default delegates to the version-agnostic form, which is
    /// the honest declaration of "this resolver does not model
    /// versions" -- for an REQUIRED version such a resolver refuses
    /// at the caller (see the decision in the relocation pass).
    fn resolve_versioned(
        &self,
        name: &[u8],
        required: Option<(&[u8], &[u8])>,
    ) -> Result<Option<u64>, ElfError> {
        let _ = required;
        self.resolve(name)
    }

    /// Whether this resolver's scope satisfies a `DT_NEEDED` SONAME.
    /// The [`SymbolRegistry`] compares against its entries' real
    /// DT_SONAME bytes; the default answers `true` -- the explicit
    /// declaration that this resolver does not model dependency
    /// contracts and must not silently refuse them.
    fn has_soname(&self, soname: &[u8]) -> Result<bool, ElfError> {
        let _ = soname;
        Ok(true)
    }
}

/// The demo-facing registry: ALREADY-LOADED shared objects, in load
/// order. `resolve` walks the entries in order, first DEFINED match
/// wins. Objects are pushed AFTER their own load/relocation
/// completes, so self-definitions always precede registered matches
/// (the self-first rule above is structural, not a convention callers
/// must uphold manually).
///
/// **Interposition**: [`SymbolRegistry::interpose`] marks a
/// registered entry as an OVERLAY that is consulted BEFORE the load
/// order for every LATER object's symbol resolution (an entry loaded
/// earlier is already relocated and keeps its own resolved slots --
/// that is the same one-way effect real `LD_PRELOAD` has). This is
/// the loader's answer to the "interposition beyond load order"
/// limitation: an explicit, opt-in mechanism instead of unspecified
/// search-order magic, pinned every boot by
/// `elf_dyn_deps_demo`'s flipped `dup_symbol` slot check.
#[derive(Clone)]
pub struct SymbolRegistry {
    entries: alloc::vec::Vec<(FatDynTables, u64)>,
    interposed: alloc::vec::Vec<usize>,
}

impl SymbolRegistry {
    pub fn new() -> Self {
        SymbolRegistry {
            entries: alloc::vec::Vec::new(),
            interposed: alloc::vec::Vec::new(),
        }
    }

    /// Registers an already-loaded object (its dynamic tables parsed
    /// with [`parse_dyn_tables`] at ITS load bias) for later objects'
    /// undefined-symbol relocations to resolve against. The entry
    /// carries the object's real DT_SONAME (when it declares one) so
    /// DT_NEEDED contracts and version file-bindings check against
    /// fact, not assumption.
    pub fn push(&mut self, tables: FatDynTables, load_bias: u64) {
        self.entries.push((tables, load_bias));
    }

    /// Marks entry `idx` as an interposition overlay: every symbol
    /// resolution issued by entries registered LATER consults `idx`
    /// FIRST (before load order). The overlay position is validated,
    /// never assumed.
    pub fn interpose(&mut self, idx: usize) {
        if idx >= self.entries.len() {
            panic!("interpose: index {} is not a registered entry", idx);
        }
        if !self.interposed.contains(&idx) {
            self.interposed.push(idx);
        }
    }

    /// The heterogeneous entry list the resolver sees, in visit
    /// order: interposed overlays first, then load order.
    fn visit_order(&self) -> alloc::vec::Vec<(FatDynTables, u64)> {
        let mut order = alloc::vec::Vec::new();
        for &idx in &self.interposed {
            order.push(self.entries[idx].clone());
        }
        // Compressed clone pass: interposed entries are visited first
        // AND remain in load order for everything else.
        for (idx, entry) in self.entries.iter().enumerate() {
            if !self.interposed.contains(&idx) {
                order.push(entry.clone());
            }
        }
        order
    }

    /// The chosen candidate's SONAME lookup, for demo-side prints.
    pub fn soname_at(&self, entry: usize) -> Option<&'static [u8]> {
        self.entries.get(entry).and_then(|(t, _)| t.0.soname)
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// Direct (non-trait) lookup, exposing the same search order as
    /// [`SymbolResolver::resolve`] for demo-side checks that need to
    /// inspect the registry's behavior itself (interposition/order
    /// controls) rather than consume it.
    ///
    /// # Safety
    /// Every registered object's dynamic tables must still be mapped
    /// and populated in the currently-active address space (the
    /// condition `load`/`parse_dyn_tables` guaranteed at registration
    /// time; these checks dereference mapped memory). The demo
    /// call sites satisfy this by keeping everything in one active
    /// `AddressSpace`.
    pub fn lookup(&self, name: &[u8]) -> Result<Option<u64>, ElfError> {
        // SAFETY: caller contract above -- registered tables live
        // in mapped, active space for every call site this kernel
        // has.
        unsafe { ordered_lookup(&self.visit_order(), name) }
    }
}

/// Visit-order walk over (tables, bias) pairs: first DEFINED match
/// wins; `Err` = malformed visibility, never silently reinterpreted
/// as absent.
///
/// # Safety
/// The tables must be mapped and populated in the currently-active
/// address space (the registration-time condition; see
/// `load`/`parse_dyn_tables`).
unsafe fn ordered_lookup(
    entries: &[(FatDynTables, u64)],
    name: &[u8],
) -> Result<Option<u64>, ElfError> {
    for (tables, load_bias) in entries {
        match locate_symbol_in_tables(tables, *load_bias, name)? {
            Some(addr) => return Ok(Some(addr)),
            None => continue,
        }
    }
    Ok(None)
}

/// Whether any entry carries the given DT_SONAME.
fn soname_check(entries: &[(FatDynTables, u64)], soname: &[u8]) -> bool {
    entries
        .iter()
        .any(|(t, _)| matches!(t.0.soname, Some(s) if s == soname))
}

impl SymbolResolver for SymbolRegistry {
    fn resolve(&self, name: &[u8]) -> Result<Option<u64>, ElfError> {
        self.lookup(name)
    }

    /// Search order, documented: interposition overlays first, then
    /// load order, first match wins. With a required (version,
    /// soname) pair, a candidate counts ONLY if its real DT_SONAME
    /// satisfies the required file (an entry with no SONAME never
    /// satisfies a versioned binding naming a file) and its actual
    /// provided version equals the required one. A versionless
    /// candidate under a version requirement is `VersionNotProvided`
    /// -- versions are exactly what a plain name lookup silently
    /// ignores, and silently ignoring is what made a versionless
    /// "resolver" unacceptable.
    fn resolve_versioned(
        &self,
        name: &[u8],
        required: Option<(&[u8], &[u8])>,
    ) -> Result<Option<u64>, ElfError> {
        // SAFETY: registered tables live in mapped, active space (the
        // same caller contract the unsafe DynTables readers document).
        unsafe { versioned_lookup(&self.visit_order(), name, required) }
    }

    fn has_soname(&self, soname: &[u8]) -> Result<bool, ElfError> {
        Ok(soname_check(&self.visit_order(), soname))
    }
}

/// The version-aware per-entry lookup shared between
/// [`SymbolRegistry`] and [`ScopedResolver`]
/// (and thus between every load-time and trap-time resolution path):
/// with a required (version, soname) pair, a candidate counts ONLY
/// if its real DT_SONAME satisfies the required file and its actual
/// provided version equals the required one.
///
/// # Safety
/// The tables must be mapped in the active address space as at
/// registration time.
unsafe fn versioned_lookup(
    entries: &[(FatDynTables, u64)],
    name: &[u8],
    required: Option<(&[u8], &[u8])>,
) -> Result<Option<u64>, ElfError> {
    for (tables, load_bias) in entries {
        if let Some((_, req_file)) = required {
            let entry_soname = match tables.0.soname {
                Some(s) => s,
                None => continue, // cannot prove the file binding
            };
            if entry_soname != req_file {
                continue;
            }
        }
        match locate_symbol_in_tables(tables, *load_bias, name)? {
            None => continue,
            Some(addr) => {
                // The REQUIRED side carries the version contract;
                // only enforce it when the requester demands a
                // version (self-defined and legacy objects stay
                // compatible with the pre-versioning behavior).
                if let Some((req_ver, _)) = required {
                    // Version requirement: the PROVIDING index is
                    // whatever entry name got matched
                    let sym_idx = {
                        let count = tables.0.symbol_count(*load_bias)?;
                        tables.0.lookup_hash_any(*load_bias, name, count)?
                    };
                    let got = tables.0.provided_version(*load_bias, sym_idx)?;
                    match got {
                        Some(v) if v == req_ver => {}
                        _ => return Err(ElfError::VersionNotProvided),
                    }
                }
                return Ok(Some(addr));
            }
        }
    }
    Ok(None)
}

/// The dynamic-table facts one read of `PT_DYNAMIC` yields, parsed
/// once and shared by relocation processing, RELRO planning, and
/// symbol lookup. All addresses are pre-bias (file-relative); every
/// consumer adds the load bias itself, so this struct stays valid for
/// both `ET_EXEC` (bias 0) and `ET_DYN` files.
#[derive(Clone)]
struct DynTables {
    has_dynamic: bool,
    rela_vaddr: Option<u64>,
    rela_size: Option<u64>,
    rela_entsize: Option<u64>,
    symtab_vaddr: Option<u64>,
    strtab_vaddr: Option<u64>,
    strsz: Option<u64>,
    syment: Option<u64>,
    hash_vaddr: Option<u64>,
    /// This object's own SONAME (DT_SONAME, resolved to bytes).
    soname: Option<&'static [u8]>,
    /// The object's declared dependencies (DT_NEEDED strings,
    /// resolved to bytes at parse time).
    needed: alloc::vec::Vec<&'static [u8]>,
    /// String-table offsets collected in-loop, resolved after the
    /// walk once DT_STRTAB/DT_STRSZ are (likely) known.
    needed_raw_offsets: alloc::vec::Vec<u64>,
    soname_raw_offset: Option<u64>,
    /// The PLT relocation table (`DT_JMPREL`, `DT_PLTRELSZ`) --
    /// separately relocatable from `DT_RELA`.
    jmprel_vaddr: Option<u64>,
    plt_relsz: Option<u64>,
    /// The value the object's DT_PLTREL carries (7 == DT_RELA on
    /// x86-64 -- the only PLT-entry shape this loader accepts).
    plt_rel: Option<u64>,
    /// Version machinery: per-symbol u16 version-source indices
    /// (`DT_VERSYM`) and the tables that give those indices meaning
    /// (`DT_VERDEF` etc. for what this object PROVIDES, `DT_VERNEED`
    /// etc. for what its undefined symbols REQUIRE).
    versym_vaddr: Option<u64>,
    verdef_vaddr: Option<u64>,
    verdef_num: Option<u64>,
    verneed_vaddr: Option<u64>,
    verneed_num: Option<u64>,
    /// The GNU hash table (DT_GNU_HASH) when the object carries one:
    /// parsed and used for LOOKUP only -- `symbol_count`'s
    /// authoritative nchain stays in DT_HASH whenever it is present
    /// (the GNU table's structure cannot state the total symbol
    /// count without a chain walk; DT_HASH publishes it directly).
    gnu_hash_vaddr: Option<u64>,
    /// The PLT GOT base (DT_PLTGOT) -- the object's OWN `.got.plt`:
    /// its [1]/[2] slots (the link-map cookie / resolver entry) are
    /// what the GNU dynamic-linker lazy-binding protocol points the
    /// resolver at. Captured so [`arm_own_plt`] can compute their
    /// runtime addresses; `None` for objects without a PLT GOT.
    pltgot_vaddr: Option<u64>,
}

impl DynTables {
    fn new() -> Self {
        DynTables {
            has_dynamic: false,
            rela_vaddr: None,
            rela_size: None,
            rela_entsize: None,
            symtab_vaddr: None,
            strtab_vaddr: None,
            strsz: None,
            syment: None,
            hash_vaddr: None,
            soname: None,
            needed: alloc::vec::Vec::new(),
            needed_raw_offsets: alloc::vec::Vec::new(),
            soname_raw_offset: None,
            jmprel_vaddr: None,
            plt_relsz: None,
            plt_rel: None,
            versym_vaddr: None,
            verdef_vaddr: None,
            verdef_num: None,
            verneed_vaddr: None,
            verneed_num: None,
            gnu_hash_vaddr: None,
            pltgot_vaddr: None,
        }
    }

    /// Reads the PT_DYNAMIC entries out of the ALREADY-MAPPED segment
    /// (the PT_LOAD pass must have populated it). Each 16-byte Elf64_Dyn
    /// is read unaligned through the runtime (bias-added) address;
    /// values are stored pre-bias so consumers re-add bias.
    ///
    /// # Safety
    /// `load_bias + dyn_vaddr .. + dyn_filesz` must lie inside a
    /// PT_LOAD-mapped, populated region (the at-load-call condition).
    unsafe fn read_from_memory(load_bias: u64, dyn_vaddr: u64, dyn_filesz: u64) -> Result<Self, ElfError> {
        let mut t = DynTables::new();
        t.has_dynamic = true;
        let dyn_base = load_bias.wrapping_add(dyn_vaddr);
        let dyn_entry_count = (dyn_filesz as usize) / DYN_ENTRY_SIZE;

        for i in 0..dyn_entry_count {
            let entry_addr = dyn_base + (i * DYN_ENTRY_SIZE) as u64;
            let (tag, val) = {
                let tag = core::ptr::read_unaligned(entry_addr as *const i64);
                let val = core::ptr::read_unaligned((entry_addr + 8) as *const u64);
                (tag, val)
            };
            match tag {
                DT_NULL => break,
                DT_HASH => t.hash_vaddr = Some(val),
                DT_STRTAB => t.strtab_vaddr = Some(val),
                DT_SYMTAB => t.symtab_vaddr = Some(val),
                DT_RELA => t.rela_vaddr = Some(val),
                DT_RELASZ => t.rela_size = Some(val),
                DT_RELAENT => t.rela_entsize = Some(val),
                DT_STRSZ => t.strsz = Some(val),
                DT_SYMENT => t.syment = Some(val),
                DT_NEEDED => t.needed_raw_offsets.push(val),
                DT_SONAME => t.soname_raw_offset = Some(val),
                DT_JMPREL => t.jmprel_vaddr = Some(val),
                DT_PLTGOT => t.pltgot_vaddr = Some(val),
                DT_PLTRELSZ => t.plt_relsz = Some(val),
                DT_PLTREL => t.plt_rel = Some(val),
                DT_VERSYM => t.versym_vaddr = Some(val),
                DT_VERDEF => t.verdef_vaddr = Some(val),
                DT_VERDEFNUM => t.verdef_num = Some(val),
                DT_VERNEED => t.verneed_vaddr = Some(val),
                DT_VERNEEDNUM => t.verneed_num = Some(val),
                DT_GNU_HASH => t.gnu_hash_vaddr = Some(val),
                _ => {} // other GNU extensions are irrelevant while DT_HASH is present
            }
        }

        // String-table-backed entries (DT_NEEDED strings, DT_SONAME)
        // resolve to bytes NOW, once the loop has (likely) seen
        // DT_STRTAB/DT_STRSZ -- with the same bounded-discipline the
        // `symbol_at` name reader uses. Deferred to here rather than
        // in-loop because dynamic-entry order is not guaranteed.
        if let Some(strtab) = t.strtab_vaddr {
            if let Some(strsz) = t.strsz {
                let str_base = load_bias.wrapping_add(strtab);
                // SONAME resolution: a transportable string-reference
                // read (Result), not an assumption -- a malformed
                // offset refures the WHOLE load, mirroring how the
                // dynsym path refuses malformed names.
                t.soname = match t.soname_raw_offset {
                    Some(off) => match read_cstr_in_table(str_base, strsz, off) {
                        Ok(name) => Some(name),
                        Err(_) => {
                            return Err(ElfError::MalformedDynamicTables);
                        }
                    },
                    None => None,
                };
                // DT_NEEDED strings: same posture. A malformed offset
                // anywhere in the list is a hard refusal.
                for off in core::mem::take(&mut t.needed_raw_offsets) {
                    match read_cstr_in_table(str_base, strsz, off) {
                        Ok(name) => t.needed.push(name),
                        Err(_) => return Err(ElfError::MalformedDynamicTables),
                    }
                }
            } else {
                // String-offset entries present but NO strtab/STRSZ
                // to bound them: leave raw values uninterpreted
                // rather than trusting the mapping. Anything that
                // needs a name later sees the tagged refusal by its
                // own check.
            }
        }
        Ok(t)
    }

    /// The symbol count, from DT_HASH's nchain (the authoritative
    /// SysV definition: symbol table and chain table have the same
    /// length). Requires the DT_HASH table to be present, mapped, and
    /// sane; every failure is a typed refusal, never a guess.
    ///
    /// # Safety
    /// Conditions as `read_from_memory`.
    unsafe fn symbol_count(&self, load_bias: u64) -> Result<usize, ElfError> {
        let hash_vaddr = self.hash_vaddr.ok_or(ElfError::MalformedDynamicTables)?;
        // A sane upper bound: a symbol table entry (24 bytes) must fit in
        // the loadable image for each declared symbol, so more than this
        // many symbols is a malformed file (and 2^20 symbols at 24 bytes
        // each would already require 48 MiB of image).
        const MAX_SYMBOLS: u32 = 1 << 20;
        let hash_base = load_bias.wrapping_add(hash_vaddr);
        let nchain = core::ptr::read_unaligned((hash_base + 4) as *const u32);
        if nchain > MAX_SYMBOLS {
            return Err(ElfError::MalformedDynamicTables);
        }
        Ok(nchain as usize)
    }

    /// Reads symbol `idx`'s Elf64_Sym from the mapped DT_SYMTAB and,
    /// if `st_name` is nonzero, its name from DT_STRTAB (bounded by
    /// DT_STRSZ). Returns (name_bytes, st_value, st_shndx).
    ///
    /// # Safety
    /// Conditions as `read_from_memory`.
    unsafe fn symbol_at(&self, load_bias: u64, idx: usize) -> Result<(&'static [u8], u64, u16), ElfError> {
        let symtab_vaddr = self.symtab_vaddr.ok_or(ElfError::MalformedDynamicTables)?;
        let strtab_vaddr = self.strtab_vaddr.ok_or(ElfError::MalformedDynamicTables)?;
        if let Some(syment) = self.syment {
            if syment as usize != SYM_ENTRY_SIZE {
                return Err(ElfError::MalformedDynamicTables);
            }
        }
        let sym_base = load_bias.wrapping_add(symtab_vaddr);
        let entry = sym_base + (idx * SYM_ENTRY_SIZE) as u64;
        let (st_name, st_info, st_other, st_shndx, st_value, _st_size) = {
            let st_name = core::ptr::read_unaligned(entry as *const u32);
            let st_info = core::ptr::read_unaligned((entry + 4) as *const u8);
            let st_other = core::ptr::read_unaligned((entry + 5) as *const u8);
            let st_shndx = core::ptr::read_unaligned((entry + 6) as *const u16);
            let st_value = core::ptr::read_unaligned((entry + 8) as *const u64);
            let st_size = core::ptr::read_unaligned((entry + 16) as *const u64);
            (st_name, st_info, st_other, st_shndx, st_value, st_size)
        };
        let _ = (st_info, st_other);
        let name: &'static [u8] = if st_name == 0 {
            b""
        } else {
            let strsz = self.strsz.ok_or(ElfError::MalformedDynamicTables)? as usize;
            if (st_name as usize) >= strsz {
                return Err(ElfError::MalformedDynamicTables);
            }
            let name_base = load_bias.wrapping_add(strtab_vaddr);
            let start = name_base + st_name as u64;
            // strlen within the STRSZ bound: NUL must arrive before the
            // end of the string table, so the name cannot read past it.
            let max = strsz - (st_name as usize);
            let mut len = 0usize;
            while len < max {
                if core::ptr::read_unaligned((start + len as u64) as *const u8) == 0 {
                    break;
                }
                len += 1;
            }
            if len == max {
                return Err(ElfError::MalformedDynamicTables); // unterminated name
            }
            core::slice::from_raw_parts(start as *const u8, len)
        };
        Ok((name, st_value, st_shndx))
    }

    /// SysV ELF hash of a name (the function the SysV DT_HASH
    /// table's bucket/chain walk is keyed by, and the one the SysV
    /// table publishes directly).
    ///
    /// NOTE: this is NOT the DT_GNU_HASH hash -- GNU hash tables use
    /// a different, simpler multiplicative function ([gnu_hash]), so
    /// their bloom words, bucket indices, and chain values are keyed
    /// by THAT function, never this one.
    fn elf_hash(name: &[u8]) -> u32 {
        let mut h: u32 = 0;
        for &c in name {
            h = (h << 4).wrapping_add(c as u32);
            let g = h & 0xF000_0000;
            if g != 0 {
                h ^= g >> 24;
            }
            h &= !g;
        }
        h
    }

    /// The GNU hash of a name (the DT_GNU_HASH function): the
    /// multiplicative hash with initial value 5381 and multiplier 33
    /// (DJBX33A), taken over the name bytes. This -- not [elf_hash]
    /// -- is the function whose low bits pick the bloom word and bit,
    /// the bucket index, and whose value is stored in each chain
    /// entry (low bit set marks the bucket run's end). Hashing a name
    /// with anything else here yields a systematically wrong bucket
    /// and is indistinguishable from a fresh symbol.
    fn gnu_hash(name: &[u8]) -> u32 {
        let mut h: u32 = 5381;
        for &c in name {
            h = h.wrapping_mul(33).wrapping_add(c as u32);
        }
        h
    }

    /// Looks a symbol up by name via the SysV DT_HASH table (the
    /// bucket/chain walk -- the real lookup mechanism a dynamic linker
    /// uses for this table kind, not a linear scan), refusing
    /// malformed input and separating "parsed but absent" from
    /// "malformed". Returns the symbol's index on success.
    ///
    /// # Safety
    /// Conditions as `read_from_memory`, plus the DT_HASH table must
    /// be mapped (same PT_LOAD pass).
    unsafe fn lookup_hash_walk(&self, load_bias: u64, name: &[u8], sym_count: usize) -> Result<usize, ElfError> {
        let hash_vaddr = self.hash_vaddr.ok_or(ElfError::MalformedDynamicTables)?;
        let hash_base = load_bias.wrapping_add(hash_vaddr);
        let nbucket = core::ptr::read_unaligned(hash_base as *const u32) as usize;
        if nbucket == 0 {
            // A zero-bucket hash table is structurally absurd (and
            // would panic the modulo below); paranoia refuses it.
            return Err(ElfError::MalformedDynamicTables);
        }
        let nchain = sym_count;
        // buckets[nbucket], then chains[nchain], right after the header.
        let buckets_off = hash_base + 8;
        let chains_off = buckets_off + (nbucket * 4) as u64;
        let h = DynTables::elf_hash(name);
        let mut i = core::ptr::read_unaligned((buckets_off + ((h as usize % nbucket) * 4) as u64) as *const u32) as usize;
        while i != 0 {
            if i >= nchain {
                return Err(ElfError::MalformedDynamicTables);
            }
            let (sym_name, _st_value, _st_shndx) = self.symbol_at(load_bias, i)?;
            if sym_name == name {
                return Ok(i);
            }
            i = core::ptr::read_unaligned((chains_off + (i * 4) as u64) as *const u32) as usize;
        }
        Err(ElfError::SymbolNotFound)
    }

    /// Looks a symbol up by name through the GNU hash table
    /// (DT_GNU_HASH) -- the glibc-style mechanism: a per-symbol
    ///Elf-hash filter (the bloom table), then a bucket/chain walk
    /// (buckets address dynsym indices >= `symoffset`; the chain is
    /// indexed by `symbol_index - symoffset`, and a chain entry's low
    /// bit marks the end of a bucket's run; names are compared
    /// exactly). Bounds at every hop: a bad bucket, an out-of-table
    /// chain run, or a walk past the DT_HASH-bounded symbol count is
    /// a typed refusal, and the BLOOM MISS is typed as absent, not
    /// malformed.
    ///
    /// # Safety
    /// Conditions as `lookup_hash_walk` (the GNU table must be
    /// mapped, same PT_LOAD pass).
    unsafe fn lookup_gnu_hash(
        &self,
        load_bias: u64,
        name: &[u8],
        sym_count: usize,
    ) -> Result<usize, ElfError> {
        let gnu = self.gnu_hash_vaddr.ok_or(ElfError::MalformedDynamicTables)?;
        if sym_count == 0 {
            return Err(ElfError::SymbolNotFound); // an empty symbol table cannot carry any name
        }
        let base = load_bias.wrapping_add(gnu);
        let nbuckets = core::ptr::read_unaligned(base as *const u32) as usize;
        let symoffset = core::ptr::read_unaligned((base + 4) as *const u32) as usize;
        let bloom_size = core::ptr::read_unaligned((base + 8) as *const u32) as usize;
        let bloom_shift = core::ptr::read_unaligned((base + 12) as *const u32);
        if nbuckets == 0 || bloom_size == 0 {
            return Err(ElfError::MalformedDynamicTables);
        }
        let bloom_off = base + 16;
        let bucket_off = bloom_off.wrapping_add((bloom_size * 8) as u64);
        let chain_off = bucket_off.wrapping_add((nbuckets * 4) as u64);
        let h = DynTables::gnu_hash(name);
        // h itself and (when the table carries more than one word) one
        // from the c-chain spread (h >> bloom_shift). Both the h and
        // the bloom/bucket/chain values below are the GNU hash, NOT
        // the SysV one -- the two tables share neither the function
        // nor the meaning of their entries.
        let bloom_val = core::ptr::read_unaligned(
            (bloom_off + (((h as usize >> 6) % bloom_size) * 8) as u64) as *const u64,
        );
        let mut mask = 1u64 << (h % 64);
        if bloom_size > 1 {
            mask |= 1u64 << ((((h >> bloom_shift) as usize) % 64) as u64);
        }
        if (bloom_val & mask) != mask {
            return Err(ElfError::SymbolNotFound); // the filter excludes the name
        }
        let bucket = core::ptr::read_unaligned(
            (bucket_off + ((h as usize % nbuckets) * 4) as u64) as *const u32,
        ) as usize;
        if bucket == 0 {
            return Err(ElfError::SymbolNotFound); // empty bucket
        }
        if bucket < symoffset {
            return Err(ElfError::MalformedDynamicTables); // a bucket may never point below symoffset
        }
        let mut idx = bucket;
        loop {
            if idx >= sym_count {
                return Err(ElfError::MalformedDynamicTables); // the chain ran out of the (hash-bounded) table
            }
            let (sym_name, _st_value, _st_shndx) = self.symbol_at(load_bias, idx)?;
            if sym_name == name {
                return Ok(idx);
            }
            let chain_val = core::ptr::read_unaligned(
                (chain_off + ((idx - symoffset) * 4) as u64) as *const u32,
            ) as u32;
            if chain_val & 1 == 1 {
                return Err(ElfError::SymbolNotFound); // end-of-chain marker
            }
            idx += 1;
        }
    }

    /// The combined lookup the RESOLUTION paths use: the GNU table
    /// when the object carries one (GNU ld emits it alongside
    /// DT_HASH for these shared artifacts), the SysV table otherwise.
    /// Both are the SAME ELF hash function; the choice is always an
    /// explicit fact, never a silent cross-fallback when one table
    /// says no.
    unsafe fn lookup_hash_any(&self, load_bias: u64, name: &[u8], sym_count: usize) -> Result<usize, ElfError> {
        if self.gnu_hash_vaddr.is_some() {
            return self.lookup_gnu_hash(load_bias, name, sym_count);
        }
        self.lookup_hash_walk(load_bias, name, sym_count)
    }

    /// The required version for dynsym entry `idx`, read from this
    /// object's DT_VERSYM + DT_VERNEED surface: `Option((name, file))`
    /// -- the (name, file) halves of [`DynTables::required_version_info`]
    /// without the weak flag; kept as the exact shape callers matched
    /// before weak semantics existed.
    ///
    /// # Safety
    /// Conditions as `required_version_info` (all related tables
    /// mapped).
    unsafe fn required_version(
        &self,
        load_bias: u64,
        idx: usize,
    ) -> Result<Option<(&'static [u8], &'static [u8])>, ElfError> {
        Ok(match self.required_version_info(load_bias, idx)? {
            Some((v, f, _weak)) => Some((v, f)),
            None => None,
        })
    }

    /// The version name this object's dynsym entry `idx` (DEFINED)
    /// carries, from DT_VERSYM + DT_VERDEF: `None` = the entry is
    /// unversioned; `Some(name)` = the version string whose index the
    /// symbol's versym entry points at.
    /// # Safety
    /// Conditions as `read_from_memory` (all related tables mapped).
    unsafe fn provided_version(&self, load_bias: u64, idx: usize) -> Result<Option<&'static [u8]>, ElfError> {
        let (Some(versym), Some(verdef_base)) = (self.versym_vaddr, self.verdef_vaddr) else {
            return Ok(None);
        };
        let sym_count = self.symbol_count(load_bias)?;
        if idx >= sym_count {
            return Err(ElfError::MalformedDynamicTables);
        }
        let raw = core::ptr::read_unaligned((load_bias.wrapping_add(versym) + (idx * 2) as u64) as *const u16);
        let v = (raw & 0x7fff) as usize;
        if v <= 1 {
            return Ok(None);
        }
        let str_base = load_bias.wrapping_add(self.strtab_vaddr.ok_or(ElfError::MalformedDynamicTables)?);
        let strsz = self.strsz.ok_or(ElfError::MalformedDynamicTables)?;
        let mut off = 0u64;
        let count = self.verdef_num.unwrap_or(u64::MAX);
        for _ in 0..count {
            let base = load_bias.wrapping_add(verdef_base) + off;
            let vd_ndx = core::ptr::read_unaligned((base + 4) as *const u16) as usize;
            let vd_aux = core::ptr::read_unaligned((base + 12) as *const u32) as u64;
            let vd_next = core::ptr::read_unaligned((base + 16) as *const u32) as u64;
            if vd_ndx == v {
                // The FIRST aux entry carries the version name.
                let aux = base + vd_aux;
                let vda_name = core::ptr::read_unaligned((aux) as *const u32) as u64;
                return Ok(Some(read_cstr_in_table(str_base, strsz, vda_name)?));
            }
            if vd_next == 0 {
                break;
            }
            off += vd_next;
        }
        Err(ElfError::MalformedDynamicTables)
    }

    /// Like [`DynTables::required_version`] but ALSO reports whether
    /// the VERNEED aux entry carries `VER_FLG_WEAK` -- the gABI's
    /// "this requirement may go unsatisfied" bit. Callers implement
    /// the weak semantics: tolerated-nonfatal means slot 0, NEVER a
    /// guessed address.
    ///
    /// # Safety
    /// Conditions as `required_version` (all related tables mapped).
    unsafe fn required_version_info(
        &self,
        load_bias: u64,
        idx: usize,
    ) -> Result<Option<(&'static [u8], &'static [u8], bool)>, ElfError> {
        let (Some(versym), Some(verneed_base)) = (self.versym_vaddr, self.verneed_vaddr) else {
            return Ok(None); // no version surface at all: legacy object
        };
        let sym_count = self.symbol_count(load_bias)?;
        if idx >= sym_count {
            return Err(ElfError::MalformedDynamicTables);
        }
        let raw = core::ptr::read_unaligned((load_bias.wrapping_add(versym) + (idx * 2) as u64) as *const u16);
        let v = (raw & 0x7fff) as usize;
        if v <= 1 {
            return Ok(None); // 0 = local, 1 = global: unversioned requirement
        }
        let str_base = load_bias.wrapping_add(self.strtab_vaddr.ok_or(ElfError::MalformedDynamicTables)?);
        let strsz = self.strsz.ok_or(ElfError::MalformedDynamicTables)?;
        let mut off = 0u64;
        let count = self.verneed_num.unwrap_or(u64::MAX);
        for _ in 0..count {
            let base = load_bias.wrapping_add(verneed_base) + off;
            let vn_file = core::ptr::read_unaligned((base + 4) as *const u32);
            let vn_aux = core::ptr::read_unaligned((base + 8) as *const u32) as u64;
            let vn_next = core::ptr::read_unaligned((base + 12) as *const u32) as u64;
            let file = read_cstr_in_table(str_base, strsz, vn_file as u64)?;
            let mut aux_off = vn_aux;
            loop {
                if aux_off == 0 {
                    break;
                }
                let aux = base + aux_off;
                let vna_flags = core::ptr::read_unaligned((aux + 4) as *const u16);
                let vna_other = core::ptr::read_unaligned((aux + 6) as *const u16) as usize;
                let vna_name = core::ptr::read_unaligned((aux + 8) as *const u32) as u64;
                let vna_next = core::ptr::read_unaligned((aux + 12) as *const u32) as u64;
                if (vna_other & 0x7fff) == v {
                    let name_bytes = read_cstr_in_table(str_base, strsz, vna_name)?;
                    let weak = (vna_flags & VER_FLG_WEAK) != 0;
                    return Ok(Some((name_bytes, file, weak)));
                }
                aux_off = vna_next;
            }
            if vn_next == 0 {
                break;
            }
            off += vn_next;
        }
        Err(ElfError::MalformedDynamicTables)
    }

    /// `(name, st_value, st_shndx, st_info)` for dynsym entry `idx`:
    /// [`DynTables::symbol_at`] plus the binding (`st_info&0xf`, mun
    /// where `st_info >> STB_BIND_SHIFT == STB_WEAK` decides the
    /// weak-symbol semantics in the relocation pass) and keeping the
    /// value together with the definedness.
    ///
    /// # Safety
    /// Conditions as `symbol_at`. The caller must bounds-check `idx`
    /// against the symbol count first.
    unsafe fn symbol_at_info(
        &self,
        load_bias: u64,
        idx: usize,
    ) -> Result<(&'static [u8], u64, u16, u8), ElfError> {
        let symtab_vaddr = self.symtab_vaddr.ok_or(ElfError::MalformedDynamicTables)?;
        let sym_base = load_bias.wrapping_add(symtab_vaddr);
        let entry = sym_base + (idx * SYM_ENTRY_SIZE) as u64;
        let st_info = core::ptr::read_unaligned((entry + 4) as *const u8);
        let (name, st_value, st_shndx) = self.symbol_at(load_bias, idx)?;
        Ok((name, st_value, st_shndx, st_info))
    }
}

/// What the loader did with a `PT_GNU_RELRO` segment. Returned so a
/// caller (and the boot transcript) can see the region's final
/// protection state as a fact, not an assumption -- and so a caller
/// can structurally re-verify it against the live page tables, which
/// `experimental::pie_demo` does every boot.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RelroOutcome {
    /// The RELRO region existed, every relocation affecting it was a
    /// `R_X86_64_RELATIVE` (the only kind this loader can satisfy),
    /// all were applied, and the region's pages were then revoked to
    /// read-only. `start`/`end` are runtime (bias-adjusted) byte
    /// addresses -- `end` exclusive.
    MadeReadOnly { start: u64, end: u64 },
    /// The RELRO region contained at least one relocation this loader
    /// cannot satisfy (an unsupported symbol-resolved type, a
    /// symbol-resolved entry against a genuinely UNDEFINED symbol
    /// needing cross-object resolution, or malformed dynamic tables),
    /// so the region was NOT revoked -- leaving it writable is the
    /// honest state here and is *reported*, not hidden: a caller can
    /// refuse the binary outright or proceed knowingly.
    /// `start`/`end` as above.
    Denied { start: u64, end: u64 },
}

pub struct LoadedElf {
    pub entry_point: VirtAddr,
    /// `Some` iff the file carried a `PT_GNU_RELRO` segment; see
    /// [`RelroOutcome`] for the two possible states.
    pub relro: Option<RelroOutcome>,
    /// `Some` iff the object's OWN linker-emitted `.plt`/`.got.plt`
    /// was armed for the GNU dynamic-linker lazy protocol (the caller
    /// supplied `LoadContext::own_plt_resolver_page`). The returned
    /// facts are exactly what the caller must feed
    /// `lazy_binder::register_lm` BEFORE the object's first PLT call.
    pub own_plt: Option<OwnPltArming>,
}

/// What `elf::load` wrote into an object's OWN `.got.plt` to arm the
/// GNU dynamic-linker lazy-binding protocol.
#[derive(Debug, Clone, Copy)]
pub struct OwnPltArming {
    /// Runtime (bias-added) address of the object's `.got.plt[0]`.
    pub pltgot: u64,
    /// The per-object link-map id written into GOT[1] -- the registry
    /// key `lazy_binder` dispatches on. This kernel's integer-id
    /// equivalent of the GNU `struct link_map*` cookie: the resolver
    /// deliberately does NOT dereference (or ever be handed) a
    /// user-writable kernel pointer.
    pub lm_id: u64,
}

/// Reads a little-endian integer field out of `data` at `offset`,
/// bounds-checked. Written by hand (not via a crate) to keep this
/// module's only dependency on external code being the paging/frame
/// allocator plumbing it already needs -- parsing a handful of small
/// integer fields doesn't justify a new dependency.
fn read_u16(data: &[u8], offset: usize) -> Option<u16> {
    data.get(offset..offset + 2).map(|b| u16::from_le_bytes([b[0], b[1]]))
}
fn read_u32(data: &[u8], offset: usize) -> Option<u32> {
    data.get(offset..offset + 4).map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]))
}
fn read_u64(data: &[u8], offset: usize) -> Option<u64> {
    data.get(offset..offset + 8)
        .map(|b| u64::from_le_bytes([b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7]]))
}

/// Reads a NUL-terminated byte string starting at `str_base + off`,
/// bounded by the string-table size `strsz` (the same discipline the
/// dynsym name reader uses: the NUL must arrive before the bound, so
/// nothing reads past the table). `off` beyond the bound is a typed
/// refusal.
///
/// # Safety
/// `str_base .. + strsz` must lie inside PT_LOAD-mapped, populated
/// memory (the same condition as every other dyn-table read).
unsafe fn read_cstr_in_table(str_base: u64, strsz: u64, off: u64) -> Result<&'static [u8], ElfError> {
    if off >= strsz {
        return Err(ElfError::MalformedDynamicTables);
    }
    let start = str_base + off;
    let max = (strsz - off) as usize;
    let mut len = 0usize;
    while len < max {
        if core::ptr::read_unaligned((start + len as u64) as *const u8) == 0 {
            break;
        }
        len += 1;
    }
    if len == max {
        return Err(ElfError::MalformedDynamicTables); // unterminated
    }
    Ok(core::slice::from_raw_parts(start as *const u8, len))
}

struct ProgramHeader {
    p_type: u32,
    p_flags: u32,
    p_offset: u64,
    p_vaddr: u64,
    p_filesz: u64,
    p_memsz: u64,
}

fn parse_program_header(data: &[u8], off: usize) -> Option<ProgramHeader> {
    Some(ProgramHeader {
        p_type: read_u32(data, off)?,
        p_flags: read_u32(data, off + 4)?,
        p_offset: read_u64(data, off + 8)?,
        p_vaddr: read_u64(data, off + 16)?,
        // p_paddr at off+24, unused by this loader
        p_filesz: read_u64(data, off + 32)?,
        p_memsz: read_u64(data, off + 40)?,
        // p_align at off+48, unused (this loader always page-aligns)
    })
}

/// Parses `data` as an ELF64 executable, maps every `PT_LOAD` segment
/// at `load_bias + p_vaddr` (zero-filled first, then the file's bytes
/// copied on top for the `p_filesz` portion -- so `p_memsz > p_filesz`,
/// i.e. an implicit `.bss`, is handled correctly rather than left as
/// stale/garbage physical-frame content), applies any `R_X86_64_RELATIVE`
/// relocations described by a `PT_DYNAMIC` segment (see the module doc
/// for why that's the only relocation type supported), and returns
/// `load_bias + e_entry`. Does not jump anywhere -- that's the
/// caller's job, same separation as `usermode::prepare` vs
/// `usermode::enter`.
///
/// `load_bias` MUST be `0` for an `ET_EXEC` file (its addresses are
/// fixed at link time; any other bias would be wrong, not just
/// unusual -- enforced with an error, not just documented). For
/// `ET_DYN`, `load_bias` is the real runtime base address the caller
/// has chosen (typically wherever it mapped this process's address
/// space to place code); the loader adds it everywhere the file's
/// own addresses are used.
/// Everything a caller supplies alongside the file itself: the
/// resolver scope for undefined symbols, and (optionally) a page the
/// loader may map executable trampolines into for LAZY JUMP_SLOT
/// binding (a resolver without a lazy page still works -- PLT entries
/// stay eager, the pre-existing behavior).
pub struct LoadContext<'a> {
    pub resolver: Option<&'a dyn SymbolResolver>,
    pub lazy_plt_page: Option<VirtAddr>,
    /// When set (and a resolver is present), the loader leaves its
    /// synthetic `int 0x88` trampolines OFF and instead arms the
    /// object's OWN linker-emitted `.plt`/`.got.plt` for the GNU
    /// dynamic-linker lazy protocol: GOT[1] gets a per-object
    /// link-map id, GOT[2] gets the user-callable resolver thunk,
    /// and every JUMP_SLOT slot gets the GNU lazy relocation
    /// applied as `slot += load_bias` over the linker's own baked
    /// initial `plt_stub+6` value (the file bytes carry pre-bias,
    /// the real dynamic linker's exact runtime state). Requires
    /// DT_PLTGOT + DT_JMPREL in the file; anything else is a typed
    /// refusal.
    pub own_plt_resolver_page: Option<VirtAddr>,
}

impl<'a> LoadContext<'a> {
    /// The pre-cross-object context (no resolver, no lazy page).
    pub fn none() -> Self {
        LoadContext {
            resolver: None,
            lazy_plt_page: None,
            own_plt_resolver_page: None,
        }
    }
}

pub fn load(
    data: &[u8],
    mapper: &mut OffsetPageTable<'static>,
    frame_allocator: &mut BumpFrameAllocator,
    load_bias: u64,
    ctx: &LoadContext,
) -> Result<LoadedElf, ElfError> {
    if data.len() < EHDR_SIZE {
        return Err(ElfError::TooShort);
    }
    if data[0..4] != ELF_MAGIC {
        return Err(ElfError::BadMagic);
    }
    if data[4] != ELFCLASS64 {
        return Err(ElfError::Not64Bit);
    }
    if data[5] != ELFDATA2LSB {
        return Err(ElfError::NotLittleEndian);
    }
    let e_type = read_u16(data, 16).ok_or(ElfError::TooShort)?;
    if e_type != ET_EXEC && e_type != ET_DYN {
        return Err(ElfError::UnsupportedType);
    }
    if e_type == ET_EXEC && load_bias != 0 {
        return Err(ElfError::NonZeroBiasOnFixedAddressExecutable);
    }
    // e_entry is read again below, AFTER relocations; reading it here
    // first validates the header before any mapping work happens.
    read_u64(data, 24).ok_or(ElfError::TooShort)?;
    let e_phoff = read_u64(data, 32).ok_or(ElfError::TooShort)? as usize;
    let e_phentsize = read_u16(data, 54).ok_or(ElfError::TooShort)? as usize;
    let e_phnum = read_u16(data, 56).ok_or(ElfError::TooShort)? as usize;

    if e_phentsize < PHDR_SIZE {
        return Err(ElfError::ProgramHeaderOutOfBounds);
    }

    let mut load_segments_found = 0u32;
    // Read-only segments mapped writable for the load itself, owing the
    // revoke (see the grant note in the mapping below): (seg_start,
    // page_count) pairs, revoked read-only after every segment is in
    // place.
    let mut ro_segments_to_revoke: alloc::vec::Vec<(u64, u64)> = alloc::vec::Vec::new();
    let mut dynamic_segment: Option<(u64, u64)> = None; // (p_vaddr, p_filesz), pre-bias
    let mut relro_segment: Option<(u64, u64)> = None; // (p_vaddr, p_memsz), pre-bias
    let mut dt = DynTables::new();

    for i in 0..e_phnum {
        let off = e_phoff + i * e_phentsize;
        if off + PHDR_SIZE > data.len() {
            return Err(ElfError::ProgramHeaderOutOfBounds);
        }
        let ph = parse_program_header(data, off).ok_or(ElfError::ProgramHeaderOutOfBounds)?;

        if ph.p_type == PT_DYNAMIC {
            dynamic_segment = Some((ph.p_vaddr, ph.p_filesz));
            continue;
        }
        if ph.p_type == PT_GNU_RELRO {
            relro_segment = Some((ph.p_vaddr, ph.p_memsz));
            continue;
        }
        if ph.p_type != PT_LOAD {
            continue;
        }
        load_segments_found += 1;

        let file_start = ph.p_offset as usize;
        let file_end = file_start
            .checked_add(ph.p_filesz as usize)
            .ok_or(ElfError::SegmentDataOutOfBounds)?;
        if file_end > data.len() {
            return Err(ElfError::SegmentDataOutOfBounds);
        }
        if ph.p_memsz < ph.p_filesz {
            return Err(ElfError::SegmentDataOutOfBounds); // nonsensical: file data larger than the memory reserved for it
        }

        let runtime_vaddr = load_bias.wrapping_add(ph.p_vaddr);

        // Page-align the mapping to cover [runtime_vaddr, runtime_vaddr + p_memsz),
        // which may extend before/after the exact requested range.
        let seg_start = VirtAddr::new(runtime_vaddr).align_down(4096u64);
        let seg_end_addr = runtime_vaddr + ph.p_memsz;
        let seg_end = VirtAddr::new(seg_end_addr).align_up(4096u64);
        let page_count = (seg_end.as_u64() - seg_start.as_u64()) / 4096;

        let mut flags = PageTableFlags::PRESENT | PageTableFlags::USER_ACCESSIBLE;
        // Writable-while-loading: the loader itself zero-fills and copies
        // every mapped page (below), INCLUDING pages of segments the file
        // declared read-only, so the mapping must be writable for the
        // duration of the load. Relying on CR0.WP == 0 masking supervisor
        // writes (the legacy BIOS state boot.s leaves) is NOT a load
        // contract: boot firms like OVMF hand over with CR0.WP == 1, and
        // this write then faults as PROTECTION_VIOLATION. The file-
        // requested final permission set is restored after the copy pass
        // (the revoke below), which is also exactly what `page_flags_of`
        // observers and the RELRO grant/revoke machinery see.
        let writable_requested = ph.p_flags & PF_W != 0;
        flags |= PageTableFlags::WRITABLE;
        // Real W^X enforcement: a segment without PF_X gets
        // NO_EXECUTE, meaning the CPU will refuse to fetch
        // instructions from it once EFER.NXE is enabled (see
        // memory::paging::enable_nx, called before any ELF is loaded).
        // Verified with an actual positive-control test, not just
        // asserted -- see IMPLEMENTATION_STATUS.md for the transcript
        // of a deliberate data-page execution attempt correctly
        // page-faulting with an instruction-fetch error.
        if ph.p_flags & PF_X == 0 {
            flags |= PageTableFlags::NO_EXECUTE;
        }
        // A PT_DYNAMIC-bearing binary's relocations write into this
        // segment after it's mapped (see below) -- if PF_W is absent
        // (the segment is nominally read-only, e.g. under
        // GNU_RELRO-style layouts), the RELRO pass below temporarily
        // grants the write itself, relocates, then revokes to
        // read-only; the mapping here stays as the file requested.
        // Similarly, an *executable* GNU_RELRO region (some linkers
        // emit PLT-containing relro as PF_R|PF_X) would otherwise make
        // the revoke pass silently widen permissions -- so the revoke
        // below only ever *removes* writability from flags the
        // segment already carries.

        if page_count > 0 {
            let mapped = paging::map_range(mapper, frame_allocator, seg_start, page_count, flags);
            if mapped != page_count {
                return Err(ElfError::MappingFailed);
            }
            if !writable_requested {
                ro_segments_to_revoke.push((seg_start.as_u64(), page_count));
            }

            unsafe {
                // Zero the whole mapped range first -- this is what makes
                // p_memsz > p_filesz (an implicit .bss) correct rather
                // than leaving stale physical-frame content in the gap.
                core::ptr::write_bytes(seg_start.as_mut_ptr::<u8>(), 0, page_count as usize * 4096);
                // Then copy the file's actual bytes for this segment on top.
                let dest = VirtAddr::new(runtime_vaddr).as_mut_ptr::<u8>();
                core::ptr::copy_nonoverlapping(data.as_ptr().add(file_start), dest, ph.p_filesz as usize);
            }
        }
    }

    if load_segments_found == 0 {
        return Err(ElfError::NoLoadSegments);
    }

    // Revoke the load-time write grant now that every segment is placed:
    // pages of segments the file declared read-only go back exactly to
    // the pre-grant flag set. Runs BEFORE relocations, whose own RELRO
    // grant/revoke cycle then handles any region that needs write
    // access for GOT/pointer updates. The early return keeps a failed
    // revoke a hard load failure rather than a silently-wider image.
    for (seg_start, page_count) in ro_segments_to_revoke.iter() {
        if !update_page_flags_range(mapper, *seg_start, *page_count, |f| f & !PageTableFlags::WRITABLE) {
            return Err(ElfError::SegmentProtectFailed);
        }
    }

    // One parse of the (now-mapped) PT_DYNAMIC segment, shared by the
    // RELRO planner and the relocation pass -- a single walk of the
    // table instead of two, and the same data both consumers see.
    if let Some((dyn_vaddr, dyn_filesz)) = dynamic_segment {
        dt = unsafe { DynTables::read_from_memory(load_bias, dyn_vaddr, dyn_filesz) }?;
    }

    // The declared dependency CONTRACT: every DT_NEEDED SONAME must
    // be satisfiable by the caller's resolver scope BEFORE any
    // relocation work -- the loader never starts relocating a file
    // whose declared dependencies do not exist in scope. (Registry
    // resolvers compare real DT_SONAME bytes; the trait's default
    // explicitly declares itself outside the contract rather than
    // silently refusing.
    if let Some(resolver) = ctx.resolver {
        for needed in &dt.needed {
            if !resolver.has_soname(needed)? {
                return Err(ElfError::MissingDependency);
            }
        }
    }

    // JUMP_SLOT entries bind eagerly (the pre-lazy behavior) EXCEPT
    // when a lazy page was supplied with a resolver (either lazy mode:
    // the synthetic `int 0x88` trampolines, or the object's own GNU
    // `.plt` protocol) -- in which case the install pass below owns
    // those slots; eager writes and lazy arming must never race the
    // same slot.
    let own_plt_armed = ctx.resolver.is_some() && ctx.own_plt_resolver_page.is_some();
    let lazy_plt_armed = ctx.resolver.is_some()
        && (ctx.lazy_plt_page.is_some() || own_plt_armed);
    // The OWN-.plt protocol, validated UP FRONT (before any relocation
    // work) so a caller asking for it against a file that cannot carry
    // it is refused with a typed error, exactly like the dependency
    // contract above: the file must declare its own DT_PLTGOT base and
    // a PLT relocation table, and a declared PLTREL must be DT_RELA
    // (the only PLT-entry shape the resolver understands), and the
    // relocation count must be > 0 (a PLT size of zero with no entries
    // is a malformed declaration).
    if own_plt_armed {
        if dt.pltgot_vaddr.is_none()
            || dt.jmprel_vaddr.is_none()
            || dt.plt_relsz.is_none()
        {
            return Err(ElfError::MalformedDynamicTables);
        }
        if let Some(plt_rel) = dt.plt_rel {
            if plt_rel != (DT_RELA as u64) {
                return Err(ElfError::UnsupportedRelocationType);
            }
        }
        if (dt.plt_relsz.unwrap() as usize) / RELA_ENTRY_SIZE == 0 {
            return Err(ElfError::MalformedDynamicTables); // a size with no entries: malformed
        }
    }

    // The load happens in a strict order, and only inside this scope
    // does the RELRO region hold caller-granted write permission:
    //  1. map + populate every PT_LOAD segment (above),
    //  2. grant W on the GNU_RELRO pages *only if* relocations exist
    //     whose targets land in the region,
    //  3. apply the relocations,
    //  4. revoke W, restoring exactly the file-requested flag set the
    //     mapping pass laid down (it never adds W to a read-only
    //     segment).
    // If step 3 fails (an unsupported relocation type / entry size),
    // the region is left writable and `LoadedElf::relro` reports
    // `RelroOutcome::Denied` -- the honest state, surfaced to the
    // caller rather than silently kept or silently unchanged.
    // Declared before the RELRO match only so the own-.plt arming (a
    // mutable borrow of the load's own mapper/fa) can store what it
    // wrote into either arming site (the in-RELRO one below, or the
    // writable-segment one for files without a GNU_RELRO region).
    let mut own_plt_result: Option<OwnPltArming> = None;
    let relro_outcome: Option<RelroOutcome> = match relro_segment {
        Some((relro_vaddr, relro_memsz)) => {
            let relro_start_v = load_bias.wrapping_add(relro_vaddr);
            // GNU ld computes the memory size already page-aligned;
            // be exact for the denial-vs-revocation decision (which
            // relocation targets count as "inside" the region), then
            // extend to whole pages for the actual permission work.
            let relro_end_v = relro_start_v.wrapping_add(relro_memsz);
            let page_start = VirtAddr::new(relro_start_v).align_down(4096u64).as_u64();
            let page_end = VirtAddr::new(relro_end_v).align_up(4096u64).as_u64();
            let relro_pages = (page_end - page_start) / 4096;

            let relocations_planned =
                unsafe { relro_outcome_planned(&dt, load_bias, relro_start_v, relro_end_v, ctx.resolver) };
            // The own-.plt arming writes INSIDE the GNU_RELRO region
            // (the linker places .got.plt's first three slots inside
            // it), so the grant window opens for the arming too when
            // it is armed -- the same order GNU ld uses (resolver
            // pointers written pre-revoke, protected afterward).
            if relocations_planned.some || own_plt_armed {
                // Grant write for the relocation pass only. This is
                // the loader's own request, deliberately distinct
                // (WRITABLE only, the rest untouched) from the file's.
                if !update_page_flags_range(mapper, page_start, relro_pages, |mut f| {
                    f.insert(PageTableFlags::WRITABLE);
                    f
                }) {
                    return Err(ElfError::RelroPermissionUpdateFailed);
                }
            }

            let apply_result = if dt.has_dynamic {
                unsafe { apply_relocations(&dt, load_bias, ctx.resolver, lazy_plt_armed) }
            } else {
                Ok(())
            };
            match apply_result {
                Ok(()) => {
                    // The own-.plt arming runs INSIDE the grant window
                    // (GOT[1]/GOT[2] sit inside GNU_RELRO -- the same
                    // order GNU ld writes its resolver pointers), and
                    // must NOT run when the relocations were REFUSED
                    // (the `Err` arm below reports the denial; a load
                    // whose contract failed leaves its GOT untouched).
                    if own_plt_armed {
                        own_plt_result = Some(unsafe {
                            arm_own_plt(
                                &dt,
                                load_bias,
                                ctx.own_plt_resolver_page.unwrap(),
                                mapper,
                                frame_allocator,
                            )?
                        });
                    }
                    // Revoke: strip WRITABLE, keep everything else
                    // (including NO_EXECUTE and USER_ACCESSIBLE)
                    // exactly as mapped. This runs UNCONDITIONALLY on
                    // the success path: a GNU_RELRO region often
                    // overlaps a writable PT_LOAD segment (here it sits
                    // inside the RW data segment), so "no in-region
                    // relocations" does NOT imply "already read-only"
                    // -- skipping the revoke for that case would leave
                    // the dynamic segment writable while the loader
                    // still reported it protected (exactly the silent
                    // lie this increment exists to eliminate). A revoke
                    // on an already-read-only page is a harmless
                    // no-op.
                    if !update_page_flags_range(mapper, page_start, relro_pages, |f| {
                        f & !PageTableFlags::WRITABLE
                    }) {
                        return Err(ElfError::RelroPermissionUpdateFailed);
                    }
                    Some(RelroOutcome::MadeReadOnly { start: relro_start_v, end: relro_end_v })
                }
                Err(e) => match e {
                    // The failures the RELRO decision logic can
                    // produce: something in the dynamic segment is
                    // deliberately unrepresentable here (unsupported
                    // types; symbol-resolved against a genuinely
                    // undefined symbol; malformed symbol tables).
                    // Leave the region writable and deny (see
                    // RelroOutcome) -- the honest state for a region
                    // whose relocations cannot all be satisfied.
                    ElfError::UnsupportedRelocationType
                    | ElfError::UnsupportedRelocationEntrySize
                    | ElfError::UndefinedSymbol
                    | ElfError::VersionNotProvided
                    | ElfError::MalformedDynamicTables => {
                        Some(RelroOutcome::Denied { start: relro_start_v, end: relro_end_v })
                    }
                    other => return Err(other),
                },
            }
        }
        None => None,
    };

    // No GNU_RELRO region: the object's .got.plt lives in a simply-
    // writable PT_LOAD segment (mapped W by the segment pass), so the
    // arming needs no grant window at all.
    if own_plt_armed && own_plt_result.is_none() {
        own_plt_result = Some(unsafe {
            arm_own_plt(
                &dt,
                load_bias,
                ctx.own_plt_resolver_page.unwrap(),
                mapper,
                frame_allocator,
            )?
        });
    }

    // ---- lazy JUMP_SLOT binding (bind-on-first-use) ----
    // Armed only when the caller supplied BOTH a resolver and a
    // trampoline page. Each .rela.plt entry's GOT slot is filled with
    // the address of a per-entry stub -- `push idx; int 0x88; add
    // rsp,8; jmp *(slot)` -- mapped executable/user in THIS load's
    // page tables; the first call through the slot traps into
    // `elf::resolve_lazy_entry` (via the demo's IDT handler), the
    // slot is patched with the resolved address, and every later call
    // takes the direct path. A resolver WITHOUT a lazy page stays on
    // the eager path above. Unresolved-forever honesty is unchanged:
    // the trap-time resolution goes through the same resolver +
    // version contract, so what cannot resolve at load cannot wait
    // one call to discover it also cannot resolve at first use.
    if lazy_plt_armed && !own_plt_armed {
        if let (Some(jmprel), Some(plt_relsz)) = (dt.jmprel_vaddr, dt.plt_relsz) {
            if let Some(plt_rel) = dt.plt_rel {
                if plt_rel != (DT_RELA as u64) {
                    return Err(ElfError::UnsupportedRelocationType);
                }
            }
            let plt_base = load_bias.wrapping_add(jmprel);
            let plt_count = (plt_relsz as usize) / RELA_ENTRY_SIZE;
            if plt_count == 0 {
                return Err(ElfError::MalformedDynamicTables); // a size with no entries: malformed
            }
            if plt_count > 127 {
                return Err(ElfError::UnsupportedRelocationEntrySize); // stub's `push imm8` index limit
            }
            if plt_count * 16 > 4096 {
                return Err(ElfError::UnsupportedRelocationEntrySize); // trampoline page capacity
            }
            let page = ctx.lazy_plt_page.unwrap();
            // Executable + user-accessible, non-writable *at rest*: emitted
            // before any user code can run, never rewritten after. The
            // mapping carries the load-time WRITABLE grant (the emit
            // pass below writes every stub byte) -- same reasoning as the
            // PT_LOAD grant/revoke above: WP is NOT zero when a boot firm
            // like OVMF handed over, so a read-only mapping cannot be
            // kernel-written. The grant is revoked right after the emit
            // loop, before anything can execute.
            let stub_flags = PageTableFlags::PRESENT
                | PageTableFlags::USER_ACCESSIBLE
                | PageTableFlags::WRITABLE;
            if paging::map_range(mapper, frame_allocator, page, 1, stub_flags) != 1 {
                return Err(ElfError::MappingFailed);
            }
            for i in 0..plt_count {
                let entry_addr = plt_base + (i * RELA_ENTRY_SIZE) as u64;
                let r_offset = unsafe { core::ptr::read_unaligned(entry_addr as *const u64) };
                let slot = load_bias.wrapping_add(r_offset);
                let stub = page.as_u64() + (i * 16) as u64;
                // rel32 in the jmp lands at rip = stub + 14 (2 push +
                // 2 int + 4 add + 6 jmp): target slot == stub + 14 +
                // rel32. The trampoline page's address is chosen by the
                // frame allocator and can sit BELOW the image's GOT
                // slot, so the difference can genuinely be negative --
                // wrap at the u64 level (the low 4 bytes then carry the
                // correct i32 displacement, exactly what `FF 25`
                // consumes).
                let rel32 = slot.wrapping_sub(stub + 14) as i64;
                let rb = rel32.to_le_bytes();
                let bytes: [u8; 16] = [
                    0x6A, i as u8, // push imm8 idx (the trap handler reads it off the user stack)
                    0xCD, 0x88, // int 0x88 -- the lazy-bind trap
                    0x48, 0x83, 0xC4, 0x08, // add rsp, 8 (drop the pushed idx)
                    0xFF, 0x25, // jmp *(rip-rel32) -> the slot; direct path from then on
                    rb[0],
                    rb[1],
                    rb[2],
                    rb[3],
                    0x00, 0x00, // pad to the 16-byte stride
                ];
                for (k, b) in bytes.iter().enumerate() {
                    // SAFETY: the freshly mapped trampoline page is
                    // kernel-writable until user execution resumes (the
                    // same window the segment-mapping pass uses).
                    unsafe {
                        core::ptr::write_volatile((stub + k as u64) as *mut u8, *b);
                    }
                }
                // SAFETY: as above -- the slot's page was mapped RW by
                // this same load and stays kernel-addressable here.
                unsafe {
                    core::ptr::write_volatile(slot as *mut u64, stub);
                }
            }
            // Revoke the emit-window grant: byte-for-byte the designed
            // final state (read-only + executable) before user code can
            // ever see this page.
            if !update_page_flags_range(mapper, page.as_u64(), 1, |f| f & !PageTableFlags::WRITABLE) {
                return Err(ElfError::SegmentProtectFailed);
            }
        }
    }

    let e_entry_final = read_u64(data, 24).ok_or(ElfError::TooShort)?;

    Ok(LoadedElf {
        entry_point: VirtAddr::new(load_bias.wrapping_add(e_entry_final)),
        relro: relro_outcome,
        own_plt: own_plt_result,
    })
}

/// Arms the GNU dynamic-linker lazy-binding protocol on the object's
/// OWN linker-emitted `.plt`/`.got.plt`:
///
/// * GOT[1] := a per-object link-map id (the integer this kernel's
///   registry dispatches on -- the honest analogy to the GNU
///   `struct link_map*` cookie: the resolver deliberately never
///   dereferences, or is ever handed through writable user data, a
///   kernel pointer),
/// * GOT[2] := the loader-mapped, user-executable resolver thunk
///   (`int 0x8B` entered from ring 3, then a self-hang pad that is
///   unreachable on the success path -- the resolver returns not to
///   the thunk but DIRECTLY into the resolved callee, guided return
///   in `lazy_binder::plt_service`),
/// * every JUMP_SLOT slot gets the GNU LAZY relocation applied the
///   way a real dynamic linker does (`elf_machine_lazy_rel`): the
///   file's own initial content -- the PLT stub's push-half address
///   (plt_stub+6), baked into the file image by the linker -- is
///   ADJUSTED BY THE LOAD BIAS and written back, nothing more. The
///   solver's per-entry link-time arithmetic is unchanged: the file
///   bytes carry the pre-bias state, the runtime slots carry the
///   live state.
///
/// MUST run while the GNU_RELRO pages (which hold GOT[1]/GOT[2]) are
/// still granted writable -- GNU ld writes its resolver pointers
/// pre-revoke for exactly that reason; the no-RELRO case simply has
/// a writable .got.plt in an RW PT_LOAD segment. The thunk page is
/// caller-chosen and MUST be disjoint from every PT_LOAD mapping of
/// the object (CI-gated, see tools/verify_prog_layout.sh).
///
/// # Safety
/// The object is already mapped in the ACTIVE space; `mapper`/`fa`
/// are the same load's own plumbing.
unsafe fn arm_own_plt(
    dt: &DynTables,
    load_bias: u64,
    thunk_page: VirtAddr,
    mapper: &mut OffsetPageTable<'static>,
    frame_allocator: &mut BumpFrameAllocator,
) -> Result<OwnPltArming, ElfError> {
    let pltgot_vaddr = dt.pltgot_vaddr.ok_or(ElfError::MalformedDynamicTables)?;
    let pltgot = load_bias.wrapping_add(pltgot_vaddr);
    // Executable + user-accessible, non-writable *at rest*: written by
    // this pass only, before any user code can run -- mirrors the synthetic
    // trampoline page's exact discipline. WRITABLE is part of the mapping
    // for the emit window below only (the revoke lands right after the
    // 4 stub bytes; the same WP-not-zero reasoning applies).
    let thunk_flags = PageTableFlags::PRESENT
        | PageTableFlags::USER_ACCESSIBLE
        | PageTableFlags::WRITABLE;
    if paging::map_range(mapper, frame_allocator, thunk_page, 1, thunk_flags) != 1 {
        return Err(ElfError::MappingFailed);
    }
    let thunk = thunk_page.as_u64();
    // `int 0x8B` = CD 8B (the raw `int` here enters the ring-3-allowed
    // PLT-resolve gate with the GNU spill already on the user stack);
    // `jmp self` (EB FE) hangs loudly should any stray flow ever land
    // after the trap, instead of executing page-fill garbage.
    let bytes: [u8; 4] = [0xCD, PLT_RESOLVE_VECTOR, 0xEB, 0xFE];
    for (k, b) in bytes.iter().enumerate() {
        // SAFETY: the freshly mapped thunk page is kernel-writable
        // until user execution resumes (the same window the segment
        // mapping pass uses).
        unsafe {
            core::ptr::write_volatile((thunk + k as u64) as *mut u8, *b);
        }
    }
    // Revoke the emit-window grant immediately: the thunk page's final
    // state is read-only + executable, byte-for-byte the design.
    if !update_page_flags_range(mapper, thunk, 1, |f| f & !PageTableFlags::WRITABLE) {
        return Err(ElfError::SegmentProtectFailed);
    }
    let lm_id = crate::experimental::lazy_binder::allocate_lm_id();
    // SAFETY: .got.plt is mapped RW in the active space on both arming
    // paths: granted-writable via the RELRO pass while its first three
    // slots are inside GNU_RELRO, or simply RW-by-segment-flags when
    // the object has no GNU_RELRO region.
    unsafe {
        core::ptr::write_volatile((pltgot + 8) as *mut u64, lm_id);
        core::ptr::write_volatile((pltgot + 16) as *mut u64, thunk);
    }
    // The GNU lazy JUMP_SLOT relocation, applied verbatim: each entry
    // of the object's DT_JMPREL table is a slot whose FILE content is
    // the linker-baked pre-bias initial value (the matching .plt
    // stub's push-half); a live runtime value needs only the bias
    // added -- exactly what `elf_machine_lazy_rel` does for a lazy-
    // bound JUMP_SLOT (no resolver-side resolution happens here; the
    // slot stays a PLT-entry address until the resolver patches it).
    if let (Some(jmprel), Some(plt_relsz)) = (dt.jmprel_vaddr, dt.plt_relsz) {
        let plt_base = load_bias.wrapping_add(jmprel);
        let count = (plt_relsz as usize) / RELA_ENTRY_SIZE;
        for i in 0..count {
            let entry_addr = plt_base + (i * RELA_ENTRY_SIZE) as u64;
            // SAFETY: the .rela.plt table was mapped and populated by
            // the segment pass (the same condition every dyn-table
            // read relies on); the slot's page is RW on both arming
            // paths (JUMP_SLOT slots sit outside GNU_RELRO by linker
            // convention; the segment pass mapped their page W).
            unsafe {
                let r_offset = core::ptr::read_unaligned(entry_addr as *const u64);
                let slot = load_bias.wrapping_add(r_offset);
                let initial = core::ptr::read_unaligned(slot as *const u64);
                core::ptr::write_volatile(slot as *mut u64, initial.wrapping_add(load_bias));
            }
        }
    }
    Ok(OwnPltArming { pltgot, lm_id })
}

/// Classifies the RELRO region's supportability WITHOUT applying
/// anything, over the (already-mapped) relocation table: the first
/// in-region entry of an UNSUPPORTED type -- anything other than
/// RELATIVE or the spanning symbol-resolved kinds this loader can
/// resolve (GLOB_DAT, JUMP_SLOT, 64 -- a DEFINED symbol, or an
/// SHN_UNDEF symbol the caller's [`SymbolResolver`] can resolve) --
/// denies the region; otherwise any in-region entry means the
/// grant/revoke cycle will run. Entries outside the region never
/// affect the region own decision.
///
/// Soundness note: an inspection pass over data already in memory,
/// re-walking the exact same table `apply_relocations` walks, with
/// the same bounds/threshold arithmetic. Read BEFORE any relocation
/// is applied: the RELRO grant decision comes from it.
unsafe fn relro_outcome_planned(
    dt: &DynTables,
    load_bias: u64,
    relro_start: u64,
    relro_end: u64,
    resolver: Option<&dyn SymbolResolver>,
) -> RelroPlanningOutcome {
    if !dt.has_dynamic {
        return RelroPlanningOutcome { some: false };
    }

    let (Some(rela_addr), Some(rela_size)) = (dt.rela_vaddr, dt.rela_size) else {
        return RelroPlanningOutcome { some: false };
    };
    if let Some(entsize) = dt.rela_entsize {
        if entsize as usize != RELA_ENTRY_SIZE {
            return RelroPlanningOutcome { some: false }; // apply will refuse it; region is Denied
        }
    }
    if let Some(syment) = dt.syment {
        if syment as usize != SYM_ENTRY_SIZE {
            return RelroPlanningOutcome { some: false };
        }
    }

    let rela_base = load_bias.wrapping_add(rela_addr);
    let rela_count = (rela_size as usize) / RELA_ENTRY_SIZE;

    // The symbol-count source for definitional checks; absence only
    // matters if an in-region symbol-resolved entry exists (then the
    // apply pass would refuse for MalformedDynamicTables anyway -- so
    // the region is Denied rather than granted).
    let sym_count = match dt.symbol_count(load_bias) {
        Ok(n) => n,
        Err(_) => return RelroPlanningOutcome { some: false },
    };

    for i in 0..rela_count {
        let entry_addr = rela_base + (i * RELA_ENTRY_SIZE) as u64;
        let (r_offset, r_info, _r_addend) = {
            let r_offset = core::ptr::read_unaligned(entry_addr as *const u64);
            let r_info = core::ptr::read_unaligned((entry_addr + 8) as *const u64);
            let r_addend = core::ptr::read_unaligned((entry_addr + 16) as *const i64);
            (r_offset, r_info, r_addend)
        };
        let _ = _r_addend;
        let target_addr = load_bias.wrapping_add(r_offset);
        let r_type = (r_info & 0xffff_ffff) as u32;
        let target_in_region = target_addr >= relro_start && target_addr < relro_end;
        if !target_in_region {
            continue;
        }
        match r_type {
            R_X86_64_RELATIVE => return RelroPlanningOutcome { some: true },
            R_X86_64_64 | R_X86_64_GLOB_DAT | R_X86_64_JUMP_SLOT => {
                // Symbol-resolved but in-object: granted only if its
                // symbol is DEFINED here (the apply pass would refuse
                // an undefined one, and a "granted then never-revoked"
                // region is exactly the silent lie the RELRO work
                // exists to eliminate).
                let sym_idx = (r_info >> 32) as usize;
                if sym_idx == 0 {
                    return RelroPlanningOutcome { some: false }; // apply refuses UndefinedSymbol
                }
                if sym_idx >= sym_count {
                    return RelroPlanningOutcome { some: false }; // apply refuses MalformedDynamicTables
                }
                let (sym_name, _val, st_shndx, st_bind) = match dt.symbol_at_info(load_bias, sym_idx) {
                    Ok(x) => x,
                    Err(_) => return RelroPlanningOutcome { some: false },
                };
                if st_shndx == SHN_UNDEF {
                    // SHN_UNDEF in the FILE: supportable IFF the
                    // caller's resolver can satisfy it WITH the same
                    // version/SONAME contract the relocation pass
                    // will enforce (the apply pass refuses otherwise,
                    // so grant-then-refuse would leave the region in
                    // the honest-but-denied state anyway -- plan that
                    // instead) -- OR the entry is a WEAK requirement
                    // (weak bind or VER_FLG_WEAK): the NULL-slot
                    // outcome the apply pass tolerates is supportable
                    // and still ends read-only, exactly as a real
                    // candidate would.
                    let weak_sym = (st_bind >> STB_BIND_SHIFT) == STB_WEAK;
                    let weak_req = match dt.required_version_info(load_bias, sym_idx) {
                        Ok(req) => req.as_ref().map(|(_, _, w)| *w).unwrap_or(false),
                        Err(_) => return RelroPlanningOutcome { some: false },
                    };
                    let tolerate = weak_sym || weak_req;
                    let supported = match resolver {
                        Some(r) => match r.resolve_versioned(
                            sym_name,
                            // version/soname; mirrors the apply pass.
                            {
                                let required = match dt.required_version(load_bias, sym_idx) {
                                    Ok(x) => x,
                                    Err(_) => return RelroPlanningOutcome { some: false },
                                };
                                required.as_ref().map(|(v, f)| (*v, *f))
                            },
                        ) {
                            Ok(Some(_)) => true,
                            Ok(None) | Err(ElfError::VersionNotProvided) => tolerate,
                            Err(_) => false,
                        },
                        None => tolerate,
                    };
                    return RelroPlanningOutcome {
                        some: supported,
                    };
                }
                return RelroPlanningOutcome { some: true };
            }
            _ => {
                // An in-region entry of an unsupported type: this
                // region cannot be honestly relocated-and-revoked.
                return RelroPlanningOutcome { some: false };
            }
        }
    }
    RelroPlanningOutcome { some: false }
}

struct RelroPlanningOutcome {
    some: bool,
}

/// Applies `f page_flag -> new page_flag` to every 4KiB page in
/// `[start, start + pages * 4096)`, by re-walking the page tables via
/// the same `OffsetPageTable` the mapping pass used. Any page missing
/// or unmapped, or unreadable through the table walk, is a hard
/// failure (`false`), since the mapping pass must have created all of
/// them (making this a check that the load itself is intact, not a
/// formality).
///
/// The update is applied to the page's CURRENT leaf flags, read back
/// through [`crate::memory::paging::page_flags_of`] (CR3-based -- the
/// caller must be running the address space that owns these tables,
/// as every demo's load/activate ordering guarantees). This matters:
/// `Mapper::update_flags` REPLACES the whole flag set, so a
/// synthetic base like `PRESENT` would silently strip
/// `USER_ACCESSIBLE` and `NO_EXECUTE` from the RELRO pages -- which
/// would leave `ld -shared` GOT pages (which sit inside the GNU_RELRO
/// region here) kernel-only, faulting the very ring-3 GOT reads the
/// loaded code performs. Grant/revoke must change exactly one bit.
pub(crate) fn update_page_flags_range(
    mapper: &mut OffsetPageTable<'static>,
    start: u64,
    pages: u64,
    update: impl Fn(PageTableFlags) -> PageTableFlags,
) -> bool {
    for i in 0..pages {
        let addr = VirtAddr::new(start + i * 4096);
        let page: Page<Size4KiB> = Page::containing_address(addr);
        // Read the leaf's CURRENT flags first (see doc); `mapper`'s
        // tables and CR3's must agree here -- the demo callers load
        // into and relocate the ACTIVE address space.
        let current = match crate::memory::paging::page_flags_of(mapper, addr) {
            Some(Ok(f)) => f,
            _ => return false, // unmapped/unreadable: never silently widen or narrow
        };
        match unsafe { mapper.update_flags(page, update(current)) } {
            Ok(flush) => {
                flush.flush();
            }
            Err(_) => return false,
        }
    }
    true
}

/// Walks the relocation table via the pre-parsed [`DynTables`] and
/// applies every entry it can honestly satisfy:
///
/// - `R_X86_64_RELATIVE`: `*(base + r_offset) = base + r_addend`
///   (base-address adjustment only, no symbol involved);
/// - `R_X86_64_64`:        `*(base + r_offset) = S + r_addend`,
/// - `R_X86_64_GLOB_DAT`, `R_X86_64_JUMP_SLOT`: `*(base + r_offset) = S`,
///
/// where `S` is the runtime address of the relocation's symbol --
/// `load_bias + st_value` for a symbol DEFINED in this object, or
/// (for an `SHN_UNDEF` symbol) whatever the caller's
/// [`SymbolResolver`] resolves it to. Undefined-and-unresolvable
/// needs cross-object resolution nobody provided and is refused
/// (`UndefinedSymbol`); a symbol index outside the DT_HASH-bounded
/// table, a DT_SYMENT other than 24, a missing DT_SYMTAB, or a string
/// offset past DT_STRSZ is refused (`MalformedDynamicTables`).
/// Entries of any other type are refused (`UnsupportedRelocationType`)
/// -- a mis-relocated target is worse than a refused load, the
/// loader's standing posture.
///
/// # Safety
/// The whole dynamic surface (PT_DYNAMIC itself, DT_HASH/DT_SYMTAB/
/// DT_STRTAB, the RELA table, and every r_offset target) must lie
/// inside PT_LOAD-mapped, populated memory (the caller's condition);
/// DTO reads/writes go through unaligned pointer access on those
/// runtime addresses.
unsafe fn apply_relocations(
    dt: &DynTables,
    load_bias: u64,
    resolver: Option<&dyn SymbolResolver>,
    skip_plt: bool,
) -> Result<(), ElfError> {
    // Both relocation tables use the same Elf64_Rela shape on x86-64:
    // DT_RELA (data relocations) always, and DT_JMPREL (the PLT's
    // JUMP_SLOT table) EXCEPT when the lazy-install pass owns those
    // slots (both writers can never race the same slot).
    let mut relocations_planted: alloc::vec::Vec<(u64, u64)> = alloc::vec::Vec::new();
    if let (Some(a), Some(s)) = (dt.rela_vaddr, dt.rela_size) {
        relocations_planted.push((a, s));
    }
    if !skip_plt {
        if let (Some(a), Some(s)) = (dt.jmprel_vaddr, dt.plt_relsz) {
            relocations_planted.push((a, s));
        }
    }
    if relocations_planted.is_empty() {
        return Ok(()); // no relocation table -- nothing to do, not an error
    }
    if let Some(entsize) = dt.rela_entsize {
        if entsize as usize != RELA_ENTRY_SIZE {
            return Err(ElfError::UnsupportedRelocationEntrySize);
        }
    }

    // Symbol table metadata only when a symbol-resolved entry appears.
    // Sym count is derived lazily from DT_HASH (the SysV authority for
    // the table length). The cache spans BOTH tables (each walk reuses
    // the first DT_HASH read).
    let mut sym_count_cache: Option<Result<usize, ElfError>> = None;

    for (table_vaddr, table_size) in relocations_planted {
        let rela_base = load_bias.wrapping_add(table_vaddr);
        let rela_count = (table_size as usize) / RELA_ENTRY_SIZE;

        for i in 0..rela_count {
        let entry_addr = rela_base + (i * RELA_ENTRY_SIZE) as u64;
        let (r_offset, r_info, r_addend) = {
            let r_offset = core::ptr::read_unaligned(entry_addr as *const u64);
            let r_info = core::ptr::read_unaligned((entry_addr + 8) as *const u64);
            let r_addend = core::ptr::read_unaligned((entry_addr + 16) as *const i64);
            (r_offset, r_info, r_addend)
        };
        let r_type = (r_info & 0xffff_ffff) as u32;
        let target_addr = load_bias.wrapping_add(r_offset);

        let value: u64 = match r_type {
            R_X86_64_RELATIVE => load_bias.wrapping_add(r_addend as u64),
            R_X86_64_64 | R_X86_64_GLOB_DAT | R_X86_64_JUMP_SLOT => {
                let sym_idx = (r_info >> 32) as usize;
                if sym_idx == 0 {
                    // Symbol-resolved with a zero index: malformed for
                    // these types (r_info's upper 32 bits are the
                    // symbol index; a real linker would also refuse).
                    return Err(ElfError::MalformedDynamicTables);
                }
                // Lazily pull the table length from DT_HASH once.
                let sym_count = match sym_count_cache.take() {
                    Some(r) => r,
                    None => {
                        let r = dt.symbol_count(load_bias);
                        match r {
                            Ok(n) => {
                                sym_count_cache = Some(Ok(n));
                                Ok(n)
                            }
                            Err(e) => {
                                sym_count_cache = Some(Err(ElfError::MalformedDynamicTables));
                                Err(e)
                            }
                        }
                    }
                };
                let sym_count = sym_count?;
                if sym_idx >= sym_count {
                    return Err(ElfError::MalformedDynamicTables);
                }
                let (sym_name, st_value, st_shndx, st_bind) =
                    dt.symbol_at_info(load_bias, sym_idx)?;
                let s = if st_shndx != SHN_UNDEF {
                    // DEFINED in this object: the file's own dynsym
                    // entry wins, always -- this is the self-priority
                    // half of the documented search order (earlier
                    // loaded objects' same-named definitions must NOT
                    // displace an object's own dynsym; verified
                    // every boot by elf_dyn_cross_demo's duplicate-
                    // symbol check, which would show the other
                    // object's address if a by-name registry pass
                    // were built carelessly here instead).
                    load_bias.wrapping_add(st_value)
                } else {
                    // UNDEFINED in this object: cross-object
                    // resolution is the file stating a fact -- the
                    // symbol MUST come from the caller's resolver
                    // scope, honoring whatever VERSION the requester
                    // declares (DT_VERSYM/DT_VERNEED; legacy
                    // unversioned objects resolve version-agnostic).
                    // Unresolvable = refuse, never
                    // resolve-to-garbage -- EXCEPT for the ELF weak
                    // semantics, which deliberately permit
                    // unresolvable needs: a WEAK undefined symbol
                    // (`st_info >> STB_BIND_SHIFT == STB_WEAK`) or a
                    // VERNEED requirement carrying `VER_FLG_WEAK`
                    // resolves to the NULL slot (0) when no candidate
                    // exists, and the load proceeds. A strong
                    // requirement stays a hard, typed refusal.
                    let required = dt.required_version_info(load_bias, sym_idx)?;
                    let weak_req = required.as_ref().map(|(_, _, w)| *w).unwrap_or(false);
                    let weak_sym = (st_bind >> STB_BIND_SHIFT) == STB_WEAK;
                    let tolerate = weak_sym || weak_req;
                    match resolver {
                        Some(r) => match r
                            .resolve_versioned(
                                sym_name,
                                required.as_ref().map(|(v, f, _)| (*v, *f)),
                            ) {
                            Ok(Some(addr)) => addr,
                            Ok(None) => {
                                if tolerate {
                                    0 // the NULL slot: weak semantics, never a guess at a real address
                                } else {
                                    return Err(ElfError::UndefinedSymbol);
                                }
                            }
                            Err(ElfError::VersionNotProvided) if tolerate => {
                                0 // weak version contract unmet: NULL slot, load proceeds
                            }
                            Err(e) => return Err(e),
                        },
                        None => {
                            if tolerate {
                                0
                            } else {
                                return Err(ElfError::UndefinedSymbol);
                            }
                        }
                    }
                };
                match r_type {
                    R_X86_64_64 => s.wrapping_add(r_addend as u64),
                    _ => s, // GLOB_DAT / JUMP_SLOT: S exactly
                }
            }
            _ => {
                return Err(ElfError::UnsupportedRelocationType);
            }
        };

        core::ptr::write_unaligned(target_addr as *mut u64, value);
        }
    }

    Ok(())
}

/// Public, safe wrapper for the demo/self-tests: resolves a symbol BY
/// NAME through the object's SysV DT_HASH table, returning its
/// runtime address (`load_bias + st_value`). Requires a DEFINED
/// symbol; `SymbolNotFound` is the typed "absent" outcome (distinct
/// from parse errors).
///
/// # Safety
/// The dynamic tables (DT_HASH/DT_SYMTAB/DT_STRTAB) must be inside
/// PT_LOAD-mapped, populated memory for THIS load (the same condition
/// `load` itself operated under, and `LoadedElf::relro` consumers
/// already assume).
/// Locates the PT_DYNAMIC segment's (p_vaddr, p_filesz) range in an
/// ELF64 image by scanning its program headers -- the same scan `load`
/// itself does. Public because the demo needs the same range to build
/// its [`FatDynTables`] for by-name lookups.
///
/// Bounds-checked against the image length; `None` when the image is
/// too short to hold a header table or carries no PT_DYNAMIC.
pub fn pt_dynamic_range(data: &[u8]) -> Option<(u64, u64)> {
    if data.len() < EHDR_SIZE {
        return None;
    }
    if data[0..4] != ELF_MAGIC {
        return None;
    }
    let e_phoff = read_u64(data, 32)? as usize;
    let e_phentsize = read_u16(data, 54)? as usize;
    let e_phnum = read_u16(data, 56)? as usize;
    if e_phentsize < PHDR_SIZE {
        return None;
    }
    for i in 0..e_phnum {
        let off = e_phoff.checked_add(i.checked_mul(e_phentsize)?)?;
        if off + PHDR_SIZE > data.len() {
            return None;
        }
        let ph = parse_program_header(data, off)?;
        if ph.p_type == PT_DYNAMIC {
            return Some((ph.p_vaddr, ph.p_filesz));
        }
    }
    None
}

/// Same lookup machinery as [`lookup_symbol`], in `Option`/`Err`
/// shape for [`SymbolResolver`] use: `Ok(Some(addr))` = a DEFINED
/// match at `addr`, `Ok(None)` = the name is absent or UNDEFINED in
/// this object (="do not use this object" for the resolver), `Err` =
/// malformed dynamic tables (never silently reinterpreted as
/// absent).
///
/// # Safety
/// The object's dynamic tables (DT_HASH/DT_SYMTAB/DT_STRTAB) must be
/// mapped and populated in the currently-active address space (the
/// same condition `load`/`parse_dyn_tables` guaranteed at
/// registration time; the caller must keep the registered objects'
/// mappings alive in the active space).
unsafe fn locate_symbol_in_tables(
    tables: &FatDynTables,
    load_bias: u64,
    name: &[u8],
) -> Result<Option<u64>, ElfError> {
    let sym_count = tables.0.symbol_count(load_bias)?;
    let idx = match tables.0.lookup_hash_any(load_bias, name, sym_count) {
        Ok(idx) => idx,
        Err(ElfError::SymbolNotFound) => return Ok(None),
        Err(e) => return Err(e),
    };
    let (_nm, st_value, st_shndx) = tables.0.symbol_at(load_bias, idx)?;
    if st_shndx == SHN_UNDEF {
        return Ok(None); // an UNDEF entry is not a definition
    }
    Ok(Some(load_bias.wrapping_add(st_value)))
}

pub unsafe fn lookup_symbol(
    tables: &FatDynTables,
    load_bias: u64,
    name: &[u8],
) -> Result<u64, ElfError> {
    let sym_count = tables.0.symbol_count(load_bias)?;
    let idx = match tables.0.lookup_hash_any(load_bias, name, sym_count) {
        Ok(idx) => idx,
        // The GNU table EXCLUDES undefined symbols from its buckets
        // (verified on the artifacts: an UNDEF name hits an empty
        // GNU bucket, while DT_HASH -- which chains EVERY dynsym
        // entry, defined or not -- still carries it). So "absent by
        // GNU" cannot distinguish "not this object's name at all"
        // from "present as UNDEF": when the GNU walk says absent and
        // DT_HASH exists (the always-present authority over the
        // symbol count), re-walk the SysV table to recover that
        // distinction. This is the documented DT_HASH authority, not
        // a silent fallback: the SysV re-walk can only restate
        // absent-ness or surface the UNDEF entry as UndefinedSymbol;
        // it never overrides a GNU DEFINED answer.
        Err(ElfError::SymbolNotFound) => {
            tables.0.lookup_hash_walk(load_bias, name, sym_count)?
        }
        Err(e) => return Err(e),
    };
    let (nm, st_value, st_shndx) = tables.0.symbol_at(load_bias, idx)?;
    debug_assert_eq!(nm, name);
    if st_shndx == SHN_UNDEF {
        return Err(ElfError::UndefinedSymbol);
    }
    Ok(load_bias.wrapping_add(st_value))
}

/// The two mechanisms compared EXPLICITLY, for demos that need to pin
/// which table answered: the SysV bucket/chain walk ([`lookup_symbol_sysv`])
/// and the glibc-style GNU bloom+bucket/chain walk
/// ([`lookup_symbol_gnu`]). The ordinary resolution paths use
/// [`lookup_hash_any`] (GNU preferred) internally; these exist so the
/// equality of the two mechanisms' ANSWERS on the same tables is a
/// checked fact, not an implementation detail.

/// # Safety
/// Conditions as [`lookup_symbol`].
pub unsafe fn lookup_symbol_sysv(
    tables: &FatDynTables,
    load_bias: u64,
    name: &[u8],
) -> Result<u64, ElfError> {
    let sym_count = tables.0.symbol_count(load_bias)?;
    let idx = tables.0.lookup_hash_walk(load_bias, name, sym_count)?;
    let (_nm, st_value, st_shndx) = tables.0.symbol_at(load_bias, idx)?;
    if st_shndx == SHN_UNDEF {
        return Err(ElfError::UndefinedSymbol);
    }
    Ok(load_bias.wrapping_add(st_value))
}

/// # Safety
/// Conditions as [`lookup_symbol`]; requires the GNU table (an object
/// without DT_GNU_HASH is a typed refusal, not a silent SysV
/// fall-through).
pub unsafe fn lookup_symbol_gnu(
    tables: &FatDynTables,
    load_bias: u64,
    name: &[u8],
) -> Result<u64, ElfError> {
    let sym_count = tables.0.symbol_count(load_bias)?;
    let idx = tables.0.lookup_gnu_hash(load_bias, name, sym_count)?;
    let (_nm, st_value, st_shndx) = tables.0.symbol_at(load_bias, idx)?;
    if st_shndx == SHN_UNDEF {
        return Err(ElfError::UndefinedSymbol);
    }
    Ok(load_bias.wrapping_add(st_value))
}

#[derive(Clone)]
/// Public typedef for keeping `lookup_symbol`'s signature readable;
/// wraps the parse-once [`DynTables`] for a given load.
pub struct FatDynTables(DynTables);

impl FatDynTables {
    /// This object's real DT_SONAME bytes, when it declares one --
    /// what `SymbolRegistry::has_soname` and the version contract
    /// compare against.
    pub fn soname(&self) -> Option<&'static [u8]> {
        self.0.soname
    }

    /// Whether the object carries a DT_GNU_HASH table (checked by the
    /// demo-side GNU/SysV mechanism-equality fact).
    pub fn carries_gnu_hash(&self) -> bool {
        self.0.gnu_hash_vaddr.is_some()
    }
}

/// Parses a loaded ELF object's dynamic tables from its mapped
/// PT_DYNAMIC segment, returning the wrapper [`lookup_symbol`] needs.
/// Must be called with the same `load_bias`/segment the load used.
///
/// # Safety
/// The PT_DYNAMIC segment must be mapped and populated (i.e. this is
/// called after a successful `elf::load` of the same object).
pub unsafe fn parse_dyn_tables(
    load_bias: u64,
    dyn_vaddr: u64,
    dyn_filesz: u64,
) -> Result<FatDynTables, ElfError> {
    Ok(FatDynTables(DynTables::read_from_memory(
        load_bias,
        dyn_vaddr,
        dyn_filesz,
    )?))
}

/// Resolves the `idx`th .rela.plt (JUMP_SLOT) entry AT FIRST USE --
/// the lazy-binding service the IDT `0x88` trap handler calls with
/// the index a stub pushed on the user stack. Same rules as the
/// load-time pass: self-dynsym first, then the resolver's search
/// scope, honoring the requester's version contract.
///
/// # Safety
/// The object's dynamic tables must be mapped in the active space
/// (the same condition `parse_dyn_tables` guaranteed); the resolver
/// scope likewise (registered objects' tables mapped).
/// The runtime address of the `idx`th .rela.plt entry's GOT slot --
/// what the IDT lazy-bind handler must patch after
/// [`resolve_lazy_entry`] returns. Bounds-checked like the resolver.
///
/// # Safety
/// Conditions as [`resolve_lazyEntry`].
pub unsafe fn lazy_slot_for(
    tables: &FatDynTables,
    load_bias: u64,
    idx: usize,
) -> Result<u64, ElfError> {
    let (Some(jmprel), Some(plt_relsz)) = (tables.0.jmprel_vaddr, tables.0.plt_relsz) else {
        return Err(ElfError::MalformedDynamicTables);
    };
    if idx >= (plt_relsz as usize) / RELA_ENTRY_SIZE {
        return Err(ElfError::MalformedDynamicTables);
    }
    let entry_addr = load_bias.wrapping_add(jmprel) + (idx * RELA_ENTRY_SIZE) as u64;
    let r_offset = core::ptr::read_unaligned(entry_addr as *const u64);
    Ok(load_bias.wrapping_add(r_offset))
}

pub unsafe fn resolve_lazy_entry(
    tables: &FatDynTables,
    load_bias: u64,
    idx: usize,
    resolver: Option<&dyn SymbolResolver>,
) -> Result<u64, ElfError> {
    let (Some(jmprel), Some(plt_relsz)) = (tables.0.jmprel_vaddr, tables.0.plt_relsz) else {
        return Err(ElfError::MalformedDynamicTables); // no PLT surface -- nothing could have armed a lazy stub
    };
    if idx >= (plt_relsz as usize) / RELA_ENTRY_SIZE {
        return Err(ElfError::MalformedDynamicTables); // an index no live stub could have pushed
    }
    let entry_addr = load_bias.wrapping_add(jmprel) + (idx * RELA_ENTRY_SIZE) as u64;
    let r_info = core::ptr::read_unaligned((entry_addr + 8) as *const u64);
    if (r_info & 0xffff_ffff) as u32 != R_X86_64_JUMP_SLOT {
        return Err(ElfError::MalformedDynamicTables);
    }
    let sym_idx = (r_info >> 32) as usize;
    if sym_idx == 0 {
        return Err(ElfError::MalformedDynamicTables);
    }
    let (sym_name, st_value, st_shndx, st_bind) = tables.0.symbol_at_info(load_bias, sym_idx)?;
    if st_shndx != SHN_UNDEF {
        return Ok(load_bias.wrapping_add(st_value));
    }
    // Same weak semantics at first-use binding as at load time: a
    // WEAK undefined symbol (or weak VERNEED requirement) with no
    // candidate binds the NULL slot (0) and the call proceeds; a
    // strong requirement stays a hard refusal.
    let required = tables.0.required_version_info(load_bias, sym_idx)?;
    let weak_req = required.as_ref().map(|(_, _, w)| *w).unwrap_or(false);
    let weak_sym = (st_bind >> STB_BIND_SHIFT) == STB_WEAK;
    let tolerate = weak_sym || weak_req;
    match resolver {
        Some(r) => {
            match r.resolve_versioned(sym_name, required.as_ref().map(|(v, f, _)| (*v, *f))) {
                Ok(Some(addr)) => Ok(addr),
                Ok(None) => {
                    if tolerate {
                        Ok(0) // NULL slot: the same lazy-time weak semantics as the loader
                    } else {
                        Err(ElfError::UndefinedSymbol)
                    }
                }
                Err(ElfError::VersionNotProvided) if tolerate => Ok(0),
                Err(e) => Err(e),
            }
        }
        None => {
            if tolerate {
                Ok(0)
            } else {
                Err(ElfError::UndefinedSymbol)
            }
        }
    }
}

/// Locates the GOT slot (`R_X86_64_GLOB_DAT`/`JUMP_SLOT`/`64`-family
/// target) that binds `name` in this object's relocation tables, by
/// SYMBOL NAME rather than by pinned offsets. Returns the runtime
/// slot address, `Ok(None)` when `name` has no relocation binding it
/// (a demo check that would give away the point by reading an
/// arbitrary address).
///
/// # Safety
/// The dynamic relocation tables must be mapped (the load completed),
/// the same condition every DynTables reader documents.
pub unsafe fn find_slot_for_symbol(
    tables: &FatDynTables,
    load_bias: u64,
    name: &[u8],
) -> Result<Option<u64>, ElfError> {
    let (Some(rela), Some(size)) = (tables.0.rela_vaddr, tables.0.rela_size) else {
        return Ok(None);
    };
    let base = load_bias.wrapping_add(rela);
    let count = (size as usize) / RELA_ENTRY_SIZE;
    for i in 0..count {
        let entry_addr = base + (i * RELA_ENTRY_SIZE) as u64;
        let (r_offset, r_info) = {
            let r_offset = core::ptr::read_unaligned(entry_addr as *const u64);
            let r_info = core::ptr::read_unaligned((entry_addr + 8) as *const u64);
            (r_offset, r_info)
        };
        let r_type = (r_info & 0xffff_ffff) as u32;
        if r_type != R_X86_64_GLOB_DAT && r_type != R_X86_64_64 && r_type != R_X86_64_JUMP_SLOT {
            continue;
        }
        let sym_idx = (r_info >> 32) as usize;
        if sym_idx == 0 {
            continue;
        }
        let (sym_name, _v, _s) = tables.0.symbol_at(load_bias, sym_idx)?;
        if sym_name == name {
            return Ok(Some(load_bias.wrapping_add(r_offset)));
        }
    }
    Ok(None)
}

// ------------------------------------------------------------------
// Dependency CATALOG + recursive DT_NEEDED loading.
//
// The closing half of the "DT_NEEDED-driven dependency discovery"
// limitation: instead of refusing a load whose declared SONAME is not
// already in scope, a caller may supply a [`DepCatalog`] -- a named
// TABLE of already-known object files with load bases (the kernel's
// honest stand-in for a storage surface until Phase 8 provides a
// filesystem: the objects are embedded bytes and the catalog maps
// SONAME -> file/base, exactly what `/lib` + a search path becomes
// then). [`load_tree`] loads a root object and recursively satisfies
// EVERY unresolved DT_NEEDED it declares, dependency-first (each
// dependency lands in scope before the dependent that NEEDS it is
// relocated), with cycle detection (typed `DependencyCycle`) and a
// depth cap (`DependencyDepthExceeded`).
//
// What this deliberately does NOT claim: a filesystem. The catalog
// cannot "find" anything not registered into it, and the loader
// never probes storage -- same never-guess posture, with the
// discovery surface made explicit.

/// A SONAME-addressable table of object files for automatic
/// `DT_NEEDED` loading. Entries map a SONAME to its file bytes and
/// the base the loader maps them at; nothing in this table implies
/// storage -- every entry was placed there upstream (demo code with
/// embedded bytes today; a filesystem client in Phase 8).
#[derive(Clone)]
pub struct DepCatalog {
    entries: alloc::vec::Vec<(&'static [u8], &'static [u8], u64)>,
    max_depth: usize,
}

impl DepCatalog {
    /// `max_depth` = the recursion cap over ON-CHAIN NEEDED hops.
    pub fn new(max_depth: usize) -> Self {
        DepCatalog {
            entries: alloc::vec::Vec::new(),
            max_depth,
        }
    }

    pub fn add(&mut self, soname: &'static [u8], file: &'static [u8], base: u64) {
        self.entries.push((soname, file, base));
    }

    fn get(&self, soname: &[u8]) -> Option<(&'static [u8], &'static [u8], u64)> {
        self.entries
            .iter()
            .find(|(s, _, _)| *s == soname)
            .map(|(s, f, b)| (*s, *f, *b))
    }
}

/// A resolver over a base [`SymbolRegistry`] plus the objects a
/// recursive [`load_tree`] has loaded so far (kept OUT of the base to
/// avoid handing out mutable registry references mid-recursion; the
/// demo pushes them into its registry after the tree completes, in
/// load order).
pub struct ScopedResolver<'s> {
    base: &'s SymbolRegistry,
    extras: &'s alloc::vec::Vec<(FatDynTables, u64)>,
}

impl SymbolResolver for ScopedResolver<'_> {
    fn resolve(&self, name: &[u8]) -> Result<Option<u64>, ElfError> {
        // SAFETY: base + extras hold the same registration-time
        // mapping guarantees the demo maintains for its registry.
        unsafe { ordered_lookup(&self.combined(), name) }
    }

    fn resolve_versioned(
        &self,
        name: &[u8],
        required: Option<(&[u8], &[u8])>,
    ) -> Result<Option<u64>, ElfError> {
        unsafe { versioned_lookup(&self.combined(), name, required) }
    }

    fn has_soname(&self, soname: &[u8]) -> Result<bool, ElfError> {
        Ok(soname_check(&self.combined(), soname))
    }
}

impl ScopedResolver<'_> {
    fn combined(&self) -> alloc::vec::Vec<(FatDynTables, u64)> {
        // Combined view: the base registry's own visit order (with its
        // interposition overlays intact), then the extras in load
        // order. Small singular object counts per boot keep the clone
        // churn trivial.
        let mut combined = self.base.visit_order();
        combined.extend(self.extras.iter().cloned());
        combined
    }
}

/// A SONAME is "in scope" when any entry -- base (interposition
/// order respected) or extras -- declares it.
fn soname_any_in_scope(
    base: &SymbolRegistry,
    extras: &[(FatDynTables, u64)],
    soname: &[u8],
) -> bool {
    soname_check(&base.visit_order(), soname) || soname_check(extras, soname)
}

/// The recursive loader's bookkeeping. All in-loader state stays
/// local; recursion via `load` itself has no reentrancy hazards (each
/// step is sequential mapping work).
struct TreeLoader<'m, 'f, 's> {
    mapper: &'m mut OffsetPageTable<'static>,
    fa: &'f mut BumpFrameAllocator,
    base: &'s SymbolRegistry,
    catalog: &'s DepCatalog,
    extras: alloc::vec::Vec<(FatDynTables, u64)>,
    in_progress: alloc::vec::Vec<alloc::vec::Vec<u8>>,
}

impl TreeLoader<'_, '_, '_> {
    unsafe fn ensure(&mut self, needed: &[u8]) -> Result<(), ElfError> {
        // Already in scope (base registry or extras): satisfied.
        if soname_any_in_scope(self.base, &self.extras, needed) {
            return Ok(());
        }
        // In-progress: the caller is currently loading the object that
        // needs this very name -> a cycle; reloading it to satisfy
        // itself can never terminate.
        if self.in_progress.iter().any(|n| n.as_slice() == needed) {
            return Err(ElfError::DependencyCycle);
        }
        // Catalog hit; no guesswork and no probing storage.
        let (_soname, file, file_base) = self
            .catalog
            .get(needed)
            .ok_or(ElfError::MissingDependency)?;
        if self.in_progress.len() >= self.catalog.max_depth {
            return Err(ElfError::DependencyDepthExceeded);
        }
        self.in_progress.push(needed.to_vec());
        let out = self.load_recursive(file, file_base);
        self.in_progress.pop();
        out
    }

    /// Loads one dependency AT ITS CATALOG BASE. Its name is in
    /// scope (extras) for everything loaded after it.
    unsafe fn load_recursive(&mut self, file: &'static [u8], base: u64) -> Result<(), ElfError> {
        let needed = needed_from_file(file)?;
        for n in &needed {
            self.ensure(n)?;
        }
        let scope = ScopedResolver {
            base: self.base,
            extras: &self.extras,
        };
        let ctx = LoadContext {
            resolver: Some(&scope),
            lazy_plt_page: None,
            own_plt_resolver_page: None,
        };
        load(file, self.mapper, self.fa, base, &ctx)?;
        let (dv, df) = pt_dynamic_range(file).ok_or(ElfError::MalformedDynamicTables)?;
        let tables = parse_dyn_tables(base, dv, df)?;
        self.extras.push((tables, base));
        Ok(())
    }
}

/// Loads `root` at `root_base`, recursively satisfying its unresolved
/// DT_NEEDED declarations through `catalog` BEFORE relocating it
/// (dependency-first, so the root's contract/relocation checks see a
/// complete scope). `root_name` (its SONAME, or a caller-chosen
/// pseudo-identifier) seeds cycle detection for the root itself.
/// Returns the loaded root, the objects loaded from the catalog in
/// load order, and the root's parsed dynamic tables.
///
/// # Safety
/// Same conditions as `elf::load` for every object loaded; the
/// caller must own the mapping/address-space lifetime and keep every
/// returned object mapped in the ACTIVE space (trap-time resolvers
/// rely on that).
pub unsafe fn load_tree(
    root: &'static [u8],
    root_name: Option<&'static [u8]>,
    root_base: u64,
    mapper: &mut OffsetPageTable<'static>,
    fa: &mut BumpFrameAllocator,
    base_registry: &SymbolRegistry,
    catalog: &DepCatalog,
    lazy_plt_page: Option<VirtAddr>,
) -> Result<(LoadedElf, alloc::vec::Vec<(FatDynTables, u64)>, FatDynTables), ElfError> {
    let mut loader = TreeLoader {
        mapper,
        fa,
        base: base_registry,
        catalog,
        extras: alloc::vec::Vec::new(),
        in_progress: alloc::vec::Vec::new(),
    };
    if let Some(n) = root_name {
        loader.in_progress.push(n.to_vec());
    }
    let needed = needed_from_file(root)?;
    for n in &needed {
        loader.ensure(n)?;
    }
    let scope = ScopedResolver {
        base: loader.base,
        extras: &loader.extras,
    };
    let ctx = LoadContext {
        resolver: Some(&scope),
        lazy_plt_page,
        own_plt_resolver_page: None,
    };
    let loaded = load(root, loader.mapper, loader.fa, root_base, &ctx)?;
    let (dv, df) = pt_dynamic_range(root).ok_or(ElfError::MalformedDynamicTables)?;
    let root_tables = parse_dyn_tables(root_base, dv, df)?;
    Ok((loaded, loader.extras, root_tables))
}

/// Reads the DT_NEEDED list out of an ELF64 image's FILE bytes (the
/// readahead a real dynamic linker does BEFORE mapping: parse the
/// dynamic segment from the file, translate `DT_STRTAB`'s pre-bias
/// vaddr through the PT_LOAD list, and slice the NEEDED strings out
/// of the file bytes under STRSZ binds). No dynamic segment (or none
/// needed) = an empty list: a leaf object, satisfiable everywhere.
fn needed_from_file(data: &[u8]) -> Result<alloc::vec::Vec<alloc::vec::Vec<u8>>, ElfError> {
    if data.len() < EHDR_SIZE || data[0..4] != ELF_MAGIC {
        return Err(ElfError::MalformedDynamicTables);
    }
    let e_phoff = read_u64(data, 32).ok_or(ElfError::TooShort)? as usize;
    let e_phentsize = read_u16(data, 54).ok_or(ElfError::TooShort)? as usize;
    let e_phnum = read_u16(data, 56).ok_or(ElfError::TooShort)? as usize;
    if e_phentsize < PHDR_SIZE {
        return Err(ElfError::ProgramHeaderOutOfBounds);
    }
    let mut loads = alloc::vec::Vec::new(); // (vaddr, fileoff, filesz)
    let mut dyn_range: Option<(u64, u64)> = None;
    for i in 0..e_phnum {
        let off = e_phoff + i * e_phentsize;
        if off + PHDR_SIZE > data.len() {
            return Err(ElfError::ProgramHeaderOutOfBounds);
        }
        let ph = parse_program_header(data, off).ok_or(ElfError::ProgramHeaderOutOfBounds)?;
        if ph.p_type == PT_DYNAMIC {
            dyn_range = Some((ph.p_vaddr, ph.p_filesz));
        } else if ph.p_type == PT_LOAD {
            let start = ph.p_offset as usize;
            let end = start
                .checked_add(ph.p_filesz as usize)
                .ok_or(ElfError::SegmentDataOutOfBounds)?;
            if end > data.len() {
                return Err(ElfError::SegmentDataOutOfBounds);
            }
            loads.push((ph.p_vaddr, ph.p_offset, ph.p_filesz));
        }
    }
    let (dyn_vaddr, dyn_filesz) = match dyn_range {
        Some(x) => x,
        None => return Ok(alloc::vec::Vec::new()), // leaf object
    };
    let vaddr_to_off = |vaddr: u64| -> Option<u64> {
        loads
            .iter()
            .find(|(v, _, filesz)| vaddr >= *v && vaddr - *v < *filesz)
            .map(|(v, off, _)| *off + (vaddr - *v))
    };
    let dyn_file_off = vaddr_to_off(dyn_vaddr).ok_or(ElfError::MalformedDynamicTables)?;
    if dyn_file_off as usize + dyn_filesz as usize > data.len() {
        return Err(ElfError::SegmentDataOutOfBounds);
    }
    let mut strtab_vaddr: Option<u64> = None;
    let mut strsz: Option<u64> = None;
    let mut needed_offsets = alloc::vec::Vec::new();
    let count = dyn_filesz as usize / DYN_ENTRY_SIZE;
    for i in 0..count {
        let e = dyn_file_off as usize + i * DYN_ENTRY_SIZE;
        let tag = read_i64(data, e).ok_or(ElfError::MalformedDynamicTables)?;
        let val = read_u64(data, e + 8).ok_or(ElfError::MalformedDynamicTables)?;
        if tag == DT_NULL {
            break;
        }
        match tag {
            DT_STRTAB => strtab_vaddr = Some(val),
            DT_STRSZ => strsz = Some(val),
            DT_NEEDED => needed_offsets.push(val),
            _ => {}
        }
    }
    if strtab_vaddr.is_none() {
        return Ok(alloc::vec::Vec::new()); // no string table: no NAMES at all
    }
    let str_off = vaddr_to_off(strtab_vaddr.unwrap()).ok_or(ElfError::MalformedDynamicTables)?;
    let strsz = strsz.unwrap_or(data.len() as u64); // unbound STRSZ still gets a real bound: the file end
    let mut out = alloc::vec::Vec::new();
    for off in needed_offsets {
        if off >= strsz {
            return Err(ElfError::MalformedDynamicTables);
        }
        let s_abs = str_off as usize + off as usize;
        let slice = data
            .get(s_abs..s_abs + (strsz - off) as usize)
            .ok_or(ElfError::SegmentDataOutOfBounds)?;
        let len = slice
            .iter()
            .position(|&c| c == 0)
            .ok_or(ElfError::MalformedDynamicTables)?;
        out.push(data[s_abs..s_abs + len].to_vec());
    }
    Ok(out)
}

/// Little-endian i64 read out of a byte slice, bounds-checked.
fn read_i64(data: &[u8], offset: usize) -> Option<i64> {
    data.get(offset..offset + 8).map(|b| {
        i64::from_le_bytes([
            b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7],
        ])
    })
}

