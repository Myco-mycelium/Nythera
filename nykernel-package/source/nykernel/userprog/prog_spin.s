/*
 * prog_spin.s -- loops forever WITHOUT EVER TRAPPING: increments a
 * counter kept in a register (r12), mirrors it to a private
 * per-address-space memory location, and jumps straight back. There
 * is no `int` instruction anywhere in this program -- the only way
 * for anything else to run is an asynchronous interrupt.
 *
 * experimental::ring3_scheduler loads the SAME compiled binary into
 * two separate address spaces (tasks "H" and "I") and enters the
 * first one with timer preemption armed. Since neither task ever
 * yields, the second task can only ever run -- and the proof can only
 * ever terminate -- if the PIT's timer interrupt (IRQ0) force-
 * switches the CPU away from whatever ring-3 code is running. A
 * kernel whose ring-3 switching is cooperative-only would spin here
 * forever and the boot test would time out, so a passing boot is
 * itself part of the preemption proof (the kernel-side self-test
 * additionally checks both tasks' counters and the exact per-task
 * preemption counts -- see ring3_scheduler::preempt_finale).
 *
 * r12 surviving correctly across forced preemptions is part of the
 * proof too: it's one of the registers the timer path's full-GPR
 * save/restore must preserve across a switch. If that save/restore
 * were buggy, these counters would reset, corrupt, or fail to reach
 * the observed values.
 */

.section .text
.global _start
_start:
    xor %r12, %r12
.loop:
    inc %r12
    movabs $0x40000000000, %rbx   /* matches ring3_scheduler::PRIVATE_ADDR */
    mov %r12, (%rbx)
    jmp .loop
