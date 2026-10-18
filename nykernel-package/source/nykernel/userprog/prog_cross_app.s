/*
 * prog_cross_app.s -- the DEPENDENT half of the cross-object
 * resolution increment: a real `ld -shared` shared object whose
 * relocations genuinely NEED the registry:
 *
 *   cross_value is UNDEFINED in this object (no local definition at
 *   all), referenced through a linker-created GOT slot
 *   (`mov cross_value@GOTPCREL(%rip)`) which `ld -shared` materializes
 *   as an R_X86_64_GLOB_DAT entry whose dynsym entry has
 *   st_shndx == SHN_UNDEF. Without a resolver the loader REFUSES this
 *   file (typed UndefinedSymbol); with the registry holding the
 *   provider .so, the slot resolves to the PROVIDER's cross_value.
 *
 *   dup_symbol is DEFINED HERE with the same name the provider ALSO
 *   exports (different immediate). Because the relocator resolves
 *   this object's OWN dynsym entries first (self-priority is
 *   structural: a defined symbol is taken from the file, the
 *   registry is consulted only for SHN_UNDEF), its GOT slot must
 *   resolve to THIS object's dup_symbol -- a careless by-name pass
 *   over the registry instead would put the provider's address in
 *   the slot, and the kernel-side check MISMATCHes loudly. The
 *   provider's entry is distinguishable for exactly this control.
 *
 *   export: cross_payload -- entry point. Dereferences its
 *     cross-resolved GOT slot in ring 3 and writes the observed
 *     value to the demo's private page (ef_dyn-style behavioral
 *     half), then traps vector 0x87.
 */

.section .data
.align 8
.globl dup_symbol
dup_symbol:
    /* THIS object's own duplicate. Distinct from the provider's
     * 0x00000000BAD0B100 -- a self-priority violation shows up as
     * the wrong VALUE in the slot check (and the wrong ADDRESS in
     * the resolved-address check). */
    .quad 0x00000000BA5E1A11

/*
 * Behavioral half: rax = *(GOT[cross_value]) -- the registry-resolved
 * &cross_value in the PROVIDER; rbx = *that = 0xACE5F00DD1CE5EED;
 * write it to the demo's private page and report via the 0x87 trap.
 * r8 = *(GOT[dup_symbol]) must be THIS object's own dup_symbol
 * (self-priority); r9 = *that = 0x00000000BA5E1A11 -- loaded,
 * dereferenced, and DETACHED (the kernel-side checks verify the slot
 * contents and the registry behavior; r9's read proves the resolved
 * self-address is dereferenceable memory, nothing more).
 */
.section .text
.align 16
.globl cross_payload
cross_payload:
    mov  cross_value@GOTPCREL(%rip), %rax  /* rax = registry-resolved &cross_value (PROVIDER) */
    mov  (%rax), %rbx                      /* rbx = cross_value == 0xACE5F00DD1CE5EED */
    mov  dup_symbol@GOTPCREL(%rip), %r8    /* r8 = &dup_symbol (SELF-priority: THIS object's) */
    mov  (%r8), %r9                        /* r9 = *dup_symbol == 0x00000000BA5E1A11 */
    movabs $0x6C000080000, %rcx            /* PRIVATE_ADDR (matches elf_dyn_cross_demo.rs) */
    mov  %rbx, (%rcx)
    int $0x87                              /* elf_dyn_cross_demo::TRAP_VECTOR */
.hang:
    pause
    jmp .hang
