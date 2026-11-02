/* prog_livesh.s -- `livesh`: process 0 of the SECOND pass of the live ISO (1-49). The first pass is the whole boot demo chain with its scripted keyboard demo, exactly as in the
 * test build; when that chain has finished the live kernel starts this program instead of `init`. It does only what the end of `init` does for the keyboard session: a
 * container for the interactive `shell` (C6: Endpoint|SharedMem|Reply|FrameBuffer, with ALL 768 rows since 1-51: the full-screen window), one for the router (C5: admits Input), the expanded shell
 * (go = 2) and the never-ending router `kbdlive` holding the Input capability and a SEND capability to the shell. 1-52: it also starts the clock service `rtcd` (the only holder of the CMOS ports). Then it exits; the shell and the router keep running. */
.set DATA, 0x40000000000
.macro SENDQ h, buf, len, xfer, rights, slot
8:  mov $1, %rax; mov \h, %rdi; lea \buf, %rsi; mov \len, %rdx; mov \xfer, %r10; mov \rights, %r8; int $0x84
    cmp $-7, %rax; jne 9f
    mov $0, %rax; int $0x84
    jmp 8b
9:  mov %rax, \slot
.endm
.macro RUNSHELL width, hslot, rslot, go=1
    movq $\width, 0xe60(%rbx); movq $\go, 0xe50(%rbx)
    mov $14, %rax; lea n_desk(%rip), %rdi; mov $4, %rsi; mov %r15, %rdx; int $0x84; mov %rax, \hslot; mov %rax, %r14
    SENDQ %r14, 0xe60(%rbx), $8, 0xa0(%rbp), $12, \rslot
    SENDQ %r14, 0xe60(%rbx), $8, 0x218(%rbp), $1, 0x258(%rbp)                                          /* 1-60: a SEND capability to the NyFS service (desk is its only client) */
    SENDQ %r14, 0xe60(%rbx), $8, 0x210(%rbp), $12, 0x260(%rbp)                                         /* ... and the file-data region it shares with that service */
    SENDQ %r14, 0xe60(%rbx), $8, 0x2a0(%rbp), $12, 0x2a8(%rbp)                                         /* 1-62: ... and the game-image region */
5:  mov $9, %rax; mov %r14, %rdi; lea 0xe50(%rbx), %rsi; mov $8, %rdx; lea 0xe68(%rbx), %r10; mov $8, %r8; int $0x84
    cmp $-7, %rax; jne 6f                                                                              /* 1-62: the endpoint queue holds 4 messages and four capabilities were just sent -- a full queue (-7) must be retried, */
    mov $0, %rax; int $0x84; jmp 5b                                                                    /* never dropped: desk would then take the NEXT sender's message (rtcd's first TIME) as its start-up CALL */
6:
.endm
.section .text
.global _start
_start:
    movabs $DATA, %rbx
    movabs $0x40000010100, %rbp
    mov $22, %rax; mov $127, %rdi; lea -64(%rsp), %rsi; int $0x84; cmp $-13, %rax; je .done      /* no Input path in this configuration: nothing to run */
    /* 1-60 STORAGE: the user-space ATA driver `atadm` (only holder of the IDE ports 0x1F0..0x1F7, narrowed from the bootstrap I/O-port capability) serves ONE badged client, the NyFS
     * service `nyfs`, whose volume is the 138 sectors at LBA 1900 (free in the demo chain, so it starts empty). The desktop `desk` is nyfs's client: it receives only a SEND capability and the file-data region. */
    mov $11, %rax; mov $2, %rdi; mov $0x1f0, %rsi; mov $8, %rdx; int $0x84; mov %rax, 0x200(%rbp)  /* ports 0x1F0..0x1F7 */
    mov $7, %rax; int $0x84; mov %rax, 0x208(%rbp)                                                  /* scratch region: nyfs <-> driver */
    mov $7, %rax; int $0x84; mov %rax, 0x210(%rbp)                                                  /* file-data region: nyfs <-> desk */
    mov $7, %rax; int $0x84; mov %rax, 0x2a0(%rbp)                                                  /* 1-62: the game-image region: livesh (BOOT_FILE) -> desk (verifies it itself) */
    mov $8, %rax; mov 0x2a0(%rbp), %rdi; movabs $0x40000106000, %rsi; int $0x84
    mov $4, %rax; lea n_nyfs(%rip), %rdi; mov $4, %rsi; int $0x84; mov %rax, 0x218(%rbp)            /* spawn nyfs */
    mov $4, %rax; lea n_atadm(%rip), %rdi; mov $5, %rsi; int $0x84; mov %rax, 0x220(%rbp)           /* spawn the driver */
    movq $1, 0xe60(%rbx)
    SENDQ 0x220(%rbp), 0xe60(%rbx), $8, 0x200(%rbp), $12, 0x228(%rbp)                               /* driver msg1: the port capability + {1 client} */
    movq $1, 0xe60(%rbx); movq $1900, 0xe68(%rbx); movq $138, 0xe70(%rbx)
    SENDQ 0x220(%rbp), 0xe60(%rbx), $24, 0x208(%rbp), $12, 0x230(%rbp)                              /* the client: {badge 1, lo 1900, len 138} + its region */
    mov $16, %rax; mov 0x220(%rbp), %rdi; mov $1, %rsi; int $0x84; mov %rax, 0x238(%rbp)            /* BADGE 1 */
    movq $1900, 0xe60(%rbx)
    SENDQ 0x218(%rbp), 0xe60(%rbx), $8, 0x238(%rbp), $1, 0x240(%rbp)                                /* nyfs msg1: the badged driver capability + the volume base */
    SENDQ 0x218(%rbp), 0xe60(%rbx), $8, 0x208(%rbp), $12, 0x248(%rbp)                               /* nyfs msg2: the scratch region */
    SENDQ 0x218(%rbp), 0xe60(%rbx), $8, 0x210(%rbp), $12, 0x250(%rbp)                               /* nyfs msg3: the file-data region */
    mov $13, %rax; mov $113, %rdi; mov $4, %rsi; int $0x84; mov %rax, (%rbp)                      /* C5 */
    mov $13, %rax; mov $57, %rdi; mov $6, %rsi; int $0x84; mov %rax, 0x98(%rbp); mov %rax, %r15   /* C6 */
    mov $11, %rax; mov $3, %rdi; xor %esi, %esi; mov $768, %rdx; int $0x84; mov %rax, 0xa0(%rbp)  /* the shell's window: the whole screen (1-51) */
    RUNSHELL 1024, 0x128(%rbp), 0x130(%rbp), 3
    mov $14, %rax; lea n_kbdrt(%rip), %rdi; mov $5, %rsi; mov (%rbp), %rdx; int $0x84; mov %rax, 0x120(%rbp); mov %rax, %r14
    movq $0, 0xe60(%rbx)
    SENDQ %r14, 0xe60(%rbx), $8, $4, $4, 0x138(%rbp)
    SENDQ %r14, 0xe60(%rbx), $8, 0x128(%rbp), $1, 0x140(%rbp)
    mov $13, %rax; mov $21, %rdi; mov $4, %rsi; int $0x84; mov %rax, 0x148(%rbp); mov %rax, %r15     /* C7 (1-52): Endpoint|IoPort|Reply, for the clock service */
    mov $11, %rax; mov $2, %rdi; mov $0x70, %rsi; mov $2, %rdx; int $0x84; mov %rax, 0x150(%rbp)      /* the I/O-port capability narrowed to the CMOS ports 0x70..0x71 */
    mov $14, %rax; lea n_rtcd(%rip), %rdi; mov $4, %rsi; mov %r15, %rdx; int $0x84; mov %rax, 0x158(%rbp); mov %rax, %r14
    movq $0, 0xe60(%rbx)
    SENDQ %r14, 0xe60(%rbx), $8, 0x150(%rbp), $12, 0x160(%rbp)                                        /* msg1: the narrowed I/O-port capability (READ|WRITE) */
    SENDQ %r14, 0xe60(%rbx), $8, 0x128(%rbp), $1, 0x168(%rbp)                                         /* msg2: a SEND capability to the shell */
    mov $300, %r12d
1:  mov $0, %rax; int $0x84; dec %r12d; jnz 1b
    /* 1-60 LAUNCHER SERVICE (Console Hub "Programs"): livesh is the root process (the only one allowed BOOT_FILE / CONTAINER_CREATE), so it stays alive as the desktop's launcher with a LONG-POLL
     * protocol that needs no extra capability: it CALLs desk {100, last result, last program}; desk keeps the reply capability and answers only when the user clicks a program ({index}).
     * livesh then does exactly what `init` does for a PE / DEX file: BOOT_FILE copies the file into a shared region, a fresh `peload` / `dexload` is spawned INTO a container that admits only
     * Endpoint|SharedMem|Reply (quota 2), is handed the region and CALLed with the length; its reply (-20 = refused, 0 = verified and running) is the next poll's result. */
    mov $13, %rax; mov $25, %rdi; mov $2, %rsi; int $0x84; mov %rax, 0x270(%rbp)                    /* the container for loaded programs */
    mov $7, %rax; int $0x84; mov %rax, 0x278(%rbp)                                                  /* the file region */
    mov $8, %rax; mov 0x278(%rbp), %rdi; movabs $0x40000105000, %rsi; int $0x84; mov %rax, 0x280(%rbp)
    movq $0, 0x288(%rbp); movq $-1, 0x290(%rbp); movq $0, 0x2b0(%rbp)                                                    /* nothing launched yet: program -1 */
.poll:
    movq $100, 0xe80(%rbx); mov 0x288(%rbp), %rax; mov %rax, 0xe88(%rbx); mov 0x290(%rbp), %rax; mov %rax, 0xe90(%rbx)
    mov $26, %rax; lea 0xe98(%rbx), %rdi; int $0x84                                                  /* SYSINFO (root-only, read-only): {secure boot, digest, image length, ...} */
    mov 0x2b0(%rbp), %rax; mov %rax, 0xeb8(%rbx)                                                    /* word 7: the last program's output (bit 63 = valid, low 32 bits = value) */
    mov $9, %rax; mov 0x128(%rbp), %rdi; lea 0xe80(%rbx), %rsi; mov $64, %rdx; lea 0xe98(%rbx), %r10; mov $8, %r8; int $0x84
    cmp $-7, %rax; jne 2f
    mov $0, %rax; int $0x84; jmp .poll
2:  cmp $8, %rax; jne .done                                                                         /* desk is gone */
    mov 0xe98(%rbx), %rcx; cmp $100, %rcx; je .snap; cmp $101, %rcx; je .killc; cmp $200, %rcx; jb 9f; cmp $300, %rcx; jb .killp; jmp .poll
9:  cmp $11, %rcx; jae .poll                                                  /* not a program index: poll again */
    movq $0, 0x2b0(%rbp)
    mov %rcx, 0x290(%rbp)                                                                           /* which program (reported with its result at the next poll) */
    shl $5, %rcx; lea apptab(%rip), %r13; add %rcx, %r13                                            /* entry: name[16], len, loader (0 PE, 1 DEX) */
    movabs $0x40000105000, %rdx; cmpq $2, 24(%r13); jne 6f; movabs $0x40000106000, %rdx           /* a game image goes to the region it shares with desk */
6:  mov $19, %rax; mov %r13, %rdi; mov 16(%r13), %rsi; mov $4096, %r10; int $0x84
    mov %rax, 0xe50(%rbx); mov %rax, 0x298(%rbp); test %rax, %rax; js .launched                    /* no such boot file: the (negative) errno is the result */
    cmpq $2, 24(%r13); je .launched                                                                 /* 1-62: a .nygi image is not run: its size is the result and desk verifies the bytes itself */
    movabs $0x40000105000, %rdx; movq $0, 0xff0(%rdx)                                               /* 1-69: clear the output slot at the page's tail */
    mov 24(%r13), %rax; test %rax, %rax; jnz 3f
    lea n_peload(%rip), %rdi; mov $6, %rsi; jmp 4f
3:  lea n_dexload(%rip), %rdi; mov $7, %rsi
4:  mov $14, %rax; mov 0x270(%rbp), %rdx; int $0x84; mov %rax, %r14; test %rax, %rax; js .spawnfail
    SENDQ %r14, 0xe60(%rbx), $8, 0x278(%rbp), $12, 0xe58(%rbx)
    mov $9, %rax; mov %r14, %rdi; lea 0xe50(%rbx), %rsi; mov $8, %rdx; lea 0xe68(%rbx), %r10; mov $8, %r8; int $0x84
    mov 0xe68(%rbx), %rcx; cmp $8, %rax; je 5f
    mov %rax, %rcx
5:  mov %rcx, 0x298(%rbp)
    test %rcx, %rcx; jnz .launched                                                                  /* 1-70: a program (PE or DEX) that loaded: wait until it publishes its result (exit code / return value) or 150 yields have passed, whichever is first */
    movq $150, 0x2b8(%rbp)
7:  movabs $0x40000105000, %rdx; mov 0xff4(%rdx), %eax; test %eax, %eax; js 8f
    mov $0, %rax; int $0x84; decq 0x2b8(%rbp); jnz 7b; jmp .launched
8:  mov 0xff0(%rdx), %eax; movabs $0x8000000000000000, %rcx; or %rcx, %rax; mov %rax, 0x2b0(%rbp); jmp .launched
.snap:                                                                                              /* 1-70: PROCINFO for every process -> 16-byte records in the page shared with desk: alive, container, blocked, pages (clamped), name at +8 */
    movq $100, 0x290(%rbp); movq $0, 0x2c0(%rbp)
1:  mov $27, %rax; mov 0x2c0(%rbp), %rdi; lea 0xf00(%rbx), %rsi; int $0x84
    test %rax, %rax; jnz 2f
    mov 0x2c0(%rbp), %rcx; shl $4, %rcx; movabs $0x40000106000, %rdx; add %rcx, %rdx
    mov 0xf00(%rbx), %rax; mov %al, (%rdx); mov 0xf08(%rbx), %rax; mov %al, 1(%rdx); mov 0xf10(%rbx), %rax; mov %al, 2(%rdx)
    mov 0xf18(%rbx), %rax; cmp $255, %rax; jbe 3f; mov $255, %eax
3:  mov %al, 3(%rdx); mov 0xf20(%rbx), %rax; mov %rax, 8(%rdx)
    incq 0x2c0(%rbp); cmpq $88, 0x2c0(%rbp); jb 1b
2:  mov 0x2c0(%rbp), %rax; mov %rax, 0x288(%rbp); jmp .poll
.killc:                                                                                             /* 1-70: CONTAINER_KILL of the loaders' container -> number killed */
    movq $101, 0x290(%rbp); mov $15, %rax; mov 0x270(%rbp), %rdi; int $0x84; mov %rax, 0x288(%rbp); jmp .poll
.killp:                                                                                             /* 1-71: PROC_KILL of one process (desk asked with 200 + pid) -> 1 or a negative errno */
    movq $102, 0x290(%rbp); lea -200(%rcx), %rdi; mov $28, %rax; int $0x84; mov %rax, 0x288(%rbp); jmp .poll
.spawnfail:
    mov %rax, 0x298(%rbp)
.launched:
    mov 0x298(%rbp), %rax; mov %rax, 0x288(%rbp)                                                    /* result for the next poll; 0x290 = which program */
    jmp .poll
.done:
    mov $3, %rax; int $0x84                                       /* EXIT */
.hang:
    jmp .hang
n_shell: .ascii "shell"
n_desk: .ascii "desk"
n_kbdrt: .ascii "kbdrt"
n_rtcd: .ascii "rtcd"
n_nyfs: .ascii "nyfs"
n_atadm: .ascii "atadm"
n_peload: .ascii "peload"
n_dexload: .ascii "dexload"
.balign 32
apptab:
    .ascii "hello.exe"; .fill 7,1,0; .quad 9, 0
    .ascii "pewin.exe"; .fill 7,1,0; .quad 9, 0
    .ascii "pereloc.exe"; .fill 5,1,0; .quad 11, 0
    .ascii "bad_mz.exe"; .fill 6,1,0; .quad 10, 0
    .ascii "hello.dex"; .fill 7,1,0; .quad 9, 1
    .ascii "ops.dex"; .fill 9,1,0; .quad 7, 1
    .ascii "calls.dex"; .fill 7,1,0; .quad 9, 1
    .ascii "dex_magic.dex"; .fill 3,1,0; .quad 13, 1
    .ascii "demo.nygi"; .fill 7,1,0; .quad 9, 2
    .ascii "bad.nygi"; .fill 8,1,0; .quad 8, 2
    .ascii "spin.exe"; .fill 8,1,0; .quad 8, 0                                                    /* 1-71: index 10, not in the Console Hub list: started by the terminal's `spawn spin` */
