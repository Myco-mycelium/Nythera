/*
 * prog_svc84.s -- a ring-3 program that CALLS the 0x84 service gate.
 *
 * This is the proof the gate itself cannot make: until this file existed,
 * `int 0x84` had a dispatcher, an entry stub and a boot self-test, but no
 * ring-3 caller -- so "the surface exists" was a claim about kernel code,
 * not about the privilege transition a real program performs.
 *
 * It runs only when the boot line asks for it (`nyrqis.proof=svc84`), in
 * its own address space, and it does seven things in order:
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
 *   6. the IPC rows, exercising THE KERNEL'S REAL MAILBOX PRIMITIVE
 *      (experimental::ipc) end to end:
 *        a. IPC_SEND (rax = 0x45, rdi = 0 endpoint, rsi = 7 seq)
 *           -> ack stored at BUF+72. The kernel must queue
 *           (sender = current task, seq = 7) through the primitive.
 *        b. IPC_RECV (rax = 0x46, rdi = 0) -> value stored at BUF+80.
 *           The expected answer is (sender << 32) | seq = 7 (sender is
 *           0 here, not zero-by-default): proving ONE SENT message came
 *           back WHOLE, from KERNEL memory across the trap boundary.
 *        c. IPC_RECV with an unknown dial (r10 = 2) -> stored at
 *           BUF+88; expected -EINVAL (a shape the primitive cannot
 *           own).
 *        d. IPC_RECV on the now-EMPTY endpoint 0 -> stored at BUF+96;
 *           expected -ENODATA -- the real-and-empty answer, NOT a
 *           refusal. (The nonexistent-endpoint case, -ENOENT, is asserted
 *           by the boot self-test, whose zero-table state IS the
 *           nonexistent case.)
 *      The read-back compares BUF+80 against what the drain actually
 *      unpacked, so the queue/unpack path through kernel memory is
 *      proven with values, not assumptions.
 *   7. the CAPABILITY rows, exercising THE KERNEL'S REAL HANDLE-TABLE
 *      MACHINE (experimental::caps) end to end. The boot equip granted
 *      THIS PROGRAM'S GATE SESSION (its identity on these rows, recorded
 *      at the ring transition) endpoint 0 with a SEND|GRANT capability
 *      at slot 1 of the session's OWN table -- slot 0 holds the
 *      endpoint's owner cap, the mint lands after it -- and the
 *      kernel-side check panics on anything else, so the hardcoded
 *      handle/endpoint pair below names the SAME granted capability:
 *        a. CAP_SEND (0x47, rdi = HANDLE 1, rsi = payload, rdx = 6)
 *           -> ack stored at BUF+104. The kernel must COPY the payload
 *           out of THIS program's memory into the queue (kernel bytes
 *           only -- a stale pointer can never be dereferenced at receive
 *           time) and queue it tagged with the kernel-declared caller identity (the gate session's id).
 *        b. CAP_RECV (rax = 0x48, rdi = ENDPOINT 0, rsi = BUF+192,
 *           rdx = 8) -> packed (sender << 32) | len stored at BUF+112
 *           (expected 6), and the payload bytes written at BUF+192.
 *           "nyrqis" is 6 bytes, so the first-8 read of BUF+192 is the
 *           sentinel zero-tailed -- what the kernel's queue record must
 *           match, value-for-value, not constant-for-constant.
 *        c. CAP_SEND through a FORGED handle (rdi = 31, an empty slot of
 *           the same table) -> stored at BUF+120; expected -EBADF.
 *        d. CAP_SEND claiming a 65-byte payload -> stored at BUF+128;
 *           expected -EMSGSIZE (the bound, refused before anything is
 *           queued -- the release's backpressure, not truncation).
 *   8. the INPUT_POLL row, exercising THE KERNEL'S REAL INPUT KIND
 *      (experimental::caps::Kind::Input) end to end. The boot equip
 *      minted this program's session an Input capability at slot 2 of
 *      its OWN table (READ|GRANT) and drove TWO real scancodes through
 *      the controller's output buffer (0x1E then 0x9E -- the 0xD2
 *      command, each byte raising IRQ1 exactly as a keystroke would)
 *      before ring 3, so the polls are deterministic:
 *        a. INPUT_POLL (0x49, rdi = HANDLE 2, rsi = BUF+208) -> ack
 *           stored at BUF+136; expected 1, and the 16-byte record at
 *           BUF+208 is {0x1E, 0, 0, 0, 0, 0, 0, 0, ticks LE...} -- the
 *           raw byte exactly as the injected stream delivered it, nothing
 *           decoded in the kernel.
 *        b. INPUT_POLL again (record BUF+224) -> BUF+144; expected 1
 *           with byte 0x9E in the record.
 *        c. INPUT_POLL a THIRD time (record pointer BUF+240: it MUST be
 *           a valid record pointer even on this empty path -- the
 *           validator runs after the authority check, and a null here
 *           is refused EFAULT, the proof's first RUN caught exactly
 *           that) -> BUF+152; expected 0, and the record buffer at
 *           BUF+240 must still be all ZERO: the empty path writes
 *           NOTHING (a poll that "returned empty" but scribbled into
 *           the caller's record would be a lie).
 *        d. INPUT_POLL through a FORGED handle (31, an empty slot of
 *           the same table) -> stored at BUF+168; expected -EBADF, and
 *           NO byte was consumed -- the authority-first ordering.
 *
 * Then it asks the kernel to close the proof (rax = 0x43, rdi = BUF): the
 * kernel reads the seventeen stored results back OUT OF USER MEMORY and
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

    /* 6. The IPC rows. Every argument except the one a call consumes is
       ZEROED before it: the trap restores caller registers, so the junk
       markers step 3 left in rsi/rdx/r10/r8 would otherwise be read as
       shape claims and (wrongly) refused before anything is sent. That
       is exactly the class of call-site bug a proof should catch -- and
       the first run of this program lost the whole round trip to it. */
    /* 6a. IPC_SEND (0x45): endpoint 0, seq 7 -> ack at BUF+72. The
       transfer register carries the release's OWN "no transfer" claim
       (u64::MAX): a zero there is NOT "no transfer" on this row, it is
       a shape this primitive does not own every other value of, and
       sending 0 in r10 is refused EINVAL (the first run of this
       program tripped exactly that). */
    xor %rdi, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    xor %r10, %r10
    xor %r8, %r8
    mov $7, %rsi
    movabs $-1, %r10
    mov $0x45, %rax
    int $0x84
    mov %rax, 72(%rbx)
    /* 6b. IPC_RECV (0x46): drain endpoint 0 -> (sender << 32) | 7 at
       BUF+80, proving the queued message came back WHOLE. (%rsi still
       holds 6a's seq -- it must be re-zeroed or the RECV is refused.) */
    xor %rdi, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    xor %r10, %r10
    xor %r8, %r8
    mov $0x46, %rax
    int $0x84
    mov %rax, 80(%rbx)
    /* 6c. An UNKNOWN DIAL must be refused: r10 = 2 is neither the plain
       drain (0) nor the release's blocking form (1). (This leg used to
       claim a payload pointer in rdx and expect EINVAL; a2 != 0 is a
       SERVED dial now -- the release's payload RECV -- so the shape
       refusal this slot pins is the unknown a_xfer.) */
    xor %rdi, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    mov $2, %r10
    xor %r8, %r8
    mov $0x46, %rax
    int $0x84
    mov %rax, 88(%rbx)
    /* 6d. Endpoint 0 is now EMPTY -- real and empty -- so the honest
       answer is -ENODATA, the distinct ("exists, but bare now") value,
       NOT a refusal. The NONEXISTENT case (-ENOENT) is asserted by the
       kernel's own boot self-test on this call, whose zero-state IS the
       nonexistent-table state; one refused-value slot here (BUF+96) is
       where that difference is pinned. The shape claim 6c left in
       %rdx MUST be cleared: the trap preserves caller registers, so a
       leftover dial would be answered EINVAL, not ENODATA, and the
       proof would catch the mistake -- 6c set %r10, so this clears it
       (and %rdx, which the payload legs above share). */
    xor %rdi, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    xor %r10, %r10
    mov $0x46, %rax
    int $0x84
    mov %rax, 96(%rbx)

    /* 7. The capability rows (experimental::caps). Identity on these rows
       is THIS program's gate session (recorded at the ring transition)
       -- its OWN capability table -- and the boot equip put a SEND|GRANT
       cap for endpoint 0 at slot 1 of that table. Zero what a call does
       not consume, for the same trap-preservation reason step 6
       documents. */
    /* 7a. Stage the payload "nyrqis" + 2 zero bytes (little-endian qword:
           n=0x6E y=0x79 r=0x72 q=0x71 i=0x69 s=0x73) at BUF+160, and
           point CAP_SEND's payload pointer there. */
    movabs $0x000073697172796E, %rcx
    mov %rcx, 160(%rbx)
    /* 7b. CAP_SEND (0x47): handle 1, payload BUF+160 len 6, no transfer
       (r10 = u64::MAX -- zero is NOT "no transfer" on this row).
       Expected ack 0. */
    mov $1, %rdi
    lea 160(%rbx), %rsi
    mov $6, %rdx
    xor %r8, %r8
    movabs $-1, %r10
    mov $0x47, %rax
    int $0x84
    mov %rax, 104(%rbx)
    /* 7c. CAP_RECV (0x48): ENDPOINT 0, receive buffer BUF+352 claiming
       8 bytes (>= the queued 6). Expected (sender << 32) | 6, and the
       payload bytes land at BUF+352. The tail is zeroed FIRST: this
       freshly mapped page is not guaranteed zero-filled physical
       memory, and the kernel's read-back reads BUF+352..360 as one
       qword -- replicating payload bytes 6 and 7 as garbage would make
       a correct kernel's copy MISMATCH by itself. (The buffer moved
       from BUF+192 to BUF+352 on 2026-11-05: the blocking-RECV legs'
       stores took over 192..208.) */
    movabs $0, %rcx
    mov %rcx, 352(%rbx)
    xor %rdi, %rdi
    lea 352(%rbx), %rsi
    mov $8, %rdx
    xor %r8, %r8
    movabs $-1, %r10
    mov $0x48, %rax
    int $0x84
    mov %rax, 112(%rbx)
    /* 7d. A FORGED handle (31: a real index, an empty slot of the same
       table) must be refused -EBADF, not answered with zero. */
    mov $31, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    xor %r8, %r8
    movabs $-1, %r10
    mov $0x47, %rax
    int $0x84
    mov %rax, 120(%rbx)
    /* 7e. An oversize payload claim (65 > the kernel's 64) must be
       refused -EMSGSIZE BEFORE anything is queued. */
    mov $1, %rdi
    lea 160(%rbx), %rsi
    mov $65, %rdx
    xor %r8, %r8
    movabs $-1, %r10
    mov $0x47, %rax
    int $0x84
    mov %rax, 128(%rbx)

    /* 8. The INPUT_POLL row (0x49). The boot equip minted an Input
       capability at slot 2 of the session's own table and drove the raw
       stream with 0x1E then 0x9E through the controller (IRQ1). Zero
       what a call does not consume, for the trap-preservation reason
       documented at step 6. */
    /* 8a. poll #1: handle 2, record at BUF+208 (tail zeroed first: a
       freshly mapped page is not guaranteed zero) -> rax 1 at BUF+136. */
    movabs $0, %rcx
    mov %rcx, 208(%rbx)
    mov %rcx, 216(%rbx)
    mov $2, %rdi
    lea 208(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x49, %rax
    int $0x84
    mov %rax, 136(%rbx)
    /* 8b. poll #2: record at BUF+224 -> rax 1 (byte 0x9E) at BUF+144. */
    movabs $0, %rcx
    mov %rcx, 224(%rbx)
    mov %rcx, 232(%rbx)
    mov $2, %rdi
    lea 224(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x49, %rax
    int $0x84
    mov %rax, 144(%rbx)
    /* 8c. poll #3: the queue is bare and the caller is authorized ->
       0 at BUF+152 -- the real-and-empty answer, not a refusal. The
       record pointer VALID (BUF+240) even though nothing is written: a
       null here is refused EFAULT first (the first run proved it), and
       the kernel must leave BUF+240 all-zero on this empty path. */
    movabs $0, %rcx
    mov %rcx, 240(%rbx)
    mov %rcx, 248(%rbx)
    mov $2, %rdi
    lea 240(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x49, %rax
    int $0x84
    mov %rax, 152(%rbx)
    /* 8d. a FORGED handle (31, an empty slot) must be refused -EBADF
       at BUF+168, and no byte consumed: the authority-first ordering. */
    mov $31, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x49, %rax
    int $0x84
    mov %rax, 168(%rbx)

    /* 9. The IPC row's BLOCKING form (0x46 with r10 = 1) -- the
       released 0x46 row's blocking dial, now SERVED (a budget, never a
       parked wait: see the kernel's arm docs for why). Every leg
       zero-tail-checks nothing else (answers land directly in rax). */
    /* 9a. SEND to endpoint 0 (seq 42) so the blocking recv has a real
       message: ack 0 at BUF+176. Carry-through zeros r10 = u64::MAX as
       the "no transfer" claim and r8 as the row requires. */    xor %rdi, %rdi
    movabs $42, %rsi
    xor %r8, %r8
    movabs $-1, %r10
    mov $0x45, %rax
    int $0x84
    mov %rax, 176(%rbx)
    /* 9b. BLOCKING RECV (dest 0, budget 1): the queued message answers
       immediately with (sender << 32) | 42 = 0x70000002a (the sender is
       the kernel-declared caller identity -- the recorded gate session,
       7). Stored at BUF+184; the kernel records ITS OWN copy of the
       answer and compares both sides in PROOF_DONE. */
    xor %rdi, %rdi
    movabs $1, %rsi
    xor %rdx, %rdx
    mov $1, %r10
    xor %r8, %r8
    mov $0x46, %rax
    int $0x84
    mov %rax, 184(%rbx)
    /* 9c. BLOCKING RECV on the now-empty endpoint: refused -EAGAIN by
       name -- the budget is spent, but a session's PARK would hang
       (no waker exists for a session id), so the answer is a refusal,
       never a hang. Stored at BUF+192 (the CAP buffer moved to
       BUF+352). */
    xor %rdi, %rdi
    movabs $1, %rsi
    xor %rdx, %rdx
    mov $1, %r10
    xor %r8, %r8
    mov $0x46, %rax
    int $0x84
    mov %rax, 192(%rbx)
    /* 9d. BLOCKING RECV on a nonexistent endpoint: refused -ENOENT --
       the shape/budget/destination checks happen in that order, so the
       destination check outranks the park attempt entirely. Stored at
       BUF+200. */
    movabs $0x4000, %rdi
    movabs $1, %rsi
    xor %rdx, %rdx
    mov $1, %r10
    xor %r8, %r8
    mov $0x46, %rax
    int $0x84
    mov %rax, 200(%rbx)

    /* 10. The IPC rows' PAYLOAD dials (2026-11-06) -- the release's
       SEND(handle, buf, len) / RECV(idx, buf, buflen) half. The
       difference between this and step 9 is ONE register: a2. Zero
       keeps the surface's (sender, seq) form; a whole-message size
       makes a1 a user BUFFER and a2 its length, and RECV answers the
       BYTE COUNT with the bytes in the caller's buffer. */
    /* 10a. SEND a payload: endpoint 0, buffer BUF+368, 8 bytes
       ("payload!"), no transfer (r10 = u64::MAX), no rights (r8 = 0).
       Expected ack 0 at BUF+448. */
    movabs $0x2164616f6c796170, %rcx   /* "payload!" little-endian */
    mov %rcx, 368(%rbx)
    xor %rdi, %rdi
    lea 368(%rbx), %rsi
    mov $8, %rdx
    xor %r8, %r8
    movabs $-1, %r10
    mov $0x45, %rax
    int $0x84
    mov %rax, 448(%rbx)
    /* 10b. RECV the payload: endpoint 0, buffer BUF+384, buflen 64 (the
       release's floor -- a smaller buffer is refused, 10f). Expected 8
       at BUF+456. RECV's r10 is its DIAL, not the SEND-side "no
       transfer" claim: 0 is the plain drain. (-1 there would be an
       unknown dial, EINVAL -- the first run of these legs was refused
       exactly that way.) */
    xor %rdi, %rdi
    lea 384(%rbx), %rsi
    mov $64, %rdx
    xor %r10, %r10
    xor %r8, %r8
    mov $0x46, %rax
    int $0x84
    mov %rax, 456(%rbx)
    /* 10c. Read the landed BYTES back out of the program's own memory
       (a kernel that answered 8 and copied nothing would pass 10b
       alone). Expected "payload!" at BUF+464. */
    mov 384(%rbx), %rcx
    mov %rcx, 464(%rbx)
    /* 10d. A payload WIDER than one message (65 > 64) is refused
       -EMSGSIZE before any queue is touched. BUF+472. */
    xor %rdi, %rdi
    lea 368(%rbx), %rsi
    mov $65, %rdx
    xor %r8, %r8
    movabs $-1, %r10
    mov $0x45, %rax
    int $0x84
    mov %rax, 472(%rbx)
    /* 10e. A payload at a NULL pointer is refused -EFAULT by the
       user-range validator (never dereferenced first). BUF+480. */
    xor %rdi, %rdi
    xor %rsi, %rsi
    mov $8, %rdx
    xor %r8, %r8
    movabs $-1, %r10
    mov $0x45, %rax
    int $0x84
    mov %rax, 480(%rbx)
    /* 10f. A receive buffer smaller than one message (32 < 64) is
       refused -EINVAL -- the release's buflen floor, refused by name
       BEFORE the queue is looked at. BUF+488. */
    xor %rdi, %rdi
    lea 384(%rbx), %rsi
    mov $32, %rdx
    xor %r10, %r10
    xor %r8, %r8
    mov $0x46, %rax
    int $0x84
    mov %rax, 488(%rbx)

    /* Close the proof (0x43): the kernel reads BUF back through the
       validator. */
    mov %rbx, %rdi
    mov $0x43, %rax
    int $0x84

.hang:
    pause
    jmp .hang
