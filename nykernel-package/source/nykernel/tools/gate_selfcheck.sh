#!/usr/bin/env bash
#
# gate_selfcheck.sh -- prove a proof gate FAILS when the evidence is wrong.
#
#   ./tools/gate_selfcheck.sh [transcript]
#
# Every gate in this tree is a claim of the form "a wrong kernel cannot pass
# me". Nothing checked that claim, and the block-info bug is what that costs:
# a verdict line printed another leg's record, said "matches", and the gate was
# green while a served call was wrong. A gate nobody has ever seen fail is a
# comment with a segfault story.
#
# This tool takes a REAL transcript from tools/svc84_proof_test.sh (it saves
# one to target/svc84-proof-last.log after every pass) and, one at a time,
# deletes a piece of the evidence or moves an expectation, requiring the gate
# to (a) exit non-zero and (b) name the right failure. Then it requires the
# unmutated transcript to still PASS, so a gate that simply fails everything
# cannot pass this either.
#
# The transcript cases prove the CHECKER reads evidence; they cannot prove the
# matrix detects a kernel that stopped refusing, because an edited log is not
# a kernel. So the last three cases are SOURCE mutations, each rebuilt and
# booted by the gate itself:
#   - remove 0x4c's handle guard, so the row answers a WRONG errno, and require
#     the gate to name that row with its own values;
#   - make 0x50's EBADF arm drop the caller's window on its way out, so the
#     row still answers correctly and its NO-EFFECT witness is the only thing
#     that can catch it, and require the gate to name cell 0x50's witness;
#   - make 0x4d's EBADF arm give back every region capability the caller holds
#     while still answering EBADF, so a DIFFERENT row's witness carries the
#     same burden, and require the gate to name cell 0x4d's no-effect line.
# The 0x50 case takes its expected marker from the kernel's own table
# (`REFUSAL_WINDOW_WITNESS`), never from a copy here: a marker changed in the
# kernel must move this expectation with it instead of turning a correct
# kernel into a false failure. It is checked before the mutation runs, so an
# unreadable constant fails the self-check rather than proving nothing. The
# 0x4d case needs no extraction: its row's witness is 0, the value a live
# capability's geometry call must answer.
# The source is restored from a backup in the EXIT trap as well as on the
# normal path, so an interrupted run does not leave a mutant tree behind; the
# real kernel is rebuilt afterwards.
#
# Usage:
#   ./tools/svc84_proof_test.sh            # once, to produce the transcript
#   ./tools/gate_selfcheck.sh              # mutate it, expect refusals
#   ./tools/gate_selfcheck.sh path/to/log  # a specific transcript
#
# Exit: 0 every mutation was caught and the clean run passed; 1 otherwise.

set -uo pipefail
cd "$(dirname "$0")/.."

LOG="${1:-target/svc84-proof-last.log}"
GATE=./tools/svc84_proof_test.sh
TMP="$(mktemp -d)"
# The source-level cases below edit a tracked file. Restoring it is not left to
# the happy path: an interrupt or an early exit must not leave a mutant kernel
# in the tree, so the backup is put back from the trap as well.
SRC_MUTATED=0
SRC_BACKUP="$TMP/service84.rs.orig"
restore_source() {
    if [[ "$SRC_MUTATED" -eq 1 && -f "$SRC_BACKUP" ]]; then
        cp "$SRC_BACKUP" "src/service84.rs" 2>/dev/null || true
        echo "  (restored src/service84.rs from its backup on exit)"
    fi
    rm -rf "$TMP"
}
trap restore_source EXIT
trap 'exit 130' INT TERM

FAILS=0
ok()  { echo "  OK: $*"; }
bad() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }

if [[ ! -f "$LOG" ]]; then
    echo "FAIL: no transcript at $LOG"
    echo "      run ./tools/svc84_proof_test.sh once to produce one"
    exit 1
fi
if [[ ! -x "$GATE" ]]; then
    echo "FAIL: $GATE is not executable"
    exit 1
fi

# The file the kernel-regression cases mutate, named once so the cases below
# agree on it. The 0x50 case needs one value from the kernel's own table: the
# marker its witness reads. It is EXTRACTED, not copied -- a copy here would go
# stale the first time the marker changed and then report a correct kernel as
# "did not read the missing marker". A hedged extraction (empty on failure) is
# checked before the mutation runs, so an unreadable constant is a self-check
# failure in its own right rather than a mutation that proves nothing.
SRC=src/service84.rs
MARKER_HEX="$(sed -n 's/.*pub const REFUSAL_WINDOW_WITNESS: i64 = 0x\([0-9a-fA-F_]*\);.*/\1/p' "$SRC" 2>/dev/null | head -1 | tr -d '_' | tr 'A-F' 'a-f')"
WITNESS_WANT=""
[[ -n "$MARKER_HEX" ]] && WITNESS_WANT="0x$MARKER_HEX"

echo "==> baseline: the saved transcript must PASS unmutated"
if "$GATE" --replay "$LOG" >"$TMP/base.log" 2>&1; then
    ok "the unmutated transcript passes (the gate is not failing everything)"
else
    bad "the unmutated transcript FAILS -- the gate or the transcript is broken"
    grep -E "^  FAIL|^FAIL" "$TMP/base.log" | head -6 | sed 's/^/        /'
fi

# name | sed expression applied to a copy | text the refusal must contain
CASES=(
    "ring-3 caller evidence|s/CPL 3/CPL 0/|does not report CPL 3"
    "refusal-matrix cell|s#fold 0x929e3f5e86b1c950#fold 0x929e3f5e86b1c951#|refusal matrix did not hold"
    "refusal-matrix no-effect count|s/no-effect 0x19/no-effect 0x18/|refusal matrix did not hold"
    "block-info geometry leg|s/BLOCK-INFO matches/BLOCK-INFO /|block-info leg"
    "supervised display leg|s/DISPLAY matches/DISPLAY /|supervised display"
    "kernel's own verdict|s/-- OK/-- FAILED/|reported the proof as FAILED"
    "the whole verdict|s/service-0x84 proof: .*/proof-line-removed/|never reached its verdict line"
)

i=0
for case in "${CASES[@]}"; do
    i=$((i + 1))
    name="${case%%|*}"
    rest="${case#*|}"
    expr="${rest%%|*}"
    want="${rest#*|}"
    mutant="$TMP/mutant-$i.log"
    sed "$expr" "$LOG" >"$mutant"
    if cmp -s "$LOG" "$mutant"; then
        bad "'$name': the mutation changed nothing (the transcript no longer has that evidence)"
        continue
    fi
    if "$GATE" --replay "$mutant" >"$TMP/out-$i.log" 2>&1; then
        bad "'$name': the gate PASSED a transcript with that evidence removed"
    elif ! grep -qF "$want" "$TMP/out-$i.log"; then
        bad "'$name': the gate failed, but not for the right reason (wanted: $want)"
        grep -E "^  FAIL" "$TMP/out-$i.log" | head -3 | sed 's/^/        /'
    else
        ok "'$name': removed -> the gate refused it"
    fi
done

# The leg-count invariant cannot be exercised by editing the transcript (a
# deleted leg also fails its own check), so move the EXPECTATION instead: if
# raising it by one does not fail, the assertion is not live. The number is
# READ from the gate rather than copied here -- a hard-coded one goes stale the
# first time a leg is added, and then this case fails while the gate is right.
BASE_LEGS="$(sed -n 's/.*SVC84_EXPECTED_LEGS:-\([0-9][0-9]*\).*/\1/p' "$GATE" | head -1)"
echo "==> the leg-count invariant is live (gate expects ${BASE_LEGS:-?} legs)"
if SVC84_EXPECTED_LEGS=$(( BASE_LEGS + 1 )) "$GATE" --replay "$LOG" >"$TMP/legs.log" 2>&1; then
    bad "expecting one more leg than the transcript carries still passed: the leg-count assertion is not reading anything"
elif grep -q "distinct proved leg" "$TMP/legs.log"; then
    ok "expecting one more leg than the transcript carries is refused"
else
    bad "the leg-count mismatch was not named as such"
    grep -E "^  FAIL" "$TMP/legs.log" | head -3 | sed 's/^/        /'
fi

# ---- a KERNEL regression, not an edited transcript ------------------------
# Every case above edits the log, so together they prove the checker reads the
# evidence. None of them proves the matrix notices a kernel whose row stopped
# refusing: a transcript is not a kernel. This case removes one row's guard
# from the source, lets the gate BUILD AND BOOT that kernel, and requires the
# failure to name the row it broke.
#
# 0x4c is the subject because its guard is the only thing between an oversized
# handle and `a0 as u32` == 0, which is a LIVE capability (the endpoint owner's
# slot). Strip the guard and the row answers EACCES instead of EBADF, so the
# cell's own errno changes -- a wrong refusal, not a crashed kernel, which is
# the interesting kind of failure for a matrix to catch.
echo "==> a kernel regression: strip 0x4c's handle guard, rebuild, expect a red matrix"
if [[ ! -f "$SRC" ]]; then
    bad "no $SRC to mutate: the source-level case cannot run"
else
    cp "$SRC" "$SRC_BACKUP" || bad "could not back up $SRC"
    if python3 - "$SRC" <<'PY'
import sys

path = sys.argv[1]
src = open(path, encoding='utf-8').read()
old = """        CALL_CAP_SHM => {
            if a0 > u32::MAX as u64 {
                return EBADF;
            }
"""
new = """        CALL_CAP_SHM => {
"""
if src.count(old) != 1:
    sys.exit("the 0x4c guard is not where this case thinks it is (%d matches at this exact shape)" % src.count(old))
open(path, 'w', encoding='utf-8').write(src.replace(old, new))
PY
    then
        SRC_MUTATED=1
        # The gate runs ./build.sh and boots QEMU itself, so the rebuild is
        # part of this invocation rather than a separate step that could be
        # skipped or silently use the previous kernel.
        if "$GATE" >"$TMP/src.log" 2>&1; then
            bad "a kernel with 0x4c's handle guard REMOVED still passed the matrix -- the cell does not depend on the refusal it claims to pin"
        elif ! grep -qF "REFUSAL-MATRIX ROW 0x4c" "$TMP/src.log"; then
            bad "the stripped guard was caught, but not by the 0x4c cell (no 'REFUSAL-MATRIX ROW 0x4c' line)"
            grep -E "^  FAIL" "$TMP/src.log" | head -3 | sed 's/^/        /'
        elif ! grep -qF "handle above u32::MAX" "$TMP/src.log"; then
            bad "the gate named row 0x4c, but not this cell: the ROW line must carry its own description"
        else
            got="$(grep -m1 'REFUSAL-MATRIX ROW 0x4c' "$TMP/src.log" | grep -o 'got 0x[0-9a-f]*' | head -1)"
            ok "0x4c's guard stripped (${got:-no got field}) -> the gate refused the kernel and named the cell"
        fi
    else
        bad "could not strip the 0x4c guard (see message above); source left unchanged"
    fi
    # Restore and PROVE the restore before rebuilding, so the kernel left in
    # target/ is the real one even when the comparison fails.
    cp "$SRC_BACKUP" "$SRC"
    SRC_MUTATED=0
    if ! cmp -s "$SRC_BACKUP" "$SRC"; then
        bad "$SRC does not match its backup after the mutation: the tree is NOT back to its original state"
    elif ! ./build.sh >"$TMP/rebuild.log" 2>&1; then
        bad "the source is restored but ./build.sh no longer succeeds -- target/kernel.elf may still be the mutant"
        tail -5 "$TMP/rebuild.log" | sed 's/^/        /'
    else
        ok "source restored byte-for-byte and the real kernel rebuilt"
    fi
fi

# ---- a second KERNEL regression: the witness, not the errno ----------------
# The case above moves an ANSWER. A row can also answer exactly right and
# still act, which is what the NO-EFFECT pass exists for: this mutation leaves
# 0x50's EBADF answer alone and makes that arm drop every window the caller
# holds before returning it. The errno matrix cannot see that -- cell 23 still
# reads EBADF -- so if the live-window witness were not doing its job, this
# kernel would pass. The witness reads the boot-adopted region's marker
# THROUGH its window; with the mapping gone the load faults ring 3, the slot
# keeps its zeroed value, and the NO-EFFECT ROW 0x50 line must name the cell.
echo "==> a kernel regression: 0x50 answers EBADF after dropping the window, expect a red matrix"
if [[ -z "$WITNESS_WANT" ]]; then
    bad "REFUSAL_WINDOW_WITNESS is not readable from $SRC: the 0x50 case cannot know what its witness must read"
elif [[ ! -f "$SRC" ]]; then
    bad "no $SRC to mutate: the 0x50 witness case cannot run"
else
    cp "$SRC" "$SRC_BACKUP" || bad "could not back up $SRC"
    if python3 - "$SRC" <<'PY'
import sys

path = sys.argv[1]
src = open(path, encoding='utf-8').read()
old = """        CALL_CAP_SHM_UNMAP => {
            if a0 > u32::MAX as u64 {
                return EBADF;
            }
"""
new = """        CALL_CAP_SHM_UNMAP => {
            if a0 > u32::MAX as u64 {
                // MUTANT: the guard still answers EBADF, but it has already
                // dropped every window the caller held on its way out.
                for h in 0..16u32 {
                    let _ = crate::experimental::caps::unmap_region_for(
                        crate::experimental::scheduler::session_identity(),
                        h,
                    );
                }
                return EBADF;
            }
"""
if src.count(old) != 1:
    sys.exit("the 0x50 EBADF arm is not where this case thinks it is (%d matches at this exact shape)" % src.count(old))
open(path, 'w', encoding='utf-8').write(src.replace(old, new))
PY
    then
        SRC_MUTATED=1
        if "$GATE" >"$TMP/src50.log" 2>&1; then
            bad "a kernel whose 0x50 drops the caller's window and still answers EBADF passed the matrix -- the live-window witness does not catch it"
        elif ! grep -qF "REFUSAL-MATRIX NO-EFFECT ROW 0x50" "$TMP/src50.log"; then
            bad "the dropped window was caught, but not by cell 0x50's witness (no 'REFUSAL-MATRIX NO-EFFECT ROW 0x50' line)"
            grep -E "^  FAIL" "$TMP/src50.log" | head -3 | sed 's/^/        /'
        elif ! grep -qF "the boot-adopted region's live window still reads its marker" "$TMP/src50.log"; then
            bad "the gate named cell 0x50, but not this witness: the NO-EFFECT line must carry its own description"
        elif ! grep -qE "REFUSAL-MATRIX NO-EFFECT ROW 0x50 .* got 0x0 , want $WITNESS_WANT" "$TMP/src50.log"; then
            bad "cell 0x50 was named, but the witness did not read the missing marker (expected 'got 0x0 , want $WITNESS_WANT')"
            grep -m1 "NO-EFFECT ROW 0x50" "$TMP/src50.log" | sed 's/^/        /'
        elif ! grep -qF "REFUSAL-MATRIX MISMATCH" "$TMP/src50.log"; then
            bad "cell 0x50's witness disagreed, but the matrix line did not name the verdict MISMATCH"
        else
            ok "0x50 drops the window and still answers EBADF -> the gate refused the kernel and named cell 0x50's witness (got 0x0 , want $WITNESS_WANT)"
        fi
    else
        bad "could not make 0x50 drop the window (see message above); source left unchanged"
    fi
    cp "$SRC_BACKUP" "$SRC"
    SRC_MUTATED=0
    if ! cmp -s "$SRC_BACKUP" "$SRC"; then
        bad "$SRC does not match its backup after the 0x50 mutation: the tree is NOT back to its original state"
    elif ! ./build.sh >"$TMP/rebuild50.log" 2>&1; then
        bad "the source is restored but ./build.sh no longer succeeds -- target/kernel.elf may still be the 0x50 mutant"
        tail -5 "$TMP/rebuild50.log" | sed 's/^/        /'
    else
        ok "source restored byte-for-byte after the 0x50 case and the real kernel rebuilt"
    fi
fi

# ---- a third KERNEL regression: a different row, a different witness ------
# The two cases above prove one row's errno and then one row's window. This
# case asks the no-effect pass to carry that burden for a DIFFERENT row, so
# the property is not a single cell's special case. 0x4d CAP_SHM_CLOSE's
# EBADF arm is the subject because the matrix already probes it (cell 22) and
# its witness is real state it could destroy: the caller's own live region
# capability from the WINDOW leg, re-asked through 0x4c after the refusal.
#
# The mutation gives back EVERY region capability the caller holds before
# returning EBADF. The answer is unchanged, so this is not a wrong-refusal
# bug: `matched 0x19` must still be printed for the mutant. What changes is
# that the capability the witness reads is no longer held, so 0x4c answers
# EBADF where it must answer 0, and the NO-EFFECT ROW 0x4d line must name it.
# The other two matrix cells that use later capabilities (0x50's boot-adopted
# handle, 0x52's endpoint) may report their own witnesses too; the gate's
# requirement here is that 0x4d's line is present and attributable.
echo "==> a kernel regression: 0x4d gives back the caller's capabilities but answers EBADF, expect a red matrix"
if [[ ! -f "$SRC" ]]; then
    bad "no $SRC to mutate: the 0x4d witness case cannot run"
else
    cp "$SRC" "$SRC_BACKUP" || bad "could not back up $SRC"
    if python3 - "$SRC" <<'PY'
import sys

path = sys.argv[1]
src = open(path, encoding='utf-8').read()
old = """        CALL_CAP_SHM_CLOSE => {
            if a0 > u32::MAX as u64 {
                return EBADF;
            }
"""
new = """        CALL_CAP_SHM_CLOSE => {
            if a0 > u32::MAX as u64 {
                // MUTANT: the guard still answers EBADF, but it has already
                // given back every region capability the caller held.
                for h in 0..16u32 {
                    let _ = crate::experimental::caps::close_region_for(
                        crate::experimental::scheduler::session_identity(),
                        h,
                    );
                }
                return EBADF;
            }
"""
if src.count(old) != 1:
    sys.exit("the 0x4d EBADF arm is not where this case thinks it is (%d matches at this exact shape)" % src.count(old))
open(path, 'w', encoding='utf-8').write(src.replace(old, new))
PY
    then
        SRC_MUTATED=1
        if "$GATE" >"$TMP/src4d.log" 2>&1; then
            bad "a kernel whose 0x4d gives back the caller's capabilities and still answers EBADF passed the matrix -- the no-effect witness does not catch it"
        elif ! grep -qF "REFUSAL-MATRIX NO-EFFECT ROW 0x4d" "$TMP/src4d.log"; then
            bad "the freed capability was caught, but not by cell 0x4d's witness (no 'REFUSAL-MATRIX NO-EFFECT ROW 0x4d' line)"
            grep -E "^  FAIL" "$TMP/src4d.log" | head -3 | sed 's/^/        /'
        elif ! grep -qF "the caller's live capability is still usable through 0x4c after the refused close" "$TMP/src4d.log"; then
            bad "the gate named cell 0x4d, but not this witness: the NO-EFFECT line must carry its own description"
        elif ! grep -qE "REFUSAL-MATRIX \(rows 0x19 , matched 0x19" "$TMP/src4d.log"; then
            bad "the mutant did not keep every errno right (expected 'matched 0x19'): this case is about acting, not answering"
            grep -m1 "REFUSAL-MATRIX (" "$TMP/src4d.log" | sed 's/^/        /'
        elif ! grep -qE "REFUSAL-MATRIX NO-EFFECT ROW 0x4d .* got 0xfffffffffffffff7 , want 0x0" "$TMP/src4d.log"; then
            bad "cell 0x4d was named, but its witness did not read the closed capability (expected 'got 0xfffffffffffffff7 , want 0x0')"
            grep -m1 "NO-EFFECT ROW 0x4d" "$TMP/src4d.log" | sed 's/^/        /'
        elif ! grep -qF "REFUSAL-MATRIX MISMATCH" "$TMP/src4d.log"; then
            bad "cell 0x4d's witness disagreed, but the matrix line did not name the verdict MISMATCH"
        else
            ok "0x4d gives back the caller's capabilities and still answers EBADF -> the gate refused the kernel and named cell 0x4d's no-effect line (got 0xfffffffffffffff7 , want 0x0) with every errno still matched"
        fi
    else
        bad "could not make 0x4d give back the caller's capabilities (see message above); source left unchanged"
    fi
    cp "$SRC_BACKUP" "$SRC"
    SRC_MUTATED=0
    if ! cmp -s "$SRC_BACKUP" "$SRC"; then
        bad "$SRC does not match its backup after the 0x4d mutation: the tree is NOT back to its original state"
    elif ! ./build.sh >"$TMP/rebuild4d.log" 2>&1; then
        bad "the source is restored but ./build.sh no longer succeeds -- target/kernel.elf may still be the 0x4d mutant"
        tail -5 "$TMP/rebuild4d.log" | sed 's/^/        /'
    else
        ok "source restored byte-for-byte after the 0x4d case and the real kernel rebuilt"
    fi
fi

echo
if [[ "$FAILS" -ne 0 ]]; then
    echo "FAIL: $FAILS self-check(s) did not hold -- the gate can pass wrong evidence"
    exit 1
fi
echo "PASS: the proof gate rejects every mutation tried here, and passes the real transcript"
