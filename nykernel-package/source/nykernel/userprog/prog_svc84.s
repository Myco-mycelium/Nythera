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

    /* 11. The release's CALL/REPLY pair (2026-11-06) -- the Reply
       capability KIND. A call is a send whose message carries a
       one-shot right to answer it; the receiver learns the handle from
       RECV's out-pointer, answers ONCE, and the answer comes back at
       the caller's own endpoint. Everything here is on the same
       endpoint 0 and the same session as step 7: the caller and the
       server are one holder, which is all a single-session proof can
       be, and every leg below is still the real machine.
       Results: ack BUF+496, call-recv byte count BUF+504, handle
       out-slot BUF+512, handle read back BUF+520, call bytes BUF+528,
       reply ack BUF+536, second-reply refusal BUF+544, kind refusal
       BUF+552, answer byte count BUF+560, answer bytes BUF+568.
       Staging: call payload BUF+576, call buffer BUF+592, answer
       payload BUF+656, answer buffer BUF+672. */
    /* 11a. CALL (0x4a): HANDLE 1 (the SEND|GRANT endpoint capability),
       8-byte "callme!!" request, no transfer (r10 = u64::MAX), no
       rights (r8 = 0). The answer is the endpoint's OWNER. */
    movabs $0x2121656d6c6c6163, %rcx   /* "callme!!" little-endian */
    mov %rcx, 576(%rbx)
    mov $1, %rdi
    lea 576(%rbx), %rsi
    mov $8, %rdx
    xor %r8, %r8
    movabs $-1, %r10
    mov $0x4a, %rax
    int $0x84
    mov %rax, 496(%rbx)
    /* 11b. RECV the call. r8 is NOT a rights mask here: it is the
       OUT-POINTER for the transferred handle (BUF+512). Expected 8
       bytes at BUF+504, the request at BUF+592. */
    xor %rdi, %rdi
    lea 592(%rbx), %rsi
    mov $64, %rdx
    movabs $-1, %r10          /* no transfer CLAIM: the machine mints
                                 the reply capability itself */
    lea 512(%rbx), %r8
    mov $0x48, %rax
    int $0x84
    mov %rax, 504(%rbx)
    /* 11c. The handle the kernel installed, read back out of the
       program's own memory (a kernel that reported a transfer and
       wrote nothing would leave the sentinel -1 there). */
    mov 512(%rbx), %rcx
    mov %rcx, 520(%rbx)
    /* 11d. The request's bytes, read back out of BUF+592. */
    mov 592(%rbx), %rcx
    mov %rcx, 528(%rbx)
    /* 11e. REPLY (0x4b) through that handle: 8-byte "answer!!",
       expected ack 0. */
    movabs $0x2121726577736e61, %rcx   /* "answer!!" little-endian */
    mov %rcx, 656(%rbx)
    mov 520(%rbx), %rdi
    lea 656(%rbx), %rsi
    mov $8, %rdx
    xor %r8, %r8
    movabs $-1, %r10
    mov $0x4b, %rax
    int $0x84
    mov %rax, 536(%rbx)
    /* 11f. The SAME handle again: one-shot, so the slot is empty and
       the answer must be -EBADF (not a second reply). BUF+544. */
    mov 520(%rbx), %rdi
    lea 656(%rbx), %rsi
    mov $8, %rdx
    xor %r8, %r8
    movabs $-1, %r10
    mov $0x4b, %rax
    int $0x84
    mov %rax, 544(%rbx)
    /* 11g. REPLY through the ENDPOINT handle (1): the kind field
       refuses with -EACCES -- answering is not sending. BUF+552. */
    mov $1, %rdi
    lea 656(%rbx), %rsi
    mov $8, %rdx
    xor %r8, %r8
    movabs $-1, %r10
    mov $0x4b, %rax
    int $0x84
    mov %rax, 552(%rbx)
    /* 11h. RECV the ANSWER at endpoint 0 (no out-pointer this time:
       r8 = 0 is the common shape). Expected 8 bytes at BUF+560 with
       "answer!!" at BUF+672, read back at BUF+568. */
    xor %rdi, %rdi
    lea 672(%rbx), %rsi
    mov $64, %rdx
    movabs $-1, %r10          /* again: no transfer claim */
    xor %r8, %r8
    mov $0x48, %rax
    int $0x84
    mov %rax, 560(%rbx)
    mov 672(%rbx), %rcx
    mov %rcx, 568(%rbx)

    /* 12. The release's SHARED-MEMORY kind (2026-11-06). The kernel
       mapped one page and wrote a sentinel into it BEFORE ring 3 began;
       the region capability (handle 3) is the authority to be TOLD where
       that mapping is. Both sides then touch the same bytes: the program
       reads the kernel's sentinel and writes its own value, which the
       kernel reads back after the proof closes.
       Results: ack BUF+760, base BUF+792, len BUF+800, rights BUF+808,
       the sentinel read THROUGH the mapping BUF+816, the value written
       back BUF+824, the forged-handle refusal BUF+832, the wrong-kind
       refusal BUF+840. Record (24 bytes) at BUF+768. */
    /* 12a. SHM_GEOMETRY (0x4c): region handle 3, record at BUF+768. */
    mov $3, %rdi
    lea 768(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4c, %rax
    int $0x84
    mov %rax, 760(%rbx)
    /* 12b. The record's three fields, read back out of the program's own
       memory: base BUF+792, length BUF+800, rights BUF+808. */
    mov 768(%rbx), %rcx
    mov %rcx, 792(%rbx)
    mov 776(%rbx), %rcx
    mov %rcx, 800(%rbx)
    mov 784(%rbx), %rcx
    mov %rcx, 808(%rbx)
    /* 12c. Read the KERNEL's sentinel through the mapping the record
       names -- the address is taken from the record, not from a constant,
       so a kernel that reported the wrong base fails here. */
    mov 792(%rbx), %rcx
    mov (%rcx), %rcx
    mov %rcx, 816(%rbx)
    /* 12d. Write OUR value through the same mapping (offset 8), which the
       kernel reads back out of the region after the proof. */
    movabs $0xfeedfacedeadbeef, %rcx
    mov 792(%rbx), %rdx
    mov %rcx, 8(%rdx)
    mov %rcx, 824(%rbx)
    /* 12e. A handle that names nothing: -EBADF at BUF+832. */
    movabs $9999, %rdi
    lea 768(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4c, %rax
    int $0x84
    mov %rax, 832(%rbx)
    /* 12f. A handle of ANOTHER kind (the endpoint cap at 1) asked for a
       region's geometry: -EACCES (WrongType) at BUF+840. */
    mov $1, %rdi
    lea 768(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4c, %rax
    int $0x84
    mov %rax, 840(%rbx)

    /* 13. TWO PROCESSES (2026-11-06): the region's LIFETIME, and a
       blocking CALL that parks THIS program while a DIFFERENT ring-3
       process answers it.

       The capability table now holds (see svc84_proof's equip):
         0 the endpoint's owner cap, 1 SEND cap, 2 INPUT cap,
         3 region cap, 4 a SECOND region cap, 5 SEND to the peer's
         endpoint, 6 the one-shot reply cap step 11 was handed.
       The peer (prog_svc84_peer.s, its own scheduler task) holds its own
       endpoint and its own grant of the same region.

       Results: close-3 ack BUF+848, geometry record BUF+856 (base 880,
       length 888, rights 896), the call's answer buffer BUF+904, the
       call's byte count BUF+936, the request BUF+944, the answer as read
       back BUF+952, the region's offset 8 re-read BUF+960, the PEER's
       offset 16 BUF+968, close-4 ack BUF+976, the second close at
       BUF+984, the geometry ack at BUF+992, the go send's ack at
       BUF+1000. */
    /* 13a. SHM_CLOSE (0x4d) on handle 3: ONE of this process's two region
       capabilities goes back. The region is NOT torn down -- handle 4 and
       the peer's grant still name it. */
    mov $3, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4d, %rax
    int $0x84
    mov %rax, 848(%rbx)
    /* 13b. Geometry through the OTHER handle (4): the record still
       answers, and its base is the one the program then dereferences. */
    mov $4, %rdi
    lea 856(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4c, %rax
    int $0x84
    mov %rax, 992(%rbx)
    mov 856(%rbx), %rcx          /* record's base */
    mov %rcx, 880(%rbx)
    mov 864(%rbx), %rcx          /* record's length */
    mov %rcx, 888(%rbx)
    mov 872(%rbx), %rcx          /* record's rights */
    mov %rcx, 896(%rbx)
    /* 16. A REGION WE ASKED FOR, HANDED TO THE OTHER PROCESS
       (0x4f -> 0x47 -> 0x4d, 2026-11-07). The requested region's
       capability travels in the DEDICATED TRANSFER FIELD of a CAP_SEND
       through handle 5 (SEND to the peer's endpoint), attenuated to
       READ|WRITE -- GRANT is deliberately left behind, so the receiver
       cannot pass it on again.

       The close below is the point of the whole step: this process gives
       its OWN capability back while the copy is still queued, and the
       region must SURVIVE that. The queued copy counts as a holder, so
       the pages stay mapped -- which the immediate read-back through the
       base proves from this side, and the peer's later read proves from
       the other.

       record BUF+2304 (32 bytes), payload "xfer" BUF+2336. Results from
       BUF+2344, nine qwords: request ack, base, handle, length,
       transfer ack, close ack, the marker read back AFTER the close, the
       consumed handle's geometry refusal, and the WIDER ask's refusal
       (BUF+2408, the ninth) -- the attenuation negative control. */
    /* 16a. ASK for one page. */
    mov $4096, %rdi
    lea 2304(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4f, %rax
    int $0x84
    mov %rax, 2344(%rbx)
    mov 2304(%rbx), %rcx
    mov %rcx, 2352(%rbx)         /* base the KERNEL chose */
    mov 2312(%rbx), %rcx
    mov %rcx, 2368(%rbx)         /* length */
    mov 2328(%rbx), %rcx
    mov %rcx, 2360(%rbx)         /* handle the kernel minted */
    /* 16b. Our marker through the mapping (the peer reads it after we let
       go, the kernel's releaser reads it at the very end). */
    movabs $0xfeedfacecafed00d, %rcx
    mov 2352(%rbx), %rdx
    mov %rcx, (%rdx)
    /* 16c0. THE WIDER ASK, REFUSED BY NAME. The same handover, but asking
       for ALL four rights (0x0f) -- SEND is one this process never held,
       so attenuation cannot manufacture it. The machine must answer
       EMSGSIZE here and touch no queue: the drain below receives the ONE
       good message, attenuated to READ|WRITE, which is what makes this a
       refusal rather than a silent widening. */
    mov $5, %rdi
    lea 2336(%rbx), %rsi
    xor %rdx, %rdx               /* no payload: this send must not queue */
    mov 2360(%rbx), %r10
    mov $0x0f, %r8               /* ALL -- includes SEND */
    mov $0x47, %rax
    int $0x84
    mov %rax, 2408(%rbx)
    /* 16c. HAND IT ON. a_xfer (r10) is the capability, a_rights (r8) the
       attenuated mask: READ|WRITE = 0x0c. */
    movabs $0x0000000072656678, %rcx   /* "xfer" little-endian */
    mov %rcx, 2336(%rbx)
    mov $5, %rdi
    lea 2336(%rbx), %rsi
    mov $4, %rdx
    mov 2360(%rbx), %r10
    mov $0x0c, %r8
    mov $0x47, %rax
    int $0x84
    mov %rax, 2376(%rbx)
    /* 16d. Our OWN close, with the copy still in flight: the region must
       not be torn down here. */
    mov 2360(%rbx), %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4d, %rax
    int $0x84
    mov %rax, 2384(%rbx)
    /* 16e. ...and the page is STILL mapped: our own marker, read through
       the base after our capability is gone. */
    mov 2352(%rbx), %rdx
    mov (%rdx), %rcx
    mov %rcx, 2392(%rbx)
    /* 16f. Our handle is consumed: -EBADF through 0x4c. */
    mov 2360(%rbx), %rdi
    lea 2416(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4c, %rax
    int $0x84
    mov %rax, 2400(%rbx)

    /* 16g. A FOURTH region, for the WINDOW leg (2026-11-08): the kernel
       maps it in OUR space and mirrors it into the peer's, then we put a
       marker in it. */
    mov $4096, %rdi
    lea 2512(%rbx), %rsi          /* its own 32-byte record... */
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4f, %rax
    int $0x84
    mov %rax, 2504(%rbx)          /* ...and its answer */
    mov 2512(%rbx), %rcx          /* the base the KERNEL chose */
    movabs $0x0ddba11cafef00d, %rdx
    mov %rdx, (%rcx)
    mov %rdx, 2488(%rbx)          /* our own read-back of the marker */

    /* 16h. DROP OUR WINDOW, KEEP THE CAPABILITY (0x50). Our own space stops
       mapping the frames; the frames stay, and the peer's space keeps its
       mirror over them -- its own read (step 7b) is the evidence, and the
       kernel's read-back checks our window is gone. */
    mov 2536(%rbx), %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x50, %rax
    int $0x84
    mov %rax, 2424(%rbx)

    /* 16h2. ...and the row is not a second success: the SAME call again is
       -ENOENT by name (nothing left to drop), not 0. */
    mov 2536(%rbx), %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x50, %rax
    int $0x84
    mov %rax, 2432(%rbx)

    /* 16i. The CAPABILITY outlives the window: geometry (0x4c) still
       answers the kernel's record for it. */
    mov 2536(%rbx), %rdi
    lea 2448(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4c, %rax
    int $0x84
    mov %rax, 2440(%rbx)

    /* 13c. THE CALL (0x4a, blocking dial): handle 5, the peer's endpoint
       as the capability names it, 8-byte "fromA!" request, the answer
       buffer at BUF+904 and its capacity 32. THIS PARK: the task that
       runs this instruction is BLOCKED until the peer answers, and the
       next instruction executed here is after that answer arrived. */
    /* 13c-pre. The GO: SEND to mailbox 1 (the PEER's endpoint) in the
       (sender, seq) form. The peer is PARKED in a blocking receive there,
       so this message is what resumes it -- and the peer's next step is to
       drain the call queued just below. Two processes, two primitives, each
       waking the other out of a park. */
    mov $1, %rdi
    mov $22, %rsi
    xor %rdx, %rdx
    xor %r8, %r8
    movabs $-1, %r10
    mov $0x45, %rax
    int $0x84
    mov %rax, 1000(%rbx)
    movabs $0x000021416d6f7266, %rcx   /* "fromA!" little-endian */
    mov %rcx, 944(%rbx)
    mov $5, %rdi
    lea 944(%rbx), %rsi
    mov $8, %rdx
    lea 904(%rbx), %r10
    mov $32, %r8
    mov $0x4a, %rax
    int $0x84
    mov %rax, 936(%rbx)
    /* 13d. The ANSWER, read back out of the buffer the kernel wrote
       through: the peer's own bytes, in THIS process's memory. */
    mov 904(%rbx), %rcx
    mov %rcx, 952(%rbx)
    /* 13e. The region, after the peer ran: offset 8 is OUR value (we
       wrote it in step 12, and nobody else touched it), offset 16 is the
       PEER's -- written by another ring-3 process through another
       capability and read here through ours. */
    mov 880(%rbx), %rcx
    mov 8(%rcx), %rdx
    mov %rdx, 960(%rbx)
    mov 16(%rcx), %rdx
    mov %rdx, 968(%rbx)
    /* 13f. SHM_CLOSE (0x4d) on handle 4: by now the PEER has exited and
       released its grant, so this is the LAST holder -- the record dies
       here, which the kernel's read-back confirms. */
    mov $4, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4d, %rax
    int $0x84
    mov %rax, 976(%rbx)
    /* 13g. The same handle again: consumed, so -EBADF. */
    mov $4, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4d, %rax
    int $0x84
    mov %rax, 984(%rbx)
    /* 15. THE REGION A HOLDER ASKS FOR (0x4f `CAP_SHM_MAP`, 2026-11-07).
       Every region before this step was mapped by the KERNEL first. Here
       the process ASKS: it names a length and a record, and the kernel
       picks the window, backs it with zeroed frames, mints a capability
       and answers {base, len, rights, handle}. The handle in the record is
       what the follow-up rows use -- the program never holds a constant
       address.

       record1 BUF+2048 (32 bytes), record2 BUF+2080 (32 bytes), geometry
       record BUF+2112 (24 bytes). Results from BUF+2136, sixteen qwords:
         ack1, base1, len1, rights1, handle1, wrote1,
         ack2, base2, len2, rights2, handle2,
         close1, close2, stale1, geometry-ack, geometry-base. */
    /* 15a. ASK for one page. */
    mov $4096, %rdi
    lea 2048(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4f, %rax
    int $0x84
    mov %rax, 2136(%rbx)
    mov 2048(%rbx), %rcx
    mov %rcx, 2144(%rbx)          /* base the KERNEL chose */
    mov 2056(%rbx), %rcx
    mov %rcx, 2152(%rbx)          /* len */
    mov 2064(%rbx), %rcx
    mov %rcx, 2160(%rbx)          /* rights */
    mov 2072(%rbx), %rcx
    mov %rcx, 2168(%rbx)          /* handle the kernel minted */
    /* 15b. Write OUR value through the record's base, and read it back
       through the same address. The kernel's RELEASER reads that word out
       of the mapping too, before unmapping it: two parties, one page. */
    movabs $0xdeadbeefcafebabe, %rcx
    mov 2144(%rbx), %rdx
    mov %rcx, (%rdx)
    mov (%rdx), %rcx
    mov %rcx, 2176(%rbx)
    /* 15c. A SECOND request (two pages): the kernel hands out its NEXT
       window, so the base is the kernel's choice and not this program's
       constant. Its pages are never written -- the kernel's releaser
       reading zero out of them is what proves a request is zero-filled. */
    mov $8192, %rdi
    lea 2080(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4f, %rax
    int $0x84
    mov %rax, 2184(%rbx)
    mov 2080(%rbx), %rcx
    mov %rcx, 2192(%rbx)
    mov 2088(%rbx), %rcx
    mov %rcx, 2200(%rbx)
    mov 2096(%rbx), %rcx
    mov %rcx, 2208(%rbx)
    mov 2104(%rbx), %rcx
    mov %rcx, 2216(%rbx)
    /* 15d. Geometry through the handle the FIRST record named (0x4c),
       not a constant: the row takes a handle, so this is what proves the
       request answered a USABLE one. */
    mov 2168(%rbx), %rdi
    lea 2112(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4c, %rax
    int $0x84
    mov %rax, 2248(%rbx)
    mov 2112(%rbx), %rcx
    mov %rcx, 2256(%rbx)
    /* 15e. CLOSE both (0x4d). Each is its region's LAST capability, so
       each close is a teardown: the pages are unmapped and the frames go
       back while this process is still running. */
    mov 2168(%rbx), %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4d, %rax
    int $0x84
    mov %rax, 2224(%rbx)
    mov 2216(%rbx), %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4d, %rax
    int $0x84
    mov %rax, 2232(%rbx)
    /* 15f. The first handle, consumed: -EBADF through 0x4c. */
    mov 2168(%rbx), %rdi
    lea 2112(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4c, %rax
    int $0x84
    mov %rax, 2240(%rbx)

    /* 14. EXIT (0x4e): releases what this table still holds and
       terminates THIS task. The proof's verdict is read by task 0, which
       is why this program does not ask for it. */
    xor %rdi, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4e, %rax
    int $0x84

.hang:
    pause
    jmp .hang
