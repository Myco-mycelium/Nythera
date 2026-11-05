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
    if [[ "$VERDICT" == *"IPC matches"* ]] && [[ "$VERDICT" == *"send-ack 0x0"* ]] && [[ "$VERDICT" == *"recv 0x7"* ]] && [[ "$VERDICT" == *"want 0x7"* ]]; then
        ok "one sent message round-tripped through the kernel's real mailbox (sender=0 seq=7 -> 0x7) and the shape/empty refusals were observed by name"
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
echo "PASS: a ring-3 program called int 0x84, received the gfx/input answers in its own memory, was refused -ENOSYS for an unported call, had a validated write land in its buffer, the identity call answered the scheduler's own running-task id, the widened rsi/rdx/r10/r8 pass reached the dispatcher exactly as sent, one IPC message round-tripped through the kernel's real mailbox primitive with the shape and empty refusals observed by name, one capability-payload round-tripped through the caller's OWN session table with the forged-handle and oversize refusals observed by name, and the kernel's controller-injected scancodes round-tripped through the INPUT kind with the empty and forged-handle answers observed by name."
