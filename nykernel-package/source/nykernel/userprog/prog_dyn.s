/*
 * prog_dyn.s -- a real ELF64 SHARED OBJECT (.so), built with the same
 * GNU toolchain as the kernel and every other userprog here
 * (`ld -shared`, see build.sh). This is the increment's subject: a
 * file whose exports are reachable ONLY through the ELF dynamic
 * machinery -- DT_HASH/DT_GNU_HASH symbol tables, DT_STRTAB string
 * table, DT_SYMTAB symbol table, and a .rela.dyn with BOTH
 * symbol-resolved relocation kinds (an R_X86_64_GLOB_DAT against the
 * linker-created .got slot, and an R_X86_64_64 against an absolute
 * .data pointer) against in-object symbols -- and the
 * workload experimental::elf::load + experimental::elf_dyn_demo must
 * handle end to end:
 *
 *   export: dyn_payload -- the entry point whose own code verifies
 *     resolution end to end by dereferencing, IN RING 3, a GOT-STYLE
 *     POINTER SLOT placed in .data (writable, user-reachable) that the
 *     loader's R_X86_64_GLOB_DAT relocation fills with the resolved
 *     address of dyn_value.
 *   export: dyn_value -- a u64 constant in .data the pointer slot must
 *     end up containing (the demo also verifies that fact kernel-
 *     side, before entering ring 3).
 *   export: dyn_timestamp -- .bss (zero-initialized): writable after
 *     load even though the same shard's RELRO pages are not. The demo
 *     writes it through its resolved address (a resolved runtime
 *     address must be a real, mapped, writable address).
 *
 * Behavioral half: dyn_payload dereferences the pointer slot IN .data
 * (the loader's R_X86_64_GLOB_DAT entry in that slot holds the
 * RESOLVED &dyn_value after relocation): if the loader resolved the
 * entry wrong, the loaded code itself dereferences a wrong pointer
 * and produces a wrong value, which the demo checks on the trap. The
 * slot lives in .data deliberately: the honest scope constraint is
 * that ring-3 code consumes the RESOLUTION, not the GOT tables
 * themselves -- GOT-protected pages (the RELRO region) are
 * kernel-side business, exercised by the loader's own revoke pass
 * and observed there, not written from ring 3.
 */

.section .data
.align 8
.globl dyn_value
dyn_value:
    .quad 0xD1CE00F00D        /* the value both the kernel-side and the
                                 program's own dereference must observe */

/* An exported POINTER SLOT in .data, holding an absolute pointer to
 * dyn_value. Uploading `.quad dyn_value` makes the linker emit a
 * REAL R_X86_64_64 (S + A) relocation against dyn_value in the .so's
 * .rela.dyn -- so the file carries BOTH symbol-resolved relocation
 * kinds the loader implements (GLOB_DAT below in the walkthrough,
 * 64 here in .data). The slot's post-relocation content is &dyn_value;
 * the demo verifies that fact kernel-side, before entering ring 3. */
.align 8
.globl dyn_value_ptr
dyn_value_ptr:
    .quad dyn_value

/* A .bss export: writable after load even though the same shard's
 * RELRO pages are not -- the pairing the demo checks. */
.section .bss
.align 8
.globl dyn_timestamp
dyn_timestamp:
    .quad 0

/* The entry point the kernel resolves BY NAME through the SysV
 * DT_HASH walk and enters in ring 3.
 *
 * The load of dyn_value goes through the GOT (`@GOTPCREL`): the
 * FROM-sym@GOTPCREL addressing asks the linker for a GOT slot backed
 * by a real R_X86_64_GLOB_DAT relocation -- which is exactly what
 * `ld -shared` produces for an EXPORTED (preemptible) symbol (it
 * cannot relax the access to a plain RIP-relative lea, because an
 * exported symbol may be interposed). The loader's relocation pass
 * fills that slot with the resolved runtime address of dyn_value;
 * `mov mem, %rax` reads the SLOT (a read of the -- after RELRO
 * revoke -- read-only GOT page is legal from ring 3), and the
 * following `mov (%rax)` dereferences what the loader wrote there.
 * Anything the loader got wrong (bias, symbol index, S value)
 * produces a wrong VALUE on the trap, not just a wrong address. */
.section .text
.align 16
.globl dyn_payload
dyn_payload:
    mov  dyn_value@GOTPCREL(%rip), %rax /* rax = *(&GOT[dyn_value]): the RESOLVED &dyn_value */
    mov  (%rax), %rbx                   /* rbx = *dyn_value == 0xD1CE00F00D */
    movabs $0x64000010000, %rcx         /* PRIVATE_ADDR (matches elf_dyn_demo.rs) */
    mov  %rbx, (%rcx)
    int $0x86                           /* elf_dyn_demo::TRAP_VECTOR */
.hang:
    pause
    jmp .hang
