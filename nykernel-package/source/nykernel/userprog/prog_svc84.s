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
 *   1. GFX_MODE      (rax = 1)             -> result stored at BUF+0
 *   2. INPUT_STATE   (rax = 0)             -> result stored at BUF+8
 *   3. an UNPORTED call (rax = 10)         -> result stored at BUF+16
 *      (the expected answer is -ENOSYS; a service that answered 0 here
 *       would be indistinguishable from "did nothing", which is exactly
 *       the lie the refusal exists to prevent)
 *   4. GFX_MODE_INTO (rax = 2, rdi = BUF+24)
 *      -- a POINTER-taking call: the kernel must validate BUF+24 with the
 *      user-range walker before writing 8 bytes there, and it writes the
 *      display geometry into the program's own memory.
 *   5. PROCESS_SELF  (rax = 4)             -> result stored at BUF+32
 *      -- the first process-side answer: the running scheduler task's id
 *      (0 = the kernel itself, while no ring-3 task owns the gate). The
 *      kernel's read-back compares it against the scheduler's OWN live
 *      value, so a real session changes both sides together.
 *
 * Then it asks the kernel to close the proof (rax = 3, rdi = BUF): the
 * kernel reads the five stored results back OUT OF USER MEMORY and
 * compares them with what it returned, so the check covers the round trip
 * through ring-3 writes rather than trusting the registers alone.
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

    /* 1. GFX_MODE */
    mov $1, %rax
    int $0x84
    mov %rax, 0(%rbx)

    /* 2. INPUT_STATE */
    mov $0, %rax
    int $0x84
    mov %rax, 8(%rbx)

    /* 3. an unported call -- must be refused, not answered */
    mov $10, %rax
    int $0x84
    mov %rax, 16(%rbx)

    /* 4. GFX_MODE_INTO: the validated-write path */
    mov %rbx, %rdi
    add $24, %rdi
    mov $2, %rax
    int $0x84

    /* 5. PROCESS_SELF: the first process-side answer -- the running
       scheduler task's id (0 = the kernel itself; nothing owns this gate
       yet). The stored copy is what the kernel reads back. */
    mov $4, %rax
    int $0x84
    mov %rax, 32(%rbx)

    /* Close the proof: the kernel reads BUF back through the validator. */
    mov %rbx, %rdi
    mov $3, %rax
    int $0x84

.hang:
    pause
    jmp .hang
