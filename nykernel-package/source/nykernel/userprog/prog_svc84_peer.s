/*
 * prog_svc84_peer.s -- the SECOND ring-3 process of the 0x84 proof.
 *
 * Why this file exists: up to 2026-11-06 every ring-3 proof in this tree
 * ran ONE program, and every capability proof's "two parties" were the
 * kernel and that program. A blocking CALL had the same shape -- a
 * scheduler task parked while a KERNEL task answered it. Neither says
 * anything about two PROCESSES, and that is the state a real service
 * surface serves. So this program is spawned as its own scheduler task (a
 * real ring-3 task, with its own trap stack), in the same address space as
 * `prog_svc84.s` (this tree has no per-process address space yet), holding
 *
 *   handle 0  its OWN endpoint -- the global endpoint the caller names --
 *             whose owner capability the caps RECV row checks
 *   handle 1  the SHARED REGION, granted here as a SECOND holder (the
 *             first process holds two capabilities over the same region,
 *             so the region has three live holders over two processes)
 *
 * and it runs the OTHER half of a rendezvous the first process parks in
 * the middle of:
 *
 *   1. SEND to mailbox 0 (IPC row 0x45, seq 11). Mailbox 0 is the first
 *      process's endpoint, and the first process is PARKED in a blocking
 *      receive on it at this moment -- so this send is what wakes it, and
 *      the value it wakes with is this message. Nothing in the first
 *      process is spinning: it is off-CPU, inside a trap, before any of
 *      its capability steps ran.
 *   2. SHM_GEOMETRY (0x4c, handle 1) -> the record at PEN+8, base PEN+32,
 *      length PEN+40. The base it then dereferences is the one the KERNEL
 *      reported, not a constant compiled here.
 *   3. a BLOCKING receive on mailbox 1 (our OWN endpoint) with a budget of
 *      one wake: we PARK here, because the first process has not called
 *      yet. This is the hand-back half of the rendezvous: the first
 *      process's "go" send is what resumes us.
 *   3b. THE REGION HANDOVER (2026-11-07): a CAP_RECV (0x48) on endpoint 1
 *      drains the message whose dedicated TRANSFER FIELD carries the
 *      region capability the first process ASKED for (0x47), and the
 *      kernel installs it in THIS table (out-pointer PEN+144). The
 *      geometry row then answers through THAT handle (PEN+160..+176), we
 *      read the first process's marker at offset 0 (PEN+184 -- written by
 *      it BEFORE it closed its own capability), write our own at offset 8
 *      (PEN+192), and CLOSE last (PEN+200), which is what returns the
 *      pages. The first process's close did not: the queued copy counted.
 *   4. read offset 8 through the region mapping -> PEN+48. The first
 *      process wrote that value (0xfeedfacedeadbeef) BEFORE this process
 *      ran again, so a non-zero value here is one ring-3 process reading
 *      another's write through a capability.
 *   5. RECV the caller's request on endpoint 1, r8 as the handle
 *      out-pointer -> byte count PEN+56, the reply capability's handle
 *      PEN+64, the request's bytes PEN+72. It RETRIES while it is refused
 *      -ENODATA and counts the retries at PEN+80: the retry count is a
 *      report on the race, not a workaround for a hang (the caller's call
 *      is queued before it parks, and the "go" send arrived after it).
 *   6. write 0xbeefcafedead1234 at offset 16 through the mapping -> PEN+96.
 *      The kernel reads THAT offset out of the region after both processes
 *      are gone, so a value only this process could have written is
 *      evidence the sharing is real. (PEN+48 is the same claim the other
 *      way round.)
 *   7. REPLY (0x4b) through the transferred handle: "fromB!" -> PEN+88.
 *      Answering is what unblocks the caller -- which is parked INSIDE its
 *      trap, on a stack this process never touches.
 *
 * Then it EXITs (0x4e): the capability rows release its table (so the
 * region loses a holder, counted rather than assumed) and the scheduler
 * terminates this task. It never returns, and it never reports anything to
 * the console -- its evidence is PEN, which the kernel reads after the
 * process is gone.
 */

/* Derived from the linker script's load address (linker_svc84_peer.ld), the
   same way prog_svc84.s derives BUF: hand-copying a page address is a
   digit-count bug waiting to happen. */
.set ANCHOR, 0x70000000000
.set PEN,    ANCHOR + 0x10000

/* 0xbeefcafedead1234 -- the value the kernel reads out of the region.
   Written as one movabs so the constant appears exactly once. */
.set PEER_WRITE, 0xbeefcafedead1234

/* The seq this process wakes the parked receiver with, and the caps RECV
   retry bound (bounded so a broken machine reports instead of hanging). */
.set WAKE_SEQ, 11
.set RECV_TRIES, 1024

.section .text
.global _start
_start:
    /* The kernel maps PEN writable + user-accessible (svc84_proof::PEER_BUF
       is the same expression). */
    movabs $PEN, %rbx

    /* 1. The WAKE: SEND to mailbox 0 (the first process's endpoint) in the
       surface's (sender, seq) form -- rsi is the seq, a2 = 0 keeps that
       form, r10 = -1 is the no-transfer claim. */
    xor %rdi, %rdi
    mov $WAKE_SEQ, %rsi
    xor %rdx, %rdx
    xor %r8, %r8
    movabs $-1, %r10
    mov $0x45, %rax
    int $0x84
    mov %rax, 112(%rbx)

    /* 2. SHM_GEOMETRY (0x4c) on handle 1: record at PEN+8. */
    mov $1, %rdi
    lea 8(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4c, %rax
    int $0x84
    mov %rax, 0(%rbx)
    mov 8(%rbx), %rcx
    mov %rcx, 32(%rbx)          /* base, as the kernel reported it */
    mov 16(%rbx), %rcx
    mov %rcx, 40(%rbx)          /* length */

    /* 3. The hand-back: BLOCKING RECV on OUR endpoint (mailbox 1) with a
       budget of one wake. This PARKS. The first process's "go" send is what
       resumes it -- with the message, so the answer is a value. */
    mov $1, %rdi
    mov $1, %rsi                /* empty-wake budget */
    xor %rdx, %rdx
    mov $1, %r10                /* the blocking dial */
    xor %r8, %r8
    mov $0x46, %rax
    int $0x84
    mov %rax, 120(%rbx)

    /* 3b. THE HANDOVER (2026-11-07): the first process ASKED the kernel
       for a region and then SENT its capability to us in the transfer
       field (0x47, attenuated to READ|WRITE). This drains that message
       and installs the capability in OUR table -- r8 is the out-pointer
       for the handle, so the index is the machine's, not a constant here.

       The send is queued BEFORE the "go" above, so this drain is not a
       race; the retry loop is the same bounded instrument step 5 uses, so
       a machine that answered ENODATA forever would REPORT a count instead
       of hanging. Everything this step learns goes to PEN+136..+208. */
    xor %r9, %r9                /* retry counter */
    mov %r9, 208(%rbx)          /* stored even when the FIRST try wins */
.xfer_retry:
    mov $1, %rdi
    lea 216(%rbx), %rsi         /* payload buffer */
    mov $64, %rdx
    movabs $-1, %r10
    lea 144(%rbx), %r8          /* OUT-POINTER for the installed handle */
    mov $0x48, %rax
    int $0x84
    mov %rax, 136(%rbx)
    cmp $-61, %rax              /* -ENODATA: authorized, box still bare */
    jne .xfer_done
    inc %r9
    mov %r9, 208(%rbx)
    cmp $RECV_TRIES, %r9
    jge .xfer_done
    jmp .xfer_retry
.xfer_done:

    /* 3c. The geometry of the region we were GIVEN (0x4c), through the
       handle the machine installed: base PEN+160, length PEN+168, rights
       PEN+176. The rights are READ|WRITE -- the sender's GRANT did not
       travel, which is what attenuation means on this kind. */
    mov 144(%rbx), %rdi
    lea 160(%rbx), %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4c, %rax
    int $0x84
    mov %rax, 152(%rbx)

    /* 3d. The OTHER process's marker, read through the mapping it handed
       on -- written BEFORE it gave its own capability back. A non-zero
       value here is two ring-3 processes sharing one page ACROSS a
       capability handover, not a constant either side chose. */
    mov 160(%rbx), %rcx
    mov (%rcx), %rcx
    mov %rcx, 184(%rbx)

    /* 3e. OUR write at offset 8. The kernel's releaser reads the last
       region's first TWO words out of the mapping before unmapping, so a
       value only this process wrote ends up in the kernel's record. */
    movabs $0x1122334455667788, %rcx
    mov 160(%rbx), %rdx
    mov %rcx, 8(%rdx)
    mov %rcx, 192(%rbx)

    /* 3f. Give the handle back: the first process closed its own already,
       so this IS the last holder -- the pages are unmapped and the frames
       returned to the allocator here, while this process keeps running. */
    mov 144(%rbx), %rdi
    xor %rsi, %rsi
    xor %rdx, %rdx
    movabs $-1, %r10
    xor %r8, %r8
    mov $0x4d, %rax
    int $0x84
    mov %rax, 200(%rbx)

    /* 4. The FIRST process's region write, read through the mapping this
       process was told about (PEN+32 is the kernel's base). */
    mov 32(%rbx), %rcx
    mov 8(%rcx), %rcx
    mov %rcx, 48(%rbx)

    /* 5. RECV the caller's call. r8 is the OUT-POINTER for the transferred
       reply capability (not a rights mask), r10 = -1 claims no transfer of
       our own. rdi is the ENDPOINT ID (1), not a handle: the row checks
       that this caller owns the endpoint, and endpoint 0 belongs to the
       OTHER process -- passing 0 here was refused -EACCES by name on the
       first run. -ENODATA (-61) means "authorized, box still bare": retry,
       counting. */
    xor %r9, %r9                /* retry counter */
.recv_retry:
    mov $1, %rdi
    lea 72(%rbx), %rsi
    mov $64, %rdx
    movabs $-1, %r10
    lea 64(%rbx), %r8
    mov $0x48, %rax
    int $0x84
    mov %rax, 56(%rbx)
    cmp $-61, %rax
    jne .recv_done
    inc %r9
    mov %r9, 80(%rbx)
    cmp $RECV_TRIES, %r9
    jge .recv_done
    jmp .recv_retry
.recv_done:

    /* 6. OUR write, at the offset the kernel reads back. */
    movabs $PEER_WRITE, %rcx
    mov 32(%rbx), %rdx
    mov %rcx, 16(%rdx)
    mov %rcx, 96(%rbx)

    /* 7. The ANSWER, through the handle the kernel installed at PEN+64.
       One reply, and this is the caller's wake. */
    movabs $0x000021426d6f7266, %rcx   /* "fromB!" little-endian */
    mov %rcx, 128(%rbx)
    mov 64(%rbx), %rdi
    lea 128(%rbx), %rsi
    mov $8, %rdx
    xor %r8, %r8
    movabs $-1, %r10
    mov $0x4b, %rax
    int $0x84
    mov %rax, 88(%rbx)

    /* 8. EXIT (0x4e): no arguments, no return. */
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
