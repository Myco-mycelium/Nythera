/*
 * prog_pe_import_app.s -- the DEPENDENT half of the PE32+ import
 * resolution proof: a real native-subsystem PE32+ executable whose
 * import table is produced by GNU ld itself (linking DIRECTLY against
 * the provider DLL -- no dlltool, no import library needed on this
 * host: ld synthesizes the client's .idata). `__imp_X` references are
 * what make the imports; the code is the faithful Windows pattern:
 *
 *   mov __imp_crossmark(%rip), %rax   -- load the IAT SLOT value (the
 *                                        resolved export address)
 *   mov (%rax), %rbx                  -- dereference it: the provider's
 *                                        own crossmark data
 *   call *__imp_crossfn(%rip)         -- indirect call THROUGH the IAT
 *                                        slot -- the provider's code
 *
 * Results land in two fixed slots the kernel reads back after the
 * `int $0x8D` trap (the same report pattern every pe_* demo uses; 0x8C is the
 * plt-demo completion gate, so this chain link took 0x8D).
 */
    .text
    .global _start
_start:
    mov __imp_crossmark(%rip), %rax
    mov (%rax), %rbx
    movabs $SLOT_A, %rdi
    mov %rbx, (%rdi)
    call *__imp_crossfn(%rip)
    movabs $SLOT_B, %rdi
    mov %rax, (%rdi)
    int $0x8D
.hang:
    pause
    jmp .hang

.set SLOT_A, 0x400000000000
.set SLOT_B, 0x400000000008
