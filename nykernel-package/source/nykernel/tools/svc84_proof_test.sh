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
# Usage: ./tools/svc84_proof_test.sh

set -uo pipefail
cd "$(dirname "$0")/.."

WORK=target/svc84-proof
rm -rf "$WORK"
mkdir -p "$WORK/boot/grub"

FAILS=0
ok()  { echo "  OK: $*"; }
bad() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }

command -v qemu-system-x86_64 >/dev/null 2>&1 || { echo "FAIL: qemu-system-x86_64 missing"; exit 1; }
command -v grub-mkrescue >/dev/null 2>&1 || { echo "FAIL: grub-mkrescue missing"; exit 1; }

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
qemu-system-x86_64 -cdrom "$WORK/svc84.iso" -m 512 \
    -serial "file:$SERIAL" -display none -no-reboot \
    >"$WORK/qemu.log" 2>&1 &
QPID=$!

waited=0
while [[ $waited -lt 240 ]]; do
    grep -q "service-0x84 proof: a ring-3 program" "$SERIAL" 2>/dev/null && break
    grep -q "KERNEL PANIC" "$SERIAL" 2>/dev/null && break
    sleep 0.5
    waited=$((waited + 1))
done
kill "$QPID" 2>/dev/null; wait "$QPID" 2>/dev/null

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

VERDICT="$(grep "service-0x84 proof: a ring-3 program" "$SERIAL" | tail -1)"
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
    if [[ "$VERDICT" == *"REPLY matches"* ]] && [[ "$VERDICT" == *"REPLY (call 0x7 , recv 0x700000008 , handle 0x6 , reply 0x0 , twice 0xfffffffffffffff7 , kind 0xfffffffffffffff3 , answer 0x700000008 , bytes 0x2121726577736e61)"* ]]; then
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
    # ("region live no holders Some(0)"), and the exits are both recorded
    # (2 exits, the last the first process's session 7, one region
    # capability given back by an exit -- the peer's).
    if [[ "$VERDICT" == *"TWO-PROCESS matches"* ]] && [[ "$VERDICT" == *"PEER matches"* ]] && [[ "$VERDICT" == *"TWO-PROCESS (close 0x0 , geometry 0x0 , call 0x8 , request 0x21416d6f7266 , answer 0x21426d6f7266 , offset8 0xfeedfacedeadbeef , offset16 0xbeefcafedead1234 , last 0x0 , twice 0xfffffffffffffff7 , region live no holders Some(0) , exits 2 session 7 released 1)"* ]] && [[ "$VERDICT" == *"PEER (ack 0x0 , base 0x60000030000 , read 0xfeedfacedeadbeef , recv 0x700000008 , handle 0x2 , request 0x21416d6f7266 , reply 0x0 , wrote 0xbeefcafedead1234)"* ]]; then
        ok "two ring-3 processes on their own tasks shared one region in both directions, one of them parked in a blocking call that the OTHER answered with its own bytes, and the region's record died with its last holder while each exit released its own capabilities"
    else
        bad "the two-process leg's region lifetime, cross-process bytes or parked answer failed their read-back"
    fi
    # The REQUESTED-REGION leg (0x4f `CAP_SHM_MAP`): until this row the
    # kernel always mapped first and a holder could only be TOLD where.
    # Here the process asks for a page, the kernel chooses the window
    # (base 0x60000100000 -- not a constant in the program), zeroes the
    # frames, records the region and mints handle 3; a SECOND request gets
    # the NEXT window (0x60000110000, two pages), so the base is the
    # kernel's choice per request. The holder's write
    # (0xdeadbeefcafebabe) is read out of the mapping by the kernel's own
    # releaser before it unmaps (word 0xdeadbeefcafebabe, and 0x0 out of
    # the region the program never wrote -- a request is zero-filled).
    # Both last-capability closes are teardowns: the two windows are
    # UNMAPPED in the page tables (unmapped true / true) and the frame
    # count is back to its pre-pump baseline (296 == 296). The consumed
    # handle is refused -EBADF.
    if [[ "$VERDICT" == *"MAP matches"* ]] && [[ "$VERDICT" == *"MAP (ack 0x0 , base 0x60000100000 , len 0x1000 , rights 0xe , handle 0x3 , wrote 0xdeadbeefcafebabe , again 0x0 , base2 0x60000110000 , len2 0x2000 , handle2 0x4 , close 0x0 , close2 0x0 , stale 0xfffffffffffffff7 , geometry 0x0 , geom base 0x60000100000 , released 2 word 0xdeadbeefcafebabe/0x0 , bases 0x60000100000/0x60000110000 , frames 296 == 296 , unmapped true / true)"* ]]; then
        ok "a ring-3 holder ASKED for a region, the kernel chose the window, backed it with zeroed frames and minted a usable handle; a second request got the next window; the kernel read the holder's word out of the mapping before unmapping; and both teardowns unmapped their window and returned every frame"
    else
        bad "the requested-region leg's mapping, kernel-chosen window or page release failed their read-back"
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
if [[ "$FAILS" -ne 0 ]]; then
    echo "FAIL: $FAILS ring-3 service case(s) failed -- see $WORK/"
    exit 1
fi
echo "PASS: a ring-3 program called int 0x84, received the gfx/input answers in its own memory, was refused -ENOSYS for an unported call, had a validated write land in its buffer, the identity call answered the scheduler's own running-task id, the widened rsi/rdx/r10/r8 pass reached the dispatcher exactly as sent, one IPC message round-tripped through the kernel's real mailbox primitive with the shape and empty refusals observed by name, one capability-payload round-tripped through the caller's OWN session table with the forged-handle and oversize refusals observed by name, the kernel's controller-injected scancodes round-tripped through the INPUT kind with the empty and forged-handle answers observed by name, one call carried a one-shot reply capability that answered exactly once, and one shared region was read and written by BOTH the kernel and ring 3, two ring-3 processes on their own tasks shared that region in both directions while one of them parked in a blocking call the other answered, and a ring-3 holder ASKED the kernel for a region (at a window the kernel chose), had it backed with zeroed frames and minted a usable handle, and saw every page unmapped and returned when its last capability closed."
