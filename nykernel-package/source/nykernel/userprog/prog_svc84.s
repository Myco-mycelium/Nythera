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

    /* 0. DISPLAY_SERVE (0x64): this session becomes the display SERVER
       before anything else, publishing a policy that grants clients the
       IDENTITY bit only (mask 1). The peer process attaches under it and
       proves, from ring 3, that a client gets exactly what the policy
       allows; this session keeps full authority as the server, which is
       why its own identity and form-factor rows below still pass.

       STATUS (0x68) right after serve pins the starting shape: this
       session is the server, zero clients, and the server's own grant
       word is 3. It is taken BEFORE this process ever parks, so no client
       can have attached yet -- the word is deterministic. */
    mov $1, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x64, %rax
    int $0x84
    mov %rax, 11624(%rbx)

    mov $0, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x68, %rax
    int $0x84
    mov %rax, 11632(%rbx)

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

    /* 18. BADGE and NOTIFY (2026-11-09), the release's endpoint tag and
       payload-free signal clauses. Results from BUF+2600, eleven qwords:
         notify ack, take bits, second take, wrong-kind refusal, forged
         refusal, non-owner refusal, badge ack (a NEW handle), zero-badge
         refusal, wrong-kind badge refusal, forged badge refusal, and the
         re-badge refusal through the badged handle. The badge is minted
         from HANDLE 1 (SEND|GRANT); the take is through HANDLE 0 (the
         session's OWN owner capability); the non-owner take is through
         HANDLE 5 (the peer endpoint's SEND grant, owned by session 8). */
    /* 18a. NOTIFY (0x52): HANDLE 1, bits 0x5. */
    mov $1, %rdi
    mov $5, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x52, %rax
    int $0x84
    mov %rax, 2600(%rbx)
    /* 18b. NOTIFY_TAKE (0x53): HANDLE 0, the owner's own capability.
       Expected the OR-ed bits, 0x5. */
    xor %rdi, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x53, %rax
    int $0x84
    mov %rax, 2608(%rbx)
    /* 18c. The SAME take again: the word was cleared, so 0. */
    xor %rdi, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x53, %rax
    int $0x84
    mov %rax, 2616(%rbx)
    /* 18d. NOTIFY through HANDLE 2 (the Input kind): -EACCES. */
    mov $2, %rdi
    mov $1, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x52, %rax
    int $0x84
    mov %rax, 2624(%rbx)
    /* 18e. NOTIFY through FORGED handle 31: -EBADF. */
    mov $31, %rdi
    mov $1, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x52, %rax
    int $0x84
    mov %rax, 2632(%rbx)
    /* 18f. NOTIFY_TAKE through HANDLE 5 (the PEER's endpoint, owned by
       session 8): a non-owner's take is -EACCES. */
    mov $5, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x53, %rax
    int $0x84
    mov %rax, 2640(%rbx)
    /* 18g. BADGE (0x51): HANDLE 1, badge 0xBEEF -> a new handle. */
    mov $1, %rdi
    movabs $0xBEEF, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x51, %rax
    int $0x84
    mov %rax, 2648(%rbx)
    /* 18h. A zero badge is not a badge: -EACCES. */
    mov $1, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x51, %rax
    int $0x84
    mov %rax, 2656(%rbx)
    /* 18i. Badging the INPUT capability (HANDLE 2): -EACCES (WrongType). */
    mov $2, %rdi
    mov $1, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x51, %rax
    int $0x84
    mov %rax, 2664(%rbx)
    /* 18j. Badging through FORGED handle 31: -EBADF. */
    mov $31, %rdi
    mov $1, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x51, %rax
    int $0x84
    mov %rax, 2672(%rbx)
    /* 18k. Re-badging the handle the kernel just minted: the badge is
       immutable, so -EACCES. The handle is read from the program's own
       memory, not guessed. */
    mov 2648(%rbx), %rdi
    mov $1, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x51, %rax
    int $0x84
    mov %rax, 2680(%rbx)

    /* 18l. SEND through the BADGED handle minted at 18g (read from
       BUF+2648, never guessed), 6-byte "badge!", no transfer: ack at
       BUF+2688. */
    movabs $0x216567646162, %rcx   /* "badge!" little-endian */
    mov %rcx, 2720(%rbx)
    mov 2648(%rbx), %rdi
    lea 2720(%rbx), %rsi
    mov $6, %rdx
    xor %r8, %r8
    movabs $-1, %r10
    mov $0x47, %rax
    int $0x84
    mov %rax, 2688(%rbx)
    /* 18m. RECV with a NULL badge out-pointer (a_xfer = 0): the validator
       refuses BEFORE the queue is drained, so -EFAULT lands at BUF+2712
       and the message stays queued for 18n. A drain-then-check would eat
       it here. */
    xor %rdi, %rdi
    lea 2736(%rbx), %rsi
    mov $64, %rdx
    xor %r10, %r10
    xor %r8, %r8
    mov $0x48, %rax
    int $0x84
    mov %rax, 2712(%rbx)
    /* 18n. RECV again with a VALID badge out-pointer (BUF+2704): the
       message 18m did not consume is drained, its badge is written, and
       (sender<<32)|6 answers at BUF+2696. */
    xor %rdi, %rdi
    lea 2736(%rbx), %rsi
    mov $64, %rdx
    lea 2704(%rbx), %r10
    xor %r8, %r8
    mov $0x48, %rax
    int $0x84
    mov %rax, 2696(%rbx)

    /* 19a. DEVICE_INFO (0x54) on the BLOCK-DEVICE handle (slot 6): no
       out-pointer, so only the packed answer is stored -- code 2 and the
       rights that travelled with the kernel's mint (READ|WRITE|GRANT =
       14). */
    mov $6, %rdi
    movabs $-1, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x54, %rax
    int $0x84
    mov %rax, 2744(%rbx)
    /* 19b. DEVICE_INFO (0x54) on the I/O-PORT handle (slot 7), asking
       for the window: the answer at BUF+2752 and the out-word at
       BUF+2760 must name exactly the ports the kernel granted. (Each
       pair gets its own 8 bytes: an answer stored under a window word
       would overlap it and read back half of each.) */
    mov $7, %rdi
    lea 2760(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x54, %rax
    int $0x84
    mov %rax, 2752(%rbx)
    /* 19c. DEVICE_INFO on the FRAMEBUFFER handle (slot 8): code 32, and
       the out-word is the ROW window (0 .. display height). */
    mov $8, %rdi
    lea 2776(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x54, %rax
    int $0x84
    mov %rax, 2768(%rbx)
    /* 19d. An ENDPOINT handle (slot 1) asked for a device window: the row
       answers EACCES by name (-13), never a zero window. */
    mov $1, %rdi
    movabs $-1, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x54, %rax
    int $0x84
    mov %rax, 2784(%rbx)
    /* 19e. The row's SHAPE check: a payload claim in rdx is EINVAL
       (-22), refused before the machine is consulted. */
    mov $6, %rdi
    movabs $-1, %rsi
    mov $1, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x54, %rax
    int $0x84
    mov %rax, 2792(%rbx)

    /* 20a. IOPORT_OUT (0x56): write 0xAB to port 0x80 through the I/O-port
       capability (slot 7). Port 0x80 is the diagnostic port the kernel
       granted; the ack lands at BUF+2800. */
    mov $7, %rdi
    mov $0x80, %rsi
    mov $0xab, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x56, %rax
    int $0x84
    mov %rax, 2800(%rbx)
    /* 20b. IOPORT_IN (0x55) on the same port: the byte comes back at
       BUF+2808 (compared against the kernel's own read of 0x80). */
    mov $7, %rdi
    mov $0x80, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x55, %rax
    int $0x84
    mov %rax, 2808(%rbx)
    /* 20c. One port PAST the window (0x81): EACCES by name at BUF+2816,
       even though the capability is a valid IoPort. */
    mov $7, %rdi
    mov $0x81, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x55, %rax
    int $0x84
    mov %rax, 2816(%rbx)
    /* 20d. FB_WRITE (0x58) at pixel (1,1): stores the raw word
       0x00ff00ff through the framebuffer capability (slot 8). */
    mov $8, %rdi
    movabs $0x100000001, %rsi
    mov $0xff00ff, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x58, %rax
    int $0x84
    mov %rax, 2824(%rbx)
    /* 20e. FB_READ (0x57) at the same pixel: the same RAW word comes
       back at BUF+2832. */
    mov $8, %rdi
    movabs $0x100000001, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x57, %rax
    int $0x84
    mov %rax, 2832(%rbx)
    /* 20f. A row OUTSIDE the granted window (1023, past the bound 768):
       EACCES at BUF+2840 before the scan-out is touched. */
    mov $8, %rdi
    mov $0x3ff, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x57, %rax
    int $0x84
    mov %rax, 2840(%rbx)
    /* 20g. FB_WRITE through the I/O-PORT handle: EACCES (WrongType) at
       BUF+2848, so the data rows refuse a handle of another kind. */
    mov $7, %rdi
    movabs $0x100000001, %rsi
    mov $0xff00ff, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x58, %rax
    int $0x84
    mov %rax, 2848(%rbx)

    /* 21a. BLOCK_READ (0x59): read LBA 16 of the boot device (the ISO's
       volume descriptor) and copy its first 8 bytes to BUF+2864. The ack
       at BUF+2856 must be 8. */
    mov $6, %rdi
    mov $16, %rsi
    lea 2864(%rbx), %rdx
    mov $8, %r10
    xor %r8, %r8
    mov $0x59, %rax
    int $0x84
    mov %rax, 2856(%rbx)
    /* 21b. An LBA OUTSIDE the window (0xffffffff, one past the full
       range): EACCES at BUF+2872 before the device is touched. */
    mov $6, %rdi
    mov $0xffffffff, %rsi
    lea 2864(%rbx), %rdx
    mov $8, %r10
    xor %r8, %r8
    mov $0x59, %rax
    int $0x84
    mov %rax, 2872(%rbx)
    /* 21c. A zero copy length is EINVAL (-22) at BUF+2880: the row's
       shape, refused before the machine is consulted. */
    mov $6, %rdi
    mov $16, %rsi
    lea 2864(%rbx), %rdx
    xor %r10, %r10
    xor %r8, %r8
    mov $0x59, %rax
    int $0x84
    mov %rax, 2880(%rbx)
    /* 21d. BLOCK_READ through the FRAMEBUFFER handle: EACCES at
       BUF+2888 (WrongType), so the row refuses a handle of another kind. */
    mov $8, %rdi
    mov $16, %rsi
    lea 2864(%rbx), %rdx
    mov $8, %r10
    xor %r8, %r8
    mov $0x59, %rax
    int $0x84
    mov %rax, 2888(%rbx)

    /* 22a. BLOCK_INFO (0x5a): ask the boot device for its geometry. The
       ack lands at BUF+2896 and the 24-byte record at BUF+2904. */
    mov $6, %rdi
    lea 2904(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x5a, %rax
    int $0x84
    mov %rax, 2896(%rbx)
    /* 22b. BLOCK_INFO through the FRAMEBUFFER handle: EACCES at
       BUF+2928 (WrongType), before any device register is touched. */
    mov $8, %rdi
    lea 2904(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x5a, %rax
    int $0x84
    mov %rax, 2928(%rbx)

    /* 23a. BLOCK_READ SPAN (0x59, 4096 bytes): LBA 16 plus LBA 17 in ONE
       call -- the ISO's primary volume descriptor followed by the
       terminator -- copied to BUF+2944. The ack at BUF+7040 must be
       4096, the descriptor is at the START of the buffer (CD001) and
       the terminator's type byte (0xFF) at +2048. */
    mov $6, %rdi
    mov $16, %rsi
    lea 2944(%rbx), %rdx
    mov $4096, %r10
    xor %r8, %r8
    mov $0x59, %rax
    int $0x84
    mov %rax, 7040(%rbx)
    /* 23b. The SAME span starting at the last grantable sector: the
       first sector is inside the window but the second is not, so the
       WHOLE span is refused EACCES at BUF+7048 before any read. */
    mov $6, %rdi
    mov $0xffffffff, %rsi
    lea 2944(%rbx), %rdx
    mov $4096, %r10
    xor %r8, %r8
    mov $0x59, %rax
    int $0x84
    mov %rax, 7048(%rbx)
    /* 23c. A zero-length span is EINVAL at BUF+7056. */
    mov $6, %rdi
    mov $16, %rsi
    lea 2944(%rbx), %rdx
    xor %r10, %r10
    xor %r8, %r8
    mov $0x59, %rax
    int $0x84
    mov %rax, 7056(%rbx)
    /* 23d. BLOCK_WRITE (0x5b) through the block handle: the handle CARRIES
       WRITE, but the boot device is an ATAPI disc, so the media refuses
       EROFS (-30) at BUF+7064 -- authority passed, the media decided. */
    mov $6, %rdi
    mov $16, %rsi
    lea 2944(%rbx), %rdx
    mov $512, %r10
    xor %r8, %r8
    mov $0x5b, %rax
    int $0x84
    mov %rax, 7064(%rbx)
    /* 23e. A write OUTSIDE the sector window is EACCES at BUF+7072 before
       the device is touched. */
    mov $6, %rdi
    mov $0xffffffff, %rsi
    lea 2944(%rbx), %rdx
    mov $512, %r10
    xor %r8, %r8
    mov $0x5b, %rax
    int $0x84
    mov %rax, 7072(%rbx)
    /* 23f. A write through the FRAMEBUFFER handle: EACCES at BUF+7080
       (WrongType), so the write row checks kind before any device. */
    mov $8, %rdi
    mov $16, %rsi
    lea 2944(%rbx), %rdx
    mov $512, %r10
    xor %r8, %r8
    mov $0x5b, %rax
    int $0x84
    mov %rax, 7080(%rbx)
    /* 23g. The ISO9660 VOLUME ID: the primary descriptor's bytes 40..71
       (32 bytes, space-padded), read by RING 3 from the span above and
       copied to BUF+7104 for the kernel to compare against its own read
       of the same sector. */
    lea 2984(%rbx), %rsi
    lea 7104(%rbx), %rdi
    mov $32, %rcx
    rep movsb

    /* 24a. CACHE STATS (0x5c) before a repeat read: ack at BUF+7136 and
       the 16-byte record {hits, misses} at BUF+7144. */
    mov $6, %rdi
    lea 7144(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x5c, %rax
    int $0x84
    mov %rax, 7136(%rbx)
    /* 24b. The same LBA twice: the first read fills the kernel's sector
       cache, the second must be answered from memory (a HIT). */
    mov $6, %rdi
    mov $18, %rsi
    lea 8192(%rbx), %rdx
    mov $512, %r10
    xor %r8, %r8
    mov $0x59, %rax
    int $0x84
    mov $6, %rdi
    mov $18, %rsi
    lea 8192(%rbx), %rdx
    mov $512, %r10
    xor %r8, %r8
    mov $0x59, %rax
    int $0x84
    /* 24c. CACHE STATS again: ack at BUF+7160, record at BUF+7168. The
       kernel checks that hits ROSE between the two queries. */
    mov $6, %rdi
    lea 7168(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x5c, %rax
    int $0x84
    mov %rax, 7160(%rbx)
    /* 24d. BLOCK_WRITE on the scratch ATA disk (handle 9, the second
       block capability the equip minted): a pattern at BUF+8192 is
       written to LBA 1000. Ack at BUF+7184 must be 512. */
    movabs $0xfeedfacecafebeef, %rax
    mov %rax, 8192(%rbx)
    mov $9, %rdi
    mov $1000, %rsi
    lea 8192(%rbx), %rdx
    mov $512, %r10
    xor %r8, %r8
    mov $0x5b, %rax
    int $0x84
    mov %rax, 7184(%rbx)
    /* 24e. Read it BACK through the same capability into BUF+8704 (ack
       BUF+7192); the kernel compares both copies with the disk itself. */
    mov $9, %rdi
    mov $1000, %rsi
    lea 8704(%rbx), %rdx
    mov $512, %r10
    xor %r8, %r8
    mov $0x59, %rax
    int $0x84
    mov %rax, 7192(%rbx)

    /* 25a. The ISO9660 ROOT DIRECTORY: the primary descriptor's root
       record lives at PVD+156; its extent LBA is the little-endian u32
       at +2 and its length at +10. Ring 3 reads the directory through
       the block capability into BUF+9216 (ack BUF+7208). */
    mov 3102(%rbx), %r12d
    mov $6, %rdi
    mov %r12, %rsi
    lea 9216(%rbx), %rdx
    mov $2048, %r10
    xor %r8, %r8
    mov $0x59, %rax
    int $0x84
    mov %rax, 7208(%rbx)
    /* 25b. Walk the directory records in RING 3: skip the self/parent
       entries (name length 0/1), emit each name as [len][bytes] at
       BUF+7216, and stop at the first zero-length record or 12 names.
       BUF+7200 gets the count. */
    lea 7216(%rbx), %r8
    xor %r9, %r9
    xor %esi, %esi
.Ldir_next:
    cmp $12, %r9
    jae .Ldir_done
    lea 9216(%rbx,%rsi), %rdi
    movzbl (%rdi), %eax
    test %eax, %eax
    jz .Ldir_done
    movzbl 32(%rdi), %ecx
    cmp $1, %ecx
    jbe .Ldir_skip
    mov %cl, (%r8)
    inc %r8
    xor %edx, %edx
.Ldir_copy:
    movzbl 33(%rdi,%rdx), %eax
    mov %al, (%r8)
    inc %r8
    inc %edx
    cmp %ecx, %edx
    jb .Ldir_copy
    inc %r9
.Ldir_skip:
    movzbl (%rdi), %eax
    add %eax, %esi
    jmp .Ldir_next
.Ldir_done:
    mov %r9, 7200(%rbx)

    /* 13b. the PLATFORM rows: the session identity the kernel presents.

       GET (0x5d) must answer the live identity; SET (0x5e) to the
       Windows posture (index 2) must apply it, and GET must read the new
       identity back; SET 99 must be refused -EINVAL with the identity
       unchanged; the restore must put the ORIGINAL identity (whatever
       the machine auto-detected, so it is read back out of this buffer,
       never assumed to be 0) back, and GET must confirm it. The
       coordinator compares all seven words against the kernel's own
       live identity. */
    xor %rdi, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x5d, %rax
    int $0x84
    mov %rax, 11296(%rbx)

    mov $2, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x5e, %rax
    int $0x84
    mov %rax, 11304(%rbx)

    xor %rdi, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x5d, %rax
    int $0x84
    mov %rax, 11312(%rbx)

    mov $99, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x5e, %rax
    int $0x84
    mov %rax, 11320(%rbx)

    xor %rdi, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x5d, %rax
    int $0x84
    mov %rax, 11328(%rbx)

    mov 11296(%rbx), %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x5e, %rax
    int $0x84
    mov %rax, 11336(%rbx)

    xor %rdi, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x5d, %rax
    int $0x84
    mov %rax, 11344(%rbx)

    /* 13c. PLATFORM_INFO (0x5f): one call answers the whole session
       shape -- identity, form factor, display width and height, the
       status-bar height the layout contract reserved, and the skin's
       accent colour -- into a 48-byte record at BUF+11400. The ack lands
       at BUF+11360; the coordinator compares every record word with the
       kernel's own live values and with the BOUND display. */
    lea 11400(%rbx), %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x5f, %rax
    int $0x84
    mov %rax, 11360(%rbx)

    /* 13d. PLATFORM_MODE_SET (0x60): the form factor at runtime.

       Ask for the phone layout (index 2), read the resulting session
       shape through PLATFORM_INFO into BUF+11456 (ack BUF+11376), then
       ask for the ORIGINAL form factor -- read out of the first record at
       BUF+11408, never assumed -- and read it back into BUF+11512 (ack
       BUF+11392). The coordinator checks both records' geometry and
       status-bar height against `device_mode::layout` at the mode the
       kernel then reports. */
    mov $2, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x60, %rax
    int $0x84
    mov %rax, 11368(%rbx)

    lea 11456(%rbx), %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x5f, %rax
    int $0x84
    mov %rax, 11376(%rbx)

    mov 11408(%rbx), %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x60, %rax
    int $0x84
    mov %rax, 11384(%rbx)

    lea 11512(%rbx), %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x5f, %rax
    int $0x84
    mov %rax, 11392(%rbx)

    /* 13e. the supervised display (0x63/0x61/0x67/0x68/0x5e/0x60).

       OWNER (0x63) is still 0 -- a SERVER is not an exclusive owner, and
       the two forms never overlap. ACQUIRE (0x61) must therefore be
       refused -EBUSY: the display is already supervised, and a second
       blunter claim on it would race the thing that protects it. CLIENT
       (0x67, a0 = 0) says the server is NOT its own client (0).

       STATUS (0x68) now reports ONE client: the peer attached before it
       sent the wake this process was parked on, so the count is
       deterministic even though the two processes interleave freely.

       The server then uses its full authority -- the peer's policy grants
       clients the IDENTITY bit only, so the FORM FACTOR set below is
       something no client could have done -- and reads both back through
       PLATFORM_INFO into BUF+11688.

       Then EXIT (0x4e): the server's withdrawal dissolves the server and
       detaches the parked client, which the coordinator checks after both
       processes are gone. The identity and form factor it set here are
       kernel state and stay standing. */
    mov $0, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x63, %rax
    int $0x84
    mov %rax, 11640(%rbx)

    mov $0, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x61, %rax
    int $0x84
    mov %rax, 11648(%rbx)

    mov $0, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x67, %rax
    int $0x84
    mov %rax, 11656(%rbx)

    /* DISPLAY_SPACES (0x6b): the session -> address-space registry as
       three facts. The proof registered BOTH processes' spaces and this
       session is the display server, so the answer must be: two sessions
       registered, the caller among them, the server among them. No PML4
       address is handed to ring 3. */
    xor %rdi, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x6b, %rax
    int $0x84
    mov %rax, 11760(%rbx)

    /* RELEASE (0x62) is refused -EACCES for a session that holds no
       exclusive claim -- a display SERVER is not that claim, and the
       release row must not be a way for a client to unseat a server. */
    xor %rdi, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x62, %rax
    int $0x84
    mov %rax, 11744(%rbx)

    mov $0, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x68, %rax
    int $0x84
    mov %rax, 11664(%rbx)

    mov $2, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x5e, %rax
    int $0x84
    mov %rax, 11672(%rbx)

    mov $2, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x60, %rax
    int $0x84
    mov %rax, 11680(%rbx)

    /* COMPOSE (0x6a): the server blits every client surface -- the peer's
       tile, written before it sent the wake this process was parked on --
       onto the display and reads its own work back. The answer is the
       number of tiles composed, and the coordinator checks the PIXELS. */
    xor %rdi, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x6a, %rax
    int $0x84
    mov %rax, 11752(%rbx)

    /* SURFACE CHURN (0x69 then 0x6c), twelve times: the server attaches IT
       SELF as a client, opens a tile, gives it back, and repeats. This is the
       leg that proves the display's window table is RECLAIMED rather than
       consumed: twelve openings against an eight-slot table can only work if
       every release returns its slot, and the two numbers the release row
       answers say so from the kernel's own counters -- the ISSUED count kept
       climbing while the LIVE count came back to where it started, and the
       base the kernel chose was the SAME address every time.

       The words are read by the coordinator's verdict:
         11768 openings+releases completed   11776 live before
         11784 live after                    11792 issued at the first release
         11800 issued at the last release    11808 first base
         11816 last base                     11824 self-attach grant
         11832 failures                      11880 loop counter
         11872 the errno the failure came back as (0 when the churn ran)
       The 32-byte surface record lands at 11840, past everything read. */
    mov $3, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x65, %rax
    int $0x84
    mov %rax, 11824(%rbx)
    movq $0, 11768(%rbx)
    movq $0, 11776(%rbx)
    movq $0, 11784(%rbx)
    movq $0, 11792(%rbx)
    movq $0, 11800(%rbx)
    movq $0, 11808(%rbx)
    movq $0, 11816(%rbx)
    movq $0, 11832(%rbx)
    movq $0, 11872(%rbx)
    movq $0, 11880(%rbx)
.Lchurn:
    /* The record goes in a1 (rsi), NOT a0: DISPLAY_SURFACE validates its
       SECOND argument as the 32-byte record pointer. Passing it in rdi made
       the kernel see a null pointer and refuse -EFAULT before the window
       table was ever touched -- which is exactly what the churn's first
       boot reported ("churn fails 1", nothing issued). */
    xor %rdi, %rdi
    lea 11840(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x69, %rax
    int $0x84
    test %rax, %rax
    jne .Lchurn_fail
    mov 11840(%rbx), %rcx           /* the base the KERNEL chose */
    cmpq $0, 11880(%rbx)
    jne 1f
    mov %rcx, 11808(%rbx)           /* the FIRST base */
1:
    mov %rcx, 11816(%rbx)           /* the LAST base */
    xor %rdi, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x6c, %rax
    int $0x84
    test %rax, %rax
    js .Lchurn_fail
    mov %rax, %rcx
    shr $32, %rcx
    mov %rcx, 11784(%rbx)           /* windows LIVE after */
    mov %eax, %ecx                  /* the low half: ISSUED (zero-extended) */
    mov %rcx, 11800(%rbx)           /* windows ISSUED after */
    cmpq $0, 11880(%rbx)
    jne 2f
    mov %rcx, 11792(%rbx)           /* issued at the FIRST release */
    mov 11784(%rbx), %rcx
    mov %rcx, 11776(%rbx)           /* LIVE before: where the churn started */
2:
    incq 11880(%rbx)
    incq 11768(%rbx)
    cmpq $12, 11880(%rbx)
    jb .Lchurn
    jmp .Lchurn_done
    /* The refusal itself is kept, not just the fact of it: a churn that
       stops short has to name the row and the errno that stopped it, or the
       verdict can only say "zero" and nobody learns anything. */
.Lchurn_fail:
    incq 11832(%rbx)
    mov %rax, 11872(%rbx)
.Lchurn_done:
    /* DETACH (0x66): the self-attached client slot goes back, so the
       verdict's client count is zero for the right reason and not merely
       because the session died. */
    xor %rdi, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x66, %rax
    int $0x84

    /* ---- THE REFUSAL MATRIX ------------------------------------------
       One cell per call row, all of them AFTER the detachment above: every
       probe here is a call the kernel must REFUSE, so the row has to have
       its answer ready before it touches anything -- a probe that made the
       row act first would mutate the state the other legs read back (the
       first attempt at this matrix drove a block write into a live region
       and took three unrelated legs down with it).

       The answers live at BUF+12288 and up: a FOURTH results page, mapped
       for exactly this. The first attempt used BUF+12160, still inside the
       third page, and clobbered the ring-3 stack the probes were called
       from. `REFUSAL_MATRIX` in src/service84.rs names the same slot and
       the errno each cell must be; the kernel leg compares them and the
       proof gate pins the packed word. */

    /* ---- NO-EFFECT WITNESSES: the state each refusal could still move ----
       An answer proves a row refused; it does not prove the row ABSTAINED. A
       0x4d that answered EBADF and still closed the truncated handle, a 0x50
       that answered EBADF and still dropped the window, a 0x59 that answered
       EINVAL and still touched the media: every one of those passes a matrix
       of errnos alone. So every cell gets a second observation, made AFTER
       its refusal, at BUF+12544+8i.

       The observations have to use state that ALREADY EXISTS here, because
       the proof pins its region release ledger at exactly four: a fifth
       request at matrix time -- even one closed immediately -- makes
       `released` five and rewrites the LAST release's words, which are the
       transfer leg's evidence. So:
         - a CANARY (0x4e4f2d4546464543) is written into the record, the
           cache-stats record and the 512-byte transfer buffer the probes
           name, so "the row wrote nothing" is a value and not a hope;
         - the region from the WINDOW leg (step 16g) is still alive and held
           by THIS process at matrix time (its window was dropped by 0x50 in
           step 16h, its capability survives to EXIT). Its handle is at
           BUF+2536, so the 0x4d cell can re-use the caller's own capability
           through 0x4c;
         - a SECOND region, the one the matrix's 0x50 cell needs: a page the
           KERNEL mapped at boot (CODE+0x4_0000) with the marker "WITNESS!"
           in it, ADOPTED into this table before ring 3 began at the handle
           the kernel writes to BUF+16376 (its index depends on whether the
           gate's scratch disk took slot 9). It is not a 0x4f request, so it
           never enters the release ledger, and its window is LIVE -- which is
           what lets cell 23 read a word through a window after the refused
           unmap instead of taking the machine's word that the window state
           did not change;
         - a BASELINE read of LBA 16, so the 0x59 write-side cell can re-read
           the same sector afterwards and compare it byte for byte. */
    movabs $0x4e4f2d4546464543, %rcx
    mov %rcx, 12800(%rbx)
    mov %rcx, 12808(%rbx)
    mov %rcx, 12816(%rbx)
    mov %rcx, 12824(%rbx)
    mov %rcx, 12832(%rbx)
    mov %rcx, 12840(%rbx)
    lea 12928(%rbx), %rdi
    mov $64, %rcx
1:  movabs $0x4e4f2d4546464543, %rax
    mov %rax, (%rdi)
    add $8, %rdi
    dec %rcx
    jnz 1b
    /* Every witness slot starts at zero. A cell whose observation has not
       been added yet pins its slot at zero, so a sweep can add one cell per
       boot and each intermediate run still asserts something true. */
    xor %rax, %rax
    mov $25, %rcx
    lea 12544(%rbx), %rdi
2:  mov %rax, (%rdi)
    add $8, %rdi
    dec %rcx
    jnz 2b
    /* The baseline sector: LBA 16 (the ISO volume descriptor), the same
       sector the proof already reads, taken before any probe can disturb
       anything. Cell 19's witness compares against it. */
    mov $6, %rdi
    mov $16, %rsi
    lea 13440(%rbx), %rdx
    mov $512, %r10
    xor %r8, %r8
    mov $0x59, %rax
    int $0x84
    mov %rax, 12520(%rbx)

    /* Row 0x42 GFX_MODE_INTO, a0 = 0x1001: MISALIGNED and not user-
       accessible. The row's validator runs before anything is written, so
       this is a refusal that cannot disturb the results region. (Note what
       this cell actually pins: `user_range_ok` walks mappings, not
       alignment, so a misaligned MAPPED pointer is served -- the refusal
       here is the unmapped page, and svc84_refusals.md says so.) */
    mov $0x1001, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x42, %rax
    int $0x84
    mov %rax, 12288(%rbx)
    /* NO-EFFECT, cell 0: the refused pointer named an unmapped page, so
       there is no destination of ours to inspect. What can be observed is
       that the row itself still serves a legal call: a row that half-acted
       on the bad pointer would not answer this one with 0. */
    lea 12496(%rbx), %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x42, %rax
    int $0x84
    mov %rax, 12544(%rbx)

    /* Row 0x5a CAP_BLOCK_INFO, handle 31: FORGED. a1 names a real 24-byte
       record inside the fourth page, so the refusal cannot be the pointer:
       the handle is checked before the record is written, and the cell pins
       the kind of that refusal (EBADF, a handle that names nothing). */
    mov $31, %rdi
    lea 12800(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x5a, %rax
    int $0x84
    mov %rax, 12296(%rbx)
    /* NO-EFFECT, cell 1: the forged handle is resolved before the record
       is written, so the record's first word must still be the canary. */
    mov 12800(%rbx), %rcx
    mov %rcx, 12552(%rbx)

    /* Row 0x5c CAP_BLOCK_CACHE_STATS, handle 31: FORGED, with a real
       16-byte record in the fourth page. Same rule as 0x5a -- the handle
       resolves before the record is written -- and the same errno. */
    mov $31, %rdi
    lea 12832(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x5c, %rax
    int $0x84
    mov %rax, 12304(%rbx)
    /* NO-EFFECT, cell 2: same rule on the cache-stats record. */
    mov 12832(%rbx), %rcx
    mov %rcx, 12560(%rbx)

    /* Row 0x5b CAP_BLOCK_WRITE, handle 31: FORGED, with a real 512-byte
       buffer (in the fourth page) and a legal transfer length, so the only
       thing wrong with this call is the handle. Authority is settled before
       any sector is staged or written -- which is what makes this cell safe
       to probe in a live proof. */
    mov $31, %rdi
    xor %rsi, %rsi
    lea 12928(%rbx), %rdx
    mov $512, %r10
    xor %r8, %r8
    mov $0x5b, %rax
    int $0x84
    mov %rax, 12312(%rbx)
    /* NO-EFFECT, cell 3: the forged write never staged the buffer, so the
       canary in its first word is still there. */
    mov 12928(%rbx), %rcx
    mov %rcx, 12568(%rbx)

    /* Row 0x66 DISPLAY_DETACH, with NOTHING to detach: this runs after the
       detachment above, so the caller's slot is already back and the row
       has nothing of its own to free. A detach is the one matrix cell whose
       "bad input" is the caller's own absent state rather than an argument,
       so it is only a refusal HERE, after that detach -- run it before, and
       it would free the slot the display legs are still reading. */
    xor %rdi, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x66, %rax
    int $0x84
    mov %rax, 12320(%rbx)
    /* NO-EFFECT, cell 4: the absent state is still absent. A detach that
       half-acted would not answer the same call the same way twice. */
    xor %rdi, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x66, %rax
    int $0x84
    mov %rax, 12576(%rbx)

    /* Row 0x6c DISPLAY_SURFACE_RELEASE, with no tile: the churn above gave
       every tile it opened back, so the record the row looks for is gone.
       The errno here is the record's absence (ENOENT), NOT the same answer
       as the detach above -- which is exactly what a matrix is for: two
       "nothing to give back" rows, two different names for it. */
    xor %rdi, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x6c, %rax
    int $0x84
    mov %rax, 12328(%rbx)
    /* NO-EFFECT, cell 5: the tile record is still absent, so the second
       release is refused for the same reason as the first. */
    xor %rdi, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x6c, %rax
    int $0x84
    mov %rax, 12584(%rbx)

    /* Row 0x4f CAP_SHM_MAP, a0 = 100: a length in bytes that is NOT a
       multiple of the page size, with a real 32-byte record pointer. The row
       settles its shape before it asks the mapping source for anything, so
       this cell must be EINVAL and must leave no frames behind -- which is
       why it is safe HERE and why the matrix's first attempt was wrong to
       blame this row for what the overlapping slots had done. */
    mov $100, %rdi
    lea 12800(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4f, %rax
    int $0x84
    mov %rax, 12336(%rbx)
    /* NO-EFFECT, cell 6: the shape check runs before the mapping source,
       so the record it was handed is untouched and no frame was taken. */
    mov 12800(%rbx), %rcx
    mov %rcx, 12592(%rbx)

    /* Row 0x59 CAP_BLOCK_READ, transfer length 0. The row's shape check
       (`a_xfer == 0 || a_xfer > MAX_BLOCK_SPAN || a_rights != 0`) runs
       before the handle is resolved and long before the drive is asked, so
       a zero-length read is -EINVAL and reaches no sector. */
    mov $1, %rdi
    xor %rsi, %rsi
    lea 12800(%rbx), %rdx
    xor %r10, %r10
    xor %r8, %r8
    mov $0x59, %rax
    int $0x84
    mov %rax, 12344(%rbx)
    /* NO-EFFECT, cell 7: the zero-length read reaches no sector and writes
       no byte of the destination it was given. */
    mov 12800(%rbx), %rcx
    mov %rcx, 12600(%rbx)

    /* Row 0x5b CAP_BLOCK_WRITE, transfer length 0. Same shape check, same
       answer, and again before authority is resolved and long before any
       sector is staged -- a zero-length write must never reach the media. */
    mov $1, %rdi
    xor %rsi, %rsi
    lea 12800(%rbx), %rdx
    xor %r10, %r10
    xor %r8, %r8
    mov $0x5b, %rax
    int $0x84
    mov %rax, 12352(%rbx)
    /* NO-EFFECT, cell 8: the zero-length write must not have poisoned the
       row. A legal write through the real handle still passes authority and
       lets the MEDIA refuse it (ATAPI is read-only -> EROFS). */
    mov $6, %rdi
    mov $16, %rsi
    lea 12928(%rbx), %rdx
    mov $512, %r10
    xor %r8, %r8
    mov $0x5b, %rax
    int $0x84
    mov %rax, 12608(%rbx)

    /* Row 0x4f CAP_SHM_MAP, length 0. `a0 == 0` is part of the same shape
       check as the alignment gate, so a zero-length request is -EINVAL and
       asks the mapping source for nothing. */
    xor %rdi, %rdi
    lea 12800(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4f, %rax
    int $0x84
    mov %rax, 12360(%rbx)
    /* NO-EFFECT, cell 9: nothing was asked of the mapping source, so the
       record is still the canary. */
    mov 12800(%rbx), %rcx
    mov %rcx, 12616(%rbx)

    /* Row 0x4f CAP_SHM_MAP, record pointer 0x1001: misaligned AND not user-
       accessible, with a legal length, so the refusal is the pointer. The
       range check runs before `request_region_for`, so this cell proves the
       row refuses a bad record without consuming a frame. (As with 0x42:
       `user_range_ok` does not look at alignment, so the page is what makes
       this a refusal -- svc84_refusals.md states that rather than implying
       the row validates alignment.) */
    mov $4096, %rdi
    mov $0x1001, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4f, %rax
    int $0x84
    mov %rax, 12368(%rbx)
    /* NO-EFFECT, cell 10: the row was asked with a bad pointer and its
       shape check is still first. A legal-record, zero-length request is
       still refused -EINVAL: the refused call left the mapping source
       alone and the row in the state it was in. */
    xor %rdi, %rdi
    lea 12744(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4f, %rax
    int $0x84
    mov %rax, 12624(%rbx)

    /* Row 0x4c CAP_SHM (geometry), record pointer 0x1001: misaligned and not
       user-accessible. This row validates the record BEFORE it resolves the
       handle (a0 = 31 here is forged), so the cell pins the pointer's answer
       and not the handle's -- the order is the point, and it is why the same
       bad pointer would answer EBADF on a row that looked the handle up
       first. */
    mov $31, %rdi
    mov $0x1001, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4c, %rax
    int $0x84
    mov %rax, 12376(%rbx)
    /* NO-EFFECT, cell 11: the caller's own live capability (the WINDOW
       leg's, released only by EXIT) was not touched by the refused geometry
       call, so it still answers geometry with 0. */
    mov 2536(%rbx), %rdi
    lea 12744(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4c, %rax
    int $0x84
    mov %rax, 12632(%rbx)

    /* Row 0x5a CAP_BLOCK_INFO, record pointer 0x1001: misaligned and not
       user-accessible, handle forged. This block row also validates the
       record before it resolves the handle, so the cell pins EFAULT -- the
       counterpart of the earlier 0x5a cell, which passes a REAL record and
       gets EBADF from the handle. Two cells, one row, two names: that is the
       difference a per-row matrix is for. */
    mov $31, %rdi
    mov $0x1001, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x5a, %rax
    int $0x84
    mov %rax, 12384(%rbx)
    /* NO-EFFECT, cell 12: the refused record was a foreign page; the row
       still serves the same call with a real record and the real handle. */
    mov $6, %rdi
    lea 12744(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x5a, %rax
    int $0x84
    mov %rax, 12640(%rbx)

    /* Row 0x59 CAP_BLOCK_READ, handle 2^32: ABOVE the u32 handle space.
       Every block row's FIRST check is this EBADF arm -- a handle that cannot
       name a capability is refused before the record pointer, before the
       transfer length, before authority -- and until now no cell probed it.
       The rest of the call is well-formed on purpose: only the handle is
       wrong, so the answer cannot be explained by anything else. */
    movabs $0x100000000, %rdi
    xor %rsi, %rsi
    lea 12928(%rbx), %rdx
    mov $512, %r10
    xor %r8, %r8
    mov $0x59, %rax
    int $0x84
    mov %rax, 12392(%rbx)
    /* NO-EFFECT, cell 13: the guard is the only thing between 2^32 and
       `a0 as u32`; the destination the call named is still the canary. */
    mov 12928(%rbx), %rcx
    mov %rcx, 12648(%rbx)

    /* Row 0x5a CAP_BLOCK_INFO, handle 2^32. Same arm, same answer: the row
       would rather refuse a handle it cannot even name than let it reach the
       capability table as a wrapped u32. */
    movabs $0x100000000, %rdi
    lea 12800(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x5a, %rax
    int $0x84
    mov %rax, 12400(%rbx)
    /* NO-EFFECT, cell 14: the record handed to the oversized call is still
       the canary -- the row refused before it wrote. */
    mov 12800(%rbx), %rcx
    mov %rcx, 12656(%rbx)

    /* Row 0x5b CAP_BLOCK_WRITE, handle 2^32, with a legal LBA, a real buffer
       and a legal length: the handle is the ONLY malformed argument, so an
       answer other than EBADF would name the wrong arm. */
    movabs $0x100000000, %rdi
    xor %rsi, %rsi
    lea 12928(%rbx), %rdx
    mov $512, %r10
    xor %r8, %r8
    mov $0x5b, %rax
    int $0x84
    mov %rax, 12408(%rbx)
    /* NO-EFFECT, cell 15: the truncated handle would have named a device;
       the buffer it named is still the canary, so nothing was staged. */
    mov 12928(%rbx), %rcx
    mov %rcx, 12664(%rbx)

    /* Row 0x5c CAP_BLOCK_CACHE_STATS, handle 2^32 -- the fourth and last
       block row whose first check is this arm. The answer slot lands on
       BUF+12416, which used to be where the argument records lived: they
       moved to BUF+12800 in the same change, so a slot never shares a byte
       with a probe's own scratch. */
    movabs $0x100000000, %rdi
    lea 12832(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x5c, %rax
    int $0x84
    mov %rax, 12416(%rbx)
    /* NO-EFFECT, cell 16: no hit/miss record was written for a handle the
       row refused. */
    mov 12832(%rbx), %rcx
    mov %rcx, 12672(%rbx)

    /* Row 0x59 CAP_BLOCK_READ, transfer length 16385: one byte past the
       row's own MAX_BLOCK_SPAN (8 x 2048). The same shape check that refuses
       a zero length refuses too much, so an oversized read reaches no sector
       and allocates nothing. */
    mov $1, %rdi
    xor %rsi, %rsi
    lea 12928(%rbx), %rdx
    mov $16385, %r10
    xor %r8, %r8
    mov $0x59, %rax
    int $0x84
    mov %rax, 12424(%rbx)
    /* NO-EFFECT, cell 17: too much was asked for, so no sector was read
       and the destination's first word is still the canary. */
    mov 12928(%rbx), %rcx
    mov %rcx, 12680(%rbx)

    /* Row 0x4f CAP_SHM_MAP, length 17 pages: one page past the row's own
       MAX_REGION_REQUEST_PAGES. The size cap is checked with the alignment
       and the zero check, before the record pointer and before the mapping
       source, so an oversized request allocates nothing. */
    mov $69632, %rdi
    lea 12800(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4f, %rax
    int $0x84
    mov %rax, 12432(%rbx)
    /* NO-EFFECT, cell 18: the size cap is checked with the shape, so the
       record is untouched. */
    mov 12800(%rbx), %rcx
    mov %rcx, 12688(%rbx)

    /* Row 0x59 CAP_BLOCK_READ, destination a2 = BUF+20480: the proof's FIFTH
       page, which the kernel maps USER_ACCESSIBLE and NOT WRITABLE. Nothing
       else about the call is wrong -- real block handle 6, LBA 0, a legal
       512-byte transfer -- so EFAULT here is the WRITE flag and nothing
       else. The row validates a2 for writing BEFORE it resolves the handle
       or reaches the drive, so the refusal allocates no sector and cannot
       disturb another leg. (A row that asked for a readable range would
       serve this call and fault ring 3 on the copy instead: the page is
       present and user-accessible, only its write bit is clear.) */
    mov $6, %rdi
    xor %rsi, %rsi
    lea 20480(%rbx), %rdx
    mov $512, %r10
    xor %r8, %r8
    mov $0x59, %rax
    int $0x84
    mov %rax, 12440(%rbx)
    /* NO-EFFECT, cell 19: the refused destination could not have been
       written (it is not writable), so the observable effect a broken row
       could still have is on the MEDIA. Re-read LBA 16 and compare it with
       the baseline taken before any probe ran. The witness is 1 only when
       both reads returned 512 bytes and all 64 words agree. */
    mov $6, %rdi
    mov $16, %rsi
    lea 13952(%rbx), %rdx
    mov $512, %r10
    xor %r8, %r8
    mov $0x59, %rax
    int $0x84
    mov %rax, %rdx
    cmpq $512, %rdx
    jne 6f
    cmpq $512, 12520(%rbx)
    jne 6f
    lea 13440(%rbx), %rsi
    lea 13952(%rbx), %rdi
    mov $64, %rcx
5:  mov (%rsi), %r8
    cmp (%rdi), %r8
    jne 6f
    add $8, %rsi
    add $8, %rdi
    dec %rcx
    jnz 5b
    mov $1, %rcx
    jmp 7f
6:  xor %rcx, %rcx
7:  mov %rcx, 12696(%rbx)

    /* Row 0x4f CAP_SHM_MAP, record a1 = BUF+20480: the same read-only page,
       with a legal one-page length and no other claim. The row checks the
       length, then the record for WRITING, then asks the mapping source, so
       this EFAULT allocates no frame and consumes no window slot -- the
       frames-in-use accounting the MAP leg pins is untouched by the cell.
       The block cell above and this one are the two halves of the write-side
       arm: a device row and a region row, each handed a page it may read but
       not write. */
    mov $4096, %rdi
    lea 20480(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4f, %rax
    int $0x84
    mov %rax, 12448(%rbx)
    /* NO-EFFECT, cell 20: the read-only record was refused before the
       mapping source was asked, and the row still refuses a zero-length
       request -- no frame was consumed, nothing was mapped. */
    xor %rdi, %rdi
    lea 12744(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4f, %rax
    int $0x84
    mov %rax, 12704(%rbx)

    /* Row 0x4c CAP_SHM, handle 2^32: ABOVE the u32 handle space, with a real
       24-byte record in the fourth page. The geometry row's FIRST check is
       this EBADF arm, ahead of the record pointer and ahead of the table
       lookup, so a handle that cannot be named is refused rather than
       truncated to the u32 the table would happily resolve (0, the endpoint
       owner's slot -- which would have answered EACCES and made the cell
       look like a different arm). */
    movabs $0x100000000, %rdi
    lea 12800(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4c, %rax
    int $0x84
    mov %rax, 12456(%rbx)
    /* NO-EFFECT, cell 21: a0 as u32 would be 0, the endpoint owner's live
       capability; the record the call named is still the canary. */
    mov 12800(%rbx), %rcx
    mov %rcx, 12712(%rbx)

    /* Row 0x4d CAP_SHM_CLOSE, handle 2^32: the close row's EBADF arm, ahead
       of the shape check, so this cell answers the handle and not the (also
       wrong) argument claims it deliberately leaves empty. A forged handle
       here would have been answered by the capability table; 2^32 is refused
       by the ROW, which is the arm this cell names. Nothing is given back:
       a refused close never consumes a handle. */
    movabs $0x100000000, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4d, %rax
    int $0x84
    mov %rax, 12464(%rbx)
    /* NO-EFFECT, cell 22: truncating 2^32 to 0 would have answered for the
       endpoint owner's slot; what matters is that the caller's OWN region is
       still held. The WINDOW leg's capability (BUF+2536) is released only by
       EXIT, so geometry through it must still answer 0 here. */
    mov 2536(%rbx), %rdi
    lea 12744(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4c, %rax
    int $0x84
    mov %rax, 12720(%rbx)

    /* Row 0x50 CAP_SHM_UNMAP, handle 2^32: the window-drop row's EBADF arm.
       Its neighbouring refusal would be ENOENT (a real handle with no window
       left), so this cell is what separates "the handle names nothing" from
       "the handle names a region this caller has already dropped" -- the two
       answers the doc promises for this row, one from the row and one from
       the machine. */
    movabs $0x100000000, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x50, %rax
    int $0x84
    mov %rax, 12472(%rbx)
    /* NO-EFFECT, cell 23: the refused unmap must not have dropped a window,
       and the window it could have dropped must still be USABLE -- not just
       still named. The caller holds the region the KERNEL mapped at boot
       (svc84_proof::PROOF_WITNESS, CODE+0x4_0000) and ADOPTED into its table
       at the handle the kernel WROTE at BUF+16376 (10 with the gate's
       scratch disk attached, 9 without -- the index is a boot fact, so the
       program never names it), its first word the kernel's marker
       "WITNESS!". It was never asked for with 0x4f, so it costs no entry in
       the release ledger, and unlike the WINDOW leg's region it has a live
       window. Ask the real handle's geometry (0x4c) and read the marker
       THROUGH the base the kernel names. A 0x50 that dropped a window on its
       way to answering EBADF would leave this one unmapped: the geometry
       would still answer, but the read would fault ring 3, so the mutant
       cannot pass as a value. */
    mov 16376(%rbx), %rdi
    lea 12744(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4c, %rax
    int $0x84
    test %rax, %rax
    jne 8f
    mov 12744(%rbx), %rcx        /* the base the kernel answered for that handle */
    mov (%rcx), %rcx             /* the marker, through the live window */
    jmp 9f
8:  xor %rcx, %rcx               /* no geometry: the witness fails by name */
9:  mov %rcx, 12728(%rbx)

    /* Row 0x52 CAP_NOTIFY, handle 2^32: the badge family's EBADF arm, the
       first check before the shape and before the endpoint's own kind/right
       checks. It is the badge row whose oversized handle this class owns --
       0x51 CAP_BADGE answers EINVAL for its oversized a0 (a shape refusal,
       deliberately not this arm), so a cell there would pin a different
       class and the doc says which is which. a1 = 0 leaves the signal bits
       empty: the handle is the only malformed argument, so the answer cannot
       be explained by anything else. */
    movabs $0x100000000, %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x52, %rax
    int $0x84
    mov %rax, 12480(%rbx)
    /* NO-EFFECT, cell 24: handle 1 is still a live endpoint capability.
       0x4c on it is the wrong KIND (-EACCES), not a missing handle
       (-EBADF): the refusal consumed nothing and truncated nothing. */
    mov $1, %rdi
    lea 12744(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4c, %rax
    int $0x84
    mov %rax, 12736(%rbx)

    /* OUT OF THE MATRIX: give the boot-adopted witness capability back
       (0x4d, the handle at BUF+16376). The region was created by the KERNEL
       (`owned: false`), so this close frees no frames and spends no entry in
       the release ledger -- what it protects is the EXIT leg's pin that this
       process exits holding exactly ONE region capability (the WINDOW
       leg's). A close that failed would leave two, and that leg would say
       so (`released 2`) instead of the proof silently leaking a handle. */
    mov 16376(%rbx), %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4d, %rax
    int $0x84

    lea 11688(%rbx), %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x5f, %rax
    int $0x84
    mov %rax, 11736(%rbx)

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
