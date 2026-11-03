/*
 * prog_svc84.s -- a ring-3 program that CALLS the 0x84 service gate.
 *
 * This is the proof the gate itself cannot make: until this file existed,
 * `int 0x84` had a dispatcher, an entry stub and a boot self-test, but no
 * ring-3 caller -- so "the surface exists" was a claim about kernel code,
 * not about the privilege transition a real program performs.
 *
 * It runs only when the boot line asks for it (`nyrqis.proof=svc84`), in
 * its own address space, and it does five things in order:
 *
 *   1. GFX_MODE      (rax = 0x41)          -> result stored at BUF+0
 *   2. INPUT_STATE   (rax = 0x40)          -> result stored at BUF+8
 *   3. an UNPORTED call (rax = 10, a RELEASE number on purpose) -> BUF+16
 *      (the expected answer is -ENOSYS; a service that answered 0 here
 *       would be indistinguishable from "did nothing", which is exactly
 *       the lie the refusal exists to prevent). Its four WIDENED argument
 *       registers -- rsi, rdx, r10, r8 -- carry the recognisable markers
 *       0x1111/0x2222/0x3333/0x4444, each stored to BUF+40..+64 after the
 *       call returns (the trap preserves and restores them): the kernel
 *       compares what its dispatcher ACTUALLY received against THESE
 *       stored copies, so the stub's widened pass through the trap
 *       boundary is proven with before/after values, not by reading the
 *       assembly and nodding.
 *   4. GFX_MODE_INTO (rax = 0x42, rdi = BUF+24)
 *      -- a POINTER-taking call: the kernel must validate BUF+24 with the
 *      user-range walker before writing 8 bytes there, and it writes the
 *      display geometry into the program's own memory.
 *   5. PROCESS_SELF  (rax = 0x44)          -> result stored at BUF+32
 *      -- the first process-side answer: the running scheduler task's id
 *      (0 = the kernel itself, while no ring-3 task owns the gate). The
 *      kernel's read-back compares it against the scheduler's OWN live
 *      value, so a real session changes both sides together.
 *
 * Then it asks the kernel to close the proof (rax = 0x43, rdi = BUF): the
 * kernel reads the five stored results back OUT OF USER MEMORY and
 * compares them with what it returned, so the check covers the round trip
 * through ring-3 writes rather than trusting the registers alone.
 *
 * The served calls are numbered in the surface's OWN range, 0x40..: the
 * release's ledger spans 0..28 (yield=0, SEND=1, RECV=2, exit=3,
 * spawn=4, ...), and the first version's numbering collided at every
 * served call -- a release-typed program's yield would have been
 * ANSWERED, not refused -- which is why these are 0x40-plus and why
 * tools/check_svc84_ledger.py refuses any drift back.
 *
 * The program then parks in a spin loop. It deliberately never returns to
 * the kernel: this is a proof mode, and the boot ends when the gate reads
 * the verdict off the serial console.
 */

/* Derived from the linker script's load address, not typed out again: the
   first version of this file hand-wrote the page address and got the digit
   count wrong, so the program wrote one page BELOW what the kernel had
   mapped and faulted on its first instruction. Expressing it as ANCHOR +
   offset makes that class of mistake impossible to repeat here. */
.set ANCHOR, 0x60000000000
.set BUF,    ANCHOR + 0x10000

.section .text
.global _start
_start:
    /* The kernel maps BUF writable + user-accessible (svc84_proof::PROOF_BUF
       is the same expression). */
    movabs $BUF, %rbx

    /* 1. GFX_MODE (0x41) */
    mov $0x41, %rax
    int $0x84
    mov %rax, 0(%rbx)

    /* 2. INPUT_STATE (0x40) */
    mov $0x40, %rax
    int $0x84
    mov %rax, 8(%rbx)

    /* 3. an unported call -- must be refused, not answered. The four
       widened argument registers carry recognisable markers; what the
       kernel's dispatcher SAW is what the read-back is judged against.
       rsi/rdx/r10/r8 are saved and restored across the trap, so the
       stores after the int capture exactly what the program sent. */
    mov $0x1111, %rsi
    mov $0x2222, %rdx
    mov $0x3333, %r10
    mov $0x4444, %r8
    mov $10, %rax
    int $0x84
    mov %rax, 16(%rbx)
    mov %rsi, 40(%rbx)
    mov %rdx, 48(%rbx)
    mov %r10, 56(%rbx)
    mov %r8, 64(%rbx)

    /* 4. GFX_MODE_INTO (0x42): the validated-write path */
    mov %rbx, %rdi
    add $24, %rdi
    mov $0x42, %rax
    int $0x84

    /* 5. PROCESS_SELF (0x44): the first process-side answer -- the running
       scheduler task's id (0 = the kernel itself; nothing owns this gate
       yet). The stored copy is what the kernel reads back. */
    mov $0x44, %rax
    int $0x84
    mov %rax, 32(%rbx)

    /* Close the proof (0x43): the kernel reads BUF back through the
       validator. */
    mov %rbx, %rdi
    mov $0x43, %rax
    int $0x84

.hang:
    pause
    jmp .hang
