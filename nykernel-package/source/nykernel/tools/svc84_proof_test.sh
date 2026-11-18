#!/usr/bin/env bash
#
# svc84_proof_test.sh -- proves the 0x84 service gate is callable from RING 3.
#
# The kernel's own boot self-test calls `dispatch` from ring 0, which proves
# the answers and the refusals but not the privilege transition. This gate
# boots with `nyrqis.proof=svc84`, so the kernel loads a separately-built
# user program (userprog/prog_svc84.s) into its own address space, enters
# ring 3, and lets THAT program make the calls. The kernel then reads the
# program's stored results back out of user memory and compares them with
# what it returned.
#
# Asserted, each on its own line:
#   - the program was loaded and its results/stack pages mapped user-accessible;
#   - the gate saw CPL 3 (not ring 0 leaking into the proof);
#   - GFX_MODE and INPUT_STATE answered, and the program's stored copies match;
#   - an UNPORTED call answered -ENOSYS rather than zeroes;
#   - the pointer-taking call passed the user-range validator, wrote the
#     geometry, and the program's memory reads back that exact geometry;
#   - the PROCESS_SELF identity call answered the scheduler's own running-
#     task id and that value reads back from the program's memory;
#   - the four WIDENED argument registers (rsi/rdx/r10/r8, the release's
#     five-argument ABI shape) survived the trap boundary into the
#     dispatcher exactly as the program sent them -- the differential
#     check of the stub's widened pass;
#   - the IPC rows (SEND 0x45 / RECV 0x46) round-trip ONE message through
#     the kernel's real mailbox primitive (experimental::ipc): queued in
#     ring 0, drained in ring 3, and the program's stored copy of the
#     (sender << 32) | seq return matches what the drain unpacked. The
#     shape refusal (-EINVAL) and the empty-endpoint answer (-ENODATA,
#     distinct from the self-test's -ENOENT for a nonexistent endpoint)
#     are each observed by the program.
#   - the CAPABILITY rows (0x47 / 0x48) round-trip ONE payload through the
#     kernel's real handle-table machine (experimental::caps): the boot
#     equip granted the program's GATE SESSION (its identity on these
#     rows, its OWN table -- the session machine's first slice) a
#     SEND|GRANT capability for endpoint 0, the program sends "nyrqis"
#     through HANDLE 1, receives it back whole into its own memory (first
#     8 bytes zero-tailed), and the kernel compares the drained payload's
#     record against what the program actually holds. A forged handle
#     (empty slot 31) refuses -EBADF and an oversize payload claim refuses
#     -EMSGSIZE, each observed by name in the program's own buffer.
#
# The proof mode does not return to the kernel (it ends in a ring-3 spin),
# so this harness waits for the verdict line and then stops QEMU.
#
# REPLAY AND META-TESTING
#
# The verdict checks below are a function of ONE thing: the serial log. So
# they can be run against a saved transcript with no QEMU at all:
#
#   SVC84_REPLAY_LOG=target/svc84-proof-last.log ./tools/svc84_proof_test.sh --replay LOG
#
# That is not a convenience. It is what makes the gate testable: a gate is a
# claim that a wrong kernel fails it, and tools/gate_selfcheck.sh checks that
# claim by deleting one piece of evidence at a time from a real transcript and
# requiring this harness to FAIL. A gate that cannot be shown to fail is a
# comment. The `--selfcheck` of that tool is the meta-gate; this mode is the
# seam it needs.
#
# Usage: ./tools/svc84_proof_test.sh [--replay <serial-log>]

set -uo pipefail
cd "$(dirname "$0")/.."

REPLAY=""
if [[ "${1:-}" == "--replay" ]]; then
    REPLAY="${2:-}"
    [[ -n "$REPLAY" ]] || { echo "usage: $0 --replay <serial-log>"; exit 2; }
    [[ -f "$REPLAY" ]] || { echo "FAIL: no such transcript: $REPLAY"; exit 2; }
elif [[ -n "${1:-}" ]]; then
    echo "usage: $0 [--replay <serial-log>]"; exit 2
fi

WORK=target/svc84-proof
rm -rf "$WORK"
mkdir -p "$WORK/boot/grub"

FAILS=0
ok()  { echo "  OK: $*"; }
bad() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }

command -v qemu-system-x86_64 >/dev/null 2>&1 || { echo "FAIL: qemu-system-x86_64 missing"; exit 1; }
command -v grub-mkrescue >/dev/null 2>&1 || { echo "FAIL: grub-mkrescue missing"; exit 1; }

# Everything that needs a machine lives here, so the verdict checks
# below can also run against a saved transcript (see REPLAY above).
run_boot() {
    # Replay needs none of this; a real boot needs all of it, named.
    ./tools/gate_env.sh --quiet || { echo "FAIL: the gate environment is not ready"; exit 1; }
echo "==> building the kernel"
if ! ./build.sh >"$WORK/build.log" 2>&1; then
    echo "FAIL: ./build.sh failed (see $WORK/build.log)"
    tail -20 "$WORK/build.log"
    exit 1
fi

cp target/kernel.elf "$WORK/boot/kernel.elf"
cat > "$WORK/boot/grub/grub.cfg" <<'EOF'
set timeout=0
set default=0
menuentry "Nyrqis (0x84 ring-3 proof)" {
    multiboot2 /boot/kernel.elf nyrqis.proof=svc84
    boot
}
EOF
grub-mkrescue -o "$WORK/svc84.iso" "$WORK" >/dev/null 2>&1 \
    || { echo "FAIL: grub-mkrescue failed"; exit 1; }

SERIAL="$WORK/serial.log"
rm -f "$SERIAL"
# The WRITABLE scratch disk for the block-write leg: 4 MiB of raw sectors
# on the SECONDARY IDE channel (index 2 = secondary master), so the boot
# disc stays the primary master and the kernel sees it as channel 1. It is
# recreated every run, so the proof starts from a known image and a failed
# write cannot leave state behind.
SCRATCH="$WORK/scratch.img"
rm -f "$SCRATCH"
truncate -s 4M "$SCRATCH"
# `setsid`: the emulator gets its own session, so the teardown below can
# take down the WHOLE group. Under a user-space root (proot) the wrapper does
# not forward SIGTERM to the emulator it traces, and a plain `kill` on the
# wrapper left qemu running and `wait` blocked forever -- the gate hung after
# the proof had already finished, with no verdict printed.
setsid qemu-system-x86_64 -m 512 \
    -drive file="$WORK/svc84.iso",format=raw,if=ide,index=0,media=cdrom \
    -drive file="$SCRATCH",format=raw,if=ide,index=2,media=disk -boot d \
    -serial "file:$SERIAL" -display none -no-reboot \
    >"$WORK/qemu.log" 2>&1 &
QPID=$!

waited=0
while [[ $waited -lt 240 ]]; do
    # Wait for the verdict to be FINISHED, not merely started. The old
    # condition broke on the verdict's own prefix, and a line that long is
    # written in pieces: the kill below could land in the middle of it and
    # leave a transcript whose last leg is half a line -- which reads as
    # "22 legs instead of 27" and blames the kernel for the harness's
    # impatience. The terminal word is the print's last argument, so its
    # presence means the line is whole.
    grep -qE "service-0x84 proof: a ring-3 program.*-- (OK|FAILED)" "$SERIAL" 2>/dev/null && break
    grep -q "KERNEL PANIC" "$SERIAL" 2>/dev/null && break
    sleep 0.5
    waited=$((waited + 1))
done
kill -- -"$QPID" 2>/dev/null
sleep 1
kill -9 -- -"$QPID" 2>/dev/null
wait "$QPID" 2>/dev/null
}

if [[ -n "$REPLAY" ]]; then
    # No QEMU, no build: the checks are a pure function of the transcript.
    SERIAL="$REPLAY"
    echo "==> replaying a saved transcript: $SERIAL"
else
    run_boot
fi

# The distinct legs the kernel claims to have proved. This is pinned because
# a leg that stops running must not become a smaller, quieter verdict: every
# line below can still say "matches" while one whole leg is missing.
# 26 distinct `<NAME> matches` facts in the verdict as of the display/session
# increment (ARGS, BLOCK, BLOCK-CACHE, BLOCK-INFO, BLOCK-ROOT, BLOCK-SCRATCH,
# BLOCK-SPAN, BLOCK-VOLUME, CAP, DATA-PLANE, DEVICE, DISPLAY, ENOSYS, GFX,
# INPUT, IPC, MAP, NOTIFY, PEER, PLATFORM, PLATFORM-INFO, PLATFORM-MODE,
# REFUSAL-MATRIX, REPLY, SELF, SHM, TWO-PROCESS). Raising it is part of adding
# a leg; a leg that stops running without this number moving is the failure
# this catches. REFUSAL-MATRIX arrived with the per-row matrix (2026-11-17) and
# grows by CELLS, not by legs: its row count is pinned separately below, so a
# cell that silently stopped being probed is not hidden by a stable leg count.
EXPECTED_LEGS="${SVC84_EXPECTED_LEGS:-27}"

if grep -q "KERNEL PANIC" "$SERIAL"; then
    bad "the kernel panicked before the proof finished"
    grep -A3 "KERNEL PANIC" "$SERIAL" | sed 's/^/        /' | head -6
fi

if grep -q "service-0x84 proof: program loaded" "$SERIAL"; then
    ok "the proof program was loaded and entered (results + stack pages mapped user-accessible)"
else
    bad "the kernel never loaded the ring-3 proof program"
    tail -6 "$SERIAL" 2>/dev/null | sed 's/^/        /'
fi

# The verdict is several lines (the original ring-3 transcript, BADGE/NOTIFY,
# the device/data-plane/block legs, and the platform identity rows). Join
# them; every pattern below is a substring. The tail bound is generous on
# purpose: a row that joins the surface must not push an earlier row's line
# out of the window its assertion reads.
VERDICT="$(grep -E "service-0x84 proof: (a ring-3 program|BADGE/NOTIFY|DEVICE|DATA-PLANE|BLOCK|PLATFORM|DISPLAY|REFUSAL-MATRIX)" "$SERIAL" | tail -40 | tr '\n' ' ')"
if [[ -z "$VERDICT" ]]; then
    bad "the ring-3 proof never reached its verdict line"
else
    echo "  ---- verdict:"
    echo "        $VERDICT"
    if [[ "$VERDICT" == *"CPL 3"* ]]; then
        ok "the call came from ring 3 (CPL 3), not from the kernel's own self-test"
    else
        bad "the verdict does not report CPL 3"
    fi
    if [[ "$VERDICT" == *"refused, not answered"* ]] && [[ "$VERDICT" == *"ENOSYS matches"* ]]; then
        ok "an unported call was refused with -ENOSYS and the program saw that value"
    else
        bad "the unported call's refusal was not observed by the program"
    fi
    if [[ "$VERDICT" == *"GFX matches"* ]] && [[ "$VERDICT" == *"INPUT matches"* ]]; then
        ok "GFX_MODE and INPUT_STATE round-tripped through the program's own memory"
    else
        bad "the served calls' results did not read back from user memory"
    fi
    if [[ "$VERDICT" == *"geometry matches"* ]]; then
        ok "the validated write landed: the program's buffer holds the display geometry"
    else
        bad "the pointer-taking call's write did not read back"
    fi
    if [[ "$VERDICT" == *"SELF matches"* ]]; then
        ok "the identity call answered the running scheduler task id and it read back"
    else
        bad "the PROCESS_SELF result did not read back from user memory"
    fi
    if [[ "$VERDICT" == *"ARGS matches"* ]]; then
        ok "the widened rsi/rdx/r10/r8 pass reached the dispatcher exactly as sent"
    else
        bad "the widened argument pass did not round-trip through the trap boundary"
    fi
    # recv/want 0x700000007: with the session machine generalized, the
    # ring-3 send's message carries the KERNEL-DECLARed caller identity
    # -- the recorded gate session (7), not whoever owns the running
    # slot -- so the recv answer is (sender << 32) | seq with sender = 7.
    # The blocking dial's legs (2026-11-06): 0x46 with a_xfer=1 is SERVED
    # for its instant half -- the queued message (seq 42) answers at once
    # with the SESSION sender, so blocked == want-blocked == 0x70000002a;
    # the empty retry is refused -EAGAIN (= 18446744073709551605 as the
    # unsigned rendering) and the dead destination -ENOENT
    # (= 18446744073709551614) -- by name, never parked, never "empty".
    if [[ "$VERDICT" == *"IPC matches"* ]] && [[ "$VERDICT" == *"send-ack 0x0"* ]] && [[ "$VERDICT" == *"recv 0x700000007"* ]] && [[ "$VERDICT" == *"want 0x700000007"* ]] && [[ "$VERDICT" == *"blocked 0x70000002a"* ]] && [[ "$VERDICT" == *"want-blocked 0x70000002a"* ]] && [[ "$VERDICT" == *"again 34359738379"* ]] && [[ "$VERDICT" == *"baddest 18446744073709551614"* ]] && [[ "$VERDICT" == *"payload (ack 0 , len 8 , bytes 0x2164616f6c796170 , wide 18446744073709551526 , null 18446744073709551602 , short 18446744073709551594)"* ]]; then
        ok "one sent message round-tripped through the kernel's real mailbox (session sender=7 seq=7 -> 0x700000007), the blocking dial drained a queued message at once (session sender=7 seq=42 -> 0x70000002a) and answered EAGAIN/ENOENT by name, and the shape/empty refusals were observed"
    else
        bad "the IPC rows' round trip or refusals failed their read-back"
    fi
    # recv 0x700000006: the record is (sender << 32) | len, and the sender
    # is the caller's GATE SESSION (7) -- the session machine's first
    # slice legitimately changed the sender claim from task 0's 0 to the
    # session's 7; the payload length is still 6.
    if [[ "$VERDICT" == *"CAP matches"* ]] && [[ "$VERDICT" == *"caps (ack 0x0"* ]] && [[ "$VERDICT" == *"recv 0x700000006"* ]] && [[ "$VERDICT" == *"forged 0xfffffffffffffff7"* ]] && [[ "$VERDICT" == *"payload 0x73697172796e"* ]]; then
        ok "one nyrqis payload round-tripped through the kernel's real capability machine (send via HANDLE 1, drained whole into the program's buffer) and the forged-handle/oversize refusals were observed by name"
    else
        bad "the capability rows' round trip or refusals failed their read-back"
    fi
    # The INPUT_POLL row's leg: two real polls of the byte stream the
    # kernel INJECTED through the controller (IRQ1 -> the raw ring)
    # (bytes 0x1e and 0x9e delivered whole, device keyboard), the third
    # poll's real-and-empty 0, and the forged handle's EBADF.
    if [[ "$VERDICT" == *"INPUT matches"* ]] && [[ "$VERDICT" == *"INPUT_POLL (1 0x1 , 2 0x1 , 3 0x0"* ]] && [[ "$VERDICT" == *"forged 0xfffffffffffffff7"* ]] && [[ "$VERDICT" == *"bytes 0x1e/0x9e"* ]]; then
        ok "the kernel's scancodes injected through the controller (IRQ1) were polled through the INPUT kind (HANDLE 2, bytes 0x1e/0x9e, device keyboard), the authorized-AND-bare third poll answered 0 with an untouched record buffer, and the forged handle was refused -EBADF"
    else
        bad "the INPUT_POLL row's polls, refusals or byte delivery failed their read-back"
    fi
    # The CALL/REPLY leg (the Reply KIND): the call answered the
    # endpoint's OWNER (the proof session, 7), the request arrived with a
    # one-shot reply capability whose handle the program read back out of
    # its own memory (3 -- the session's table indexes 0..2 are the
    # endpoint, its SEND cap and the INPUT cap), the reply was accepted
    # once, the SAME handle was then refused -EBADF, the wrong kind
    # (an endpoint handle asked to answer) was refused -EACCES, and the
    # answer came back at the caller's own endpoint with its bytes.
    if [[ "$VERDICT" == *"REPLY matches"* ]] && [[ "$VERDICT" == *"REPLY (call 0x7 , recv 0x700000008 , handle 0xb , reply 0x0 , twice 0xfffffffffffffff7 , kind 0xfffffffffffffff3 , answer 0x700000008 , bytes 0x2121726577736e61)"* ]]; then
        ok "one call carried a one-shot reply capability round-trip through the session's OWN table (handle 3), the answer landed back at the caller's endpoint, and the second reply and the wrong-kind reply were refused -EBADF/-EACCES by name"
    else
        bad "the CALL/REPLY leg's round trip or refusals failed their read-back"
    fi
    # The SHARED-REGION leg: the geometry the kernel reports is its OWN
    # mapping (base 0x60000030000, one page) and the rights the session's
    # capability carries (READ|WRITE|GRANT = 0xe); the sentinel is the word
    # the KERNEL wrote before ring 3, the program then wrote its own value
    # through the same mapping, and the kernel's read-back found that value
    # in the region -- two parties, one page. A handle that names nothing is
    # -EBADF and an endpoint handle asked for a region's geometry is
    # -EACCES.
    if [[ "$VERDICT" == *"SHM matches"* ]] && [[ "$VERDICT" == *"SHM (ack 0x0 , base 0x60000030000 , len 0x1000 , rights 0xe , sentinel 0x123456789abcdef , wrote 0xfeedfacedeadbeef , forged 0xfffffffffffffff7 , kind 0xfffffffffffffff3)"* ]]; then
        ok "the shared-memory kind reported the kernel's own mapping, ring 3 read the kernel's sentinel THROUGH it and wrote back a value the kernel then read out of the region, and the forged/wrong-kind handles were refused -EBADF/-EACCES by name"
    else
        bad "the shared-region leg's geometry, two-party bytes or refusals failed their read-back"
    fi
    # The RESOURCE-KIND leg: the block device's and framebuffer's answers
    # carry the kernel's kind bits (2 and 32) with the rights it minted
    # (0xe), the I/O-port capability's out-word names exactly the granted
    # ports, the framebuffer's names its ROW window, and an endpoint handle
    # asked for a device window and a payload-shaped device call were
    # refused -EACCES and -EINVAL by name.
    if [[ "$VERDICT" == *"DEVICE matches"* ]] && [[ "$VERDICT" == *"DEVICE (block 0x20000000e , ioport 0x40000000e window 0x8000000081 , fb 0x200000000e rows"* ]] && [[ "$VERDICT" == *"endpoint 0xfffffffffffffff3 , shape 0xffffffffffffffea)"* ]]; then
        ok "the resource kinds reported their kernel-minted authority (block-device code 2, I/O-port code 4 over ports 0x80..0x81, framebuffer code 32 over the bound rows) and refused a non-device handle -EACCES and a payload-shaped call -EINVAL"
    else
        bad "the resource-kind leg's windows, kind codes or refusals failed their read-back"
    fi
    # The DATA-PLANE leg: a byte written through the I/O-port capability
    # came back on the same port and matches the kernel's OWN read at
    # verdict time; one port past the window was refused -EACCES; a raw
    # framebuffer word survived a write/read round trip; a row outside the
    # granted window was refused -EACCES; and a framebuffer operation
    # through an I/O-port handle was refused -EACCES.
    if [[ "$VERDICT" == *"DATA-PLANE matches"* ]] && [[ "$VERDICT" == *"DATA-PLANE (port-write 0x0 , port-read 0x"* ]] && [[ "$VERDICT" == *"past-window 0xfffffffffffffff3 , fb-write 0x0 , fb-read 0xff00ff , fb-row 0xfffffffffffffff3 , fb-kind 0xfffffffffffffff3)"* ]]; then
        ok "the I/O-port and framebuffer data planes actually moved bytes: a port write/read round-tripped and matched the kernel's own read, a raw pixel word survived write/read, and the out-of-window and wrong-kind operations were refused -EACCES by name"
    else
        bad "the data-plane leg's port/pixel bytes or refusals failed their read-back"
    fi
    # The BLOCK-DEVICE leg: one sector read from the boot device at LBA 16
    # (the ISO volume descriptor) carried "CD001" back to ring 3, an LBA
    # past the window was refused -EACCES, a zero-length copy was refused
    # -EINVAL, and a block read through another kind's handle was refused
    # -EACCES.
    if [[ "$VERDICT" == *"BLOCK matches"* ]] && [[ "$VERDICT" == *"BLOCK (read 0x8 , lba16 0x1313030444301 (want 0x1313030444301) , past-window 0xfffffffffffffff3 , zero-len 0xffffffffffffffea , wrong-kind 0xfffffffffffffff3)"* ]]; then
        ok "the block-device data plane read one real sector from the boot device: LBA 16's ISO volume descriptor bytes (\"CD001\") reached ring 3, and the out-of-window, zero-length and wrong-kind claims were refused by name"
    else
        bad "the block-device leg's sector bytes or refusals failed their read-back"
    fi
    # The BLOCK-INFO leg: the drive's OWN geometry (2048-byte sectors, a
    # nonzero sector count, the reserved word zero) reached ring 3, and a
    # geometry call through another kind's handle was refused -EACCES.
    if [[ "$VERDICT" == *"BLOCK-INFO matches"* ]] && [[ "$VERDICT" == *"BLOCK-INFO (ack 0x0 , sector-size 0x800 , sectors 0x"* ]] && [[ "$VERDICT" == *" , reserved 0x0 , wrong-kind 0xfffffffffffffff3)"* ]]; then
        ok "the block-device geometry row asked the drive itself: 2048-byte sectors and a nonzero sector count reached ring 3, and the wrong-kind call was refused -EACCES"
    else
        bad "the block-info leg's geometry or refusal failed its read-back"
    fi
    # The BLOCK-SPAN leg: ONE call read four kilobytes (LBA 16 plus LBA 17,
    # the descriptor chain), a span whose SECOND sector fell outside the
    # window was refused as a whole, and the write row's authority/media
    # split was observed by name (ATAPI is read-only -> EROFS).
    if [[ "$VERDICT" == *"BLOCK-SPAN matches"* ]] && [[ "$VERDICT" == *"BLOCK-SPAN (ack 0x1000 , past-window 0xfffffffffffffff3 , zero-len 0xffffffffffffffea , media 0xffffffffffffffe2 , write-window 0xfffffffffffffff3 , write-kind 0xfffffffffffffff3)"* ]]; then
        ok "a 4 KiB block span reached ring 3 in one call, a span crossing the window was refused EACCES, and a write through the block capability was refused by the read-only ATAPI media (EROFS) while a wrong-kind or out-of-window write was EACCES"
    else
        bad "the block-span / block-write leg's bytes or refusals failed their read-back"
    fi
    # The REFUSAL MATRIX: the cells the per-row table names, each one a call
    # ring 3 makes with an argument the row must refuse. The fold is the whole
    # matrix in one word -- every cell's answer byte, in cell order, hashed with
    # FNV-1a -- so this assertion pins EVERY cell's errno, not just the row
    # count, and it needs no per-word bookkeeping as the matrix grows. A cell
    # that starts being served instead of refused changes its byte and the fold
    # with it, and the boot fails here. Since the no-effect pass landed the same
    # word also folds each cell's post-refusal witness, after all 25 answers:
    # a row that answers correctly and still acts changes the fold even when
    # every errno in the matrix is unchanged.
    if [[ "$VERDICT" == *"REFUSAL-MATRIX matches"* ]] && [[ "$VERDICT" == *"REFUSAL-MATRIX (rows 0x19 , matched 0x19 , no-effect 0x19 , fold 0x929e3f5e86b1c950)"* ]]; then
        ok "the refusal matrix refused every cell it pins from ring 3 with the typed errno the row's own rule names (0x42 misaligned non-user pointer -> EFAULT, 0x5a/0x5b/0x5c forged block handles -> EBADF, 0x66 detach with no slot -> EACCES, 0x6c release with no tile -> ENOENT, 0x4f non-page-aligned length -> EINVAL, 0x59 zero-length read and 0x5b zero-length write -> EINVAL, 0x4f zero length -> EINVAL, 0x4f misaligned non-user record -> EFAULT, 0x4c and 0x5a misaligned non-user records -> EFAULT, 0x59/0x5a/0x5b/0x5c handles above u32::MAX -> EBADF, 0x59 oversized read and 0x4f oversized request -> EINVAL) -- and every cell's NO-EFFECT witness held: the canary the refused call would have written was intact in the record, the cache record, the transfer buffer and each read destination, the caller's own live capability still answered geometry through 0x4c, the boot-adopted region's live window was still mapped and read its marker through a fresh 0x4c (a live window, not just an unchanged window record), each absent-state row answered its second call the same way, and LBA 16 was byte-identical to the pre-probe baseline"
    else
        bad "the refusal matrix did not hold (see the REFUSAL-MATRIX line and any ROW line naming a mismatch)"
    fi
    # The BLOCK-VOLUME leg: ring 3 parsed the ISO9660 primary volume
    # descriptor it read and handed the volume id back, and the kernel's
    # own read of the same sector agreed byte-for-byte.
    if [[ "$VERDICT" == *"BLOCK-VOLUME matches"* ]] && [[ "$VERDICT" == *"BLOCK-VOLUME (id \""* ]] && [[ "$VERDICT" == *"\" , pvd 0x"* ]] && [[ "$VERDICT" == *" , next 0x"* ]] && [[ "$VERDICT" == *" , ring3-id matches)"* ]]; then
        ok "ring 3 read the ISO9660 descriptor chain through the block capability, extracted the volume id, and the kernel's own read of LBA 16 confirmed it; the span's second sector was a real descriptor (\"CD001\" and version 1)"
    else
        bad "the ISO9660 volume leg's ring-3 id failed the kernel's own read-back"
    fi
    # The BLOCK-CACHE leg: two queries around the same LBA read, with the
    # hit counter rising and the device miss counter not going backwards.
    if [[ "$VERDICT" == *"BLOCK-CACHE matches"* ]] && [[ "$VERDICT" == *"BLOCK-CACHE (hits 0x"* || "$VERDICT" == *"BLOCK-CACHE (hits "* ]]; then
        ok "the kernel sector cache answered a repeated LBA from memory: the hit counter rose between the two stat queries"
    else
        bad "the block cache's hit/miss counters did not show a repeat read being cached"
    fi
    # The BLOCK-SCRATCH leg: a real LBA28 write to the attached ATA disk,
    # read back through the same capability and compared with the kernel's
    # own read of that LBA.
    if [[ "$VERDICT" == *"BLOCK-SCRATCH matches"* ]] && [[ "$VERDICT" == *"BLOCK-SCRATCH (write 0x200 , read 0x200 , disk and both ring-3 copies agree yes)"* ]]; then
        ok "a 512-byte write landed on the writable ATA scratch disk through the capability, read back through the SAME handle, and the kernel's own read of LBA 1000 agreed byte-for-byte"
    else
        bad "the scratch-disk write/read-back leg failed"
    fi
    # The BLOCK-ROOT leg: ring 3 walked the ISO9660 root directory and the
    # kernel re-walked it from its own read; the emitted name sequences are
    # identical.
    if [[ "$VERDICT" == *"BLOCK-ROOT matches"* ]] && [[ "$VERDICT" == *"BLOCK-ROOT (entries "* ]] && [[ "$VERDICT" == *" , names \""* ]]; then
        ok "ring 3 walked the ISO9660 root directory from the block capability and the kernel's own re-walk matched it name-for-name"
    else
        bad "the ring-3 ISO9660 root listing failed the kernel's own re-walk"
    fi
    # The PLATFORM rows (0x5d/0x5e): a ring-3 process read the identity
    # the session presents, switched it to the Windows posture, read the
    # new one back, had an out-of-range switch refused -EINVAL, confirmed
    # the refusal changed nothing, and restored the identity the machine
    # booted with (compared against the kernel's OWN live identity).
    if [[ "$VERDICT" == *", PLATFORM matches"* ]] && [[ "$VERDICT" == *"PLATFORM (get 0x"* ]] \
        && [[ "$VERDICT" == *" , set windows 0x0 , get after 0x2 , bad set 0xffffffffffffffea , after refusal 0x2"* ]]; then
        ok "ring 3 read the session identity, switched it to the Windows posture and read the new identity back, had an out-of-range switch refused -EINVAL with the identity unchanged, and restored the identity the machine booted with"
    else
        bad "the platform identity rows' read-back failed"
    fi
    # The PLATFORM-INFO leg (0x5f): one record carried the identity, the
    # form factor, the bound display's width and height, the status-bar
    # height and the accent colour, each compared against the kernel's
    # own live values. Only the machine-independent words are pinned here.
    if [[ "$VERDICT" == *", PLATFORM-INFO matches"* ]] && [[ "$VERDICT" == *"PLATFORM-INFO (ack 0x0 , identity 0x"* ]]; then
        ok "one PLATFORM_INFO call carried the session shape (identity, form factor, display geometry, status-bar height, accent) and the kernel's own live values matched every word"
    else
        bad "the platform-info record did not match the kernel's live session values"
    fi
    # The PLATFORM-MODE leg (0x60): ring 3 asked for the phone form
    # factor, read the new layout back through PLATFORM_INFO, restored the
    # form factor it booted with (read, not assumed) and read that back.
    # Only the machine-independent words are pinned here.
    if [[ "$VERDICT" == *", PLATFORM-MODE matches"* ]] && [[ "$VERDICT" == *"PLATFORM-MODE (set phone 0x0 , info2 ack 0x0 , mode 0x2"* ]]; then
        ok "ring 3 requested the phone form factor, the kernel re-laid-out the session and reported the new geometry and status-bar height back, and the original form factor was restored and read back"
    else
        bad "the runtime form-factor request did not re-lay-out and report the session correctly"
    fi
    # The supervised display (0x64..0x6b): one ring-3 session SERVED the
    # display before it parked, with a policy that grants clients the
    # identity bit only. A second ring-3 process attached under it, was
    # CLAMPED to that bit (it asked for both), changed the identity -- and
    # was refused the form factor with -EACCES, which the peer's own page
    # records for the kernel to check. The server could not be claimed
    # exclusively while it was served (-EBUSY), reported zero clients
    # before the attach and one while the client was attached, used its own
    # authority for the form factor no client could touch, and its
    # withdrawal dissolved the server and detached the parked client. At
    # verdict time the identity and form factor it set are still the
    # kernel's live state. Its second process also got a SURFACE (0x69) --
    # a page the kernel mapped and mirrored into the server's address space
    # -- and drew three pixels straight into that window; the server's
    # composition (0x6a) read them through the mirror and put them on the
    # display, and the three probes at the surface's origin, one to its
    # right and at (3,2) are read back out of the FRAMEBUFFER here. Only
    # the machine-independent words are pinned. It also asks the
    # session -> address-space registry (0x6b): two sessions registered, the
    # caller and its server both among them, and the caller's row gone after
    # its withdrawal -- no PML4 address crosses the gate.
    if [[ "$VERDICT" == *", DISPLAY matches"* ]] \
        && [[ "$VERDICT" == *"DISPLAY (serve 0x1 , status first 0x"* ]] \
        && [[ "$VERDICT" == *" , owner 0x0 , acquire beside server 0xfffffffffffffff0 , self client 0x0 , status serving 0x"* ]] \
        && [[ "$VERDICT" == *" , server at verdict 0x0 , clients 0x0 , surfaces at verdict 0x0 ,"* ]] \
        && [[ "$VERDICT" == *" , churn 12/"* ]] \
        && [[ "$VERDICT" == *" , compose 0x1 , spaces 0x200000003 , churn 12/"* ]] \
        && [[ "$VERDICT" == *" , tile 0x224466/0x335577/0xabcdef ,"* ]]; then
        ok "a ring-3 session served the display under a published policy, clamped a second process's client grant to it, that client changed the identity it was granted and was refused the form factor it was not, its own surface tile's pixels were composed onto the display by the server and read back out of the framebuffer, the server's own authority covered the difference, and its exit dissolved the server and detached the still-parked client with its chosen identity and form factor left standing"
    else
        bad "the supervised display did not grant, clamp and withdraw authority as documented"
    fi
    # The TWO-PROCESS leg: two ring-3 processes, each its own scheduler
    # task with its own session and its own trap stack. The first process
    # closes ONE of its two region capabilities (the record survives: the
    # geometry through the other handle still answers), then parks in a
    # blocking CALL; the PEER answers it, so the answer bytes in the
    # caller's buffer (0x21426d6f7266 = "fromB!") are another process's
    # writes. The region's offset 16 is the peer's value read through the
    # FIRST process's capability, and offset 8 is the first process's value
    # read by the peer -- sharing in both directions. The last close is by
    # the last holder: the kernel's own table says the record is GONE
    # The HANDLE numbers below are one higher than they were before the
    # surface leg existed, and that is the increment's own arithmetic: the
    # peer's SURFACE (0x69) mints a capability handle BEFORE these region
    # legs run, so the same legs get 4 and 3 instead of 3 and 2. They stay
    # pinned exactly, because a shifted number is evidence, not noise.
    # ("region live no holders Some(0)"), and the exits are both recorded
    # (2 exits, the last the first process's session 7, one region
    # capability given back by an exit -- the peer's).
    if [[ "$VERDICT" == *"TWO-PROCESS matches"* ]] && [[ "$VERDICT" == *"PEER matches"* ]] && [[ "$VERDICT" == *"TWO-PROCESS (close 0x0 , geometry 0x0 , call 0x8 , request 0x21416d6f7266 , answer 0x21426d6f7266 , offset8 0xfeedfacedeadbeef , offset16 0xbeefcafedead1234 , last 0x0 , twice 0xfffffffffffffff7 , region live no holders Some(0) , exits 1 session 7 released 1)"* ]] && [[ "$VERDICT" == *"PEER (ack 0x0 , base 0x60000030000 , read 0xfeedfacedeadbeef , recv 0x700000008 , handle 0x4 , request 0x21416d6f7266 , reply 0x0 , wrote 0xbeefcafedead1234)"* ]]; then
        ok "two ring-3 processes on their own tasks shared one region in both directions, one of them parked in a blocking call that the OTHER answered with its own bytes, and the region's record died with its last holder while each exit released its own capabilities"
    else
        bad "the two-process leg's region lifetime, cross-process bytes or parked answer failed their read-back"
    fi
    # The REQUESTED-REGION leg (0x4f `CAP_SHM_MAP`): until this row the
    # kernel always mapped first and a holder could only be TOLD where.
    # Here the process asks for a page, the kernel chooses the window
    # (base -- not a constant in the program), zeroes the frames, records
    # the region and mints a handle. THREE requests happen in the pump, so
    # the window slots are: the one HANDED ON to the other process
    # (0x60000100000, `PEER-XFER base 0x60000100000`), then step 15's two
    # (0x60000110000 and 0x60000120000, the second two pages). The
    # handover's region is released FIRST -- the receiver closes before it
    # answers the call that unblocks the sender -- and the kernel's
    # releaser reads BOTH words out of that mapping as the pages go:
    # 0xfeedfacecafed00d, written by the sender BEFORE it gave its own
    # capability back (`survived` is the same value read through the base
    # after that close: the mapping outlived the capability), and
    # 0x1122334455667788, written only by the RECEIVER. Step 15's own
    # regions are the last two releases and are zero -- never written by
    # anyone, which is what makes a request's pages zero-filled.
    # All three windows are UNMAPPED in the page tables (unmapped true /
    # true / true -- and the read-back runs in the FIRST program's space,
    # so "unmapped" is said of that space's tables; the release removes the
    # mirror from the peer's as well, which is why the count balances) and
    # the frame count is back to its pre-pump baseline (302 == 302 -- six
    # frames above the single-space tree: the peer's PML4 and its page
    # tables plus its own pages, all allocated before the baseline). The consumed handle is refused -EBADF. The same
    # handover asked a second time with a_rights = ALL (0x0f), one right
    # the sender never held, is refused by NAME (EMSGSIZE = -90 = 
    # 0xffffffffffffffa6) rather than clipped -- and because the refusal
    # precedes the queue, the receiver still drains the ONE good message
    # attenuated to READ|WRITE (0x0c), which is the proof that the wider
    # ask queued nothing.
    if [[ "$VERDICT" == *"MAP matches"* ]] && [[ "$VERDICT" == *"SPACES (same va 0x60000010000 , first 0x1260296 , peer 0x60000030000) , REVOKE (base 0x60000100000 , addr 0x60000100000 , err 0x4 , task 2 , expected 2 , hits 1 , woke 1 , reason not mapped , session 8) , WINDOW (req 0x0 , base 0x60000110000 , len 0x1000 , handle 0x3 , marker 0xddba11cafef00d , unmap 0x0 , again 0xfffffffffffffffe , geom 0x0 , geom base 0x60000110000 , peer read 0xddba11cafef00d , no-window release 1) , MAP (ack 0x0 , base 0x60000120000 , len 0x1000 , rights 0xe , handle 0x4 , wrote 0xdeadbeefcafebabe , again 0x0 , base2 0x60000130000 , len2 0x2000 , handle2 0xb , close 0x0 , close2 0x0 , stale 0xfffffffffffffff7 , geometry 0x0 , geom base 0x60000120000 , released 4 word 0xdeadbeefcafebabe/0xfeedfacecafed00d , bases 0x60000120000/0x60000100000 , frames "*"== "*" , unmapped true / true / true / true , xfer (ack 0x0 , base 0x60000100000 , send 0x0 , close 0x0 , survived 0xfeedfacecafed00d , first8 0x0 , last8 0x1122334455667788 , wider 0xffffffffffffffa6))"* ]]; then
        ok "a ring-3 holder ASKED for a region, the kernel chose the window, backed it with zeroed frames and minted a usable handle; a second request got the next window; the kernel read the holder's word out of the mapping before unmapping; both teardowns unmapped their window and returned every frame"
    else
        bad "the requested-region leg's mapping, kernel-chosen window or page release failed their read-back"
    fi
    # The HANDOVER leg: the SAME requested region, handed to the OTHER
    # process through the dedicated transfer field. The sender closed its
    # own capability while the copy was still queued and the page stayed
    # mapped (survived 0xfeedfacecafed00d); the receiver read that marker
    # across the handover (read 0xfeedfacecafed00d), wrote at offset 8
    # (0x1122334455667788), and its close -- the LAST holder's -- is what
    # returned the pages, which the releaser's two readings confirm. The
    # copy arrived attenuated (the geometry row answers rights 0xc for a
    # peer whose sender held 0xe), and the message was already queued, so
    # the drain needed no retry (tries 0).
    if [[ "$VERDICT" == *"PEER-XFER (recv 0x700000004 , handle 0x3 , base 0x60000100000 , read 0xfeedfacecafed00d , wrote 0x1122334455667788 , parked 0x63 , tries 0)"* ]]; then
        ok "one ring-3 process handed its requested region to the OTHER through the capability transfer field; the receiver's copy was counted as a holder (the sender's close freed nothing), the page stayed mapped across that close, and the receiver's own close freed it with both processes' bytes in the kernel's record"
    else
        bad "the region handover's receiver-side facts failed their read-back"
    fi
    # THE TWO ADDRESS SPACES (2026-11-08): each ring-3 process runs on its
    # OWN PML4 (spawn_with_cr3 names it, switch_to loads it on every
    # switch), and both programs are linked at the SAME anchor with their
    # results pages at the SAME virtual address -- 0x60000010000. The
    # kernel captures the word at that VA plus 8 on each side of a CR3
    # switch: the first program's INPUT_STATE record on one side
    # (0x1260296) and the region base the PEER's own geometry call
    # answered on the other (0x60000030000). Same VA, two different pages
    # -- a single shared space cannot produce that pair, and a wrong CR3 in
    # the read-back would produce the FIRST value twice.
    if [[ "$VERDICT" == *"SPACES (same va 0x60000010000 , first 0x1260296 , peer 0x60000030000)"* ]]; then
        ok "the two ring-3 processes ran in separate address spaces: the same virtual address resolved to different pages in each, read by the kernel across an explicit CR3 switch"
    else
        bad "the two processes' address spaces were not distinct (the same VA read the same page, or the peer's page was not the one it wrote)"
    fi
    # THE REVOCATION LEG (2026-11-08): the kernel took the handed-on
    # region's pages back while the peer STILL HELD the capability (it no
    # longer closes -- that close was what used to make it the last
    # holder), then woke it. The peer's own next read faulted: the armed
    # probe recorded the address the KERNEL handed out, the error code of a
    # READ from RING 3 that found no mapping (0x4: not present, read, user),
    # and the task it was armed for -- then terminated that task through the
    # scheduler's lifecycle, so the machine kept running to print this.
    # `revoked`/`woke` say the machine accepted the revoke and a message
    # really reached the parked holder.
    if [[ "$VERDICT" == *"REVOKE (base 0x60000100000 , addr 0x60000100000 , err 0x4 , task 2 , expected 2 , hits 1 , woke 1 , reason not mapped , session 8)"* ]]; then
        ok "the kernel revoked a region's mapping while a holder still had its capability, and that holder's next read faulted on the revoked page from ring 3 -- recorded and absorbed by the armed probe, with the faulting task terminated through the scheduler instead of halting the machine"
    else
        bad "the revocation leg's page did not fault (or faulted elsewhere, or was not revoked while a holder lived)"
    fi
    # THE WINDOW LEG (2026-11-08): a holder drops ITS OWN window (0x50) and
    # keeps the capability. The row answers 0 once and -ENOENT by name the
    # second time; the capability still answers geometry afterwards; the
    # OTHER space the kernel mirrored those frames into reads the dropped
    # holder's marker -- ring 3, in the other process, after the window was
    # gone here; and the release that finally frees the region reports it
    # ran in a space with NO window at all, which is the machine's own
    # record that the mapping was dropped before the region died.
    if [[ "$VERDICT" == *"WINDOW (req 0x0 , base 0x60000110000 , len 0x1000 , handle 0x3 , marker 0xddba11cafef00d , unmap 0x0 , again 0xfffffffffffffffe , geom 0x0 , geom base 0x60000110000 , peer read 0xddba11cafef00d , no-window release 1)"* ]]; then
        ok "a holder dropped its own window on a shared region while keeping the capability: the second drop was refused by name, the capability still answered geometry, the OTHER process's window over the same frames still read the marker, and the region's release found no window left in the dropper's space"
    else
        bad "the window leg's per-holder unmap did not hold (the row, its refusal, the capability's survival or the other window)"
    fi
    # THE BADGE/NOTIFY LEG (2026-11-09): payload-free signal bits OR-ed
    # into the endpoint and collected by its OWNER (cleared by the take),
    # three by-name refusals, and a badge minted once into the caller's own
    # table -- a zero badge, another kind, a forged slot and a re-badge all
    # refused rather than answered.
    if [[ "$VERDICT" == *"BADGE/NOTIFY (notify 0x0 , take 0x5 , again 0x0 , wrong-kind 0xfffffffffffffff3 , forged 0xfffffffffffffff7 , non-owner 0xfffffffffffffff3 , badge 0x4 , zero 0xfffffffffffffff3 , kind 0xfffffffffffffff3 , forged-badge 0xfffffffffffffff7 , twice 0xfffffffffffffff3 , send 0x0 , recv 0x700000006 , tag 0xbeef , null-tag 0xfffffffffffffff2) , BADGE/NOTIFY matches"* ]]; then
        ok "notify OR-ed payload-free bits into the endpoint and its OWNER collected them (a second take answered 0), a zero badge / another kind / a forged slot / a re-badge were refused by name, the minted badge handle was installed in the caller's own table, a send through it carried tag 0xbeef to RECV's badge out-pointer, and a null badge pointer was refused -EFAULT before the queue was drained"
    else
        bad "the badge/notify leg did not hold (the signal word, its clearing, the badge mint or the by-name refusals)"
    fi
    # REFUSAL BREADTH. The gate pins individual errnos per leg, but nothing
    # said how MANY distinct refusal kinds the proof still demonstrates: a
    # leg that stopped refusing would keep its own pattern if the pattern
    # were loose, and the board would look unchanged. Five kinds are carried
    # by the current surface -- EBUSY (-16), EFAULT (-14), EACCES (-13),
    # EBADF (-9), ENOENT (-2) -- so the count is pinned and may only grow.
    # This is the checkable half of a per-row refusal matrix; the matrix
    # itself (every served number, every bad-input shape) still needs new
    # ring-3 legs -- see the README's "what these gates cannot see".
    REFUSALS=$(printf '%s' "$VERDICT" | grep -oE '0xfffffffffffffff[0-9a-f]' | sort -u | wc -l | tr -d ' ')
    WANT_REFUSALS="${SVC84_EXPECTED_REFUSAL_WORDS:-5}"
    if [[ "$REFUSALS" -lt "$WANT_REFUSALS" ]]; then
        bad "the verdict shows $REFUSALS distinct refusal errno(s), expected at least $WANT_REFUSALS (a refusal stopped being exercised)"
    else
        ok "the verdict shows $REFUSALS distinct refusal errno(s) (at least the $WANT_REFUSALS pinned kinds)"
    fi
    # The legs are COUNTED, not just looked for one by one: a deleted leg
    # would leave every remaining pattern matching and the verdict green.
    LEGS=$(printf '%s' "$VERDICT" | grep -oE '[A-Z][A-Z0-9-]* matches' | sort -u | wc -l | tr -d ' ')
    if [[ "$EXPECTED_LEGS" -gt 0 && "$LEGS" -ne "$EXPECTED_LEGS" ]]; then
        bad "the verdict carries $LEGS distinct proved leg(s), expected $EXPECTED_LEGS (a leg stopped running)"
    else
        ok "the verdict carries $LEGS distinct proved leg(s)"
    fi
    # Trailing wildcard matters: the serial line ends with a carriage
    # return, so a pattern anchored at the very end never matches.
    if [[ "$VERDICT" == *"-- OK"* ]]; then
        ok "the kernel's own verdict for the proof is OK"
    else
        bad "the kernel reported the proof as FAILED"
    fi
fi

echo
if [[ "$FAILS" -eq 0 && -z "$REPLAY" ]]; then
    # tools/gate_selfcheck.sh mutates this copy: one piece of evidence
    # removed at a time, each of which this harness must reject.
    cp "$SERIAL" target/svc84-proof-last.log 2>/dev/null || true
fi
if [[ "$FAILS" -ne 0 ]]; then
    echo "FAIL: $FAILS ring-3 service case(s) failed -- see $WORK/"
    exit 1
fi
echo "PASS: a ring-3 program called int 0x84, received the gfx/input answers in its own memory, was refused -ENOSYS for an unported call, had a validated write land in its buffer, the identity call answered the scheduler's own running-task id, the widened rsi/rdx/r10/r8 pass reached the dispatcher exactly as sent, one IPC message round-tripped through the kernel's real mailbox primitive with the shape and empty refusals observed by name, one capability-payload round-tripped through the caller's OWN session table with the forged-handle and oversize refusals observed by name, the kernel's controller-injected scancodes round-tripped through the INPUT kind with the empty and forged-handle answers observed by name, one call carried a one-shot reply capability that answered exactly once, and one shared region was read and written by BOTH the kernel and ring 3, two ring-3 processes on their own tasks shared that region in both directions while one of them parked in a blocking call the other answered, and a ring-3 holder ASKED the kernel for a region (at a window the kernel chose), had it backed with zeroed frames and minted a usable handle, and saw every page unmapped and returned when its last capability closed, handed one of those requested regions to the OTHER process through the capability transfer field -- the receiver's copy counted as a holder, so the sender's own close freed nothing and the page stayed mapped until the receiver closed last, with both processes' bytes in the kernel's release record -- both processes ran in address spaces of their own, their identical virtual addresses resolving to different pages, and the kernel revoked that region's mapping while one process still held its capability -- the holder's own next read faulted on the revoked page from ring 3, was recorded by the armed probe, and terminated only that task, and the notify row OR-ed payload-free signal bits that the endpoint's owner collected and cleared while a zero badge, another kind, a forged slot and a re-badge were refused by name, and a message sent through a badged capability delivered its tag to RECV's badge out-pointer (with a null pointer refused before the drain), and the session identity was read from ring 3, switched to another posture and read back, had an out-of-range switch refused by name, was restored to the identity the machine booted with, and one PLATFORM_INFO call carried the whole session shape -- identity, form factor, display geometry, status-bar height and accent -- back to ring 3 word-for-word, and a runtime form-factor request re-laid-out the session at the phone layout and restored the one it booted with, with the geometry and status-bar height verified at both modes, and a ring-3 session served the display under a published policy -- clamping a second process's client grant to that policy, letting it change the identity it was granted while refusing it the form factor it was not, keeping the server above a second exclusive claim, and dissolving server and clients on withdrawal while the identity and form factor it chose stayed standing as the kernel's live state."
