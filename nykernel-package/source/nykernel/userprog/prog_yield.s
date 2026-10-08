/*
 * prog_yield.s -- loops forever: increments a counter kept purely in
 * a register (r12), writes it to a private per-address-space memory
 * location, then traps via `int 0x81` (a NEW, dedicated vector --
 * distinct from 0x80, which experimental::usermode/isolation_demo use
 * for their one-shot report-and-halt traps; 0x81 means "yield back to
 * the scheduler and expect to be resumed later").
 *
 * The SAME compiled binary is loaded into two separate address spaces
 * by experimental::ring3_scheduler (tasks "E" and "F") -- isolation
 * between them is proven by each one's own private memory correctly
 * reaching its own expected count independently, exactly as in
 * experimental::isolated_tasks, but now for genuine ring-3 code
 * suspended and resumed via a real trap-based context switch instead
 * of kernel-mode cooperative yielding.
 *
 * r12 surviving correctly across many suspend/resume cycles is itself
 * part of the proof: it's one of the registers the yield trap handler
 * must save and restore. If that save/restore were buggy, this
 * counter would reset, corrupt, or diverge between the two tasks in a
 * way the kernel-side self-test would catch.
 */

.section .text
.global _start
_start:
    xor %r12, %r12
.loop:
    inc %r12
    movabs $0x40000000000, %rbx   /* matches ring3_scheduler::PRIVATE_ADDR */
    mov %r12, (%rbx)
    int $0x81
    jmp .loop
