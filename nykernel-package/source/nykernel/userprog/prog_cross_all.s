/*
 * prog_cross_all.s -- the ALL-IN-ONE DEPENDENT for the full
 * ELF-dynamic-features increment: a real `ld -shared` shared object
 * LINKED against the versioned provider (`ld -shared ...)
 * prog.o lib.so`), which makes the linker record everything a
 * dependent contractually declares:
 *
 *   DT_NEEDED crosslib.so.1 -- the provider's SONAME; a load whose
 *     resolver scope cannot satisfy it is confronted with the typed
 *     `MissingDependency` before any relocation work.
 *   cross_value UNDEF via @GOTPCREL -- an R_X86_64_GLOB_DAT whose
 *     dynsym entry is SHN_UNDEF and (via .gnu.version) requires the
 *     PROVIDER's VERS_1.0: version-checked cross-object resolution.
 *   cross_fn called through a real .plt stub -- an R_X86_64_JUMP_SLOT
 *     entry bound LAZILY at first use (kernel trap 0x88), not at load.
 *   dup_symbol DEFINED HERE -- same name as the provider's export;
 *     the self-priority control (its GOT slot must hold THIS
 *     object's own immediate).
 *
 *   export: all_payload -- entry point. Three lazy-bound calls to
 *     cross_fn, a read of cross_value through its resolved slot,
 *     a read of dup_symbol through its own slot, then the demo's
 *     private page gets [0]=cross_value, [8]/[16]=function results,
 *     and it traps vector 0x89 to report.
 */

.section .data
.align 8
.globl dup_symbol
dup_symbol:
    /* THIS object's own duplicate: distinct from the provider's. */
    .quad 0x00000000BA5E1A11

.section .text
.align 16
.globl all_payload
all_payload:
    movabs $0x6C000300000, %rdi            /* PRIVATE_ADDR (matches elf_dyn_full_demo.rs) */
    call  cross_fn                          /* PLT: lazy JUMP_SLOT; first call traps 0x88 */
    mov   %eax, 8(%rdi)
    call  cross_fn                          /* second call: must take the already-bound path */
    mov   %eax, 16(%rdi)
    call  cross_fn
    mov   %eax, 24(%rdi)
    mov   cross_value@GOTPCREL(%rip), %rax  /* rax = provider's cross_value (version-checked) */
    mov   (%rax), %rbx                      /* rbx = 0xACE5F00DD1CE5EED */
    mov   %rbx, (%rdi)
    mov   dup_symbol@GOTPCREL(%rip), %r8    /* r8 = THIS object's own dup_symbol */
    mov   (%r8), %r9
    int $0x89                               /* elf_dyn_full_demo::TRAP_VECTOR */
.hang:
    pause
    jmp .hang
